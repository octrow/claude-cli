"""Runner tests — the real `claude` binary is never invoked (subprocess is faked)."""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from claude_cli import (
    ClaudeCliError,
    ClaudeResult,
    NotLoggedInError,
    UsageLimitError,
    arun_claude,
    build_args,
    ping,
    run_claude,
    stream_claude,
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


class FakePopen:
    """Popen stand-in: yields canned stream-json lines, records what was written."""

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.returncode = 0
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(STREAM + "\n")
        self.stderr = io.StringIO("")

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_stream_claude_reports_events_as_they_arrive(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    seen: list[dict] = []
    result = stream_claude("p", seen.append, cwd=None)
    assert [e.get("type") for e in seen] == ["system", "stream_event", "result"]
    assert result.text == "hello"  # the final envelope still folds the same way


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
    assert args[:5] == ["omniroute", "run", "claude", "--", "-p"]
    for flag in (
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ):
        assert flag in args
    assert args[args.index("--mcp-config") + 1] == "/tmp/mcp.json"
    assert args[args.index("--setting-sources") + 1] == "user"
    assert args[args.index("--tools") + 1] == ""  # no tools by default


def test_build_args_project_mode_enables_slash_commands():
    args = build_args(
        mcp_config="m",
        slash_commands=True,
        setting_sources="user,project",
        permission_mode="acceptEdits",
    )
    assert "--disable-slash-commands" not in args
    assert args[args.index("--setting-sources") + 1] == "user,project"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "--bare" not in args


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


def test_answer_mentioning_rate_limits_is_not_a_usage_limit(fake_run):
    stdout = json.dumps(
        {"type": "result", "result": "System design: protect the API with a rate limit"}
    )
    fake_run(returncode=0, stdout=stdout)
    res = run_claude("x", output_format="stream-json")
    assert res.text.endswith("rate limit")


def test_context_limit_is_not_a_usage_limit(fake_run):
    fake_run(returncode=1, stderr="context limit exceeded")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)


def test_nonzero_exit_strips_omniroute_noise_from_stderr(fake_run):
    """The real error survives; the wrapper's warnings around it don't."""
    stderr = "\n".join(
        [
            "\x1b[33m⚠ STORAGE_ENCRYPTION_KEY in ... is ignored, ... set it first\x1b[0m",
            "⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY ...",
            "📋 Loaded env from /home/octrow/.omniroute/.env",
            "Error: the real cause",
        ]
    )
    fake_run(returncode=1, stdout="", stderr=stderr)
    with pytest.raises(ClaudeCliError) as exc_info:
        run_claude("x")
    message = str(exc_info.value)
    assert "Error: the real cause" in message
    assert "is ignored" not in message
    assert "Loaded env from" not in message
    assert "connectors are disabled" not in message


def test_nonzero_exit_stderr_only_omniroute_warnings(fake_run):
    """Nothing left after cleanup — a meaningful fallback, not an empty tail."""
    stderr = "📋 Loaded env from /home/octrow/.omniroute/.env\n⚠ FOO is ignored, BAR set it first"
    fake_run(returncode=3, stdout="", stderr=stderr)
    with pytest.raises(
        ClaudeCliError, match=r"claude exited 3: \(stderr held only omniroute warnings\)"
    ):
        run_claude("x")


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

    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: "/usr/bin/tool")
    assert claude_available() is True
    monkeypatch.setattr(
        runner_mod.shutil,
        "which",
        lambda name: "/usr/bin/claude" if name == "claude" else None,
    )
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


# --- ping --------------------------------------------------------------------

def test_ping_success(monkeypatch):
    def fake_run_claude(prompt, *, model=None, timeout=None, **_kwargs):
        assert "pong" in prompt
        assert model == "haiku"
        return ClaudeResult(text="pong")

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)
    ok, message = ping()
    assert ok is True
    assert message.startswith("pong via omniroute, ")
    assert message.endswith("s")


def test_ping_failure_never_raises(monkeypatch):
    def fake_run_claude(prompt, *, model=None, timeout=None, **_kwargs):
        raise ClaudeCliError("claude exited 1: Not logged in")

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)
    ok, message = ping(model="sonnet", timeout=5)
    assert ok is False
    assert "Not logged in" in message
    assert len(message) <= 200


