## MODIFIED Requirements

### Requirement: Four combo tiers drain free tokens first

The matrix SHALL define exactly four serving tiers — ULTRA (frontier-class), HIGH (opus-class), MIDDLE (sonnet-class), LOW (bulk/cheap-class) — each with free-first ordering (free providers before paid subscription) grounded in FREE_TIERS and AUTO-COMBO, and each with benchmark-driven membership (OpenRouter session-cost rankings, BenchLM arena Elo, LiveBench cost-per-task, Artificial Analysis leaderboards). The HIGH tier SHALL list `Muse Spark 1.3-class` (free via `opencode-zen` / `oc`, including the `Contributor Free` variant) among its opus-class members, ordered before paid subscription targets. Paid subscription SHALL be the last resort inside every tier, never the first pick.

#### Scenario: Every tier orders free before paid

- **WHEN** any tier's combo definition is inspected
- **THEN** its free targets precede its paid subscription targets and its membership cites at least one of the four benchmark sources
- **Anchor**: `tests/test_preflight.py::test_tiers_order_free_before_paid`

#### Scenario: HIGH tier serves Muse Spark 1.3 before paid subscription

- **WHEN** the HIGH tier membership is inspected
- **THEN** it contains `Muse Spark 1.3-class` sourced from `opencode-zen` / `oc` free targets ordered before paid subscription, with a benchmark citation
- **Anchor**: `tests/test_preflight.py::test_high_tier_includes_muse_spark_1_3`
