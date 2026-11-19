"""0.0.26 P32 `IDE-JOBS` — cooperative cancellation for long walks, on the thread doing them.

Jas, 2026-10-21/22: no arbitrary timeouts on graph, search or index work on multi-million-line
repos; long work is a visible job with progress, a Cancel that really stops it, and partial
results. A deadline used to be the only brake (the IDE's 20 s heavy lane): past it the page got a
503 and the work kept burning CPU for nobody (audit A #3).

The brake is now a FLAG on the thread running the work (`scope(flag)`), and CHECKPOINTS inside
the loops that take the time — per file in the grep floor's walk and reads, per file in the AST
index build, per batch in the meaning index, per wait slice in a code-review-graph read. A
checkpoint raises `Cancelled` when the flag is set; a subprocess (rg, code-review-graph) is killed
by `kill_on_cancel`. Outside a scope every checkpoint is a no-op, so the CLI path is unchanged.

The O7 `checkpoint=` parameter on `run_query` stays the per-question hook; this module is what it
points at for the walks underneath it.

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, List, Optional

from ..errors import ControlSignal

_T = threading.local()


class Cancelled(ControlSignal):
    """The work this thread is doing was cancelled (its job's Cancel, or its last viewer left).
    A control signal, not a failure: it unwinds to the job runner that armed the flag."""


# ============================================================== 0.0.29 lane B (R-B1) — the lever
#: Per-thread CRITICAL depth: a window a second Ctrl-C must not break into (a record write, the
#: process door's start and end). A plain dict keyed by thread ident — one item read or written at a
#: time under the GIL, no lock — because the CLI's signal handler reads it on the main thread while
#: that thread may be anywhere, including inside this module.
_CRIT: dict = {}
#: Escalation owed to a thread when it leaves its last critical window (ident -> True). Set by the
#: CLI's signal handler (`ci_check.cancel_on_sigint`), consumed by `leave_critical`.
_PENDING: dict = {}


def enter_critical() -> None:
    i = threading.get_ident()
    _CRIT[i] = _CRIT.get(i, 0) + 1


def leave_critical() -> None:
    """Leave one critical window. Leaving the LAST one with an escalation pending raises
    `KeyboardInterrupt` here — the second Ctrl-C that arrived inside the window, delivered the
    moment the window is done (R-B5)."""
    i = threading.get_ident()
    n = _CRIT.get(i, 0) - 1
    if n > 0:
        _CRIT[i] = n
        return
    _CRIT.pop(i, None)
    if _PENDING.pop(i, False):
        raise KeyboardInterrupt


def critical_depth(ident: Optional[int] = None) -> int:
    return _CRIT.get(threading.get_ident() if ident is None else ident, 0)


class Lever(threading.Event):
    """The ONE cancel lever (0.0.29 R-B1): an `Event` (so `scope`, `kill_on_cancel`, `bounded_io`
    and `git_safe` keep working) that also knows the work's commit points.

      * `pull()` — True: the work is cancelled (the event is set). False: too late (`sealed`), or a
        commit is in progress — then the pull is DEFERRED and delivered when the last commit ends.
      * `seal()` — permanent: from here a pull is too late. False when already pulled.
      * `commit(what)` — a durable write. Entry raises `Cancelled` if already pulled; a pull during
        the body waits for it; on success `what` is appended to `recorded`.

    The lock is held only to flip the state, never across a commit body or a wait, and no signal
    handler ever calls any of this (the CLI's handler only counts and writes a byte)."""

    def __init__(self) -> None:
        super().__init__()
        self._flip = threading.Lock()
        self.sealed = False
        self.committing = 0
        self.deferred = False
        #: A pull arrived while a commit was running (said once the block ends).
        self.deferred_seen = False
        self.recorded: List[str] = []

    def pull(self) -> bool:
        with self._flip:
            if self.sealed:
                return False
            if self.committing:
                self.deferred = self.deferred_seen = True
                return False
            self.set()
            return True

    def seal(self) -> bool:
        with self._flip:
            if self.is_set():
                return False
            self.sealed = True
            return True

    @contextmanager
    def commit(self, what: str) -> Iterator[None]:
        enter_critical()                     # FIRST (attack M4b): no gap a Ctrl-C can strand
        try:
            with self._flip:
                if self.is_set():
                    raise Cancelled("cancelled")
                self.committing += 1
            ok = False
            try:
                yield
                ok = True
            finally:
                with self._flip:
                    self.committing -= 1
                    if ok:
                        self.recorded.append(what)
                    if self.committing == 0 and self.deferred:   # depth 0 only (attack M4a)
                        self.deferred = False
                        if not self.sealed:
                            self.set()
        finally:
            leave_critical()


def seal() -> bool:
    """Seal this thread's lever (the commit point). True: go on, nothing can cancel the rest.
    False: a cancel won. No flag: True. A plain `Event`: the pull wins if it is set."""
    flag = getattr(_T, "flag", None)
    if flag is None:
        return True
    if isinstance(flag, Lever):
        return flag.seal()
    return not flag.is_set()


@contextmanager
def commit(what: str) -> Iterator[None]:
    """A durable write on this thread's lever (see `Lever.commit`). No lever: a critical window
    only; a plain `Event` that is already set raises `Cancelled` at entry (no deferral)."""
    flag = getattr(_T, "flag", None)
    if isinstance(flag, Lever):
        with flag.commit(what):
            yield
        return
    if flag is not None and flag.is_set():
        raise Cancelled("cancelled")
    enter_critical()
    try:
        yield
    finally:
        leave_critical()


@contextmanager
def scope(flag: Optional[threading.Event]) -> Iterator[None]:
    """Arm `flag` as this thread's cancel flag for the duration (nests; restores the outer one)."""
    prev = getattr(_T, "flag", None)
    _T.flag = flag
    try:
        yield
    finally:
        _T.flag = prev


def current() -> Optional[threading.Event]:
    return getattr(_T, "flag", None)


def cancelled() -> bool:
    flag = getattr(_T, "flag", None)
    return flag is not None and flag.is_set()


def checkpoint() -> None:
    """Raise `Cancelled` when this thread's work was cancelled; otherwise return at once."""
    flag = getattr(_T, "flag", None)
    if flag is not None and flag.is_set():
        raise Cancelled("cancelled")


@contextmanager
def reporting(fn: Optional[Callable[..., None]]) -> Iterator[None]:
    """Route this thread's `report(...)` calls to `fn` for the duration (a job's progress)."""
    prev = getattr(_T, "report", None)
    _T.report = fn
    try:
        yield
    finally:
        _T.report = prev


def current_reporter() -> Optional[Callable[..., None]]:
    """This thread's armed progress sink (None: no job) — so a caller can wrap a nested walk's
    reports in its own frame (0.0.27 P13 K5: an inner count must not overwrite an outer total)."""
    return getattr(_T, "report", None)


def report(**kw: Any) -> None:
    """Progress from inside a walk: `done`, `total`, `unit`, `note` (any subset). A no-op unless a
    job armed `reporting(...)` on this thread."""
    fn = getattr(_T, "report", None)
    if fn is not None:
        fn(**kw)


@contextmanager
def kill_on_cancel(proc: Any, poll: float = 0.1) -> Iterator[None]:
    """While the block runs, a watcher kills `proc` the moment this thread's flag is set — a
    blocking read on its pipe then ends (EOF) and the caller's next checkpoint raises. No flag
    armed (the CLI): no watcher."""
    flag = current()
    if flag is None:
        yield
        return
    done = threading.Event()

    def watch() -> None:
        while not done.is_set():
            if flag.wait(poll):
                if not done.is_set():
                    try:
                        proc.kill()
                    except (OSError, AttributeError):
                        pass
                return
    t = threading.Thread(target=watch, daemon=True, name="mokata-cancel-watch")
    t.start()
    try:
        yield
    finally:
        done.set()


__all__ = ["Cancelled", "Lever", "commit", "critical_depth", "enter_critical", "leave_critical",
           "seal", "scope", "current", "cancelled", "checkpoint", "reporting", "report",
           "kill_on_cancel"]
