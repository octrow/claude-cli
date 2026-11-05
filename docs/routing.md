# Routing and errors

Details moved out of the README. The step-by-step flow is in [logic-map.md](https://github.com/octrow/claude-cli/blob/main/docs/logic-map.md); gateway notes are in [OMNIROUTE.md](https://github.com/octrow/claude-cli/blob/main/docs/OMNIROUTE.md#http-route-2026-10).

## HTTP route

Tool-less `run_claude` / `arun_claude` calls are POSTed to OmniRoute's `/v1/chat/completions`.
The key comes from `OMNIROUTE_API_KEY` or `~/.omniroute/.env`, the URL from `OMNIROUTE_URL` (default `http://localhost:20128`).
Without a key the HTTP route is skipped.

If HTTP fails, the wrapper logs a warning, falls back to the CLI and stops trying HTTP for 600 s in that process.
A 400, 413 or 422 only rejects that one request, so it falls back without the pause.

The key is only sent over https, or over plain http to a loopback host.
Redirects are not followed, and a URL with `user:password@` in it is refused.

## What stays on the CLI

- a `claude-*` model id (only the CLI gets the gateway's glob mappings)
- calls with `tools` / `allowed_tools`
- a project `cwd` (one holding `CLAUDE.md` or `.claude/`)
- `slash_commands`, remote overrides, `output_format="stream-json"`, and `ping`

A tool-less `stream_claude` also goes over HTTP and then emits no events.

## Switches and notes

- `backend="cli"` (or `CLAUDE_CLI_BACKEND=cli`, which wins over the kwarg) turns HTTP off. An explicit `backend="cli"` is never flipped back by the env.
- `backend="api"` uses HTTP only. A call that needs the CLI raises `ValueError` there, and any other value raises `ValueError` too.
- HTTP results have `cost_usd=0.0`, and `effort` is ignored there.
- No `temperature` is sent, because it would turn on OmniRoute's semantic cache.
- Test suites that fake the CLI must set `CLAUDE_CLI_BACKEND=cli`, or they reach the live gateway.

## Errors

All errors derive from `ClaudeCliError(RuntimeError)`, so existing `except RuntimeError` fallbacks keep working.

- `"Not logged in"` in stdout or stderr raises `NotLoggedInError`.
- Usage, rate and session-limit messages raise `UsageLimitError` before the exit code is checked, so a batch stops at the first one instead of burning through the rest. The match is narrow on purpose: `context limit exceeded` is not a usage limit.
- Before classification, stdout and stderr are stripped of ANSI codes and OmniRoute's own banner and warning lines, so the real error at the tail isn't buried. `ClaudeCliError.message` carries the cleaned text.

When the failure looks like the gateway's, the message says so and lists the OmniRoute checks that spend no quota.
A 503 there is ambiguous: a spent quota and `ALL_TARGETS_SKIPPED` (no provider matched) share the code, and only the first is worth waiting out.
[OMNIROUTE.md](https://github.com/octrow/claude-cli/blob/main/docs/OMNIROUTE.md) has the diagnosis order and the error strings.
