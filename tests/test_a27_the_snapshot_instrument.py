"""A27 — the "nothing was written" instrument, graded on a HOSTILE build.

`tests/_store_snapshot.py` replaced a whole-tree BYTE snapshot with one that drops SQLite's WAL
sidecars and reads each store's committed content instead. That trade is only safe if the half it
adds really does catch what the half it drops used to: a write that lands in an un-checkpointed
`-wal` moves NO byte of `m.db`, and an exclusion on its own would be blind to it. The comment this
module replaced named that trap and then walked into a different one; this file is the guard that
the trap stays named.

⭐ EVERY TEST HERE ARRANGES ITS OWN SIDECARS by HOLDING A CONNECTION OPEN. On a build whose clean
last-close unlinks `-wal`/`-shm` (Debian sqlite 3.37) they would otherwise never exist, and every
assertion below would pass by describing a machine instead of the instrument — which is the exact
defect, in the exact subsystem, that this file exists because of. A held connection reproduces the
persisted-sidecar state on EVERY build, so these grade the code everywhere.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _store_snapshot as S


def _seed(path):
    """A store with one row, committed and checkpointed, with NO connection left open."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(id TEXT PRIMARY KEY, doc TEXT)")
    conn.execute("INSERT INTO t VALUES ('a', 'first')")
    conn.commit()
    conn.close()
    return path


def _sidecars(d):
    return sorted(n for n in os.listdir(d) if S.is_sidecar(n))


def _bytes_only(root):
    """What the snapshot would be if it had ONLY dropped the sidecars and stopped there — the
    half-repair the old comment warned about. Used to PROVE the content half is load-bearing."""
    return {k: v for k, v in S.tree_snapshot(root).items() if not k.endswith(S.CONTENT_SUFFIX)}


class TheSidecarsAreNotEvidence(unittest.TestCase):

    def test_sidecars_exist_at_all_in_this_arrangement(self):
        # FIXTURE CHECK. If holding a connection open did not produce sidecars, every test in this
        # class would be grading an empty arrangement.
        with tempfile.TemporaryDirectory() as d:
            held = sqlite3.connect(_seed(os.path.join(d, "m.db")))
            try:
                held.execute("SELECT * FROM t").fetchall()
                self.assertTrue(_sidecars(d), "no sidecars — this suite would grade nothing")
            finally:
                held.close()

    def test_a_pure_READ_reports_no_write_even_with_sidecars_live(self):
        # The 0.0.20 failure, reproduced on any build: nine registered READ charters were reported
        # as writes because opening a connection created `m.db-shm`.
        with tempfile.TemporaryDirectory() as d:
            path = _seed(os.path.join(d, "m.db"))
            held = sqlite3.connect(path)
            try:
                held.execute("SELECT * FROM t").fetchall()
                before = S.tree_snapshot(d)
                reader = sqlite3.connect(path)
                reader.execute("SELECT * FROM t").fetchall()
                reader.close()
                self.assertEqual([], S.changed(before, S.tree_snapshot(d)))
            finally:
                held.close()

    def test_a_sidecar_appearing_from_nothing_is_not_a_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = _seed(os.path.join(d, "m.db"))
            before = S.tree_snapshot(d)          # taken with NO sidecars present
            held = sqlite3.connect(path)
            try:
                held.execute("SELECT * FROM t").fetchall()
                self.assertTrue(_sidecars(d), "fixture: sidecars should now exist")
                self.assertEqual([], S.changed(before, S.tree_snapshot(d)))
            finally:
                held.close()


