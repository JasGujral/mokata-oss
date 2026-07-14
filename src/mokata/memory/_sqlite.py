"""MS.S4 — the ONE SQLite connect path: WAL + a bounded busy_timeout (M-4).

The bug this closes — as GROUNDED, which is NOT quite as M-4 stated it. M-4's premise was that the
store ran with "no busy_timeout". That is false: Python's `sqlite3.connect(path)` takes `timeout=5.0`
and applies it as `busy_timeout=5000`, so there has always been a 5s bound — an IMPLICIT one, owned
by a stdlib default rather than by mokata.

The live bug is the JOURNAL MODE. SQLite's default (`delete`, the rollback journal) needs an
EXCLUSIVE lock to COMMIT, so a writer cannot commit while another process holds a read open: it
waits out its busy_timeout and then fails with exactly `sqlite3.OperationalError: database is
locked`. A LARGER busy_timeout does not fix that — it only makes the writer wait longer before
failing. WAL does: readers never block the writer. The local-first path (P8) failed at exactly the
point where "local" stopped meaning "one window" (P22).

This module is the SINGLE place a SQLite connection is opened (the twin of `_pg.connect_psycopg`),
so the pragmas can never drift or be forgotten at a new call site.

Two pragmas, both defended as CORRECTNESS — not perf tuning:
  * `journal_mode=WAL` — THE fix. Readers stop blocking the writer and the writer stops blocking
    readers, so concurrent windows read a consistent snapshot instead of colliding. WAL is a
    property of the DATABASE FILE (set once, persists across connections), so re-asserting it per
    connect is a cheap no-op after the first.
  * `busy_timeout` — NOT the fix, but ours to OWN. WAL still serializes WRITER-vs-WRITER, so a
    collision remains possible and needs a bounded wait. We set that bound EXPLICITLY rather than
    inheriting whatever `sqlite3.connect`'s `timeout` parameter happens to default to: a future
    stdlib change to that default must not be able to move mokata's behaviour silently. See
    `BUSY_TIMEOUT_MS`.

Deliberately NOT set: `synchronous=NORMAL`, WAL's usual companion, trades DURABILITY for speed —
it can lose the last committed transaction on power loss. That is perf tuning, and it weakens a
guarantee mokata's human-gated writes depend on (P2), so the default (FULL) stands. `foreign_keys`
is a no-op on this schema (no FKs). Two pragmas, no more.

Degrade-clean (P8, CM.S2 notice pattern): some filesystems — network mounts (NFS/SMB) that cannot
back WAL's shared-memory `-shm` index — cannot do WAL at all. SQLite says so either by returning a
journal mode that is NOT 'wal' or by raising. Either way the store KEEPS WORKING on its previous
journal mode, the busy_timeout STILL applies (it is journal-mode independent, so the wait stays
bounded and the store is still strictly better than before), and ONE loud notice per subsystem
says what happened. Never a hard failure.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Set, Tuple

from ..degrade import FAILURE_WAL, DegradeNotice, emit_degrade_notice

# The once-per-subsystem notice key (CM.S2 `emit_degrade_notice` keys on it), naming the LOCAL
# store — not "memory", which CM.S2 already owns for the TEAM degrade. A window that is both
# team-degraded and WAL-degraded is telling the user two different true things, and must be able
# to say both.
SUBSYSTEM = "memory-sqlite"

WAL = "wal"

# SQLite result codes for "someone else holds the lock right now" (transient), as distinct from a
# filesystem that cannot back WAL at all (permanent). See `_is_busy`.
_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6

# The bounded wait for a contended cross-process SQLite WRITE (WAL still serializes writer-vs-
# writer). Set EXPLICITLY: `sqlite3.connect` already applies its own `timeout=5.0` default as a
# 5000ms busy_timeout, so leaving this implicit means mokata's cross-process behaviour is decided
# by a stdlib default it does not control and would change silently if CPython ever moved it.
# Justification for 10s:
#   * BOUNDED, never infinite — a genuinely wedged holder still surfaces as an error the user can
#     see, rather than hanging the window forever. (An unbounded busy handler would trade
#     "database is locked" for "mokata hangs", which is not a fix.)
#   * It is the SAME bound `oslock.DEFAULT_TIMEOUT` (10.0s, MS.S1) already uses for cross-process
#     file-lock contention. Same class of wait → same number; one bound to reason about, not two.
#   * Enormous headroom over real contention: mokata's writes are single-row upserts on a tiny
#     store (sub-millisecond), so even a dozen concurrent windows serialize in well under a second.
#     10s is orders of magnitude above the worst realistic case — it is a backstop, not a budget.
# It is a BACKSTOP, not the fix: no bound lets a writer commit past a held reader on the rollback
# journal (a bigger one only waits longer). WAL is what removes that collision.
BUSY_TIMEOUT_MS = 10_000


@dataclass
class WalDegradeNotice(DegradeNotice):
    """A WAL-unavailable degrade, rendered in the CM.S2 shape (loud, once per subsystem, actionable).

    `env_name` is empty: this is a LOCAL filesystem-capability degrade, not a team/DSN one. And no
    PATH is ever interpolated — the notice names the subsystem and the fallback journal mode and
    nothing else, so it cannot leak a directory layout (secret-safety).
    """

    fallback_mode: str = "unknown"

    def render(self, *, ascii_only: bool = False) -> str:
        glyph = "[!]" if ascii_only else "⚠"
        return (f"{glyph} local {self.subsystem}: DEGRADED — this filesystem cannot enable SQLite "
                f"WAL, so the store stays on its previous journal mode ('{self.fallback_mode}'). "
                f"Concurrent windows still work — a contended write WAITS (bounded) instead of "
                f"failing — but readers and the writer now block each other. No data is at risk; "
                f"moving the store to a LOCAL (non-network) filesystem restores WAL.")


def _stderr(msg: str) -> None:
    """Notices go to STDERR, never stdout: the MCP server speaks JSON-RPC on stdout, and a stray
    print there would corrupt the protocol framing. (Same convention as `prompt.py`/`hook_cli.py`.)"""
    print(msg, file=sys.stderr)


def is_memory_path(path: str) -> bool:
    """True for an in-memory DB — private to its own connection, so it has no file, no sidecars,
    and nothing to share. SQLite reports its journal mode as 'memory' and DECLINES WAL; that is BY
    DESIGN, not a degrade, so the WAL path skips it silently (see `_apply_pragmas`)."""
    return path in (":memory:", "") or "mode=memory" in path


# The nap between retries of a BUSY-refused WAL switch. Short: the holder is a sub-millisecond
# single-row write, so the switch almost always lands on the first retry.
_WAL_RETRY_SLEEP_S = 0.01


def _is_busy(exc: sqlite3.Error) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED — a lock held by someone else RIGHT NOW (transient), as opposed
    to a filesystem that structurally cannot do WAL (permanent). `sqlite_errorcode` exists from
    3.11; on the 3.10 floor the driver only carries the message, hence the text fallback."""
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code in (_SQLITE_BUSY, _SQLITE_LOCKED)
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _current_mode(conn: Any) -> str:
    """The journal mode the DB is ACTUALLY on. A header READ, not a mode CHANGE — it needs no
    exclusive lock, so it cannot itself be refused for contention."""
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower() if row else ""
    except sqlite3.Error:
        return ""


