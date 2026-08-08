"""MS.S5 — SINGLE-FLUSHER IDEMPOTENT SYNC (M-5 / doc 74 D3).

The bug: the flush had NO IDENTITY. Any process holding pending entries flushed them, so two Claude
Code windows on one repo (two OS processes, ONE shared journal file) both snapshotted the same
pending list and both applied it. The loser of the race hit a CAS miss on a row its own sibling had
just written and recorded a CONFLICT — a PHANTOM self-conflict: a scary "a concurrent writer changed
this row" warning about a write that had actually succeeded, plus a double-apply attempt underneath.

Two mechanisms close it, and these tests pin BOTH — separately, so neither can mask the other:

  (a) the single-flusher MUTEX (`oslock` sidecar beside the journal, non-blocking): two windows on
      one repo can no longer be inside the apply loop at once, and the pending set is re-read INSIDE
      the lock so a queued process never re-applies what the holder just flushed;
  (b) the IDEMPOTENT APPLY: a CAS miss whose remote row already holds this entry's exact end state
      is `already_applied` → marked flushed, never conflicted, never applied twice. This is what
      covers the cases the mutex CANNOT: a crash mid-flush (the un-acked entry replays), and two
      SEPARATE journals (worktrees) flushing overlapping writes to one shared DB.

The negative control matters as much as the fix: a genuinely divergent concurrent edit must STILL
surface as a conflict. A fix that eats real conflicts would pass every other test here.

The shared backend is a real file-backed SQLite standing in for the shared Postgres (there is no
live DB in CI) — real cross-process visibility and real CAS, which a dict-based fake cannot give.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import multiprocessing as mp
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager

import _support  # noqa: F401  (puts src/ on the path)
from _translating import (
    Declaration,
    ResultAdaptation,
    Rewrite,
    TranslatingConnection,
    placeholder_rewrite,
)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, flush_liveness, team_health, team_journal, teamdb
from mokata.config import Surface
from mokata.init import init_repo
from mokata.oslock import file_lock

_CTX = mp.get_context("spawn")   # stable across POSIX/Windows; children re-import cleanly


# ------------------------------------------------------------------ the shared "Postgres"
class _Cursor:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


SHARED_PG_DECLARATION = Declaration(
    suite="MS.S5 single-flusher (cross-process exactly-once)",
    reason="the M-5 bug is a CROSS-PROCESS one, so the backend has to be genuinely shared between "
           "processes and has to enforce CAS for real. An in-memory dict cannot do either; SQLite "
           "in WAL mode does both, and `total_changes` gives an exact rowcount rather than the "
           "driver-specific INSERT quirk.",
    rewrites=(
        placeholder_rewrite(),
        Rewrite.literal(
            "transaction-clock", "now()", "CURRENT_TIMESTAMP",
            "⚠ THE STRONGEST CONCESSION IN THIS FILE. Postgres's `now()` is TRANSACTION-stable — "
            "every statement in one transaction reads the same instant. SQLite's "
            "`CURRENT_TIMESTAMP` is STATEMENT-stable, and it is second-granular. Two writes this "
            "suite believes are distinguishable by their stamps may be indistinguishable in "
            "Postgres, and vice versa."),
    ),
    adaptations=(
        ResultAdaptation(
            "rowcount-from-total-changes",
            "psycopg reports `cursor.rowcount`; SQLite's is unreliable for the statements here, so "
            "the rowcount is derived from the connection's `total_changes` delta. CAS correctness "
            "is read from this number, so the adaptation is load-bearing, not cosmetic."),
    ),
    not_proven=(
        "Postgres's TRANSACTION semantics — WAL-mode SQLite in autocommit has neither Postgres's "
        "isolation levels nor its lock escalation, so 'the loser's UPDATE affected 0 rows' is "
        "proven for SQLite's concurrency model and not for Postgres's",
        "anything about the stamps themselves: `now()` is rewritten to a statement-stable, "
        "second-granular clock, so ordering or equality of timestamps is not evidence here",
        "the driver's own INSERT rowcount behaviour, which is the very quirk this stands in for",
    ),
)


class SharedPg(TranslatingConnection):
    """A file-backed stand-in for the shared `mokata_memory` table, speaking the flush's SQL.

    Deliberately NOT an in-memory dict: the M-5 bug is a CROSS-PROCESS one, so the backend has to be
    genuinely shared between processes and has to enforce CAS for real. SQLite in WAL mode does both
    — `ON CONFLICT DO NOTHING` and revision-guarded UPDATE/DELETE mean the same thing they do in
    Postgres, and `total_changes` gives an exact rowcount (never the driver-specific INSERT quirk).

    It TRANSLATES Postgres SQL, which went undeclared until 0.0.17 stage 2 derived it structurally
    — the name carries no `Shim` in it, so every name-based sweep had missed it. See
    `SHARED_PG_DECLARATION`, and note that the `now()` rewrite is a semantic substitution, not a
    placeholder swap."""

    def __init__(self, path, declaration=None):
        self.path = path
        super().__init__(
            declaration or SHARED_PG_DECLARATION,
            connection=sqlite3.connect(path, timeout=20.0, isolation_level=None))  # autocommit
        self._db = self._c
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=20000")
        self._db.execute(
            f"CREATE TABLE IF NOT EXISTS {teamdb.MEMORY_TABLE} ("
            "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT, status TEXT, doc TEXT,"
            f"  project TEXT, {teamdb.MEMORY_REVISION_COLUMN} INT NOT NULL DEFAULT 1,"
            f"  {teamdb.MEMORY_UPDATED_AT_COLUMN} TEXT,"
            # DB.S2b — the v3 scope/precedence columns. The flush's INSERT/UPDATE populate them
            # now (the journal is a memory write path like any other), so a stand-in for the
            # shared table has to carry them or it is modelling a schema this build won't talk to.
            f"  {teamdb.MEMORY_SCOPE_LEVEL_COLUMN} TEXT NOT NULL DEFAULT 'personal',"
            f"  {teamdb.MEMORY_SCOPE_ID_COLUMN} TEXT,"
            f"  {teamdb.MEMORY_PIN_COLUMN} INT NOT NULL DEFAULT 0,"
            f"  {teamdb.MEMORY_PRIORITY_COLUMN} INT NOT NULL DEFAULT 0)")

    def execute(self, sql, params=()):
        """Only the ROWCOUNT bookkeeping lives here; the translation is the base class's, by the
        declared rules and nothing else."""
        self._before_changes = self._db.total_changes
        return super().execute(sql, params)

    def _adapt(self, cursor):
        """The declared `rowcount-from-total-changes` adaptation."""
        return _Cursor(cursor.fetchall(),
                       rowcount=self._db.total_changes - self._before_changes)

    # --- assertions the tests make against the shared table ---------------------------
    def rows(self):
        cur = self._db.execute(
            f"SELECT id, doc, {teamdb.MEMORY_REVISION_COLUMN} FROM {teamdb.MEMORY_TABLE}")
        return {r[0]: {"doc": r[1], "revision": r[2]} for r in cur.fetchall()}

    def count(self, key):
        cur = self._db.execute(
            f"SELECT count(*) FROM {teamdb.MEMORY_TABLE} WHERE id=?", (key,))
        return cur.fetchone()[0]

    def plant(self, key, doc, revision=1):
        self._db.execute(
            f"INSERT INTO {teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project,"
            f" {teamdb.MEMORY_REVISION_COLUMN}) VALUES (?,?,?,?,?,?,?)",
            (key, "fact", key, "active", doc, "p1", revision))

    def close(self):
        self._db.close()


# ------------------------------------------------------------------ fixtures
@contextmanager
def _shared_pg(d):
    """The shared backend for one test: provisioned on entry, and CLOSED BEFORE `d` is removed.

    Ordering, not taste. These tests used `self.addCleanup(pg.close)`, and an addCleanup callback
    runs at TEARDOWN — i.e. AFTER the enclosing `with tempfile.TemporaryDirectory()` block has
    already deleted the tree. So the SQLite handle always outlived the directory holding its file.
    POSIX unlinks an open file happily (the leak was invisible for the whole life of this suite);
    Windows REFUSES, and the cleanup dies with `WinError 32: file in use by another process`.

    Entered alongside the tempdir in the SAME `with`, this closes first (context managers unwind
    LIFO), so the handle is deterministically gone before the rmtree — no retry, no swallowed
    error. Closing the LAST connection is also what makes SQLite checkpoint and remove the
    `-wal`/`-shm` sidecars, so there is nothing else of ours left in `d` to delete.
    """
    pg = SharedPg(os.path.join(d, "shared.db"))     # __init__ provisions the shared table
    try:
        yield pg
    finally:
        pg.close()


def _repo(d, mode="team"):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(d)


def _doc(key, value):
    return json.dumps({"id": key, "value": value})


def _payload(key, value):
    return {"id": key, "mtype": "fact", "subject": key, "status": "active",
            "doc": _doc(key, value), "project": "p1"}


def _seed(surface, key, value, *, base_revision=None, op=team_journal.OP_PUT):
    return team_journal.record_team_write(
        surface, op=op, table=teamdb.MEMORY_TABLE, key=key, payload=_payload(key, value),
        ledger_id=7, project="p1", actor="alice", base_revision=base_revision)


def _healthy():
    return team_health.HealthVerdict(team_health.HEALTHY, "reachable")


def _flush(surface, pg, **kw):
    return team_journal.flush(surface, health=_healthy(), connect=lambda *a, **k: pg, **kw)


def _journal(surface):
    return team_journal.TeamJournal.for_surface(surface)


# ------------------------------------------------------------------ child processes
def _child_flush(root, db, go, done):
    """A REAL second window: load the repo off disk and flush it to the shared table."""
    import _support  # noqa: F401
    from mokata import team_health as th, team_journal as tj
    from mokata.config import Surface as S

    surface = S.load(root)
    pg = SharedPg(db)
    go.wait(timeout=20.0)                      # both children enter the flush together
    res = tj.flush(surface, health=th.HealthVerdict(th.HEALTHY, "reachable"),
                   connect=lambda *a, **k: pg)
    pg.close()
    done.put({"flushed": res.flushed, "conflicts": res.conflicts, "skipped": res.skipped,
              "contended": res.contended, "already_applied": res.already_applied})


def _child_die_mid_flush(root, db, applied):
    """A flusher that CRASHES mid-flush: it takes the flush mutex, applies its entry to the shared
    table, and is killed BEFORE it can append the `flushed` marker — the exact window in which the
    journal and the DB disagree. The OS lock dies with the process (that is the point)."""
    import _support  # noqa: F401
    from mokata import team_journal as tj
    from mokata.config import Surface as S
    from mokata.oslock import file_lock as fl

    surface = S.load(root)
    journal = tj.TeamJournal.for_surface(surface)
    pg = SharedPg(db)
    with fl(journal.flush_lock_path):
        entry = journal.pending()[0]
        tj.apply_memory_write(pg, entry)       # the write LANDS...
        applied.set()                          # ...and now we die, un-acked, holding the lock.
        time.sleep(120)                        # parent SIGKILLs us here


# ================================================================== (1) the M-5 regression
class TestM5Regression(unittest.TestCase):
    def test_m5_regression_two_processes_overlapping_journals_apply_exactly_once(self):
        """TWO real processes, overlapping pending journals, ONE backend, flushing concurrently.

        Two repos (the worktree shape: separate journals, hence separate mutexes — so this exercises
        the IDEMPOTENT APPLY, not the lock) holding the SAME approved writes. Every entry must land
        EXACTLY once, no entry may be reported as a conflict, and BOTH journals must converge to
        flushed/empty. Pre-MS.S5 the loser of each race recorded a phantom conflict instead."""
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            roots = [os.path.join(d, "w1"), os.path.join(d, "w2")]
            keys = ["m1", "m2", "m3"]
            for root in roots:
                os.makedirs(root)
                surface = _repo(root)
                for k in keys:
                    _seed(surface, k, "v1")    # the SAME approved writes in both journals

            go, done = _CTX.Event(), _CTX.Queue()
            procs = [_CTX.Process(target=_child_flush, args=(root, pg.path, go, done))
                     for root in roots]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=60.0)
                self.assertEqual(p.exitcode, 0, "a flusher process died")

            results = [done.get(timeout=10.0) for _ in procs]
            self.assertEqual(sum(r["conflicts"] for r in results), 0,
                             "M-5: a sibling flush of the SAME write is not a conflict")

            rows = pg.rows()
            self.assertEqual(sorted(rows), keys, "every write landed")
            for k in keys:
                self.assertEqual(pg.count(k), 1, f"{k} was applied more than once")
                self.assertEqual(rows[k]["revision"], 1,
                                 f"{k} was re-applied on top of itself (revision bumped)")
                self.assertEqual(json.loads(rows[k]["doc"])["value"], "v1")

            for root in roots:                 # both journals converge: nothing left, nothing stuck
                j = _journal(Surface.load(root))
                self.assertEqual(j.pending(), [], f"{root}: entries left pending")
                self.assertEqual(j.conflicts(), [], f"{root}: PHANTOM conflict recorded")

    def test_two_windows_on_one_repo_serialise_on_the_flush_mutex(self):
        """The literal two-Claude-Code-windows case: ONE repo, ONE journal, two processes flushing at
        once. They share the mutex, so exactly one of them does the work and the other skips
        (contended) — every entry still applied exactly once, zero conflicts, journal drained."""
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            keys = ["m1", "m2", "m3", "m4"]
            for k in keys:
                _seed(surface, k, "v1")

            go, done = _CTX.Event(), _CTX.Queue()
            procs = [_CTX.Process(target=_child_flush, args=(d, pg.path, go, done))
                     for _ in range(2)]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=60.0)
                self.assertEqual(p.exitcode, 0, "a flusher process died")
            results = [done.get(timeout=10.0) for _ in procs]

            self.assertEqual(sum(r["conflicts"] for r in results), 0, "no phantom conflicts")
            self.assertEqual(sum(r["flushed"] for r in results), len(keys),
                             "each entry was flushed exactly once across the two windows")
            self.assertEqual(sorted(pg.rows()), keys)
            for k in keys:
                self.assertEqual(pg.count(k), 1)
                self.assertEqual(pg.rows()[k]["revision"], 1, "no double-apply")
            j = _journal(surface)
            self.assertEqual(j.pending(), [])
            self.assertEqual(j.conflicts(), [])


# ================================================================== (2) phantom self-conflict
class TestPhantomSelfConflict(unittest.TestCase):
    """Doc 74 D3, pinned dead: A flushes entry X, then B (still holding X pending in ITS journal)
    flushes. B must recognise X as already applied — flushed, no conflict, no duplicate row."""

    def _two_surfaces(self, d, *, op=team_journal.OP_PUT, base_revision=None, value="v1"):
        """The two repos only. The shared backend is owned by `_shared_pg`, which closes it before
        the tempdir is removed — a fixture that RETURNED an open handle could not."""
        out = []
        for name in ("a", "b"):
            root = os.path.join(d, name)
            os.makedirs(root)
            s = _repo(root)
            _seed(s, "m1", value, base_revision=base_revision, op=op)
            out.append(s)
        return out[0], out[1]

    def test_b_recognises_a_flushed_write_as_flushed_not_conflicted(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            a, b = self._two_surfaces(d)

            ra = _flush(a, pg)                                  # A flushes X
            self.assertEqual((ra.flushed, ra.conflicts), (1, 0))

            rb = _flush(b, pg)                                  # B flushes the SAME X, later
            self.assertEqual(rb.conflicts, 0,
                             "the PHANTOM self-conflict: B's write already landed — not a conflict")
            self.assertEqual(rb.flushed, 1, "B's entry is DONE, so it is flushed")
            self.assertEqual(rb.already_applied, 1, "B recognised it as already applied")

            self.assertEqual(pg.count("m1"), 1, "no duplicate row")
            self.assertEqual(pg.rows()["m1"]["revision"], 1, "not applied a second time")
            jb = _journal(b)
            self.assertEqual(jb.conflicts(), [], "no conflict warning was raised at all")
            self.assertEqual(jb.pending(), [], "B's journal converged to flushed")

    def test_an_already_applied_update_is_flushed_not_conflicted(self):
        # The same phantom on the UPDATE path: A's revision-guarded update lands (rev 1 → 2), so B's
        # identical update CAS-misses against base 1 — but the row already says what B wanted.
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            a, b = self._two_surfaces(d, op=team_journal.OP_UPDATE, base_revision=1, value="v2")
            pg.plant("m1", _doc("m1", "v0"), revision=1)

            ra = _flush(a, pg)
            self.assertEqual((ra.flushed, ra.conflicts), (1, 0))
            self.assertEqual(pg.rows()["m1"]["revision"], 2)

            rb = _flush(b, pg)
            self.assertEqual((rb.flushed, rb.conflicts, rb.already_applied), (1, 0, 1))
            self.assertEqual(pg.rows()["m1"]["revision"], 2, "B did not re-apply on top of A")
            self.assertEqual(json.loads(pg.rows()["m1"]["doc"])["value"], "v2")
            self.assertEqual(_journal(b).conflicts(), [])

    def test_an_already_applied_delete_is_flushed_not_conflicted(self):
        # And on the DELETE path: A's CAS-delete removes the row, so B's identical delete misses —
        # but the row is already GONE, which is exactly the end state B was approved to produce.
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            a, b = self._two_surfaces(d, op=team_journal.OP_DELETE, base_revision=1)
            pg.plant("m1", _doc("m1", "v0"), revision=1)

            ra = _flush(a, pg)
            self.assertEqual((ra.flushed, ra.conflicts), (1, 0))
            self.assertEqual(pg.count("m1"), 0)

            rb = _flush(b, pg)
            self.assertEqual((rb.flushed, rb.conflicts, rb.already_applied), (1, 0, 1))
            self.assertEqual(pg.count("m1"), 0)
            self.assertEqual(_journal(b).conflicts(), [])


# ================================================================== (3) real conflicts stay real
class TestRealConflictsStillSurface(unittest.TestCase):
    """The negative control. The fix distinguishes "already applied" from "someone else changed it"
    by CONTENT, so a genuinely divergent concurrent edit must still surface as a conflict — a fix
    that quietly ate true conflicts would pass every other test in this file."""

    def test_a_divergent_concurrent_update_still_conflicts(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            pg.plant("m1", _doc("m1", "theirs"), revision=2)    # a real concurrent writer
            _seed(surface, "m1", "mine", base_revision=1)       # we based on the OLD revision

            res = _flush(surface, pg)
            self.assertEqual(res.conflicts, 1, "a REAL lost update must still surface")
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.already_applied, 0)
            self.assertEqual(json.loads(pg.rows()["m1"]["doc"])["value"], "theirs",
                             "the other writer's value is NOT overwritten")
            self.assertEqual(len(_journal(surface).conflicts()), 1,
                             "it is queued for the human gate in `mokata sync`")

    def test_a_divergent_concurrent_create_still_conflicts(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            pg.plant("m1", _doc("m1", "theirs"), revision=1)
            _seed(surface, "m1", "mine")                        # believed-new, but they got there

            res = _flush(surface, pg)
            self.assertEqual((res.conflicts, res.flushed), (1, 0))
            self.assertEqual(json.loads(pg.rows()["m1"]["doc"])["value"], "theirs")

    def test_a_divergent_concurrent_change_still_blocks_a_delete(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            pg.plant("m1", _doc("m1", "theirs"), revision=3)    # they changed it under us
            _seed(surface, "m1", "mine", base_revision=1, op=team_journal.OP_DELETE)

            res = _flush(surface, pg)
            self.assertEqual((res.conflicts, res.flushed), (1, 0))
            self.assertEqual(pg.count("m1"), 1, "the row SURVIVES — never a silent hard-delete")


# ================================================================== (4) mutex honesty
class _SpyConnect:
    """Records whether the flush reached out at all."""

    def __init__(self, pg):
        self.pg, self.calls = pg, 0

    def __call__(self, *_a, **_k):
        self.calls += 1
        return self.pg


class TestMutexHonesty(unittest.TestCase):
    def test_a_contended_flush_skips_without_connecting_or_probing(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            _seed(surface, "m1", "v1")
            _seed(surface, "m2", "v1")
            spy = _SpyConnect(pg)
            probes = []

            with file_lock(_journal(surface).flush_lock_path):     # another window is flushing
                res = team_journal.flush(surface, connect=spy,
                                         probe=lambda dsn: probes.append(dsn))

            self.assertTrue(res.skipped and res.contended)
            self.assertEqual(spy.calls, 0, "a contended flush must NOT connect")
            self.assertEqual(probes, [], "a contended flush must NOT probe either — it skips flat")
            self.assertEqual(res.pending, 2, "the pending count stays honest")
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.conflicts, 0)
            self.assertEqual(len(_journal(surface).pending()), 2, "the entries are untouched")
            self.assertEqual(pg.rows(), {}, "nothing was written behind the holder's back")

    def test_a_contended_skip_does_not_escalate_the_backoff(self):
        # CM.S4 coherence: a contended skip is not a FAILURE. It must not count an attempt, record a
        # failure class, or push out `next_retry_at` — the DB was never even tried, and the backlog
        # is at that moment being drained by the holder.
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            _seed(surface, "m1", "v1")

            with file_lock(_journal(surface).flush_lock_path):
                res = flush_liveness.flush_with_liveness(
                    surface, health=_healthy(), connect=lambda *a, **k: pg)

            self.assertTrue(res.contended)
            state = flush_liveness.load_state(surface)
            self.assertEqual(state.attempts, 0, "a contended skip is not a failed attempt")
            self.assertEqual(state.next_retry_at, 0.0, "no backoff was scheduled")
            self.assertEqual(state.last_failure_class, "", "nothing failed, so nothing to classify")
            self.assertFalse(state.capped)

            status = flush_liveness.pending_status(surface)
            self.assertEqual(status.pending, 1, "the pending count is still honest")

            # ...and once the holder is gone, the very next touchpoint flushes normally.
            res2 = flush_liveness.flush_with_liveness(
                surface, health=_healthy(), connect=lambda *a, **k: pg)
            self.assertFalse(res2.skipped)
            self.assertEqual(res2.flushed, 1)
            self.assertEqual(pg.count("m1"), 1)

    def test_an_unhealthy_skip_still_escalates_the_backoff(self):
        # The other half of the discrimination: a REAL failure must still engage CM.S4's machinery
        # exactly as before (the contended path must not have flattened it).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, "m1", "v1")
            down = team_health.HealthVerdict(team_health.OFFLINE, "connection refused")

            res = flush_liveness.flush_with_liveness(surface, health=down,
                                                     connect=lambda *a, **k: None)
            self.assertTrue(res.skipped)
            self.assertFalse(res.contended)
            state = flush_liveness.load_state(surface)
            self.assertEqual(state.attempts, 1, "a real failure DOES count an attempt")
            self.assertGreater(state.next_retry_at, 0.0, "and DOES schedule a backoff")


# ================================================================== (5) crash mid-flush
class TestCrashMidFlush(unittest.TestCase):
    def test_a_flusher_killed_holding_the_mutex_leaves_no_stale_lock_and_no_double_apply(self):
        """The flusher dies (SIGKILL) after its write LANDED but before it could mark the entry
        flushed — holding the mutex. Two things must hold: the OS lock dies with the process (no
        stale lock file to reap, no hang), and the next flush re-applies the un-acked entry
        IDEMPOTENTLY — exactly-once survives the crash, with no phantom conflict."""
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            _seed(surface, "m1", "v1")

            applied = _CTX.Event()
            child = _CTX.Process(target=_child_die_mid_flush, args=(d, pg.path, applied))
            child.start()
            self.assertTrue(applied.wait(timeout=30.0), "the child never applied its write")
            child.kill()                                   # SIGKILL, mid-flush, holding the lock
            # The KILLED child's handles (its SQLite connection, the flush mutex fd) die with it —
            # the OS reclaims them, and `join` does not return until that teardown is done. So the
            # only handle that can outlive this block is the PARENT's, which `_shared_pg` owns.
            child.join(timeout=30.0)

            self.assertEqual(pg.count("m1"), 1, "the crashed flusher's write DID land")
            j = _journal(surface)
            self.assertEqual(len(j.pending()), 1, "...but it never got acked — it replays as pending")

            res = _flush(surface, pg)                      # the next process picks it up
            self.assertEqual(res.skipped, False, "the dead process's lock did NOT survive it")
            self.assertEqual(res.conflicts, 0, "the un-acked entry is not a conflict")
            self.assertEqual((res.flushed, res.already_applied), (1, 1),
                             "it is recognised as already applied and marked flushed")
            self.assertEqual(pg.count("m1"), 1, "and it was NOT applied a second time")
            self.assertEqual(pg.rows()["m1"]["revision"], 1)
            self.assertEqual(j.pending(), [])
            self.assertEqual(j.conflicts(), [])


# ================================================================== (6) no behaviour change
class TestSingleWindowUnchanged(unittest.TestCase):
    def test_a_single_window_flush_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            for k in ("m1", "m2"):
                _seed(surface, k, "v1")

            res = _flush(surface, pg)
            self.assertEqual((res.flushed, res.conflicts, res.blocked), (2, 0, 0))
            self.assertFalse(res.skipped or res.contended)
            self.assertEqual(res.already_applied, 0, "nothing was pre-applied in a single window")
            self.assertEqual(res.pending, 0)
            self.assertEqual(sorted(pg.rows()), ["m1", "m2"])

    def test_flush_order_is_journal_order(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            order = ["m3", "m1", "m2"]
            for k in order:
                _seed(surface, k, "v1")
            seen = []
            real = team_journal.apply_memory_write

            def spy(conn, entry):
                seen.append(entry.key)
                return real(conn, entry)

            team_journal.apply_memory_write = spy
            try:
                _flush(surface, pg)
            finally:
                team_journal.apply_memory_write = real
            self.assertEqual(seen, order, "batching/ordering is untouched")

    def test_nothing_to_flush_takes_no_lock(self):
        # The common single-window call: an empty journal never reaches the mutex at all, so the hot
        # path costs exactly what it did pre-MS.S5.
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            with file_lock(_journal(surface).flush_lock_path):      # even while "held"
                res = _flush(surface, pg)
            self.assertEqual(res.reason, "nothing to flush")
            self.assertFalse(res.skipped or res.contended)

    def test_local_mode_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="local")
            self.assertIsNone(_seed(surface, "m1", "v1"), "local mode has no team write path")
            self.assertFalse(os.path.exists(_journal(surface).path))
            self.assertFalse(os.path.exists(_journal(surface).flush_lock_path),
                             "and no flush mutex is ever created")

    def test_the_approval_id_still_rides_every_flush_including_an_idempotent_one(self):
        # C-5: deferred durability inherits the original human approval. That must hold on the new
        # already-applied path too — a write recognised as landed is still ledgered to its approval.
        class _Ledger:
            def __init__(self):
                self.records = []

            def record(self, kind, **fields):
                self.records.append((kind, fields))

        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            a_root, b_root = os.path.join(d, "a"), os.path.join(d, "b")
            for root in (a_root, b_root):
                os.makedirs(root)
                _seed(_repo(root), "m1", "v1")

            _flush(Surface.load(a_root), pg)                       # A lands it
            led = _Ledger()
            _flush(Surface.load(b_root), pg, ledger=led)           # B recognises it

            flushes = [f for k, f in led.records if k == "team_flush"]
            self.assertEqual(len(flushes), 1)
            self.assertEqual(flushes[0]["approval_ledger_id"], 7,
                             "the original approval id still rides the flush (C-5)")
            self.assertTrue(flushes[0]["already_applied"],
                            "and the audit trail says it was already applied, not re-applied")


# ================================================================== (7) secret-safety
class TestSecretSafety(unittest.TestCase):
    def test_the_contended_notice_leaks_nothing(self):
        # The one new user-visible surface is the skip notice. It must carry no path, no DSN, and no
        # memory content — it says only that another window holds the flush.
        text = team_journal.CONTENDED_REASON
        for leak in ("/", "\\", "postgres", "dsn", "password", "@"):
            self.assertNotIn(leak, text.lower(), f"the skip notice leaks {leak!r}")

    def test_the_secret_scan_still_blocks_a_publish_under_the_mutex(self):
        with tempfile.TemporaryDirectory() as d, _shared_pg(d) as pg:
            surface = _repo(d)
            _seed(surface, "m1", "v1")
            res = _flush(surface, pg, scan=lambda _e: ["a secret"])
            self.assertEqual((res.blocked, res.flushed), (1, 0))
            self.assertEqual(pg.rows(), {}, "a secret payload is NOT published")


if __name__ == "__main__":
    unittest.main()
