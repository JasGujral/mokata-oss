"""0.0.29 E `AST-INDEX-NOT-SINGLE-FLIGHT` — two askers that reach a cold AST index start ONE parse
of the repo; the others wait (cancellably) and get the same index; a cancelled builder hands its
partial work to the next asker; an invalidation during a build is honoured by the NEXT asker
(cost ∝ churn), never by serving an empty index to the askers the build was for.

No sleeps and no wall-clock verdicts: every wait here is on an event the test controls, or on a
count of the waiter's own Cancel polls (lack of progress), never on a short clock.
"""
import os
import tempfile
import threading
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import ast_backend as AB
from mokata.knowledge import cancel
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.tree_overlay import Snapshot

N_FILES = 60
#: Cancel polls a waiter may make AFTER the event it waits for has happened before the test calls
#: it stuck. A count of the waiter's own progress, not a clock.
STUCK_POLLS = 400


def _tree(n=N_FILES):
    root = tempfile.mkdtemp(prefix="e-ast-")
    for i in range(n):
        d = os.path.join(root, f"pkg{i % 6}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"m{i}.py"), "w", encoding="utf-8") as fh:
            fh.write(f"def f{i}():\n    return g{i}()\n\n\ndef g{i}():\n    return {i}\n")
    return root


def _backend(root):
    return AB.AstBackend(root=root, grep=GrepBackend(root=root),
                         cache_dir=os.path.join(root, "_cache"))


class _Rig:
    """Instruments ONE backend: counts walks and parses, can hold the walk at its start (`go`),
    and records which threads are polling for Cancel while they wait (`waiting`)."""

    def __init__(self, b, hold=True):
        self.b = b
        self.walks = 0
        self.parses = 0
        self.go = threading.Event()
        if not hold:
            self.go.set()
        self.entered = threading.Event()
        self.lock = threading.Lock()
        self.waiting = {}                      # thread ident -> polls
        self.owner = None
        real_walk, real_parse = b._walk_index, b._parse_file
        self.after_parse = None

        def walk(*a, **kw):
            with self.lock:
                self.walks += 1
                self.owner = threading.get_ident()
            self.entered.set()
            self.go.wait()
            return real_walk(*a, **kw)

        def parse(ab):
            with self.lock:
                self.parses += 1
                n = self.parses
            if self.after_parse:
                self.after_parse(n)
            return real_parse(ab)
        b._walk_index = walk
        b._parse_file = parse
        self.real_cp = AB.checkpoint

        def cp():
            me = threading.get_ident()
            if me != self.owner:
                with self.lock:
                    self.waiting[me] = self.waiting.get(me, 0) + 1
            self.real_cp()
        self.patch = mock.patch.object(AB, "checkpoint", cp)
        self.slice = mock.patch.object(AB, "_WAIT_SLICE_S", 0.005, create=True)
        rig = self

        class CountingEvent(threading.Event):
            """The build's Event: every wait slice a waiter spends on it is counted, so a waiter
            that waits WITHOUT polling for Cancel is still seen waiting (and seen stuck)."""

            def wait(self, timeout=None):
                me = threading.get_ident()
                if me != rig.owner:
                    with rig.lock:
                        rig.slices[me] = rig.slices.get(me, 0) + 1
                return super().wait(timeout)
        self.slices = {}
        self.events = mock.patch.object(AB, "threading", types.SimpleNamespace(
            Event=CountingEvent, Lock=threading.Lock), create=True)

    def __enter__(self):
        self.patch.start()
        self.slice.start()
        self.events.start()
        return self

    def __exit__(self, *exc):
        self.go.set()
        self.patch.stop()
        self.slice.stop()
        self.events.stop()

    def waiters(self):
        with self.lock:
            return len(set(self.waiting) | set(self.slices))

    def polls(self, ident):
        """How many times `ident` showed it is still waiting: Cancel polls or wait slices."""
        with self.lock:
            return max(self.waiting.get(ident, 0), self.slices.get(ident, 0))

    def until_waiting(self, n):
        """Block until n threads other than the owner are polling — by their own polls — or until a
        second walk started (no single flight: the caller's assertions then say so)."""
        while self.waiters() < n and self.walks <= 1:
            self.entered.wait()
            threading.Event().wait(0.001)       # yield; the verdict is the count, not the clock


def _join_or_stuck(rig, t, ident_box):
    """Join `t`; a thread that keeps polling STUCK_POLLS times without ending is reported stuck."""
    start = None
    while t.is_alive():
        t.join(0.01)
        ident = ident_box.get("id")
        if ident is None:
            continue
        p = rig.polls(ident)
        start = p if start is None else start
        if p - start > STUCK_POLLS:
            return False
    return True


def _join(tc, rig, t):
    """Join `t`, or fail the test when it keeps showing it waits (a hang reads as a red, never as a
    run that does not end); test threads are daemons, so a stuck one cannot hold the process."""
    tc.assertTrue(_join_or_stuck(rig, t, {"id": t.ident}), "a thread was left waiting")


class OneWalk(unittest.TestCase):

    def test_eight_cold_askers_and_quick_open_start_one_walk_and_share_one_index(self):
        root = _tree()
        b = _backend(root)
        out, errs = {}, []
        with _Rig(b) as rig:
            def ask(i):
                try:
                    if i == 0:
                        rows = b.definitions()                 # ⌘P's question
                    else:
                        rows = b.query("callers", f"g{i}").references
                    out[i] = (b.index_generation, len(rows))
                except BaseException as e:  # noqa: BLE001 - reported below
                    errs.append(e)
            ts = [threading.Thread(target=ask, args=(i,), daemon=True) for i in range(9)]
            for t in ts:
                t.start()
            rig.entered.wait()
            rig.until_waiting(8)                                # all eight others are waiting
            rig.go.set()
            for t in ts:
                _join(self, rig, t)
        self.assertFalse(errs, errs)
        self.assertEqual(rig.walks, 1, "a second asker walked the repo again")
        self.assertEqual(rig.parses, N_FILES)
        self.assertEqual({g for g, _ in out.values()}, {1})
        self.assertEqual(out[0][1], 2 * N_FILES)                # every definition, for every asker
        for i in range(1, 9):
            self.assertEqual(out[i][1], 1, i)


class HandOff(unittest.TestCase):

    def test_a_cancelled_builder_hands_its_partial_work_to_the_waiter(self):
        root = _tree()
        b = _backend(root)
        flag = threading.Event()
        res, errs, box = {}, [], {}
        with _Rig(b) as rig:
            def owner():
                with cancel.scope(flag):
                    try:
                        b.definitions()
                    except cancel.Cancelled:
                        res["owner"] = "cancelled"

            def waiter():
                box["id"] = threading.get_ident()
                try:
                    res["waiter"] = len(b.definitions())
                except BaseException as e:  # noqa: BLE001
                    errs.append(e)

            def after(n):
                if n == N_FILES // 3 and threading.get_ident() == rig.owner \
                        and not flag.is_set():
                    flag.set()                                  # the owner's Cancel, mid-walk
            rig.after_parse = after
            to = threading.Thread(target=owner, daemon=True)
            to.start()
            rig.entered.wait()
            tw = threading.Thread(target=waiter, daemon=True)
            tw.start()
            rig.until_waiting(1)
            rig.go.set()
            _join(self, rig, to)
            self.assertTrue(_join_or_stuck(rig, tw, box), "the waiter was never woken")
        self.assertFalse(errs, errs)
        self.assertEqual(res.get("owner"), "cancelled")
        self.assertEqual(res.get("waiter"), 2 * N_FILES)
        self.assertEqual(rig.walks, 2)                          # the waiter took the build over
        self.assertEqual(rig.parses, N_FILES, "a file was parsed twice across the hand-off")

    def test_a_waiters_cancel_ends_its_wait_never_the_build(self):
        root = _tree()
        b = _backend(root)
        mine = threading.Event()
        res = {}
        with _Rig(b) as rig:
            to = threading.Thread(target=lambda: res.setdefault("owner", len(b.definitions())), daemon=True)
            to.start()
            rig.entered.wait()

            def waiter():
                with cancel.scope(mine):
                    try:
                        b.definitions()
                        res["waiter"] = "answered"
                    except cancel.Cancelled:
                        res["waiter"] = "cancelled"
            tw = threading.Thread(target=waiter, daemon=True)
            tw.start()
            rig.until_waiting(1)
            if rig.walks > 1:                          # no single flight: it walked instead
                rig.go.set()
                _join(self, rig, tw)
                _join(self, rig, to)
                self.fail(f"the second asker started its own walk ({rig.walks} walks)")
            box = {"id": tw.ident}
            mine.set()
            self.assertTrue(_join_or_stuck(rig, tw, box),       # its wait ended while the walk
                            "the waiter's Cancel did not end its wait")   # is held
            self.assertFalse(rig.go.is_set())
            rig.go.set()
            _join(self, rig, to)
        self.assertEqual(res["waiter"], "cancelled")
        self.assertEqual(res["owner"], 2 * N_FILES)
        self.assertEqual(rig.walks, 1)


class InvalidateDuringBuild(unittest.TestCase):

    def test_the_build_serves_its_askers_and_the_next_asker_walks_only_the_churn(self):
        root = _tree()
        b = _backend(root)
        res = {}
        with _Rig(b) as rig:
            t = threading.Thread(target=lambda: res.setdefault("first", len(b.definitions())), daemon=True)
            t.start()
            rig.entered.wait()
            b.invalidate()                              # a file changed while the walk runs
            with open(os.path.join(root, "pkg0", "m0.py"), "a", encoding="utf-8") as fh:
                fh.write("\n\ndef added():\n    return 0\n")
            rig.go.set()
            _join(self, rig, t)
            parsed = rig.parses
            self.assertEqual(res["first"], 2 * N_FILES + 1)    # never an empty index
            with open(os.path.join(root, "pkg1", "m1.py"), "a", encoding="utf-8") as fh:
                fh.write("\n\ndef added_again():\n    return 1\n")
            rows = b.definitions()
        self.assertEqual(rig.walks, 2, "the invalidation was not honoured by the next asker")
        self.assertEqual(rig.parses - parsed, 1, "the next walk re-parsed more than the churn")
        self.assertEqual(len(rows), 2 * N_FILES + 2)
        self.assertEqual(b.index_generation, 2)

    def test_an_invalidate_with_no_build_running_still_drops_the_index(self):
        b = _backend(_tree(4))
        b.definitions()
        b.invalidate()
        self.assertIsNone(b.index_generation)


class _Overlay:
    def __init__(self, small):
        self.small = small

    def current(self):
        return Snapshot(version=1, changed={}, edges={}, small=self.small)


class Overlay(unittest.TestCase):

    def test_four_askers_on_a_worktree_overlay_walk_the_main_index_once_and_merge_once(self):
        root = _tree()
        main = _backend(root)
        ov = AB.OverlayAstBackend(main, root, _Overlay(None))
        gens, errs = [], []
        with _Rig(main) as rig:
            def ask():
                try:
                    ov.definitions()
                    gens.append(ov.index_generation)
                except BaseException as e:  # noqa: BLE001
                    errs.append(e)
            ts = [threading.Thread(target=ask, daemon=True) for _ in range(4)]
            for t in ts:
                t.start()
            rig.entered.wait()
            rig.until_waiting(3)
            rig.go.set()
            for t in ts:
                _join(self, rig, t)
        self.assertFalse(errs, errs)
        self.assertEqual(rig.walks, 1)
        self.assertEqual(gens, [1, 1, 1, 1], "the overlay merged more than once")


if __name__ == "__main__":
    unittest.main()
