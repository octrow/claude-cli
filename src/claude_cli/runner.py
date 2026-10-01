"""The one place a ``claude -p`` subprocess is spawned through OmniRoute.

Invariants (learned the hard way, previously duplicated as comments in six
repos — change them here and nowhere else):

* **Always launch through ``omniroute run claude``.** This routes every Claude
  Code request through the local OmniRoute gateway.
* **NEVER pass ``--bare``.** It forces ``ANTHROPIC_API_KEY``-only mode and
  breaks the Claude Code *subscription* auth every consumer relies on. The flag
  is intentionally absent from :func:`build_args`.
* **Isolate the surface.** An empty temp mcp-config plus ``--strict-mcp-config``,
  ``--setting-sources user``, ``--disable-slash-commands`` and
  ``--no-session-persistence``: no project MCP servers, no project settings, no
  slash commands, no session files left behind.
* **Run from a throwaway cwd** so no project ``CLAUDE.md`` is auto-discovered.
* **Project mode is opt-in and reverses the two above.** ``cwd=`` plus
  ``slash_commands=True`` / ``setting_sources="user,project"`` /
  ``permission_mode=`` runs a project's own slash command (``/apply <file>``) with
  its ``CLAUDE.md`` and settings loaded. That is a different trust posture — the
  run reads project config and may write files — so nothing about it is a default.
* ``--tools ""`` by default (no tool use at all). A non-empty ``tools`` value is
  passed to BOTH ``--tools`` and ``--allowed-tools``: headless ``-p`` mode
  auto-denies any call not on the allow-list, so an enabled-but-not-allowed tool
  would stall or fail silently mid-run.
* "Not logged in" and usage-limit signatures are classified before the exit code
  so callers get an actionable, fail-fast error instead of a generic non-zero.
* **A gateway failure is not a Claude failure.** OmniRoute answers 503 both when a
  provider quota is spent and when no provider matched at all
  (``ALL_TARGETS_SKIPPED`` — a restricted API key, a combo resolving to nothing).
  The second is a config bug that waiting never fixes, so it is deliberately kept
  out of :data:`_USAGE_LIMIT_RE` and instead earns a pointer to OmniRoute's own
  docs and issues. See ``docs/OMNIROUTE.md`` for the diagnosis order.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .errors import ClaudeCliError, NotLoggedInError, UsageLimitError
from .parsing import clean_cli_text, error_detail, parse_envelope, parse_stream
from .preflight import TIERS

log = logging.getLogger(__name__)

#: Model aliases the CLI accepts (cheap/fast → deep). Passed through verbatim.
#:
#: Claude Code expands these to ``claude-haiku-*`` / ``claude-sonnet-*`` /
#: ``claude-opus-*``, and OmniRoute's model→combo glob mappings turn that into a
#: routing tier (dashboard: Settings → Routing, or ``/api/model-combo-mappings``):
#:
#: * ``opus``   → combo ``hard``       — subscriptions only: Claude Opus 5, then
#:   ChatGPT (gpt-5.6-terra) and Grok 4.6, Claude Sonnet 5, metered GLM-5.3 last.
#: * ``sonnet`` → combo ``free-stack`` — free/plan-included ladder led by
#:   muse-spark-1.2-contributor-free; every target verified to emit tool_calls.
#: * ``haiku``  → combo ``free-first``  — the older free ladder (several targets are
#:   currently quota-exhausted; see OmniRoute _artifacts/MODEL-STATUS.md).
#:
#: So ``sonnet`` is now the "bulk work, spare the $20 plan" lever: it costs nothing
#: and does NOT touch the Claude subscription. ``opus`` is the one that spends it —
#: and the one where prompt caching actually pays off (``hard`` runs with
#: context_cache_protection on, so the combo is pinned per conversation).
#: Never send ``combo/<name>`` as the model: Claude Code rejects an unrecognized
#: model id locally (``claude-code:unrecognized_model``) before the request leaves.
MODELS = ("haiku", "sonnet", "opus")

#: Narrow on purpose: a bare "limit" would false-positive on batch-local errors
#: ("context limit exceeded") and abort a whole run for one oversized prompt.
_USAGE_LIMIT_RE = re.compile(
    r"usage limit|session limit|rate.?limit|quota exceeded|too many requests|\b429\b",
    re.IGNORECASE,
)

#: Where to look when the gateway itself is the problem. Named here rather than in a comment
#: because they belong in the *error text* a caller sees at 3am, not in a file nobody opens.
OMNIROUTE_DOCS = "https://github.com/diegosouzapw/OmniRoute#-documentation"
OMNIROUTE_ISSUES = "https://github.com/diegosouzapw/OmniRoute/issues"

#: A failure that came from OmniRoute, not from Claude. Deliberately *not* folded into
#: `_USAGE_LIMIT_RE`: a 503 `ALL_TARGETS_SKIPPED` means no provider matched the request (a wrong
#: `allowedModels` on the API key, a combo that resolves to nothing), which reads like a quota
#: error and is not one — retrying or waiting fixes nothing. Widening the limit pattern to cover
#: it would abort runs that a config fix would have saved.
_GATEWAY_RE = re.compile(
    r"ALL_TARGETS_SKIPPED|no targets|no provider matched|\bomniroute\b|\b50[23]\b"
    r"|socket hang up|keepAlive|pipelining|PROXY_FAST_FAIL|circuit.?breaker"
    r"|fallback exhausted|allowedModels|resolves to nothing"
    r"|ECONNREFUSED|unsupported_country_region_territory|unrecognized_model",
    re.IGNORECASE,
)

#: The subset of `_GATEWAY_RE` that outranks a limit match: the gateway itself ran out of
#: routes. Not the generic `omniroute` / 502 / 503 markers — OmniRoute prefixes relayed
#: upstream text with its own name, so those would swallow every limit and login failure.
_GATEWAY_EXHAUSTED_RE = re.compile(
    r"ALL_TARGETS_SKIPPED|no targets|no provider matched|fallback exhausted"
    r"|allowedModels|resolves to nothing|PROXY_FAST_FAIL|circuit.?breaker",
    re.IGNORECASE,
)

_EMPTY_MCP = '{"mcpServers":{}}'


def _kill_tree(proc: subprocess.Popen | asyncio.subprocess.Process) -> None:
    """SIGKILL the child's whole process group (it was started with ``start_new_session``).

    ``omniroute run`` spawns the real ``claude`` underneath; killing only the wrapper
    orphans that grandchild, which keeps the pipes open (the read loop hangs until it
    exits) and keeps spending quota. Falls back to a plain kill if the group is gone.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # already exited and reaped


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
    #: Requested serving tier (ultra/high/middle/low), echoed from the call so
    #: free-vs-paid spend stays diagnosable. None when the caller picked no tier.
    tier: str | None = None


