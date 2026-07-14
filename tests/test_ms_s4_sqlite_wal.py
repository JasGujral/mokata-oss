"""MS.S4 — SQLITE WAL + BUSY_TIMEOUT (M-4).

The bug this closes — as GROUNDED, not as originally stated. M-4's premise was that the store ran
with "no busy_timeout"; that is FALSE. Python's `sqlite3.connect(path)` takes `timeout=5.0` and
applies it as `busy_timeout=5000`, so mokata always had a 5s bound — an IMPLICIT, unowned one.

The live bug is the JOURNAL MODE. SQLite's default (`delete`, the rollback journal) needs an
EXCLUSIVE lock to commit, so a writer CANNOT commit while another process holds a read open: it
waits out its busy_timeout and then fails with exactly `sqlite3.OperationalError: database is
locked`. And a bigger busy_timeout is NOT the fix — it only makes the writer wait longer. WAL is:
readers never block the writer, so the write lands immediately (P22 — the second window stops being
the thing that breaks the first; P8 — local-first has to mean local-ROBUST).

These tests pin the fix:
  (a) M-4 regression: two REAL processes on one store — a child HOLDS a read open while the parent
      writes through `SQLiteBackend`. Under WAL the write lands immediately, with zero "database is
      locked". `test_rollback_journal_is_the_bug` pins the PRE-fix behaviour (the same scenario on
      the `delete` journal DOES raise "database is locked"), so (a) is a real RED/GREEN
      discriminator and not a test that passes either way. The broad multi-window stress harness is
      MS.S7's, deliberately not pre-empted here.
  (b) Factory coverage (grep-guard, like CM.S3's): every `sqlite3.connect` in `src/` goes through
      `_sqlite.connect_sqlite` — no second pragma path can be introduced without failing this.
  (c) Pragmas verified by querying them BACK off an opened connection: `journal_mode=wal` and the
      chosen `busy_timeout`. Plus: the two pragmas are the ONLY two (no `synchronous` durability
      trade smuggled in as "perf").
  (d) Degrade: a WAL-refusing filesystem (simulated faithfully — a REAL SQLite left on 'delete')
      still WORKS, and says so LOUDLY exactly ONCE per subsystem despite many per-op connects.
  (e) Sidecar hygiene: at rest (all connections closed) the store is a single, fully-checkpointed
      `memory.db` — no `-wal`/`-shm` left for a copier to miss; and the sidecars live inside the
      already-excluded `.mokata/` tree anyway.
  (f) Negatives: no schema change, single-window behaviour identical, notice leaks no path.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import multiprocessing as mp
import os
import re
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade
from mokata.memory import _sqlite
from mokata.memory._sqlite import BUSY_TIMEOUT_MS, SUBSYSTEM, connect_sqlite
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem

_CTX = mp.get_context("spawn")  # stable across POSIX/Windows; children re-import cleanly

# A connection-SETTING pragma statement — the thing that must exist ONLY in the factory. (A pragma
# that READS schema, like `PRAGMA table_info`, is deliberately not matched.)
_SETTING_PRAGMA = re.compile(r"PRAGMA\s+(journal_mode|busy_timeout|synchronous)", re.I)

# `SQLiteBackend.put`'s upsert, verbatim — so the pre-fix pin below measures the REAL write mokata
# issues, not a strawman.
_UPSERT = """INSERT INTO memory (id, mtype, subject, status, doc) VALUES (?, ?, ?, ?, ?)
             ON CONFLICT(id) DO UPDATE SET doc=excluded.doc"""

# A deliberately SHORT bound for the pre-fix pin: it is the journal MODE that decides whether the
# write can ever land, not the length of the wait — so a short bound shows the failure in 0.3s
# instead of making the suite sit through the real 10s one.
_SHORT_BOUND_MS = 300


# ----------------------------------------------------------------- spawn-safe worker functions
def _hold_reader(db_path, holding, release):
    """A concurrent window holding a read OPEN (an unfinished scan → a SHARED lock), via the
    factory. Under the rollback journal this lock is what a writer cannot commit past."""
    import _support  # noqa: F401  (child re-import: puts src/ on the path)
    from mokata.memory._sqlite import connect_sqlite

    conn = connect_sqlite(db_path)
    conn.execute("BEGIN")
    conn.execute("SELECT doc FROM memory").fetchone()   # lock taken, scan NOT finished
    holding.set()
    release.wait(timeout=30.0)
    conn.close()


def _hold_reader_bare(db_path, holding, release):
    """The same held reader on the PRE-MS.S4 connect path (bare `sqlite3.connect`) — used by the
    pre-fix pin, where routing the reader through the factory would flip the DB back to WAL and
    quietly destroy the very condition under test."""
    import sqlite3 as s

    conn = s.connect(db_path)
    conn.execute("BEGIN")
    conn.execute("SELECT doc FROM memory").fetchone()
    holding.set()
    release.wait(timeout=30.0)
    conn.close()


def _sidecars(d):
    return sorted(n for n in os.listdir(d) if n.endswith(("-wal", "-shm")))


class _NoWalConnection:
    """A REAL SQLite connection that REFUSES the WAL switch the way a network mount does: the
    `PRAGMA journal_mode=WAL` is answered with the previous mode ('delete'). Everything else is
    genuinely SQLite — so 'the store still works' is proven against a real store, not a mock."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args):
        if "journal_mode" in sql.lower():
            return self._real.execute("PRAGMA journal_mode=delete")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _RaisingWalConnection(_NoWalConnection):
    """The other WAL-unavailable shape: the filesystem cannot back the `-shm` index and SQLite
    RAISES rather than declining."""

    def execute(self, sql, *args):
        if "journal_mode" in sql.lower():
            raise sqlite3.OperationalError("disk I/O error")
        return self._real.execute(sql, *args)


