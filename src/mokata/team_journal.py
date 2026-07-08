"""TM.S5 — ONE write path: journal always, flush when healthy (doc 48 E3 + C1 + C5).

Every durable TEAM write lands in a crash-safe LOCAL journal FIRST, then flushes to Postgres in
batches. Healthy and offline use the SAME path — offline just means the flush waits. This is the
opposite of the old silent fallback (doc 48 finding 3): nothing diverges silently, nothing is
lost, and a broken connection degrades to an EXPLICIT, journaled work-locally state that
`mokata sync` reconciles later.

Three correctness guarantees ride the flush:
  * **CAS (doc 48 C1):** each memory write carries the `revision` it was based on; the flush is a
    compare-and-set. A lost-update (a concurrent writer advanced the row) SURFACES as a conflict —
    never a silent last-writer-wins. Conflicts are resolved through the human gate in `mokata sync`.
  * **Approval inheritance (doc 48 C5 / P2):** each journal entry records the LEDGER ID of the
    human approval that authorised it; the flush re-records that id, so deferred durability is
    never deferred consent — the flush inherits the original gate decision, never bypasses it.
  * **Per-publish secret-scan (P2):** every payload is scanned again at flush time; a secret is a
    hard block (that entry is not published), independent of the earlier approval.

Local mode is untouched: `record_team_write` is a no-op guard in local mode (there is no team
write path), so zero-config stays byte-for-byte the default. The journal file is append-only
JSONL (crash-safe: state is replayed, never rewritten in place).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import TEMP_LOCAL_DIRNAME, run_mode as _rm, teamdb

JOURNAL_FILENAME = "team_journal.jsonl"

# The durable memory ops a journal entry can carry (TM.S5c). Every gated store method passes one so
# the flush picks the right compare-and-set: `put` = a believed-new INSERT-or-conflict, `update` =
# a revision-guarded UPDATE, `delete` = a revision-guarded DELETE (a PRUNE never hard-deletes a
# shared row — a concurrent change SURFACES as a conflict instead).
OP_PUT = "memory_put"
OP_UPDATE = "memory_update"
OP_DELETE = "memory_delete"

# Entry lifecycle (append-only markers, replayed to a status):
_PENDING = "pending"       # waiting to flush
_FLUSHED = "flushed"       # committed to Postgres (done)
_CONFLICT = "conflict"     # CAS lost-update — needs a human decision in `sync`
_BLOCKED = "blocked"       # a secret was found at publish time — needs the secret removed
_DROPPED = "dropped"       # a `kept-remote` resolution discarded the local write


# --------------------------------------------------------------------------- data types
@dataclass
class JournalEntry:
    """One durable team write, buffered locally. `ledger_id` is the id of the human approval
    that authorised it (C5). `base_revision` is the revision the write was based on (None = a
    believed-new row); the flush CAS uses it. `source` is `write` (normal) or `recovery`
    (a row rescued from the SQLite floor — doc 48 finding 3)."""

    id: str
    op: str
    table: str
    key: str
    payload: Dict[str, Any]
    ledger_id: Any = None
    project: Optional[str] = None
    actor: str = "user"
    base_revision: Optional[int] = None
    source: str = "write"


@dataclass
class ConflictView:
    """A surfaced CAS conflict awaiting a human decision in `mokata sync`."""

    id: str
    key: str
    entry: JournalEntry
    detail: str
    remote: Optional[Dict[str, Any]] = None


@dataclass
class ApplyOutcome:
    status: str                      # "ok" | "conflict"
    new_revision: Optional[int] = None
    detail: str = ""
    remote: Optional[Dict[str, Any]] = None


@dataclass
class FlushResult:
    """The verdict of one flush pass. `skipped` = the connection wasn't healthy (work-locally;
    nothing pushed, nothing lost). Never raises — a flush is best-effort by construction."""

    flushed: int = 0
    conflicts: int = 0
    blocked: int = 0
    pending: int = 0
    skipped: bool = False
    reason: str = ""
    verdict: Any = None


@dataclass
class SyncResult:
    """The verdict of `mokata sync` (manual flush + reconcile). Conflicts are surfaced and
    resolved through the human gate — a conflict left un-decided stays `deferred` (still
    conflicted), never silently last-writer-wins."""

    recovered: int = 0
    flushed: int = 0
    conflicts_found: int = 0
    resolved_local: int = 0
    resolved_remote: int = 0
    deferred: int = 0
    blocked: int = 0
    pending: int = 0
    skipped: bool = False
    reason: str = ""
    verdict: Any = None


# --------------------------------------------------------------------------- the journal
class TeamJournal:
    """The append-only local write journal for team mode. State is REPLAYED from the log (never
    rewritten in place), so a crash mid-flush loses nothing: an un-acked write simply replays as
    still-pending on the next run."""

    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @classmethod
    def for_surface(cls, surface: Any) -> "TeamJournal":
        return cls(os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, JOURNAL_FILENAME))

    # --- append-only writers -------------------------------------------------
    def _append(self, rec: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())        # crash-safe: the write is durable before we return
            except Exception:  # pragma: no cover - fsync can be unsupported on some FS
                pass

    def append(self, entry: JournalEntry) -> JournalEntry:
        self._append({"kind": "write", "id": entry.id, "op": entry.op, "table": entry.table,
                      "key": entry.key, "payload": entry.payload, "ledger_id": entry.ledger_id,
                      "project": entry.project, "actor": entry.actor,
                      "base_revision": entry.base_revision, "source": entry.source})
        return entry

    def mark_flushed(self, entry_id: str, *, remote_revision: Optional[int] = None) -> None:
        self._append({"kind": _FLUSHED, "id": entry_id, "remote_revision": remote_revision})

    def mark_conflict(self, entry_id: str, *, detail: str,
                      remote: Optional[Dict[str, Any]] = None) -> None:
        self._append({"kind": _CONFLICT, "id": entry_id, "detail": detail, "remote": remote})

    def mark_blocked(self, entry_id: str, *, detail: str) -> None:
        self._append({"kind": _BLOCKED, "id": entry_id, "detail": detail})

    def resolve(self, entry_id: str, resolution: str, *,
                remote_revision: Optional[int] = None) -> None:
        """Human decision on a conflict (P2). `kept-local` re-queues the local write at the
        CURRENT remote revision (so a re-flush CAS overwrites remote — an explicit choice);
        `kept-remote` drops the local write."""
        self._append({"kind": "resolved", "id": entry_id, "resolution": resolution,
                      "remote_revision": remote_revision})

    # --- replay --------------------------------------------------------------
    def _records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:  # pragma: no cover - skip a torn last line
                        pass
        return out

    def _replay(self):
        entries: Dict[str, JournalEntry] = {}
        status: Dict[str, str] = {}
        conflicts: Dict[str, ConflictView] = {}
        order: List[str] = []
        for rec in self._records():
            k, rid = rec.get("kind"), rec.get("id")
            if not rid:
                continue
            if k == "write":
                entries[rid] = JournalEntry(
                    id=rid, op=rec.get("op", ""), table=rec.get("table", ""),
                    key=rec.get("key", ""), payload=rec.get("payload", {}),
                    ledger_id=rec.get("ledger_id"), project=rec.get("project"),
                    actor=rec.get("actor", "user"), base_revision=rec.get("base_revision"),
                    source=rec.get("source", "write"))
                status[rid] = _PENDING
                if rid not in order:
                    order.append(rid)
            elif k == _FLUSHED:
                status[rid] = _FLUSHED
                conflicts.pop(rid, None)
            elif k == _CONFLICT:
                status[rid] = _CONFLICT
                if rid in entries:
                    conflicts[rid] = ConflictView(rid, entries[rid].key, entries[rid],
                                                  rec.get("detail", ""), rec.get("remote"))
            elif k == _BLOCKED:
                status[rid] = _BLOCKED
                conflicts.pop(rid, None)
            elif k == "resolved":
                if rec.get("resolution") == "kept-local":
                    status[rid] = _PENDING
                    if rid in entries:
                        entries[rid].base_revision = rec.get("remote_revision")
                    conflicts.pop(rid, None)
                else:                                   # kept-remote → discard the local write
                    status[rid] = _DROPPED
                    conflicts.pop(rid, None)
        return entries, status, conflicts, order

    def pending(self) -> List[JournalEntry]:
        entries, status, _c, order = self._replay()
        return [entries[i] for i in order if status.get(i) == _PENDING]

    def conflicts(self) -> List[ConflictView]:
        _e, status, conflicts, order = self._replay()
        return [conflicts[i] for i in order if status.get(i) == _CONFLICT and i in conflicts]

    def blocked(self) -> List[JournalEntry]:
        entries, status, _c, order = self._replay()
        return [entries[i] for i in order if status.get(i) == _BLOCKED]

    def has_pending_key(self, key: str) -> bool:
        return any(e.key == key for e in self.pending())


# --------------------------------------------------------------------------- record (entry)
def record_team_write(surface: Any, *, op: str, table: str, key: str,
                      payload: Dict[str, Any], ledger_id: Any, project: Optional[str] = None,
                      actor: str = "user", base_revision: Optional[int] = None,
                      source: str = "write") -> Optional[JournalEntry]:
    """The ONE entry point every durable team write calls. Journals the write locally (crash-
    safe) and returns the entry. In LOCAL mode it is a no-op guard (returns None) — there is no
    team write path, so zero-config is completely unaffected."""
    if _rm.read_mode(surface) != _rm.TEAM:
        return None
    entry = JournalEntry(id=uuid.uuid4().hex, op=op, table=table, key=key, payload=payload,
                         ledger_id=ledger_id, project=project, actor=actor,
                         base_revision=base_revision, source=source)
    return TeamJournal.for_surface(surface).append(entry)


# --------------------------------------------------------------------------- CAS apply
_INSERT_SQL = (
    f"INSERT INTO {teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project, "  # nosec B608
    f"{teamdb.MEMORY_REVISION_COLUMN}, {teamdb.MEMORY_UPDATED_AT_COLUMN}) "
    "VALUES (%s, %s, %s, %s, %s, %s, 1, now()) ON CONFLICT (id) DO NOTHING"
)
_UPDATE_SQL = (
    f"UPDATE {teamdb.MEMORY_TABLE} SET mtype=%s, subject=%s, status=%s, doc=%s, project=%s, "  # nosec B608
    f"{teamdb.MEMORY_REVISION_COLUMN}={teamdb.MEMORY_REVISION_COLUMN}+1, "
    f"{teamdb.MEMORY_UPDATED_AT_COLUMN}=now() "
    f"WHERE id=%s AND {teamdb.MEMORY_REVISION_COLUMN}=%s"
)
_SELECT_SQL = (
    f"SELECT doc, {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE} WHERE id=%s"  # nosec B608
)
_DELETE_SQL = (
    f"DELETE FROM {teamdb.MEMORY_TABLE} "  # nosec B608
    f"WHERE id=%s AND {teamdb.MEMORY_REVISION_COLUMN}=%s"
)


def _read_remote(conn: Any, key: str) -> Optional[Dict[str, Any]]:
    try:
        row = conn.execute(_SELECT_SQL, (key,)).fetchone()
    except Exception:  # pragma: no cover - defensive
        return None
    if not row:
        return None
    return {"doc": row[0], "revision": row[1]}


def apply_memory_write(conn: Any, entry: JournalEntry) -> ApplyOutcome:
    """Compare-and-set a memory write against the shared table (doc 48 C1). A believed-new row
    is an INSERT ... ON CONFLICT DO NOTHING; an update is a revision-guarded UPDATE; a delete
    (TM.S5c — the gated PRUNE path) is a revision-guarded DELETE. Either way, a row that doesn't
    match (already created / revision advanced / already removed) is a CONFLICT — never a silent
    overwrite and never a silent hard-delete of a shared row."""
    p = entry.payload
    cols = (p.get("mtype"), p.get("subject"), p.get("status"), p.get("doc"), p.get("project"))
    if entry.op == OP_DELETE:
        # TM.S5c — a PRUNE in team mode. Revision-guarded so a concurrent writer's change is NOT
        # silently destroyed: the delete only lands if the row is still at the base revision.
        if entry.base_revision is None:
            # No known base (an item read off a non-revision backend). If it isn't remote there is
            # nothing to lose (ok); if it IS, we can't safely CAS-delete → surface a conflict.
            remote = _read_remote(conn, entry.key)
            if remote is None:
                return ApplyOutcome("ok")
            return ApplyOutcome("conflict",
                                detail="cannot delete without a known base revision — the row "
                                       "exists remotely (a concurrent state)", remote=remote)
        cur = conn.execute(_DELETE_SQL, (p.get("id"), entry.base_revision))
        if (getattr(cur, "rowcount", 0) or 0) > 0:
            return ApplyOutcome("ok")
        return ApplyOutcome("conflict",
                            detail=(f"remote revision advanced past base {entry.base_revision} "
                                    "(the row changed or was already removed) — a concurrent "
                                    "writer touched this row"),
                            remote=_read_remote(conn, entry.key))
    if entry.base_revision is None:
        cur = conn.execute(_INSERT_SQL, (p.get("id"), *cols))
        if (getattr(cur, "rowcount", 0) or 0) > 0:
            return ApplyOutcome("ok", new_revision=1)
        return ApplyOutcome("conflict",
                            detail="a row with this id already exists remotely (concurrent create)",
                            remote=_read_remote(conn, entry.key))
    cur = conn.execute(_UPDATE_SQL, (*cols, p.get("id"), entry.base_revision))
    if (getattr(cur, "rowcount", 0) or 0) > 0:
        return ApplyOutcome("ok", new_revision=int(entry.base_revision) + 1)
    return ApplyOutcome("conflict",
                        detail=(f"remote revision advanced past base {entry.base_revision} "
                                "(lost update) — a concurrent writer changed this row"),
                        remote=_read_remote(conn, entry.key))


# --------------------------------------------------------------------------- flush
class _JournalUnavailable(Exception):
    pass


def _default_connect(surface: Any, environ: Optional[dict]) -> Any:
    env = os.environ if environ is None else environ
    dsn = (env.get(_rm.CREDENTIAL_ENV) or "").strip()
    if not dsn:
        return None
    from .memory._pg import get_connection
    try:
        return get_connection(dsn, _JournalUnavailable)
    except Exception:
        return None


def _default_scan(entry: JournalEntry) -> list:
    """Per-publish secret-scan of the payload (P2). Egress-strength (`for_send`) — a durable
    shared write leaves this machine, so it is held to the outbound bar."""
    from .govern.secrets import scan
    return scan(text=json.dumps(entry.payload), path=entry.key, for_send=True)


def flush(surface: Any, *, environ: Optional[dict] = None, health: Any = None,
          probe: Optional[Callable[[str], Any]] = None,
          connect: Optional[Callable[..., Any]] = None, ledger: Any = None,
          scan: Optional[Callable[[JournalEntry], list]] = None,
          out: Optional[Callable[[str], None]] = None) -> FlushResult:
    """Flush the journal to Postgres in one batch — the shared primitive `mokata sync` drives.

    NEVER blocks and NEVER raises: an unhealthy connection returns `skipped` immediately (the
    journal stays pending — explicit work-locally, nothing lost). When healthy, each pending
    entry is secret-scanned (a secret hard-blocks that entry), then applied via CAS; a success
    marks it flushed AND records the inherited approval ledger id (C5), a lost-update marks it
    conflicted for the human gate. `health`/`connect`/`scan`/`ledger` are injectable for tests."""
    from . import team_health
    journal = TeamJournal.for_surface(surface)
    pend = journal.pending()
    verdict = health if health is not None else team_health.check(
        surface, environ=environ, probe=probe)

    if not pend:
        return FlushResult(flushed=0, pending=0, skipped=False, verdict=verdict,
                           reason="nothing to flush")

    if not getattr(verdict, "ok", False):
        return FlushResult(flushed=0, pending=len(pend), skipped=True, verdict=verdict,
                           reason=("connection not healthy — journaled locally; "
                                   "run `mokata sync` when reconnected (work-locally, nothing lost)"))

    conn = (connect or _default_connect)(surface, environ)
    if conn is None:
        return FlushResult(flushed=0, pending=len(pend), skipped=True, verdict=verdict,
                           reason="no reachable connection to flush to (driver/DSN unavailable)")

    do_scan = scan or _default_scan
    flushed = conflicts = blocked = 0
    for entry in pend:
        findings = do_scan(entry)
        if findings:
            journal.mark_blocked(entry.id,
                                 detail="blocked: secret detected in the payload — NOT published")
            blocked += 1
            if out:
                out(f"⚠ blocked publish of {entry.key}: secret detected (remove it, then re-sync)")
            continue
        outcome = apply_memory_write(conn, entry)
        if outcome.status == "ok":
            journal.mark_flushed(entry.id, remote_revision=outcome.new_revision)
            if ledger is not None:
                # C5 / P2 — the flush INHERITS the original approval; record its ledger id so the
                # audit trail links deferred durability back to the human decision (no bypass).
                ledger.record("team_flush", journal_id=entry.id, table=entry.table,
                              key=entry.key, actor=entry.actor,
                              approval_ledger_id=entry.ledger_id, revision=outcome.new_revision,
                              reason="flush inherits the original human approval (P2)")
            flushed += 1
        else:
            journal.mark_conflict(entry.id, detail=outcome.detail, remote=outcome.remote)
            conflicts += 1

    return FlushResult(flushed=flushed, conflicts=conflicts, blocked=blocked,
                       pending=len(journal.pending()), skipped=False, verdict=verdict)


# --------------------------------------------------------------------------- sync (reconcile)
def _conflict_prompt(c: ConflictView) -> str:
    remote_rev = (c.remote or {}).get("revision")
    return (f"mokata · sync conflict on '{c.key}': {c.detail}\n"
            f"  your local write vs the remote version (revision {remote_rev}).\n"
            f"  Keep your LOCAL version (overwrite the remote)?  "
            f"[y = keep local / n = keep remote]")


def _decide_conflict(c: ConflictView, *, assume_yes: bool,
                     confirm: Optional[Callable[[str], bool]],
                     emit: Callable[[str], None]) -> str:
    """One human decision per conflict → 'local' | 'remote' | 'defer'. NEVER silently picks a
    winner: with no way to ask (non-interactive, no `confirm`) it DEFERS (leaves the entry
    conflicted) rather than last-writer-wins."""
    emit(_conflict_prompt(c))
    if confirm is not None:
        return "local" if confirm(_conflict_prompt(c)) else "remote"
    if assume_yes:
        return "defer"                         # can't decide safely without a human
    from .prompt import read_yes_no
    try:
        keep_local = read_yes_no(_conflict_prompt(c),
                                 "Keep your LOCAL version (overwrite the remote)?")
    except Exception:                          # non-interactive stdin → fail-closed to defer
        return "defer"
    return "local" if keep_local else "remote"


def sync(surface: Any, *, environ: Optional[dict] = None, assume_yes: bool = False,
         confirm: Optional[Callable[[str], bool]] = None, health: Any = None,
         connect: Optional[Callable[..., Any]] = None, ledger: Any = None,
         recover: Optional[Callable[[], int]] = None,
         out: Optional[Callable[[str], None]] = None) -> SyncResult:
    """`mokata sync` = manual flush + reconcile (doc 48 E3). Recovers stranded floor rows, flushes
    the journal (carrying each original approval, C5), then reconciles every CAS conflict through
    the human gate (P2) — keep-local re-queues + re-flushes, keep-remote drops the local write,
    and an un-decided conflict stays deferred. Never silent last-writer-wins."""
    emit = out or (lambda *_a: None)
    recovered = 0
    if recover is not None:
        try:
            recovered = int(recover() or 0)
        except Exception as exc:               # recovery is best-effort — never break sync
            emit(f"floor recovery skipped: {exc}")

    r1 = flush(surface, environ=environ, health=health, connect=connect, ledger=ledger, out=emit)
    if r1.skipped:
        return SyncResult(recovered=recovered, skipped=True, verdict=r1.verdict,
                          reason=r1.reason, pending=r1.pending)

    journal = TeamJournal.for_surface(surface)
    conflicts = journal.conflicts()
    resolved_local = resolved_remote = deferred = 0
    for c in conflicts:
        decision = _decide_conflict(c, assume_yes=assume_yes, confirm=confirm, emit=emit)
        remote_rev = (c.remote or {}).get("revision")
        if decision == "local":
            journal.resolve(c.id, "kept-local", remote_revision=remote_rev)
            resolved_local += 1
        elif decision == "remote":
            journal.resolve(c.id, "kept-remote", remote_revision=remote_rev)
            resolved_remote += 1
        else:
            deferred += 1
        if ledger is not None and decision != "defer":
            ledger.record("team_sync_conflict", journal_id=c.id, key=c.key,
                          decision=f"kept-{decision}", remote_revision=remote_rev,
                          reason="human-gated sync conflict resolution (P2)")

    # re-flush the kept-local entries (now re-queued at the current remote revision).
    r2 = FlushResult()
    if resolved_local:
        r2 = flush(surface, environ=environ, health=health, connect=connect, ledger=ledger,
                   out=emit)

    return SyncResult(recovered=recovered, flushed=r1.flushed + r2.flushed,
                      conflicts_found=len(conflicts), resolved_local=resolved_local,
                      resolved_remote=resolved_remote, deferred=deferred,
                      blocked=r1.blocked + r2.blocked, pending=len(journal.pending()),
                      skipped=False, verdict=r1.verdict)


# --------------------------------------------------------------------------- floor recovery
def recover_stranded_floor(surface: Any, *, floor_rows: List[Dict[str, Any]],
                           remote_ids, project: Optional[str] = None, actor: str = "user",
                           ledger_id: Any = "floor-recovery") -> int:
    """Recovery migration (doc 48 finding 3): rows the OLD silent fallback stranded in the local
    SQLite floor are enqueued into the journal so they flush through the SAME gated path. A floor
    row already present remotely (`remote_ids`) or already pending is skipped, so re-running is
    idempotent. Returns the number newly enqueued. Team-mode only (no-op in local mode)."""
    if _rm.read_mode(surface) != _rm.TEAM:
        return 0
    journal = TeamJournal.for_surface(surface)
    have = {e.key for e in journal.pending()} | {e.key for e in journal.blocked()}
    have |= {c.key for c in journal.conflicts()}
    remote = set(remote_ids or ())
    n = 0
    for row in floor_rows:
        rid = row.get("id")
        if not rid or rid in remote or rid in have:
            continue
        journal.append(JournalEntry(
            id=uuid.uuid4().hex, op="memory_put", table=teamdb.MEMORY_TABLE, key=rid,
            payload=row, ledger_id=ledger_id, project=project, actor=actor,
            base_revision=None, source="recovery"))
        have.add(rid)
        n += 1
    return n


def _floor_rows(surface: Any, project: Optional[str]) -> List[Dict[str, Any]]:
    """Read the local SQLite memory floor as flush-ready payload rows (best-effort; [] on any
    error). These are the rows the OLD silent fallback may have stranded off the shared DB."""
    import json as _json
    from . import TEMP_LOCAL_DIRNAME as _tl
    from .memory.backends import SQLiteBackend
    path = os.path.join(surface.mokata_dir, _tl, "memory", "memory.db")
    if not os.path.exists(path):
        return []
    try:
        floor = SQLiteBackend(path)
        rows = []
        for item in floor.all():
            rows.append({"id": item.id, "mtype": item.mtype, "subject": item.subject,
                         "status": item.status, "doc": _json.dumps(item.to_dict()),
                         "project": project})
        floor.close()
        return rows
    except Exception:
        return []


def live_recover(surface: Any, environ: Optional[dict], *, project: Optional[str] = None,
                 actor: str = "user") -> int:
    """The CLI's floor-recovery step: read the SQLite floor + the remote id set, then enqueue any
    stranded rows. Best-effort — returns 0 if the floor/remote can't be read (never breaks sync)."""
    rows = _floor_rows(surface, project)
    if not rows:
        return 0
    conn = _default_connect(surface, environ)
    remote_ids = set()
    if conn is not None:
        try:
            got = conn.execute(f"SELECT id FROM {teamdb.MEMORY_TABLE}").fetchall()  # nosec B608
            remote_ids = {r[0] for r in got}
        except Exception:
            remote_ids = set()
    return recover_stranded_floor(surface, floor_rows=rows, remote_ids=remote_ids,
                                  project=project, actor=actor, ledger_id="floor-recovery")