def claude_available() -> bool:
    """True if OmniRoute and the underlying ``claude`` CLI are on PATH."""
    return shutil.which("omniroute") is not None and shutil.which("claude") is not None


def build_args(
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    output_format: str = "json",
    tools: str = "",
    mcp_config: str,
    slash_commands: bool = False,
    setting_sources: str = "user",
    permission_mode: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    base_url: str | None = None,
    context: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> list[str]:
    """Build argv for one Claude call through OmniRoute. Never includes ``--bare``.

    ``tier`` (ultra/high/middle/low) selects the OmniRoute-side combo/profile of
    that name via the gateway-level ``--profile`` flag placed before ``--``: the
    Claude-side ``--model`` stays haiku/sonnet/opus because Claude Code rejects
    ``combo/<name>`` locally. ``remote``/``base_url``/``context``/``provider``/
    ``profile`` are explicit opt-ins for remote-mode and routing overrides; all
    default to off so plain calls keep the historical argv untouched.
    """
    if tier is not None and tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}: expected one of {', '.join(TIERS)}")
    gateway: list[str] = []
    if remote:
        gateway += ["--remote", remote]
    if base_url:
        gateway += ["--base-url", base_url]
    if context:
        gateway += ["--context", context]
    if provider:
        gateway += ["--provider", provider]
    resolved_profile = profile or tier
    if resolved_profile:
        gateway += ["--profile", resolved_profile]
    args = ["omniroute", "run", *gateway, "claude", "--", "-p", "--model", model]
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
    if not slash_commands:
        args += ["--disable-slash-commands"]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    args += [
        "--setting-sources", setting_sources,
        "--strict-mcp-config",
        "--mcp-config", mcp_config,
    ]
    return args


