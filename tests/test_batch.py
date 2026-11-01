from __future__ import annotations

import threading

import pytest

from claude_cli import NotLoggedInError, UsageLimitError, pack_batches, run_chunks


def test_pack_batches_respects_budget_and_count():
    items = ["x" * 10, "y" * 10, "z" * 90, "w", "v", "u"]
    assert pack_batches(items, 50, size_of=len) == [items[:2], items[2:3], items[3:]]
    # an item over budget rides alone; max_items caps an otherwise-fitting batch
    assert [len(b) for b in pack_batches(items, 50, size_of=len, max_items=2)] == [2, 1, 2, 1]


def _double(chunk):
    return chunk * 2, 0.5


@pytest.mark.parametrize("workers", [1, 4])
def test_run_chunks_collects_results_and_cost(workers):
    results, cost, failed = run_chunks([1, 2, 3], _double, label=str, workers=workers)
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


SESSION_ENDING = [NotLoggedInError("log in"), UsageLimitError("limit", session_wide=True)]


@pytest.mark.parametrize("exc", SESSION_ENDING, ids=lambda e: type(e).__name__)
def test_run_chunks_stops_spending_after_a_session_ending_error(exc):
    calls = []

    def fn(chunk):
        calls.append(chunk)
        if chunk == 2:
            raise exc
        return chunk, 1.0

    results, cost, failed = run_chunks([1, 2, 3, 4], fn, label=lambda c: f"chunk-{c}")
    name = type(exc).__name__
    assert results == [1] and cost == pytest.approx(1.0) and calls == [1, 2]
    assert failed == [
        f"chunk-2: {name}: {exc}",
        f"chunk-3: skipped after {name}",
        f"chunk-4: skipped after {name}",
    ]


def test_run_chunks_parallel_cancels_what_has_not_started():
    release = threading.Event()
    calls = []

    def fn(chunk):
        calls.append(chunk)
        if chunk == 1:
            raise NotLoggedInError("log in")
        release.wait(5)
        return chunk, 1.0

    def progress(stream, total=None):
        for fut in stream:
            yield fut
            release.set()  # the fatal result has been handled: let the running chunks end

    results, _, failed = run_chunks(
        range(1, 7), fn, label=lambda c: f"chunk-{c}", workers=2, progress=progress
    )
    skipped = [f for f in failed if "skipped after NotLoggedInError" in f]
    assert len(calls) <= 3 and len(skipped) == 6 - len(calls)
    assert len(results) == len(calls) - 1


def test_run_chunks_keeps_going_after_a_gateway_limit():
    def fn(chunk):
        if chunk == 1:
            raise UsageLimitError("one upstream", session_wide=False)
        return chunk, 1.0

    results, _, failed = run_chunks([1, 2, 3], fn, label=str)
    assert results == [2, 3] and len(failed) == 1
