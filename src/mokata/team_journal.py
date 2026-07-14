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

MS.S5 (M-5) makes the flush EXACTLY-ONCE across processes. Two Claude Code windows on one repo are
two OS processes sharing ONE journal file, and the flush had no identity: both snapshotted the same
pending list and both applied it. The second apply lost its CAS (the row it was inserting/updating
was already there — put there by its own sibling) and was recorded as a CONFLICT: a PHANTOM
self-conflict, warning the user about a write that had in fact succeeded. Two mechanisms close it:

  * **Single flusher (the mutex).** A flush takes a NON-BLOCKING cross-process `oslock` on a sidecar
    beside the journal and re-reads the pending set INSIDE it. A process that finds the lock held
    SKIPS quietly — it never waits, never connects, and never probes: the holder is flushing the
    SAME shared journal, so the skipper's entries are already in the batch the holder reads. No
    daemon, no timer, no background thread.
  * **Idempotent apply.** A CAS miss is no longer conflict-by-assumption. The remote row is read and
    compared to the end state the entry wants: if it is ALREADY that state (a sibling flush — or
    this machine's own pre-crash flush — landed it), the entry is marked flushed, never conflicted
    and never applied twice. A row that diverges (different content) is a REAL concurrent-writer
    conflict and surfaces exactly as it always has.

Together they hold across the ugly cases: two windows racing, a crash mid-flush (the OS lock dies
with the process; the next flush re-applies the un-acked entries idempotently), and two SEPARATE
journals (worktrees) flushing overlapping writes to one shared DB — where the mutex does not apply
but the idempotent apply still yields exactly-once.

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
from .atomicfile import lock_path_for
from .degrade import FAILURE_UNREACHABLE, note_degraded
from .oslock import DEFAULT_TIMEOUT, LockTimeout, file_lock
from .errors import DegradedCapability

JOURNAL_FILENAME = "team_journal.jsonl"

# MS.S5 (M-5) — the single-flusher mutex. A dot-prefixed sidecar BESIDE the journal (never the
# journal file itself, which is appended to under the lock), so the lock is keyed on exactly the
# journal a set of processes shares. Held only for the duration of one flush pass; being an OS
# advisory lock it dies with the process, so a crashed flusher leaves nothing to reap.
FLUSH_LOCK_FILENAME = ".team_journal.jsonl.flush.lock"

# Non-blocking by construction: ONE attempt, then skip. A flush never waits on another flusher —
# the holder is draining the same shared journal, and an un-drained entry simply stays pending for
# the next touchpoint (work-locally, nothing lost).
FLUSH_LOCK_TIMEOUT = 0.0

# MS.S8 — the APPEND lock. A SECOND, separate sidecar (`lock_path_for`, the standard convention), and
# emphatically NOT the flush mutex above: the flush appends its own markers while holding that mutex,
# so sharing them would self-deadlock (`oslock` is not reentrant across fds).
#
# It exists because `open(path, "a")` is NOT atomic on Windows. POSIX's `O_APPEND` makes "seek to
# EOF, then write" one indivisible step; the Windows CRT has no such handle and EMULATES append mode
# — it seeks to the end (`_lseeki64_nolock`) and then writes, two steps, serialised only between
# THREADS of one process. Two processes therefore both seek to EOF=N and both write at N, and the
# second write lands on top of the first: a record is silently GONE, in a file that still parses.
#
# That is reachable here because appends arrive under THREE different lock contexts that exclude each
# other not at all: a gated write's `append` (under the LEDGER lock, held by the WriteGate across its
# commit closure), the flusher's `mark_flushed`/`mark_conflict`/`mark_blocked` (under the FLUSH mutex
# above), and `resolve`/`recover_stranded_floor` (under NO lock — `sync` runs them after the flush
# mutex is released). A lost `write` record is the data-loss case: a human-approved team write that
# never flushes and never even reports as pending, because it is not in the journal to be counted.
#
# Held across open→write→fsync inside `_append`, so EVERY append site inherits it — they all funnel
# through there. It is a LEAF lock (it takes nothing else while held), so it cannot form a cycle with
# the ledger or flush locks: the only orders it can appear in are LEDGER→APPEND, FLUSH→APPEND and
# ∅→APPEND. Bounded (appends are tiny and uncontended in the common case); a timeout is a genuine
# stuck-lock error and PROPAGATES rather than dropping the write on the floor.
APPEND_LOCK_TIMEOUT = DEFAULT_TIMEOUT

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
    already_applied: bool = False    # MS.S5 — the row already held this exact end state (no re-apply)


@dataclass
class FlushResult:
    """The verdict of one flush pass. `skipped` = nothing was pushed (work-locally; nothing lost),
    for one of two reasons: the connection wasn't healthy, or (MS.S5) `contended` — another process
    holds the flush mutex and is draining this same journal. `already_applied` counts the entries
    that were found ALREADY landed and marked flushed idempotently (they are counted in `flushed`
    too: they are done). Never raises — a flush is best-effort by construction."""

    flushed: int = 0
    conflicts: int = 0
    blocked: int = 0
    pending: int = 0
    skipped: bool = False
    reason: str = ""
    verdict: Any = None
    contended: bool = False
    already_applied: int = 0


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

    @property
    def flush_lock_path(self) -> str:
        """The single-flusher mutex for THIS journal (MS.S5). Derived from the journal path, so every
        process that shares the journal shares the lock — and a process on a different journal (a
        separate worktree) takes a different one and is free to flush its own entries."""
        return os.path.join(os.path.dirname(self.path) or ".", FLUSH_LOCK_FILENAME)

    @property
    def append_lock_path(self) -> str:
        """The APPEND lock for THIS journal (MS.S8) — distinct from the flush mutex above, which the
        flush already holds when it appends its markers. Keyed on the journal path, so every process
        sharing the file shares the lock."""
        return lock_path_for(self.path)

    # --- append-only writers -------------------------------------------------
    def _append(self, rec: Dict[str, Any]) -> None:
        """The ONE funnel every journal record goes through — and therefore the one place the append
        lock has to be taken (MS.S8). Held across open→write→fsync, because on Windows `open(path,
        "a")` is a SEEK then a WRITE, not an atomic append: without this, a gated write (LEDGER lock)
        and a flusher's marker (FLUSH mutex) can both seek to EOF and both write there, and one
        record is silently overwritten. See `APPEND_LOCK_TIMEOUT`."""
        with file_lock(self.append_lock_path, timeout=APPEND_LOCK_TIMEOUT):
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())    # crash-safe: the write is durable before we return
                except OSError:  # pragma: no cover - fsync is unsupported on some filesystems
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
                    except json.JSONDecodeError:  # pragma: no cover - skip a torn last line
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

    def pending_count(self) -> int:
        """The number of approved-but-unflushed team writes waiting locally (CM.S4). O(pending)
        over the replayed status — the count the badge/doctor/MCP surface so a backlog is never
        silent (a team can no longer accumulate local-only writes with zero signal)."""
        _e, status, _c, order = self._replay()
        return sum(1 for i in order if status.get(i) == _PENDING)

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
    """The remote row for `key`, or None when there ISN'T ONE. `None` means EXACTLY that — never
    "we couldn't tell".

    D5 — this used to be `except Exception: return None`, and that swallow was a DATA-LOSS bug, not
    a fallback. `apply_memory_write` reads `None` as "no such row remotely", which on the DELETE
    path is the *success* signal: a transient DB error made the flush mark the entry FLUSHED and
    write a `team_flush` ledger row for a delete that NEVER TOUCHED Postgres. The user's gated PRUNE
    silently didn't prune, and the journal, doctor and the ledger all agreed it had. On the
    INSERT/UPDATE paths the same swallow turned a read failure into a phantom CONFLICT.

    A false success is not a degrade — there is nothing to fall back TO — so the error PROPAGATES.
    The flush loop catches it per-entry, leaves the entry PENDING, and says so loudly (nothing is
    lost; the next healthy flush re-applies it idempotently, MS.S5)."""
    row = conn.execute(_SELECT_SQL, (key,)).fetchone()
    if not row:
        return None
    return {"doc": row[0], "revision": row[1]}


