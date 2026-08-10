from __future__ import annotations

import json

import pytest

from claude_cli import (
    extract_json,
    extract_json_or_none,
    parse_envelope,
    parse_stream,
    strip_fences,
)


def test_strip_fences_plain_passthrough():
    assert strip_fences('  {"a": 1}  ') == '{"a": 1}'


@pytest.mark.parametrize("fence", ["```", "```json"])
def test_strip_fences(fence):
    assert strip_fences(f'{fence}\n{{"a": 1}}\n```') == '{"a": 1}'


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_extract_json_surrounding_prose():
    assert extract_json('Sure! Here it is: {"a": 1} — hope that helps.') == {"a": 1}


def test_extract_json_first_balanced_object_only():
    assert extract_json('{"a": {"b": 2}}\n{"c": 3}') == {"a": {"b": 2}}


def test_extract_json_ignores_braces_in_strings():
    assert extract_json('{"a": "}{ not a brace"}') == {"a": "}{ not a brace"}


def test_extract_json_handles_escapes():
    assert extract_json(r'{"a": "quote \" and \\"}') == {"a": 'quote " and \\'}


def test_extract_json_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        extract_json("no json here")


def test_extract_json_or_none_degrades_on_garbage():
    assert extract_json_or_none("no json here") is None
    assert extract_json_or_none('ok: {"a": 1}') == {"a": 1}


def test_parse_envelope_non_json():
    with pytest.raises(ValueError, match="non-JSON"):
        parse_envelope("oops")


def test_parse_envelope_not_an_object():
    with pytest.raises(ValueError):
        parse_envelope("[1, 2]")


def test_parse_stream_picks_final_result_event():
    stream = "\n".join(
        [
            '{"type":"system","subtype":"init","session_id":"s1"}',
            "",
            "plain text noise",
            '{"type":"assistant","message":{}}',
            '{"type":"result","result":"first","total_cost_usd":0.1}',
            '{"type":"result","result":"final","total_cost_usd":0.2,"duration_ms":7}',
        ]
    )
    env = parse_stream(stream)
    assert env["result"] == "final"
    assert env["total_cost_usd"] == 0.2
    assert env["duration_ms"] == 7


def test_parse_stream_no_result_event():
    assert parse_stream('{"type":"system"}') == {}
