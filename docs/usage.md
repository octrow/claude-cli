# More ways to call it

Examples moved out of the README. Routing rules are in [routing.md](https://github.com/octrow/claude-cli/blob/main/docs/routing.md).

## Streaming envelope, for long runs

```python
res = run_claude(prompt, model="sonnet", output_format="stream-json")
```

## Live progress

`stream_claude` calls you back on each event while the run is going.

```python
from claude_cli import stream_claude

res = stream_claude(prompt, lambda ev: print(ev["type"]), model="opus", timeout=3600)
```

## Project mode

Project mode is opt-in and reverses the isolation. It runs a project's own slash command with its `CLAUDE.md` and settings loaded, and lets it write files.

```python
res = stream_claude(
    "/apply data/export/interested/acme.md",
    on_event,
    cwd="~/projects/my-repo",
    slash_commands=True,
    setting_sources="user,project",
    permission_mode="acceptEdits",
    tools="Read,Write,Edit,Bash,WebFetch",
)
```

## Async

```python
from claude_cli import arun_claude

res = await arun_claude(prompt, model="opus")
```

## Cache

```python
from claude_cli import FileCacheStore, cached_run, run_claude

store = FileCacheStore("~/.cache/myapp/llm.db")
text = cached_run(run_claude, store, prompt, model="haiku", purpose="classify", version="v3")
```

`cache_key(model, prompt, schema=..., version=..., purpose=..., tools=...)` is a sha256 over the normalized prompt.
Empty optional parts never change a key, so adding a dimension doesn't invalidate old rows.

`cached_run` doesn't store the prompt by default (`store_prompt=False`), because prompts often carry private data.
The response itself is stored in plaintext, so a new cache file is created owner-only (`0600`, its folder `0700`).
For hit/miss visibility use `store.get` / `store.put` directly. `DictCacheStore` is the in-memory store for tests.

## Batch

One bad chunk never aborts the run.

```python
from claude_cli import run_chunks

results, cost, failed = run_chunks(chunks, summarize, label=str, workers=4)
```

## Picking a route

The combo is chosen by `model` alone. `haiku` / `sonnet` / `opus` go through the gateway's model-to-combo mappings, or you pass a combo name directly.
`tier=` and `profile=` only set `CLAUDE_CONFIG_DIR` (a separate Claude Code settings dir), not the route.

```python
res = run_claude(prompt, model="sub-first")  # explicit combo
```

`direct=True` skips OmniRoute. `claude -p` runs on the account the local Claude Code is logged in to, `model` is a plain Claude model id, and a usage limit ends the session.

```python
res = run_claude(prompt, model="claude-sonnet-5-5", direct=True)
```

## Checks before you spend quota

```python
from claude_cli import health, doctor, quota_status, simulate, dry_run_plan

health()  # omniroute health
simulate("hi", combo="free-stack")
dry_run_plan()  # planned command, args and env key *names* only
```

None of these raise or spend quota; each returns `(ok, text)`.

- `claude_available()` checks that `omniroute` and `claude` are on `PATH`, for callers that fall back to a non-LLM path. `claude_available(direct=True)` checks `claude` only.
- `ping(model="haiku", timeout=90)` makes one real call ("reply with the single word pong") for doctor-style startup checks. It never raises: `(True, "pong via omniroute, 6.2s")` on success, `(False, <cleaned error, up to 200 chars>)` otherwise.
- `ClaudeCliProvider` / `LLMProvider` are a one-method seam (`ask(prompt, *, model=None) -> str`) for swappable backends.

## Replacing a local wrapper

- Mock seam: tests that patched their own `subprocess.run` now patch `claude_cli.runner.subprocess.run` (sync) or `claude_cli.runner.asyncio.create_subprocess_exec` (async), and `claude_cli.runner.shutil.which` for `claude_available`.
- `extract_json` is stricter than the usual first-`{` / last-`}` slice. It takes the first balanced object, so a reply with two top-level objects yields the first one instead of a decode error.
- Per-project defaults: subclass `ClaudeCliProvider` and override `DEFAULT_MODEL` / `DEFAULT_TIMEOUT`. No `__init__` needed.

## Tests

```bash
uv run --extra dev pytest -q
```

The suite fakes `subprocess.run` and `asyncio.create_subprocess_exec`; it never runs the real `claude` binary.
