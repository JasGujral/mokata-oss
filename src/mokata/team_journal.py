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

J-PERF makes the READ path stop paying for that history. Every team-mode read replayed the ENTIRE
file — `pending`/`pending_count`/`conflicts`/`blocked` each re-opened and re-parsed it, so one
`flush` replayed three times and the memory overlay replayed once per read — and the append-only
file never shrank, so every read got slower for the life of the repo. Two changes, no new infra:

  * **The replay is cached on file identity** (`(st_mtime_ns, st_size)`, re-stat'd on EVERY access).
    Sound because BOTH writers of the file hold the append lock and move that pair one-directionally
    — see `_identity` for the full argument, including the cross-process case.
  * **A settled flush compacts** past `COMPACT_FLUSHED_THRESHOLD` flushed entries. Append-only is
    still the rule for every WRITE; compaction is a separate, atomic (`R-MAN`), append-locked
    rewrite that only ever REMOVES whole lines that the replay has already resolved to FLUSHED —
    entries whose audit record lives durably on the LEDGER, not here. See `TeamJournal.compact`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import TEMP_LOCAL_DIRNAME, run_mode as _rm, teamdb
from .atomicfile import atomic_write_text, lock_path_for
from .degrade import (FAILURE_PARTIAL_APPLY, FAILURE_SCHEMA, FAILURE_UNREACHABLE,
                      note_degraded)
from .memory import edges as _edges                   # DB.S7a — the ONE edge projection
from .memory import item as _item                     # DB.S7a — `now_iso`, the one clock
from .memory.backends import scope_columns_from_doc   # DB.S2b — the ONE scope-column projection
from .oslock import DEFAULT_TIMEOUT, LockTimeout, file_lock
from .errors import ControlSignal, DegradedCapability

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

# J-PERF — COMPACTION THRESHOLD. Past this many FLUSHED entries in the replayed state, a successful
# flush rewrites the journal without them (see `TeamJournal.compact`). Deliberately a plain, tunable
# constant rather than a setting: it trades a rare O(n) rewrite against the read cost of carrying
# dead history forever, and there is no user decision in it. 500 is ~a month of heavy team writing
# on one repo — high enough that a normal session NEVER compacts (the flush stays exactly what it
# was), low enough that the file cannot grow without bound for the life of the repo.
COMPACT_FLUSHED_THRESHOLD = 500

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
    """The append-only local write journal for team mode. State is REPLAYED from the log, so a
    crash mid-flush loses nothing: an un-acked write simply replays as still-pending on the next
    run. Every WRITE is an append (`_append` is the sole funnel); the only rewrite is J-PERF's
    `compact`, which drops already-flushed lines atomically under the append lock and leaves the
    replayed state — and therefore every caller-visible read — bit-for-bit identical."""

    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # J-PERF — the replay cache. PER INSTANCE and nothing wider: no module-level dict, no
        # daemon, no cross-instance sharing. A `TeamJournal` is constructed per call site, so the
        # cache's whole lifetime is one logical operation — it can never outlive the state it
        # describes the way a process-global cache would.
        self._cache_identity: Optional[tuple] = None
        self._cache: Optional[tuple] = None

    @classmethod
    def for_surface(cls, surface: Any) -> "TeamJournal":
        return cls(cls.path_for(surface))

    @staticmethod
    def path_for(surface: Any) -> str:
        """This surface's journal path, resolved WITHOUT touching the disk.

        DB.S6/I6 — `__init__` creates the parent directory, which is right for a writer and wrong
        for the propose-only detection arm: constructing a journal just to ask "are there any
        conflicts?" would make a read path create `.mokata/temp_local/`. The detector resolves the
        path through here and only constructs when the file already exists, so detection writes
        nothing at all — not a row, not a file, not a directory."""
        return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, JOURNAL_FILENAME)

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
        """One record through the funnel — the single-record case of `_append_all`."""
        self._append_all([rec])

    def _append_all(self, recs: List[Dict[str, Any]]) -> None:
        """The ONE funnel every journal record goes through — and therefore the one place the append
        lock has to be taken (MS.S8). Held across open→write→fsync, because on Windows `open(path,
        "a")` is a SEEK then a WRITE, not an atomic append: without this, a gated write (LEDGER lock)
        and a flusher's marker (FLUSH mutex) can both seek to EOF and both write there, and one
        record is silently overwritten. See `APPEND_LOCK_TIMEOUT`.

        DB.S7d — takes a LIST, and that is the whole atomicity mechanism for a group decision. All
        records are serialised into ONE buffer and written with ONE `write` under ONE lock hold, so
        a set of records either reaches the log together or not at all. Appending them one at a time
        would leave N-1 windows in which a crash lands a half-resolved approval on disk — the
        durable form of exactly the half-decided state `group_decision_refusal` exists to prevent.
        Serialising BEFORE the open matters too: a record that cannot be encoded raises with the
        file untouched rather than after its siblings are already down."""
        if not recs:
            return
        blob = "".join(json.dumps(rec) + "\n" for rec in recs)
        with file_lock(self.append_lock_path, timeout=APPEND_LOCK_TIMEOUT):
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(blob)
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
        self.resolve_group([(entry_id, resolution, remote_revision)])

    def resolve_group(self, decisions: List["Tuple[str, str, Optional[int]]"]) -> None:
        """DB.S7d — commit N human decisions as ONE durable act (`resolve` is the 1-member case).

        A whole-approval verdict that reached the log one record at a time would not be a group
        decision: a crash between two appends replays as an approval where some members are settled
        and the rest are still conflicted, which is precisely the half-decided state the surface
        exists to remove. Routed through `_append_all`, the set is one buffer, one write, one lock
        hold — the members land together or not at all."""
        self._append_all([{"kind": "resolved", "id": eid, "resolution": res,
                           "remote_revision": rev} for eid, res, rev in decisions])

    # --- replay --------------------------------------------------------------
    def _lines(self) -> List[tuple]:
        """Every parseable record as `(raw_line, parsed)`. The RAW line is carried alongside the
        parse for exactly one caller — `compact`, which writes surviving lines back BYTE-FOR-BYTE
        rather than re-serialising them. Re-dumping would be a schema change by accident (key
        order, unicode escaping, separators); keeping the bytes means compaction can only ever
        REMOVE lines, never alter the shape of one."""
        if not os.path.exists(self.path):
            return []
        out: List[tuple] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    try:
                        out.append((line, json.loads(stripped)))
                    except json.JSONDecodeError:  # pragma: no cover - skip a torn last line
                        pass
        return out

    def _records(self) -> List[Dict[str, Any]]:
        return [rec for _line, rec in self._lines()]

    def _identity(self) -> Optional[tuple]:
        """J-PERF — the file-identity the replay cache is keyed on: `(st_mtime_ns, st_size)`, or
        `None` when the journal does not exist (itself a valid, cacheable state: "empty").

        WHY THIS IS A SOUND INVALIDATION SIGNAL. There are exactly TWO writers of `self.path`:
        `_append` (every marker and every write record funnels through it — MS.S8) and `compact`
        (J-PERF). Both hold the APPEND lock, and their effects on the pair are one-directional:
        an append strictly GROWS `st_size`; a compaction strictly SHRINKS it (it only ever removes
        whole lines, and only runs when there is at least one to remove). There is no in-place,
        same-size overwrite anywhere in the module — no `seek`+write, no fixed-width field, no
        truncate-and-rewrite — so a content change without a size change is not a shape this file
        can take. `st_mtime_ns` is the second axis and the defense against the one pathological
        interleaving size alone would miss (append → compact → append back to the identical byte
        count): all three are separate `fsync`'d writes under a lock, which cannot land inside one
        nanosecond tick.

        CROSS-PROCESS. This is a STAT, taken on EVERY access — the cache stores the PARSE, never
        the stat. Another process's append is taken under the same append lock and moves the pair,
        so the very next read here re-parses and sees it. That is the whole cross-process argument:
        we never assume our own writes are the only ones."""
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _invalidate(self) -> None:
        self._cache_identity = None
        self._cache = None

    def _replay(self):
        """The replayed state, cached on file identity (J-PERF). Re-stats every call (cheap) and
        re-parses ONLY when the journal actually changed — so `flush`'s three reads, and the
        overlay's per-read `pending()`, cost one parse between them instead of one each.

        The cached tuple is returned BY REFERENCE. That is safe because the replayed state is
        read-only by contract: every caller (`pending`/`conflicts`/`blocked`/the overlay/the flush
        loop) reads the entries and builds its own list; nothing mutates a `JournalEntry` it was
        handed. The one mutation in the module (`base_revision` on a `kept-local` resolve) happens
        DURING the build, off the record, not on a returned object."""
        identity = self._identity()
        if self._cache is not None and self._cache_identity == identity:
            return self._cache
        state = self._replay_uncached()
        self._cache_identity = identity
        self._cache = state
        return state

    def _replay_uncached(self):
        return self._replay_records(self._records())

    def _replay_records(self, records):
        entries: Dict[str, JournalEntry] = {}
        status: Dict[str, str] = {}
        conflicts: Dict[str, ConflictView] = {}
        order: List[str] = []
        # J-PERF — the membership set beside `order`. `order` still IS the order (the replay's
        # output shape is unchanged); the set only answers "have I seen this id", which used to be
        # a linear scan of a growing list INSIDE the per-record loop — O(n²) over the journal.
        seen: set = set()
        for rec in records:
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
                if rid not in seen:
                    seen.add(rid)
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

    # --- compaction (J-PERF) -------------------------------------------------
    def flushed_count(self) -> int:
        """How many entries the replay resolves to FLUSHED — the dead weight compaction removes."""
        _e, status, _c, _o = self._replay()
        return sum(1 for s in status.values() if s == _FLUSHED)

    def compact(self) -> int:
        """Rewrite the journal without its FLUSHED entries. Returns the number of lines dropped.

        WHAT IS PRUNABLE, AND WHY IT IS ONLY THIS. A FLUSHED entry is DONE: it is in Postgres, and
        `ledger.record("team_flush", ...)` wrote the durable audit row that links it back to the
        human approval that authorised it (C5). The LEDGER is the audit trail, not the journal —
        nothing in the codebase reads a flushed journal record. The replay is the journal's only
        reader, and its four outputs are consumed solely as `pending`/`pending_count`/`conflicts`/
        `blocked`; a FLUSHED id appears in none of them. So dropping a flushed id's records is
        invisible to every caller, which is precisely the bar.

        Everything else STAYS: PENDING entries (still to flush), CONFLICT entries and their
        `ConflictView` detail (awaiting the human gate in `sync`), BLOCKED entries (awaiting a
        removed secret), and the `base_revision` each carries for its CAS. DROPPED (`kept-remote`)
        entries stay too — they are terminal and unread, but keeping them costs nothing and
        pruning them is a second argument this stage does not need to make.

        Records are filtered BY ID, so an entry's `write` record and its `flushed` marker leave
        together — never one without the other (dropping a marker while keeping its write would
        RESURRECT a flushed write as pending, which is the one way compaction could lose data).

        WHICH LOCK PROTECTS THE REWRITE: the APPEND lock, taken here. NOT the flush mutex — the
        flush mutex excludes other FLUSHERS and nothing else, which is the very gap MS.S8 added the
        append lock to close (a gated write appends under the LEDGER lock, `resolve` under no lock
        at all). Holding the append lock across read→filter→replace is what makes "an append racing
        the compact window" impossible: the appender either lands before we read (its line is in
        `kept`) or blocks until the replace is done (it appends to the new file). Taking it here is
        also cycle-free — the append lock is a LEAF, and FLUSH→APPEND is the order the flusher's own
        `mark_flushed` already runs in. Nothing inside this window calls `_append`, so the
        non-reentrant `oslock` is never asked to nest.

        The rewrite itself is `atomic_write_text` (R-MAN): same-directory temp, fsync, `os.replace`.
        A crash at any point leaves the WHOLE old journal — which is the correct outcome, since the
        old journal is a superset of the new one and simply replays the same live state."""
        dropped = 0
        with file_lock(self.append_lock_path, timeout=APPEND_LOCK_TIMEOUT):
            # Re-read INSIDE the lock: whatever a concurrent appender landed before we got here is
            # part of the input, so it survives into the rewritten file.
            lines = self._lines()
            _e, status, _c, _o = self._replay_records([rec for _l, rec in lines])
            prunable = {rid for rid, s in status.items() if s == _FLUSHED}
            if not prunable:
                return 0
            kept = [raw for raw, rec in lines if rec.get("id") not in prunable]
            dropped = len(lines) - len(kept)
            if not dropped:
                return 0
            # Byte-preserving: `kept` holds the ORIGINAL lines, so the file can only ever have had
            # lines removed. Nothing is written here that was not already in the journal.
            atomic_write_text(self.path, "".join(kept))
        self._invalidate()
        return dropped

    def compact_if_needed(self, *, threshold: Optional[int] = None) -> int:
        """Compact past `threshold` FLUSHED entries; below it, the journal is left byte-untouched.

        The default is resolved HERE, at call time, not bound into the signature — so the module
        constant is the single source of truth and stays patchable (a default argument would
        snapshot it at import and silently ignore every later change)."""
        if threshold is None:
            threshold = COMPACT_FLUSHED_THRESHOLD
        if self.flushed_count() <= threshold:
            return 0
        return self.compact()


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
# DB.S2b — the flush is a memory WRITE PATH, so it populates the scope/precedence columns like
# every other one. It was easy to miss: the row reaches Postgres through the journal here, NOT
# through `PostgresBackend.put`, so leaving it out would have left team-flushed rows carrying the
# DDL default while locally-written rows carried the truth — a projection gap that only appears in
# team mode, i.e. exactly where the cross-tenant filter runs. The values come from the entry's own
# `doc` via `scope_columns_from_doc`, the same single definition the two `put()`s use.
_INSERT_SQL = (
    f"INSERT INTO {teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project, "  # nosec B608
    f"{teamdb.MEMORY_SCOPE_LEVEL_COLUMN}, {teamdb.MEMORY_SCOPE_ID_COLUMN}, "
    f"{teamdb.MEMORY_PIN_COLUMN}, {teamdb.MEMORY_PRIORITY_COLUMN}, "
    f"{teamdb.MEMORY_REVISION_COLUMN}, {teamdb.MEMORY_UPDATED_AT_COLUMN}) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, now()) ON CONFLICT (id) DO NOTHING"
)
_UPDATE_SQL = (
    f"UPDATE {teamdb.MEMORY_TABLE} SET mtype=%s, subject=%s, status=%s, doc=%s, project=%s, "  # nosec B608
    f"{teamdb.MEMORY_SCOPE_LEVEL_COLUMN}=%s, {teamdb.MEMORY_SCOPE_ID_COLUMN}=%s, "
    f"{teamdb.MEMORY_PIN_COLUMN}=%s, {teamdb.MEMORY_PRIORITY_COLUMN}=%s, "
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


def _doc_mapping(value: Any) -> Dict[str, Any]:
    """DB.S2b — a journal payload's `doc` as a plain mapping, for projecting the scope columns.

    Same input variety `_canon_doc` already copes with (a JSON string, or a dict when the column is
    jsonb-typed), and the same refusal to raise: anything unparseable degrades to `{}`, which
    `scope_columns_from_doc` turns into the item model's defaults. A malformed doc must not fail a
    flush — and defaulting lands the row at the NARROWEST scope, which is the direction that
    cannot leak."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:              # pragma: no cover - undecodable bytes
            return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


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


# --------------------------------------------------------------- DB.S7a · the edge projection
# The attribute the v5 capability probe is memoized under, ON THE CONNECTION. Per-connection
# because that is what the answer is about: one flush applies several entries against one
# connection, and re-asking the catalog per entry would be a round trip per write to learn
# something that cannot change mid-flush (only `team init` creates the table, and it is not
# running concurrently with this — the single-flusher mutex sees to that).
_EDGES_PRESENT_ATTR = "_mokata_edges_present"


def _edges_present(conn: Any) -> bool:
    """Does the shared store carry the v5 edge table? Probed, memoized, and FAIL-CLOSED.

    **This is what "a v4 team degrades byte-identically" is made of (E3).** A v4 store has no
    `mokata_memory_edges`, so this returns False and `_project_edges_for` below does nothing at
    all: the flush issues exactly the statements it issued before DB.S7a, in the same order, with
    the same outcomes. Nothing about the CAS, the conflicts, the markers or the ledger moves.

    It must be a PROBE and not a try/except around the projection, and the reason is specific to
    Postgres: a statement that errors inside an open transaction ABORTS that transaction, so a
    "just try it and catch the missing table" projection would poison the approval group's
    transaction and turn a v4 store's every flush into a rollback. Asking first is the only shape
    that degrades instead of breaking.

    HONEST BOUNDARY of the per-connection memo, stated rather than left to be discovered: a process
    that probed a v4 store and cached False keeps that answer for the life of the connection, so if
    a TEAMMATE migrates the store to v5 underneath it, this process stops short of projecting until
    its connection is replaced. That is a lag, not a loss, and the reason it is acceptable is the
    same reason the projection can be derived at all — the inline doc fields are authoritative and
    still flush normally, and the next `team init` backfill re-derives every missing edge
    idempotently. Re-probing per entry would trade a real per-write round trip for a window that
    closes itself.
    """
    cached = getattr(conn, _EDGES_PRESENT_ATTR, None)
    if cached is not None:
        return bool(cached)
    try:
        row = conn.execute("SELECT to_regclass(%s)", (teamdb.EDGES_TABLE,)).fetchone()
        present = bool(row and row[0])
    except Exception as exc:
        # D5 — deliberately BROAD, deliberately fail-CLOSED, and LOUD. There is no narrower class
        # worth naming: an old server without `to_regclass`, a driver error and an injected double
        # that does not model the catalog all mean the same thing to this caller — we could not
        # establish that the table is there, and unknown is not permission.
        #
        # But it is NOT silent, and that distinction matters more here than the fallback does. A
        # genuinely v4 store answers this probe successfully with NULL and never reaches this
        # handler, so arriving here means something ELSE went wrong on a store that may well be v5
        # — and the consequence is a projection that quietly stops tracking its docs. The notice is
        # what makes that recoverable: it names the drift and the fix (`mokata team init`, whose
        # backfill re-derives the whole projection). Once per subsystem per process, so a flush of
        # fifty entries cannot turn one bad connection into fifty lines.
        note_degraded("memory-edges", FAILURE_SCHEMA, detail=str(exc),
                      fallback="edge projection skipped — the item write is unaffected",
                      fix="run `mokata team init` to (re-)provision and re-derive the edges")
        present = False
    # No try/except on the memoization, and that is a decision rather than an omission: psycopg3's
    # Connection is a plain class (no `__slots__` — checked against the live driver), so this cannot
    # fail for any connection mokata actually uses. A guard here would hide a genuinely novel
    # connection object behind a silent per-entry re-probe instead of surfacing it.
    setattr(conn, _EDGES_PRESENT_ATTR, present)
    return present


def _project_edges_for(conn: Any, entry: JournalEntry) -> None:
    """Maintain the edge projection for an entry whose compare-and-set JUST MATCHED.

    **CAS-guarded by CONSTRUCTION, not by a second CAS (E4).** Every call site below sits on the
    `rowcount > 0` branch — the branch reached only when Postgres itself decided this writer won
    the row. A losing writer returns a conflict several lines earlier and never arrives here, so
    two writers racing one item can no more produce two conflicting edge sets than they can produce
    two item rows. Inventing a revision column for edges would have added a second, weaker CAS over
    data that is derived from the first one's result.

    It also runs INSIDE whatever transaction the caller holds. For a multi-entry approval group
    that is the group's explicit BEGIN/COMMIT (I1), so a rollback takes the edges with it: an
    approval that does not land as a whole leaves no half of it behind, edges included.

    Skipped entirely when the entry carries no parseable doc — the projection is derived FROM the
    doc, so no doc means nothing to derive, not an error.
    """
    if not _edges_present(conn):
        return
    doc = _doc_mapping(entry.payload.get("doc"))
    if not doc:
        return
    doc = dict(doc)
    doc.setdefault("id", entry.payload.get("id") or entry.key)
    _edges.project_edges(conn, teamdb.EDGES_TABLE, doc, now=_item.now_iso(), placeholder="%s",
                         # C5 / P2 — the edge rows carry the SAME approval id the item write
                         # inherits, so the audit trail from a relation back to the human decision
                         # that created it is a column, not an inference.
                         approval_ledger_id=entry.ledger_id)


def _close_edges_for(conn: Any, entry: JournalEntry) -> None:
    """A gated PRUNE (`OP_DELETE`) removed the item — close its OPEN edges, never delete them.

    Never-delete applies to the projection too. The item row is gone (that is what a prune IS), but
    the relations it asserted were TRUE for a while, and a closed window says so honestly while a
    `DELETE FROM memory_edges` would erase the only record that they ever held. Same CAS guarantee
    as `_project_edges_for`: reached only after the revision-guarded DELETE matched a row.
    """
    if not _edges_present(conn):
        return
    now = _item.now_iso()
    for kind in _edges.WIRED_KINDS:
        sql, params = _edges.close_withdrawn_sql(teamdb.EDGES_TABLE, kind, (), placeholder="%s")
        conn.execute(sql, (now, entry.payload.get("id") or entry.key, kind, *params))


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
    # DB.S2b — the scope/precedence columns are projected from the entry's OWN `doc`, so a flushed
    # row lands in Postgres as faithful a projection as a locally-`put()` one. `_doc_mapping`
    # degrades to `{}` on an unparseable payload, which `scope_columns_from_doc` turns into the
    # item model's defaults (personal/''/false/0) — the narrowest scope, which leaks nothing.
    cols = (p.get("mtype"), p.get("subject"), p.get("status"), p.get("doc"), p.get("project"),
            *scope_columns_from_doc(_doc_mapping(p.get("doc"))))
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
            _close_edges_for(conn, entry)      # DB.S7a — the pruned item's edges CLOSE, never drop
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
            _project_edges_for(conn, entry)    # DB.S7a — the row landed; project its edges with it
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
        _project_edges_for(conn, entry)        # DB.S7a — only the CAS WINNER projects (E4)
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


class _GroupRollback(ControlSignal):
    """Raised INSIDE a group's transaction block to roll the whole group back. Never escapes
    `_apply_approval_group`; it exists only because a transaction context manager rolls back on an
    exception, and "one member conflicted" is not an error worth propagating to the caller.

    A `ControlSignal`, NOT a `MokataError`, and the base is load-bearing rather than decorative.
    This is not a failure being reported: nothing broke, nothing degraded, the compare-and-set did
    precisely its job and the human is about to be asked. Filing it under the error base meant a
    caller doing the one thing that base exists for — `except MokataError`, to catch a failure
    mokata DEFINED — would intercept a rollback signal mid-transaction and leave the group
    half-applied with no marker on any member. The signal base makes that unrepresentable; the D5
    sweep still sees the class, so opting out of the error taxonomy is a NAMED decision, not a gap.
    """


def _approval_groups(pend: List[JournalEntry]) -> List[List[JournalEntry]]:
    """Partition the pending entries into ATOMIC UNITS: the writes that one human approval
    authorised (DB.S6/I1).

    Why `ledger_id`, and why only an INT one. A gated store method that makes several durable
    writes — `apply_proposal` on a contradiction supersedes the old row AND updates the winner —
    computes `len(ledger)+1` for each of them inside ONE WriteGate hold, so they carry the SAME
    approval seq. That shared int IS the approval, which makes it the correct atomic boundary: one
    decision by one human should have one durable outcome, not two independent ones that can half
    land. Anything that is not an int is deliberately NOT grouped: `None` (no ledger) would collapse
    every unrelated write into one giant transaction, and `"floor-recovery"` (the recovery
    migration's shared marker) would do the same to a whole rescued corpus.

    JOURNAL ORDER IS PRESERVED EVERYWHERE — across groups and within them. Sorting a group by key
    was tempting (a fleet-wide lock order makes a cross-machine deadlock unrepresentable) and is
    deliberately NOT done: "flush order is journal order" is a contract MS.S5 pinned, and a
    reordering that only pays off in a rare cross-machine cycle is not worth quietly breaking it.
    The cycle is already handled safely without it — Postgres detects the deadlock and aborts one
    side, which reaches the flush as a mid-apply exception, and that path leaves every entry
    PENDING with no marker, so the next flush simply re-applies the group."""
    groups: Dict[Any, List[JournalEntry]] = {}
    order: List[Any] = []
    for i, e in enumerate(pend):
        key = _approval_key(e)
        key = key if key is not None else ("_solo", i)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    return [groups[key] for key in order]


def _approval_key(entry: JournalEntry) -> Optional[int]:
    """The approval this entry belongs to — an INT `ledger_id`, or None for "no group".

    Extracted so that the flush's partition (`_approval_groups`) and the resolver's membership
    question (`retire_without_replace_refusal`) cannot drift: "same approval" has to mean the same
    thing to the code that applies a group atomically and to the code that refuses to break one up
    by hand, or the guard would protect a boundary the flush does not actually use. `None` and
    `"floor-recovery"` deliberately do NOT group (see `_approval_groups`), and `bool` is excluded
    because `True == 1` would silently fold a flag into approval #1."""
    led = entry.ledger_id
    return led if isinstance(led, int) and not isinstance(led, bool) else None


# I1b — the payload statuses that TAKE a fact out of the active set. `superseded` is the one the
# heal path actually writes (`apply_proposal` on a contradiction sets it on `p.old`); the rest are
# listed because they are the other ways an item stops being live, and a status that retires a fact
# must not slip past a guard that only knew about one word for it.
_RETIRING_STATUSES = frozenset({"superseded", "stale", "rejected", "archived", "deleted"})

_KEPT_LOCAL = "kept-local"


def _retires_a_fact(entry: JournalEntry) -> bool:
    """Does landing this write REMOVE a fact from the active set? A delete always does; otherwise
    it is the plain `status` column the flush already writes — read from the payload rather than
    the embedded doc, because that column is what the shared row is actually SET to."""
    if entry.op == OP_DELETE:
        return True
    return str((entry.payload or {}).get("status") or "").lower() in _RETIRING_STATUSES


def retire_without_replace_refusal(journal: "TeamJournal", conflict_id: str,
                                   resolution: str) -> Optional[str]:
    """I1b — the FAIL-CLOSED guard on resolving ONE member of a rolled-back approval group.
    Returns the refusal text, or None to allow.

    WHY THIS EXISTS ON TOP OF I1. The group transaction makes the FLUSH all-or-nothing, so a heal
    can no longer half-land by accident. But a rolled-back group surfaces as N SEPARATE conflicts
    and the resolver settles them one prompt at a time — so the very end state the transaction
    prevents is still reachable by hand: approve the write that retires the old fact, discard (or
    simply never decide) the write that installs its replacement, and the subject is left with no
    active value at all. Atomicity at the flush, non-atomicity at the resolution.

    THE ONE DIRECTION IT CLOSES is retire-without-replace, because that is the one that LOSES a
    fact silently. Keeping both sides is the opposite failure — two active facts on one subject —
    and that is visible, reviewable, and already surfaced as a contradiction proposal; nothing is
    lost while it waits. So this refuses, and nothing more: it does not decide the group, re-order
    the prompts, or offer a group verdict. Deciding a whole approval in one prompt is the
    group-decision surface, and it is DB.S7/K2 — building half of it here would be worse than the
    gap, because a half-built group verdict is one a human would trust.

    ALLOWED, deliberately: the replacement is PENDING (re-queued by a `kept-local` resolution, or
    never conflicted — either way it is landing on the next flush) or already FLUSHED. REFUSED: it
    is still CONFLICT (nobody has decided), DROPPED (`kept-remote` — discarded), or BLOCKED (a
    secret means it will never publish). A member that was compacted away is a member that FLUSHED,
    which is why an absent sibling reads as "landed" rather than as "missing".

    Costs an extra pass when the human resolves in journal order — the retirement comes first, is
    refused, and lands after the replacement is decided. That is the intended shape of a minimal
    guard: it never loses a fact, and the ergonomics of one-prompt group resolution are DB.S7's."""
    if resolution != _KEPT_LOCAL:
        # `kept-remote` DROPS the local retirement, so the fact stays active in the shared row.
        # Only publishing the retirement can lose anything.
        return None
    entries, status, _conflicts, order = journal._replay()
    entry = entries.get(conflict_id)
    if entry is None or not _retires_a_fact(entry):
        return None
    key = _approval_key(entry)
    if key is None:
        return None
    members = [i for i in order if _approval_key(entries[i]) == key]
    if len(members) < 2:
        return None
    stranded = [(i, status.get(i)) for i in members
                if i != conflict_id and not _retires_a_fact(entries[i])
                and status.get(i) not in (_PENDING, _FLUSHED)]
    if not stranded:
        return None
    why = ", ".join(f"'{entries[i].key}' {_STRANDED_WORDS.get(s, 'is not going to land')}"
                    for i, s in stranded)
    return (f"refused: this write RETIRES '{entry.key}', and it is "
            f"{members.index(conflict_id) + 1} of {len(members)} writes approved together — but "
            f"its replacement {why}. Publishing the retirement on its own leaves the subject with "
            f"NO active fact at all, and nothing anywhere saying so. Nothing was changed; the "
            f"whole approval is still open — resolve them together, deciding the replacement "
            f"first.")


_STRANDED_WORDS = {_CONFLICT: "is still undecided", _DROPPED: "was discarded",
                   _BLOCKED: "is blocked — a secret was found in it"}


def duplicate_both_active_refusal(journal: "TeamJournal", conflict_id: str,
                                  resolution: str) -> Optional[str]:
    """DB.S7d — the MIRROR of `retire_without_replace_refusal`, and the direction DB.S6 left open.

    WHY IT WAS LEFT OPEN, AND WHY IT CLOSES NOW. I1b guards the resolution that LOSES a fact. This
    guards the one that DUPLICATES it: drop the retirement (`kept-remote`, so the teammate's row —
    still the old fact, still active — stands) while the replacement lands, and the subject carries
    two active facts from a single approval nobody decided as a whole. DB.S6 judged that benign
    because nothing is lost, and it was right that nothing is lost. But nothing-lost is not
    decided: the approval ends up half-settled, in silence, by a human who never saw its two halves
    as one thing. The reason it could not be refused THERE is that refusing it would have been a
    deadlock — there was no way to settle the group as a unit. `group_decision_refusal` +
    `TeamJournal.resolve_group` are that way, so the refusal now has an exit and can exist.

    BOTH ORDERS, because the human can arrive either way and it is always the SECOND decision that
    creates the duplicate: dropping the retirement while the replacement is already landing, or
    landing the replacement when the retirement was already dropped. Guarding one order only would
    leave a guard that fires on the ordering it was written for and waves the other one through.

    NARROW BY CONSTRUCTION, so the DB.S6 allowances survive: an UNDECIDED retirement is not a
    dropped one (nothing is duplicated yet — `test_the_replacement_side_is_never_blocked_by_the_
    guard` still passes), and keeping THEIRS on both sides duplicates nothing at all."""
    entries, status, _conflicts, order = journal._replay()
    entry = entries.get(conflict_id)
    if entry is None:
        return None
    key = _approval_key(entry)
    if key is None:
        return None
    members = [i for i in order if _approval_key(entries[i]) == key and i != conflict_id]
    if not members:
        return None
    retires = _retires_a_fact(entry)
    if resolution == _KEPT_LOCAL and not retires:
        # Landing a replacement. The duplicate exists iff its retiring sibling was DISCARDED — the
        # old fact will stay active on the shared row with this one beside it.
        other = [i for i in members
                 if _retires_a_fact(entries[i]) and status.get(i) == _DROPPED]
        dropped, kept = entry.key, (entries[other[0]].key if other else "")
    elif resolution != _KEPT_LOCAL and retires:
        # Discarding a retirement. The duplicate exists iff a replacement sibling IS going to land —
        # PENDING (re-queued by a `kept-local`, landing on the next flush) or already FLUSHED. Still
        # CONFLICT, DROPPED or BLOCKED means nothing is coming, so nothing is duplicated.
        other = [i for i in members
                 if not _retires_a_fact(entries[i]) and status.get(i) in (_PENDING, _FLUSHED)]
        dropped, kept = (entries[other[0]].key if other else ""), entry.key
    else:
        return None
    if not other:
        return None
    return (f"refused: this leaves TWO ACTIVE facts on one subject. Discarding the retirement of "
            f"'{kept}' keeps it live on the shared row, and '{dropped}' — approved in the SAME "
            f"group of {len(members) + 1} writes — lands beside it. Nothing is lost, but half the "
            f"approval has now been decided one way and half the other, with nothing recording "
            f"that. Nothing was changed: decide the WHOLE APPROVAL in one prompt instead, or keep "
            f"the retirement so the replacement actually replaces something.")


class _ProjectedJournal:
    """The journal as it WOULD replay if a set of resolutions had been committed.

    This is what lets a group verdict be checked by the very guards that police one-at-a-time
    resolution, instead of by a group-shaped re-derivation of them. Both guards ask their question
    of `journal._replay()`, and the replay is a pure function of the record list
    (`_replay_records`) — so "what would the state be after this verdict" is just the records plus
    the resolutions it would write. The guards are then run UNCHANGED against that.

    It matters that this is a projection and not a rewrite of the predicates: a member the verdict
    is about to settle must not read as stranding its siblings (every member of a `kept-local`
    verdict is PENDING in the projection, so none of them strands another), while a member settled
    in an EARLIER pass keeps whatever state that pass left it in — which is exactly the case a
    fresh group-shaped predicate would have been most likely to miss."""

    def __init__(self, journal: "TeamJournal",
                 decisions: Sequence[Tuple[str, str, Optional[int]]]) -> None:
        extra = [{"kind": "resolved", "id": eid, "resolution": res, "remote_revision": rev}
                 for eid, res, rev in decisions]
        self._state = journal._replay_records(list(journal._records()) + extra)

    def _replay(self):
        return self._state


def group_decision_refusal(journal: "TeamJournal",
                           decisions: Sequence[Tuple[str, str, Optional[int]]]) -> Optional[str]:
    """DB.S7d — every per-member guard, run against the state this whole-approval verdict WOULD
    produce, BEFORE any of it is committed. Returns the first refusal, or None to allow.

    THE POINT OF THIS FUNCTION IS WHAT IT DOES NOT DO. A one-prompt group verdict looks safe by
    construction — one decision for every member, so no member can strand another — and that
    reasoning is true only for members STILL CONFLICTED. A member settled in an earlier
    one-at-a-time pass is already outside the group's reach: discard the replacement on Monday, and
    Tuesday's "keep local for the whole approval" has one member left to decide, lands the
    retirement, and the fact is gone in a single prompt the human trusted precisely because it
    claimed to cover everything. DB.S6's docstring named this in advance: a half-built group
    verdict is worse than the gap it fills.

    So the group path runs `retire_without_replace_refusal` and `duplicate_both_active_refusal`
    THEMSELVES — the shipped functions, on the shipped predicates, resolved through the module
    globals so there is exactly one definition of each question in the codebase and no second copy
    to drift. All the group layer contributes is the state they are asked about."""
    projected = _ProjectedJournal(journal, decisions)
    for entry_id, resolution, _rev in decisions:
        for guard in (retire_without_replace_refusal, duplicate_both_active_refusal):
            refusal = guard(projected, entry_id, resolution)
            if refusal is not None:
                return refusal
    return None


def approval_group_conflicts(journal: "TeamJournal", conflict_id: str) -> List[str]:
    """The still-CONFLICTED entry ids sharing this conflict's approval, in journal order.

    Membership is `_approval_key`'s answer and nothing else, so the group a human DECIDES is the
    same group the flush APPLIES — the drift `_approval_key`'s own docstring exists to prevent. A
    conflict with no approval key (`None`, or the `floor-recovery` marker) is a group of ONE rather
    than an error: the surface must degrade to the single-member case, never refuse to open."""
    entries, status, _conflicts, order = journal._replay()
    entry = entries.get(conflict_id)
    if entry is None:
        return []
    key = _approval_key(entry)
    if key is None:
        return [conflict_id]
    return [i for i in order
            if _approval_key(entries[i]) == key and status.get(i) == _CONFLICT]


def _group_transaction(conn: Any) -> Any:
    """A transaction context manager for `conn`, or None if it cannot offer one.

    I1 — psycopg3's `conn.transaction()` works on an AUTOCOMMIT connection: it emits an explicit
    BEGIN/COMMIT around the block and leaves autocommit behaviour outside it untouched (measured
    on a live PG 16 before this was written). So mokata's connection posture does not have to
    change to get all-or-nothing, and the file locks the flush relies on — the single-flusher mutex
    and the append lock — are not DB-level constructs and are unaffected either.

    Returning None is the honest degrade for a connection object that has no `transaction` (an
    injected double). The caller then falls back to per-entry apply and reports any partial
    outcome LOUDLY, rather than pretending an atomicity it did not get."""
    factory = getattr(conn, "transaction", None)
    if not callable(factory):
        return None
    try:
        return factory()
    except Exception:  # pragma: no cover - a driver that has the name but not the behaviour
        return None


def _record_flushed(journal: TeamJournal, entry: JournalEntry, outcome: ApplyOutcome,
                    ledger: Any) -> None:
    journal.mark_flushed(entry.id, remote_revision=outcome.new_revision)
    if ledger is not None:
        # C5 / P2 — the flush INHERITS the original approval; record its ledger id so the audit
        # trail links deferred durability back to the human decision (no bypass). `already_applied`
        # keeps that trail HONEST about the M-5 case: this pass recognised the write as already
        # landed and marked it flushed, rather than applying it twice.
        ledger.record("team_flush", journal_id=entry.id, table=entry.table,
                      key=entry.key, actor=entry.actor,
                      approval_ledger_id=entry.ledger_id, revision=outcome.new_revision,
                      already_applied=outcome.already_applied,
                      reason=("already applied by another flush — marked flushed, NOT "
                              "re-applied (exactly-once, MS.S5)"
                              if outcome.already_applied
                              else "flush inherits the original human approval (P2)"))


def _apply_approval_group(conn: Any, journal: TeamJournal, group: List[JournalEntry], *,
                          ledger: Any, out: Optional[Callable[[str], None]],
                          do_scan: Callable[[JournalEntry], list]) -> tuple:
    """Apply ONE approval's writes. Returns `(flushed, conflicts, blocked, already_applied)`.

    A single-entry group is byte-identical to the pre-DB.S6 loop: same scan, same apply, same
    markers, same ledger record, and no transaction is opened at all.

    A MULTI-entry group is the case I1 exists for. `apply_proposal` on a contradiction issues two
    durable writes — retire the old fact, install the new one — under one human decision. Applied
    independently, the first can land and the second lose its CAS, which leaves the shared store
    with the old fact retired and the new one absent: the subject has NO active value, and nothing
    anywhere says so. That is silent fact-loss, and it is the reachable-by-accident kind.

    So the group is applied inside ONE transaction and any conflict rolls back ALL of it. The human
    then sees the whole approval as conflicted — N proposals carrying a detail that says they are
    one unit — instead of a half-applied heal nobody can see.

    A secret in ANY member blocks the WHOLE group: the offending entries are marked blocked and the
    rest are left PENDING (no marker, so the next flush retries them). Publishing the innocent half
    of a blocked approval would be the same partial apply by another route."""
    solo = len(group) == 1

    # --- secret scan first: nothing in a group publishes if any member is blocked.
    findings = {e.id: do_scan(e) for e in group}
    if any(findings.values()):
        blocked = 0
        for e in group:
            if findings[e.id]:
                journal.mark_blocked(
                    e.id, detail="blocked: secret detected in the payload — NOT published")
                blocked += 1
                if out:
                    out(f"⚠ blocked publish of {e.key}: secret detected "
                        f"(remove it, then re-sync)")
        if not solo and out:
            out(f"⚠ the other {len(group) - blocked} write(s) approved alongside it were NOT "
                f"published either — one approval lands as a unit (they stay pending)")
        return 0, 0, blocked, 0

    def _apply(entry: JournalEntry) -> Optional[ApplyOutcome]:
        try:
            return apply_memory_write(conn, entry)
        except Exception as exc:
            # D5 — the DB failed MID-APPLY (the statement, or `_read_remote`'s CAS-miss re-read,
            # which no longer lies about it). Broad by necessity: `conn` is a psycopg connection
            # and psycopg is an OPTIONAL extra, so its error class cannot be named at module scope
            # — and narrowing it wrong here would turn a transient DB blip into a CRASHED flush.
            #
            # The entry is simply left PENDING: we append NO marker, so the replay still reads it
            # as pending and the next healthy flush re-applies it (idempotently — MS.S5). That is
            # the ONLY safe outcome. Marking it flushed on a failed read is precisely the false
            # success this fix removes; marking it conflicted would invent a concurrent writer that
            # does not exist. Loud once per process, and `pending` stays true.
            note_degraded("team-flush", FAILURE_UNREACHABLE,
                          fallback="the entry stays PENDING — nothing is lost",
                          fix="run `mokata sync` once the connection is healthy",
                          detail=f"{type(exc).__name__}: {exc}")
            return None

    if solo:
        outcome = _apply(group[0])
        if outcome is None:
            return 0, 0, 0, 0
        if outcome.status == "ok":
            _record_flushed(journal, group[0], outcome, ledger)
            return 1, 0, 0, (1 if outcome.already_applied else 0)
        journal.mark_conflict(group[0].id, detail=outcome.detail, remote=outcome.remote)
        return 0, 1, 0, 0

    txn = _group_transaction(conn)
    if txn is None:
        return _apply_group_without_transaction(conn, journal, group, ledger=ledger, out=out,
                                                apply=_apply)

    outcomes: Dict[str, ApplyOutcome] = {}
    try:
        with txn:
            for entry in group:
                outcome = _apply(entry)
                if outcome is None or outcome.status != "ok":
                    outcomes[entry.id] = outcome or ApplyOutcome(
                        "conflict", detail="the database was unreachable mid-apply")
                    raise _GroupRollback
                outcomes[entry.id] = outcome
    except _GroupRollback:
        _mark_group_conflicted(conn, journal, group, outcomes, out=out)
        return 0, len(group), 0, 0

    already = 0
    for entry in group:
        _record_flushed(journal, entry, outcomes[entry.id], ledger)
        already += 1 if outcomes[entry.id].already_applied else 0
    return len(group), 0, 0, already


def _mark_group_conflicted(conn: Any, journal: TeamJournal, group: List[JournalEntry],
                           outcomes: Dict[str, ApplyOutcome], *,
                           out: Optional[Callable[[str], None]]) -> None:
    """Roll-back bookkeeping: EVERY member of the group is marked conflicted, including the ones
    whose statement succeeded before the rollback undid it.

    The remote state is re-read per member rather than reused from the outcome, because the members
    that "succeeded" have no remote to report and a `ConflictView` with `remote=None` renders the
    other writer's value as unreadable — which would be a worse conflict prompt than one extra
    SELECT costs. The detail names the group, so the human resolving these knows they belong to one
    approval and should be decided together."""
    n = len(group)
    for entry in group:
        own = outcomes.get(entry.id)
        try:
            remote = _read_remote(conn, entry.key)
        except Exception:
            # A re-read that fails after a rollback costs only the richness of the prompt; the
            # conflict marker itself must still land, or the entry would silently stay pending and
            # be retried forever against a row it can never win.
            remote = own.remote if own is not None else None
        detail = (own.detail if own is not None and own.status == "conflict"
                  else "rolled back with the rest of this approval")
        journal.mark_conflict(
            entry.id,
            detail=(f"{detail} — this write is 1 of {n} approved together and the whole approval "
                    f"was rolled back atomically (nothing partial was published); resolve all "
                    f"{n} together"),
            remote=remote)
    if out:
        out(f"⚠ an approval of {n} writes hit a conflict — ALL {n} were rolled back "
            f"(nothing partial was published). Resolve them together: `mokata sync`")


def _apply_group_without_transaction(conn: Any, journal: TeamJournal, group: List[JournalEntry], *,
                                     ledger: Any, out: Optional[Callable[[str], None]],
                                     apply: Callable[[JournalEntry], Optional[ApplyOutcome]]
                                     ) -> tuple:
    """The DETECT-AND-SURFACE fallback for a connection that cannot open a transaction.

    Every real deployment takes the prevention path — psycopg3 offers `transaction()` on an
    autocommit connection, which is measured, not assumed. This branch exists for an injected
    connection object that does not (a test double, an exotic adapter), and its contract is
    narrower and honest: it applies per entry exactly as the pre-DB.S6 flush did, and if the
    approval lands only PARTLY it says so LOUDLY through the degrade channel rather than returning
    a clean-looking verdict over a half-written heal."""
    flushed = conflicts = already = 0
    for entry in group:
        outcome = apply(entry)
        if outcome is None:
            continue
        if outcome.status == "ok":
            _record_flushed(journal, entry, outcome, ledger)
            flushed += 1
            already += 1 if outcome.already_applied else 0
        else:
            journal.mark_conflict(entry.id, detail=outcome.detail, remote=outcome.remote)
            conflicts += 1
    if flushed and (conflicts or flushed != len(group)):
        message = (f"{flushed} of {len(group)} writes from ONE approval landed — the rest did "
                   f"not. The shared store is in a PARTIAL state for this decision.")
        note_degraded("team-flush", FAILURE_PARTIAL_APPLY,
                      fallback=message,
                      fix=("run `mokata sync` and resolve the remaining conflicts, then re-check "
                           "`mokata memory` for this subject"),
                      detail="this connection could not open a transaction, so the approval could "
                             "not be applied atomically")
        if out:
            out(f"⚠ {message}")
    return flushed, conflicts, 0, already


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
    for group in _approval_groups(pend):
        f, c, b, a = _apply_approval_group(conn, journal, group, ledger=ledger, out=out,
                                           do_scan=do_scan)
        flushed += f
        conflicts += c
        blocked += b
        already += a

    # J-PERF — compact the dead history this flush (and every flush before it) left behind. Here,
    # at the END of a successful pass, is the one moment the journal is at its most settled: we are
    # the sole flusher (the mutex is held by our caller), every entry we could resolve has its
    # marker on disk, and no further append is coming from this pass. Past the threshold only — a
    # normal flush leaves the file untouched. Best-effort by construction: compaction is pure
    # housekeeping, so a failure to take the append lock (a stuck concurrent writer) must never turn
    # a SUCCESSFUL flush into an error. The dead lines simply stay and the next flush tries again.
    try:
        journal.compact_if_needed()
    except (LockTimeout, OSError):
        pass

    return FlushResult(flushed=flushed, conflicts=conflicts, blocked=blocked, already_applied=already,
                       pending=len(journal.pending()), skipped=False, verdict=verdict)


# --------------------------------------------------------------------------- sync (reconcile)
def _conflict_prompt(c: ConflictView) -> str:
    remote_rev = (c.remote or {}).get("revision")
    return (f"mokata · sync conflict on '{c.key}': {c.detail}\n"
            f"  your local write vs the remote version (revision {remote_rev}).\n"
            f"  Keep your LOCAL version (overwrite the remote)?  "
            f"[y = keep local / n = keep remote]")


def _ask_conflict(c: ConflictView, *, assume_yes: bool,
                  confirm: Optional[Callable[[str], bool]],
                  emit: Callable[[str], None]) -> str:
    """ASK the human which side wins → 'approve' (keep yours) | 'discard' (keep theirs) | 'defer'.

    DB.S6/R4 — this only ASKS. It settles nothing: the decision is handed to
    `MemoryStore.apply_proposal`, which is the ONE place a conflict's state actually changes. That
    split is the whole point of R4 — `sync` used to own a second, independent resolver, so
    resolving here and resolving through the healing path could drift apart.

    NEVER silently picks a winner: with no way to ask (non-interactive, no `confirm`) it DEFERS
    (leaves the entry conflicted) rather than last-writer-wins. The vocabulary is the healing
    path's, so the two entry points cannot disagree about what a word means."""
    emit(_conflict_prompt(c))
    if confirm is not None:
        return "approve" if confirm(_conflict_prompt(c)) else "discard"
    if assume_yes:
        return "defer"                         # can't decide safely without a human
    from .prompt import read_yes_no
    try:
        keep_local = read_yes_no(_conflict_prompt(c),
                                 "Keep your LOCAL version (overwrite the remote)?")
    except Exception:                          # non-interactive stdin → fail-closed to defer
        return "defer"
    return "approve" if keep_local else "discard"


def _group_prompt(group: List[ConflictView]) -> str:
    """DB.S7d — the ONE question for a whole rolled-back approval. It lists every member, for the
    same reason `render_group_decision` does: a single prompt that hides how many durable writes it
    covers is worse than the N honest prompts it replaces."""
    rows = "\n".join(f"    · '{c.key}': {c.detail}" for c in group)
    return (f"mokata · sync conflict — {len(group)} writes approved TOGETHER all lost their CAS:\n"
            f"{rows}\n"
            f"  They were one approval, so they are decided as one: keeping only some of them is "
            f"how a fact gets retired with nothing in its place, or duplicated on the shared row.\n"
            f"  Keep your LOCAL versions for ALL of them (overwrite the remote)?  "
            f"[y = keep local / n = keep remote]")


def _ask_group(group: List[ConflictView], *, assume_yes: bool,
               confirm: Optional[Callable[[str], bool]],
               emit: Callable[[str], None]) -> str:
    """ASK once for the whole approval → 'approve' | 'discard' | 'defer'. Settles nothing, exactly
    as `_ask_conflict` settles nothing: the decision is handed to `MemoryStore
    .apply_group_decision`, which is where the guards run and the state changes."""
    prompt = _group_prompt(group)
    emit(prompt)
    if confirm is not None:
        return "approve" if confirm(prompt) else "discard"
    if assume_yes:
        return "defer"                         # can't decide safely without a human
    from .prompt import read_yes_no
    try:
        keep_local = read_yes_no(prompt, "Keep your LOCAL versions for ALL of them?")
    except Exception:                          # non-interactive stdin → fail-closed to defer
        return "defer"
    return "approve" if keep_local else "discard"


def _conflict_groups(journal: "TeamJournal",
                     conflicts: List[ConflictView]) -> List[List[ConflictView]]:
    """Partition the conflicts into approvals, computed ONCE from the pre-resolution snapshot.

    Deriving it up front rather than per iteration matters: resolving one group rewrites the
    journal, and a membership question asked mid-loop would be answered against a state the
    partition was not built from."""
    by_id = {c.id: c for c in conflicts}
    groups: List[List[ConflictView]] = []
    seen: set = set()
    for c in conflicts:
        if c.id in seen:
            continue
        ids = [i for i in approval_group_conflicts(journal, c.id) if i in by_id] or [c.id]
        seen.update(ids)
        groups.append([by_id[i] for i in ids])
    return groups


def _conflict_resolver(surface: Any, ledger: Any) -> Callable[..., bool]:
    """R4 — the delegation seam: `(ConflictView, decision) -> committed?`, routed through
    `MemoryStore.apply_proposal`.

    `sync` no longer knows HOW a conflict is settled; it only knows who to ask and who to tell.
    The projection into plain fields happens where it belongs (the memory layer's team-writer
    boundary), so the same `CROSS_WRITER` proposal object drives both entry points — resolving via
    `sync` and resolving via `apply_proposal` are the same code, not two implementations that
    happen to agree today (I8).

    `assume_yes=True` on the store call is NOT a bypass of the human gate: the human has already
    been asked, one prompt above, exactly as `mokata memory edit` has done since Stage 54c. The
    WriteGate's SECRET hard-block still fires — approve cannot override a security block.

    Degrade-clean: if the store cannot be built at all (memory disabled, an unreachable backend),
    the conflict is simply not resolved and stays conflicted, which is the safe state and the one
    `sync` already reports as deferred."""
    from .memory.store import MemoryStore
    try:
        store = MemoryStore.from_surface(surface)
    except Exception as exc:
        # D5 — BROAD by necessity: `from_surface` composes the configured backend chain (SQLite,
        # Postgres via an OPTIONAL driver, the vault backend), so its raisable set spans classes
        # this module cannot name without depending on the optional extras. The fallback is the
        # SAFE direction and it is LOUD: every conflict stays conflicted and is counted as
        # deferred, so `sync` prints "some conflicts need your decision" rather than reporting a
        # clean pass over conflicts it silently could not touch.
        note_degraded("sync-conflicts", FAILURE_UNREACHABLE,
                      fallback="conflicts stay CONFLICTED — none were resolved",
                      fix="run `mokata doctor`, then `mokata sync` again",
                      detail=f"{type(exc).__name__}: {exc}")
        return lambda _c, _d: False

    def _resolve(c: ConflictView, decision: str, *, whole_group: bool = False) -> bool:
        # Looked up FRESH per conflict rather than snapshotted: resolving one conflict re-queues a
        # write and can flush, so a snapshot taken before the loop would carry stale remote
        # revisions into later decisions — and a stale revision is exactly what a CAS is for.
        proposal = next((p for p in store.cross_writer_proposals() if p.conflict_id == c.id), None)
        if proposal is None:                   # already resolved (a sibling window got there first)
            return False
        # DB.S7d — the group verdict is the SAME resolver, one method along: `apply_group_decision`
        # runs the identical guards and the identical gate, then commits every member in one
        # append. `sync` still settles nothing itself (R4).
        if whole_group:
            return bool(store.apply_group_decision(proposal, decision, assume_yes=True).changed)
        return bool(store.apply_proposal(proposal, decision, assume_yes=True).changed)

    return _resolve


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
    if conflicts:
        # R4 — ONE resolver. `sync` asks; the STORE settles. Built once, outside the loop, because
        # `apply_proposal` is the same gated path `mokata memory` drives, and a per-conflict store
        # would re-open the backend for every decision.
        resolve = _conflict_resolver(surface, ledger)
    # DB.S7d — conflicts are settled by APPROVAL, not one row at a time. A rolled-back approval
    # surfaces as N conflicts, and deciding them separately is what produced both half-decided end
    # states the guards refuse; it also cost an extra `mokata sync` pass, because the retirement
    # comes first in journal order and is refused until its replacement is decided. A group of one
    # takes the byte-identical single-conflict path below.
    for group in (_conflict_groups(journal, conflicts) if conflicts else []):
        whole_group = len(group) > 1
        c = group[0]
        decision = (_ask_group(group, assume_yes=assume_yes, confirm=confirm, emit=emit)
                    if whole_group
                    else _ask_conflict(c, assume_yes=assume_yes, confirm=confirm, emit=emit))
        if decision == "defer":
            deferred += len(group)
            continue
        if not resolve(c, decision, whole_group=whole_group):
            # The store refused (a guard, the gate declined, or a secret was found in the value
            # being re-published). Every entry stays CONFLICTED — which is the safe state — and the
            # next `detect_issues` surfaces them again. Counting them as deferred keeps the verdict
            # honest: nothing was resolved.
            deferred += len(group)
            continue
        if decision == "approve":
            resolved_local += len(group)
        else:
            resolved_remote += len(group)
        if ledger is not None:
            for member in group:
                ledger.record("team_sync_conflict", journal_id=member.id, key=member.key,
                              decision=("kept-local" if decision == "approve" else "kept-remote"),
                              remote_revision=(member.remote or {}).get("revision"),
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
