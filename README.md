<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/hero-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/hero-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/hero-dark.svg">
    <img src="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/hero-light.svg" width="100%" alt="claude-subprocess: call claude -p from Python on your Claude Code subscription, with the risky flags locked down. Beside the title, the real argv build_args(model='haiku') produces: omniroute run claude -- -p --model haiku --effort low --output-format json --no-session-persistence --tools '' --disable-slash-commands --setting-sources user --strict-mcp-config --mcp-config mcp-empty.json. --bare is never sent because it breaks OAuth.">
  </picture>
</p>

[![CI](https://github.com/octrow/claude-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/octrow/claude-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/claude-subprocess.svg)](https://pypi.org/project/claude-subprocess/)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/octrow/claude-cli/blob/main/LICENSE)

A Python wrapper for running Claude Code headless (`claude -p`) on your Claude Code
**subscription** (OAuth). There is no `ANTHROPIC_API_KEY` anywhere. Calls go through the
[OmniRoute](https://github.com/octrow/claude-cli/blob/main/docs/OMNIROUTE.md) gateway, which picks the route. The wrapper handles the flags,
the isolation, the error classification and the parsing that every project calling
`claude -p` ends up writing again. Six of my projects had their own copy of this code; this
library replaced them.

Standard library only, Python 3.12+.

## Install

```bash
uv add claude-subprocess   # or: pip install claude-subprocess
```

The PyPI name is `claude-subprocess`, the import name is `claude_cli`. At runtime you need
`omniroute` and `claude` on `PATH`, with Claude Code logged in.

## A call

```python
from claude_cli import run_claude, extract_json

res = run_claude('Reply with {"ok": true}', model="haiku", effort="low", timeout=60)
res.text         # the model's answer
res.cost_usd     # cost the envelope reported (may be None)
res.duration_ms
res.raw          # the full envelope dict
extract_json(res.text)   # {'ok': True}
```

`extract_json` tolerates code fences and prose around the object:

```python
>>> extract_json('Sure! Here it is:\n```json\n{"ok": true}\n```\nAnything else?')
{'ok': True}
```

## How a call is routed

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/flow-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/flow-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/flow-dark.svg">
  <img src="https://raw.githubusercontent.com/octrow/claude-cli/main/assets/readme/flow-light.svg" width="100%" alt="run_claude, arun_claude and stream_claude send tool-less calls to OmniRoute's HTTP API at /v1/chat/completions. Calls with tools, a project cwd or stream-json go through omniroute run claude -- -p with a throwaway cwd and an empty MCP config. An HTTP error falls back to the CLI and parks HTTP for 600 seconds. Both paths return a ClaudeResult. NotLoggedInError and UsageLimitError fail fast and derive from ClaudeCliError(RuntimeError).">
</picture>

Tool-less `run_claude` / `arun_claude` calls are POSTed to OmniRoute's `/v1/chat/completions`.
The key comes from `OMNIROUTE_API_KEY` or `~/.omniroute/.env`, the URL from `OMNIROUTE_URL`
(default `http://localhost:20128`). Without a key the HTTP route is skipped. If it fails, the
wrapper logs a warning, falls back to the CLI and stops trying HTTP for 600 s in that process.

These stay on the CLI: calls with `tools`/`allowed_tools`, a project `cwd` (one holding
`CLAUDE.md` or `.claude/`), `slash_commands`, remote overrides, `output_format="stream-json"`,
and `ping`. A tool-less `stream_claude` also goes over HTTP and then emits no events.

* `backend="cli"` (or `CLAUDE_CLI_BACKEND=cli`, which wins over the kwarg) turns HTTP off;
  `backend="api"` uses HTTP only.
* HTTP results have `cost_usd=0.0`, and `effort` is ignored there.
* No `temperature` is sent, because it would turn on OmniRoute's semantic cache.
* Test suites that fake the CLI must set `CLAUDE_CLI_BACKEND=cli`, or they reach the live gateway.

Decisions, migration notes and what still runs on the CLI:
[docs/OMNIROUTE.md](https://github.com/octrow/claude-cli/blob/main/docs/OMNIROUTE.md#http-route-2026-10).

## Invariants

These are the reason the library exists.

* **`--bare` is never passed.** It forces API-key-only mode and breaks subscription auth.
  `build_args` has no way to emit it, and a test checks that.
* **OmniRoute is mandatory.** Every Claude Code subprocess is launched via
  `omniroute run claude`, and the HTTP route talks to the same gateway.
* **Isolated surface**: an empty temp MCP config with `--strict-mcp-config`,
  `--setting-sources user`, `--disable-slash-commands`, `--no-session-persistence`.
* **Throwaway cwd** for every call, so no project `CLAUDE.md` gets loaded.
* **`--tools ""` by default.** A non-empty `tools` value goes to both `--tools` and
  `--allowed-tools`, because headless `-p` denies anything that isn't allow-listed.
* **Both output shapes**: `--output-format json` (one envelope) and `stream-json` (the final
  `{"type":"result"}` event) are normalized into the same `ClaudeResult`.

## Errors

All errors derive from `ClaudeCliError(RuntimeError)`, so existing `except RuntimeError`
fallbacks keep working.

* `"Not logged in"` in stdout or stderr raises `NotLoggedInError`.
* Usage, rate and session-limit messages raise `UsageLimitError` before the exit code is
  checked, so a batch stops at the first one instead of burning through the rest. The match
  is narrow on purpose: `context limit exceeded` is not a usage limit.
* Before classification, stdout and stderr are stripped of ANSI codes and OmniRoute's own
  banner and warning lines, so the real error at the tail isn't buried.
  `ClaudeCliError.message` carries the cleaned text.

When the failure looks like the gateway's, the message says so and lists the OmniRoute checks
that spend no quota. A 503 there is ambiguous: a spent quota and `ALL_TARGETS_SKIPPED` (no
provider matched) share the code, and only the first is worth waiting out.
[docs/OMNIROUTE.md](https://github.com/octrow/claude-cli/blob/main/docs/OMNIROUTE.md) has the diagnosis order and the error strings.

## More ways to call it

Streaming envelope, for long runs:

```python
res = run_claude(prompt, model="sonnet", output_format="stream-json")
```

Live progress: `stream_claude` calls you back on each event while the run is going.

```python
from claude_cli import stream_claude

res = stream_claude(prompt, lambda ev: print(ev["type"]), model="opus", timeout=3600)
```

Project mode is opt-in and reverses the isolation above. It runs a project's own slash command
with its `CLAUDE.md` and settings loaded, and lets it write files:

```python
res = stream_claude(
    "/apply data/export/interested/acme.md", on_event,
    cwd="~/projects/my-repo", slash_commands=True,
    setting_sources="user,project", permission_mode="acceptEdits",
    tools="Read,Write,Edit,Bash,WebFetch",
)
```

Async:

```python
from claude_cli import arun_claude

res = await arun_claude(prompt, model="opus")
```

Cached:

```python
from claude_cli import FileCacheStore, cached_run, run_claude

store = FileCacheStore("~/.cache/myapp/llm.db")
text = cached_run(run_claude, store, prompt, model="haiku",
                  purpose="classify", version="v3")
```

`cache_key(model, prompt, schema=..., version=..., purpose=..., tools=...)` is a sha256 over
the normalized prompt. Empty optional parts never change a key, so adding a dimension doesn't
invalidate old rows. `cached_run` doesn't store the prompt by default (`store_prompt=False`):
prompts often carry private data, so only the key touches disk. For hit/miss visibility use
`store.get` / `store.put` directly. `DictCacheStore` is the in-memory store for tests.

Batch, where one bad chunk never aborts the run:

```python
from claude_cli import run_chunks

results, cost, failed = run_chunks(chunks, summarize, label=str, workers=4)
```

Picking a route: the combo is chosen by `model` alone. `haiku`/`sonnet`/`opus` go through
the gateway's model-to-combo mappings, or you pass a combo name directly. `tier=` and
`profile=` only set `CLAUDE_CONFIG_DIR` (a separate Claude Code settings dir), not the route.

```python
res = run_claude(prompt, model="sub-first")  # explicit combo
```

## Checks before you spend quota

```python
from claude_cli import health, doctor, quota_status, simulate, dry_run_plan

health()                          # omniroute health
simulate("hi", combo="free-first")
dry_run_plan()                    # planned command, args and env key *names* only
```

None of these raise or spend quota; each returns `(ok, text)`.

* `claude_available()` checks that `omniroute` and `claude` are on `PATH`, for callers that
  fall back to a non-LLM path.
* `ping(model="haiku", timeout=90)` makes one real call ("reply with the single word pong")
  for doctor-style startup checks. It never raises: `(True, "pong via omniroute, 6.2s")` on
  success, `(False, <cleaned error, up to 200 chars>)` otherwise.
* `ClaudeCliProvider` / `LLMProvider` are a one-method seam
  (`ask(prompt, *, model=None) -> str`) for swappable backends.

## Replacing a local wrapper

* **Mock seam**: tests that patched their own `subprocess.run` now patch
  `claude_cli.runner.subprocess.run` (sync) or
  `claude_cli.runner.asyncio.create_subprocess_exec` (async), and
  `claude_cli.runner.shutil.which` for `claude_available`.
* **`extract_json` is stricter** than the usual first-`{` / last-`}` slice: it takes the first
  *balanced* object. A reply with two top-level objects now yields the first one instead of a
  decode error.
* **Per-project defaults**: subclass `ClaudeCliProvider` and override `DEFAULT_MODEL` /
  `DEFAULT_TIMEOUT`. No `__init__` needed.

## Tests

```bash
uv run --extra dev pytest -q
```

The suite fakes `subprocess.run` and `asyncio.create_subprocess_exec`; it never runs the real
`claude` binary.

## License

[MIT](https://github.com/octrow/claude-cli/blob/main/LICENSE). Contributions: [CONTRIBUTING.md](https://github.com/octrow/claude-cli/blob/main/CONTRIBUTING.md).
