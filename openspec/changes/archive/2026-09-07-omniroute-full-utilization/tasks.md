## 1. Audit matrix

- [x] 1.1 Fetch and record the v3.8.51 guide surface (USER_GUIDE, SETUP_GUIDE, CLI-TOOLS, REMOTE-MODE, CLAUDE-CODE-CONFIGURATION, ENVIRONMENT, ARCHITECTURE, COMPRESSION_GUIDE, RTK_COMPRESSION, AUTO-COMBO, PROXY_GUIDE, FREE_TIERS, CODEBASE_DOCUMENTATION, FEATURES, API_REFERENCE, openapi.yaml, README quick-start, UNINSTALL) plus local `omniroute -v` and `omniroute <subcommand> --help` outputs, and verify every guide maps to a USED/OPT-IN/AVOIDED row with a constraint reason
- [x] 1.2 Write failing test `tests/test_preflight.py::test_utilization_matrix_covers_guide_list` asserting the matrix covers the guide list, and verify it fails before implementation
- [x] 1.3 Write failing test `tests/test_preflight.py::test_avoided_entries_name_constraint` asserting AVOIDED rows name a blocking constraint, and verify it fails before implementation
- [x] 1.4 Define the four combo tiers (ULTRA/HIGH/MIDDLE/LOW) with free-first ordering per tier and benchmark-cited membership (OpenRouter session-cost, BenchLM arena Elo, LiveBench cost-per-task, Artificial Analysis), and verify `simulate` plus `--dry-run --json` resolve each tier without spending quota
- [x] 1.5 Write failing test `tests/test_preflight.py::test_tiers_order_free_before_paid` asserting every tier lists free targets before paid subscription with a benchmark citation, and verify it fails before implementation

## 2. Preflight helpers

- [x] 2.1 Write failing test `tests/test_preflight.py::test_preflight_failure_returns_false` for never-raises `(False, cleaned detail)` on failure/timeout, and verify it fails before implementation
- [x] 2.2 Write failing test `tests/test_preflight.py::test_preflight_spends_no_quota` asserting only quota-free subcommands are invoked, and verify it fails before implementation
- [x] 2.3 Write failing test `tests/test_preflight.py::test_preflight_needs_no_live_gateway` running the preflight suite on faked `subprocess.run`/`shutil.which` seams, and verify it fails before implementation
- [x] 2.4 Implement the stdlib-only preflight module (re-exported, bounded timeouts, ANSI/banner cleaning, truncation) and verify all six `tests/test_preflight.py` tests pass

## 3. Invocation routing

- [x] 3.1 Write failing test `tests/test_runner.py::test_build_args_routing_defaults_off` asserting defaults carry no routing flags and keep isolation flags, and verify it fails before implementation
- [x] 3.2 Write failing test `tests/test_runner.py::test_build_args_routing_never_passes_bare` asserting no routing combination emits `--bare`, and verify it fails before implementation
- [x] 3.3 Write failing test `tests/test_runner.py::test_dry_run_returns_plan_without_execution` asserting `--dry-run --json` returns command plus env-key names with no model run and no secret values, and verify it fails before implementation
- [x] 3.4 Write failing test `tests/test_runner.py::test_tier_leaves_claude_model_valid` asserting tier selection is expressed only in gateway-level flags before `--` while Claude-side `--model` stays `haiku`/`sonnet`/`opus`, and verify it fails before implementation
- [x] 3.5 Implement opt-in routing pass-throughs, tier selection, and the dry-run helper preserving never-`--bare`, isolation-by-default, and throwaway-cwd, and verify the four new `tests/test_runner.py` routing tests pass

## 4. Execution classification

- [x] 4.1 Write failing test `tests/test_runner.py::test_all_targets_skipped_is_gateway_not_limit` asserting `ALL_TARGETS_SKIPPED` raises `ClaudeCliError` (not `UsageLimitError`) with docs/issues pointers, and verify it fails before implementation
- [x] 4.2 Write failing test `tests/test_runner.py::test_gateway_strings_earn_docs_pointer` asserting each expanded v3.8.51 gateway string appends the diagnosis order and links, and verify it fails before implementation
- [x] 4.3 Write failing test `tests/test_runner.py::test_gateway_hint_orders_free_checks_first` asserting `health`/`doctor`/`--dry-run` precede `quota` in hint text, and verify it fails before implementation
- [x] 4.4 Write failing test `tests/test_runner.py::test_ping_shape_preserved` asserting `ping` keeps its never-raises `(bool, str)` shape on success and failure, and verify it fails before implementation
- [x] 4.5 Write failing test `tests/test_runner.py::test_tier_attribution_survives_success_and_failure` asserting successes expose the serving tier and gateway failures name the attempted tier/combo, and verify it fails before implementation
- [x] 4.6 Implement the expanded gateway set, cheapest-first hint order, tier attribution, and ping/preflight cleaning parity (keeping `context limit exceeded` out of the limit pattern), and verify the five new `tests/test_runner.py` execution tests pass

## 5. Docs and validation

- [x] 5.1 Refresh `docs/OMNIROUTE.md` and error-text pointers to the v3.8.51 diagnosis order and links including the versioned four-tier combo table with benchmark sources and review date, and verify every command named there exists in local `--help` output
- [x] 5.2 Run `uv run --extra dev pytest -q`, confirm the anchor↔test bijection holds (no orphan anchors, no unanchored tests), and verify `openspec validate --change "omniroute-full-utilization" --strict` passes