def _canon_doc(value: Any) -> Optional[str]:
    """A `doc` value in a comparable canonical form. The column is TEXT holding the item's JSON, but
    a driver may hand back bytes (or a dict, on a jsonb-typed column), and two equal documents can
    serialise with different key order — so parse-then-re-dump with sorted keys where possible, and
    fall back to the raw string when it isn't JSON. Returns None only when there is nothing to
    compare (which never counts as a match)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:              # pragma: no cover - undecodable bytes never match
            return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), sort_keys=True)
        except (json.JSONDecodeError, ValueError):
            return value                        # not JSON — compare the raw text
    return str(value)                           # pragma: no cover - defensive


def _already_applied(remote: Optional[Dict[str, Any]], entry: JournalEntry) -> bool:
    """MS.S5 (M-5) — does the remote row ALREADY hold exactly the end state this entry wants?

    This is the phantom-self-conflict test. A CAS miss means only "the row is not where I thought it
    was"; it does NOT mean someone else's change is about to be lost. When the remote content is
    IDENTICAL to what this entry would write, the write has already landed — put there by a sibling
    process flushing the same approved write, or by this machine's own flush before it crashed. The
    entry is done: re-applying it would be a double-apply and calling it a conflict would warn about
    a write that in fact succeeded. Content-equality (not provenance) is deliberately the test: if
    the shared row already says exactly what we were approved to make it say, the end state is the
    approved one, whoever typed it.

    Anything else — a different doc, a row that is absent when we expect one — is a REAL conflict and
    is returned to the human gate unchanged."""
    if remote is None:
        return False
    mine = _canon_doc(entry.payload.get("doc"))
    if mine is None:
        return False
    return _canon_doc(remote.get("doc")) == mine


def apply_memory_write(conn: Any, entry: JournalEntry) -> ApplyOutcome:
    """Compare-and-set a memory write against the shared table (doc 48 C1). A believed-new row
    is an INSERT ... ON CONFLICT DO NOTHING; an update is a revision-guarded UPDATE; a delete
    (TM.S5c — the gated PRUNE path) is a revision-guarded DELETE. A row that doesn't match is a
    CONFLICT — never a silent overwrite and never a silent hard-delete of a shared row.

    MS.S5 (M-5): a CAS miss is checked for IDEMPOTENCY first. If the remote row already holds this
    entry's exact end state (`_already_applied` — or, for a delete, is already gone), the write has
    already landed and the entry is `ok`/`already_applied`: marked flushed, not re-applied, and NOT
    reported as a conflict. Only a genuine divergence still surfaces as a conflict."""
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
        remote = _read_remote(conn, entry.key)
        if remote is None:
            # MS.S5 — the row is already GONE: this same delete already landed (a sibling flush, or
            # our own pre-crash one). The end state is the approved one → flushed, not conflicted.
            return ApplyOutcome("ok", already_applied=True,
                                detail="already applied — the row is already gone (this delete "
                                       "landed in an earlier/other flush)")
        return ApplyOutcome("conflict",
                            detail=(f"remote revision advanced past base {entry.base_revision} "
                                    "(the row changed or was already removed) — a concurrent "
                                    "writer touched this row"),
                            remote=remote)
    if entry.base_revision is None:
        cur = conn.execute(_INSERT_SQL, (p.get("id"), *cols))
        if (getattr(cur, "rowcount", 0) or 0) > 0:
            return ApplyOutcome("ok", new_revision=1)
        remote = _read_remote(conn, entry.key)
        if _already_applied(remote, entry):
            return ApplyOutcome("ok", new_revision=(remote or {}).get("revision"),
                                already_applied=True,
                                detail="already applied — the remote row already holds this exact "
                                       "write (an earlier/other flush landed it)")
        return ApplyOutcome("conflict",
                            detail="a row with this id already exists remotely (concurrent create)",
                            remote=remote)
    cur = conn.execute(_UPDATE_SQL, (*cols, p.get("id"), entry.base_revision))
    if (getattr(cur, "rowcount", 0) or 0) > 0:
        return ApplyOutcome("ok", new_revision=int(entry.base_revision) + 1)
    remote = _read_remote(conn, entry.key)
    if _already_applied(remote, entry):
        return ApplyOutcome("ok", new_revision=(remote or {}).get("revision"),
                            already_applied=True,
                            detail="already applied — the remote row already holds this exact write "
                                   "(an earlier/other flush landed it)")
    return ApplyOutcome("conflict",
                        detail=(f"remote revision advanced past base {entry.base_revision} "
                                "(lost update) — a concurrent writer changed this row"),
                        remote=remote)


# --------------------------------------------------------------------------- flush
class _JournalUnavailable(DegradedCapability):
    pass


def _default_connect(surface: Any, environ: Optional[dict]) -> Any:
    env = os.environ if environ is None else environ
    # Resolve the CONFIGURED shared-DB env-var NAME (C-1) so the flush connects to the SAME DB
    # memory reads/health resolve — a custom `team connect --dsn-env` no longer strands writes.
    from .dsn import resolve_dsn_env
    dsn = (env.get(resolve_dsn_env(surface)) or "").strip()
    if not dsn:
        return None
    from .memory._pg import get_connection
    try:
        # `get_connection` funnels EVERY failure it can produce — a missing psycopg (the optional
        # extra) and any connect/timeout failure alike — into the typed unavailable we hand it, so
        # this is the whole raisable set. The caller degrades LOUDLY on `None` (the flush returns
        # `skipped` with "no reachable connection", journal intact — nothing lost).
        return get_connection(dsn, _JournalUnavailable)
    except _JournalUnavailable:
        return None


def _default_scan(entry: JournalEntry) -> list:
    """Per-publish secret-scan of the payload (P2). Egress-strength (`for_send`) — a durable
    shared write leaves this machine, so it is held to the outbound bar."""
    from .govern.secrets import scan
    return scan(text=json.dumps(entry.payload), path=entry.key, for_send=True)


CONTENDED_REASON = ("another window is already flushing this journal — skipped (its flush covers "
                    "these writes; nothing lost, nothing duplicated)")


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
    conflicted for the human gate. `health`/`connect`/`scan`/`ledger` are injectable for tests.

    MS.S5 (M-5) — SINGLE FLUSHER. The whole pass runs under a non-blocking cross-process mutex on a
    sidecar beside the journal. If another process holds it, this call returns `skipped`+`contended`
    IMMEDIATELY: no wait, no connect, no probe. That is safe, not lossy — the holder is draining the
    SAME journal file this process appends to, so these entries are in its batch (an entry appended
    after the holder's snapshot simply stays pending for the next touchpoint). The pending set is
    re-read INSIDE the lock, so a process that queued behind a just-finished flusher sees the
    entries it already flushed as flushed, and never applies them twice."""
    from . import team_health
    journal = TeamJournal.for_surface(surface)

    # Cheap unlocked pre-check: an empty journal takes no lock at all and never reaches the apply
    # path (nothing to apply), so the common single-window "nothing to flush" call is byte-identical
    # — same verdict, same result — and costs exactly what it did before.
    if not journal.pending():
        verdict = health if health is not None else team_health.check(
            surface, environ=environ, probe=probe)
        return FlushResult(flushed=0, pending=0, skipped=False, verdict=verdict,
                           reason="nothing to flush")

    try:
        with file_lock(journal.flush_lock_path, timeout=FLUSH_LOCK_TIMEOUT):
            return _flush_locked(surface, journal, environ=environ, health=health, probe=probe,
                                 connect=connect, ledger=ledger, scan=scan, out=out)
    except LockTimeout:
        if out:
            out(f"· flush skipped: {CONTENDED_REASON}")
        return FlushResult(flushed=0, pending=journal.pending_count(), skipped=True,
                           contended=True, reason=CONTENDED_REASON)


