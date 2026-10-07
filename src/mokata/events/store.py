"""R1.S1a — the append-only event store, and the API over it.

⛔ **WHERE IT LIVES IS A DELIBERATE DEVIATION FROM doc 42, WHICH SAYS `.mokata/events.db`.** That
spec was written before stage 24D split the surface: in a user's repo `.mokata/` is the
COMMITTED area (manifest, constitution) and `.mokata/temp_local/` is the gitignored runtime
area, where the memory SQLite store and the audit ledger already live. A database at the path
doc 42 names would be committed to the user's repository on their next `git add .`. The store
is `temp_local/events/events.db`, and this paragraph is the record of why the spec was not
followed to the letter.

**APPEND-ONLY BY CONSTRUCTION, AND THE CLAIM IS BOUNDED.** This module has one writer, `emit`,
and it only ever INSERTs: there is no update and no delete anywhere in the events package, and
`test_a39` derives that from the module's own AST rather than taking this sentence for it. ⚠
**That is a claim about mokata, not about SQLite** — anyone holding the file can rewrite it, and
pretending otherwise would be the false-green class (§7e). The honest integrity story is doc
42's own: *the ledger stays canonical, events derive from it.* An event carrying a `ledger_seq`
is backed by the ledger's hash chain; an event without one is backed by nothing and says so.
A second hash chain here would be the redundant defence §7f asks to be separated or deleted.

**EVERY CONNECTION GOES THROUGH `memory._sqlite.connect_sqlite`, THE ONE FACTORY.** My first
version opened its own connection and set its own `journal_mode=WAL` / `synchronous=NORMAL`,
and `test_ms_s4_sqlite_wal` convicted it twice — once for the bare connect call, once for the
private pragma path. ⭐ It was right, and not merely procedurally: the factory already applies
the correctness pragmas, already bounds the busy wait, and already emits a LOUD once-per-process
notice when SQLite DECLINES WAL and silently leaves the database on another journal mode. A
second connect path would have been a second WAL policy that degrades QUIETLY — §7f's two
redundant defences, with the weaker one shipping.

⚠ **DEGRADE-CLEAN, LIKE EVERY OBSERVABILITY SURFACE IN THIS TREE.** `emit` never raises and
never blocks: a locked, full, read-only or corrupt database costs a row and nothing else. It
returns the event it wrote, or None — and a caller that cares can tell the difference, which is
the part `log_calibration` got right and worth copying rather than reinventing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from .schema import (
    ENVELOPE_KEYS,
    EVENT_SCHEMA_VERSION,
    EVENT_TYPES,
    Envelope,
    Payload,
    payload_for,
)

EVENTS_DIRNAME = "events"
EVENTS_FILENAME = "events.db"

# The manifest toggle. doc 42: `events.enabled` (default ON), local-only in every profile.
EVENTS_SETTINGS_KEY = "events"

# A query's default ceiling. A store that has run for months answers `query()` with a page, not
# a history — the same frugality `progress_events.DEFAULT_TAIL` applies to its own log.
DEFAULT_LIMIT = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT    NOT NULL UNIQUE,
    ts             TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    type           TEXT    NOT NULL,
    session_id     TEXT    NOT NULL,
    run_id         TEXT    NOT NULL DEFAULT '',
    actor          TEXT    NOT NULL DEFAULT '',
    duration_ms    INTEGER,
    ledger_seq     INTEGER,
    data           TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, seq);
CREATE INDEX IF NOT EXISTS events_type    ON events (type, seq);
"""


def events_dir(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, EVENTS_DIRNAME)


def events_path(root: str) -> str:
    return os.path.join(events_dir(root), EVENTS_FILENAME)


# `events.enabled` per repo root, read ONCE per process. See `enabled_for_root`.
_ENABLED_CACHE: Dict[str, bool] = {}


def reset_enabled_cache() -> None:
    """Drop the per-root toggle cache (tests only — a live process's manifest does not move)."""
    _ENABLED_CACHE.clear()


