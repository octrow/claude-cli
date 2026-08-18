## Purpose

Turn a long list of items into a small number of prompt-sized calls, run those
calls with optional parallelism, and make sure one bad chunk never destroys the
work the other chunks already did.

## Requirements

### Requirement: Items are packed greedily within both caps, preserving order

`pack_batches` MUST fill batches greedily in input order, closing the current
batch when adding the next item would exceed either the size `budget` (measured
by the caller's `size_of`) or `max_items`. Both caps MUST be treated as disabled
when non-positive. A batch MUST always hold at least one item, so an item larger
than `budget` on its own rides alone rather than being dropped or looping
forever.

#### Scenario: Both the size budget and the item count close a batch

- **WHEN** `pack_batches` is called with a size budget and a `max_items` cap over
  items that trip each in turn
- **THEN** the returned batches respect both caps, preserve input order, and
  contain every input item exactly once
- **Anchor**: `tests/test_batch.py::test_pack_batches_respects_budget_and_count`

### Requirement: Chunk results and cost are accumulated across the run

`run_chunks` MUST apply `fn(chunk) -> (data, cost)` over every chunk and return
`(results, total_cost, failed_labels)`. Costs MUST sum across chunks, treating a
`None` cost as zero so a subscription run that reports no cost does not break the
total.

#### Scenario: Results and costs accumulate

- **WHEN** `run_chunks` processes several chunks that each return data and a cost
- **THEN** it returns every chunk's data and the summed cost
- **Anchor**: `tests/test_batch.py::test_run_chunks_collects_results_and_cost`

### Requirement: A failing chunk is recorded, not fatal

An exception from any chunk MUST be caught, recorded in `failed_labels` as the
chunk's `label` plus the exception type and a truncated message, and skipped. One
bad chunk MUST NOT abort the batch or discard the results already collected — a
batch is expensive, and a single oversized or malformed chunk is expected.

#### Scenario: The batch survives a raising chunk

- **WHEN** one chunk's `fn` raises while the others succeed
- **THEN** the successful chunks' results and costs are still returned, and the
  failing chunk appears in `failed_labels` with its label and error
- **Anchor**: `tests/test_batch.py::test_run_chunks_records_failures_without_aborting`

### Requirement: Parallelism is opt-in via a thread pool

`run_chunks` MUST run sequentially when `workers <= 1` and MUST use a thread pool
of `workers` threads otherwise. Threads are correct here because each call shells
out to a `claude` subprocess and the GIL is released for its duration. Failure
isolation and cost accumulation MUST behave identically in both modes.

#### Scenario: Failure handling is identical under parallelism

- **WHEN** chunks are run with more than one worker and one of them raises
- **THEN** the surviving results are returned and the failure is recorded, exactly
  as in the sequential path
- **Anchor**: `tests/test_batch.py::test_run_chunks_records_failures_without_aborting`

### Requirement: An optional progress wrapper wraps the iteration

`run_chunks` MUST accept an optional `progress` callable and wrap its iteration
with it — the chunk list in sequential mode, the completion stream with a `total`
in parallel mode — so a caller can pass a progress bar without this library
depending on one.

#### Scenario: The progress callable receives the iteration

- **WHEN** `run_chunks` is called with a `progress` wrapper
- **THEN** the wrapper is invoked around the iteration and the run's results are
  unaffected
- **Anchor**: `tests/test_batch.py::test_run_chunks_progress_wrapper_is_used`
