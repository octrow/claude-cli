## 1. Tests (red first)

- [ ] 1.1 Write failing test `tests/test_preflight.py::test_high_tier_includes_muse_spark_1_3` asserting `TIERS["high"]` examples contain `Muse Spark 1.3-class` with `free_first is True` and a benchmark citation, and verify it fails before implementation via `uv run --extra dev pytest tests/test_preflight.py::test_high_tier_includes_muse_spark_1_3 -q`
- [ ] 1.2 Confirm existing `tests/test_preflight.py::test_tiers_order_free_before_paid` still passes unmodified via `uv run --extra dev pytest tests/test_preflight.py::test_tiers_order_free_before_paid -q`

## 2. Implementation

- [ ] 2.1 Add `Muse Spark 1.3-class` to `TIERS["high"]["examples"]` in `src/claude_cli/preflight.py` (examples only, no mechanism change) and verify the new test plus the full preflight file pass via `uv run --extra dev pytest tests/test_preflight.py -q`
- [ ] 2.2 Update the HIGH row of the tier table in `docs/OMNIROUTE.md` to list `Muse Spark 1.3-class` (free via `opencode-zen`/`oc`) with a new review date, keeping `Muse Spark 1.2-class` in MIDDLE, and verify by grepping the table for both entries

## 3. Verification (quota-free only)

- [ ] 3.1 Record source-of-truth state with `opencode models | grep -i "muse-spark-1.3"` showing 1.3 + contributor-free, and `omniroute models --search "Muse Spark"` state (documents gateway lag if still 1.2-only), and verify no quota was spent (read-only commands only)
- [ ] 3.2 Run quota-free routing checks `omniroute simulate --combo free-first "hi"` and `dry_run_plan()` and verify they complete without upstream execution, then run the full suite via `uv run --extra dev pytest -q` and verify it passes
- [ ] 3.3 Update (operator-side, outside this repo) the HIGH combo definition so 1.3 free targets precede paid subscription, and verify via `omniroute combo list` plus a quota-free `simulate` naming the HIGH combo without spending quota