class TheContentHalfIsLoadBearing(unittest.TestCase):

    def test_a_write_buffered_in_the_WAL_is_still_caught(self):
        # ⭐ THE ONE THAT MATTERS. A committed INSERT through a connection that is never closed sits
        # in the `-wal`. Dropping the sidecars and stopping there — the half-repair — would report
        # NOTHING. The committed-content read is what sees it.
        with tempfile.TemporaryDirectory() as d:
            path = _seed(os.path.join(d, "m.db"))
            held = sqlite3.connect(path)
            try:
                held.execute("SELECT * FROM t").fetchall()
                before, before_bytes = S.tree_snapshot(d), _bytes_only(d)
                held.execute("INSERT INTO t VALUES ('b', 'buffered')")
                held.commit()                      # COMMITTED, and not checkpointed
                after, after_bytes = S.tree_snapshot(d), _bytes_only(d)

                self.assertIn("m.db (committed content)", S.changed(before, after),
                              "a committed write was invisible to the snapshot")
                # And the demonstration that the content half is not decoration: with only the
                # exclusion, this write leaves no trace at all.
                self.assertEqual(
                    {}, {k: v for k, v in after_bytes.items() if before_bytes.get(k) != v},
                    "expected the bytes-only half to MISS this write — if it caught it, this test "
                    "no longer proves the content read is load-bearing and must be re-armed")
            finally:
                held.close()

    def test_an_ordinary_file_write_is_still_caught(self):
        with tempfile.TemporaryDirectory() as d:
            _seed(os.path.join(d, "m.db"))
            before = S.tree_snapshot(d)
            with open(os.path.join(d, "ledger.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("{}\n")
            self.assertEqual(["ledger.jsonl"], S.changed(before, S.tree_snapshot(d)))

    def test_a_checkpointed_write_is_caught_by_BOTH_halves(self):
        with tempfile.TemporaryDirectory() as d:
            path = _seed(os.path.join(d, "m.db"))
            before = S.tree_snapshot(d)
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO t VALUES ('c', 'durable')")
            conn.commit()
            conn.close()
            moved = S.changed(before, S.tree_snapshot(d))
            self.assertIn("m.db (committed content)", moved)
            self.assertIn("m.db", moved)


class TheInstrumentSaysWhatItCannotSee(unittest.TestCase):

    def test_an_unreadable_store_is_DISTINCT_from_an_empty_one(self):
        # §7g. A store that cannot be read must not be representable as a store with no rows, or a
        # corruption mid-test reads as "nobody wrote anything".
        with tempfile.TemporaryDirectory() as d:
            good = S.db_content(_seed(os.path.join(d, "m.db")))
            junk = os.path.join(d, "broken.db")
            with open(junk, "wb") as fh:
                fh.write(b"SQLite format 3\x00" + b"\x00" * 64)   # header, then nonsense
            bad = S.db_content(junk)
            self.assertTrue(bad.startswith((b"UNREADABLE:", b"UNOPENABLE:")), bad[:60])
            self.assertNotEqual(bad, good)
            self.assertNotEqual(bad, repr([]).encode(), "an unreadable store read as an empty one")

    def test_a_store_is_found_by_HEADER_and_not_by_extension(self):
        with tempfile.TemporaryDirectory() as d:
            odd = _seed(os.path.join(d, "store.sqlite3"))
            self.assertTrue(S.looks_like_sqlite(odd))
            plain = os.path.join(d, "notes.db")
            with open(plain, "w", encoding="utf-8") as fh:
                fh.write("not a database")
            self.assertFalse(S.looks_like_sqlite(plain),
                             "a text file named .db must not be read as a store")
            self.assertIn("store.sqlite3" + S.CONTENT_SUFFIX, S.tree_snapshot(d))

    def test_the_content_key_cannot_collide_with_a_real_file(self):
        # The marker has to be un-nameable on disk, or a file could impersonate a content row.
        self.assertIn("\x00", S.CONTENT_SUFFIX)

    def test_without_sidecars_keeps_everything_else(self):
        self.assertEqual(["m.db", "src"],
                         S.without_sidecars(["m.db", "m.db-wal", "m.db-shm", "src"]))


if __name__ == "__main__":
    unittest.main()
