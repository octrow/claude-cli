"""The one place a ``claude -p`` subprocess is spawned.

Invariants (learned the hard way, previously duplicated as comments in six
repos — change them here and nowhere else):

* **NEVER pass ``--bare``.** It forces ``ANTHROPIC_API_KEY``-only mode and
  breaks the Claude Code *subscription* auth every consumer relies on. The flag
  is intentionally absent from :func:`build_args`.
* **Isolate the surface.** An empty temp mcp-config plus ``--strict-mcp-config``,
  ``--setting-sources user``, ``--disable-slash-commands`` and
  ``--no-session-persistence``: no project MCP servers, no project settings, no
  slash commands, no session files left behind.
* **Run from a throwaway cwd** so no project ``CLAUDE.md`` is auto-discovered.
* ``--tools ""`` by default (no tool use at all). A non-empty ``tools`` value is
  passed to BOTH ``--tools`` and ``--allowed-tools``: headless ``-p`` mode
  auto-denies any call not on the allow-list, so an enabled-but-not-allowed tool
  would stall or fail silently mid-run.
* "Not logged in" and usage-limit signatures are classified before the exit code
  so callers get an actionable, fail-fast error instead of a generic non-zero.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .errors import ClaudeCliError, NotLoggedInError, UsageLimitError
from .parsing import error_detail, parse_envelope, parse_stream

log = logging.getLogger(__name__)

#: Model aliases the CLI accepts (cheap/fast → deep). Passed through verbatim.
MODELS = ("haiku", "sonnet", "opus")

#: Narrow on purpose: a bare "limit" would false-positive on batch-local errors
#: ("context limit exceeded") and abort a whole run for one oversized prompt.
_USAGE_LIMIT_RE = re.compile(
    r"usage limit|session limit|rate.?limit|quota exceeded|too many requests|\b429\b",
    re.IGNORECASE,
)

_EMPTY_MCP = '{"mcpServers":{}}'


@dataclass(frozen=True)
class ClaudeResult:
    """One completed call: the answer text plus what the envelope reported.

    ``stdout`` is the raw CLI output (the full stream-json text for that format)
    so consumers that archive runs for inspection don't lose partial events the
    folded ``raw`` envelope drops.
    """

    text: str
    cost_usd: float | None = None
    duration_ms: int | None = None
    raw: dict = field(default_factory=dict)
    stdout: str = ""


def claude_available() -> bool:
    """True if the ``claude`` CLI is on PATH."""
    return shutil.which("claude") is not None


def build_args(
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    output_format: str = "json",
    tools: str = "",
    mcp_config: str,
) -> list[str]:
    """Build the argv for one ``claude -p`` call. Never includes ``--bare``."""
    args = ["claude", "-p", "--model", model]
    if effort:
        args += ["--effort", effort]
    args += ["--output-format", output_format]
    if output_format == "stream-json":
        # stream-json requires --verbose in -p mode; partial messages keep the
        # stream flowing so a long run doesn't look wedged.
        args += ["--include-partial-messages", "--verbose"]
    args += ["--no-session-persistence", "--tools", tools]
    if tools:
        args += ["--allowed-tools", tools]
    args += [
        "--disable-slash-commands",
        "--setting-sources", "user",
        "--strict-mcp-config",
        "--mcp-config", mcp_config,
    ]
    return args


def _write_empty_mcp(work_dir: str) -> str:
    path = Path(work_dir) / "mcp-empty.json"
    path.write_text(_EMPTY_MCP, encoding="utf-8")
    return str(path)


def _finish(
    returncode: int | None,
    stdout: str,
    stderr: str,
    *,
    output_format: str,
    elapsed_ms: int,
) -> ClaudeResult:
    """Classify a finished run: raise on failure, else return the result."""
    env = parse_stream(stdout) if output_format == "stream-json" else {}
    result = env.get("result") if isinstance(env.get("result"), str) else None
    combined = (stdout or "") + "\n" + (stderr or "")

    if _USAGE_LIMIT_RE.search(combined):
        raise UsageLimitError(
            f"claude hit a usage/rate limit: {error_detail(stdout, stderr, result)}",
            stdout=stdout,
        )
    if "Not logged in" in combined:
        raise NotLoggedInError(
            "claude reports 'Not logged in' — run `claude` once interactively, "
            "complete /login, then retry.",
            stdout=stdout,
        )
    if returncode != 0:
        raise ClaudeCliError(
            f"claude exited {returncode}: {error_detail(stdout, stderr, result)}",
            stdout=stdout,
        )

    if output_format != "stream-json":
        try:
            env = parse_envelope(stdout)
        except ValueError as exc:
            raise ClaudeCliError(str(exc), stdout=stdout) from exc
    if env.get("is_error"):
        raise ClaudeCliError(
            f"claude error: {str(env.get('result'))[-300:]}", stdout=stdout
        )
    text = env.get("result")
    if not isinstance(text, str) or not text:
        raise ClaudeCliError("claude produced no result", stdout=stdout)

    cost = env.get("total_cost_usd")
    duration = env.get("duration_ms")
    log.info(
        "claude ok: %dms, cost=%s (subscription)", duration or elapsed_ms, cost
    )
    return ClaudeResult(
        text=text,
        cost_usd=float(cost) if cost is not None else None,
        duration_ms=int(duration) if duration is not None else elapsed_ms,
        raw=env,
        stdout=stdout,
    )


def run_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    timeout: int = 300,
    tools: str = "",
    output_format: str = "json",
) -> ClaudeResult:
    """Run one prompt through the local CLI (blocking).

    Raises :class:`UsageLimitError`, :class:`NotLoggedInError` or
    :class:`ClaudeCliError` — all ``RuntimeError`` subclasses.
    """
    log.info(
        "claude call: model=%s effort=%s prompt=%d chars tools=%r",
        model, effort, len(prompt), tools,
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="claude-cli-") as work:
        args = build_args(
            model=model, effort=effort, output_format=output_format,
            tools=tools, mcp_config=_write_empty_mcp(work),
        )
        try:
            proc = subprocess.run(
                args, input=prompt, cwd=work,
                capture_output=True, text=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(f"claude timed out after {timeout}s") from exc
    return _finish(
        proc.returncode, proc.stdout or "", proc.stderr or "",
        output_format=output_format,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


async def arun_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    timeout: int = 300,
    tools: str = "",
    output_format: str = "json",
) -> ClaudeResult:
    """:func:`run_claude` on asyncio — same flags, same errors."""
    log.info(
        "claude call (async): model=%s effort=%s prompt=%d chars tools=%r",
        model, effort, len(prompt), tools,
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="claude-cli-") as work:
        args = build_args(
            model=model, effort=effort, output_format=output_format,
            tools=tools, mcp_config=_write_empty_mcp(work),
        )
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            proc.kill()
            await proc.wait()
            raise ClaudeCliError(f"claude timed out after {timeout}s") from exc
    return _finish(
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        output_format=output_format,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


@runtime_checkable
class LLMProvider(Protocol):
    """One-method LLM seam: send a prompt, get the model's text answer back.

    ``@runtime_checkable`` so callers can assert a backend satisfies it
    structurally; a future local-model provider implements the same signature.
    """

    def ask(self, prompt: str, *, model: str | None = None) -> str: ...


class ClaudeCliProvider:
    """:class:`LLMProvider` over ``claude -p`` — text in, ``.result`` text out.

    ``DEFAULT_MODEL``/``DEFAULT_TIMEOUT`` are class attributes so a consumer pins
    its own defaults by subclassing one line each (no ``__init__`` override).
    """

    DEFAULT_MODEL = "sonnet"
    DEFAULT_TIMEOUT = 300

    def __init__(self, *, model: str | None = None, timeout: int | None = None,
                 **kwargs) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.kwargs = {"timeout": timeout or self.DEFAULT_TIMEOUT, **kwargs}

    def ask(self, prompt: str, *, model: str | None = None) -> str:
        return run_claude(prompt, model=model or self.model, **self.kwargs).text
