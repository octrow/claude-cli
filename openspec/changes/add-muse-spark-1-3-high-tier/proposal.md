## Why

`opencode models` already serves `opencode/muse-spark-1.3` and `opencode/muse-spark-1.3-contributor-free`, and this session runs on the latter — but `docs/OMNIROUTE.md` and `claude_cli.preflight.TIERS` still name only `Muse Spark 1.2-class` in the MIDDLE (sonnet) tier. The HIGH (opus) tier has no Muse Spark member, so opus-level routing cannot drain the free 1.3 source first.

## What Changes

- Add `Muse Spark 1.3-class` (free via `opencode-zen` / `oc`, incl. `Contributor Free` variant) to the HIGH tier membership, keeping free-first ordering (free targets before paid subscription) inside the tier.
- Update `TIERS["high"]["examples"]` in `src/claude_cli/preflight.py` and the tier table in `docs/OMNIROUTE.md` with a new review date; create/update the HIGH combo on the OmniRoute side (Dashboard Routing / `omniroute combo`) with the same ordering.
- No change to the tier-selection mechanism: `tier="high"` still maps to gateway-level flags before `--`, `--model` stays `haiku`/`sonnet`/`opus`, and paid subscription remains last resort in every tier.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `omniroute-preflight`: the "Four combo tiers drain free tokens first" requirement gains Muse Spark 1.3-class membership in HIGH with free-first ordering and benchmark citation.

## Impact

- Code: `src/claude_cli/preflight.py` (`TIERS` examples only, no mechanism change); tests: `tests/test_preflight.py` (extend tier-membership coverage).
- Docs: `docs/OMNIROUTE.md` tier table + review date.
- Ops: HIGH combo definition in OmniRoute (operator config, outside this repo).
- No new runtime dependencies, no auth changes, no new error types.
