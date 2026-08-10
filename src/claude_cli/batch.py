"""Batch helper: apply a per-chunk LLM call over many chunks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger(__name__)


def run_chunks(
    chunks: Iterable[Any],
    fn: Callable[[Any], tuple[Any, float]],
    *,
    label: Callable[[Any], str],
    workers: int = 1,
    progress: Callable[..., Iterable] | None = None,
) -> tuple[list, float, list[str]]:
    """Apply ``fn(chunk) -> (data, cost)`` over ``chunks``, optionally in parallel.

    Each call shells out to a ``claude`` subprocess, so threads give real
    concurrency (the GIL is released during ``subprocess.run``).

    Returns ``(results, total_cost, failed_labels)``. A chunk that raises is
    recorded in ``failed_labels`` and skipped — one bad chunk never aborts the
    batch.
    """
    chunks = list(chunks)
    results, spent, failed = [], 0.0, []

    def _record(produce: Callable[[], tuple[Any, float]], chunk) -> None:
        nonlocal spent
        try:
            data, cost = produce()
        except Exception as exc:  # noqa: BLE001 — keep the batch alive
            failed.append(f"{label(chunk)}: {type(exc).__name__}: {str(exc)[:120]}")
            return
        spent += cost or 0.0
        results.append(data)

    if workers <= 1:
        iterator = progress(chunks) if progress else chunks
        for chunk in iterator:
            _record(lambda c=chunk: fn(c), chunk)
        return results, spent, failed

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, c): c for c in chunks}
        stream = as_completed(futs)
        if progress:
            stream = progress(stream, total=len(futs))
        for fut in stream:
            _record(fut.result, futs[fut])
    return results, spent, failed
