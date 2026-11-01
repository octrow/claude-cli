"""Batch helpers: pack items into prompt-sized chunks, and run a call per chunk."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from .errors import NotLoggedInError, UsageLimitError

log = logging.getLogger(__name__)


def pack_batches(
    items: Iterable[Any],
    budget: int,
    *,
    size_of: Callable[[Any], int],
    max_items: int = 0,
) -> list[list[Any]]:
    """Greedily pack ``items`` into batches within a size budget, order preserved.

    A batch is closed when adding the next item would push it over ``budget``
    (chars, tokens — whatever ``size_of`` counts) or over ``max_items``. Both
    caps are disabled when ``<= 0``. A batch always holds at least one item, so
    an item larger than ``budget`` on its own rides alone.
    """
    batches: list[list[Any]] = []
    cur: list[Any] = []
    cur_size = 0
    for item in items:
        size = size_of(item)
        over_count = max_items > 0 and len(cur) + 1 > max_items
        over_budget = budget > 0 and cur_size + size > budget
        if cur and (over_count or over_budget):
            batches.append(cur)
            cur, cur_size = [], 0
        cur.append(item)
        cur_size += size
    if cur:
        batches.append(cur)
    return batches


def _session_ending(exc: Exception) -> bool:
    """An error every later chunk would hit too: the login is gone, or the only route is spent."""
    return isinstance(exc, NotLoggedInError) or (
        isinstance(exc, UsageLimitError) and exc.session_wide
    )


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
    batch. Except a session-ending error (:class:`NotLoggedInError`, or a
    :class:`UsageLimitError` with ``session_wide``): every later call would fail the
    same way, so chunks not yet started are recorded as ``"<label>: skipped after
    <Error>"`` instead of being run. Results collected so far are kept either way.
    """
    chunks = list(chunks)
    results, spent, failed = [], 0.0, []
    stop: str | None = None  # the session-ending error's type name, once one was seen

    def _record(produce: Callable[[], tuple[Any, float]], chunk) -> None:
        nonlocal spent, stop
        try:
            data, cost = produce()
        except Exception as exc:  # noqa: BLE001 — keep the batch alive
            failed.append(f"{label(chunk)}: {type(exc).__name__}: {str(exc)[:120]}")
            if _session_ending(exc) and stop is None:
                stop = type(exc).__name__
                log.warning("%s ends the session: remaining chunks are skipped", stop)
            return
        spent += cost or 0.0
        results.append(data)

    if workers <= 1:
        iterator = progress(chunks) if progress else chunks
        for chunk in iterator:
            if stop:
                failed.append(f"{label(chunk)}: skipped after {stop}")
                continue
            _record(lambda c=chunk: fn(c), chunk)
        return results, spent, failed

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, c): c for c in chunks}
        stream = as_completed(futs)
        if progress:
            stream = progress(stream, total=len(futs))
        for fut in stream:
            if fut.cancelled():
                failed.append(f"{label(futs[fut])}: skipped after {stop}")
                continue
            _record(fut.result, futs[fut])
            if stop:
                for pending in futs:
                    pending.cancel()  # no-op for running and finished ones
    return results, spent, failed