def test_ping_failure_message_is_cleaned_and_capped(monkeypatch):
    def fake_run_claude(prompt, *, model=None, timeout=None, **_kwargs):
        raise ClaudeCliError("📋 Loaded env from x\nError: " + "x" * 300)

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)
    ok, message = ping()
    assert ok is False
    assert "Loaded env from" not in message
    assert len(message) <= 200


def test_usage_limit_message_quotes_the_limit_not_an_unrelated_stderr_warning(fake_run):
    """The real limit sits in stdout while stderr carries something else entirely (an
    untrusted-workspace notice). `error_detail` prefers stderr, so the raised message used to
    name the warning and hide the limit — the reader then chases the wrong bug."""
    stdout = json.dumps({"type": "result", "result": "You've hit your session limit"})
    fake_run(
        returncode=1,
        stdout=stdout,
        stderr="Ignoring 7 permissions.allow entries: this workspace has not been trusted.",
    )
    with pytest.raises(UsageLimitError) as exc:
        run_claude("x", output_format="stream-json")
    assert "session limit" in str(exc.value)
    assert "permissions.allow" not in str(exc.value)


def test_all_targets_skipped_is_not_a_usage_limit_and_points_at_omniroute(fake_run):
    """OmniRoute answers 503 both for a spent quota and for "no provider matched" — the second is
    a config bug that waiting never fixes, so it must stay a plain ClaudeCliError (a
    UsageLimitError tells callers to abort the run) and instead carry the pointer."""
    fake_run(returncode=1, stderr="503 ALL_TARGETS_SKIPPED: no target accepted the request")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)
    assert "omniroute health" in str(exc.value)
    assert "github.com/diegosouzapw/OmniRoute/issues" in str(exc.value)


def test_an_ordinary_failure_carries_no_omniroute_pointer(fake_run):
    """The hint is a signal, not a footer: appending it to every error would make it noise and
    send the reader to the wrong repository."""
    fake_run(returncode=1, stderr="ENOENT: no such file or directory, open 'posting.md'")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert "OmniRoute" not in str(exc.value)


# --- routing pass-throughs (omniroute-full-utilization) -----------------------

def test_build_args_routing_defaults_off():
    args = build_args(mcp_config="m")
    assert args[:5] == ["omniroute", "run", "claude", "--", "-p"]
    for flag in ("--remote", "--base-url", "--context", "--provider", "--profile"):
        assert flag not in args
    for flag in (
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ):
        assert flag in args


def test_build_args_routing_never_passes_bare():
    combos = [
        {"tier": "ultra"},
        {"tier": "low", "remote": "http://gw:20128"},
        {"tier": "high", "context": "prod", "provider": "agy"},
        {"base_url": "http://gw:20128", "profile": "middle"},
        {
            "tier": "middle",
            "remote": "http://gw:20128",
            "context": "c",
            "provider": "p",
            "output_format": "stream-json",
            "tools": "Read",
            "slash_commands": True,
            "setting_sources": "user,project",
            "permission_mode": "acceptEdits",
        },
    ]
    for kwargs in combos:
        assert "--bare" not in build_args(mcp_config="m", **kwargs)


def test_dry_run_returns_plan_without_execution(monkeypatch):
    """The dry-run helper surfaces the plan; key names only, never secret values."""
    import json as json_mod

    from claude_cli.preflight import dry_run_plan

    plan = {
        "target": "claude",
        "command": "claude",
        "args": [],
        "env": {"changedOrAdded": ["ANTHROPIC_BASE_URL"], "removed": []},
    }
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(list(args))
        proc = FakeProc(returncode=0, stdout=json_mod.dumps(plan), stderr="")
        return proc

    monkeypatch.setattr(subprocess, "run", _run)
    result = dry_run_plan()
    assert result["env_keys"] == ["ANTHROPIC_BASE_URL"]
    assert "sk-" not in json_mod.dumps(result)
    assert calls and "--dry-run" in calls[0] and "--json" in calls[0]


def test_tier_leaves_claude_model_valid():
    for tier in ("ultra", "high", "middle", "low"):
        args = build_args(mcp_config="m", tier=tier)
        assert args[:2] == ["omniroute", "run"]
        claude_at = args.index("claude")
        gateway = args[2:claude_at]
        assert "--profile" in gateway and gateway[gateway.index("--profile") + 1] == tier
        assert args[args.index("--model") + 1] in ("haiku", "sonnet", "opus")
        assert "combo/" not in " ".join(args)
        assert "--bare" not in args


