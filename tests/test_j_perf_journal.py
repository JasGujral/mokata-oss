"""J-PERF — JOURNAL READ-PATH CACHE + COMPACTION (doc 84 §7; doc 86 #19).

Verified from code (2026-07-17, re-verified this stage): every team-mode read replayed the WHOLE
journal. `_records()` re-opened and json-parsed the file per call and `_replay()` rebuilt from it,
and `pending()` / `pending_count()` / `conflicts()` / `blocked()` EACH called `_replay()` — so one
`flush()` replayed three times (the unlocked pre-check, the snapshot inside the mutex, and the
final `pending` count) and the memory overlay replayed once per read. The file is append-only and
never compacted, so flushed markers accumulate and every read gets slower for the life of the repo.
The replay loop also carried an O(n²): `if rid not in order` is a linear scan of a growing list,
run once per record.

WHAT THESE TESTS PIN
  * the cache HITS — one parse across `pending` + `conflicts` + `blocked` at one file state;
  * the cache is not a stale layer — it re-STATS every access, so an append by ANOTHER writer (and
    by a second `TeamJournal` instance, the two-process shape) is seen on the very next read;
  * compaction is thresholded, state-preserving, byte-preserving, atomic under fault injection, and
    cannot lose a line to an appender racing its window;
  * the dedup is a set, structurally.

The bar for "behaviour identical" is not in this file: it is the MS.S7 stress suite and the tm_s5
journal suites passing UNMODIFIED.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_health, team_journal, teamdb
from mokata.config import Surface
from mokata.init import init_repo


def silent(*_a, **_k):
    pass


def _repo(d, mode="team"):
    init_repo(root=d, profile="standard", assume_yes=True, out=silent)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(d)


def _payload(rid, value):
    return {"id": rid, "mtype": "fact", "subject": rid, "status": "active",
            "doc": json.dumps({"id": rid, "value": value}), "project": "p1"}


def _entry(rid, key=None):
    return team_journal.JournalEntry(
        id=rid, op=team_journal.OP_PUT, table=teamdb.MEMORY_TABLE, key=(key or rid),
        payload=_payload(key or rid, "v1"), ledger_id=1, project="p1", actor="a")


class _CountingRecords:
    """A spy on `_records` — the ONE place a parse of the file happens. Counting calls to it counts
    parses exactly; a cache hit is a call that does not happen."""

    def __init__(self, journal):
        self.journal = journal
        self.calls = 0
        self._real = journal._records

    def __enter__(self):
        def counted():
            self.calls += 1
            return self._real()
        self.journal._records = counted
        return self

    def __exit__(self, *_exc):
        self.journal._records = self._real
        return False


def _state(journal):
    """The full caller-visible read surface, as comparable plain data. This — not the file bytes —
    is what compaction must leave identical."""
    return {
        "pending": [(e.id, e.key, e.op, e.base_revision, e.payload) for e in journal.pending()],
        "pending_count": journal.pending_count(),
        "conflicts": [(c.id, c.key, c.detail, c.remote) for c in journal.conflicts()],
        "blocked": [(e.id, e.key) for e in journal.blocked()],
    }


# --------------------------------------------------------------------------- the replay cache
class TestReplayCache(unittest.TestCase):
    def test_j_perf_cache_hits(self):
        """Same file state → ONE parse across pending + conflicts + blocked (was three)."""
        with tempfile.TemporaryDirectory() as d:
            j = team_journal.TeamJournal(os.path.join(d, "team_journal.jsonl"))
            j.append(_entry("a"))
            j.append(_entry("b"))
            j.mark_conflict("b", detail="boom")
            j.append(_entry("c"))
            j.mark_blocked("c", detail="secret")

            with _CountingRecords(j) as spy:
                first = (j.pending(), j.conflicts(), j.blocked(), j.pending_count())
                self.assertEqual(spy.calls, 1, "four reads at one file state must parse ONCE")
                # ...and repeated reads keep hitting.
                j.pending(), j.conflicts(), j.blocked()
                self.assertEqual(spy.calls, 1)

            self.assertEqual([e.id for e in first[0]], ["a"])
            self.assertEqual([c.id for c in first[1]], ["b"])
            self.assertEqual([e.id for e in first[2]], ["c"])
            self.assertEqual(first[3], 1)

    def test_j_perf_cache_invalidates_on_append(self):
        """The cache stores the PARSE, not the stat: an append moves (mtime_ns,size) and the very
        next read re-parses. Proven through the SAME instance's own writer."""
        with tempfile.TemporaryDirectory() as d:
            j = team_journal.TeamJournal(os.path.join(d, "team_journal.jsonl"))
            j.append(_entry("a"))
            with _CountingRecords(j) as spy:
                self.assertEqual([e.id for e in j.pending()], ["a"])
                self.assertEqual(spy.calls, 1)
                j.append(_entry("b"))                       # the file changed under the cache
                self.assertEqual([e.id for e in j.pending()], ["a", "b"],
                                 "a read after an append must see the append")
                self.assertEqual(spy.calls, 2, "the changed file MUST be re-parsed")

    def test_j_perf_cache_invalidates_on_a_foreign_write(self):
        """Not just our own appends: a raw write by anything at all is seen, because the identity
        is re-stat'd on every access rather than tracked by us."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            j = team_journal.TeamJournal(path)
            j.append(_entry("a"))
            self.assertEqual(j.pending_count(), 1)          # warm the cache
            with open(path, "a", encoding="utf-8") as fh:   # a writer we know nothing about
                fh.write(json.dumps({"kind": "flushed", "id": "a"}) + "\n")
            self.assertEqual(j.pending_count(), 0, "a foreign write must not be cached over")

    def test_j_perf_cross_process(self):
        """The two-window shape: a SECOND journal instance on the same file appends, and the first
        instance's next read reflects it. This is the cross-process case — the instances share no
        state whatsoever, only the file, which is exactly what two OS processes share."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            first = team_journal.TeamJournal(path)
            second = team_journal.TeamJournal(path)

            first.append(_entry("a"))
            self.assertEqual(first.pending_count(), 1)      # first's cache is warm

            second.append(_entry("b"))                      # the "other process" writes
            self.assertEqual([e.id for e in first.pending()], ["a", "b"],
                             "the other process's append must be visible immediately")

            second.mark_flushed("a")                        # ...and its markers too
            self.assertEqual([e.id for e in first.pending()], ["b"])

    def test_j_perf_cache_handles_a_missing_file(self):
        """A journal that does not exist is a valid cached state ('empty'), and its creation
        invalidates — `os.stat` failing is not an error path, it is an identity."""
        with tempfile.TemporaryDirectory() as d:
            j = team_journal.TeamJournal(os.path.join(d, "team_journal.jsonl"))
            self.assertEqual(j.pending(), [])
            self.assertEqual(j.pending_count(), 0)
            j.append(_entry("a"))
            self.assertEqual(len(j.pending()), 1, "creating the file must invalidate")

    def test_j_perf_no_cache_shared_across_instances(self):
        """No global/module state: a fresh instance starts cold. (Guards against 'optimising' the
        cache into a process-wide dict, which would outlive the state it describes.)"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            team_journal.TeamJournal(path).append(_entry("a"))
            fresh = team_journal.TeamJournal(path)
            self.assertIsNone(fresh._cache, "a new instance must not inherit a cache")
            with _CountingRecords(fresh) as spy:
                fresh.pending()
                self.assertEqual(spy.calls, 1, "a cold instance must actually read the file")


# --------------------------------------------------------------------------- the dedup
class TestReplayDedup(unittest.TestCase):
    def _membership_comparisons(self, n):
        """Replay a journal of `n` write records, counting ELEMENT comparisons in the replay's
        dedup container. Deterministic — a count of operations, never a wall-clock measurement."""
        counted = {"n": 0}

        class _Probe(str):
            """An id that counts every equality test against it. A SET hashes and compares against
            at most its bucket (≈1 per lookup); a LIST `in` compares against every element so far."""

            def __eq__(self, other):
                counted["n"] += 1
                return str.__eq__(self, other)

            def __hash__(self):
                return str.__hash__(self)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for i in range(n):
                    fh.write(json.dumps({"kind": "write", "id": f"e{i}",
                                         "op": team_journal.OP_PUT, "table": teamdb.MEMORY_TABLE,
                                         "key": f"e{i}", "payload": {}}) + "\n")
            journal = team_journal.TeamJournal(path)
            real_records = journal._records

            def probed():
                # same records, but every id is comparison-counting
                out = []
                for rec in real_records():
                    rec = dict(rec)
                    rec["id"] = _Probe(rec["id"])
                    out.append(rec)
                return out

            journal._records = probed
            self.assertEqual(journal.pending_count(), n)
            return counted["n"]

    def test_j_perf_replay_linear(self):
        """The O(n²) is gone: doubling the journal must roughly DOUBLE the dedup's comparison
        count, not quadruple it. `if rid not in order` scanned a growing list once per record —
        ~n²/2 comparisons; a set is ~1 per record. Counted, not timed, so it is deterministic."""
        small = self._membership_comparisons(200)
        large = self._membership_comparisons(400)

        # linear: 2× the records, ~2× the work (generous headroom for hash collisions).
        self.assertLess(large, small * 3 + 50,
                        f"membership looks super-linear: {small} comparisons at n=200 vs "
                        f"{large} at n=400 — the dedup must be a set, not a list scan")
        # and the absolute scale is O(1)-per-record, not O(n) (the list scan would be ~80_000).
        self.assertLess(large, 400 * 4,
                        f"membership must be O(1) per record, saw {large} for n=400")

    def test_j_perf_dedup_preserves_order_and_uniqueness(self):
        """The set is beside `order`, not instead of it: order and uniqueness are unchanged."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            j = team_journal.TeamJournal(path)
            for rid in ("c", "a", "b"):
                j.append(_entry(rid))
            with open(path, "a", encoding="utf-8") as fh:   # a duplicated write record
                fh.write(json.dumps({"kind": "write", "id": "a", "op": team_journal.OP_PUT,
                                     "table": teamdb.MEMORY_TABLE, "key": "a",
                                     "payload": _payload("a", "v2")}) + "\n")
            self.assertEqual([e.id for e in j.pending()], ["c", "a", "b"],
                             "append order preserved, each id exactly once")


