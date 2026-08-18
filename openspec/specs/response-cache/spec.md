## Purpose

Never pay for the same prompt twice. Define one key recipe, a minimal store
protocol with a serverless sqlite implementation, and a caching wrapper whose
failure modes never cost the caller an answer that was already produced.

## Requirements

### Requirement: The cache key is a labeled sha256 over stable dimensions

`cache_key` MUST be a hex sha256 over the model and the whitespace-normalized
prompt, plus any non-empty `schema`, `version`, `purpose` and `tools` values.
Each optional part MUST be labeled (`schema=x`) so that the same value in two
different dimensions never collides. Empty optional parts MUST be omitted
entirely, so introducing a new dimension later never invalidates existing rows.

#### Scenario: Whitespace differences do not change the key

- **WHEN** two prompts differ only in runs of whitespace
- **THEN** `normalize_prompt` collapses both to the same string
- **Anchor**: `tests/test_cache.py::test_normalize_prompt_collapses_whitespace`

#### Scenario: The key is a stable hex sha256

- **WHEN** `cache_key` is called twice with identical inputs
- **THEN** it returns the same 64-character hex digest both times
- **Anchor**: `tests/test_cache.py::test_cache_key_is_stable_and_hex_sha256`

#### Scenario: Every dimension participates in the key

- **WHEN** any one of model, prompt, schema, version, purpose or tools changes
- **THEN** the resulting key changes
- **Anchor**: `tests/test_cache.py::test_cache_key_varies_with_every_dimension`

#### Scenario: The same value in different dimensions does not collide

- **WHEN** one call passes a value as `schema` and another passes the identical
  value as `version`
- **THEN** the two keys differ
- **Anchor**: `tests/test_cache.py::test_cache_key_same_value_in_different_dimensions_differs`

#### Scenario: Empty optionals are inert

- **WHEN** `cache_key` is called with all optional dimensions empty and again with
  them omitted
- **THEN** both produce the same key
- **Anchor**: `tests/test_cache.py::test_cache_key_empty_optionals_do_not_change_key`

### Requirement: Tools-on and tools-off calls never share a row

The `tools` value MUST participate in the key. A tools-enabled, web-grounded
answer and a tools-disabled answer to the same prompt are different answers and
MUST NOT be served from the same cache row.

#### Scenario: A tools value separates cache rows

- **WHEN** the same prompt and model are run once with `tools` set and once
  without
- **THEN** the second call is a miss and invokes the runner rather than returning
  the first call's answer
- **Anchor**: `tests/test_cache.py::test_cached_run_tools_do_not_share_a_row`

### Requirement: The store is a two-method protocol with a serverless default

`CacheStore` MUST require only `get(key)` and `put(key, text, **meta)`.
`FileCacheStore` MUST implement it over a single local sqlite file, creating its
parent directory and its table with `CREATE TABLE IF NOT EXISTS` so it is safe to
point at an existing database. The table name MUST be namespaced
(`claude_cli_cache`) so it cannot collide with a consumer's own table of a
generic name and different schema. `DictCacheStore` MUST provide the in-memory
implementation used by tests.

#### Scenario: A stored response round-trips

- **WHEN** a response is written to a `FileCacheStore` and read back by key
- **THEN** the stored text is returned along with its recorded metadata
- **Anchor**: `tests/test_cache.py::test_file_store_roundtrip`

#### Scenario: Writing the same key twice replaces the row

- **WHEN** two responses are written under the same key
- **THEN** the later write replaces the earlier one rather than raising or
  duplicating
- **Anchor**: `tests/test_cache.py::test_file_store_overwrites_same_key`

### Requirement: Expiry is evaluated on read and never deletes rows

`FileCacheStore(max_age_days=N)` MUST treat a row older than `N` days as a miss.
`max_age_days=None`, the default, keeps rows forever. An expired row MUST NOT be
deleted — it is simply ignored, so the next call re-asks and overwrites it. That
keeps `created_at` history intact and avoids a delete path that could discard a
still-valid answer under clock skew. An unparseable timestamp MUST be treated as
expired: re-ask rather than trust it.

#### Scenario: Rows past max_age are misses

- **WHEN** a row's `created_at` is older than `max_age_days`
- **THEN** `get` returns `None`
- **Anchor**: `tests/test_cache.py::test_file_store_expires_rows_older_than_max_age`

#### Scenario: Without max_age nothing expires

- **WHEN** a store is constructed with no `max_age_days` and an old row is read
- **THEN** the stored text is returned
- **Anchor**: `tests/test_cache.py::test_file_store_without_max_age_keeps_everything`

### Requirement: cached_run consults the store before invoking the runner

`cached_run` MUST return a cached hit without calling the runner, and on a miss
MUST call `runner(prompt, model=..., **kwargs)`, store the result text with its
model, purpose, cost and duration, and return that text. `refresh=True` MUST
bypass the read while still writing the fresh answer back. Extra keyword
arguments MUST be forwarded to the runner unchanged.

#### Scenario: A miss calls the runner, a repeat is served from cache

- **WHEN** the same prompt is run twice through `cached_run`
- **THEN** the runner is invoked exactly once and both calls return the same text
- **Anchor**: `tests/test_cache.py::test_cached_run_misses_then_hits`

#### Scenario: refresh bypasses an existing hit

- **WHEN** `cached_run` is called with `refresh=True` on a key that is present
- **THEN** the runner is invoked and the stored row is replaced
- **Anchor**: `tests/test_cache.py::test_cached_run_refresh_bypasses_hit`

#### Scenario: Extra kwargs reach the runner

- **WHEN** `cached_run` is given additional keyword arguments
- **THEN** they are passed through to the runner call unchanged
- **Anchor**: `tests/test_cache.py::test_cached_run_forwards_kwargs`

### Requirement: Prompts are not persisted by default

`cached_run` MUST default `store_prompt=False`, leaving the `prompt` column
`NULL`. Prompts often embed privacy-gated payloads, so by default only the sha256
key touches disk. Callers who need prompt visibility opt in explicitly or use
`store.get`/`store.put` directly.

#### Scenario: The prompt column stays empty by default

- **WHEN** `cached_run` writes a row without `store_prompt=True`
- **THEN** the persisted prompt value is `None`
- **Anchor**: `tests/test_cache.py::test_cached_run_does_not_store_prompt_by_default`

### Requirement: A broken cache never costs an answer

A failure while writing to the store MUST be logged and swallowed, never raised.
The answer has already been paid for and produced; a broken cache MUST NOT turn a
successful run into an exception.

#### Scenario: A store that raises on put still returns the answer

- **WHEN** the store's `put` raises during `cached_run`
- **THEN** the runner's answer text is still returned to the caller
- **Anchor**: `tests/test_cache.py::test_cached_run_survives_a_broken_store`
