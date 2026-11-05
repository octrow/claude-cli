"""HTTP route tests — urlopen is faked, no gateway and no CLI is ever reached."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
from http.client import BadStatusLine, HTTPMessage, IncompleteRead

import pytest

from claude_cli import arun_claude, cached_run, http_api, ping, run_claude, stream_claude
from claude_cli import runner as runner_mod
from claude_cli.cache import DictCacheStore
from claude_cli.http_api import HttpLlmError, omniroute_chat

REPLY = {"choices": [{"message": {"content": "hi"}}]}


@pytest.fixture
def auto(monkeypatch):
    monkeypatch.delenv("CLAUDE_CLI_BACKEND")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "k")


@pytest.fixture
def http(monkeypatch):
    """Fake urlopen: records requests; `.fail` makes it raise."""
    calls = []

    class H:
        fail = False
        calls: list
        body: bytes

    def urlopen(req, timeout):
        calls.append((req, json.loads(req.data), timeout))
        if H.fail:
            raise urllib.error.URLError("down")
        return io.BytesIO(H.body)

    H.calls = calls
    H.body = json.dumps(REPLY).encode()
    monkeypatch.setattr(http_api._OPENER, "open", urlopen)
    return H


@pytest.fixture
def cli(monkeypatch):
    """Fake CLI path: counts the argv builds that reach the subprocess layer."""
    seen = []

    def fake(*_args, **kw):
        seen.append(kw)
        raise RuntimeError("cli reached")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake)
    return seen


def test_alias_mapping(auto, http):
    for model, combo in [
        ("haiku", "free-stack"),
        ("sonnet", "free-stack"),
        ("opus", "hard"),
        ("sub-first", "sub-first"),
    ]:
        omniroute_chat("p", model=model, timeout=5)
        assert http.calls[-1][1]["model"] == combo


def test_request_shape_no_temperature_system_message(auto, http):
    res = omniroute_chat("p", model="haiku", timeout=7, system_prompt="be brief", effort="low")
    req, body, timeout = http.calls[0]
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "p"},
    ]
    assert "temperature" not in body
    assert req.get_header("Authorization") == "Bearer k" and timeout == 7
    assert res.text == "hi" and res.cost_usd == 0.0


def test_key_from_env_then_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nOMNIROUTE_API_KEY='file-key'\n")
    monkeypatch.setattr(http_api, "ENV_FILE", env)
    assert http_api.api_key() == "file-key"
    monkeypatch.setenv("OMNIROUTE_API_KEY", "env-key")
    assert http_api.api_key() == "env-key"


def test_no_key_skips_http(monkeypatch, http, cli):
    monkeypatch.delenv("CLAUDE_CLI_BACKEND")
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p")
    assert not http.calls


def test_run_claude_uses_http(auto, http, cli):
    assert run_claude("p", model="opus").text == "hi"
    assert not cli


def test_failure_falls_back_to_cli_and_cools_down(auto, http, cli):
    http.fail = True
    for _ in range(2):
        with pytest.raises(RuntimeError, match="cli reached"):
            run_claude("p")
    assert len(http.calls) == 1 and len(cli) == 2  # second call skipped HTTP


def test_http_error_is_cli_error():
    assert issubclass(HttpLlmError, runner_mod.ClaudeCliError)


@pytest.mark.parametrize(
    "kw",
    [
        {"tools": "Read"},
        {"tools": "Read", "allowed_tools": "Read(//f)"},
        {"output_format": "stream-json"},
        {"slash_commands": True},
        {"backend": "cli"},
    ],
)
def test_cli_only_calls_skip_http(auto, http, cli, kw):
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p", **kw)
    assert not http.calls


def test_env_forces_cli(auto, monkeypatch, http, cli):
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "cli")
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p")
    assert not http.calls


def test_backend_api_propagates_errors(auto, http, cli):
    http.fail = True
    with pytest.raises(HttpLlmError):
        run_claude("p", backend="api")
    assert not cli


def test_async_path(auto, http, monkeypatch):
    assert asyncio.run(arun_claude("p")).text == "hi"

    async def no_spawn(*_args, **_kw):
        raise RuntimeError("cli reached")

    monkeypatch.setattr(runner_mod.asyncio, "create_subprocess_exec", no_spawn)
    http.fail = True
    with pytest.raises(RuntimeError, match="cli reached"):  # HTTP failed -> CLI fallback
        asyncio.run(arun_claude("p"))


def test_cached_run_unchanged(auto, http):
    store = DictCacheStore()
    assert cached_run(run_claude, store, "p", model="haiku") == "hi"
    assert cached_run(run_claude, store, "p", model="haiku") == "hi"
    assert len(http.calls) == 1


def test_empty_cwd_still_uses_http(auto, http, cli, tmp_path):
    # a neutral cwd (jobs draft_stages) carries no project context: nothing to lose over HTTP
    assert run_claude("p", cwd=tmp_path).text == "hi"
    assert not cli


@pytest.mark.parametrize("marker", ["CLAUDE.md", ".claude"])
def test_project_cwd_stays_on_cli(auto, http, cli, tmp_path, marker):
    (tmp_path / marker).mkdir() if marker == ".claude" else (tmp_path / marker).write_text("x")
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p", cwd=tmp_path)
    assert not http.calls


def test_stream_claude_uses_http_without_events(auto, http, cli, tmp_path):
    events = []
    res = stream_claude("p", events.append, model="haiku", cwd=tmp_path)
    assert res.text == "hi" and not events and not cli
    assert http.calls[-1][1]["model"] == "free-stack"


def test_stream_claude_with_tools_stays_on_cli(auto, http, cli):
    with pytest.raises(RuntimeError, match="cli reached"):
        stream_claude("p", lambda _e: None, tools="WebSearch")
    assert not http.calls


@pytest.mark.parametrize(
    "url", ["http://example.com:20128", "http://10.0.0.5:20128", "ftp://localhost"]
)
def test_remote_plain_http_never_gets_the_key(auto, http, monkeypatch, url):
    monkeypatch.setenv("OMNIROUTE_URL", url)
    with pytest.raises(HttpLlmError, match="https"):
        omniroute_chat("p", timeout=5)
    assert http.calls == []


@pytest.mark.parametrize(
    "url", ["http://localhost:20128", "http://127.0.0.2:1", "http://[::1]:1", "https://gw.example"]
)
def test_loopback_http_and_any_https_are_allowed(auto, http, monkeypatch, url):
    monkeypatch.setenv("OMNIROUTE_URL", url)
    omniroute_chat("p", timeout=5)
    assert http.calls[0][0].full_url == url + "/v1/chat/completions"


def test_credentials_in_url_are_refused_and_not_echoed(auto, http, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_URL", "https://user:hunter2@gw.example")
    with pytest.raises(HttpLlmError) as exc:
        omniroute_chat("p", timeout=5)
    assert "hunter2" not in str(exc.value)
    assert http.calls == []


def test_oversized_response_is_rejected(auto, http, monkeypatch):
    monkeypatch.setattr(http_api, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(HttpLlmError, match="larger than"):
        omniroute_chat("p", timeout=5)


def test_redirect_is_not_followed_with_the_key(auto, monkeypatch):
    """A real local server answers 302 to /stolen; the key must never reach it."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/v1/chat/completions":
                self.send_response(302)
                self.send_header("Location", "/stolen")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(REPLY).encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("OMNIROUTE_URL", f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(HttpLlmError, match="302"):
            omniroute_chat("p", timeout=5)
    finally:
        server.shutdown()
    assert [path for path, _ in seen] == ["/v1/chat/completions"]


def test_dotenv_export_prefix_and_binary_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    env = tmp_path / ".env"
    monkeypatch.setattr(http_api, "ENV_FILE", env)
    env.write_text('export OMNIROUTE_API_KEY="k1"\n')
    assert http_api.api_key() == "k1"
    env.write_bytes(b"\xff\xfe\x00garbage")
    assert http_api.api_key() is None  # unreadable file: route off, never a crash


def test_explicit_cli_kwarg_beats_env(auto, monkeypatch, http, cli):
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "api")
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p", backend="cli")
    assert not http.calls


