"""MS.S8 — THE JOURNAL APPEND LOCK (the second Windows-only release blocker).

THE BUG. `TeamJournal._append` is the ONE funnel every journal write goes through, and it wrote with
a bare `open(path, "a")`. That is atomic on POSIX and NOT atomic on Windows:

  * POSIX `O_APPEND` makes the "seek to EOF, then write" pair ONE indivisible step, by contract.
  * The Windows CRT has no such flag on the file. It EMULATES append mode: it records `FAPPEND` in
    its per-process fd table and, before each write, does `_lseeki64_nolock(fd, 0, SEEK_END)` and
    then writes. Two steps. The `_nolock` suffix names the exact limit of the protection — the CRT
    serialises THREADS inside one process and nothing at all across processes. (A native handle
    opened with `FILE_APPEND_DATA` would be atomic, but CPython's `open()` goes through the CRT.)

So on Windows two processes can both seek to EOF=N, and both write their record AT N. The second
write lands on top of the first: one record is silently GONE, and the file is not even torn — it is
plausible, parseable JSONL that is simply missing a line.

WHY TWO PROCESSES ARE EVER IN `_append` AT ONCE — the part that makes this reachable rather than
theoretical. Appends reach this file from THREE different lock contexts, and no two of them exclude
each other:

  * `append()` (the gated team write) runs under the LEDGER append-lock, held by the WriteGate
    across its commit closure;
  * `mark_flushed` / `mark_conflict` / `mark_blocked` run inside `_flush_locked`, under the FLUSH
    mutex — a DIFFERENT sidecar, so it excludes other flushers and nobody else;
  * `resolve()` (a human's sync decision) and `recover_stranded_floor` run under NO lock at all —
    `sync` calls them after `flush()` has already released the mutex.

Two different mutexes give ZERO mutual exclusion against each other. Window A committing a gated
write (LEDGER held) and window B marking an entry flushed (FLUSH held) are free to be inside
`_append` at the same instant. On Windows, one of those records dies. Losing a `write` record is the
data-loss case: an approved, human-gated team write that will never flush and that nothing will ever
report as pending — it is not in the journal to be pending.

THE FIX. The journal file gets its OWN lock — an `oslock` sidecar (`lock_path_for`, the standard
convention) held across open→write→fsync INSIDE `_append`. Every append site inherits it for free
because they all funnel through `_append`. It is a LEAF lock (it takes nothing else while held), so
it cannot form a cycle with the LEDGER or FLUSH locks — the only orders it can ever appear in are
LEDGER→APPEND, FLUSH→APPEND and ∅→APPEND. POSIX gets belt-and-braces; Windows gets correctness.

HOW THESE TESTS PROVE IT — and the honesty that costs.

`TwoProcessAppendStorm` (the end-to-end witness) CANNOT GO RED ON POSIX, before the fix or after it:
`O_APPEND` masks the bug on the machine this suite is usually run on. Said plainly rather than
buried, because a green storm on a laptop is NOT evidence the bug is fixed. Windows CI is where that
test has teeth.

So the load-bearing tests are the two that ARE deterministic everywhere:

  * `WindowsAppendSemantics` reproduces the bug ON POSIX by driving the REAL `_append` with `open`
    replaced by a file object that emulates the CRT (`lseek(END)` … `write()`, two steps, with the
    gap held open). Pre-fix it LOSES records on a Mac; post-fix it loses none. That is the actual
    proof, and it is the test that fails if the lock is ever removed.
  * `AppendLockIsEngaged` asserts the mechanism is genuinely TAKEN (an append blocks while another
    process holds the sidecar) rather than inferring it from a passing storm.

This mirrors MS.S7-FIX-1's shape, and for the same reason it gives: a race that only reddens
sometimes (or, here, never on this OS) is too weak to guard a fix, so the guard is deterministic and
the race is kept as the witness.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import multiprocessing as mp
import os
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_health, team_journal, teamdb
from mokata.atomicfile import lock_path_for
from mokata.config import Surface
from mokata.init import init_repo
from mokata.team_journal import JournalEntry, TeamJournal

_CTX = mp.get_context("spawn")   # stable across POSIX/Windows; children re-import cleanly

# The gap the Windows CRT leaves between its `lseek(END)` and its `write()`. Held open deliberately
# in the emulation so the race is DETERMINISTIC rather than a 1-in-N lottery: a real Windows box
# loses the record only when the scheduler happens to interleave there, which is exactly the kind of
# "reddens 3 runs in 10" flakiness that cannot guard a fix.
_CRT_GAP = 0.05


def _entry(key, i=0):
    return JournalEntry(id=key, op=team_journal.OP_PUT, table=teamdb.MEMORY_TABLE, key=key,
                        payload={"id": key, "mtype": "fact", "subject": key, "status": "active",
                                 "doc": json.dumps({"id": key, "value": i}), "project": "p1"},
                        ledger_id=1, project="p1", actor="w")


def _lines(path):
    """Every VALID JSONL record in the journal. A record that was clobbered is simply absent; a torn
    one fails to parse — this counts what actually survived, which is the only thing that matters."""
    if not os.path.exists(path):
        return []
    out, torn = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                torn += 1
    return out, torn


# ============================================================================ the CRT emulation
class _WinAppendFile:
    """`open(path, "a")` with WINDOWS semantics, on any OS.

    The CRT does not hand the kernel an append-mode handle; it emulates one. This does exactly what
    it does — seek to the end, then write — with the two steps left visibly apart. Everything else
    (`fileno` for the caller's `fsync`, the context-manager protocol) is real, so the code under test
    is driven unmodified: `_append` cannot tell it is being watched.
    """

    def __init__(self, path, gap=_CRT_GAP):
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)   # NOT O_APPEND — that is the point
        self._buf = []
        self._gap = gap

    def write(self, s):
        self._buf.append(s)
        return len(s)

    def flush(self):
        data = "".join(self._buf).encode("utf-8")
        self._buf = []
        if not data:
            return
        os.lseek(self._fd, 0, os.SEEK_END)   # step 1 — the CRT's `_lseeki64_nolock(fd, 0, SEEK_END)`
        time.sleep(self._gap)                # the gap the CRT leaves open across processes
        os.write(self._fd, data)             # step 2 — writes at the offset we REMEMBERED, not at EOF

    def fileno(self):
        return self._fd

    def close(self):
        self.flush()
        os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _win_open(real_open):
    """A drop-in `open` that routes append-mode opens through the CRT emulation and everything else
    to the real one (the journal's own reads must keep working)."""
    def _opener(path, mode="r", *a, **kw):
        if "a" in mode:
            return _WinAppendFile(path)
        return real_open(path, mode, *a, **kw)
    return _opener


# ============================================================================ workers
def _win_append_worker(path, keys, go):
    """One window appending under emulated-Windows semantics, through the REAL `_append`."""
    import builtins
    import _support  # noqa: F401
    from unittest import mock

    from mokata.team_journal import TeamJournal as TJ

    journal = TJ(path)
    entries = [_entry(k) for k in keys]
    go.wait(timeout=60)
    with mock.patch.object(builtins, "open", _win_open(open)):
        for e in entries:
            journal.append(e)


def _storm_worker(path, wid, n, go, barrier, q):
    """One window in the real storm: n appends and n flush-side markers, interleaved — the exact
    cross-lock pair (a gated write's `append` vs a flusher's `mark_flushed`) that shares this file."""
    import _support  # noqa: F401

    from mokata.team_journal import TeamJournal as TJ

    journal = TJ(path)
    keys = [f"storm-w{wid}-{i}" for i in range(n)]
    go.wait(timeout=60)
    barrier.wait(timeout=60)                       # both windows enter the storm together
    err = None
    try:
        for i, k in enumerate(keys):
            journal.append(_entry(k, i))
            journal.mark_flushed(k, remote_revision=1)
    except Exception as exc:                       # noqa: BLE001 - the report IS the point
        err = f"{type(exc).__name__}: {exc}"
    q.put({"wid": wid, "keys": keys, "error": err})


def _hold_lock_worker(lock_path, acquired, release):
    """Hold the append sidecar from ANOTHER OS PROCESS — the only thing that proves a cross-process
    lock (a `threading.Lock` would prove nothing about two Claude Code windows)."""
    import _support  # noqa: F401

    from mokata.oslock import file_lock as fl

    with fl(lock_path, timeout=30.0):
        acquired.set()
        release.wait(timeout=30)


# ============================================================================ the deterministic proof
class WindowsAppendSemantics(unittest.TestCase):
    """THE PROOF, and the one test that reddens on a Mac if the lock is removed.

    Two REAL processes append to one journal with the CRT's seek-then-write emulation in force.
    Without the append lock, their writes land on top of each other and records vanish. With it, the
    seek and the write are inside one critical section and every record survives."""

    def test_two_windows_appending_with_windows_semantics_lose_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            a = [f"win-a-{i}" for i in range(4)]
            b = [f"win-b-{i}" for i in range(4)]
            go = _CTX.Event()
            procs = [_CTX.Process(target=_win_append_worker, args=(path, keys, go))
                     for keys in (a, b)]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=120)

            self.assertEqual([p.exitcode for p in procs], [0, 0],
                             "an emulated-Windows appender crashed")
            recs, torn = _lines(path)
            got = {r.get("id") for r in recs}
            missing = (set(a) | set(b)) - got
            self.assertEqual(
                torn, 0, f"{torn} TORN line(s) in the journal — a record was written over mid-line")
            self.assertEqual(
                missing, set(),
                f"{len(missing)} journal record(s) were CLOBBERED under Windows append semantics: "
                f"{sorted(missing)}. Two processes each seeked to EOF and then wrote at the offset "
                f"they remembered, so one record landed on top of the other. On Windows this is a "
                f"silently LOST human-approved team write — the entry is not in the journal at all, "
                f"so it never flushes and never even reports as pending. `_append` must hold the "
                f"journal's append lock across open→write→fsync.")

    def test_the_emulation_really_does_lose_records_without_the_lock(self):
        """SKIP-HONESTY, and the load-bearing control. If the CRT emulation could not actually lose a
        record, the test above would be green for the wrong reason — a fake that never reproduces the
        bug proves nothing about the fix that "stops" it. So: run the SAME two-process race with the
        SAME emulation but writing WITHOUT the lock, and assert the loss really happens. This is the
        bug, reproduced on POSIX. If this test ever goes green, the emulation has stopped modelling
        Windows and every other test in this class is hollow."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "unlocked.jsonl")
            go = _CTX.Event()
            procs = [_CTX.Process(target=_unlocked_win_worker,
                                  args=(path, [f"raw-{w}-{i}" for i in range(4)], go))
                     for w in ("a", "b")]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=120)

            recs, _torn = _lines(path)
            self.assertLess(
                len(recs), 8,
                "the Windows-append emulation did NOT lose a record with no lock held — it is no "
                "longer modelling the CRT's seek-then-write, so the proof above is hollow")


def _unlocked_win_worker(path, keys, go):
    """The pre-fix `_append`, verbatim (open → write → fsync, no lock), under CRT semantics. This is
    the control: it is what the product did before this stage, and it MUST lose records."""
    import _support  # noqa: F401

    go.wait(timeout=60)
    for k in keys:
        rec = {"kind": "write", "id": k, "op": "memory_put", "key": k}
        with _WinAppendFile(path) as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


# ============================================================================ the mechanism is taken
class AppendLockIsEngaged(unittest.TestCase):
    """Prove the lock is genuinely HELD, rather than inferring it from a passing race. Every append
    site funnels through `_append`, so each of these pins a different caller onto the same mutex."""

    def _blocked_while_held(self, call, path):
        """Run `call()` while ANOTHER PROCESS holds the journal's append sidecar. Returns whether the
        journal was still empty during the hold (i.e. the append genuinely WAITED)."""
        acquired, release = _CTX.Event(), _CTX.Event()
        holder = _CTX.Process(target=_hold_lock_worker,
                              args=(lock_path_for(path), acquired, release))
        holder.start()
        try:
            self.assertTrue(acquired.wait(timeout=30), "the holder never took the lock")
            done = threading.Event()
            t = threading.Thread(target=lambda: (call(), done.set()))
            t.start()
            wrote_while_held = done.wait(timeout=0.75)   # did it barge past the held lock?
            release.set()
            t.join(timeout=30)
            self.assertTrue(done.is_set(), "the append never completed after the lock was released "
                                           "— the lock is not being released (a deadlock)")
            return not wrote_while_held
        finally:
            release.set()
            holder.join(timeout=30)

    def test_a_gated_write_append_waits_for_the_append_lock(self):
        """The LEDGER-side caller: `append()`, run from inside the WriteGate's commit closure."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            journal = TeamJournal(path)
            waited = self._blocked_while_held(lambda: journal.append(_entry("gated-1")), path)
            self.assertTrue(waited,
                            "`append()` wrote while another process held the journal's append lock "
                            "— the append is NOT serialised, so on Windows it can land on top of a "
                            "concurrent record")
            self.assertEqual(len(_lines(path)[0]), 1, "the append must still land once released")

    def test_a_flush_side_marker_waits_for_the_SAME_lock(self):
        """The FLUSH-side caller: `mark_flushed`, run under the flush mutex — a DIFFERENT lock, which
        is precisely why it needs this one. This is the other half of the cross-lock pair."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            journal = TeamJournal(path)
            waited = self._blocked_while_held(
                lambda: journal.mark_flushed("e1", remote_revision=1), path)
            self.assertTrue(waited,
                            "`mark_flushed` wrote while the append lock was held. It runs under the "
                            "FLUSH mutex, which excludes other flushers and NOTHING else — a gated "
                            "write appending under the LEDGER lock races it directly")

    def test_an_unlocked_sync_resolution_waits_too(self):
        """`resolve()` is called by `sync` AFTER the flush mutex is released — it holds no lock at
        all. It writes to the same file, so it must take the same append lock."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            journal = TeamJournal(path)
            waited = self._blocked_while_held(
                lambda: journal.resolve("e1", "kept-local", remote_revision=2), path)
            self.assertTrue(waited, "`resolve()` wrote while the append lock was held — a human's "
                                    "sync decision races every other appender")

    def test_the_append_lock_is_a_sidecar_distinct_from_the_flush_mutex(self):
        """Naming, but load-bearing: reusing the FLUSH mutex here would SELF-DEADLOCK, because
        `_flush_locked` already holds it when it calls `mark_flushed` and `oslock` is not reentrant
        across fds. The append lock must be its own sidecar, and never the journal file itself."""
        with tempfile.TemporaryDirectory() as d:
            journal = TeamJournal(os.path.join(d, "team_journal.jsonl"))
            append_lock = journal.append_lock_path
            self.assertNotEqual(append_lock, journal.flush_lock_path,
                                "the append lock MUST NOT be the flush mutex — the flush appends its "
                                "markers while holding the flush mutex, so sharing them deadlocks")
            self.assertNotEqual(append_lock, journal.path,
                                "never lock the journal file itself")
            self.assertEqual(append_lock, lock_path_for(journal.path),
                             "the append lock follows the standard sidecar convention")


# ============================================================================ no self-deadlock
class TheFlushStillWorks(unittest.TestCase):
    """The regression the fix could plausibly introduce. `_flush_locked` holds the FLUSH mutex and
    then calls `mark_flushed` → `_append` → takes the APPEND lock. Nested locks are how deadlocks are
    born, so drive a REAL flush end-to-end and prove it still drains."""

    def test_a_flush_appends_its_markers_under_the_flush_mutex_without_deadlocking(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            manifest = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("settings", {})["mode"] = "team"
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            surface = Surface.load(d)

            journal = TeamJournal.for_surface(surface)
            for i in range(3):
                journal.append(_entry(f"flushme-{i}", i))
            self.assertEqual(len(journal.pending()), 3)

            applied = []

            class _Conn:
                def execute(self, sql, params=()):
                    applied.append(params)
                    return _Cur()

            class _Cur:
                rowcount = 1

                def fetchone(self):
                    return None

                def fetchall(self):
                    return []

            start = time.monotonic()
            res = team_journal.flush(
                surface,
                health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                connect=lambda *a, **k: _Conn(),
                scan=lambda _e: [])
            elapsed = time.monotonic() - start

            self.assertEqual(res.flushed, 3, f"the flush did not drain: {res}")
            self.assertEqual(journal.pending(), [],
                             "the flush's `mark_flushed` markers never landed")
            self.assertLess(elapsed, 8.0,
                            "the flush took the whole oslock timeout — the append lock deadlocked "
                            "against the flush mutex it is nested inside")


# ============================================================================ the honest witness
class TwoProcessAppendStorm(unittest.TestCase):
    """The end-to-end witness: two REAL processes storming one journal with the cross-lock pair
    (gated `append` + flush-side `mark_flushed`) interleaved.

    HONESTY, stated where it cannot be missed: this test CANNOT GO RED ON POSIX, before the fix or
    after it. `O_APPEND` makes the append atomic here, so a Mac/Linux green says nothing whatsoever
    about the Windows bug. It is kept because Windows CI runs this suite, and THERE it is a true
    end-to-end proof on the real primitive. The tests that guard the fix on every platform are
    `WindowsAppendSemantics` (which reproduces the loss on POSIX) and `AppendLockIsEngaged`."""

    def test_every_record_survives_a_two_process_storm(self):
        n = 60
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team_journal.jsonl")
            go, barrier, q = _CTX.Event(), _CTX.Barrier(2), _CTX.Queue()
            procs = [_CTX.Process(target=_storm_worker, args=(path, wid, n, go, barrier, q))
                     for wid in range(2)]
            for p in procs:
                p.start()
            go.set()
            reports = [q.get(timeout=180) for _ in procs]
            for p in procs:
                p.join(timeout=60)

            self.assertEqual([p.exitcode for p in procs], [0, 0], "a storm worker crashed")
            self.assertEqual([r["error"] for r in reports], [None, None],
                             f"an append raised under contention: {[r['error'] for r in reports]}")

            recs, torn = _lines(path)
            self.assertEqual(torn, 0, f"{torn} TORN line(s) — two appends interleaved mid-record")

            expected_writes = {k for r in reports for k in r["keys"]}
            writes = {r["id"] for r in recs if r.get("kind") == "write"}
            flushed = {r["id"] for r in recs if r.get("kind") == "flushed"}

            self.assertEqual(expected_writes - writes, set(),
                             f"{len(expected_writes - writes)} `write` record(s) LOST — an approved "
                             f"team write is gone from the journal entirely")
            self.assertEqual(expected_writes - flushed, set(),
                             f"{len(expected_writes - flushed)} `flushed` marker(s) LOST")
            self.assertEqual(len(recs), 2 * 2 * n,
                             f"expected exactly {2 * 2 * n} records (2 windows x {n} appends x "
                             f"[write + flushed]), found {len(recs)}")


if __name__ == "__main__":
    unittest.main()
