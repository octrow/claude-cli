"""Exceptions raised by the runner.

All derive from :class:`ClaudeCliError`, itself a ``RuntimeError`` subclass — so
existing callers that ``except RuntimeError`` to degrade gracefully (the
finance-insights rules-only fallback, chat-watch's regex-only mode) keep working
unchanged.
"""

from __future__ import annotations


class ClaudeCliError(RuntimeError):
    """The ``claude`` CLI exited non-zero, timed out, or reported an error.

    ``stdout`` carries the raw CLI output of the failed run (empty when there
    was none, e.g. a timeout) — consumers that archive failing runs for
    inspection read it instead of losing the stream.
    """

    def __init__(self, message: str, *, stdout: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout


class NotLoggedInError(ClaudeCliError):
    """The CLI reported "Not logged in" — run ``claude`` once and ``/login``."""


class UsageLimitError(ClaudeCliError):
    """The CLI hit a usage/rate/session limit — fail fast, retrying won't help."""