def _switch_to_wal(conn: Any, *, busy_timeout_ms: int) -> Tuple[str, str]:
    """Put the DB into WAL. Returns (the mode we ended on, the failure detail if we did not).

    MS.S7 (two-process stress) found the hole. Putting a db into WAL needs a brief EXCLUSIVE lock,
    so the switch can be refused with SQLITE_BUSY — and the old code BELIEVED that refusal, treating
    it as the PERMANENT "this filesystem cannot do WAL" degrade. Two ways it is refused, both seen:

      * two windows opening ONE FRESH repo at the same moment both attempt the initial delete->wal
        switch, and the loser is refused IMMEDIATELY (this is what the stress hit);
      * a sibling holding a transaction makes the switch wait out `busy_timeout` and then fail.
        (`busy_timeout` IS honoured for this statement — measured. It is simply not enough.)

    Believing either is a LIE with consequences: the notice tells the user their filesystem cannot
    back WAL and advises them to MOVE THEIR STORE, when nothing is wrong and the db is in WAL
    anyway (the sibling put it there) — and, worse, the window that believed it stays on the
    rollback journal, which is the M-4 bug MS.S4 exists to kill.

    So: a BUSY refusal is retried within the same bound, and a sibling's successful switch is taken
    as the win it is. A refusal that is NOT busy (a mount with no -shm) still degrades immediately
    and loudly — unchanged.
    """
    deadline = time.monotonic() + (busy_timeout_ms / 1000.0)
    while True:
        try:
            row = conn.execute(f"PRAGMA journal_mode={WAL}").fetchone()
            return (str(row[0]).lower() if row else ""), ""
        except sqlite3.Error as exc:
            if not _is_busy(exc):
                return "unknown", str(exc)      # a REAL refusal — degrade now, do not wait it out
            if _current_mode(conn) == WAL:
                return WAL, ""                  # a sibling window won the switch: that IS the fix
            if time.monotonic() >= deadline:
                return _current_mode(conn) or "unknown", str(exc)
            time.sleep(_WAL_RETRY_SLEEP_S)


