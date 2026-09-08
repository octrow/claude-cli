## Context

See `proposal.md` for motivation. Current state: `TIERS["high"]["examples"]` in `src/claude_cli/preflight.py` names only `GPT-5.6 Sol-class` and `Gemini 3.8 Flash-class`; `docs/OMNIROUTE.md` tier table matches. `opencode models` already lists `opencode/muse-spark-1.3` and `opencode/muse-spark-1.3-contributor-free`, while the local OmniRoute catalog (`omniroute models`) still lists only 1.2 — the gateway DB lags upstream. Tier selection mechanism (`tier=` → gateway flags before `--`, `--model` stays `haiku`/`sonnet`/`opus`) is settled and out of scope for change.

## Goals / Non-Goals

- Goals: add 1.3-class to HIGH membership with free-first ordering; update `TIERS` examples, tier-table docs, and the operator-side HIGH combo; extend tier test coverage.
- Non-Goals: changing how `tier=` maps to gateway flags; touching `--model` validation; re-benchmarking tier membership from scratch; adding runtime dependencies.

## Decisions

- **Membership as examples + operator config, not a model-id enum.** `TIERS` examples stay human-readable `-class` labels and the real routing lives in the OmniRoute HIGH combo definition (Dashboard Routing / `omniroute combo`), because provider model ids drift and the gateway catalog lags upstream. Alternative (hardcoding `opencode/muse-spark-1.3-contributor-free` as a Python constant): rejected — it would rot on every upstream rename and duplicate gateway routing.
- **Source of truth for availability is `opencode models`, not `omniroute models`.** Verified 2026-09-07: opencode lists 1.3 + 1.3-contributor-free; omniroute lists only 1.2 on `opencode-zen`/`oc`. Tasks verify against opencode first and re-check omniroute after its DB syncs, so a stale gateway DB cannot block the change.
- **No spec change to `cli-invocation` or `run-execution`.** Tier attribution (`ClaudeResult.tier`, gateway errors naming attempted tier/combo) already covers a new member with no mechanism change; only `omniroute-preflight` requirements change.

## Risks / Trade-offs

- [Risk] OmniRoute DB still lacks 1.3 → HIGH combo cannot resolve it yet → Mitigation: land docs + `TIERS` examples + tests now; flip the operator combo once `omniroute models --search "Muse Spark"` shows 1.3, verified by a no-quota `simulate` run.
- [Risk] `Gemini 3.8 Flash-class` in HIGH examples is likewise absent from the gateway catalog → Mitigation: leave it untouched; this change only adds 1.3-class, it does not re-audit other members.

## Migration Plan

1. Merge `TIERS` examples + docs + tests (no behavior change to routing mechanism).
2. Operator creates/updates the HIGH combo with 1.3 free targets first, paid subscription last.
3. Verify with quota-free commands only (`opencode models`, `omniroute models --search`, `omniroute simulate`, `--dry-run --json`); rollback is a combo-config + docs revert.

## Open Questions

None.
