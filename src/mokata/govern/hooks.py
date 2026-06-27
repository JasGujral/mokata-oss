"""G4 — sync / async hook discipline.

SYNC hooks are for SECURITY ONLY and are the only hooks allowed to block: a failing
security check returns exit code 2 (the hard block). ASYNC hooks are for observability;
they never block and never fail the action — exceptions are captured, not raised.

This module is the in-process runner; `hooks/secret_guard.py` is the canonical shipped
sync security hook, and `hooks/session_start.py` is the canonical async one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

SECURITY_BLOCK_EXIT = 2


@dataclass
class HookResult:
    name: str
    blocked: bool
    exit_code: int
    message: str = ""
    is_async: bool = False


def run_sync_hook(name: str, passed: bool, reason: str = "",
                  security: bool = True, ledger: Any = None) -> HookResult:
    """Run a synchronous hook. Sync hooks may ONLY exist for security; a non-security
    sync hook is a misuse and is rejected."""
    if not security:
        raise ValueError(
            "sync hooks are for security only — use an async hook for observability")
    blocked = not passed
    exit_code = SECURITY_BLOCK_EXIT if blocked else 0
    result = HookResult(name=name, blocked=blocked, exit_code=exit_code,
                        message=reason if blocked else "ok")
    if ledger is not None:
        ledger.record("hook", hook=name, sync=True, blocked=blocked,
                      exit_code=exit_code, reason=result.message)
    return result


def run_async_hook(name: str, observe: Callable[[], Any],
                   ledger: Any = None) -> HookResult:
    """Run an asynchronous observability hook. It never blocks; a raised exception is
    captured so the action proceeds regardless."""
    message = "ok"
    try:
        observe()
    except Exception as exc:                      # observability must never break flow
        message = f"observer error (ignored): {exc}"
    result = HookResult(name=name, blocked=False, exit_code=0, message=message,
                        is_async=True)
    if ledger is not None:
        ledger.record("hook", hook=name, sync=False, blocked=False, reason=message)
    return result
