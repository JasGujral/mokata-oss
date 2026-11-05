"""MS.S1 — the shared cross-process OS file-lock helper (M-1).

Two Claude Code windows on one repo are TWO MCP processes. A `threading.Lock` is per-process and
therefore worthless across them — the only thing that serialises two OS processes is an OS-level
advisory file lock. This is THE one small, dependency-free (stdlib-only) lock the whole codebase
shares: MS.S1's StateStore RMW holds it across read→modify→replace, and MS.S3's ledger lock + MS.S6
reuse this SAME helper (it lives at the package top level so they import it without a cycle — it
imports nothing from mokata).

`file_lock(path)` is a context manager taking an EXCLUSIVE lock on a dedicated lock file:

  * **POSIX** uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)` in a bounded poll loop.
  * **Windows** uses `msvcrt.locking(fd, LK_NBLCK, 1)` on a one-byte region.
  * On BOTH, the poll passes a turnstile sidecar first (`_hold`, 0.0.27 `WIN-LOCK-WAIT`), so a
    waiter is not starved by a holder that re-takes the lock without pause.

Both back onto the OS, so the lock is **released when the fd is closed AND when the process dies**
(`kill -9`, crash, power loss) — there is no stale lock file to reap. The wait is BOUNDED: past the
timeout it raises `LockTimeout` NAMING the contended path (never an indefinite hang). The uncontended
path is two opens and two non-blocking lock syscalls (turnstile + lock) — cheap enough that holding it
around every state write costs nothing measurable single-process.

The lock file itself is only a rendezvous handle; its CONTENTS are never read or written (no schema,
no secret surface). Leaving it in place between runs is intentional and standard — deleting a lock
file races the next acquirer.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import errno
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from .errors import MokataError

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore

try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # POSIX (the common case in CI / contributor machines)
    msvcrt = None  # type: ignore

# The errnos a non-blocking lock attempt raises when the lock is already held by someone else — the
# signal to keep polling (anything else is a real error and propagates).
_CONTENDED = {getattr(errno, n) for n in ("EACCES", "EAGAIN", "EWOULDBLOCK", "EDEADLK", "EDEADLOCK")
              if hasattr(errno, n)}

DEFAULT_TIMEOUT = 10.0
_POLL_INTERVAL = 0.02


class LockTimeout(MokataError, TimeoutError):
    """Raised when an exclusive lock can't be acquired within the bounded wait. Its message NAMES the
    contended lock path so a stuck concurrent window is diagnosable, not mysterious."""


def backend_name() -> str:
    """The lock primitive in force on this host: ``"fcntl"`` (POSIX) or ``"msvcrt"`` (Windows).
    Exposed so callers/tests can assert the platform-appropriate branch is selected."""
    if fcntl is not None:
        return "fcntl"
    if msvcrt is not None:  # pragma: no cover - Windows only
        return "msvcrt"
    return "none"  # pragma: no cover - no locking primitive (unsupported platform)


def _try_acquire(fd: int) -> None:
    """One non-blocking attempt to take the exclusive lock on `fd`. Raises OSError (errno in
    `_CONTENDED`) if another holder has it; returns on success."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:  # pragma: no cover - Windows only
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - unsupported platform
        raise RuntimeError("no OS file-locking primitive (fcntl/msvcrt) available")


