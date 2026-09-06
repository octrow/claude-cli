from __future__ import annotations

import json

import pytest

from claude_cli.parsing import clean_cli_text, error_detail, strip_ansi
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


@pytest.mark.parametrize("fence", ["```", "```json "])
def test_strip_fences_single_line(fence):
    assert strip_fences(f'{fence}{{"a": 1}}```') == '{"a": 1}'


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


def test_parse_envelope_skips_wrapper_banner_on_stdout():
    # `omniroute run claude` prints its banner on stdout, ahead of the envelope.
    stdout = "  📋 Loaded env from ~/.omniroute/.env\n" '{"result":"hi","is_error":false}'
    assert parse_envelope(stdout)["result"] == "hi"


def test_error_detail_keeps_the_tail_of_a_noisy_stderr():
    # The real failure is last; a leading wrapper banner must not crowd it out.
    stderr = "⚠ banner\n" * 200 + "Error: the actual cause"
    assert error_detail("", stderr, None).endswith("Error: the actual cause")


def test_strip_ansi_removes_color_codes():
    assert strip_ansi("\x1b[33m⚠ warning\x1b[0m") == "⚠ warning"


def test_strip_ansi_passthrough_plain_text():
    assert strip_ansi("plain text, no escapes") == "plain text, no escapes"


def test_clean_cli_text_drops_known_omniroute_noise():
    # Real tail from tlf logs/2026-08-30-10-04-50.log (lines 843-862).
    tail = "\n".join(
        [
            "\x1b[33m⚠ STORAGE_ENCRYPTION_KEY in /home/octrow/.nvm/.../omniroute/.env "
            "is ignored, /home/octrow/.omniroute/.env set it first\x1b[0m",
            "\x1b[33m⚠ REQUIRE_API_KEY in ... is ignored, ... set it first\x1b[0m",
            "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another "
            "auth source is set and takes precedence over your claude.ai login · "
            "Unset it to load your organization's ...",
            "📋 Loaded env from /home/octrow/.omniroute/.env",
            "Error: something actually broke",
        ]
    )
    cleaned = clean_cli_text(tail)
    assert cleaned == "Error: something actually broke"


def test_clean_cli_text_only_noise_yields_empty_string():
    tail = "📋 Loaded env from /home/octrow/.omniroute/.env\n⚠ FOO in x is ignored, y set it first"
    assert clean_cli_text(tail) == ""


def test_clean_cli_text_never_touches_unrelated_warnings():
    # Narrow patterns only — a real warning must survive.
    assert clean_cli_text("Warning: disk almost full") == "Warning: disk almost full"