def _write_empty_mcp(work_dir: str) -> str:
    path = Path(work_dir) / "mcp-empty.json"
    path.write_text(_EMPTY_MCP, encoding="utf-8")
    return str(path)


def _gateway_hint(combined: str, tier: str | None = None) -> str:
    """A pointer to OmniRoute's own docs, appended when the failure looks like the gateway's.

    Diagnosis order, cheapest first (none of these spend quota):
    `omniroute health` → `omniroute doctor` → `omniroute run claude --dry-run --json`
    (shows the planned command without executing) → `omniroute logs` → `omniroute quota`.
    """
    if not _GATEWAY_RE.search(combined):
        return ""
    scope = f" (tier {tier})" if tier else ""
    return (
        f"\nThis looks like OmniRoute{scope}, not Claude. Cheapest checks first, none spend quota: "
        f"`omniroute health`, `omniroute doctor`, "
        f"`omniroute run claude --dry-run --json`, `omniroute logs`, `omniroute quota`. "
        f"For routing add `omniroute simulate [--combo <tier>]`, `omniroute status`, "
        f"`models`, `combo list`, `test <provider> <model>`; for spend add "
        f"`omniroute cost --group-by combo` and `omniroute usage`. "
        f"Docs: {OMNIROUTE_DOCS} — issues: {OMNIROUTE_ISSUES}"
    )


def _matched_line(combined: str, hit: re.Match) -> str:
    """The line that actually matched the limit pattern.

    Not `error_detail`: it prefers stderr, so an unrelated stderr warning (an untrusted-workspace
    notice, an omniroute banner) gets reported as the limit message while the real
    "You've hit your session limit" sits in stdout, unread.
    """
    start = combined.rfind("\n", 0, hit.start()) + 1
    end = combined.find("\n", hit.end())
    return combined[start:end if end != -1 else len(combined)].strip()[:500]


def _finish(
    returncode: int | None,
    stdout: str,
    stderr: str,
    *,
    output_format: str,
    elapsed_ms: int,
    tier: str | None = None,
    via_gateway: bool = True,
) -> ClaudeResult:
    """Classify a finished run: raise on failure, else return the result.

    ``via_gateway`` is True whenever the argv came from :func:`build_args` (which
    always prefixes ``omniroute run``); it decides whether a usage limit is
    session-wide. Pass False only for a hand-built direct ``claude`` invocation.
    """
    env = parse_stream(stdout) if output_format == "stream-json" else {}
    result = env.get("result") if isinstance(env.get("result"), str) else None
    clean_stdout = clean_cli_text(stdout or "")
    clean_stderr = clean_cli_text(stderr or "")
    combined = clean_stdout + "\n" + clean_stderr

    # Only a FAILED run's output is evidence of a limit or a login problem: a
    # successful answer may legitimately discuss "rate limits" or "not logged in"
    # (ponytail: gate on failure, don't try to outsmart the regex).
    failed = returncode != 0 or bool(env.get("is_error"))

    # Gateway first, limit second — the reverse of the obvious order, and the
    # reason is the whole point of routing through a combo: OmniRoute quotes the
    # failing upstream's own words, so "combo 'hard': fallback exhausted (429)"
    # matches BOTH patterns. Checking the limit first reported a gateway-level
    # exhaustion as a personal-subscription limit and aborted a 187-item batch
    # that a skip-and-continue would have finished. `_GATEWAY_RE`'s own docstring
    # already says waiting fixes nothing for these; now the code agrees.
    if failed and "Not logged in" in combined:
        raise NotLoggedInError(
            "claude reports 'Not logged in' — run `claude` once interactively, "
            "complete /login, then retry.",
            stdout=stdout,
        )
    if failed and _GATEWAY_EXHAUSTED_RE.search(combined):
        raise ClaudeCliError(
            f"omniroute gateway error: {error_detail(clean_stdout, clean_stderr, result)}"
            f"{_gateway_hint(combined, tier)}",
            stdout=stdout,
        )
    if failed and (limit_hit := _USAGE_LIMIT_RE.search(combined)):
        # Every call built by `build_args` goes through `omniroute run`, so a limit
        # here is ONE upstream in the chain, not the end of the road.
        raise UsageLimitError(
            f"claude hit a usage/rate limit: {_matched_line(combined, limit_hit)}",
            stdout=stdout,
            session_wide=not via_gateway,
        )
    if returncode != 0:
        detail = error_detail(clean_stdout, clean_stderr, result)
        if detail == "(no output)" and ((stdout or "") + (stderr or "")).strip():
            detail = "(stderr held only omniroute warnings)"
        raise ClaudeCliError(
            f"claude exited {returncode}: {detail}{_gateway_hint(combined, tier)}", stdout=stdout
        )

    if output_format != "stream-json":
        try:
            env = parse_envelope(stdout)
        except ValueError as exc:
            raise ClaudeCliError(str(exc), stdout=stdout) from exc
    if env.get("is_error"):
        raise ClaudeCliError(
            f"claude error: {clean_cli_text(str(env.get('result')))[-300:]}", stdout=stdout
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
        tier=tier,
    )