def test_ping_never_answers_over_http(auto, monkeypatch, http, cli):
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "api")
    ok, message = ping()
    assert not ok and "cli reached" in message  # it went for the CLI
    assert not http.calls


ENTRIES = {
    "run": lambda p, **kw: run_claude(p, **kw),
    "arun": lambda p, **kw: asyncio.run(arun_claude(p, **kw)),
    "stream": lambda p, **kw: stream_claude(p, lambda _e: None, **kw),
}


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("kw", [{"tier": "bogus"}, {"model": "-x"}, {"effort": "--bare"}])
def test_bad_kwargs_refused_before_http(auto, http, cli, entry, kw):
    with pytest.raises(ValueError):
        ENTRIES[entry]("p", **kw)
    assert not http.calls and not cli


# --- one timeout budget per call (#2) ------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    """A fake monotonic clock the HTTP fake can advance."""
    now = [1000.0]
    monkeypatch.setattr(runner_mod.time, "monotonic", lambda: now[0])
    return now


def _slow_failing_http(monkeypatch, clock, spent):
    def urlopen(req, timeout):
        clock[0] += spent
        raise urllib.error.URLError("stalled")

    monkeypatch.setattr(http_api._OPENER, "open", urlopen)


def _recording_popen(monkeypatch):
    timeouts = []

    class _Popen:
        pid, returncode = 1, 0

        def __init__(self, *_args, **_kwargs):
            pass

        def communicate(self, _input=None, timeout=None):
            timeouts.append(timeout)
            return json.dumps({"is_error": False, "result": "ok"}), ""

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _Popen)
    return timeouts