def _no_wal(proxy=_NoWalConnection):
    """Patch the ONE factory's connect so every connection it hands out refuses WAL."""
    real = sqlite3.connect
    return mock.patch.object(_sqlite.sqlite3, "connect",
                             side_effect=lambda p, *a, **k: proxy(real(p, *a, **k)))


# ================================================================== (a) the M-4 regression
class TestM4Regression(unittest.TestCase):
    def _with_held_reader(self, db, worker):
        """Run `worker` in a REAL second process holding a read open on `db`, for the body's
        duration. Yields once the lock is genuinely taken (never a sleep-and-hope)."""
        holding, release = _CTX.Event(), _CTX.Event()
        p = _CTX.Process(target=worker, args=(db, holding, release))
        p.start()
        self.assertTrue(holding.wait(timeout=20.0), "the reader process never took its lock")
        return p, release

    def test_writer_survives_a_held_reader_in_another_process(self):
        # M-4, the real one: a second window holds a read OPEN while this one writes. On the
        # rollback journal the write cannot commit and dies with "database is locked" (pinned
        # below); under WAL it lands immediately.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            SQLiteBackend(db).close()          # first window: creates the schema, sets WAL
            p, release = self._with_held_reader(db, _hold_reader)
            try:
                backend = SQLiteBackend(db)
                t0 = time.time()
                try:
                    backend.put(MemoryItem.create("decision", "under a held reader", id="a"))
                except sqlite3.OperationalError as exc:      # the M-4 symptom
                    self.fail(f"M-4: the write failed against a concurrent reader: {exc}")
                elapsed = time.time() - t0
                backend.close()
                # Not merely "it eventually succeeded": under WAL the writer never WAITS on a
                # reader at all. A write that blocked would mean the rollback journal is back.
                self.assertLess(elapsed, 2.0,
                                f"the write BLOCKED on the reader for {elapsed:.2f}s "
                                "(rollback-journal behaviour — WAL must not block)")
            finally:
                release.set()
                p.join(timeout=20.0)
            self.assertEqual([i.id for i in SQLiteBackend(db).all()], ["a"], "the write was lost")

    def test_rollback_journal_is_the_bug(self):
        # The PRE-MS.S4 behaviour, PINNED — this is what makes the test above a discriminator
        # rather than a test that passes either way. The identical scenario on the `delete` journal
        # mokata used to ship DOES raise "database is locked".
        #
        # It also shows busy_timeout is NOT the fix: the writer here is given a bound and still
        # fails, because on this journal mode no bound lets it commit past a held reader — a bigger
        # number would only make it wait longer before failing. (Verified: at 10s it waits 8s and
        # only survives because the reader let go first.) WAL is the fix; the bound is a backstop.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            SQLiteBackend(db).close()
            bare = sqlite3.connect(db)         # force the DB back to the pre-fix journal mode
            bare.execute("PRAGMA journal_mode=delete")
            bare.commit()
            bare.close()

            p, release = self._with_held_reader(db, _hold_reader_bare)
            try:
                conn = connect_sqlite(db, busy_timeout_ms=_SHORT_BOUND_MS)
                with self.assertRaises(sqlite3.OperationalError) as cm:
                    conn.execute(_UPSERT, ("a", "decision", "s", "active", "{}"))
                    conn.commit()
                self.assertIn("database is locked", str(cm.exception).lower())
                conn.close()
            finally:
                release.set()
                p.join(timeout=20.0)

    def test_concurrent_windows_leave_no_sidecars_at_rest(self):
        # Sidecar hygiene: connections are per-operation, so the LAST close checkpoints and removes
        # -wal/-shm. At rest the store is one complete file — nothing for a copier to miss.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            b = SQLiteBackend(db)
            b.put(MemoryItem.create("decision", "x", id="x"))
            b.close()
            self.assertEqual(_sidecars(d), [], "WAL sidecars survived at rest")
            self.assertTrue(os.path.exists(db))


