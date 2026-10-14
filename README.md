# claude-subprocess

```bash
uv add claude-subprocess   # imported as `claude_cli`
```

One hardened wrapper around the OmniRoute gateway: tool-less calls go over its HTTP API,
everything else (and any HTTP failure) over `omniroute run claude -- -p` (Claude Code
**subscription** / OAuth — there is no `ANTHROPIC_API_KEY`), extracted from six
independent copies of the same subprocess contract (dialogue-lens,
tiktok-lazy-follower, get_cool_event, finance-insights, chat-watch, buy-smarter).

stdlib only. Python ≥3.12.

## Invariants (the reason this library exists)

* **NEVER pass `--bare`** — it forces API-key-only mode and breaks subscription
  auth. The flag is intentionally absent from `build_args`; a test guards it.
* **OmniRoute is mandatory** — every Claude Code subprocess is launched via
  `omniroute run claude`, and the HTTP route talks to the same gateway, so it owns routing.
* **Isolated surface**: an empty temp mcp-config + `--strict-mcp-config`,
  `--setting-sources user`, `--disable-slash-commands`,
  `--no-session-persistence`.
* **Throwaway cwd** for every call, so no project `CLAUDE.md` is auto-loaded.
* **`--tools ""` by default.** A non-empty `tools` value goes to *both* `--tools`
  and `--allowed-tools` — headless `-p` auto-denies anything not allow-listed.
* `"Not logged in"` in stdout+stderr → `NotLoggedInError` (actionable).
* Usage/rate/session-limit signatures → `UsageLimitError`, raised *before* the
  exit code so callers fail fast instead of burning the rest of a batch.
  Deliberately narrow: `context limit exceeded` is **not** a usage limit.
* Both output shapes supported: `--output-format json` (one envelope) and
  `--output-format stream-json` (final `{"type":"result"}` event); both are
  normalized into the same `ClaudeResult`.

All errors derive from `ClaudeCliError(RuntimeError)`, so `except RuntimeError`
graceful-degrade paths keep working.

When the failure is the gateway's rather than Claude's, the error text says so and points at
OmniRoute's own docs and issues. A 503 is ambiguous there — a spent quota and
`ALL_TARGETS_SKIPPED` (no provider matched at all) share the number, and only the first is worth
waiting out. [docs/OMNIROUTE.md](docs/OMNIROUTE.md) has the diagnosis order, the recognisable
error strings, and the checks that cost no quota.

## Usage

```python
from claude_cli import run_claude, extract_json

res = run_claude("Reply with {\"ok\": true}", model="haiku", effort="low", timeout=60)
res.text        # the model's answer
res.cost_usd    # subscription cost the envelope reported (may be None)
res.duration_ms
res.raw         # the full envelope dict
extract_json(res.text)   # {'ok': True} — tolerates ```fences``` and prose
```

