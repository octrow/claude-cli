"""Runner tests — the real `claude` binary is never invoked (subprocess is faked)."""

from __future__ import annotations

import json
import subprocess

import pytest

from claude_cli import (
    ClaudeCliError,
    NotLoggedInError,
    UsageLimitError,
    arun_claude,
    build_args,
    run_claude,
)
from claude_cli import runner as runner_mod

ENVELOPE = json.dumps(
    {"is_error": False, "result": "hello", "total_cost_usd": 0.0123, "duration_ms": 1500}
)

STREAM = "\n".join(
    [
        '{"type":"system","subtype":"init"}',
        "not json at all",
        '{"type":"stream_event","event":{"delta":"hel"}}',
        json.dumps(
            {
                "type": "result",
                "result": "hello",
                "total_cost_usd": 0.5,
                "duration_ms": 42,
                "num_turns": 1,
            }
        ),
    ]
)


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_run(monkeypatch):
    """Capture argv/cwd and return a canned CompletedProcess-alike."""
    calls: list[dict] = []

    def install(returncode=0, stdout=ENVELOPE, stderr=""):
        def _run(args, **kwargs):
            calls.append({"args": args, **kwargs})
            return FakeProc(returncode, stdout, stderr)

        monkeypatch.setattr(subprocess, "run", _run)
        return calls

    return install


def test_result_and_error_carry_raw_stdout(fake_run):
    """Archiving consumers (tlf digest) need the raw CLI output back."""
    fake_run(stdout=ENVELOPE)
    assert run_claude("p").stdout == ENVELOPE
    fake_run(returncode=1, stdout="partial stream", stderr="boom")
    with pytest.raises(ClaudeCliError) as exc_info:
        run_claude("p")
    assert exc_info.value.stdout == "partial stream"


# --- flag construction ------------------------------------------------------

def test_build_args_isolation_flags():
    args = build_args(mcp_config="/tmp/mcp.json")
    assert args[:2] == ["claude", "-p"]
    for flag in (
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ):
        assert flag in args
    assert args[args.index("--mcp-config") + 1] == "/tmp/mcp.json"
    assert args[args.index("--setting-sources") + 1] == "user"
    assert args[args.index("--tools") + 1] == ""  # no tools by default


def test_build_args_never_passes_bare():
    for fmt in ("json", "stream-json"):
        args = build_args(output_format=fmt, mcp_config="m", tools="WebSearch")
        assert "--bare" not in args


def test_build_args_tools_are_also_allowed():
    args = build_args(tools="WebSearch", mcp_config="m")
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert args[args.index("--allowed-tools") + 1] == "WebSearch"


def test_build_args_stream_json_is_verbose():
    args = build_args(output_format="stream-json", mcp_config="m")
    assert "--verbose" in args and "--include-partial-messages" in args
    assert args[args.index("--output-format") + 1] == "stream-json"


def test_build_args_effort_optional():
    assert "--effort" not in build_args(effort=None, mcp_config="m")
    args = build_args(effort="high", mcp_config="m")
    assert args[args.index("--effort") + 1] == "high"


def test_run_claude_passes_prompt_and_scratch_cwd(fake_run, tmp_path):
    calls = fake_run()
    run_claude("say hi", model="haiku")
    call = calls[0]
    assert call["input"] == "say hi"
    assert call["args"][call["args"].index("--model") + 1] == "haiku"
    # throwaway cwd, not the caller's project dir (so no CLAUDE.md pickup)
    assert call["cwd"] and call["cwd"] != str(tmp_path.cwd())
    assert "claude-cli-" in call["cwd"]


def test_mcp_config_file_is_empty_servers(fake_run, monkeypatch):
    """The mcp-config handed to the CLI exists and declares zero servers."""
    fake_run()
    seen = {}
    real = runner_mod._write_empty_mcp

    def spy(work):
        path = real(work)
        with open(path, encoding="utf-8") as fh:
            seen["content"] = fh.read()
        return path

    monkeypatch.setattr(runner_mod, "_write_empty_mcp", spy)
    run_claude("x")
    assert json.loads(seen["content"]) == {"mcpServers": {}}


# --- result handling --------------------------------------------------------

