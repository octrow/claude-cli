"""Response cache: a sha256 key plus a tiny pluggable store.

Every consumer wants the same thing — never pay for the same prompt twice — so
the key recipe and the sqlite table live here once. :class:`FileCacheStore` needs
no server (a single sqlite file, ``CREATE TABLE IF NOT EXISTS``, safe to point at
an existing db); :class:`DictCacheStore` is the in-memory one for tests.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


def normalize_prompt(prompt: str) -> str:
    """Collapse whitespace so trivially different prompts share a cache key."""
    return " ".join(prompt.split())


def cache_key(
    model: str,
    prompt: str,
    *,
    schema: str = "",
    version: str = "",
    purpose: str = "",
    tools: str = "",
) -> str:
    """sha256 over model + normalized prompt + optional schema/version/purpose/tools.

    Optional parts are folded in only when non-empty, so adding a dimension later
    never invalidates rows that never used it. Each part is labeled so equal
    values in different dimensions (schema="x" vs version="x") never collide.
    ``tools`` matters because a tools-on (web-search-grounded) call must not
    share a row with a tools-off one.
    """
    parts = [model, normalize_prompt(prompt)]
    named = (("schema", schema), ("version", version), ("purpose", purpose), ("tools", tools))
    parts += [f"{name}={value}" for name, value in named if value]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


class CacheStore(Protocol):
    """What :func:`cached_run` needs from a backend."""

    def get(self, key: str) -> str | None: ...

    def put(self, key: str, text: str, **meta) -> None: ...


# Namespaced on purpose: consumers point FileCacheStore at existing db files, and
# a generic name like `llm_cache` can collide with a project's own table of the
# same name but a different schema (CREATE IF NOT EXISTS would no-op, INSERT
# would then fail at runtime).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_cli_cache (
    key TEXT PRIMARY KEY,
    model TEXT,
    purpose TEXT,
    prompt TEXT,
    response TEXT NOT NULL,
    cost_usd REAL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
)
"""


class FileCacheStore:
    """A local sqlite3 file — no server, so caching never silently no-ops.

    sqlite3 calls are synchronous but cost microseconds against a local file, so
    there is no thread pool or aiosqlite here.
    """

    def __init__(self, path: str | Path, *, max_age_days: int | None = None) -> None:
        """``max_age_days`` expires rows on read; None (default) keeps them forever.

        A row is never deleted here — an expired hit is simply ignored, so the next
        call re-asks and overwrites it. That keeps ``created_at`` history intact and
        avoids a delete path that could lose a still-valid answer on a clock skew.
        """
        self.path = Path(path).expanduser()
        self.max_age_days = max_age_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        # sqlite3's own context manager only manages the transaction — closing()
        # is what actually releases the file handle after each call.
        return sqlite3.connect(self.path)

    def get(self, key: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT response, created_at FROM claude_cli_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return row[0] if self._fresh(row[1]) else None

    def _fresh(self, created_at: str | None) -> bool:
        if self.max_age_days is None:
            return True
        try:
            written = datetime.fromisoformat(created_at or "")
        except ValueError:  # unparseable timestamp: treat as expired, re-ask rather than trust
            return False
        age = datetime.now(timezone.utc) - written
        return age <= timedelta(days=self.max_age_days)

    def put(self, key: str, text: str, **meta) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO claude_cli_cache "
                "(key, model, purpose, prompt, response, cost_usd, duration_ms, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    meta.get("model"),
                    meta.get("purpose"),
                    meta.get("prompt"),
                    text,
                    meta.get("cost_usd"),
                    meta.get("duration_ms"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()


class DictCacheStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.meta: dict[str, dict] = {}

    def get(self, key: str) -> str | None:
        return self.rows.get(key)

    def put(self, key: str, text: str, **meta) -> None:
        self.rows[key] = text
        self.meta[key] = meta


def cached_run(
    runner,
    store: CacheStore,
    prompt: str,
    *,
    model: str = "sonnet",
    schema: str = "",
    version: str = "",
    purpose: str = "",
    refresh: bool = False,
    store_prompt: bool = False,
    **kwargs,
) -> str:
    """Call ``runner(prompt, model=..., **kwargs)`` behind ``store``; return the text.

    ``runner`` is :func:`claude_cli.runner.run_claude` (or any callable returning
    a :class:`~claude_cli.runner.ClaudeResult`). A cache-write failure is logged,
    never raised — a broken cache must not fail an answer we already have.

    ``store_prompt`` is opt-in: prompts often embed privacy-gated payloads, so by
    default only the sha256 key touches disk and the ``prompt`` column stays NULL.
    Callers needing hit/miss visibility use ``store.get``/``store.put`` directly.
    """
    key = cache_key(
        model, prompt, schema=schema, version=version, purpose=purpose,
        tools=kwargs.get("tools", ""),
    )
    if not refresh:
        hit = store.get(key)
        if hit is not None:
            log.debug("llm cache hit %s", purpose or key[:8])
            return hit
    result = runner(prompt, model=model, **kwargs)
    try:
        store.put(
            key, result.text, model=model, purpose=purpose,
            prompt=prompt if store_prompt else None,
            cost_usd=result.cost_usd, duration_ms=result.duration_ms,
        )
    except (sqlite3.Error, OSError) as exc:
        log.warning("llm cache write failed: %s", exc)
    return result.text
