## Purpose

Defines the policy and stdlib-only helpers through which claude-cli uses OmniRoute's broader v3.8.51 surface safely, so every gateway capability is either adopted, wrapped as opt-in, or rejected with a recorded reason.

## ADDED Requirements

### Requirement: Utilization matrix is explicit and complete

The capability SHALL document every in-scope OmniRoute surface area as USED, OPT-IN, or AVOIDED with a reason: launch (`run claude`), routing (combos, `simulate`, `models`/`combo`/`test`), preflight (`health`, `doctor`, `run --dry-run --json`, `logs`), quota/cost (`quota`, `cost`, `usage`), server lifecycle (`serve`/`stop`/`restart`, `status`), resilience/proxy, sessions, compression/RTK and context-eng, cache, translator, skills/memory, provider nodes/sync/telemetry, FREE_TIERS and AUTO-COMBO. AVOIDED entries MUST name the blocking constraint (zero runtime dependencies, subscription/OAuth auth, isolation-by-default, or no-live-CLI testability).

#### Scenario: Audit covers the v3.8.51 guide list

- **WHEN** the matrix is reviewed against the proposal's v3.8.51 guide list (USER_GUIDE, SETUP_GUIDE, CLI-TOOLS, REMOTE-MODE, CLAUDE-CODE-CONFIGURATION, ENVIRONMENT, ARCHITECTURE, COMPRESSION_GUIDE, RTK_COMPRESSION, AUTO-COMBO, PROXY_GUIDE, FREE_TIERS, CODEBASE_DOCUMENTATION, FEATURES, API_REFERENCE, openapi.yaml)
- **THEN** every guide maps to at least one USED, OPT-IN, or AVOIDED entry with a reason
- **Anchor**: `tests/test_preflight.py::test_utilization_matrix_covers_guide_list`

#### Scenario: Avoided entries name their constraint

- **WHEN** an entry is marked AVOIDED
- **THEN** its reason names one of zero-dependencies, subscription-auth, isolation-default, or no-live-CLI-testability
- **Anchor**: `tests/test_preflight.py::test_avoided_entries_name_constraint`

### Requirement: Preflight helpers spend no quota and never raise unexpectedly

Preflight helpers (`health`, `doctor`, `dry-run`, quota/simulation status) SHALL invoke only OmniRoute commands documented as quota-free, SHALL run them with captured output and a bounded timeout, and SHALL return a `(bool, str)` pair rather than raising on gateway failure, so doctor-style startup checks can report instead of crash. The detail string SHALL be cleaned of ANSI/banner noise and truncated.

#### Scenario: Preflight failure returns false with cleaned detail

- **WHEN** a preflight helper's subprocess fails or times out
- **THEN** it returns `(False, <cleaned detail>)` and raises nothing
- **Anchor**: `tests/test_preflight.py::test_preflight_failure_returns_false`

#### Scenario: Preflight never invokes quota-spending commands

- **WHEN** any preflight helper runs
- **THEN** its argv contains only quota-free subcommands and never a real `run claude` execution
- **Anchor**: `tests/test_preflight.py::test_preflight_spends_no_quota`

### Requirement: Preflight execution is fakable without a live gateway

Every preflight helper SHALL go through the same faked seams as the runner (`subprocess.run`, `shutil.which`), so the full matrix is verifiable with no `omniroute` binary installed and no network access.

#### Scenario: Preflight suite passes with no binary on PATH

- **WHEN** the test suite runs with the subprocess and `which` seams faked
- **THEN** all preflight scenarios pass without invoking a real gateway
- **Anchor**: `tests/test_preflight.py::test_preflight_needs_no_live_gateway`

### Requirement: Four combo tiers drain free tokens first

The matrix SHALL define exactly four serving tiers — ULTRA (frontier-class), HIGH (opus-class), MIDDLE (sonnet-class), LOW (bulk/cheap-class) — each with free-first ordering (free providers before paid subscription) grounded in FREE_TIERS and AUTO-COMBO, and each with benchmark-driven membership (OpenRouter session-cost rankings, BenchLM arena Elo, LiveBench cost-per-task, Artificial Analysis leaderboards). Paid subscription SHALL be the last resort inside every tier, never the first pick.

#### Scenario: Every tier orders free before paid

- **WHEN** any tier's combo definition is inspected
- **THEN** its free targets precede its paid subscription targets and its membership cites at least one of the four benchmark sources
- **Anchor**: `tests/test_preflight.py::test_tiers_order_free_before_paid`
