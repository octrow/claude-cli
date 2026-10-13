"""HTTP route tests — urlopen is faked, no gateway and no CLI is ever reached."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest

from claude_cli import arun_claude, cached_run, http_api, run_claude
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
        calls = []

    def urlopen(req, timeout):
        calls.append((req, json.loads(req.data), timeout))
        if H.fail:
            raise urllib.error.URLError("down")
        return io.StringIO(json.dumps(REPLY))

    H.calls = calls
    monkeypatch.setattr(http_api.urllib.request, "urlopen", urlopen)
    return H


@pytest.fixture
def cli(monkeypatch):
    """Fake CLI path: counts the argv builds that reach the subprocess layer."""
    seen = []

    def fake(*a, **kw):
        seen.append(kw)
        raise RuntimeError("cli reached")

    monkeypatch.setattr(runner_mod.subprocess, "Popen", fake)
    return seen


def test_alias_mapping(auto, http):
    for model, combo in [("haiku", "free-stack"), ("sonnet", "free-stack"),
                         ("opus", "hard"), ("sub-first", "sub-first")]:
        omniroute_chat("p", model=model, timeout=5)
        assert http.calls[-1][1]["model"] == combo


def test_request_shape_no_temperature_system_message(auto, http):
    res = omniroute_chat("p", model="haiku", timeout=7, system_prompt="be brief", effort="low")
    req, body, timeout = http.calls[0]
    assert body["messages"] == [{"role": "system", "content": "be brief"},
                                {"role": "user", "content": "p"}]
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


@pytest.mark.parametrize("kw", [{"tools": "Read"}, {"allowed_tools": "Read"},
                                {"output_format": "stream-json"}, {"slash_commands": True},
                                {"backend": "cli"}])
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

    async def no_spawn(*a, **kw):
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
