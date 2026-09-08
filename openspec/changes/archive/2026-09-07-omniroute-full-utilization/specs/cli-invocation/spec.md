## ADDED Requirements

### Requirement: Routing pass-throughs are opt-in and never weaken invariants

`build_args` SHALL accept only explicitly passed routing options (remote base URL / context, provider/combo selection where the audit justifies them) and SHALL default every one of them to off, preserving the throwaway-cwd, isolation-flags, and never-`--bare` guarantees under all combinations. No routing option SHALL accept a value that injects a bare `--bare` flag or drops an isolation flag unless the caller also explicitly opts into project mode.

#### Scenario: Defaults carry no routing options

- **WHEN** `build_args` is called with defaults
- **THEN** the argv contains no `--remote`/`--base-url`/`--context`/`--provider` routing flags and still contains every isolation flag
- **Anchor**: `tests/test_runner.py::test_build_args_routing_defaults_off`

#### Scenario: No routing combination produces --bare

- **WHEN** `build_args` is called with any combination of the new routing options
- **THEN** `--bare` is absent from the returned argv
- **Anchor**: `tests/test_runner.py::test_build_args_routing_never_passes_bare`

### Requirement: Dry-run planning is exposed without execution

The library SHALL expose a dry-run path that surfaces OmniRoute's planned command (`run claude --dry-run --json`) without executing the model call, so callers can inspect routing (command plus env-key names, never secret values) before spending quota.

#### Scenario: Dry-run returns the plan without running the model

- **WHEN** the dry-run helper is called
- **THEN** it returns the planned command and env-key names with no model execution and no quota spent
- **Anchor**: `tests/test_runner.py::test_dry_run_returns_plan_without_execution`

### Requirement: Tier selection rides on gateway flags, never on the Claude model id

Tier selection (`ultra` / `high` / `middle` / `low`) SHALL map onto OmniRoute-level routing flags placed before `--` (provider/combo/profile selection where the audit justifies each), SHALL default to off (current model/effort behavior unchanged when no tier is passed), and SHALL never place `combo/<name>` into the Claude-side `--model` value, since Claude Code rejects an unrecognized model id locally before the request reaches the gateway.

#### Scenario: Tier leaves the Claude model id valid

- **WHEN** `build_args` is called with any tier selected
- **THEN** the Claude-side `--model` remains one of `haiku`/`sonnet`/`opus` and the tier is expressed only in gateway-level flags
- **Anchor**: `tests/test_runner.py::test_tier_leaves_claude_model_valid`
