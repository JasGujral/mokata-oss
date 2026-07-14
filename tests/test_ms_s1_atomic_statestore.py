"""MS.S1 — ATOMIC STATESTORE + OS LOCK HELPER (M-1).

The bug this closes: two Claude Code windows on one repo are two MCP processes doing UNLOCKED
read-modify-write on the same `.mokata/temp_local/state/*.json` files. Last-writer-wins clobbers a
concurrent update; a crash mid-write can leave a torn/half-written file; and there is no `fsync`, so
a `kill -9` or power loss can corrupt the state every gate reads (P2). A per-process `threading.Lock`
is worthless here — each window is a separate process.

These tests pin the fix:
  (a) M-1 regression: two REAL processes (spawned, not threads) hammer concurrent RMW updates on one
      StateStore → every update is present (no lost update) and the file parses as valid JSON at every
      intermediate observation (no torn read).
  (b) Crash atomicity: death between tmp-write and replace, and mid-tmp-write, leaves the PREVIOUS
      consistent state loadable (never a torn/partial file) and no tmp turd behind.
  (c) fsync honesty: `os.fsync` is called on the temp file BEFORE `os.replace`.
  (d) The shared OS lock helper (`mokata.oslock.file_lock`): a second process times out with a clear
      error NAMING the lock path while the first holds it; the lock releases on context exit AND on
      process death (OS locks die with the fd). The Windows (`msvcrt`) branch exists now (full two-OS
      proof is MS.S7); per-platform tests skip cleanly.
  (e) Negatives: single-process write is byte-identical (same target file, same contents) and the
      state schema is stable (no new fields injected). No credential surface (secret-safety N/A).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import oslock
from mokata.oslock import LockTimeout, file_lock
from mokata.state import StateStore

_CTX = mp.get_context("spawn")  # stable across POSIX/Windows; children re-import cleanly


# ----------------------------------------------------------------- spawn-safe worker functions
def _rmw_worker(root, wid, n):
    """Append (wid, i) to the shared `log` key via a LOCKED read-modify-write, n times."""
    from mokata.state import StateStore
    store = StateStore(root)
    for i in range(n):
        store.update("log", lambda cur: (cur or []) + [[wid, i]], default=[])


def _hold_lock_worker(lock_path, acquired_evt, release_evt):
    """Acquire the lock, signal, then hold it until told to release (or timeout)."""
    from mokata.oslock import file_lock
    with file_lock(lock_path, timeout=30.0):
        acquired_evt.set()
        release_evt.wait(timeout=30.0)


def _hold_lock_forever(lock_path, acquired_evt):
    """Acquire the lock and hold it — meant to be terminated so the OS reclaims the lock."""
    from mokata.oslock import file_lock
    with file_lock(lock_path, timeout=30.0):
        acquired_evt.set()
        time.sleep(60.0)


# =================================================================== the OS lock helper (M-1 part 2)
class TestOsLockHelper(unittest.TestCase):
    def test_uncontended_acquire_and_release(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "x.lock")
            with file_lock(lp, timeout=1.0):
                pass  # acquires + releases without error
            with file_lock(lp, timeout=1.0):  # reacquirable
                pass

    def test_second_process_times_out_with_named_path(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "held.lock")
            acquired = _CTX.Event()
            release = _CTX.Event()
            p = _CTX.Process(target=_hold_lock_worker, args=(lp, acquired, release))
            p.start()
            try:
                self.assertTrue(acquired.wait(timeout=10.0), "child never acquired the lock")
                with self.assertRaises(LockTimeout) as cm:
                    with file_lock(lp, timeout=0.3):
                        pass
                self.assertIn(lp, str(cm.exception))  # error names the contended lock path
            finally:
                release.set()
                p.join(timeout=10.0)

    def test_lock_released_on_process_death(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "dead.lock")
            acquired = _CTX.Event()
            p = _CTX.Process(target=_hold_lock_forever, args=(lp, acquired))
            p.start()
            self.assertTrue(acquired.wait(timeout=10.0), "child never acquired the lock")
            # while held, we can't get it
            with self.assertRaises(LockTimeout):
                with file_lock(lp, timeout=0.3):
                    pass
            p.terminate()      # kill -15; the OS must reclaim the fd-held lock
            p.join(timeout=10.0)
            with file_lock(lp, timeout=5.0):  # now acquirable
                pass

    def test_windows_branch_exists(self):
        src = inspect.getsource(oslock)
        self.assertIn("msvcrt", src, "no Windows (msvcrt) locking branch present")
        self.assertIn("fcntl", src, "no POSIX (fcntl) locking branch present")
        # on this host the selected backend is the platform-appropriate one
        expected = "msvcrt" if sys.platform.startswith("win") else "fcntl"
        self.assertEqual(oslock.backend_name(), expected)


# =================================================================== atomic StateStore (M-1 part 1)
class TestAtomicStateStore(unittest.TestCase):
    def test_two_processes_concurrent_rmw_no_lost_update(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "state")
            os.makedirs(root, exist_ok=True)
            workers, iters = 4, 40
            procs = [_CTX.Process(target=_rmw_worker, args=(root, w, iters))
                     for w in range(workers)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60.0)
                self.assertEqual(p.exitcode, 0, "a worker crashed")
            data = StateStore(root).read("log")
            self.assertIsNotNone(data)
            got = {tuple(pair) for pair in data}
            expected = {(w, i) for w in range(workers) for i in range(iters)}
            self.assertEqual(got, expected, "lost update — not every RMW survived")
            self.assertEqual(len(data), workers * iters, "duplicate or missing entries")

    def test_no_torn_read_during_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "state")
            os.makedirs(root, exist_ok=True)
            procs = [_CTX.Process(target=_rmw_worker, args=(root, w, 60)) for w in range(3)]
            for p in procs:
                p.start()
            path = StateStore(root).path("log")
            # While writers hammer, every observation of the file is either absent or valid JSON —
            # never a torn/half-written file (os.replace is atomic).
            for _ in range(400):
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                except FileNotFoundError:
                    continue
                if raw:
                    json.loads(raw)  # raises if torn -> test fails
            for p in procs:
                p.join(timeout=60.0)

    def test_crash_between_tmp_write_and_replace_keeps_old_state(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            store.write("k", {"v": "old"})
            with mock.patch("os.replace", side_effect=RuntimeError("boom (kill -9 before replace)")):
                with self.assertRaises(RuntimeError):
                    store.write("k", {"v": "new"})
            # old state still loads intact; nothing torn
            self.assertEqual(store.read("k"), {"v": "old"})
            # no temp turd left behind
            leftovers = [f for f in os.listdir(store.root) if f != "k.json"
                         and not f.endswith(".lock")]
            self.assertEqual(leftovers, [], f"temp file leaked: {leftovers}")

    def test_crash_mid_tmp_write_keeps_old_state(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            store.write("k", {"v": "old"})
            with mock.patch("json.dumps", side_effect=RuntimeError("boom mid-serialize")):
                with self.assertRaises(RuntimeError):
                    store.write("k", {"v": "new"})
            self.assertEqual(store.read("k"), {"v": "old"})

    def test_fsync_called_before_replace(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            order = []
            real_fsync, real_replace = os.fsync, os.replace

            def rec_fsync(fd):
                order.append("fsync")
                return real_fsync(fd)

            def rec_replace(src, dst):
                order.append("replace")
                return real_replace(src, dst)

            with mock.patch("os.fsync", rec_fsync), mock.patch("os.replace", rec_replace):
                store.write("k", {"v": 1})
            self.assertIn("fsync", order)
            self.assertIn("replace", order)
            self.assertLess(order.index("fsync"), order.index("replace"),
                            "fsync must run BEFORE replace (durability)")

    def test_single_process_write_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            data = {"a": 1, "b": ["x", "y"], "nested": {"k": True}}
            path = store.write("thing", data)
            # same target file name as before (no key-shape change)
            self.assertEqual(path, os.path.join(store.root, "thing.json"))
            with open(path, encoding="utf-8") as fh:
                got = fh.read()
            expected = json.dumps(data, indent=2, sort_keys=False) + "\n"
            self.assertEqual(got, expected, "on-disk bytes changed vs the plain write")

    def test_schema_stable_no_new_fields(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            payload = {"only": "these", "two": 2}
            store.write("k", payload)
            self.assertEqual(store.read("k"), payload)  # no injected lock/meta fields

    def test_update_roundtrips_single_process(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            store.update("counter", lambda cur: {"n": cur["n"] + 1}, default={"n": 0})
            store.update("counter", lambda cur: {"n": cur["n"] + 1}, default={"n": 0})
            self.assertEqual(store.read("counter"), {"n": 2})


if __name__ == "__main__":
    unittest.main()