def test_envelope_result(fake_run):
    fake_run()
    res = run_claude("x")
    assert res.text == "hello"
    assert res.cost_usd == pytest.approx(0.0123)
    assert res.duration_ms == 1500
    assert res.raw["is_error"] is False


def test_stream_json_result(fake_run):
    fake_run(stdout=STREAM)
    res = run_claude("x", output_format="stream-json")
    assert res.text == "hello"
    assert res.cost_usd == 0.5
    assert res.duration_ms == 42
    assert res.raw["num_turns"] == 1


def test_not_logged_in(fake_run):
    fake_run(returncode=1, stdout="", stderr="Not logged in")
    with pytest.raises(NotLoggedInError, match="/login"):
        run_claude("x")


def test_usage_limit_from_stdout_result_event(fake_run):
    stdout = json.dumps({"type": "result", "result": "You've hit your session limit"})
    fake_run(returncode=1, stdout=stdout)
    with pytest.raises(UsageLimitError):
        run_claude("x", output_format="stream-json")


@pytest.mark.parametrize(
    "msg",
    ["usage limit reached", "rate-limit", "quota exceeded", "Too Many Requests", "HTTP 429"],
)
def test_usage_limit_signatures(fake_run, msg):
    fake_run(returncode=1, stderr=msg)
    with pytest.raises(UsageLimitError):
        run_claude("x")


def test_context_limit_is_not_a_usage_limit(fake_run):
    fake_run(returncode=1, stderr="context limit exceeded")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)


def test_nonzero_exit(fake_run):
    fake_run(returncode=2, stderr="boom")
    with pytest.raises(ClaudeCliError, match="exited 2: boom"):
        run_claude("x")


def test_is_error_envelope(fake_run):
    fake_run(stdout=json.dumps({"is_error": True, "result": "model refused"}))
    with pytest.raises(ClaudeCliError, match="model refused"):
        run_claude("x")


def test_non_json_stdout(fake_run):
    fake_run(stdout="<html>gateway error</html>")
    with pytest.raises(ClaudeCliError, match="non-JSON"):
        run_claude("x")


def test_stream_without_result_event(fake_run):
    fake_run(stdout='{"type":"system"}')
    with pytest.raises(ClaudeCliError, match="no result"):
        run_claude("x", output_format="stream-json")


def test_timeout(monkeypatch):
    def _run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(ClaudeCliError, match="timed out"):
        run_claude("x", timeout=5)


def test_provider_satisfies_protocol():
    from claude_cli import ClaudeCliProvider, LLMProvider

    assert isinstance(ClaudeCliProvider(), LLMProvider)


def test_provider_ask(fake_run):
    from claude_cli import ClaudeCliProvider

    calls = fake_run()
    assert ClaudeCliProvider(model="opus").ask("x") == "hello"
    assert calls[0]["args"][calls[0]["args"].index("--model") + 1] == "opus"


def test_claude_available(monkeypatch):
    from claude_cli import claude_available

    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: "/usr/bin/claude")
    assert claude_available() is True
    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: None)
    assert claude_available() is False


# --- async ------------------------------------------------------------------

class FakeAsyncProc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self._out = (stdout.encode(), stderr.encode())

    async def communicate(self, _data=None):
        return self._out

    def kill(self):  # pragma: no cover - only on the timeout path
        pass

    async def wait(self):  # pragma: no cover
        pass


@pytest.fixture
def fake_async(monkeypatch):
    calls: list[dict] = []

    def install(returncode=0, stdout=ENVELOPE, stderr=""):
        async def _exec(*args, **kwargs):
            calls.append({"args": list(args), **kwargs})
            return FakeAsyncProc(returncode, stdout, stderr)

        monkeypatch.setattr(runner_mod.asyncio, "create_subprocess_exec", _exec)
        return calls

    return install


async def test_arun_claude_ok(fake_async):
    calls = fake_async()
    res = await arun_claude("x", model="sonnet")
    assert res.text == "hello"
    assert "--strict-mcp-config" in calls[0]["args"]
    assert "--bare" not in calls[0]["args"]


async def test_arun_claude_not_logged_in(fake_async):
    fake_async(returncode=1, stderr="Not logged in")
    with pytest.raises(NotLoggedInError):
        await arun_claude("x")