def enabled_for_root(root: str) -> bool:
    """`events.enabled` for a repo root, CACHED for the process.

    ⛔ **THE CACHE IS NOT AN OPTIMISATION, IT IS A CORRECTION.** The ledger projection runs on
    every `AuditLedger.record`, a ledger is constructed per call site, and the first version read
    the toggle by building a `Surface` each time — so one MCP tool call that recorded three
    ledger entries loaded the manifest three extra times. `test_mcp_surf_single_load` caught it,
    which is exactly what that test is for: MCP-SURF made `Surface.state` a `cached_property`
    for this reason, and an observability projection must not be the thing that undoes it.

    Keyed by root, so two repos in one process keep their own answers. Doubt resolves to ON — an
    unreadable toggle must never SILENTLY disable an audit surface (§7e)."""
    cached = _ENABLED_CACHE.get(root)
    if cached is not None:
        return cached
    # ⭐ THE ONE PLACE DOUBT RESOLVES. `on` starts ON and the except does NOT re-assign it:
    # an earlier draft wrote `on = True` in the handler too, and the mutant that changed THAT to
    # False survived the suite — the initializer had already decided, so the handler's copy was
    # unreachable text wearing the §7e guarantee (§7f).
    on = True
    try:
        from ..config import Surface
        if Surface.is_initialized(root):
            on = enabled(Surface.load(root))
    except Exception:  # noqa: BLE001 — see the register entry; `on` keeps the ON set above
        pass
    _ENABLED_CACHE[root] = on
    return on


def enabled(surface: Any) -> bool:
    """doc 42: `events.enabled`, default ON. A surface with no manifest reads as enabled, since
    the default is on and an unreadable toggle must not silently disable an audit surface."""
    try:
        cfg = surface.manifest.setting(EVENTS_SETTINGS_KEY, {}) or {}
    except Exception:  # noqa: BLE001 — see the register entry; a toggle read never breaks a caller
        return True
    if not isinstance(cfg, dict):
        return True
    return bool(cfg.get("enabled", True))


@dataclass(frozen=True)
class StoredEvent:
    """One row, read back. `data` is the typed payload's fields as a dict."""
    seq: int
    event_id: str
    ts: str
    schema_version: int
    type: str
    session_id: str
    run_id: str
    actor: str
    duration_ms: Optional[int]
    ledger_seq: Optional[int]
    data: Dict[str, Any]

    @property
    def ledger_backed(self) -> bool:
        """Whether this event's integrity rests on the ledger's hash chain. The distinction the
        module docstring makes, as a property a consumer can branch on."""
        return self.ledger_seq is not None