def _apply_pragmas(conn: Any, path: str, *, busy_timeout_ms: int,
                   out: Optional[Callable[[str], None]],
                   seen: Optional[Set[str]]) -> None:
    """Set the two correctness pragmas on `conn`. Degrade-clean: WAL refusal never raises."""
    # busy_timeout FIRST, and unconditionally (it is journal-mode independent, so it is the one
    # pragma that helps even when WAL is refused). It also covers the `journal_mode=WAL` statement
    # below — but only against a HELD transaction, not against a sibling racing the same switch,
    # which is refused outright. `_switch_to_wal` owns that gap (MS.S7).
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")

    if is_memory_path(path):
        return  # nothing to share, nothing to degrade.

    mode, detail = _switch_to_wal(conn, busy_timeout_ms=busy_timeout_ms)
    if mode != WAL:
        # SQLite declined and left the DB on its previous mode (e.g. 'delete'). It still works.
        _emit(_notice(mode or "unknown",
                      detail or f"journal_mode returned '{mode or 'unknown'}'"), out, seen)


def _notice(fallback_mode: str, detail: str) -> WalDegradeNotice:
    return WalDegradeNotice(subsystem=SUBSYSTEM, env_name="", failure_class=FAILURE_WAL,
                            detail=detail, fallback_mode=fallback_mode)


def _emit(notice: WalDegradeNotice, out: Optional[Callable[[str], None]],
          seen: Optional[Set[str]]) -> None:
    """Print LOUDLY, but exactly ONCE per subsystem per process — CM.S2's `emit_degrade_notice`
    machinery, unforked, so a per-operation connect can't turn one bad mount into a spam loop."""
    emit_degrade_notice(notice, out or _stderr, seen=seen)


def connect_sqlite(path: str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS,
                   out: Optional[Callable[[str], None]] = None,
                   seen: Optional[Set[str]] = None) -> sqlite3.Connection:
    """Open a SQLite connection to `path` with the correctness pragmas applied. THE ONE connect
    path — every `sqlite3.connect` in `src/` goes through here (pinned by a grep-guard test).

    `busy_timeout_ms`/`out`/`seen` are injectable for tests; the live defaults are the bounded
    10s wait and a once-per-process notice on stderr."""
    conn = sqlite3.connect(path)
    _apply_pragmas(conn, path, busy_timeout_ms=busy_timeout_ms, out=out, seen=seen)
    return conn
