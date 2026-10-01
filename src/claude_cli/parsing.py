"""Parsers for the CLI's two output shapes, plus JSON extraction from prose.

``--output-format json`` yields one envelope object; ``--output-format
stream-json`` yields one JSON object per line, of which the final
``{"type": "result"}`` event carries the answer and the run metadata.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Metadata keys lifted from a stream-json ``result`` event (tlf/digest.py).
_META_KEYS = (
    "usage", "total_cost_usd", "duration_ms", "duration_api_ms",
    "num_turns", "session_id", "model", "modelUsage", "is_error",
)

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)

#: CSI escape sequences (colour codes and friends) — terminal decoration only.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_fences(text: str) -> str:
    """Drop a leading/trailing markdown code fence (```json ... ```), if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) == 1:
        # ponytail: one-line reply (```json [1,2]```) has no newline to split on
        inner = stripped.removeprefix("```").removesuffix("```").strip()
        lang, sep, rest = inner.partition(" ")
        return rest.strip() if sep and lang.isalpha() else inner
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_json(text: str) -> Any:
    """Pull the first balanced ``{...}`` object out of a model reply.

    Tolerates ```fences``` and surrounding prose ("Here's the result: {…}").
    Raises :class:`json.JSONDecodeError` exactly as ``json.loads`` would, so
    callers can wrap it in their own contextual message.
    """
    text = strip_fences(text)
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass  # greedy match spanned two fenced blocks — fall back to the scan
    start = text.find("{")
    if start == -1:
        return json.loads(text)
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    return json.loads(text[start:])  # unbalanced — let json raise a clear error


def extract_json_or_none(text: str) -> Any | None:
    """:func:`extract_json`, but ``None`` on garbage — the graceful-degrade
    variant every consumer with a fallback path otherwise re-implements."""
    try:
        return extract_json(text)
    except (ValueError, TypeError):
        return None


def strip_ansi(text: str) -> str:
    """Drop ANSI/CSI escape sequences (colour codes) from CLI output."""
    return _ANSI_RE.sub("", text)


def _is_omniroute_banner_line(line: str) -> bool:
    """True for a known OmniRoute banner/warning line, never the real error.

    Narrow on purpose — three specific shapes only, matched on the plain
    (post-ANSI-strip) line so a genuine error never gets swallowed alongside
    the wrapper's noise:

    * ``📋 Loaded env from <path>`` — the env-file banner it prints on every run.
    * ``... is ignored, ... set it first`` — its "your env var is shadowed" warning.
    * ``... connectors are disabled because ANTHROPIC_API_KEY ...`` — its
      claude.ai-connector notice.
    """
    normalized = line.strip().lstrip("⚠📋").strip()
    if normalized.startswith("Loaded env from"):
        return True
    if " is ignored, " in line and "set it first" in line:
        return True
    if "connectors are disabled because ANTHROPIC_API_KEY" in line:
        return True
    return False


def strip_omniroute_noise(text: str) -> str:
    """Drop OmniRoute's banner/warning lines so the real error survives truncation.

    Apply before failure classification (usage-limit / not-logged-in regexes)
    and before any tail-truncation (``error_detail``'s ``[-500:]``, the
    ``is_error`` envelope's ``[-300:]``) — otherwise a handful of omniroute
    warnings can fill the truncated window and push the real error out of it.
    """
    lines = [line for line in text.splitlines() if not _is_omniroute_banner_line(line)]
    return "\n".join(lines)


def clean_cli_text(text: str) -> str:
    """Strip ANSI codes then OmniRoute noise — the standard pre-classification cleanup."""
    return strip_omniroute_noise(strip_ansi(text))


def parse_envelope(stdout: str) -> dict:
    """Parse the single ``--output-format json`` envelope. Raises ValueError.

    OmniRoute prints its "Loaded env from …" banner on *stdout* before handing
    over to the CLI, so the envelope is not always the first thing on the stream;
    fall back to the first ``{`` (``parse_stream`` already skips such noise).
    """
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as exc:
        start = stdout.find("{")
        try:
            # raw_decode: tolerate trailing wrapper noise after the envelope too
            env = json.JSONDecoder().raw_decode(stdout, start)[0] if start != -1 else None
        except json.JSONDecodeError:
            env = None
        if env is None:
            raise ValueError(f"claude returned non-JSON: {stdout[-300:]}") from exc
    if not isinstance(env, dict):
        raise ValueError(f"unexpected claude envelope: {type(env).__name__}")
    return env


def parse_stream(stdout: str) -> dict:
    """Fold the final ``{"type":"result"}`` stream-json event into an envelope.

    Returns a dict shaped like the ``--output-format json`` envelope (``result``
    plus the metadata keys) so both formats share one downstream path. Non-JSON
    and non-result lines are skipped — the stream also carries partial-message
    events and, on some failures, plain-text noise.
    """
    env: dict = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            if isinstance(event.get("result"), str):
                env["result"] = event["result"]
            env.update({k: event[k] for k in _META_KEYS if k in event})
    return env


def error_detail(stdout: str, stderr: str, result: str | None) -> str:
    """Best-effort human-readable reason a failed run gave.

    On usage limits ``stderr`` is empty and the real message ("You've hit your
    session limit …") only appears in the result event or raw stdout, so fall
    back to those instead of an empty "claude exited 1:" tail.
    """
    # Tail, not head: a wrapper (omniroute) prints a multi-line banner before the
    # real failure, and [:500] would return only the banner.
    if stderr.strip():
        return stderr.strip()[-500:]
    if result:
        return re.sub(r"\s+", " ", result).strip()[:500]
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line and not line.startswith("{"):
            return line[:300]
    return "(no output)"