def _flush_locked(surface: Any, journal: TeamJournal, *, environ: Optional[dict], health: Any,
                  probe: Optional[Callable[[str], Any]], connect: Optional[Callable[..., Any]],
                  ledger: Any, scan: Optional[Callable[[JournalEntry], list]],
                  out: Optional[Callable[[str], None]]) -> FlushResult:
    """One flush pass, run as the SOLE flusher of this journal (MS.S5). Identical to the pre-MS.S5
    flush except that the pending set is snapshotted HERE — under the mutex — so it can't be stale:
    an entry another process just flushed replays as flushed, not as pending."""
    from . import team_health
    verdict = health if health is not None else team_health.check(
        surface, environ=environ, probe=probe)
    pend = journal.pending()

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
    flushed = conflicts = blocked = already = 0
    for entry in pend:
        findings = do_scan(entry)
        if findings:
            journal.mark_blocked(entry.id,
                                 detail="blocked: secret detected in the payload — NOT published")
            blocked += 1
            if out:
                out(f"⚠ blocked publish of {entry.key}: secret detected (remove it, then re-sync)")
            continue
        try:
            outcome = apply_memory_write(conn, entry)
        except Exception as exc:
            # D5 — the DB failed MID-APPLY (the statement, or `_read_remote`'s CAS-miss re-read,
            # which no longer lies about it). Broad by necessity: `conn` is a psycopg connection and
            # psycopg is an OPTIONAL extra, so its error class cannot be named at module scope — and
            # narrowing it wrong here would turn a transient DB blip into a CRASHED flush.
            #
            # The entry is simply left PENDING: we append NO marker, so the replay still reads it as
            # pending and the next healthy flush re-applies it (idempotently — MS.S5). That is the
            # ONLY safe outcome. Marking it flushed on a failed read is precisely the false success
            # this fix removes; marking it conflicted would invent a concurrent writer that does not
            # exist. Loud once per process, and `pending` (re-read off the journal below) stays true.
            note_degraded("team-flush", FAILURE_UNREACHABLE,
                          fallback="the entry stays PENDING — nothing is lost",
                          fix="run `mokata sync` once the connection is healthy",
                          detail=f"{type(exc).__name__}: {exc}")
            continue
        if outcome.status == "ok":
            journal.mark_flushed(entry.id, remote_revision=outcome.new_revision)
            if ledger is not None:
                # C5 / P2 — the flush INHERITS the original approval; record its ledger id so the
                # audit trail links deferred durability back to the human decision (no bypass).
                # `already_applied` keeps that trail HONEST about the M-5 case: this pass recognised
                # the write as already landed and marked it flushed, rather than applying it twice.
                ledger.record("team_flush", journal_id=entry.id, table=entry.table,
                              key=entry.key, actor=entry.actor,
                              approval_ledger_id=entry.ledger_id, revision=outcome.new_revision,
                              already_applied=outcome.already_applied,
                              reason=("already applied by another flush — marked flushed, NOT "
                                      "re-applied (exactly-once, MS.S5)"
                                      if outcome.already_applied
                                      else "flush inherits the original human approval (P2)"))
            flushed += 1
            already += 1 if outcome.already_applied else 0
        else:
            journal.mark_conflict(entry.id, detail=outcome.detail, remote=outcome.remote)
            conflicts += 1

    return FlushResult(flushed=flushed, conflicts=conflicts, blocked=blocked, already_applied=already,
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
    """Read the local SQLite memory floor as flush-ready payload rows. These are the rows the OLD
    silent fallback may have stranded off the shared DB.

    D5 — this used to be `except Exception: return []`, and "[] on any error" is the one answer a
    RECOVERY step must never give: a locked or corrupt floor DB read back as "no stranded rows", so
    floor recovery silently NEVER RAN and `mokata sync` reported a clean pass over a floor it had
    not managed to open. The error now PROPAGATES to the sole caller path (`live_recover`, driven
    only by `sync`'s `recover=` hook), whose EXISTING handler prints `floor recovery skipped: {exc}`
    — the loud channel already exists, so this needs no new notice, only an end to the silence.

    An ABSENT floor is still `[]`: that is genuinely "nothing was stranded", not a failure."""
    import json as _json
    from . import TEMP_LOCAL_DIRNAME as _tl
    from .memory.backends import SQLiteBackend
    path = os.path.join(surface.mokata_dir, _tl, "memory", "memory.db")
    if not os.path.exists(path):
        return []
    floor = SQLiteBackend(path)
    try:
        return [{"id": item.id, "mtype": item.mtype, "subject": item.subject,
                 "status": item.status, "doc": _json.dumps(item.to_dict()),
                 "project": project}
                for item in floor.all()]
    finally:
        floor.close()                          # never leak the handle on a failed read


def _db_errors() -> tuple:
    """The exception classes a LIVE psycopg connection raises, or `()` when the driver is absent.

    psycopg is an OPTIONAL extra, so `psycopg.Error` cannot be named at module scope. It can be
    named HERE, at call time — and when it can't be, there is no live connection either (the connect
    already degraded to `None`), so the empty tuple is not a gap: the guarded block never runs."""
    try:
        import psycopg
    except ImportError:                         # pragma: no cover - the extra is absent
        return ()
    return (psycopg.Error,)


def live_recover(surface: Any, environ: Optional[dict], *, project: Optional[str] = None,
                 actor: str = "user") -> int:
    """The CLI's floor-recovery step: read the SQLite floor + the remote id set, then enqueue any
    stranded rows. A floor that cannot be READ now propagates (D5 — see `_floor_rows`): `sync`'s
    `recover=` handler prints `floor recovery skipped: {exc}` rather than reporting a clean pass."""
    rows = _floor_rows(surface, project)
    if not rows:
        return 0
    conn = _default_connect(surface, environ)
    remote_ids = set()
    if conn is not None:
        try:
            got = conn.execute(f"SELECT id FROM {teamdb.MEMORY_TABLE}").fetchall()  # nosec B608
            remote_ids = {r[0] for r in got}
        except _db_errors():
            # An unread remote id-set is SAFE to treat as empty: a floor row that IS already remote
            # is re-enqueued, flushes as an INSERT ... ON CONFLICT DO NOTHING, and the CAS miss is
            # recognised as already-applied (MS.S5) — never a duplicate, never an overwrite. This is
            # a real fallback, not a false success, so it stays a fallback.
            remote_ids = set()
    return recover_stranded_floor(surface, floor_rows=rows, remote_ids=remote_ids,
                                  project=project, actor=actor, ledger_id="floor-recovery")