def test_cli_fallback_gets_only_the_remaining_timeout(auto, monkeypatch, clock):
    _slow_failing_http(monkeypatch, clock, spent=250)
    timeouts = _recording_popen(monkeypatch)
    assert run_claude("p", timeout=300).text == "ok"
    assert timeouts == [50]


def test_http_spending_the_whole_budget_times_out_without_spawning(auto, monkeypatch, clock):
    _slow_failing_http(monkeypatch, clock, spent=400)
    timeouts = _recording_popen(monkeypatch)
    with pytest.raises(runner_mod.ClaudeCliError, match="timed out after 300s"):
        run_claude("p", timeout=300)
    assert timeouts == []


# --- backend="api" errors go through the same classifier (#5) ------------------


def _fail_with(monkeypatch, code, body):
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(
            req.full_url, code, "err", HTTPMessage(), io.BytesIO(body.encode())
        )

    monkeypatch.setattr(http_api._OPENER, "open", urlopen)
    return calls


def test_api_429_is_a_usage_limit(auto, monkeypatch, cli):
    _fail_with(monkeypatch, 429, '{"error":"slow down"}')
    with pytest.raises(runner_mod.UsageLimitError) as info:
        run_claude("p", backend="api")
    assert info.value.session_wide is False  # one upstream of a combo, not the session
    assert not cli


def test_api_targets_skipped_is_a_gateway_error(auto, monkeypatch, cli):
    _fail_with(monkeypatch, 503, '{"error":{"code":"ALL_TARGETS_SKIPPED"}}')
    with pytest.raises(runner_mod.ClaudeCliError, match="omniroute gateway error") as info:
        run_claude("p", backend="api")
    assert not isinstance(info.value, runner_mod.UsageLimitError)
    assert "ALL_TARGETS_SKIPPED" in info.value.stdout


def test_api_other_status_keeps_the_body(auto, monkeypatch, cli):
    _fail_with(monkeypatch, 422, '{"error":"bad model id"}')
    with pytest.raises(HttpLlmError, match="bad model id"):
        run_claude("p", backend="api")


def test_auto_429_still_falls_back_to_cli(auto, monkeypatch, cli):
    _fail_with(monkeypatch, 429, '{"error":"slow down"}')
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p")


# --- a per-request rejection does not park HTTP for everyone (#13) ---------------


@pytest.mark.parametrize("code", [400, 413, 422])
def test_per_request_rejection_does_not_cool_down(auto, monkeypatch, cli, code):
    calls = _fail_with(monkeypatch, code, '{"error":"bad request"}')
    for _ in range(2):
        with pytest.raises(RuntimeError, match="cli reached"):
            run_claude("p")
    assert len(calls) == 2 and len(cli) == 2  # both fell back, neither parked HTTP


