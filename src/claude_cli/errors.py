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
    """A usage/rate/session limit was hit.

    ``session_wide`` says whether the limit ends the whole run or just this call.
    Behind an OmniRoute combo it is ``False``: the limit belongs to ONE upstream
    (target #1 of the chain), the gateway still has other targets, and the next
    call may well succeed — so a batch must skip this item, not abort. It is
    ``True`` only for a direct ``claude`` call, where the limited subscription is
    the only route there is and every further call would hit the same wall.

    The caller owns the abort policy; this class only reports which case it is.
    """

    def __init__(self, message: str, *, stdout: str = "", session_wide: bool = True) -> None:
        super().__init__(message, stdout=stdout)
        self.session_wide = session_wide