# ================================================================== (b) factory coverage
class TestOneFactory(unittest.TestCase):
    def test_every_sqlite3_connect_in_src_goes_through_the_factory(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata")
        factory = os.path.join(src, "memory", "_sqlite.py")
        offenders = []
        for base, _dirs, files in os.walk(src):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                with open(p, encoding="utf-8") as fh:
                    body = fh.read()
                if "sqlite3.connect(" in body and os.path.abspath(p) != os.path.abspath(factory):
                    offenders.append(os.path.relpath(p, src))
        self.assertEqual(offenders, [],
                         "sqlite3.connect outside the ONE factory (use _sqlite.connect_sqlite): "
                         f"{offenders}")

    def test_factory_is_the_only_pragma_path(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata")
        factory = os.path.join(src, "memory", "_sqlite.py")
        offenders = []
        for base, _dirs, files in os.walk(src):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                if os.path.abspath(p) == os.path.abspath(factory):
                    continue
                with open(p, encoding="utf-8") as fh:
                    body = fh.read()
                # Match an actual PRAGMA *statement*, not prose that merely names one — and only
                # the connection-SETTING pragmas. (`PRAGMA table_info`, backends.py's idempotent
                # ADD COLUMN probe, is a schema READ and is legitimately not the factory's job.)
                for m in _SETTING_PRAGMA.findall(body):
                    offenders.append(f"{os.path.relpath(p, src)}:{m}")
        self.assertEqual(offenders, [], f"a second pragma path exists: {offenders}")


# ================================================================== (c) pragmas verified
class TestPragmas(unittest.TestCase):
    def test_opened_connection_reports_wal_and_the_busy_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            conn = connect_sqlite(os.path.join(d, "m.db"))
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0],
                                 BUSY_TIMEOUT_MS)
            finally:
                conn.close()

    def test_busy_timeout_is_bounded_and_matches_the_oslock_bound(self):
        from mokata import oslock
        self.assertGreater(BUSY_TIMEOUT_MS, 0, "an unbounded/zero busy handler is not the fix")
        self.assertEqual(BUSY_TIMEOUT_MS, int(oslock.DEFAULT_TIMEOUT * 1000),
                         "the cross-process contention bound should be ONE number, not two")

    def test_backend_connections_carry_the_pragmas(self):
        # The backend's per-op connections (not just a direct factory call) get them too.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            SQLiteBackend(db).close()
            conn = sqlite3.connect(db)  # a RAW connection: journal_mode persists in the FILE
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            finally:
                conn.close()

    def test_durability_is_not_traded_for_speed(self):
        # `synchronous=NORMAL` is WAL's usual companion but it can lose the last committed
        # transaction on power loss — a durability trade, not correctness. It must NOT be set.
        with tempfile.TemporaryDirectory() as d:
            conn = connect_sqlite(os.path.join(d, "m.db"))
            try:
                self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)  # FULL
            finally:
                conn.close()

    def test_in_memory_db_is_not_a_degrade(self):
        # An in-memory DB is private to its connection: SQLite reports 'memory' and declines WAL.
        # That is BY DESIGN — it must not emit a degrade notice.
        seen, out = set(), []
        conn = connect_sqlite(":memory:", out=out.append, seen=seen)
        try:
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], BUSY_TIMEOUT_MS)
            self.assertEqual(out, [], "an in-memory DB must not report a WAL degrade")
        finally:
            conn.close()


