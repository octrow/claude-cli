"""OmniRoute HTTP route: tool-less calls skip the `claude` subprocess.

``POST /v1/chat/completions`` (OpenAI format) on the local gateway. No ``temperature``
is ever sent: that would switch on OmniRoute's own semantic cache, whose key ignores
system prompt and tools. No API key -> the route is silently off.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .errors import ClaudeCliError
from .runner import ClaudeResult

log = logging.getLogger(__name__)

# alias -> combo, the same mapping the gateway applies to `--model` for the CLI;
# any other value (a bare combo name) passes through unchanged
COMBOS = {"haiku": "free-stack", "sonnet": "free-stack", "opus": "hard"}
COOLDOWN_SECS = 600
ENV_FILE = Path.home() / ".omniroute" / ".env"

_down_until = 0.0  # ponytail: per process, not per model; a dead gateway costs one timeout per window


class HttpLlmError(ClaudeCliError):
    """The HTTP route failed (transport, status, body); run_claude falls back to the CLI."""


def api_key() -> str | None:
    """OMNIROUTE_API_KEY from the env, else from ~/.omniroute/.env (never logged)."""
    if key := os.environ.get("OMNIROUTE_API_KEY"):
        return key
    try:
        for line in ENV_FILE.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OMNIROUTE_API_KEY":
                return value.strip().strip("\"'") or None
    except OSError:
        pass
    return None


def omniroute_chat(prompt: str, *, model: str = "sonnet", timeout: float = 300,
                   system_prompt: str | None = None, **_ignored) -> ClaudeResult:
    """One chat completion, ClaudeResult-shaped. CLI-only knobs (effort, cwd...) are ignored."""
    key = api_key()
    if not key:
        raise HttpLlmError("no OMNIROUTE_API_KEY")
    url = os.environ.get("OMNIROUTE_URL", "http://localhost:20128") + "/v1/chat/completions"
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    body = json.dumps({"model": COMBOS.get(model, model), "messages": messages}).encode()
    req = urllib.request.Request(url, body, {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        text = data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:  # 401 key, 422 body, 429/503 busy
        raise HttpLlmError(f"HTTP {exc.code} from {url}") from exc
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HttpLlmError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(text, str) or not text.strip():
        raise HttpLlmError("empty completion")
    # raw keeps the OpenAI body, but `usage` in the CLI envelope's keys so meters read it
    usage = data.get("usage") or {}
    raw = {**data, "usage": {"input_tokens": usage.get("prompt_tokens"),
                             "output_tokens": usage.get("completion_tokens")}}
    return ClaudeResult(text=text, cost_usd=0.0, raw=raw,
                        duration_ms=int((time.monotonic() - t0) * 1000))


def try_http(prompt: str, *, backend: str, cli_only: bool, **kw) -> ClaudeResult | None:
    """The HTTP answer, or None when run_claude should use the CLI.

    ``backend``: "cli" never, "api" always (errors propagate, no cooldown), "auto" when
    a key exists, the gateway is not cooling down and the call needs nothing CLI-only.
    """
    global _down_until
    backend = os.environ.get("CLAUDE_CLI_BACKEND") or backend
    if backend == "cli" or cli_only:
        return None
    if backend == "api":
        return omniroute_chat(prompt, **kw)
    if time.monotonic() < _down_until or not api_key():
        return None
    try:
        return omniroute_chat(prompt, **kw)
    except HttpLlmError as exc:
        _down_until = time.monotonic() + COOLDOWN_SECS
        log.warning("omniroute http failed (%s); CLI fallback, http off for %ds",
                    exc, COOLDOWN_SECS)
        return None
