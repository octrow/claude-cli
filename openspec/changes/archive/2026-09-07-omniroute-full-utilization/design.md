## Context

See proposal.md Why. Current state: `runner.py` owns the single spawn path (`omniroute run claude -- -p …`), `parsing.py` strips ANSI plus three known OmniRoute banner shapes, `run-execution` classifies gateway failures with a deliberately narrow limit regex and a `_gateway_hint` pointing at `health` → `doctor` → `--dry-run --json` → `logs` → `quota`. Constraints shaping every decision: zero runtime dependencies, subscription/OAuth auth (never `--bare`, no `ANTHROPIC_API_KEY`), isolation-by-default with opt-in project mode, and a test suite that fakes `subprocess.run` / `create_subprocess_exec` / `shutil.which` and requires an anchor↔test bijection.

## Goals / Non-Goals

**Goals:**
- Produce a complete, reviewable use-vs-avoid map of the v3.8.51 surface before writing any new runtime code.
- Close only gaps that fit the four constraints above; everything else becomes a reasoned AVOIDED entry.
- Define four combo tiers (ULTRA/HIGH/MIDDLE/LOW) with free-first ordering in every tier and benchmark-driven membership, draining all free-token sources before paid subscription.
- Keep the change additive: no existing requirement text changes, no error-hierarchy changes, no default changes.

**Non-Goals:**
- Adopting OmniRoute server-side features that duplicate local ones (response cache, batching) or require credentials/network daemons (remote hosting, tunnels, cloud tasks).
- A Python SDK over OmniRoute's HTTP/OpenAPI surface; the CLI remains the only integration point.
- Live-gateway testing; every new scenario stays fakable.

## Decisions

### New `preflight` module beside `runner`, not code inside it

`runner.py`'s contract is "the one place a `claude -p` subprocess is spawned". Preflight commands (`health`, `doctor`, `--dry-run`, `quota`, `simulate`, `status`/`models`/`combo`/`test`) are a different spawn family with different output shapes and a never-raises `(bool, str)` convention. A small new stdlib-only module (re-exported from `__init__.py`) keeps the runner docstring truthful and gives the new `omniroute-preflight` capability a 1:1 home, matching the existing module↔capability layout.
Alternative considered: adding helpers to `runner.py` — rejected because it dilutes the single-spawn invariant reviewers rely on.

### Wrap the CLI, never the HTTP/OpenAPI server

All preflight and dry-run access goes through `omniroute <subcommand>` subprocesses with captured output and bounded timeouts, reusing the existing fake seams. An HTTP client against `/api/*` or `openapi.yaml` would need a new dependency (or hand-rolled `urllib` + TLS + auth handling), credential management, and live-server tests — violating three constraints at once for no behavior the CLI does not already expose.
Alternative considered: `urllib`-based status client — rejected; CLI wrapping is stdlib, credential-free, and already the project's seam.

### Additive deltas only; existing requirement blocks untouched

The `cli-invocation` and `run-execution` deltas use ADDED requirements exclusively. No MODIFIED/REMOVED blocks, so archive cannot silently drop existing guarantees and the anchor bijection can only grow. If the audit finds an existing requirement is factually wrong against v3.8.51 (e.g. a banner shape changed), that becomes a follow-up change with its own MODIFIED block, not scope creep here.

### Matrix lives in two places with one owner

The normative matrix lives in `specs/omniroute-preflight/spec.md` (USED/OPT-IN/AVOIDED + constraint per entry); `docs/OMNIROUTE.md` carries the human-readable version plus the refreshed diagnosis order and v3.8.51 links. The spec owns truth; the doc points at it. Error-text pointers (`OMNIROUTE_DOCS`/`OMNIROUTE_ISSUES`) are updated to the same pinned release, with the existing `omniroute -v` caveat retained because the upstream repo ships daily fixes.

### Tier-to-combo mapping lives in OmniRoute config, not in Python constants

The four tiers are OmniRoute combo definitions (dashboard Routing / `combo` management / model-combo mappings, verified via `simulate` and `--dry-run --json`), not a Python enum of model ids: Claude Code validates `--model` locally and rejects `combo/<name>` before the request leaves, so `runner.MODELS` stays `haiku`/`sonnet`/`opus` while tier selection is expressed in gateway-level flags before `--`. Each combo lists free targets first and paid subscription last (FREE_TIERS + AUTO-COMBO grounding); tier membership examples (ULTRA frontier-class, HIGH opus-class, MIDDLE sonnet-class, LOW bulk-cheap-class) are versioned in `docs/OMNIROUTE.md` against the four benchmark sources (OpenRouter session-cost, BenchLM arena Elo, LiveBench cost-per-task, Artificial Analysis) so re-tiering is a config+docs edit, not a code change.
Alternatives considered: passing `combo/<name>` as `--model` — rejected (local `unrecognized_model`, already documented); adding a fourth Claude `--model` value — rejected unless Claude Code itself accepts it; hardcoding provider lists in Python — rejected (drifts daily, duplicates gateway routing).

## Risks / Trade-offs

- [Upstream drift] OmniRoute ships daily; v3.8.51 strings may lag HEAD → Mitigation: pin links and signature set to v3.8.51, keep the `omniroute -v` note, record drift handling as an AVOIDED-with-reason (no auto-tracking).
- [Regex overreach] Expanding the gateway set risks false-positiving user content → Mitigation: classification stays failure-gated, `ALL_TARGETS_SKIPPED` stays out of the limit pattern, each new string gets a dedicated faked test.
- [Secret leakage via dry-run/remote] `--dry-run --json` and remote URLs could surface tokens → Mitigation: spec requires key-names-only surfacing; tests assert no secret values in returned plans.
- [Scope creep into server features] Cache/compression/sessions look tempting → Mitigation: AVOIDED entries with constraint names; adopting any later needs its own change.
- [Hint staleness] Hardcoded command order rots → Mitigation: order is spec-pinned and tested, so drift fails loudly in review rather than silently.
- [Free-pool exhaustion looks like an outage] Draining free tokens first means quota errors arrive in bursts per tier → Mitigation: tier attribution in errors/results plus `quota`/`usage`/`cost` in the hint chain, so callers see which tier is spent and fall back instead of retrying.
- [Benchmark churn] Leaderboards move weekly; tier membership rots → Mitigation: membership is config+docs data with cited sources and review date, never a code constant; re-tiering needs no release.

## Migration Plan

No migration: additive helpers, unchanged defaults, unchanged error hierarchy. Rollback is archiving the change without applying follow-ups. `docs/OMNIROUTE.md` refresh ships in the same change so docs and behavior cannot diverge.

## Open Questions

- Exact final membership of the expanded gateway string set (confirm each against a local `omniroute -v` ≥ 3.8.51 during implementation; additions stay within the ADDED requirement's wording, so no spec change needed).
- Whether `simulate`'s human output needs the same banner-stripping as `run` output (implementation detail; covered by the shared cleaning requirement either way).
