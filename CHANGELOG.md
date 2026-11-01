# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `ping` always goes through the CLI: `CLAUDE_CLI_BACKEND` no longer overrides an explicit `backend="cli"`, so `ping` can't report "pong" over HTTP without starting `claude`.
- Bad kwargs (unknown `tier`, a value starting with `-`) raise `ValueError` before the HTTP route too, not only when the call falls back to the CLI.
- A missing `omniroute`/`claude` binary or `cwd` raises `ClaudeCliError` instead of a bare `FileNotFoundError`.
- `timeout` is one budget per call: after a failed HTTP attempt the CLI gets only what is left, so `timeout=300` no longer stretches to ~900 s.
- `cached_run` splits the key by `direct=True` and a non-default `effort`, so a direct Claude answer and a gateway `free-stack` answer no longer share a row. Existing keys are unchanged.
- `backend="api"`: an HTTP error keeps its body and goes through the CLI's classifier, so a 429 raises `UsageLimitError` and `ALL_TARGETS_SKIPPED` a gateway error.

## [0.1.6] - 2026-11-01

### Security

- `build_args` refuses a value that starts with `-` (model, tools, profile and the rest), since the CLI would read it as another flag.
- `preflight.simulate` puts `--` before the prompt, so a prompt starting with `-` stays a prompt.
- `~/.omniroute/.env`: an `export OMNIROUTE_API_KEY=...` line is read, and an unreadable file turns the HTTP route off instead of raising.

## [0.1.5] - 2026-11-01

### Security

- The HTTP route sends the OmniRoute key only over https, or plain http to a loopback host; a URL with embedded credentials is refused. Redirects are no longer followed, so the key never reaches a second host.
- HTTP responses are capped at 8 MiB; a body still arriving after the call's timeout is dropped.
- `FileCacheStore` creates a new db file `0600` in a `0700` folder, since cached responses are plaintext.

### Changed

- Package description matches the GitHub About text; README gains a "How it differs" section.

## [0.1.4] - 2026-11-01

### Added

- `direct=True` on `run_claude` and `build_args`: `claude -p` without OmniRoute, on the local Claude Code login (gateway and API-key variables are dropped from its environment). A usage limit there is session-wide.

### Changed

- `extract_json` finds the first object with `json.JSONDecoder.raw_decode`; results are the same, but a `JSONDecodeError` now reports positions in the whole reply rather than in the cut-out object.
- Internal cleanup with no behaviour change: ruff format and lint, simpler `_finish`, `build_args` and `dry_run_plan`; CI now runs ruff and ty.

## [0.1.3] - 2026-10-15

### Changed

- README images get phone-width variants, so text stays readable on a 390px screen.
- Python version classifiers in package metadata (the PyPI "python" badge showed "missing").

## [0.1.2] - 2026-10-15

### Changed

- README redesign: light/dark SVG hero and diagram, badges; images use absolute URLs so they render on PyPI.

## [0.1.1] - 2026-10-14

### Changed

- PyPI distribution renamed to `claude-subprocess` (import name stays `claude_cli`).

## [0.1.0] - 2026-10-14

### Added

- `run_claude` / `arun_claude` / `stream_claude`: wrapper around `claude -p` with subscription auth, sync and async execution, streaming.
- OmniRoute preflight (`dry_run_plan`) and an HTTP route for tool-less calls.
- JSON extraction from model output and an on-disk response cache.
- Process-tree kill on timeout or cancel; Claude Code retries capped by default; `allowed_tools` override.
- MIT license.
