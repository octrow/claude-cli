import pytest

from claude_cli import http_api


@pytest.fixture(autouse=True)
def _no_live_gateway(monkeypatch):
    """Tests never reach a real gateway: HTTP route off, cooldown reset, no .env key."""
    monkeypatch.setenv("CLAUDE_CLI_BACKEND", "cli")
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    monkeypatch.setattr(http_api, "ENV_FILE", http_api.Path("/nonexistent/.env"))
    monkeypatch.setattr(http_api, "_down_until", 0.0)
