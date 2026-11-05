# Agents: start here

1. `README.md`: what the library does and its public API.
2. `docs/logic-map.md`: how a prompt becomes a `ClaudeResult` or a typed error: every step, every branch, the module behind it.
3. `docs/OMNIROUTE.md`: the gateway side (combos, HTTP route, diagnosis order).
4. `openspec/specs/`: normative behavior; `docs/plans/`: work in progress, `2026-11-01-logic-map-fixes.md` lists the known logic gaps.
5. Открытые баги, планы и идеи: [PLAN.md](PLAN.md).

Rules:
- Change a step or a branch → update `docs/logic-map.md` and `docs/logic-map.ru.md` in the same commit.
- `tests/test_logic_map.py` fails when a `Module:` line in either map points to a module that is gone.
- Tests: `uv run --extra dev pytest -q`. Runtime dependencies stay empty.
