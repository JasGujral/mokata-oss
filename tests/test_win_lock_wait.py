"""0.0.27 `WIN-LOCK-WAIT` — a waiter is never starved by a mokata process that rewrites the same state
without pause.

Disclosed in the 0.0.26 notes: "On Windows, a spec proposal can wait ~20 s while another mokata
process writes the same run's state without pause; it then completes." (CI 0.0.26 cut: 20.56 s and
20.50 s against `test_o14_mcp_stability`'s 25 s tight-loop writer.)

Mechanism (read from `src/mokata/oslock.py` at v0.0.26): BOTH branches poll a NON-BLOCKING lock —
`fcntl.flock(fd, LOCK_EX | LOCK_NB)` and `msvcrt.locking(fd, LK_NBLCK, 1)` — sleeping 20 ms between
attempts. A poll only wins if it lands in the gap between a holder's release and its next acquire, so
a writer that re-takes the lock straight away leaves a gap of a few syscalls and the waiter loses
for hundreds of the writer's cycles. It is not Windows-only: measured on this Linux box with a
v0.0.26 tree, a waiter lost 150-320 cycles (~2 s) to a writer doing 2-10 ms of work per cycle.

The fix (`oslock._hold`): a turnstile sidecar every acquirer passes before it waits, held while
waiting and dropped once the lock is taken. The returning writer has to pass the same turnstile, so
it cannot cut in front of a waiter.

Business rules:
  * A waiting acquirer gets the lock within a BOUNDED number of a pause-free writer's cycles —
    asserted (a) against a MODELLED primitive with no kernel queue (the Windows `LK_NBLCK` shape),
    driven through the real acquisition policy, on any OS; and (b) against the REAL OS primitive,
    with another process rewriting a run's state through `StateStore` without pause.
  * The bound is the existing one (`LockTimeout` naming the path) — fairness adds no new timeout.
  * A process that dies while waiting (holding the turnstile) never wedges the lock.

FIRING CONTROL: `test_the_unqueued_poll_of_v0026_starves_in_the_same_model` runs the v0.0.26 loop
(reproduced verbatim below) through the same model and asserts it starves — the model detects the
bug. Checked at build: with the turnstile wait removed from `_hold` (the v0.0.26 policy) both rule
tests red, and with v0.0.26's `oslock.py` dropped in the real-primitive test reds at 35-507 writes
per proposal (up to 7.3 s) where this policy measures 0-1 writes (~25 ms).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import errno
import multiprocessing as mp
import os
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import oslock
from mokata.oslock import LockTimeout, file_lock
from mokata.state import StateStore

_CTX = mp.get_context("spawn")

#: The rule's bound: the waiter may sit out the writer's critical section in flight and at most one
#: more. v0.0.26 measured 150-320 on Linux (real primitive) and never acquired in the model.
WRITER_CYCLES_BOUND = 2
#: Real processes on a shared CI runner: scheduling noise, not policy, can add a cycle or two.
REAL_WRITER_CYCLES_BOUND = 5


class _NoQueuePrimitive:
    """An exclusive lock with NO wait queue: `try_acquire` succeeds only when nobody holds the
    path, exactly the contract of `msvcrt.locking(LK_NBLCK)` (and `flock(LOCK_NB)`). Nothing in it
    remembers who asked first — any fairness has to come from the policy that drives it."""

    def __init__(self):
        self._guard = threading.Lock()
        self._owner = {}

    def open(self, path):
        return [path]                       # a fresh handle per open, like an fd

    def try_acquire(self, handle):
        with self._guard:
            if self._owner.get(handle[0]) is None:
                self._owner[handle[0]] = handle
                return
        raise OSError(errno.EACCES, "locked")

    def release(self, handle):
        with self._guard:
            if self._owner.get(handle[0]) is handle:
                self._owner[handle[0]] = None

    def close(self, handle):                # the OS drops a closed handle's lock
        self.release(handle)


def _v0026_hold(lock_path, timeout, poll_interval, prim):
    """v0.0.26's `file_lock` loop, verbatim but for the primitive: poll one non-blocking lock."""
    from contextlib import contextmanager

    @contextmanager
    def cm():
        fd = prim.open(lock_path)
        try:
            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    prim.try_acquire(fd)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(f"timed out: {lock_path}") from exc
                    time.sleep(poll_interval)
            try:
                yield
            finally:
                prim.release(fd)
        finally:
            prim.close(fd)
    return cm()