def run_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    timeout: int = 300,
    tools: str = "",
    output_format: str = "json",
    cwd: str | Path | None = None,
    slash_commands: bool = False,
    setting_sources: str = "user",
    permission_mode: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    base_url: str | None = None,
    context: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> ClaudeResult:
    """Run one prompt through the local CLI (blocking).

    ``cwd`` runs inside a real project (its ``CLAUDE.md`` is discovered) instead of
    the throwaway dir; pair it with ``slash_commands=True`` and
    ``setting_sources="user,project"`` to invoke that project's own commands.

    ``tier`` selects an OmniRoute-side combo/profile (ultra/high/middle/low, free
    targets first); the choice is echoed on ``ClaudeResult.tier`` and named in
    gateway failure messages. ``remote``/``base_url``/``context``/``provider``/
    ``profile`` are explicit remote-mode and routing overrides, all off by default.

    Raises :class:`UsageLimitError`, :class:`NotLoggedInError` or
    :class:`ClaudeCliError` — all ``RuntimeError`` subclasses.
    """
    log.info(
        "claude call: model=%s effort=%s prompt=%d chars tools=%r cwd=%s tier=%s",
        model, effort, len(prompt), tools, cwd or "(temp)", tier,
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="claude-cli-") as work:
        args = build_args(
            model=model, effort=effort, output_format=output_format,
            tools=tools, mcp_config=_write_empty_mcp(work),
            slash_commands=slash_commands, setting_sources=setting_sources,
            permission_mode=permission_mode,
            tier=tier, remote=remote, base_url=base_url,
            context=context, provider=provider, profile=profile,
        )
        try:
            proc = subprocess.run(
                args, input=prompt, cwd=str(cwd) if cwd else work,
                capture_output=True, text=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(f"claude timed out after {timeout}s") from exc
    return _finish(
        proc.returncode, proc.stdout or "", proc.stderr or "",
        output_format=output_format,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        tier=tier,
    )


def stream_claude(
    prompt: str,
    on_event: Callable[[dict], None],
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    timeout: int = 300,
    tools: str = "",
    cwd: str | Path | None = None,
    slash_commands: bool = False,
    setting_sources: str = "user",
    permission_mode: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    base_url: str | None = None,
    context: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> ClaudeResult:
    """:func:`run_claude`, but ``on_event`` sees each stream-json event as it arrives.

    Same flags, same errors, same :class:`ClaudeResult`. The format is forced to
    ``stream-json`` — that is the only shape the CLI emits incrementally. A long agentic
    run (tool calls, file writes) reports progress instead of looking wedged for minutes.
    """
    log.info(
        "claude stream: model=%s effort=%s prompt=%d chars tools=%r cwd=%s tier=%s",
        model, effort, len(prompt), tools, cwd or "(temp)", tier,
    )
    started = time.monotonic()
    deadline = started + timeout
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="claude-cli-") as work:
        args = build_args(
            model=model, effort=effort, output_format="stream-json",
            tools=tools, mcp_config=_write_empty_mcp(work),
            slash_commands=slash_commands, setting_sources=setting_sources,
            permission_mode=permission_mode,
            tier=tier, remote=remote, base_url=base_url,
            context=context, provider=provider, profile=profile,
        )
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(cwd) if cwd else work, start_new_session=True,
        )
        # The per-line deadline check alone never fires on a child that prints nothing.
        timed_out = threading.Event()

        def _expire() -> None:
            timed_out.set()
            _kill_tree(proc)

        watchdog = threading.Timer(timeout, _expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            stderr = _pump(proc, prompt, on_event, lines, deadline=deadline, timeout=timeout)
        except BaseException as exc:  # timeout, a raising callback, Ctrl-C — never leave it running
            _kill_tree(proc)
            proc.wait()
            if timed_out.is_set() and not isinstance(exc, ClaudeCliError):
                raise ClaudeCliError(f"claude timed out after {timeout}s") from exc
            raise
        finally:
            watchdog.cancel()
        if timed_out.is_set():
            raise ClaudeCliError(f"claude timed out after {timeout}s")
    return _finish(
        proc.returncode, "".join(lines), stderr,
        output_format="stream-json",
        elapsed_ms=int((time.monotonic() - started) * 1000),
        tier=tier,
    )


def _pump(
    proc: subprocess.Popen,
    prompt: str,
    on_event: Callable[[dict], None],
    lines: list[str],
    *,
    deadline: float,
    timeout: int,
) -> str:
    """Feed the prompt in, report every event out, collect the raw lines. Returns stderr.

    All three pipes exist: the caller opened the process with ``stdin/stdout/stderr=PIPE``.
    """
    # Drain stderr concurrently: read after stdout, a child that fills the stderr pipe
    # first blocks forever while we block on its stdout.
    err: list[str] = []
    drain = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
    drain.start()
    try:
        proc.stdin.write(prompt)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:
        pass  # the child exited without reading; its own output says why
    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line)
        if time.monotonic() > deadline:
            raise ClaudeCliError(f"claude timed out after {timeout}s")
        event = _as_event(line)
        if event is not None:
            on_event(event)
    proc.wait()  # bounded by the caller's watchdog, which kills at the deadline
    drain.join()
    return "".join(err)


