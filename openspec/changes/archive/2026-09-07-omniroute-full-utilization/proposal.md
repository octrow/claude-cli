## Why

`claude-cli` routes every call through `omniroute run claude`, but it was built against OmniRoute ~3.8.50 using only one launch path plus a handful of diagnostic commands (`health`, `doctor`, `--dry-run`, `logs`, `quota`, `simulate`). OmniRoute v3.8.51 exposes a much larger documented surface (routing/combos, remote mode, compression/RTK, cache, cost/usage, resilience, sessions, translator, CLI tooling, environment) across ~18 guides. Without a systematic audit against that surface we risk under-utilizing reliability levers, duplicating features OmniRoute already owns, and letting `docs/OMNIROUTE.md` go stale.

The concrete utilization goal driving this change: drain ALL free-token sources first, and serve paid quality through four explicit combo tiers — ULTRA (frontier-class, e.g. fable-5.1-class / GPT-6 Astra-class), HIGH (opus-class, e.g. GPT-5.6 Sol / Gemini 3.8 Flash-class), MIDDLE (sonnet-class, e.g. GPT-5.6 Terra / Muse Spark 1.2 / Grok 4.6 / Claude Opus 4.7-class), LOW (bulk/cheap, e.g. DeepSeek V4 Flash / inclusionai-ling-3.0-flash-class). Tier membership is benchmark-driven (OpenRouter session-cost rankings, BenchLM arena Elo, LiveBench cost-per-task, Artificial Analysis leaderboards) and free-first ordering applies inside every tier.

## What Changes

- Audit the OmniRoute v3.8.51 documented surface (USER_GUIDE, SETUP_GUIDE, CLI-TOOLS, REMOTE-MODE, CLAUDE-CODE-CONFIGURATION, ENVIRONMENT, ARCHITECTURE, COMPRESSION_GUIDE, RTK_COMPRESSION, AUTO-COMBO, PROXY_GUIDE, FREE_TIERS, CODEBASE_DOCUMENTATION, FEATURES, API_REFERENCE, openapi.yaml, README quick-start, UNINSTALL) against what `claude-cli` actually uses.
- Produce an explicit use-vs-avoid matrix: every OmniRoute capability is either adopted, wrapped as an opt-in helper, or rejected with a recorded reason (zero-dep constraint, subscription-auth invariant, isolation default, testability without live CLI).
- Add stdlib-only, opt-in gap closures where the audit finds safe wins: richer gateway failure classification/hints, preflight helpers over no-quota commands, safe routing pass-throughs that cannot break subscription auth or isolation defaults.
- Define the four combo tiers (ULTRA / HIGH / MIDDLE / LOW) as OmniRoute-side combo config with free-first ordering inside every tier, benchmark-driven membership per tier, and paid subscription as last resort rather than first; Claude Code `--model` stays `haiku`/`sonnet`/`opus` (it rejects `combo/<name>` locally) while tier selection rides on OmniRoute-level routing flags before `--`.
- Refresh `docs/OMNIROUTE.md` and error-text pointers so diagnosis order matches v3.8.51.
- No breaking changes: error hierarchy (`ClaudeCliError(RuntimeError)`) preserved, isolation defaults unchanged, zero runtime dependencies unchanged.

## Capabilities

### New Capabilities

- `omniroute-preflight`: stdlib-only preflight/diagnostics policy and helpers over OmniRoute's no-quota commands (`health`, `doctor`, `run --dry-run --json`, `logs`, `quota`, `simulate`, `status`/`models`/`combo`/`test`), plus the use-vs-avoid matrix for the full v3.8.51 surface (compression, remote mode, cache, cost/usage, resilience, sessions, translator, context-eng, skills/memory). Owns the four-tier combo policy (ULTRA/HIGH/MIDDLE/LOW membership sources, free-first ordering per tier, FREE_TIERS + AUTO-COMBO grounding). Defines what claude-cli intentionally uses, what it intentionally avoids, and why.

### Modified Capabilities

- `cli-invocation`: safe routing pass-throughs found justified by the audit (explicit opt-in remote/context/provider/combo selection, plus four-tier selection mapped to OmniRoute-level flags before `--`, never to `combo/<name>` as the Claude `--model`) that preserve the never-`--bare`, isolation-by-default, and throwaway-cwd invariants.
- `run-execution`: gateway failure classification, `_gateway_hint` diagnosis order, and `ping`/preflight integration updated to v3.8.51 strings and commands; cost/usage propagation where OmniRoute reports it without new dependencies; errors and results attribute the serving tier/combo so free-vs-paid spend is diagnosable.

## Impact

- Affected code: `src/claude_cli/runner.py` (argv builder, gateway regex/hint, `ping`, preflight helpers), possibly `src/claude_cli/parsing.py` (new envelope/noise shapes), `src/claude_cli/errors.py` only if a new `ClaudeCliError` subclass is justified (stays inside the hierarchy).
- Docs: `docs/OMNIROUTE.md`, README usage notes for any new opt-in helper, plus the versioned four-tier combo table (tier → member-class examples → benchmark source) so tier membership review does not require re-reading four leaderboard sites.
- Tests: new faked-subprocess scenarios per requirement (no live CLI calls); anchor bijection preserved.
- Constraints upheld: `dependencies = []`, subscription/OAuth auth, isolation defaults, `except RuntimeError` compatibility.