HTTP route: `run_claude`/`arun_claude` first POST tool-less calls to OmniRoute's
`/v1/chat/completions` (key from `OMNIROUTE_API_KEY` or `~/.omniroute/.env`, URL from
`OMNIROUTE_URL`, default `http://localhost:20128`; no key = skipped). A failure logs a
warning, falls back to the CLI and parks HTTP for 600 s per process. Calls with
`tools`/`allowed_tools`, a project `cwd` (one holding `CLAUDE.md` or `.claude/`),
`slash_commands`, remote overrides or `output_format="stream-json"` stay on the CLI, as does
`ping`. A tool-less `stream_claude` goes over HTTP too and then emits no events. Opt out with
`backend="cli"` (or `CLAUDE_CLI_BACKEND=cli`, which wins over the kwarg); `backend="api"` is
HTTP only. HTTP results have `cost_usd=0.0` and `effort` is ignored. No `temperature` is
sent (it would enable OmniRoute's semantic cache). Consumer test suites that fake the CLI
must set `CLAUDE_CLI_BACKEND=cli`, or they reach the live gateway. Details, decisions,
consumer migration notes and the list of what still runs on the CLI:
[docs/OMNIROUTE.md](docs/OMNIROUTE.md#http-route-2026-10).

Streaming envelope (long runs, tlf/gce style):

```python
res = run_claude(prompt, model="sonnet", output_format="stream-json")
```

Live progress — `stream_claude` calls back per event *while the run is going*, instead of
handing you the whole stream at the end (stackpulse `apply_batch` style):

```python
from claude_cli import stream_claude

res = stream_claude(prompt, lambda ev: print(ev["type"]), model="opus", timeout=3600)
```

Project mode (opt-in, reverses the isolation invariants above) — run a project's own slash
command with its `CLAUDE.md` and settings loaded, and let it write files:

```python
res = stream_claude(
    "/apply data/export/interested/acme.md", on_event,
    cwd="~/dev/cv-adapter", slash_commands=True,
    setting_sources="user,project", permission_mode="acceptEdits",
    tools="Read,Write,Edit,Bash,WebFetch",
)
```

Async (buy-smarter style):

```python
from claude_cli import arun_claude

res = await arun_claude(prompt, model="opus")
```

Cached (chat-watch / finance-insights style):

```python
from claude_cli import FileCacheStore, cached_run, run_claude

store = FileCacheStore("~/.cache/myapp/llm.db")
text = cached_run(run_claude, store, prompt, model="haiku",
                  purpose="classify", version="v3")
```

`DictCacheStore` is the in-memory store for tests. `cache_key(model, prompt,
schema=..., version=..., purpose=..., tools=...)` is sha256 over the normalized
prompt; empty optional parts never change a key, so new dimensions don't
invalidate old rows.

Batch (dialogue-lens style) — one bad chunk never aborts the run:

```python
from claude_cli import run_chunks

results, cost, failed = run_chunks(chunks, summarize, label=str, workers=4)
```

`claude_available()` reports whether the CLI is on PATH, for callers that degrade
to a non-LLM path. `ClaudeCliProvider` / `LLMProvider` are the one-method
(`ask(prompt, *, model=None) -> str`) seam for swappable backends.

`ping(model="haiku", timeout=90) -> (bool, str)` makes one real call through
OmniRoute ("reply with the single word pong") for "doctor"-style startup
checks that want proof the whole path works, not just `claude_available()`'s
PATH check. Never raises: `(True, "pong via omniroute, 6.2s")` on success,
`(False, <cleaned error, ≤200 chars>)` on any exception or timeout.

Quota-free preflight (never raises, never spends quota):

```python
from claude_cli import health, doctor, quota_status, simulate, dry_run_plan

health()                          # (True, ...) — omniroute health
simulate("hi", combo="free-first")
dry_run_plan()                    # (True, '{"command", "args", "env_keys"}') — key *names* only
```

The combo is picked by `--model` only: `haiku`/`sonnet`/`opus` go through the
gateway's model→combo mappings, or pass a combo name directly. `tier=`/`profile=`
only set `CLAUDE_CONFIG_DIR` (a separate Claude Code settings dir), not the route —
see `docs/OMNIROUTE.md`, "Как выбирается комбо":

```python
res = run_claude(prompt, model="sub-first")  # explicit combo
```

Before classification and truncation, stderr/stdout are cleaned of ANSI codes
and OmniRoute's own banner/warning lines (env-file banner, "is ignored, ...
set it first", disabled-connectors notice) so a real error at the tail of a
noisy run isn't crowded out by wrapper noise; `ClaudeCliError.message` carries
the cleaned text.

`cached_run` never persists the prompt by default (`store_prompt=False`) — prompts
often embed privacy-gated payloads, so only the sha256 key touches disk. Callers
needing hit/miss visibility use `store.get`/`store.put` directly.

## Migration notes (for repos replacing their local wrapper)

* **Mock seam**: consumer tests that used to patch their own `subprocess.run`
  must now patch `claude_cli.runner.subprocess.run` (sync) or
  `claude_cli.runner.asyncio.create_subprocess_exec` (async), and
  `claude_cli.runner.shutil.which` for `claude_available`.
* **`extract_json` is stricter** than copy-pasted find-first-`{`/rfind-last-`}`
  variants: it takes the first *balanced* `{...}` object. Replies with two
  top-level objects now yield the first instead of a decode error.
* Per-project defaults: subclass `ClaudeCliProvider` and override the
  `DEFAULT_MODEL` / `DEFAULT_TIMEOUT` class attributes — no `__init__` needed.

## Tests

```bash
cd claude-cli && uv run --extra dev pytest -q
```

The suite fakes `subprocess.run` / `asyncio.create_subprocess_exec` — it never
invokes the real `claude` binary.