def _as_event(line: str) -> dict | None:
    """One stream-json line as a dict, or None for the stream's non-JSON noise."""
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


async def arun_claude(
    prompt: str,
    *,
    model: str = "sonnet",
    effort: str | None = "medium",
    timeout: int = 300,
    tools: str = "",
    output_format: str = "json",
    cwd: str | Path | None = None,
    slash_commands: bool = False,
    setting_sources: str = "user",
    permission_mode: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    base_url: str | None = None,
    context: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> ClaudeResult:
    """:func:`run_claude` on asyncio — same flags, same errors."""
    log.info(
        "claude call (async): model=%s effort=%s prompt=%d chars tools=%r cwd=%s tier=%s",
        model, effort, len(prompt), tools, cwd or "(temp)", tier,
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="claude-cli-") as work:
        args = build_args(
            model=model, effort=effort, output_format=output_format,
            tools=tools, mcp_config=_write_empty_mcp(work),
            slash_commands=slash_commands, setting_sources=setting_sources,
            permission_mode=permission_mode,
            tier=tier, remote=remote, base_url=base_url,
            context=context, provider=provider, profile=profile,
        )
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else work,
            start_new_session=True,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            _kill_tree(proc)
            await proc.wait()
            raise ClaudeCliError(f"claude timed out after {timeout}s") from exc
        except BaseException:  # task cancelled, Ctrl-C — never leave it running
            if proc.returncode is None:
                _kill_tree(proc)
                await asyncio.shield(proc.wait())
            raise
    return _finish(
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        output_format=output_format,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        tier=tier,
    )


def ping(model: str = "haiku", timeout: float = 90) -> tuple[bool, str]:
    """Real end-to-end check through OmniRoute — never raises.

    For "doctor"-style startup checks that want proof the whole path works
    (omniroute reachable, claude authenticated, subscription routing intact)
    instead of :func:`claude_available`'s PATH-only check.

    Returns ``(True, "pong via omniroute, <n>s")`` on success, or
    ``(False, <cleaned error, ≤200 chars>)`` on any exception or timeout.
    """
    started = time.monotonic()
    try:
        run_claude(
            "reply with the single word pong", model=model, timeout=int(timeout)
        )
    except Exception as exc:  # noqa: BLE001 - a doctor check must never raise
        return False, clean_cli_text(str(exc))[:200]
    elapsed = time.monotonic() - started
    return True, f"pong via omniroute, {elapsed:.1f}s"


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
