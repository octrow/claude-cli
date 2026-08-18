## Purpose

Define what happens between spawning a `claude -p` subprocess and handing the
caller a result: the three execution modes (blocking, streaming, async), the
timeout and cleanup guarantees, the shape of a successful result, and — most
importantly — the fixed order in which a finished run is classified as a usage
limit, a login failure, or a generic error.

## Requirements

### Requirement: A successful run returns a normalized result

A completed run MUST return a frozen `ClaudeResult` carrying `text` (the answer),
`cost_usd`, `duration_ms`, `raw` (the folded envelope dict) and `stdout` (the
raw CLI output). `duration_ms` falls back to the measured wall-clock elapsed time
when the envelope reports none. `cost_usd` MAY be `None`; a subscription run does
not always report one.

#### Scenario: The json envelope format yields a result

- **WHEN** a run with `output_format="json"` exits zero with a valid envelope
- **THEN** a `ClaudeResult` is returned whose `text` is the envelope's `result`
  and whose `cost_usd` and `duration_ms` come from the envelope
- **Anchor**: `tests/test_runner.py::test_envelope_result`

#### Scenario: The stream-json format yields the same shape

- **WHEN** a run with `output_format="stream-json"` exits zero and its stream
  ends with a `{"type": "result"}` event
- **THEN** a `ClaudeResult` equivalent to the json-envelope case is returned
- **Anchor**: `tests/test_runner.py::test_stream_json_result`

### Requirement: Raw CLI output survives both success and failure

Successes and failures alike MUST retain the raw CLI output: `ClaudeResult.stdout`
on success, `ClaudeCliError.stdout` on failure. Consumers that archive runs for
inspection would otherwise lose the partial stream events that the folded `raw`
envelope drops.

#### Scenario: Result and error both carry stdout

- **WHEN** a run succeeds, and separately when a run fails
- **THEN** the returned `ClaudeResult` and the raised `ClaudeCliError` each expose
  the run's raw stdout
- **Anchor**: `tests/test_runner.py::test_result_and_error_carry_raw_stdout`

### Requirement: Every error derives from ClaudeCliError

`NotLoggedInError` and `UsageLimitError` MUST subclass `ClaudeCliError`, which
MUST subclass `RuntimeError`. Consumers that `except RuntimeError` to degrade
gracefully keep working unchanged when this library replaces their local wrapper.

#### Scenario: A login failure is an actionable typed error

- **WHEN** a failed run's combined output contains `Not logged in`
- **THEN** `NotLoggedInError` is raised, with a message telling the caller to run
  `claude` interactively and complete `/login`
- **Anchor**: `tests/test_runner.py::test_not_logged_in`

### Requirement: Limit and login classification is gated on run failure

The usage-limit and login checks MUST apply only when the run actually failed —
a non-zero exit code or an `is_error` envelope. A successful answer may
legitimately discuss rate limits or logging in, and must never be reclassified as
an error because of its own content.

#### Scenario: An answer discussing rate limits is not a usage limit

- **WHEN** a run exits zero and its answer text mentions rate limits
- **THEN** the `ClaudeResult` is returned normally and no `UsageLimitError` is
  raised
- **Anchor**: `tests/test_runner.py::test_answer_mentioning_rate_limits_is_not_a_usage_limit`

### Requirement: Usage limits fail fast and are matched narrowly

A failed run whose output matches a usage, session, rate-limit, quota, too-many-
requests or `429` signature MUST raise `UsageLimitError` before the exit code is
reported, so a caller aborts a batch instead of burning the rest of it on calls
that cannot succeed. The match MUST stay narrow: `context limit exceeded` is a
per-prompt problem, not an account limit, and MUST NOT be classified as one.

#### Scenario: A limit reported only in the stream result event is caught

- **WHEN** a failed stream-json run reports the limit message in its `result`
  event rather than on stderr
- **THEN** `UsageLimitError` is raised carrying that message as the detail
- **Anchor**: `tests/test_runner.py::test_usage_limit_from_stdout_result_event`

#### Scenario: Every limit signature is recognized

- **WHEN** a failed run's output contains any of the usage, session, rate-limit,
  quota, too-many-requests or `429` signatures
- **THEN** `UsageLimitError` is raised
- **Anchor**: `tests/test_runner.py::test_usage_limit_signatures`

#### Scenario: A context-limit failure is an ordinary error

- **WHEN** a failed run reports `context limit exceeded`
- **THEN** `ClaudeCliError` is raised, not `UsageLimitError`
- **Anchor**: `tests/test_runner.py::test_context_limit_is_not_a_usage_limit`

### Requirement: Unclassified failures raise a detailed generic error

A failure that matches no specific signature MUST raise `ClaudeCliError` with a
best-effort human-readable reason: stderr when non-empty, otherwise the result
event text, otherwise the last non-JSON stdout line. This covers a non-zero exit,
an `is_error` envelope, unparseable output, and a run that produced no result at
all.

#### Scenario: A non-zero exit reports the exit code and a reason

- **WHEN** the subprocess exits non-zero with no recognized signature
- **THEN** `ClaudeCliError` is raised naming the exit code and the reason
- **Anchor**: `tests/test_runner.py::test_nonzero_exit`

#### Scenario: An is_error envelope on a zero exit is still an error

- **WHEN** the subprocess exits zero but the envelope sets `is_error`
- **THEN** `ClaudeCliError` is raised
- **Anchor**: `tests/test_runner.py::test_is_error_envelope`

#### Scenario: Unparseable output is an error, not a crash

- **WHEN** a json-format run exits zero with stdout that is not valid JSON
- **THEN** `ClaudeCliError` is raised rather than a `JSONDecodeError` escaping
- **Anchor**: `tests/test_runner.py::test_non_json_stdout`

#### Scenario: A stream with no result event is an error

- **WHEN** a stream-json run exits zero but emits no `{"type": "result"}` event
- **THEN** `ClaudeCliError` is raised reporting that no result was produced
- **Anchor**: `tests/test_runner.py::test_stream_without_result_event`

### Requirement: Every mode enforces a timeout and leaves no process behind

`run_claude`, `stream_claude` and `arun_claude` MUST each enforce their `timeout`
and raise `ClaudeCliError` naming the elapsed limit. The streaming and async
paths MUST kill and reap the subprocess on timeout, on a raising event callback,
and on `KeyboardInterrupt`, so no orphaned `claude` process survives the call.

#### Scenario: A timeout raises a ClaudeCliError naming the limit

- **WHEN** the subprocess does not complete within `timeout` seconds
- **THEN** `ClaudeCliError` is raised reporting the timeout in seconds
- **Anchor**: `tests/test_runner.py::test_timeout`

### Requirement: Streaming reports events while the run is in flight

`stream_claude` MUST invoke its `on_event` callback once per parsed stream-json
event as that event arrives, not after the run completes, so a long agentic run
reports progress instead of appearing wedged. The output format is forced to
`stream-json` — it is the only shape the CLI emits incrementally. Non-JSON and
non-dict lines are skipped rather than raising.

#### Scenario: Callbacks fire per event during the run

- **WHEN** `stream_claude` is called with an `on_event` callback and the CLI emits
  several events before the final result
- **THEN** the callback receives each event as a dict while the process is still
  running, and the call still returns the normalized `ClaudeResult`
- **Anchor**: `tests/test_runner.py::test_stream_claude_reports_events_as_they_arrive`