class EventStore:
    """The append-only store. One writer (`emit`), one reader (`query`), no other verbs."""

    def __init__(self, path: str) -> None:
        self.path = path

    # -------------------------------------------------------------------------------- internals

    def _connect(self) -> Any:
        """Open the store through the ONE factory and ensure its schema (see the module note)."""
        from ..memory._sqlite import connect_sqlite
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        conn = connect_sqlite(self.path)
        conn.executescript(_SCHEMA)
        return conn

    def _read_connect(self) -> Any:
        """A reader's connection — same factory. A read that opened its own would be the second
        connect path this module was just convicted for having."""
        from ..memory._sqlite import connect_sqlite
        return connect_sqlite(self.path)

    # ------------------------------------------------------------------------------------ write

    def emit(self, payload: Payload, *, session_id: str, run_id: str = "", actor: str = "",
             duration_ms: Optional[int] = None, ledger_seq: Optional[int] = None,
             envelope: Optional[Envelope] = None) -> Optional[StoredEvent]:
        """Append ONE typed event. Returns it, or None when nothing was stored.

        Never raises: this is the observability lane, and a locked or unwritable database costs
        a row. Returning None rather than swallowing silently is what lets `test_a39` assert
        the degrade instead of inferring it from an empty table."""
        try:
            event_type = getattr(payload, "TYPE", "")
            if event_type not in EVENT_TYPES:
                return None                      # the vocabulary is closed, by construction
            env = envelope or Envelope(
                type=event_type, session_id=session_id, run_id=run_id, actor=actor,
                duration_ms=duration_ms, ledger_seq=ledger_seq)
            row = env.to_row()
            data = json.dumps(payload.to_data(), sort_keys=True)
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO events (event_id, ts, schema_version, type, session_id, "
                        "run_id, actor, duration_ms, ledger_seq, data) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (row["event_id"], row["ts"], row["schema_version"], row["type"],
                         row["session_id"], row["run_id"], row["actor"], row["duration_ms"],
                         row["ledger_seq"], data))
                    seq = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            finally:
                conn.close()
            return StoredEvent(seq=seq, data=json.loads(data),
                               **{k: row[k] for k in ENVELOPE_KEYS})
        except Exception:  # noqa: BLE001 — the observability lane never fails its caller
            return None

    # ------------------------------------------------------------------------------------- read

    def after_seq(self, seq: int, *, limit: int = DEFAULT_LIMIT) -> List["StoredEvent"]:
        """The OLDEST-FIRST tail strictly after `seq`. Never raises; an absent store is empty.

        ⭐ **A SEPARATE METHOD RATHER THAN A FLAG ON `query`, AND THE ORDERING IS THE REASON.**
        `query` returns the NEWEST `limit` rows (`ORDER BY seq DESC`, reversed for display), which
        is right for "show me what just happened" and wrong for every resume: a publisher with a
        backlog larger than `limit` would send the newest slice, record its high-water mark, and
        **never come back for the middle.** A flag that silently inverts a method's ordering is
        how that bug ships looking like a one-line change, so the two contracts are two methods.

        ⚠ And it keys on `seq`, not on `ts`, because `ts` is second-resolution ISO text: two
        events in the same second cannot be ordered by it, so a `ts`-based resume must either
        re-send or skip, and both are wrong. `seq` is the store's own monotonic append index."""
        try:
            if not os.path.exists(self.path):
                return []
            conn = self._read_connect()
            try:
                rows = conn.execute(
                    "SELECT seq, event_id, ts, schema_version, type, session_id, run_id, actor, "
                    "duration_ms, ledger_seq, data FROM events WHERE seq > ? "
                    "ORDER BY seq ASC LIMIT ?", (int(seq), max(1, int(limit)))).fetchall()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — a read-only surface never raises into a caller
            return []
        return [self._row_to_event(r) for r in rows]

    def query(self, *, session_id: Optional[str] = None, types: Optional[Iterable[str]] = None,
              since: Optional[str] = None, limit: int = DEFAULT_LIMIT) -> List[StoredEvent]:
        """Events in seq order, newest LAST. Never raises; an absent store is an empty list.

        ⚠ `session_id` is the clean-resume rule's enforcement point (doc 42, Jas 2026-07-03):
        a query anchored to a session returns THAT session's events and no other's, so a
        resumed session cannot read a prior one's history even when both are in the same file."""
        try:
            if not os.path.exists(self.path):
                return []
            where: List[str] = []
            args: List[Any] = []
            if session_id is not None:
                where.append("session_id = ?")
                args.append(session_id)
            tl = [t for t in (types or ()) if t in EVENT_TYPES]
            if tl:
                where.append("type IN (%s)" % ",".join("?" * len(tl)))
                args.extend(tl)
            if since:
                where.append("ts >= ?")
                args.append(since)
            sql = "SELECT seq, event_id, ts, schema_version, type, session_id, run_id, actor, " \
                  "duration_ms, ledger_seq, data FROM events"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY seq DESC LIMIT ?"
            args.append(max(1, int(limit)))
            conn = self._read_connect()
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — a read-only surface never raises into a caller
            return []
        return [self._row_to_event(r) for r in reversed(rows)]

    @staticmethod
    def _row_to_event(r: Any) -> "StoredEvent":
        """ONE place turns a row into a `StoredEvent`. Both readers go through it, so a column
        added to the SELECTs cannot be wired up in one reader and forgotten in the other."""
        try:
            data = json.loads(r[10])
        except (TypeError, ValueError):
            data = {}
        return StoredEvent(seq=r[0], event_id=r[1], ts=r[2], schema_version=r[3],
                           type=r[4], session_id=r[5], run_id=r[6], actor=r[7],
                           duration_ms=r[8], ledger_seq=r[9],
                           data=data if isinstance(data, dict) else {})

    def max_seq(self) -> Optional[int]:
        """The newest append index. `0` for a store that is absent or empty, and **`None` when
        the store exists and could not be read at all.**

        ⭐ **THE THREE-WAY ANSWER IS THE WHOLE POINT (§7g).** `after_seq` and `count` below swallow
        every failure into `[]` / `0` — right for a read-only display surface, and wrong for the
        one caller that must tell *"nothing to publish"* from *"I cannot see your events"*: on a
        corrupt `events.db` those two render identically on the single surface a human checks, so
        a publisher reported `ok=True, nothing-new` over an unreadable store (CM.S5 review F7).
        The publisher reads THIS method and refuses on `None`.

        ⚠ Absent and empty share `0` deliberately: a store that was never written and a store
        with nothing in it are the same fact to a publisher — there is nothing to send — and
        inventing a third code for them would be a distinction with no consumer (§7f)."""
        try:
            if not os.path.exists(self.path):
                return 0
            conn = self._read_connect()
            try:
                row = conn.execute("SELECT MAX(seq) FROM events").fetchone()
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — reported as None, NEVER as "empty"
            return None
        if not row or row[0] is None:
            return 0
        return int(row[0])

    def count(self) -> int:
        try:
            if not os.path.exists(self.path):
                return 0
            conn = self._read_connect()
            try:
                return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — see `query`
            return 0