def _writer_cycles_while_waiting(hold, *, wait_budget):
    """Run a writer thread that re-takes the lock WITHOUT PAUSE (2 ms of GIL-free work inside, the
    shape of an fsync'd state write), then acquire as a proposal would. Returns the writer cycles
    completed while the proposal waited, or None if it never got the lock within `wait_budget`."""
    prim = _NoQueuePrimitive()
    path = "model/.run.json.lock"
    cycles = [0]
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            with hold(path, 600.0, 0.02, prim):
                cycles[0] += 1
                time.sleep(0.002)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        while cycles[0] < 5:                # the writer is in its loop
            if not t.is_alive():
                raise AssertionError("the model writer died before it started writing")
            time.sleep(0.005)
        before = cycles[0]
        try:
            with hold(path, wait_budget, 0.02, prim):
                return cycles[0] - before
        except LockTimeout:
            return None
    finally:
        stop.set()
        t.join(10)


class AWaiterIsServedWithinBoundedWriterCycles(unittest.TestCase):
    """The policy, on any OS, against a primitive that queues nobody (the Windows shape)."""

    def test_a_proposal_gets_the_lock_within_two_cycles_of_a_pause_free_writer(self):
        """While another mokata process rewrites the run's state in a loop with no pause, a spec
        proposal waiting on the same lock is let in after at most two of that writer's writes —
        it never sits behind the writer for as long as the writer keeps writing."""
        def hold(path, timeout, poll, prim):
            return oslock._hold(path, timeout, poll, prim)
        for attempt in range(20):            # the rule holds every time, not on average
            got = _writer_cycles_while_waiting(hold, wait_budget=30.0)
            self.assertIsNotNone(got, f"attempt {attempt}: the waiter never got the lock")
            self.assertLessEqual(got, WRITER_CYCLES_BOUND,
                                 f"attempt {attempt}: the waiter sat out {got} writer cycles")

    def test_the_unqueued_poll_of_v0026_starves_in_the_same_model(self):
        """FIRING CONTROL — the model must SEE the bug: v0.0.26's single polled lock, driven by the
        same writer, does not get the lock within the bound (measured: not within 2 s at all)."""
        served = 0
        for _ in range(5):
            got = _writer_cycles_while_waiting(_v0026_hold, wait_budget=1.0)
            if got is not None and got <= WRITER_CYCLES_BOUND:
                served += 1
        self.assertLess(served, 3, "the model no longer reproduces v0.0.26's starvation, so the "
                                   "rule test above proves nothing")

    def test_fairness_keeps_the_bounded_wait_and_its_named_error(self):
        """Fair is not unbounded: a lock held past the wait still raises `LockTimeout` naming the
        contended path, whether the waiter is stuck at the turnstile or at the lock itself."""
        prim = _NoQueuePrimitive()
        path = "model/.spec.json.lock"
        with oslock._hold(path, 1.0, 0.01, prim):
            with self.assertRaises(LockTimeout) as cm:
                with oslock._hold(path, 0.05, 0.01, prim):
                    pass
            self.assertIn(path, str(cm.exception))
        turn = prim.open(oslock.turnstile_path(path))
        prim.try_acquire(turn)                       # someone parked at the turnstile
        with self.assertRaises(LockTimeout) as cm:
            with oslock._hold(path, 0.05, 0.01, prim):
                pass
        self.assertIn(path, str(cm.exception))
        prim.release(turn)
        with oslock._hold(path, 1.0, 0.01, prim):    # and nothing was left held by the failures
            pass


# ------------------------------------------------------------------ the real OS primitive
def _state_writer(root, ready, stop):
    """Another mokata process: a read-modify-write of the run's state through StateStore with NO
    pause between writes. The 5 ms inside the lock stands for what a state write costs where the
    disclosure was measured — Windows' FlushFileBuffers + a retried os.replace — against a tmpfs
    fsync of microseconds here. The HOLD-to-GAP ratio is what starves a polling waiter: a long hold
    and a gap of a few syscalls."""
    from mokata.state import StateStore
    store = StateStore(root)

    def write(cur):
        time.sleep(0.005)
        return {"writes": (cur or {}).get("writes", 0) + 1}

    n = 0
    deadline = time.monotonic() + 120.0         # a test-harness backstop, never reached in a pass
    while not stop.is_set() and time.monotonic() < deadline:
        n += 1
        store.update("run", write, default={})
        if n == 20:
            ready.set()