def _release(fd: int) -> None:
    """Release the lock held on `fd`. The fd's close (and process death) also releases it, so this is
    belt-and-braces for the normal in-process exit path."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows only
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def _open(lock_path: str) -> int:
    # 0o600: the sidecar only coordinates one user's own processes on one repo, so nobody else ever
    # needs to open it — and it carries no content to read anyway. Windows ignores the mode; the lock
    # itself is OS-held on the fd, so owner-only permissions cost nothing.
    return os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)


class _OsPrimitive:
    """The real primitive: one OS advisory exclusive lock per handle (`fcntl.flock` / `msvcrt`).
    `_hold` takes the primitive as a parameter so the ACQUISITION POLICY — the part that decides
    who waits and who barges — is testable on any OS with a modelled primitive."""
    open = staticmethod(_open)
    try_acquire = staticmethod(_try_acquire)
    release = staticmethod(_release)
    close = staticmethod(os.close)


def turnstile_path(lock_path: str) -> str:
    """The second sidecar `file_lock` passes through before it may wait on `lock_path` (see `_hold`).
    A sibling of the lock file, ending in `.lock` like every other lock handle (so anything that
    already skips `*.lock` skips it) and never `.json`/`.md`, so no artifact scan sees it; like the
    lock file, its contents are never read or written."""
    return lock_path + ".turn.lock"


def _poll(prim: Any, handle: Any, lock_path: str, timeout: float, deadline: float,
          poll_interval: float, sleep: Callable[[float], None],
          clock: Callable[[], float]) -> None:
    while True:
        try:
            prim.try_acquire(handle)
            return
        except OSError as exc:
            if exc.errno not in _CONTENDED:
                raise
            if clock() >= deadline:
                raise LockTimeout(
                    f"timed out after {timeout}s acquiring lock (held by another "
                    f"process): {lock_path}")
            sleep(poll_interval)


@contextmanager
def _hold(lock_path: str, timeout: float, poll_interval: float, prim: Any = _OsPrimitive,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    """The acquisition POLICY (0.0.27 `WIN-LOCK-WAIT`): a TURNSTILE in front of the lock.

    Both OS branches poll a NON-BLOCKING lock (`LOCK_NB` / `LK_NBLCK`), and a poll only wins if it
    lands in the gap between a holder's release and its next acquire. A writer that re-takes the
    lock without pause leaves a gap of a few syscalls, so a waiter polling every 20 ms lost for
    hundreds of the writer's cycles (Linux, measured: 150-320 cycles, ~2 s; the 0.0.26 Windows CI
    leg: a spec proposal ~20 s). Nothing queued the waiter — the kernel never saw it waiting.

    The fix is the classic no-barging turnstile, built from the same OS primitive: every acquirer
    first takes `turnstile_path(lock_path)`, then waits for the lock itself, then drops the
    turnstile. A waiter therefore sits on the turnstile WHILE it waits, and the writer — which must
    pass the same turnstile to come back — cannot re-take the lock ahead of it. The waiter gets the
    lock at the end of the writer's critical section in flight: one writer cycle, not hundreds. The
    turnstile is held only while acquiring (never across the protected work), so it is free nearly
    all the time and an arriving waiter takes it on its first poll.

    Same bound as before: ONE deadline covers both waits and expiry raises `LockTimeout` naming the
    lock path. Both locks are OS-held, so a process dying anywhere in here releases both — nothing to
    reap. Lock order is always turnstile → lock, and a turnstile is never taken while holding its
    own lock, so it adds no deadlock that two plain locks did not already have."""
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    deadline = clock() + max(0.0, timeout)
    turn = prim.open(turnstile_path(lock_path))
    try:
        _poll(prim, turn, lock_path, timeout, deadline, poll_interval, sleep, clock)
        try:
            main = prim.open(lock_path)
            try:
                _poll(prim, main, lock_path, timeout, deadline, poll_interval, sleep, clock)
            except BaseException:
                prim.close(main)
                raise
        finally:
            prim.release(turn)
    finally:
        prim.close(turn)
    try:
        yield
    finally:
        try:
            prim.release(main)
        finally:
            prim.close(main)


@contextmanager
def file_lock(lock_path: str, *, timeout: float = DEFAULT_TIMEOUT,
              poll_interval: float = _POLL_INTERVAL) -> Iterator[str]:
    """Hold an exclusive cross-process lock on `lock_path` for the duration of the `with` block.

    Waits (fairly — see `_hold`) until acquired or `timeout` seconds elapse, at which point it
    raises `LockTimeout` naming `lock_path`. The lock file and its `.turn.lock` turnstile are created
    if needed (their contents are never touched); the lock releases on block exit and, because it is
    OS-held, on process death too."""
    with _hold(lock_path, timeout, poll_interval):
        yield lock_path