# ------------------------------------------------------------------------- the module-level API

def store_for_root(root: str) -> EventStore:
    return EventStore(events_path(root))


def emit(surface_or_root: Any, payload: Payload, *, session_id: Optional[str] = None,
         run_id: str = "", actor: str = "", duration_ms: Optional[int] = None,
         ledger_seq: Optional[int] = None) -> Optional[StoredEvent]:
    """Emit one event for a `Surface` or a repo root. Honours `events.enabled`; never raises.

    The session id DEFAULTS to this process's (`session.current_session_id`) rather than being
    minted here. ⭐ **That is the one design decision in this function and it is the dirty-set
    lesson:** `freshness` grew a second, dumber session resolution beside `run_resolver`'s
    ladder, the writer and the reader keyed on different namespaces, and the signal was dead for
    a release. There is one session identity in this tree and events use it."""
    try:
        root = getattr(surface_or_root, "root", None) or surface_or_root
        if not isinstance(root, str):
            return None

        # ⛔ **THE TOGGLE IS HONOURED FOR A ROOT STRING TOO, AND IT WAS NOT (review F1).** The
        # first version read `hasattr(surface_or_root, "manifest")`, which is False for a root
        # string — and **all six producers in this tree pass a root string.** So
        # `events.enabled: false` turned off nothing at all: the setting existed, `enabled()` was
        # correct, `enabled_for_root()` was correct and cached, and the ONE call site that decides
        # consulted neither. ⭐ A toggle nobody reads is worse than no toggle: it is a promise the
        # manifest makes on the product's behalf.
        if hasattr(surface_or_root, "manifest"):
            if not enabled(surface_or_root):
                return None
        elif not enabled_for_root(root):
            return None

        # ⛔ **AND NOTHING IS CREATED OUTSIDE A REPO (review F2).** `store_for_root` opens — and
        # therefore CREATES — `.mokata/temp_local/events/events.db` under whatever directory it is
        # handed, so an emit with a root that is not a mokata repo littered a database into a
        # user's tree. ⚠ This is a SEPARATE refusal from the toggle above and must stay separate:
        # `enabled_for_root` resolves an unreadable or absent manifest to ON by design (§7e — an
        # unreadable toggle must never silently disable an audit surface), so it can never be the
        # thing that answers *"is this even a repo?"*. Two questions, two answers (§7g).
        from ..config import Surface
        if not Surface.is_initialized(root):
            return None
        if session_id is None:
            from ..session import current_run_id, current_session_id
            session_id = current_session_id()
            run_id = run_id or current_run_id()
        return store_for_root(root).emit(payload, session_id=session_id, run_id=run_id,
                                         actor=actor, duration_ms=duration_ms,
                                         ledger_seq=ledger_seq)
    except Exception:  # noqa: BLE001 — the observability lane never fails its caller
        return None


def query(root: str, **kw: Any) -> List[StoredEvent]:
    """Query a repo's events. See `EventStore.query`; never raises."""
    return store_for_root(root).query(**kw)