def _wait_in_turnstile_then_die(lock_path, parked):
    from mokata import oslock as L
    fd = L._open(L.turnstile_path(lock_path))
    L._try_acquire(fd)                            # what a waiter holds while it waits
    parked.set()
    time.sleep(60.0)


class TheRealLockServesAWaiter(unittest.TestCase):
    """The same rule against the OS primitive in force here (`fcntl` on POSIX, `msvcrt` on Windows),
    with a real second process. On a `probe/**` push this runs on windows-latest."""

    PROPOSALS = 10

    def _proposals_against_a_pause_free_writer(self):
        """`PROPOSALS` successive proposals while one writer process keeps writing. Returns, per
        proposal, (writer writes it sat out, seconds waited). Several, because a single polled
        acquire can get lucky and land in the writer's gap — v0.0.26 measured here: ~1 in 3 of
        them sat out 140+ writes, the rest none."""
        with tempfile.TemporaryDirectory() as root:
            ready, stop = _CTX.Event(), _CTX.Event()
            p = _CTX.Process(target=_state_writer, args=(root, ready, stop))
            p.start()
            out = []
            try:
                self.assertTrue(ready.wait(60.0), "the writer process never started writing")
                store = StateStore(root)
                for i in range(self.PROPOSALS):
                    before = (store.read("run") or {}).get("writes", 0)
                    seen = {}

                    def propose(cur):
                        seen["writes"] = (cur or {}).get("writes", 0)
                        return dict(cur or {}, proposal=i)

                    started = time.perf_counter()
                    store.update("run", propose, default={})
                    out.append((seen["writes"] - before, round(time.perf_counter() - started, 3)))
                    time.sleep(0.05)               # the writer runs on between proposals
            finally:
                stop.set()
                p.join(30.0)
                if p.is_alive():
                    p.terminate()
            return out

    def _assert_served(self, label):
        got = self._proposals_against_a_pause_free_writer()
        starved = [g for g in got if g[0] > REAL_WRITER_CYCLES_BOUND]
        self.assertEqual(starved, [], f"{label}: proposals sat out (writes, seconds) {got}")

    def test_a_proposal_lands_within_a_few_writes_of_a_pause_free_writer(self):
        """Every proposal's state update lands within a handful of the other process's writes — not
        after hundreds of them (v0.0.26 on Linux: 140+ writes, ~2 s; Windows CI: ~20 s)."""
        self._assert_served(oslock.backend_name())

    @unittest.skipUnless(os.name == "nt", "CONTROL: the msvcrt branch only exists on Windows; the "
                         "policy it runs is covered here by the model and the fcntl run above")
    def test_windows_msvcrt_branch_serves_the_waiter(self):
        """On Windows the rule holds through `msvcrt.locking`, the branch the disclosure was about."""
        self.assertEqual(oslock.backend_name(), "msvcrt")
        self._assert_served("msvcrt")

    def test_a_waiter_that_dies_while_waiting_never_wedges_the_lock(self):
        """A process killed while parked at the turnstile releases it with its life (OS-held), so
        the next acquirer is not blocked by a dead waiter."""
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, ".run.json.lock")
            parked = _CTX.Event()
            p = _CTX.Process(target=_wait_in_turnstile_then_die, args=(lp, parked))
            p.start()
            self.assertTrue(parked.wait(30.0), "the child never parked at the turnstile")
            with self.assertRaises(LockTimeout):
                with file_lock(lp, timeout=0.2):
                    pass
            p.terminate()
            p.join(10.0)
            with file_lock(lp, timeout=5.0):
                pass

    def test_the_turnstile_is_a_hidden_sibling_with_no_content(self):
        """The extra sidecar is never an artifact: it sits next to the lock file, its name ends in
        `.lock` like every lock handle (no state/vault/session scan picks it up) and nothing is
        ever written into it."""
        with tempfile.TemporaryDirectory() as d:
            StateStore(d).write("run", {"a": 1})
            names = sorted(os.listdir(d))
            self.assertEqual(names, [".run.json.lock", ".run.json.lock.turn.lock", "run.json"])
            self.assertEqual(os.path.getsize(os.path.join(d, ".run.json.lock.turn.lock")), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
