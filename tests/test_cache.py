from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from claude_cli import (
    ClaudeCliProvider,
    ClaudeResult,
    DictCacheStore,
    FileCacheStore,
    cache_key,
    cached_run,
    normalize_prompt,
)


def test_normalize_prompt_collapses_whitespace():
    assert normalize_prompt("  a\n\tb   c ") == "a b c"


def test_cache_key_is_stable_and_hex_sha256():
    key = cache_key("sonnet", "hello world")
    assert key == cache_key("sonnet", "  hello   world\n")
    assert len(key) == 64 and int(key, 16) >= 0


def test_cache_key_varies_with_every_dimension():
    base = cache_key("sonnet", "p")
    assert base != cache_key("haiku", "p")
    assert base != cache_key("sonnet", "q")
    assert base != cache_key("sonnet", "p", schema="Answer")
    assert base != cache_key("sonnet", "p", version="v2")
    assert base != cache_key("sonnet", "p", purpose="summary")
    assert base != cache_key("sonnet", "p", tools="WebSearch")


def test_cache_key_same_value_in_different_dimensions_differs():
    assert cache_key("sonnet", "p", schema="x") != cache_key("sonnet", "p", version="x")
    assert cache_key("sonnet", "p", purpose="x") != cache_key("sonnet", "p", tools="x")


def test_cache_key_empty_optionals_do_not_change_key():
    assert cache_key("sonnet", "p") == cache_key(
        "sonnet", "p", schema="", version="", purpose="", tools=""
    )


def test_file_store_roundtrip(tmp_path):
    store = FileCacheStore(tmp_path / "nested" / "llm.db")
    assert store.get("k") is None
    store.put("k", "text", model="haiku", cost_usd=0.5, duration_ms=12, prompt="p")
    assert store.get("k") == "text"
    # reopening the same file must not wipe or fail (CREATE TABLE IF NOT EXISTS)
    assert FileCacheStore(tmp_path / "nested" / "llm.db").get("k") == "text"


def test_file_store_overwrites_same_key(tmp_path):
    store = FileCacheStore(tmp_path / "llm.db")
    store.put("k", "old")
    store.put("k", "new")
    assert store.get("k") == "new"


def _runner(text="answer", calls=None):
    def run(prompt, *, model="sonnet", **kwargs):
        if calls is not None:
            calls.append((prompt, model, kwargs))
        return ClaudeResult(text=text, cost_usd=0.1, duration_ms=5)

    return run


def test_cached_run_misses_then_hits():
    calls: list = []
    store = DictCacheStore()
    for _ in range(2):
        assert cached_run(_runner(calls=calls), store, "p", model="haiku") == "answer"
    assert len(calls) == 1  # second call served from cache
    key = cache_key("haiku", "p")
    assert store.meta[key]["cost_usd"] == 0.1
    assert store.meta[key]["duration_ms"] == 5


def test_cached_run_does_not_store_prompt_by_default():
    store = DictCacheStore()
    cached_run(_runner(), store, "secret payload")
    assert store.meta[cache_key("sonnet", "secret payload")]["prompt"] is None
    cached_run(_runner(), store, "secret payload", refresh=True, store_prompt=True)
    assert store.meta[cache_key("sonnet", "secret payload")]["prompt"] == "secret payload"


def test_provider_class_level_defaults_are_overridable():
    class Pinned(ClaudeCliProvider):
        DEFAULT_MODEL = "haiku"
        DEFAULT_TIMEOUT = 120

    p = Pinned()
    assert p.model == "haiku"
    assert p.kwargs["timeout"] == 120


def test_cached_run_refresh_bypasses_hit():
    calls: list = []
    store = DictCacheStore()
    cached_run(_runner(calls=calls), store, "p")
    cached_run(_runner(calls=calls), store, "p", refresh=True)
    assert len(calls) == 2


def test_cached_run_tools_do_not_share_a_row():
    store = DictCacheStore()
    cached_run(_runner(text="no-tools"), store, "p")
    assert cached_run(_runner(text="tools"), store, "p", tools="WebSearch") == "tools"
    assert store.get(cache_key("sonnet", "p")) == "no-tools"


def test_cached_run_forwards_kwargs():
    calls: list = []
    cached_run(
        _runner(calls=calls), DictCacheStore(), "p",
        model="opus", effort="high", timeout=10, output_format="stream-json",
    )
    _, model, kwargs = calls[0]
    assert model == "opus"
    assert kwargs == {"effort": "high", "timeout": 10, "output_format": "stream-json"}


def test_cached_run_survives_a_broken_store():
    class Broken(DictCacheStore):
        def put(self, key, text, **meta):
            raise OSError("disk full")

    assert cached_run(_runner(), Broken(), "p") == "answer"


def test_file_store_expires_rows_older_than_max_age(tmp_path):
    """A year-old answer is worth reusing; a stale one must not silently win."""
    store = FileCacheStore(tmp_path / "llm.db", max_age_days=365)
    store.put("k", "answer")
    assert store.get("k") == "answer"

    # rewrite created_at to 400 days ago, straight in the file
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    with closing(sqlite3.connect(tmp_path / "llm.db")) as conn:
        conn.execute("UPDATE claude_cli_cache SET created_at = ?", (old,))
        conn.commit()
    assert store.get("k") is None


def test_file_store_without_max_age_keeps_everything(tmp_path):
    store = FileCacheStore(tmp_path / "llm.db")
    store.put("k", "answer")
    old = (datetime.now(timezone.utc) - timedelta(days=4000)).isoformat()
    with closing(sqlite3.connect(tmp_path / "llm.db")) as conn:
        conn.execute("UPDATE claude_cli_cache SET created_at = ?", (old,))
        conn.commit()
    assert store.get("k") == "answer"
