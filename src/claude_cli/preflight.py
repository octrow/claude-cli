"""Quota-free preflight over the OmniRoute CLI, plus the utilization matrix.

Every helper shells out to ``omniroute <subcommand>`` with captured output and a
bounded timeout, and returns ``(ok, detail)`` instead of raising — a doctor-style
startup check must report, not crash. Only quota-free subcommands are ever
invoked here; a real model call always goes through :mod:`claude_cli.runner`.

The matrix below is the normative use-vs-avoid map for the v3.8.51 documented
surface (links pinned to release/v3.8.51; local binary verified as 3.8.50 via
``omniroute -v``). ``docs/OMNIROUTE.md`` carries the human-readable version.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from .errors import ClaudeCliError
from .parsing import clean_cli_text

log = logging.getLogger(__name__)

#: Blocking constraints an AVOIDED matrix entry MUST name one of.
CONSTRAINTS = (
    "zero-dependencies",
    "subscription-auth",
    "isolation-default",
    "no-live-CLI-testability",
)

#: The v3.8.51 guide surface the matrix covers: 16 guides plus the README
#: quick-start and UNINSTALL (18 entries — the proposal's audit scope).
GUIDES = (
    "USER_GUIDE",
    "SETUP_GUIDE",
    "CLI-TOOLS",
    "REMOTE-MODE",
    "CLAUDE-CODE-CONFIGURATION",
    "ENVIRONMENT",
    "ARCHITECTURE",
    "COMPRESSION_GUIDE",
    "RTK_COMPRESSION",
    "AUTO-COMBO",
    "PROXY_GUIDE",
    "FREE_TIERS",
    "CODEBASE_DOCUMENTATION",
    "FEATURES",
    "API_REFERENCE",
    "openapi.yaml",
    "README quick-start",
    "UNINSTALL",
)

#: Normative use-vs-avoid map. Status is USED (default path), OPT-IN (explicit
#: caller opt-in only), or AVOIDED (with the blocking constraint as the reason).
MATRIX = (
    {"area": "launch: run claude", "status": "USED",
     "reason": "the only model path; runner.build_args owns it",
     "guides": ("USER_GUIDE", "SETUP_GUIDE", "CLI-TOOLS", "README quick-start")},
    {"area": "routing: combos + simulate + models/combo/test", "status": "OPT-IN",
     "reason": "tier selection rides on gateway-level flags; verified quota-free via simulate/--dry-run",
     "guides": ("AUTO-COMBO", "CLI-TOOLS", "USER_GUIDE", "FEATURES")},
    {"area": "preflight: health/doctor/run --dry-run/logs", "status": "USED",
     "reason": "quota-free startup checks; this module wraps them",
     "guides": ("CLI-TOOLS", "USER_GUIDE", "SETUP_GUIDE")},
    {"area": "quota/cost/usage", "status": "OPT-IN",
     "reason": "read-only spend visibility; quota doubles as a diagnosis step",
     "guides": ("FREE_TIERS", "CLI-TOOLS", "FEATURES")},
    {"area": "free tiers: free-first ordering per tier", "status": "USED",
     "reason": "drains free-token sources before paid subscription in every tier",
     "guides": ("FREE_TIERS", "AUTO-COMBO", "FEATURES")},
    {"area": "remote mode", "status": "OPT-IN",
     "reason": "explicit --remote/--base-url/--context only; defaults stay local",
     "guides": ("REMOTE-MODE", "ENVIRONMENT")},
    {"area": "claude-code-configuration", "status": "USED",
     "reason": "subscription auth + isolation defaults encode it; never --bare",
     "guides": ("CLAUDE-CODE-CONFIGURATION", "CODEBASE_DOCUMENTATION")},
    {"area": "environment", "status": "USED",
     "reason": "banner/env-key handling strips wrapper noise before classification",
     "guides": ("ENVIRONMENT", "SETUP_GUIDE")},
    {"area": "architecture", "status": "USED",
     "reason": "gateway-vs-model failure split follows the gateway topology",
     "guides": ("ARCHITECTURE", "CODEBASE_DOCUMENTATION")},
    {"area": "proxy/resilience", "status": "OPT-IN",
     "reason": "failure strings classified + hinted; proxy itself is operator config",
     "guides": ("PROXY_GUIDE", "ARCHITECTURE", "API_REFERENCE")},
    {"area": "api-reference/openapi", "status": "AVOIDED",
     "reason": "no-live-CLI-testability + zero-dependencies: the CLI surface covers every behavior we need",
     "guides": ("API_REFERENCE", "openapi.yaml")},
    {"area": "server lifecycle: serve/stop/restart/status", "status": "AVOIDED",
     "reason": "isolation-default: the library never manages the gateway daemon",
     "guides": ("SETUP_GUIDE", "CLI-TOOLS", "UNINSTALL")},
    {"area": "compression/RTK + context-eng", "status": "AVOIDED",
     "reason": "isolation-default + no-live-CLI-testability: server-side pipeline, operator owned",
     "guides": ("COMPRESSION_GUIDE", "RTK_COMPRESSION", "FEATURES")},
    {"area": "response cache (gateway)", "status": "AVOIDED",
     "reason": "zero-dependencies: the local sha256 cache.py already owns this",
     "guides": ("FEATURES", "CLI-TOOLS")},
    {"area": "translator", "status": "AVOIDED",
     "reason": "no-live-CLI-testability: request-format translation is outside the claude -p contract",
     "guides": ("FEATURES", "API_REFERENCE")},
    {"area": "skills/memory", "status": "AVOIDED",
     "reason": "isolation-default: project skills/memory break the locked-down surface",
     "guides": ("FEATURES", "CODEBASE_DOCUMENTATION")},
    {"area": "provider nodes/sync/telemetry/batches/files", "status": "AVOIDED",
     "reason": "no-live-CLI-testability: fleet management, not per-call execution",
     "guides": ("CLI-TOOLS", "FEATURES", "CODEBASE_DOCUMENTATION")},
    {"area": "uninstall", "status": "AVOIDED",
     "reason": "isolation-default: the library never mutates the host install",
     "guides": ("UNINSTALL", "SETUP_GUIDE")},
)

#: Benchmark sources that cite tier membership (reviewed 2026-09-07).
BENCHMARKS = (
    "openrouter-session-cost",
    "benchlm-arena-elo",
    "livebench-cost-per-task",
    "artificial-analysis",
)

#: Four serving tiers. Each combo lists free targets first and paid subscription
#: last (FREE_TIERS + AUTO-COMBO grounding). Membership examples are operator
#: config data — re-tiering edits OmniRoute combo config + docs, never this file.
TIERS = {
    "ultra": {
        "class": "frontier",
        "examples": ("fable-5.1-class", "GPT-6 Astra-class"),
        "combo": "ultra",
        "free_first": True,
        "benchmarks": BENCHMARKS,
    },
    "high": {
        "class": "opus",
        "examples": ("GPT-5.6 Sol-class", "Gemini 3.8 Flash-class"),
        "combo": "high",
        "free_first": True,
        "benchmarks": BENCHMARKS,
    },
    "middle": {
        "class": "sonnet",
        "examples": ("GPT-5.6 Terra-class", "Muse Spark 1.2-class",
                     "Grok 4.6-class", "Claude Opus 4.7-class"),
        "combo": "middle",
        "free_first": True,
        "benchmarks": BENCHMARKS,
    },
    "low": {
        "class": "bulk-cheap",
        "examples": ("DeepSeek V4 Flash-class", "inclusionai-ling-3.0-flash-class"),
        "combo": "low",
        "free_first": True,
        "benchmarks": ("openrouter-session-cost", "livebench-cost-per-task"),
    },
}

#: Subcommands this module may invoke. `run` is allowed only with `--dry-run`
#: (the plan is printed, nothing executes, no quota is spent).
QUOTA_FREE_SUBCOMMANDS = frozenset(
    {"health", "doctor", "run", "logs", "quota", "simulate",
     "status", "models", "combo", "test", "cost", "usage"}
)

_DETAIL_CAP = 500


def _call(args: list[str], timeout: int) -> tuple[bool, str]:
    """Run one quota-free argv, returning (ok, cleaned detail) — never raises.

    A missing binary, a timeout, or any OSError reports (False, reason) instead
    of raising, so doctor-style callers keep working on a fresh machine.
    """
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout
        )
    except FileNotFoundError:
        if shutil.which("omniroute") is None:
            return False, "omniroute not on PATH — run the OmniRoute SETUP_GUIDE install first"
        return False, "omniroute launch failed: binary not found"
    except subprocess.TimeoutExpired:
        return False, f"omniroute {' '.join(args[1:2])} timed out after {timeout}s"
    except OSError as exc:
        return False, f"omniroute launch failed: {exc}"[:_DETAIL_CAP]
    combined = clean_cli_text((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        detail = combined.strip()[-_DETAIL_CAP:] or f"exit {proc.returncode}"
        return False, detail
    return True, combined.strip()[-_DETAIL_CAP:] or "ok"


def health(timeout: int = 30) -> tuple[bool, str]:
    """Gateway liveness — quota-free."""
    return _call(["omniroute", "health"], timeout)


def doctor(timeout: int = 120) -> tuple[bool, str]:
    """Install integrity — quota-free."""
    return _call(["omniroute", "doctor"], timeout)


def quota_status(timeout: int = 60) -> tuple[bool, str]:
    """Provider quota usage — quota-free (reads counters, spends nothing)."""
    return _call(["omniroute", "quota"], timeout)


def simulate(prompt: str, combo: str | None = None, timeout: int = 60) -> tuple[bool, str]:
    """Routing simulation for a prompt — shows providers, calls none upstream."""
    args = ["omniroute", "simulate"]
    if combo:
        args += ["--combo", combo]
    args.append(prompt)
    return _call(args, timeout)


def dry_run_plan(timeout: int = 60) -> dict:
    """Planned `run claude` command as data — no execution, no quota spent.

    Returns ``{"command": ..., "args": [...], "env_keys": [...]}`` with env key
    NAMES only: the gateway never prints secret values here, and neither do we.
    Raises :class:`ClaudeCliError` (a ``RuntimeError``) when the plan itself fails.
    """
    try:
        proc = subprocess.run(
            ["omniroute", "run", "claude", "--dry-run", "--json"],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"omniroute dry-run timed out after {timeout}s") from exc
    except OSError as exc:
        raise ClaudeCliError(f"omniroute dry-run failed: {exc}") from exc
    if proc.returncode != 0:
        detail = clean_cli_text((proc.stderr or "") + "\n" + (proc.stdout or ""))
        raise ClaudeCliError(f"omniroute dry-run failed: {detail.strip()[-300:]}")
    try:
        plan = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCliError(f"omniroute dry-run returned non-JSON: {(proc.stdout or '')[-200:]}") from exc
    if not isinstance(plan, dict):
        raise ClaudeCliError("omniroute dry-run returned an unexpected plan shape")
    env = plan.get("env") if isinstance(plan.get("env"), dict) else {}
    keys = env.get("changedOrAdded") or []
    # ponytail: names only — if a future gateway ever echoes values, drop
    # anything shaped like an assignment instead of leaking it.
    env_keys = [k for k in keys if isinstance(k, str) and "=" not in k]
    log.info("omniroute dry-run ok: command=%s env_keys=%d", plan.get("command"), len(env_keys))
    return {"command": plan.get("command"), "args": plan.get("args") or [],
            "env_keys": env_keys}
