# Logic map

How one prompt becomes a `ClaudeResult` (or a typed error).
The map describes the code as it is today, step by step.
Each step says what it decides, where the flow branches, and which module does it.

Terms: "gateway" is the local OmniRoute; "combo" is an OmniRoute route (`free-stack`, `hard`, `sub-first`);
"direct" is a plain `claude -p` on the local login. `docs/OMNIROUTE.md` has the gateway details.
Known gaps and fixes in progress are in `docs/plans/2026-11-01-logic-map-fixes.md`.

Rule: if you change a step or a branch, update this file and the mirror in the same commit.
`tests/test_logic_map.py` checks that every `Module:` line points to a real module.
The Russian mirror is `docs/logic-map.ru.md`; this file is the source of truth.

## The flow at a glance

```
prompt + kwargs
  │
  ├─ 1  Direct or gateway ...... skip OmniRoute entirely?
  ├─ 2  CLI-only check ......... does the call need something HTTP cannot carry?
  ├─ 3  Backend resolution ..... env/kwarg, key, cooldown → try HTTP or not
  ├─ 4  HTTP call .............. POST /v1/chat/completions, one combo name
  ├─ 5  HTTP failure ........... fall back to the CLI (auto) or raise (api)
  ├─ 6  Argv ................... validate kwargs, build `omniroute run … claude -- -p …`
  ├─ 7  Sandbox ................ temp cwd, empty MCP config, child env
  ├─ 8  Spawn and wait ......... one subprocess, timeout kills the process group
  ├─ 9  Parse output ........... json envelope or folded stream-json result event
  ├─ 10 Classify failure ....... not logged in → gateway exhausted → usage limit → exit code
  └─ 11 Success checks ......... is_error, empty result
  │
ClaudeResult | NotLoggedInError | UsageLimitError | ClaudeCliError
```

An HTTP answer leaves the flow at step 4. Steps 6–11 run only on the CLI path.
Nothing here retries: one CLI spawn per call (Claude Code itself retries inside, capped by step 7).

## Where the flow splits by route

Three routes reach a model: HTTP to the gateway, CLI through `omniroute run`, CLI direct.
The route is decided in four places by independent signals, not one registry.

| Route / variant | Switched on by | Steps it changes | What changes |
|---|---|---|---|
| CLI direct | `direct=True` (only `run_claude` has it) | 1, 6, 7, 10 | no `omniroute` prefix, no HTTP, gateway env dropped, limit is `session_wide=True` |
| CLI forced | `model` starting with `claude-`, `tools`, `allowed_tools`, `slash_commands`, remote opts, `output_format="stream-json"`, `cwd` holding `CLAUDE.md` or `.claude` | 2 | HTTP skipped |
| HTTP off | `CLAUDE_CLI_BACKEND`/`backend` = `cli`; no `OMNIROUTE_API_KEY`; cooldown active | 3 | CLI through gateway |
| HTTP only | `CLAUDE_CLI_BACKEND`/`backend` = `api` | 3, 5 | no fallback, no cooldown, errors raised; a step-2 signal or `direct` with it → `ValueError` |
| Model → combo | `COMBOS` dict on HTTP; gateway glob mappings on CLI | 4, 6 | `haiku`/`sonnet` → `free-stack`, `opus` → `hard`; a bare combo name verbatim on both; `claude-*` ids only on the CLI (glob) |
| Failure class | regexes over cleaned stdout+stderr of a failed run | 10 | which exception type, `session_wide`, gateway hint |

Examples:
- `run_claude("summarize …")` with a key in `~/.omniroute/.env` → HTTP, combo `free-stack`, `cost_usd=0.0`.
- `run_claude("…", tools="Read")` → CLI through gateway, `--tools Read --allowed-tools Read`.
- `run_claude("…", model="claude-sonnet-5-5", direct=True)` → no `omniroute run` prefix, plain `claude -p`, local login pays.
- `stream_claude("…", on_event)` with no tools and a key → HTTP; `on_event` is never called.

Module: `runner`, `http_api`

## Entry points

All model calls go through steps 1–11. They differ in defaults and in what they forward.

