"""claude-cli — one hardened wrapper around the local ``claude -p`` CLI.

Subscription/OAuth auth (no ``ANTHROPIC_API_KEY``), a locked-down surface, both
output formats, JSON extraction from prose replies, and a sha256 response cache.
See :mod:`claude_cli.runner` for the invariants this library exists to protect.
"""

from .batch import pack_batches, run_chunks
from .cache import (
    CacheStore,
    DictCacheStore,
    FileCacheStore,
    cache_key,
    cached_run,
    normalize_prompt,
)
from .errors import ClaudeCliError, NotLoggedInError, UsageLimitError
from .parsing import (
    extract_json,
    extract_json_or_none,
    parse_envelope,
    parse_stream,
    strip_fences,
)
from .preflight import (
    BENCHMARKS,
    CONSTRAINTS,
    GUIDES,
    MATRIX,
    QUOTA_FREE_SUBCOMMANDS,
    TIERS,
    doctor,
    dry_run_plan,
    health,
    quota_status,
    simulate,
)
from .runner import (
    MODELS,
    ClaudeCliProvider,
    ClaudeResult,
    LLMProvider,
    arun_claude,
    build_args,
    claude_available,
    ping,
    run_claude,
    stream_claude,
)

__all__ = [
    "BENCHMARKS",
    "CONSTRAINTS",
    "GUIDES",
    "MATRIX",
    "MODELS",
    "QUOTA_FREE_SUBCOMMANDS",
    "TIERS",
    "CacheStore",
    "ClaudeCliError",
    "ClaudeCliProvider",
    "ClaudeResult",
    "DictCacheStore",
    "FileCacheStore",
    "LLMProvider",
    "NotLoggedInError",
    "UsageLimitError",
    "arun_claude",
    "build_args",
    "cache_key",
    "cached_run",
    "claude_available",
    "doctor",
    "dry_run_plan",
    "extract_json",
    "extract_json_or_none",
    "health",
    "normalize_prompt",
    "pack_batches",
    "parse_envelope",
    "parse_stream",
    "ping",
    "quota_status",
    "run_chunks",
    "run_claude",
    "simulate",
    "stream_claude",
    "strip_fences",
]
