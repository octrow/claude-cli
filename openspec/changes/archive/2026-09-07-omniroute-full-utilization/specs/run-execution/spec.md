## ADDED Requirements

### Requirement: Gateway classification tracks v3.8.51 failure strings

Failed runs SHALL be classified against the v3.8.51 gateway signature set (including `ALL_TARGETS_SKIPPED`, proxy `socket hang up` / keep-alive guidance, `unsupported_country_region_territory`, `ECONNREFUSED`, `claude-code:unrecognized_model`, and 502/503 gateway markers) with `ALL_TARGETS_SKIPPED` deliberately excluded from the usage-limit pattern, since it signals a fixable configuration error rather than an exhaustible quota. The narrow usage-limit rule (`context limit exceeded` is NOT a usage limit) SHALL remain unchanged.

#### Scenario: Config-error 503 is a gateway error, not a usage limit

- **WHEN** a failed run reports `ALL_TARGETS_SKIPPED`
- **THEN** `ClaudeCliError` is raised (not `UsageLimitError`) with OmniRoute docs/issues pointers in the message
- **Anchor**: `tests/test_runner.py::test_all_targets_skipped_is_gateway_not_limit`

#### Scenario: Expanded gateway strings earn the docs pointer

- **WHEN** a failed run reports any v3.8.51 gateway string in the expanded set
- **THEN** the error message appends the cheapest-first diagnosis order and the OmniRoute docs/issues links
- **Anchor**: `tests/test_runner.py::test_gateway_strings_earn_docs_pointer`

### Requirement: Diagnosis order is cheapest-first and quota-free-first

The gateway hint SHALL order checks cheapest-first (`health` → `doctor` → `run --dry-run --json` → `logs` → `quota`, then `simulate` / `status` / `models` / `combo` / `test` as needed), naming only commands verified against v3.8.51, so a 3am reader spends no quota before the free checks are exhausted.

#### Scenario: Hint lists free checks before quota checks

- **WHEN** a gateway failure message is produced
- **THEN** `health`, `doctor`, and `--dry-run` appear before `quota` in the hint text
- **Anchor**: `tests/test_runner.py::test_gateway_hint_orders_free_checks_first`

### Requirement: Ping and preflight share one gateway path

`ping` SHALL remain a never-raises `(bool, str)` startup check over the real `run claude` path, and any new quota-free preflight helper SHALL share its output-cleaning and truncation behavior, so success still reads `pong via omniroute` and failures read as cleaned ≤200-char details.

#### Scenario: Ping success and failure shapes are unchanged

- **WHEN** `ping` succeeds, and separately when its call raises or times out
- **THEN** success returns `(True, <pong detail>)` and failure returns `(False, <cleaned ≤200-char detail>)` with nothing raised
- **Anchor**: `tests/test_runner.py::test_ping_shape_preserved`

### Requirement: Results and failures attribute the serving tier

Successful results SHALL expose which tier served them wherever the envelope reports it, and gateway failures SHALL name the tier/combo that was attempted, so free-vs-paid spend is diagnosable without new dependencies.

#### Scenario: Tier attribution survives success and failure

- **WHEN** a run succeeds with tier metadata in the envelope, and separately when a tiered run fails at the gateway
- **THEN** the success exposes the serving tier and the failure message names the attempted tier/combo
- **Anchor**: `tests/test_runner.py::test_tier_attribution_survives_success_and_failure`