# ================================================================== (d) degrade-clean
class TestWalUnavailableDegrade(unittest.TestCase):
    def test_wal_refused_still_works_and_is_loud_once(self):
        degrade.reset_degrade_notices()
        out = []
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            with _no_wal(), mock.patch.object(_sqlite, "_stderr", out.append):
                backend = SQLiteBackend(db)  # __init__ + put + get + all = MANY connects
                backend.put(MemoryItem.create("decision", "still works", id="a"))
                got = backend.get("a")
                allof = backend.all()
                backend.close()

            # the store STILL WORKS on the fallback journal mode
            self.assertIsNotNone(got)
            self.assertEqual(got.value, "still works")
            self.assertEqual([i.id for i in allof], ["a"])

            # The mode the FILE actually persisted, read on a RAW connection. Two reasons it sits
            # OUTSIDE `_no_wal()`: the proxy rewrites any `journal_mode` statement into a
            # `journal_mode=delete` SET, so probing through it answers its own question; and the
            # connection is closed EXPLICITLY, before the tempdir is torn down. A connection whose
            # lifetime is left to refcounting is not a closed one — on Windows the surviving handle
            # blocks the delete (WinError 32), which is exactly how this test failed.
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")
            finally:
                conn.close()

            # ...and said so LOUDLY, exactly ONCE, despite many connects
            self.assertEqual(len(out), 1, f"expected ONE notice, got {len(out)}: {out}")
            self.assertIn("DEGRADED", out[0])
            self.assertIn("delete", out[0])          # names the mode it fell back TO
            self.assertIn(SUBSYSTEM, out[0])
        degrade.reset_degrade_notices()

    def test_wal_raising_filesystem_also_degrades_cleanly(self):
        out, seen = [], set()
        with tempfile.TemporaryDirectory() as d, _no_wal(_RaisingWalConnection):
            conn = connect_sqlite(os.path.join(d, "m.db"), out=out.append, seen=seen)
            conn.execute("CREATE TABLE t(x)")  # a raising WAL pragma must not break the store
            conn.close()
        self.assertEqual(len(out), 1)
        self.assertIn("DEGRADED", out[0])

    def test_busy_timeout_still_applies_when_wal_is_unavailable(self):
        # The degrade is not a total loss: busy_timeout is journal-mode independent, so even a
        # WAL-less filesystem gets the BOUNDED wait instead of an instant "database is locked".
        out, seen = [], set()
        with tempfile.TemporaryDirectory() as d, _no_wal():
            conn = connect_sqlite(os.path.join(d, "m.db"), out=out.append, seen=seen)
            try:
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0],
                                 BUSY_TIMEOUT_MS)
            finally:
                conn.close()

    def test_notice_leaks_no_path(self):
        # Secret-safety: the notice names the subsystem + the fallback mode, never the store's
        # path (which would leak the user's directory layout).
        out, seen = [], set()
        with tempfile.TemporaryDirectory() as d, _no_wal():
            db = os.path.join(d, "memory.db")
            connect_sqlite(db, out=out.append, seen=seen).close()
        self.assertEqual(len(out), 1)
        self.assertNotIn(d, out[0])
        self.assertNotIn(db, out[0])
        self.assertNotIn("memory.db", out[0])

    def test_notice_follows_the_cm_s2_pattern(self):
        n = _sqlite._notice("delete", "journal_mode returned 'delete'")
        self.assertIsInstance(n, degrade.DegradeNotice)      # the CM.S2 notice type
        self.assertEqual(n.failure_class, degrade.FAILURE_WAL)
        self.assertEqual(n.class_label, "filesystem cannot enable SQLite WAL")
        self.assertEqual(n.env_name, "")                     # a LOCAL degrade: there is no DSN
        self.assertIn("[!]", n.render(ascii_only=True))      # ascii fallback, like CM.S2


# ================================================================== (f) negatives
class TestNoBehaviourChange(unittest.TestCase):
    def test_schema_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            SQLiteBackend(db).close()
            conn = sqlite3.connect(db)
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(memory)").fetchall()]
            finally:
                conn.close()
            self.assertEqual(cols, ["seq", "id", "mtype", "subject", "status", "doc",
                                    "scope_level", "scope_id", "pin", "priority"])

    def test_single_window_behaviour_identical(self):
        with tempfile.TemporaryDirectory() as d:
            b = SQLiteBackend(os.path.join(d, "memory.db"))
            b.put(MemoryItem.create("decision", "one", id="i1"))
            b.put(MemoryItem.create("decision", "two", id="i2"))
            b.put(MemoryItem.create("decision", "one-updated", id="i1"))   # upsert
            self.assertEqual(b.get("i1").value, "one-updated")
            self.assertEqual([i.id for i in b.all()], ["i1", "i2"])        # insertion order by seq
            self.assertTrue(b.delete("i2"))
            self.assertFalse(b.delete("nope"))
            self.assertEqual([i.id for i in b.all()], ["i1"])
            b.close()

    def test_in_memory_backend_still_round_trips(self):
        b = SQLiteBackend(":memory:")
        b.put(MemoryItem.create("decision", "mem", id="m1"))
        self.assertEqual(b.get("m1").value, "mem")
        b.close()


if __name__ == "__main__":
    unittest.main()