| Entry | Backend | `direct` | Output format | Notes |
|---|---|---|---|---|
| `run_claude` | `auto` | yes | `json` (kwarg) | blocking; the only entry with `direct` |
| `arun_claude` | `auto` | **absent** | `json` (kwarg) | HTTP step runs in `asyncio.to_thread` (cancelling the task does not stop that thread's request); CLI via `asyncio.create_subprocess_exec` |
| `stream_claude` | `auto` | **absent** | forced `stream-json` | step 2 is told `json`, so a tool-less stream goes HTTP and emits no events |
| `ping` | `cli` kwarg | no | `json` | `model="haiku"` (→ `free-stack`, no Claude-subscription target), `timeout=90`, prompt "reply with the single word pong"; never raises (catches `Exception`); always CLI (env cannot flip an explicit `cli`); the reply text is not checked |
| `ClaudeCliProvider.ask` | `auto` | via `**kwargs` | `json` | `DEFAULT_MODEL="sonnet"`, `DEFAULT_TIMEOUT=300`; returns `.text` only |
| `cached_run(runner, store, …)` | runner's | runner's | runner's | own `model="sonnet"` default, always passed; see side flows |

Shared defaults: `model="sonnet"`, `effort="medium"`, `timeout=300`, `tools=""`, `setting_sources="user"`, every gateway option `None`.
There is no console script: the package is a library only.

Module: `runner`, `cache`

## Steps

### 1. Direct or gateway

Decides: whether OmniRoute is used at all.
In: `direct` kwarg.
Branches:
- kwargs are validated first, by the step 6 checks, before any route is tried;
- `direct=True` (`run_claude` only) → skip steps 2–5, go to step 6 with no gateway prefix; kwarg `backend="api"` or an unknown value → `ValueError` (env `CLAUDE_CLI_BACKEND` is ignored here);
- otherwise → step 2.
Fails: bad kwargs → `ValueError` (see step 6), nothing is sent.
Module: `runner`

### 2. CLI-only check

Decides: whether the call needs something only the CLI carries.
In: `model`, `tools`, `allowed_tools`, `slash_commands`, `cwd`, remote opts, `output_format`. Out: `cli_only` bool.
Branches:
- `model` starting with `claude-` → CLI: only the CLI path gets the gateway's glob mappings (`claude-opus-*` → `hard` …), HTTP would send the id verbatim;
- any of `tools`, `allowed_tools`, `slash_commands`, `remote`/`base_url`/`context`/`provider` set → CLI;
- `output_format == "stream-json"` → CLI (`stream_claude` passes `"json"` here, so it does not count);
- `cwd` containing `CLAUDE.md` or `.claude` → CLI; an empty or neutral `cwd` does not count.
Not counted: `effort`, `tier`, `profile`, `setting_sources`, `permission_mode` (HTTP drops them silently).
Fails: n/a (filesystem `exists` only).
Module: `runner`

### 3. Backend resolution

Decides: try HTTP now, or go to the CLI.
In: `backend` kwarg, env `CLAUDE_CLI_BACKEND`, `cli_only`, cooldown, key.
Branches:
- env `CLAUDE_CLI_BACKEND`, when non-empty, replaces the kwarg, unless the kwarg is `cli`;
- value outside `auto`/`cli`/`api` (`CLI`, `http` …) → `ValueError` naming the kwarg or the env var;
- `api` + `cli_only` → `ValueError`: `api` never turns into a CLI call;
- `cli` or `cli_only` → CLI;
- `api` → step 4, errors propagate;
- `auto` → step 4 only if the process-wide cooldown has passed and `api_key()` finds a key.
No key: `OMNIROUTE_API_KEY` env when non-empty, else the first `OMNIROUTE_API_KEY=…` line (optional `export `, quotes stripped) in `~/.omniroute/.env`; empty value or unreadable file → no key → CLI.
Fails: `ValueError` as above, before any request.
Module: `http_api`

### 4. HTTP call

Decides: the answer, over the gateway's OpenAI-format API.
In: prompt, `model`, `timeout`. Out: `ClaudeResult(text, cost_usd=0.0, duration_ms, raw)`.
Branches:
- URL `OMNIROUTE_URL` (default `http://localhost:20128`, trailing `/` dropped) + `/v1/chat/completions`: https anywhere, http only on loopback, `user:pass@` refused;
- `model` in `COMBOS` → `free-stack`/`hard`; any other value sent as-is;
- body is `{"model", "messages"}` only: no `temperature` (it would switch on the gateway's semantic cache), no `effort`.
Redirects are not followed (the bearer key would be resent). Body capped at 8 MiB; the deadline is checked only between body reads, so connect, headers and one stalled read each get their own `timeout`.
`raw` keeps the OpenAI body with `usage` renamed to `input_tokens`/`output_tokens`. `stdout=""`, `tier` echoed. Success logs `claude ok via http: model=… combo=… Nms` (INFO).
Fails: transport (`OSError` or `http.client.HTTPException`, e.g. `BadStatusLine`, `IncompleteRead`), status, JSON or choices-shape error, or empty/whitespace content → `HttpLlmError` (a `ClaudeCliError`). A non-dict `usage` is read as empty.
Module: `http_api`

### 5. HTTP failure

Decides: whether an HTTP error ends the call.
Branches:
- `auto` + `HttpLlmError` with status 400, 413 or 422 (this request rejected) → warning log, continue at step 6, no cooldown;
- `auto` + any other `HttpLlmError` (no answer, 401, 404, 429, 5xx …) → warning log, `_down_until = now + 600 s` for the whole process (all models), continue at step 6;
- any other exception (not an `HttpLlmError`) → reaches the caller, no fallback, no cooldown;
- `api` + an HTTP status → `HTTP <code>` plus the error body (≤64 KiB, kept in `.stdout`) run through the step 10 classifier: 429 → `UsageLimitError(session_wide=False)`, `ALL_TARGETS_SKIPPED` → gateway error with hint, else `HttpLlmError("HTTP <code> from <url>: <body ≤300>")`;
- `api` + a transport error, no key (`no OMNIROUTE_API_KEY`) or a refused `OMNIROUTE_URL` → `HttpLlmError`, unclassified.
Module: `http_api`

### 6. Argv

Decides: the exact `claude` command line.
In: all CLI kwargs. Out: `list[str]`.
Branches:
- `tier` not in `ultra/high/middle/low` → `ValueError`; `direct` with any gateway option → `ValueError`; `allowed_tools` without `tools` → `ValueError`;
- any string kwarg starting with `-` → `ValueError` (it would parse as a flag);
- gateway: `omniroute run [--remote …] [--base-url …] [--context …] [--provider …] [--profile <profile or tier>] claude -- -p`;
- direct: `claude -p`;
- then `--model`, `--effort` (omitted when `None`), `--output-format`; stream-json adds `--include-partial-messages --verbose`;
- always `--no-session-persistence --tools <tools>`; non-empty `tools` adds `--allowed-tools <allowed_tools or tools>`;
- `--disable-slash-commands` unless `slash_commands`; `--permission-mode` when set;
- always `--setting-sources <setting_sources> --strict-mcp-config --mcp-config <empty file>`.
`output_format` other than `json`/`stream-json` → `ValueError` (also in step 2, before any HTTP attempt) [21].
`--bare` is never added. `tier`/`profile` only pick `CLAUDE_CONFIG_DIR` inside the gateway, not the combo.
Fails: `ValueError`. The entry points run these checks at step 1, so a bad kwarg fails on every route.
Module: `runner`, `preflight`

### 7. Sandbox

Decides: what the child can see.
Branches:
- a `TemporaryDirectory(prefix="claude-cli-")` is always created and holds `mcp-empty.json` = `{"mcpServers":{}}`, deleted after the call;
- `cwd` given → the child runs there; else in the temp dir;
- child env = `CLAUDE_CODE_MAX_RETRIES=2` overlaid by `os.environ` (caller's value wins);
- direct → `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` removed.
Fails: n/a.
Module: `runner`

### 8. Spawn and wait

Decides: when the run is over.
In: argv, prompt on stdin. Out: returncode, stdout, stderr.
Branches:
- the CLI gets what is left of `timeout` after steps 3–5 (one budget per call); nothing left → `ClaudeCliError("claude timed out after Ns")`, no spawn;
- `run_claude`: `Popen(start_new_session=True)` + `communicate(left)`;
- `arun_claude`: `create_subprocess_exec` + `wait_for(left)`;
- `stream_claude`: stdout read line by line, each JSON dict line → `on_event`; stderr drained on a thread; a `threading.Timer` kills at `left` even when the child prints nothing.
Fails: missing `omniroute`/`claude` binary or a nonexistent `cwd` (any `OSError` from the spawn) → `ClaudeCliError("cannot start <argv0>: …")`. Timeout → SIGKILL to the process group (the real `claude` under `omniroute run` dies too) → `ClaudeCliError("claude timed out after Ns")`, no stdout kept. Ctrl-C, cancellation or a raising `on_event` → kill, then re-raise.
Module: `runner`

### 9. Parse output

Decides: the envelope dict the next steps read.
Branches:
- `json` → `json.loads(stdout)`; on failure `raw_decode` from the first `{` that parses (gateway banner before, even with a stray `{`; noise after);
- `stream-json` → every `{"type":"result"}` line merged key by key: `result` plus `usage`, `total_cost_usd`, `duration_ms`, `duration_api_ms`, `num_turns`, `session_id`, `model`, `modelUsage`, `is_error`.
Fails: unparseable → `{}` here; on a zero exit step 11 raises `claude returned non-JSON: …` (json), `unexpected claude envelope: <type>` (json, not a dict) or `claude produced no result` (stream-json).
Module: `parsing`

### 10. Classify failure

Decides: which error a failed run raises. Runs only when exit ≠ 0 or `is_error` is true.
In: stdout+stderr with ANSI codes and three OmniRoute banner shapes stripped.
Branches, first match wins:
- "Not logged in" anywhere → `NotLoggedInError`;
- gateway call only: gateway exhausted (`ALL_TARGETS_SKIPPED`, `no provider matched`, `fallback exhausted`, `allowedModels`, `PROXY_FAST_FAIL`, `circuit breaker` …) → `ClaudeCliError("omniroute gateway error: …")` + hint;
- usage limit (`usage limit`, `session limit`, `rate limit`, `quota exceeded`, `too many requests`, `429`) → `UsageLimitError`, `session_wide = not via_gateway`; message is the matching line;
- exit ≠ 0, nothing matched → `ClaudeCliError("claude exited N: <detail>")`.
Gateway before limit on purpose: "combo 'hard': fallback exhausted (429)" matches both.
Detail: stderr tail 500 chars, else the result text, else the last non-JSON stdout line; when that is `(no output)` and raw stdout+stderr is non-empty: `(stderr held only omniroute warnings)` if stripping omniroute banners leaves nothing, else `(only JSON events, no error text)`.
Hint: any wider gateway marker (`502`, `503`, `omniroute`, `ECONNREFUSED`, `unrecognized_model` …) appends the cheapest `omniroute` diagnosis commands and doc links; gateway calls only, a direct run never gets it.
`arun_claude` and `stream_claude` leave `via_gateway` at its default `True`.
Fails: every error carries `.stdout`.
Module: `runner`, `parsing`, `errors`

### 11. Success checks

Decides: whether a zero-exit run actually answered.
Branches:
- `is_error` true → `ClaudeCliError("claude error: <last 300 chars>")`;
- `result` missing, empty or not a string → `ClaudeCliError("claude produced no result")`;
- else `ClaudeResult`; `duration_ms` from the envelope, else wall clock since step 1 (HTTP attempt included).
Success logs `claude ok: Nms, cost=…` (INFO) on every CLI route.
Module: `runner`

### Output

`ClaudeResult`: `text`, `cost_usd` (envelope `total_cost_usd`; `0.0` on HTTP), `duration_ms`, `raw` (envelope or OpenAI body), `stdout` (raw CLI output; `""` on HTTP), `tier` (echo of the kwarg, on both routes).
Errors: `NotLoggedInError`, `UsageLimitError(session_wide)`, `HttpLlmError`, `ClaudeCliError`; all are `RuntimeError`. Outside that family: `ValueError` for bad kwargs.
The route is not a field: `stdout == ""` and the `via http` log line tell HTTP apart.
`HttpLlmError` is not re-exported from `claude_cli`; import it from `claude_cli.http_api`.
Module: `runner`, `errors`

## Side flows

### Response cache (`cached_run`)

Key: sha256 over model, whitespace-collapsed prompt, and `schema`/`version`/`purpose`/`tools` when set.
`tier`, `provider`, `profile`, `remote`, `base_url`, `context`, `direct`, `effort`, `cwd` (resolved path), `allowed_tools`, `setting_sources`, `slash_commands`, `permission_mode` fold into the model part when off their default (`None`, `False`, `"medium"`, `"user"`), so old rows keep their keys; `effort=None` folds. `backend`, `output_format`, `timeout` do not.
Hit → stored text, runner not called. `refresh=True` skips the read. Only `.text` is returned.
Writes: one row per miss; `prompt` column NULL unless `store_prompt=True`. A read or write error (`sqlite3.Error`, `OSError`) is logged, not raised (a read error counts as a miss); `FileCacheStore()` errors propagate.
`FileCacheStore`: sqlite table `claude_cli_cache`; a new leaf dir gets 0700 (new parents get the umask default), a new file 0600 (umask applies; existing ones keep their mode); `max_age_days` makes old rows read as misses, nothing is deleted; the db is switched to WAL on open, connections wait up to 30 s on a lock.
Module: `cache`

### Batches (`pack_batches`, `run_chunks`)

`pack_batches`: greedy, order kept; a batch closes before the item that would pass `budget` or `max_items` (`<= 0` disables); an oversized item rides alone.
`run_chunks`: `fn(chunk) -> (data, cost)`; `workers > 1` → thread pool, results in completion order. Any `Exception` → `"<label>: <Type>: <120 chars>"` in `failed`, batch continues. `NotLoggedInError` or `UsageLimitError(session_wide=True)` stops spending: chunks not yet started → `"<label>: skipped after <Type>"` (parallel: pending futures cancelled, running ones finish); results so far are kept, nothing is raised.
Module: `batch`

### Health check (`ping`)

`run_claude` with `backend="cli"`, which the env cannot override; default `model="haiku"` routes to `free-stack`, so a pass proves the gateway and `claude` binary, not the Claude subscription (documented in the docstring; `model="opus"` checks it) [24]; returns `(True, "pong via omniroute, Ns")` or `(False, <cleaned error ≤200 chars>)`.
Module: `runner`

### Preflight (`health`, `doctor`, `quota_status`, `simulate`, `dry_run_plan`)

Each runs one quota-free `omniroute <subcommand>` with a timeout and returns `(ok, detail)`, never raises; detail is the cleaned output tail, 500 chars (`"ok"` / `"exit N"` when empty; compact JSON for `dry_run_plan`).
`simulate` puts `--` before the prompt. `dry_run_plan` returns `{"command","args","env_keys"}` with env key names only.
`TIERS`, `MATRIX`, `GUIDES`, `BENCHMARKS`, `CONSTRAINTS`, `QUOTA_FREE_SUBCOMMANDS` are data; only `TIERS` keys are read by code (step 6). Nothing enforces `QUOTA_FREE_SUBCOMMANDS` at runtime.
Module: `preflight`

### JSON from prose (`extract_json`, `extract_json_or_none`)

Strips a fence, tries a fenced `{…}` block, then `raw_decode` from the first `{` that parses: a brace that fails (`{name}` in prose) is skipped and the search resumes after the failure point, so a truncated object raises instead of yielding its inner object; in an array the first object wins. No `{` → `json.loads` of the whole text (`"[1,2]"` parses). `_or_none` returns `None` on `ValueError`/`TypeError`.
Module: `parsing`

## Known gaps

Details and fixes are in the plan (number in brackets).
- Open findings: none. (#19–24 closed 2026-11-05.)
New ones go to the plan with the next free number.
