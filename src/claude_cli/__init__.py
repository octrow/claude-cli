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
    "MODELS",
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
    "extract_json",
    "extract_json_or_none",
    "normalize_prompt",
    "pack_batches",
    "parse_envelope",
    "parse_stream",
    "ping",
    "run_chunks",
    "run_claude",
    "stream_claude",
    "strip_fences",
]