@pytest.mark.parametrize("code", [401, 404, 429, 503])
def test_gateway_wide_failure_still_cools_down(auto, monkeypatch, cli, code):
    calls = _fail_with(monkeypatch, code, '{"error":"no"}')
    for _ in range(2):
        with pytest.raises(RuntimeError, match="cli reached"):
            run_claude("p")
    assert len(calls) == 1


# --- claude-* ids take the CLI's glob route, not a verbatim HTTP id (#4) ------------


def test_claude_model_ids_stay_on_the_cli(auto, http, cli):
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p", model="claude-opus-5-5")
    assert not http.calls


def test_combo_names_still_use_http(auto, http, cli):
    assert run_claude("p", model="sub-first").text == "hi"
    assert http.calls[0][1]["model"] == "sub-first" and not cli


# --- backend values are checked, "api" never turns into a silent CLI call (#14) ------


@pytest.mark.parametrize("value", ["CLI", "http", "Auto"])
def test_unknown_backend_kwarg_is_refused(auto, http, cli, value):
    with pytest.raises(ValueError, match="backend"):
        run_claude("p", backend=value)
    assert not http.calls and not cli


def test_unknown_backend_env_is_refused(auto, monkeypatch, http, cli):
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "CLI")
    with pytest.raises(ValueError, match="CLAUDE_CLI_BACKEND"):
        run_claude("p")
    assert not http.calls and not cli


@pytest.mark.parametrize(
    "kw", [{"tools": "Read"}, {"output_format": "stream-json"}, {"model": "claude-opus-5-5"}]
)
def test_api_refuses_a_call_only_the_cli_can_carry(auto, http, cli, kw):
    with pytest.raises(ValueError, match="api"):
        run_claude("p", backend="api", **kw)
    assert not http.calls and not cli


def test_api_with_direct_is_refused(auto, http, cli):
    with pytest.raises(ValueError, match="direct"):
        run_claude("p", backend="api", direct=True)
    assert not cli


def test_direct_ignores_an_api_env(auto, monkeypatch, http, cli):
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "api")  # a process-wide default, not this call's ask
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p", direct=True)


# --- a malformed `usage` is an HttpLlmError, so auto still falls back (#15) ----------


@pytest.mark.parametrize("usage", ["n/a", [1, 2], 3])
def test_non_dict_usage_is_tolerated(auto, http, cli, usage):
    http.body = json.dumps({**REPLY, "usage": usage}).encode()
    res = run_claude("p")
    assert res.text == "hi" and res.raw["usage"] == {"input_tokens": None, "output_tokens": None}


# --- a trailing slash in OMNIROUTE_URL (#17) ------------------------------------------


def test_trailing_slash_in_url_is_dropped(auto, monkeypatch, http):
    monkeypatch.setenv("OMNIROUTE_URL", "http://localhost:20128/")
    omniroute_chat("p", timeout=5)
    assert http.calls[0][0].full_url == "http://localhost:20128/v1/chat/completions"


# --- an HTTP answer keeps the tier and leaves a log line (#9) ----------------------


def test_http_result_echoes_tier_and_logs(auto, http, cli, caplog):
    caplog.set_level("INFO", logger="claude_cli.http_api")
    res = run_claude("p", model="opus", tier="high")
    assert res.tier == "high"
    assert any("via http" in r.getMessage() and "hard" in r.getMessage() for r in caplog.records)


def test_async_and_stream_http_results_echo_tier(auto, http, cli):
    assert asyncio.run(arun_claude("p", tier="low")).tier == "low"
    assert stream_claude("p", lambda _e: None, tier="low").tier == "low"


@pytest.mark.parametrize("exc", [BadStatusLine("junk"), IncompleteRead(b"x")])
def test_malformed_http_answer_falls_back_to_cli_and_cools_down(auto, http, cli, monkeypatch, exc):
    def boom(req, timeout):
        raise exc

    monkeypatch.setattr(http_api._OPENER, "open", boom)
    with pytest.raises(RuntimeError, match="cli reached"):
        run_claude("p")
    assert len(cli) == 1


def test_output_format_text_is_refused_before_http(auto, http, cli):
    with pytest.raises(ValueError, match="output_format"):
        run_claude("p", output_format="text")
    assert not http.calls and not cli