# --- execution classification (omniroute-full-utilization) --------------------

def test_all_targets_skipped_is_gateway_not_limit(fake_run):
    fake_run(returncode=1, stderr="503 ALL_TARGETS_SKIPPED: no target accepted")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)
    assert "omniroute health" in str(exc.value)
    assert "github.com/diegosouzapw/OmniRoute/issues" in str(exc.value)


@pytest.mark.parametrize(
    "msg",
    [
        "PROXY_FAST_FAIL_TIMEOUT_MS tripped",
        "circuit breaker open for provider agy",
        "allowedModels excludes every combo target",
        "keepAliveTimeout: 1 suggested by PROXY_GUIDE",
    ],
)
def test_gateway_strings_earn_docs_pointer(fake_run, msg):
    fake_run(returncode=1, stderr=msg)
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert "github.com/diegosouzapw/OmniRoute" in str(exc.value)


def test_gateway_hint_orders_free_checks_first(fake_run):
    fake_run(returncode=1, stderr="ECONNREFUSED 127.0.0.1:20128")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    message = str(exc.value)
    assert message.index("`omniroute health`") < message.index("`omniroute doctor`")
    assert message.index("`omniroute doctor`") < message.index("--dry-run")
    assert message.index("--dry-run") < message.index("`omniroute quota`")


def test_ping_shape_preserved(monkeypatch):
    def fake_ok(prompt, *, model=None, timeout=None, **_kwargs):
        return ClaudeResult(text="pong")

    monkeypatch.setattr(runner_mod, "run_claude", fake_ok)
    ok, message = ping()
    assert (ok, message.startswith("pong via omniroute, ")) == (True, True)

    def fake_boom(prompt, *, model=None, timeout=None, **_kwargs):
        raise ClaudeCliError("boom")

    monkeypatch.setattr(runner_mod, "run_claude", fake_boom)
    ok, message = ping()
    assert ok is False and len(message) <= 200


def test_tier_attribution_survives_success_and_failure(fake_run):
    fake_run()
    assert run_claude("x", tier="high").tier == "high"
    fake_run(returncode=1, stderr="503 ALL_TARGETS_SKIPPED on combo ultra")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x", tier="ultra")
    assert "ultra" in str(exc.value)


def test_gateway_exhaustion_is_not_reported_as_a_usage_limit(fake_run):
    """A combo running out of targets must not masquerade as a session limit.

    OmniRoute quotes the failing upstream verbatim, so this text matches BOTH
    _GATEWAY_RE and _USAGE_LIMIT_RE. Before the reorder the limit branch won and
    a batch aborted on what was really "the gateway had nothing left right now".
    """
    fake_run(returncode=1, stderr="[omniroute] combo 'hard': fallback exhausted, all 6 targets failed (429)")
    with pytest.raises(ClaudeCliError) as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)
    assert "gateway" in str(exc.value).lower()


def test_upstream_limit_behind_the_gateway_is_not_session_wide(fake_run):
    """One limited upstream is not the end of the run: other targets remain."""
    fake_run(returncode=1, stderr="You've hit your session limit · resets 6:50am")
    with pytest.raises(UsageLimitError) as exc:
        run_claude("x")
    assert exc.value.session_wide is False


def test_all_targets_skipped_still_reads_as_a_gateway_error(fake_run):
    """Exhaustion outranks a limit: this text matches BOTH patterns, gateway must win."""
    stderr = "503 ALL_TARGETS_SKIPPED: upstream 429 rate limit"
    assert runner_mod._USAGE_LIMIT_RE.search(stderr)  # the input really is ambiguous
    fake_run(returncode=1, stderr=stderr)
    with pytest.raises(ClaudeCliError, match="omniroute gateway error") as exc:
        run_claude("x")
    assert not isinstance(exc.value, UsageLimitError)


def test_login_failure_mentioning_omniroute_is_still_not_logged_in(fake_run):
    """The gateway-first check must not swallow a login failure the wrapper relays."""
    fake_run(returncode=1, stderr="[omniroute] claude exited: Not logged in · Please run /login")
    with pytest.raises(NotLoggedInError):
        run_claude("x")