# --------------------------------------------------------------------------- compaction
class TestCompaction(unittest.TestCase):
    def _journal_with(self, path, flushed, pending=1, conflicted=1, blocked=1):
        j = team_journal.TeamJournal(path)
        for i in range(flushed):
            j.append(_entry(f"f{i}"))
            j.mark_flushed(f"f{i}", remote_revision=i + 1)
        for i in range(pending):
            j.append(_entry(f"p{i}"))
        for i in range(conflicted):
            j.append(_entry(f"c{i}"))
            j.mark_conflict(f"c{i}", detail=f"lost update {i}", remote={"revision": 9})
        for i in range(blocked):
            j.append(_entry(f"b{i}"))
            j.mark_blocked(f"b{i}", detail="secret")
        return j

    def test_j_perf_compaction_threshold(self):
        """Below the threshold the file is byte-untouched; above it, it shrinks — and the whole
        caller-visible state is IDENTICAL either way."""
        with tempfile.TemporaryDirectory() as d:
            # --- below: untouched
            below = os.path.join(d, "below.jsonl")
            jb = self._journal_with(below, flushed=3)
            before_bytes = open(below, "rb").read()
            before_state = _state(jb)
            self.assertEqual(jb.compact_if_needed(threshold=10), 0)
            self.assertEqual(open(below, "rb").read(), before_bytes,
                             "below the threshold the journal must not be rewritten at all")
            self.assertEqual(_state(jb), before_state)

            # --- above: compacted, state identical
            above = os.path.join(d, "above.jsonl")
            ja = self._journal_with(above, flushed=12)
            size_before = os.path.getsize(above)
            state_before = _state(ja)
            dropped = ja.compact_if_needed(threshold=10)
            self.assertEqual(dropped, 24, "12 flushed entries = 12 write + 12 marker lines")
            self.assertLess(os.path.getsize(above), size_before, "the file must be smaller")
            self.assertEqual(_state(ja), state_before,
                             "pending / conflict / blocked state must be IDENTICAL after compaction")
            # and a cold reader agrees — the state is in the FILE, not in our cache.
            self.assertEqual(_state(team_journal.TeamJournal(above)), state_before)
            self.assertEqual(ja.flushed_count(), 0, "the flushed dead weight is gone")

    def test_j_perf_compaction_writes_nothing_new(self):
        """Compaction may only REMOVE lines. Every surviving line must be byte-identical to a line
        that was already there — no re-serialisation, no schema change, no invented content."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            j = self._journal_with(path, flushed=12)
            original = open(path, encoding="utf-8").read().splitlines(keepends=True)
            j.compact_if_needed(threshold=10)
            after = open(path, encoding="utf-8").read().splitlines(keepends=True)
            self.assertTrue(after, "compaction must not empty a journal with live entries")
            for line in after:
                self.assertIn(line, original,
                              "compaction wrote a line that was not already in the journal")
            self.assertLess(len(after), len(original))

    def test_j_perf_compaction_keeps_a_write_and_its_marker_together(self):
        """The one way compaction could lose data: dropping a `flushed` marker but keeping its
        `write` record would RESURRECT a completed write as pending. Filtering by id forbids it."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            j = self._journal_with(path, flushed=12, pending=0, conflicted=0, blocked=0)
            j.compact_if_needed(threshold=10)
            self.assertEqual(j.pending(), [], "no flushed write may come back as pending")
            ids = {json.loads(l)["id"] for l in open(path, encoding="utf-8") if l.strip()}
            self.assertEqual(ids, set(), "a fully-flushed journal compacts to empty")

    def test_j_perf_compaction_runs_inside_flush(self):
        """Wired where the spec says: inside `flush`, under the flush mutex, after a successful
        pass. Driven end-to-end through the real flush with a monkeypatched threshold."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            j = team_journal.TeamJournal.for_surface(surface)
            for i in range(6):
                j.append(_entry(f"old{i}"))
                j.mark_flushed(f"old{i}")
            team_journal.record_team_write(
                surface, op=team_journal.OP_PUT, table=teamdb.MEMORY_TABLE, key="live",
                payload=_payload("live", "v1"), ledger_id=1, project="p1", actor="a")
            size_before = os.path.getsize(j.path)

            original = team_journal.COMPACT_FLUSHED_THRESHOLD
            team_journal.COMPACT_FLUSHED_THRESHOLD = 2
            try:
                res = team_journal.flush(
                    surface, health=team_health.HealthVerdict(team_health.HEALTHY, "ok"),
                    connect=lambda *a, **k: _FakePg())
            finally:
                team_journal.COMPACT_FLUSHED_THRESHOLD = original

            self.assertEqual(res.flushed, 1)
            self.assertEqual(res.pending, 0)
            self.assertLess(os.path.getsize(j.path), size_before,
                            "a settled flush past the threshold must compact")

    def test_j_perf_compaction_crash(self):
        """ATOMIC (R-MAN): a crash anywhere in the rewrite leaves the WHOLE old journal — never a
        torn one. The control case proves the injection actually fires."""
        with tempfile.TemporaryDirectory() as d:
            # --- the control: the pre-J-PERF mechanism (a bare truncating rewrite) TEARS.
            ctrl = os.path.join(d, "control.jsonl")
            jc = self._journal_with(ctrl, flushed=12)
            before = open(ctrl, encoding="utf-8").read()
            with self.assertRaises(_Boom):
                with open(ctrl, "w", encoding="utf-8") as fh:
                    fh.write(before[:40])
                    raise _Boom("simulated crash")
            torn = open(ctrl, encoding="utf-8").read()
            self.assertNotEqual(torn, before, "control did not modify the file — injection is a no-op")
            with self.assertRaises(ValueError):
                json.loads(torn.splitlines()[-1])       # the damage: an unparseable record

            # --- the real path, under the SAME crash points
            for attr in ("fsync", "replace"):
                with self.subTest(crash_at=attr):
                    path = os.path.join(d, f"real-{attr}.jsonl")
                    j = self._journal_with(path, flushed=12)
                    old_bytes = open(path, "rb").read()
                    old_state = _state(j)

                    real = getattr(os, attr)

                    def _raise(*_a, **_k):
                        raise _Boom("simulated crash")

                    setattr(os, attr, _raise)
                    try:
                        with self.assertRaises(_Boom):
                            j.compact_if_needed(threshold=10)
                    finally:
                        setattr(os, attr, real)

                    self.assertEqual(open(path, "rb").read(), old_bytes,
                                     "a crashed compaction must leave the OLD journal, whole")
                    # every line still parses, and the live state is unchanged
                    for line in open(path, encoding="utf-8"):
                        if line.strip():
                            json.loads(line)
                    self.assertEqual(_state(team_journal.TeamJournal(path)), old_state)
                    # no stray temp file left behind in the journal's directory
                    self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp-")], [])

    def test_j_perf_compaction_concurrent_append(self):
        """The lock argument, EXECUTED. An appender racing the compact window is never lost: the
        compaction holds the APPEND lock across read→filter→replace, so the append either lands
        before the read (and survives into the rewritten file) or blocks until the replace is done
        (and lands on the new file).

        The interleaving is FORCED rather than hoped for: the compaction is held open inside its
        window until the racer has reached its `append` call, so the racer is blocked on the lock
        for the duration. The assertion — the appended line is in the file afterwards — is what is
        pinned, and it must hold on EVERY interleaving, so the test cannot pass by luck of timing."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            j = self._journal_with(path, flushed=12)
            appender = team_journal.TeamJournal(path)       # the "other process"

            racer_ready = threading.Event()
            real_atomic = team_journal.atomic_write_text

            def _held_open(p, text, **kw):
                # We are INSIDE the compact window, holding the append lock. Do not leave it until
                # the racer has actually reached `append` and is blocking on that lock.
                racer_ready.wait(5)
                time.sleep(0.2)
                return real_atomic(p, text, **kw)

            team_journal.atomic_write_text = _held_open
            errors = []

            def _append_racer():
                try:
                    racer_ready.set()
                    appender.append(_entry("raced"))        # blocks on the append lock
                except Exception as exc:                    # pragma: no cover - a failure is the bug
                    errors.append(exc)

            t = threading.Thread(target=_append_racer)
            t.start()
            try:
                j.compact_if_needed(threshold=10)
            finally:
                team_journal.atomic_write_text = real_atomic
                t.join(10)

            self.assertEqual(errors, [], "the racing append must not fail")
            cold = team_journal.TeamJournal(path)
            self.assertIn("raced", [e.id for e in cold.pending()],
                          "an append racing the compact window must NOT be lost")
            self.assertEqual(cold.flushed_count(), 0, "the compaction still happened")

    def test_j_perf_compaction_preserves_a_kept_local_resolution(self):
        """`base_revision` re-queued by a `kept-local` resolve rides through compaction — the
        resolution records are not flushed records and must survive."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "j.jsonl")
            j = self._journal_with(path, flushed=12, conflicted=0)
            j.append(_entry("r0"))
            j.mark_conflict("r0", detail="lost", remote={"revision": 42})
            j.resolve("r0", "kept-local", remote_revision=42)
            before = _state(j)
            j.compact_if_needed(threshold=10)
            cold = team_journal.TeamJournal(path)
            self.assertEqual(_state(cold), before)
            self.assertEqual([e.base_revision for e in cold.pending() if e.id == "r0"], [42],
                             "the re-queued CAS base revision must survive compaction")


class _Boom(Exception):
    """The simulated crash. Distinct from any real error so a swallowed one can't fake a pass."""


class _FakePg:
    """Enough Postgres for a successful flush — the compaction wiring test only needs `ok`."""

    def execute(self, _sql, _params=()):
        class _C:
            rowcount = 1

            @staticmethod
            def fetchone():
                return None
        return _C()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
