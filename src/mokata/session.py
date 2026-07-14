"""MS.S2 — the minted per-session identity (M-2).

Two Claude Code windows on one repo are TWO MCP processes, invisible to each other and to bare
Claude Code itself. Before this stage nothing distinguished them: the pipeline state keys were
repo-global singletons (window B's brainstorm clobbered window A's approved approach — MS.S1 made
that clobber atomic; MS.S2 makes it not happen), and `run_id` was READ everywhere
(`find_active_run`/`list_runs`) yet had NO generator, so the two runs couldn't even be told apart.

This module is that missing identity. It mints ONE `session_id` per process — lazily, at the
session's natural start (the first subsystem that needs it; for the MCP server that is the first
tool call after the SessionStart bootstrap, for the CLI it is the command that touches state), and
STABLE for the whole process lifetime. The id is a `uuid4` (pid alone is NOT enough — pids are
reused across restarts), paired with a monotonic start stamp so the registry can order and age
sessions. All subsystems reach it through the one accessor here.

session_id ↔ run_id — the explicit relationship
------------------------------------------------
A `run_id` names a single pipeline run (brainstorm→…→ship); a `session_id` names a window/process.
In today's single-run model a window drives ONE active pipeline run at a time, so:

    run_id == session_id.

`current_run_id()` is therefore the generator that was missing: it MINTS a run_id where none
existed (by returning the session's id), it is STABLE within the process, and it is DISTINCT across
processes. Downstream, this makes the per-run checkpoint key (`pipeline_run__<run_id>`) session-
scoped for free — no separate scoping needed for checkpoints. When a future stage lets one window
hold several runs at once, this accessor is the single seam to change (a run_id would become the
session_id plus a per-run suffix); nothing else reads the relationship directly.

Stdlib-only (uuid/os/time/threading) — identity must never pull an external dependency. Clean-room.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# A parent may PIN the id (so a wrapper can make several processes share one session, and so tests
# are deterministic). Empty/unset -> a fresh uuid4 is minted. Read once, at mint time.
SESSION_ID_ENV = "MOKATA_SESSION_ID"


@dataclass(frozen=True)
class Session:
    """The identity of ONE mokata process/window, minted once and then immutable.

    `started_monotonic` comes from `time.monotonic()` (a system-wide, boot-relative clock) so the
    registry can order sessions and reason about staleness without trusting wall-clock skew;
    `started_at` is the human-facing ISO wall-clock stamp shown in `mokata windows`."""

    session_id: str
    started_at: str            # ISO-8601 UTC, for display
    started_monotonic: float   # monotonic start, for ordering/aging
    pid: int


_current: Optional[Session] = None
_lock = threading.Lock()       # a window can serve tool calls from more than one thread


def _mint() -> Session:
    pinned = os.environ.get(SESSION_ID_ENV, "").strip()
    sid = pinned or uuid.uuid4().hex
    return Session(
        session_id=sid,
        started_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        started_monotonic=time.monotonic(),
        pid=os.getpid(),
    )


def current_session() -> Session:
    """This process's session, minted lazily on first use and stable thereafter (double-checked
    under a lock so concurrent tool-call threads share the SAME identity)."""
    global _current
    if _current is None:
        with _lock:
            if _current is None:
                _current = _mint()
    return _current


def current_session_id() -> str:
    """The stable `session_id` for this process (see `current_session`)."""
    return current_session().session_id


def current_run_id() -> str:
    """The run_id for this session's current pipeline run. Minted where absent; stable within the
    session; distinct across sessions. Equals the session_id — see the module docstring for the
    session_id↔run_id relationship."""
    return current_session_id()


def short_id(session_id: str, n: int = 8) -> str:
    """A short, human-scannable prefix of a session/run id (the full id stays the key)."""
    return session_id[:n]


def reset_for_test() -> None:
    """Drop the cached identity so the next accessor re-mints — the process-restart equivalent for
    tests. NOT for production use (a live session's identity is immutable by design)."""
    global _current
    with _lock:
        _current = None


def set_for_test(session: Session) -> None:
    """Force a specific identity (tests only)."""
    global _current
    with _lock:
        _current = session