def test_upstream_limit_relayed_by_omniroute_is_still_a_usage_limit(fake_run):
    """A bare `omniroute` / 503 marker is not exhaustion: the limit must stay typed."""
    fake_run(returncode=1, stderr="[omniroute] upstream 429: You've hit your session limit")
    with pytest.raises(UsageLimitError) as exc:
        run_claude("x")
    assert exc.value.session_wide is False


def _stream_child(monkeypatch, code: str) -> None:
    """Point stream_claude at a real python child instead of omniroute."""
    import sys

    monkeypatch.setattr(runner_mod, "build_args", lambda **_kw: [sys.executable, "-c", code])


def test_stream_claude_times_out_on_a_silent_child(monkeypatch):
    """The deadline must hold even when the child prints nothing at all."""
    import time as _time

    _stream_child(monkeypatch, "import time; time.sleep(30)")
    started = _time.monotonic()
    with pytest.raises(ClaudeCliError, match="timed out after 1s"):
        stream_claude("p", lambda _e: None, timeout=1)
    assert _time.monotonic() - started < 10


def test_stream_claude_survives_a_stderr_flood(monkeypatch):
    """A child filling the stderr pipe before finishing stdout must not deadlock."""
    line = json.dumps({"type": "result", "result": "ok"})
    _stream_child(
        monkeypatch,
        f"import sys; sys.stderr.write('w' * 500_000); sys.stderr.flush(); print({line!r})",
    )
    assert stream_claude("p", lambda _e: None, timeout=5).text == "ok"


def test_stream_claude_classifies_a_child_that_exits_before_reading_stdin(monkeypatch):
    """An early-exiting child must surface its error, not a raw BrokenPipeError."""
    _stream_child(monkeypatch, "import sys; sys.stderr.write('Not logged in'); sys.exit(1)")
    with pytest.raises(NotLoggedInError):
        stream_claude("p" * 2_000_000, lambda _e: None, timeout=10)


async def test_arun_claude_kills_the_child_when_cancelled(monkeypatch):
    """Cancelling the awaiting task must not orphan the subprocess."""
    import asyncio
    import sys

    monkeypatch.setattr(
        runner_mod, "build_args",
        lambda **_kw: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    spawned = []
    real_exec = asyncio.create_subprocess_exec

    async def _exec(*args, **kwargs):
        proc = await real_exec(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    task = asyncio.create_task(arun_claude("p", timeout=60))
    while not spawned:
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spawned[0].returncode is not None  # killed and reaped


def _grandchild(pidfile) -> str:
    """A child that spawns a long sleeper (as omniroute spawns claude) and records its pid."""
    return (
        "import subprocess, sys, time;"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']);"
        f"open({str(pidfile)!r}, 'w').write(str(g.pid));"
        "time.sleep(30)"
    )


def _assert_gone(pidfile) -> None:
    import os
    import time as _time

    pid = int(pidfile.read_text())
    for _ in range(40):  # an orphan is reaped by init asynchronously
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().split(") ")[1].startswith("Z"):
                    return
            os.kill(pid, 0)
        except (ProcessLookupError, FileNotFoundError):
            return
        _time.sleep(0.05)
    os.kill(pid, 9)
    raise AssertionError("grandchild survived the timeout kill")


def test_stream_claude_timeout_kills_the_grandchild_too(monkeypatch, tmp_path):
    """omniroute spawns claude: killing only the wrapper leaves the grandchild holding
    the pipes, so the timeout waits for it (and it keeps spending quota)."""
    import time as _time

    pidfile = tmp_path / "gc.pid"
    _stream_child(monkeypatch, _grandchild(pidfile))
    started = _time.monotonic()
    with pytest.raises(ClaudeCliError, match="timed out after 1s"):
        stream_claude("p", lambda _e: None, timeout=1)
    assert _time.monotonic() - started < 6
    _assert_gone(pidfile)


async def test_arun_claude_timeout_kills_the_grandchild_too(monkeypatch, tmp_path):
    import sys

    pidfile = tmp_path / "gc.pid"
    code = _grandchild(pidfile)
    monkeypatch.setattr(runner_mod, "build_args", lambda **_kw: [sys.executable, "-c", code])
    with pytest.raises(ClaudeCliError, match="timed out after 1s"):
        await arun_claude("p", timeout=1)
    _assert_gone(pidfile)
