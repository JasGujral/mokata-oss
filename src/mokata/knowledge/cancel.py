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
from typing import Any, Callable, Iterator, Optional

from ..errors import ControlSignal

_T = threading.local()


class Cancelled(ControlSignal):
    """The work this thread is doing was cancelled (its job's Cancel, or its last viewer left).
    A control signal, not a failure: it unwinds to the job runner that armed the flag."""


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


__all__ = ["Cancelled", "scope", "current", "cancelled", "checkpoint", "reporting", "report",
           "kill_on_cancel"]
