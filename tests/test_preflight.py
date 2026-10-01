"""Preflight tests — the real `omniroute` binary is never invoked (subprocess faked)."""

from __future__ import annotations

import subprocess

import pytest

from claude_cli import preflight as preflight_mod
from claude_cli.preflight import (
    CONSTRAINTS,
    GUIDES,
    MATRIX,
    QUOTA_FREE_SUBCOMMANDS,
    TIERS,
    doctor,
    health,
    quota_status,
    simulate,
)


def test_utilization_matrix_covers_guide_list():
    """Every v3.8.51 guide maps to at least one matrix row."""
    covered: set[str] = set()
    for entry in MATRIX:
        covered.update(entry["guides"])
    assert set(GUIDES) <= covered
    assert len(GUIDES) >= 18


def test_avoided_entries_name_constraint():
    """AVOIDED rows name their blocking constraint — no unexplained gaps."""
    avoided = [e for e in MATRIX if e["status"] == "AVOIDED"]
    assert avoided
    for entry in avoided:
        assert any(c in entry["reason"] for c in CONSTRAINTS), entry


def test_tiers_order_free_before_paid():
    """ULTRA/HIGH/MIDDLE/LOW each drain free targets first, cited to benchmarks."""
    assert set(TIERS) == {"ultra", "high", "middle", "low"}
    for name, tier in TIERS.items():
        assert tier["free_first"] is True, name
        assert tier["benchmarks"], name
        assert tier["combo"], name


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_preflight_failure_returns_false(monkeypatch):
    """A dead gateway reports (False, detail) — doctor checks never raise."""

    def _run(args, **kwargs):
        raise FileNotFoundError("omniroute not on PATH")

    monkeypatch.setattr(subprocess, "run", _run)
    ok, detail = health()
    assert ok is False
    assert isinstance(detail, str) and detail

    def _timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(subprocess, "run", _timeout)
    ok, detail = doctor(timeout=5)
    assert ok is False
    assert isinstance(detail, str) and detail


def test_preflight_spends_no_quota(monkeypatch):
    """Preflight helpers only invoke quota-free subcommands."""
    calls: list[list[str]] = []

    def _run(args, **kwargs):
        calls.append(list(args))
        return FakeProc(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    assert health()[0] is True
    assert doctor()[0] is True
    assert quota_status()[0] is True
    assert simulate("hi", combo="free-first")[0] is True
    assert calls, "expected subprocess invocations"
    for args in calls:
        assert args[:1] == ["omniroute"]
        assert args[1] in QUOTA_FREE_SUBCOMMANDS
        if args[1] == "run":
            assert "--dry-run" in args


def test_preflight_needs_no_live_gateway(monkeypatch):
    """With no binary on PATH the whole suite reports instead of raising."""
    monkeypatch.setattr(preflight_mod.shutil, "which", lambda _: None)

    def _run(args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _run)
    for result in (health(), doctor(), quota_status(), simulate("hi")):
        assert result[0] is False
        assert isinstance(result[1], str)


def test_dry_run_non_json_does_not_echo_raw_output(monkeypatch):
    """A human-readable plan may carry env VALUES; the error must not repeat them."""
    from claude_cli.errors import ClaudeCliError
    from claude_cli.preflight import dry_run_plan

    monkeypatch.setattr(
        subprocess, "run",
        lambda args, **kw: FakeProc(stdout="ANTHROPIC_AUTH_TOKEN=sk-secret-123\n"),
    )
    with pytest.raises(ClaudeCliError) as exc:
        dry_run_plan()
    assert "sk-secret-123" not in str(exc.value)


def test_dry_run_env_keys_ignore_a_non_list_shape(monkeypatch):
    """A string `changedOrAdded` must not be split into one-letter key names."""
    import json

    from claude_cli.preflight import dry_run_plan

    plan = {"command": "claude", "args": [], "env": {"changedOrAdded": "ANTHROPIC_BASE_URL"}}
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: FakeProc(stdout=json.dumps(plan)))
    assert dry_run_plan()["env_keys"] == []
