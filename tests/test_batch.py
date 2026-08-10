from __future__ import annotations

import pytest

from claude_cli import run_chunks


def _double(chunk):
    return chunk * 2, 0.5


@pytest.mark.parametrize("workers", [1, 4])
def test_run_chunks_collects_results_and_cost(workers):
    results, cost, failed = run_chunks(
        [1, 2, 3], _double, label=str, workers=workers
    )
    assert sorted(results) == [2, 4, 6]
    assert cost == pytest.approx(1.5)
    assert failed == []


@pytest.mark.parametrize("workers", [1, 4])
def test_run_chunks_records_failures_without_aborting(workers):
    def flaky(chunk):
        if chunk == 2:
            raise ValueError("bad chunk")
        return chunk, 1.0

    results, cost, failed = run_chunks(
        [1, 2, 3], flaky, label=lambda c: f"chunk-{c}", workers=workers
    )
    assert sorted(results) == [1, 3]
    assert cost == pytest.approx(2.0)
    assert failed == ["chunk-2: ValueError: bad chunk"]


def test_run_chunks_progress_wrapper_is_used():
    seen = {}

    def progress(it, total=None):
        seen["total"] = total
        return it

    run_chunks([1, 2], _double, label=str, workers=2, progress=progress)
    assert seen["total"] == 2
