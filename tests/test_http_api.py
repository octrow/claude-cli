"""HTTP route tests — urlopen is faked, no gateway and no CLI is ever reached."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest

from claude_cli import arun_claude, cached_run, http_api, run_claude, stream_claude
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
        {"allowed_tools": "Read"},
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

        def log_message(self, *_args):
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
