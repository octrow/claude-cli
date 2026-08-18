## Purpose

Turn what the CLI actually prints into what callers actually want: fold either of
the two output formats into one envelope shape, and pull structured JSON out of a
model reply that may be fenced, prefixed with prose, or malformed.

## Requirements

### Requirement: Both output formats fold into one envelope shape

`parse_envelope` MUST parse the single `--output-format json` object, and
`parse_stream` MUST fold the final `{"type": "result"}` event of a stream into a
dict with the same `result` plus metadata keys. Downstream code MUST then have a
single path regardless of which format was requested. `parse_stream` MUST skip
non-JSON lines and non-result events — the stream also carries partial-message
events and, on some failures, plain-text noise.

#### Scenario: The last result event wins

- **WHEN** a stream contains partial-message events, noise lines, and one or more
  `{"type": "result"}` events
- **THEN** `parse_stream` returns the final result event's `result` text together
  with its usage, cost, duration, turn, session and model metadata
- **Anchor**: `tests/test_parsing.py::test_parse_stream_picks_final_result_event`

#### Scenario: A stream with no result event yields an empty envelope

- **WHEN** `parse_stream` is given a stream containing no result event
- **THEN** it returns an empty dict rather than raising
- **Anchor**: `tests/test_parsing.py::test_parse_stream_no_result_event`

#### Scenario: Non-JSON envelope output raises a descriptive ValueError

- **WHEN** `parse_envelope` is given output that is not valid JSON
- **THEN** it raises `ValueError` quoting the tail of the offending output
- **Anchor**: `tests/test_parsing.py::test_parse_envelope_non_json`

#### Scenario: A JSON non-object envelope raises a descriptive ValueError

- **WHEN** `parse_envelope` is given valid JSON that is not an object
- **THEN** it raises `ValueError` naming the type it got instead
- **Anchor**: `tests/test_parsing.py::test_parse_envelope_not_an_object`

### Requirement: Markdown fences are stripped before parsing

`strip_fences` MUST remove a leading and trailing markdown code fence, including
an optional language tag, and MUST handle the single-line form (`` ```json {…}```
``) that has no newline to split on. Text without a fence MUST pass through
unchanged apart from surrounding whitespace.

#### Scenario: Unfenced text passes through

- **WHEN** `strip_fences` is given text with no code fence
- **THEN** it returns the text with only surrounding whitespace trimmed
- **Anchor**: `tests/test_parsing.py::test_strip_fences_plain_passthrough`

#### Scenario: A multi-line fence is removed

- **WHEN** `strip_fences` is given a multi-line fenced block with a language tag
- **THEN** the fence lines are removed and the inner content is returned
- **Anchor**: `tests/test_parsing.py::test_strip_fences`

#### Scenario: A single-line fence is removed

- **WHEN** `strip_fences` is given a one-line fenced reply
- **THEN** the fence markers and any language tag are removed
- **Anchor**: `tests/test_parsing.py::test_strip_fences_single_line`

### Requirement: JSON extraction takes the first balanced object

`extract_json` MUST return the first *balanced* `{...}` object in a reply, not the
span between the first `{` and the last `}`. It MUST track string context and
backslash escapes so that braces inside string literals never affect the depth
count. It tolerates surrounding prose and code fences, and on unparseable input
MUST raise exactly what `json.loads` would, so callers can wrap it in their own
contextual message.

#### Scenario: A fenced JSON object is extracted

- **WHEN** `extract_json` is given a fenced JSON object
- **THEN** the parsed object is returned
- **Anchor**: `tests/test_parsing.py::test_extract_json_fenced`

#### Scenario: Surrounding prose is ignored

- **WHEN** `extract_json` is given an object embedded in explanatory prose
- **THEN** the parsed object is returned
- **Anchor**: `tests/test_parsing.py::test_extract_json_surrounding_prose`

#### Scenario: Two top-level objects yield the first

- **WHEN** a reply contains two consecutive top-level objects
- **THEN** the first is returned, rather than a decode error from a
  first-brace-to-last-brace span
- **Anchor**: `tests/test_parsing.py::test_extract_json_first_balanced_object_only`

#### Scenario: Braces inside strings do not confuse the scan

- **WHEN** a reply's JSON contains `{` or `}` characters inside string values
- **THEN** the full object is still extracted correctly
- **Anchor**: `tests/test_parsing.py::test_extract_json_ignores_braces_in_strings`

#### Scenario: Escaped quotes do not confuse the scan

- **WHEN** a reply's JSON contains backslash-escaped quotes inside string values
- **THEN** the full object is still extracted correctly
- **Anchor**: `tests/test_parsing.py::test_extract_json_handles_escapes`

#### Scenario: Garbage raises the standard decode error

- **WHEN** `extract_json` is given text containing no parseable object
- **THEN** it raises the same error `json.loads` would raise
- **Anchor**: `tests/test_parsing.py::test_extract_json_raises_on_garbage`

### Requirement: A non-raising extraction variant exists for fallback paths

`extract_json_or_none` MUST return `None` instead of raising on unparseable
input. Every consumer with a graceful-degrade path otherwise re-implements this
same try/except wrapper.

#### Scenario: Garbage degrades to None

- **WHEN** `extract_json_or_none` is given unparseable text
- **THEN** it returns `None`
- **Anchor**: `tests/test_parsing.py::test_extract_json_or_none_degrades_on_garbage`
