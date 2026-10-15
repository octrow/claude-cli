# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
