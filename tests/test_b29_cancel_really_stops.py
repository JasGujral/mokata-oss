"""0.0.29 lane B — a cancel really stops the work, and nothing mokata starts outlives it.

`CANCEL-DOES-NOT-STOP-WORK` (Jas, 0.0.28 release run): an MCP call the client cancelled ran on to
its end; the suite tree it started outlived the server; a cancelled write could run twice on a
re-call. The design is `docs/build/handoff/0029-design/B-cancel-process-door.md` (§5 is this file's
matrix; the cell number is in each test's docstring with its label: **force** = red on the base,
**guard** = pins behaviour that already held, **probe** = settled on the mirror's Windows matrix).

**Every wait here is an event the test controls, never an interval:**
  * "the tree is gone" — every member of a fake tree opens a TCP connection to a test-owned
    `Witness` and holds it; the test's blocking `recv` returns EOF exactly when that member has died
    (on every OS: a process's sockets close when it dies, whoever killed it). The scale cell uses a
    witness FIFO the members inherit (POSIX).
  * "the work reached X" — a `threading.Event` the body sets, or a byte the fake writes.
  * an "infinite" tool body loops until a test-owned `release` Event, set by `addCleanup`.
  * A hang is caught by `HANG_S` (hang detection, not a deadline: the suite's own watchdog would
    otherwise catch it) — `MOKATA_TEST_HANG_S` shortens it for a RED run or a mutant run.

Kill paths in the test itself go through `quiet_spawn.signal_pid` only (COMMON Method 7).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk  # noqa: E402  (the SDK disposition gate: ABSENT/BROKEN skip, INCOMPATIBLE fails)

from mokata import quiet_spawn as Q  # noqa: E402
from mokata.knowledge import cancel as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
#: Hang detection (not a deadline): how long a wait for an event may take before the test says the
#: event never came.
HANG_S = float(os.environ.get("MOKATA_TEST_HANG_S", "300"))
POSIX = os.name != "nt"
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)

# A fake tree. Each member connects to the witness and sends its pid, starts its child (depth-1),
# waits for that child's "ok" on the child's stderr pipe (so the whole subtree is connected before
# it goes on), then says "ok" itself and parks — or, in `leave` mode, the root exits at once leaving
# its child holding the output; `chatty` writes output forever.
FAKE = r'''
import os, socket, subprocess, sys, time
port, depth, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
s = socket.create_connection(("127.0.0.1", port))
s.sendall(("%d\n" % os.getpid()).encode())
if depth > 1:
    child = subprocess.Popen([sys.executable, __file__, str(port), str(depth - 1),
                              "chatty" if mode == "chatty" else "hang"],
                             stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    child.stderr.readline()
try:
    os.write(2, b"ok\n")
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
except OSError:
    pass
if mode == "leave":
    sys.stdout.write("leader done\n")
    sys.stdout.flush()
    sys.exit(0)
if mode == "chatty":
    while True:
        sys.stdout.write("x" * 4095 + "\n")
        sys.stdout.flush()
        time.sleep(0.01)
while True:
    time.sleep(3600)
'''

_FAKE_DIR = tempfile.mkdtemp(prefix="b29-fake-")
FAKE_PATH = os.path.join(_FAKE_DIR, "fake_tree.py")
with open(FAKE_PATH, "w", encoding="utf-8") as _fh:
    _fh.write(FAKE)


def tearDownModule():
    import shutil
    shutil.rmtree(_FAKE_DIR, ignore_errors=True)


def fake_argv(port, depth=3, mode="hang"):
    return [sys.executable, FAKE_PATH, str(port), str(depth), mode]


def fake_line(port, depth=3, mode="hang"):
    argv = fake_argv(port, depth, mode)
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


class Witness:
    """The tree-gone event: each member holds one connection; EOF on all of them = all dead."""

    def __init__(self, test):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(512)
        self.port = self.srv.getsockname()[1]
        self.conns, self.pids, self.ended = [], [], False
        test.addCleanup(self.cleanup)

    def members(self, n):
        """Block until `n` members have connected; their pids."""
        self.srv.settimeout(HANG_S)
        while len(self.conns) < n:
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                raise AssertionError(f"only {len(self.conns)} of {n} tree members ever started")
            c.settimeout(HANG_S)
            line = b""
            while not line.endswith(b"\n"):
                try:
                    b = c.recv(64)
                except socket.timeout:
                    raise
                except OSError:               # already ended and reset (Windows): it started
                    break
                if not b:
                    break
                line += b
            self.conns.append(c)
            self.pids.append(int(line or b"0"))
        return list(self.pids)

    def gone(self):
        """True once every member has died (EOF on its connection); False if one is still alive
        after `HANG_S` (the hang the cell exists to catch)."""
        for c in self.conns:
            try:
                while c.recv(4096):
                    pass
            except socket.timeout:
                return False
            except OSError:
                pass                          # reset by the dying process (Windows): gone
        self.ended = True
        return True

    def cleanup(self):
        if not self.ended:                    # a red run: never leave its tree behind
            for pid in self.pids:
                try:
                    Q.signal_pid(pid, SIGKILL)
                except (OSError, ValueError):
                    pass
        for c in self.conns:
            c.close()
        self.srv.close()


def in_thread(fn, *a, **kw):
    """Run `fn` on a thread; `(thread, box)` — `box` holds `out` or `exc`."""
    box = {}

    def go():
        try:
            box["out"] = fn(*a, **kw)
        except BaseException as exc:          # noqa: BLE001 - the test inspects it
            box["exc"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t, box


def joined(test, t):
    t.join(HANG_S)
    test.assertFalse(t.is_alive(), "the thread never finished (hang)")


# ====================================================================== the MCP server, in-process
from mokata.mcp import server as MS  # noqa: E402


class _Mcp(unittest.TestCase):
    """`_serve` on real threads, with the registry and the event log replaced by spies. Each
    request is a `_Ticket` (as `_offloop` makes one) and `_join_or_start` signals when a ticket has
    attached — the event the test waits on."""

    budget = 600.0

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        self.events = []
        self._attached = {}
        self._lock = threading.Lock()
        real_join = MS._join_or_start

        def spy_join(*a, **kw):
            out = real_join(*a, **kw)
            ticket = a[6] if len(a) > 6 else kw.get("ticket")
            self._attach_event(ticket).set()
            return out
        for target, new in ((MS, {"_spawn_registration": lambda _p: None,
                                  "_emit_tool_call": self._event,
                                  "_mcp_timeout_for": lambda _op, _path=".": self.budget,
                                  "_join_or_start": spy_join}),):
            for name, val in new.items():
                p = mock.patch.object(target, name, val)
                p.start()
                self.addCleanup(p.stop)
        self.releases = []
        self.addCleanup(self._drain)

    def _event(self, path, op, kind, result, elapsed_ms):
        with self._lock:
            self.events.append((op, result))

    def _attach_event(self, ticket):
        with self._lock:
            return self._attached.setdefault(id(ticket), threading.Event())

    def release(self):
        ev = threading.Event()
        self.releases.append(ev)
        return ev

    def _drain(self):
        for ev in self.releases:
            ev.set()
        with MS._INFLIGHT_LOCK:
            calls = list(MS._INFLIGHT.values())
        for c in calls:
            c.lever.pull()
            c.done.wait(HANG_S)
        with MS._INFLIGHT_LOCK:
            MS._INFLIGHT.clear()
            getattr(MS, "_TOMBSTONES", {}).clear()
            MS._RECEIPTS.clear()       # X1: a receipt one test left must never red the next one

    def request(self, wrapper, ticket=None, **kw):
        """Start one request on its own thread; returns `(ticket, thread, box)` once it attached."""
        ticket = ticket or MS._Ticket()
        t, box = in_thread(MS._with_ticket, ticket, wrapper, **kw)
        self.assertTrue(self._attach_event(ticket).wait(HANG_S), "the request never attached")
        return ticket, t, box

    def looping_read(self, runs, started, release, outcome=None):
        """A read tool that loops on checkpoints until `release` (an "infinite" query)."""
        def probe_tool(path="."):
            runs.append(threading.get_ident())
            started.set()
            try:
                while not release.is_set():
                    C.checkpoint()
                    release.wait(0.02)
            except C.Cancelled:
                if outcome is not None:
                    outcome["cancelled"] = True
                raise
            return {"status": "ok", "ok": True, "runs": len(runs)}
        return probe_tool


class McpCancelReachesTheWorker(_Mcp):
    def test_cancel_stops_the_body_and_one_event_says_cancelled(self):
        """#1 force — the cancelled request answers nothing; the body ends `Cancelled`; the work's
        one `ToolCall` event is `ok=False` with reason `cancelled`."""
        runs, started, release, outcome = [], threading.Event(), self.release(), {}
        w = MS._serve(self.looping_read(runs, started, release, outcome), name="probe_tool")
        ticket, t, box = self.request(w, path=self.d)
        self.assertTrue(started.wait(HANG_S))
        call = ticket.call
        ticket.cancel()
        joined(self, t)
        self.assertIsNone(box.get("out"), "a cancelled request answers nothing")
        self.assertTrue(call.done.wait(HANG_S), "the worker never stopped")
        self.assertTrue(outcome.get("cancelled"), "the body never saw Cancelled")
        self.assertEqual("cancelled", call.out.get("reason_code"))
        self.assertIs(False, call.out.get("committed"))
        self.assertEqual(1, len(runs))
        for th in [x for x in threading.enumerate() if x.name == "mokata-mcp-probe_tool"]:
            joined(self, th)
        mine = [r for op, r in self.events if op == "probe_tool"]
        self.assertEqual(1, len(mine), self.events)
        self.assertEqual("cancelled", mine[0].get("reason_code"))


class McpJoinedCancel(_Mcp):
    def test_cancelling_one_of_two_joined_requests_leaves_the_work_running(self):
        """#3 first half, guard — the other request still gets the result; one run."""
        runs, started, release = [], threading.Event(), self.release()
        w = MS._serve(self.looping_read(runs, started, release), name="probe_join")
        t1, th1, _b1 = self.request(w, path=self.d)
        self.assertTrue(started.wait(HANG_S))
        t2, th2, b2 = self.request(w, path=self.d)
        self.assertIs(t1.call, t2.call, "the same call must be joined")
        t1.cancel()
        self.assertFalse(t1.call.lever.is_set(), "one cancel of two pulled the lever")
        release.set()
        joined(self, th2)
        self.assertEqual("ok", b2["out"]["status"])
        self.assertEqual(1, len(runs))

    def test_cancelling_both_stops_the_work(self):
        """#3 second half, force — the LAST ticket's cancel pulls."""
        runs, started, release, outcome = [], threading.Event(), self.release(), {}
        w = MS._serve(self.looping_read(runs, started, release, outcome), name="probe_join2")
        t1, th1, _ = self.request(w, path=self.d)
        self.assertTrue(started.wait(HANG_S))
        t2, th2, _ = self.request(w, path=self.d)
        call = t1.call
        t1.cancel()
        t2.cancel()
        self.assertTrue(call.done.wait(HANG_S))
        self.assertTrue(outcome.get("cancelled"))
        joined(self, th1)
        joined(self, th2)


class McpWriteNeverRerun(_Mcp):
    def _write(self, runs, started, release):
        def remember_probe(path=".", text=""):
            runs.append(text)
            started.set()
            release.wait(HANG_S)              # no checkpoint: a write runs to its end
            return {"status": "ok", "ok": True, "proposals": len(runs)}
        return MS._serve(remember_probe, name="remember_probe", kind="write")

    def test_a_cancelled_write_is_collected_by_the_re_call_never_run_again(self):
        """#4 force — cancel, release, re-call: the re-call collects; the body ran once."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._write(runs, started, release)
        t1, th1, b1 = self.request(w, path=self.d, text="x")
        self.assertTrue(started.wait(HANG_S))
        call = t1.call
        t1.cancel()
        joined(self, th1)
        self.assertIsNone(b1.get("out"))
        self.assertTrue(call.collectable)
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        _t2, th2, b2 = self.request(w, path=self.d, text="x")
        joined(self, th2)
        self.assertEqual(["x"], runs, "the write ran a second time")
        self.assertEqual(1, b2["out"]["proposals"])
        self.assertIn("collected", b2["out"])

    def test_a_kept_write_is_still_collected_after_the_keep_window(self):
        """#5 force — 1800 s later (the keep clock moved, not the wall) it is still collected."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._write(runs, started, release)
        t1, th1, _ = self.request(w, path=self.d, text="y")
        self.assertTrue(started.wait(HANG_S))
        call = t1.call
        t1.cancel()
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        with MS._INFLIGHT_LOCK:
            call.finished_at -= MS._RESULT_KEEP_SECONDS + 60
        _t2, th2, b2 = self.request(w, path=self.d, text="y")
        joined(self, th2)
        self.assertEqual(["y"], runs)
        self.assertIn("collected", b2["out"])


class McpRestartUnderLock(_Mcp):
    def test_two_simultaneous_re_calls_of_a_cancelled_read_start_one_run(self):
        """#6 force — a read that ended Cancelled is replaced exactly once."""
        runs, started, release, outcome = [], threading.Event(), self.release(), {}
        first_release = self.release()
        state = {"n": 0}

        def probe_restart(path="."):
            state["n"] += 1
            runs.append(state["n"])
            gate = first_release if state["n"] == 1 else release
            started.set()
            while not gate.is_set():
                C.checkpoint()
                gate.wait(0.02)
            return {"status": "ok", "ok": True}
        w = MS._serve(probe_restart, name="probe_restart")
        t1, th1, _ = self.request(w, path=self.d)
        self.assertTrue(started.wait(HANG_S))
        old = t1.call
        t1.cancel()
        self.assertTrue(old.done.wait(HANG_S))
        self.assertTrue(old.cancelled)
        barrier = threading.Barrier(2)
        tickets = [MS._Ticket(), MS._Ticket()]
        threads = []
        for tk in tickets:
            def go(tk=tk):
                barrier.wait(HANG_S)
                return MS._with_ticket(tk, w, path=self.d)
            threads.append(in_thread(go))
        for tk in tickets:
            self.assertTrue(self._attach_event(tk).wait(HANG_S))
        self.assertIs(tickets[0].call, tickets[1].call, "two fresh runs started")
        self.assertIsNot(old, tickets[0].call)
        release.set()
        for th, box in threads:
            joined(self, th)
            self.assertEqual("ok", box["out"]["status"])
        self.assertEqual([1, 2], runs)


class McpLeaveIdempotent(_Mcp):
    def _two(self):
        runs, started, release = [], threading.Event(), self.release()
        fn = self.looping_read(runs, started, release)
        t1, t2 = MS._Ticket(), MS._Ticket()
        c1, _ = MS._join_or_start("probe_leave", None, fn, (), {"path": self.d}, self.d, t1)
        c2, j2 = MS._join_or_start("probe_leave", None, fn, (), {"path": self.d}, self.d, t2)
        self.assertIs(c1, c2)
        self.assertTrue(j2)
        return c1, t1, t2

    def test_an_answer_racing_a_cancel_detaches_once_and_pulls_nothing(self):
        """#7 force — answered then cancelled: one leave; the other ticket keeps the work."""
        call, t1, t2 = self._two()
        with MS._INFLIGHT_LOCK:
            self.assertTrue(MS._leave(t1, call))
        t1.cancel()
        self.assertFalse(call.lever.is_set(), "a late cancel of an answered ticket pulled")
        self.assertEqual({t2}, call.tickets)
        with MS._INFLIGHT_LOCK:
            self.assertFalse(MS._leave(t1, call), "leave is once")

    def test_a_cancel_then_an_answer_also_leaves_once(self):
        """#7 force — the other order."""
        call, t1, t2 = self._two()
        t1.cancel()
        with MS._INFLIGHT_LOCK:
            self.assertFalse(MS._leave(t1, call))
        self.assertEqual({t2}, call.tickets)
        self.assertFalse(call.lever.is_set())


class McpGoneTicketStartsNothing(_Mcp):
    def test_a_ticket_cancelled_before_it_attached_starts_no_work(self):
        """#8 force."""
        ran = []
        t = MS._Ticket()
        t.cancel()
        call, joined_ = MS._join_or_start("probe_gone", None, lambda path=".": ran.append(1),
                                          (), {"path": self.d}, self.d, t)
        self.assertIsNone(call)
        self.assertFalse(joined_)
        self.assertEqual([], ran)
        with MS._INFLIGHT_LOCK:
            self.assertFalse(any(c.op == "probe_gone" for c in MS._INFLIGHT.values()))


class McpCancelAfterRunning(_Mcp):
    def test_running_then_a_re_call_then_cancel_ends_the_tree(self):
        """#9 force — `running` answered (budget 0), a re-call joins, its cancel ends the tree."""
        wit = Witness(self)
        outcome = {}

        def probe_suite(path="."):
            try:
                return {"status": "ok", "ok": True,
                        "rc": Q.run_jobbed(fake_argv(wit.port, 3), timeout=None)[0]}
            except C.Cancelled:
                outcome["cancelled"] = True
                raise
        w = MS._serve(probe_suite, name="probe_suite")
        self.budget = 0.0
        _t1, th1, b1 = self.request(w, path=self.d)
        joined(self, th1)
        self.assertEqual("running", b1["out"]["status"])
        wit.members(3)
        self.budget = 600.0
        t2, th2, b2 = self.request(w, path=self.d)
        t2.cancel()
        self.assertTrue(wit.gone(), "the tree outlived the cancel")
        joined(self, th2)
        self.assertIsNone(b2.get("out"))
        self.assertTrue(t2.call.done.wait(HANG_S))
        self.assertTrue(outcome.get("cancelled"))


class McpAbandonedRunningRunsOn(_Mcp):
    def test_running_never_re_called_runs_to_its_end(self):
        """#10 guard (disclosed) — no re-call is not a cancel."""
        runs, started, release = [], threading.Event(), self.release()
        w = MS._serve(self.looping_read(runs, started, release), name="probe_abandon")
        self.budget = 0.0
        t1, th1, b1 = self.request(w, path=self.d)
        joined(self, th1)
        self.assertEqual("running", b1["out"]["status"])
        call = t1.call
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        self.assertEqual("ok", call.out["status"])
        self.assertFalse(call.lever.is_set())


def _git(d, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    subprocess.run(["git", *args], cwd=d, check=True, capture_output=True, env=env)


class ANewTreeIsANewRun(_Mcp):
    def setUp(self):
        super().setUp()
        _git(self.d, "init", "-q")
        _git(self.d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
             "--allow-empty", "-m", "base")
        with open(os.path.join(self.d, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")

    def _baseline(self, runs, release):
        def baseline(path="."):
            runs.append(1)
            release.wait(HANG_S)
            return {"status": "ok", "ok": True, "run": len(runs)}
        return MS._serve(baseline, name="baseline")

    def _finished_after_running(self, w, release):
        self.budget = 0.0
        t1, th1, b1 = self.request(w, path=self.d)
        joined(self, th1)
        self.assertEqual("running", b1["out"]["status"])
        release.set()
        self.assertTrue(t1.call.done.wait(HANG_S))
        self.budget = 600.0

    def test_an_edit_after_the_run_makes_the_same_call_a_new_run(self):
        """#11 force — the kept result describes a tree that no longer exists."""
        runs, release = [], self.release()
        w = self._baseline(runs, release)
        self._finished_after_running(w, release)
        with open(os.path.join(self.d, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 22\n")
        _t, th, box = self.request(w, path=self.d)
        joined(self, th)
        self.assertEqual(2, len(runs), "a stale result was served for an edited tree")
        self.assertEqual(2, box["out"]["run"])

    def test_the_same_tree_collects_the_kept_result(self):
        """#12 guard — one run, collected."""
        runs, release = [], self.release()
        w = self._baseline(runs, release)
        self._finished_after_running(w, release)
        _t, th, box = self.request(w, path=self.d)
        joined(self, th)
        self.assertEqual(1, len(runs))
        self.assertEqual(1, box["out"]["run"])


class GatedWake(threading.Event):
    """A ticket's wake event whose WAIT is parked until `proceed` is set (ported from the B1
    verifier's `GatedWake`): the serve thread that owns the ticket cannot see that its work finished
    until the test says so — the cancel / re-call then lands in the real finish→collect window."""

    def __init__(self):
        super().__init__()
        self.proceed = threading.Event()
        self.parked = threading.Event()

    def wait(self, timeout=None):
        self.parked.set()
        self.proceed.wait(HANG_S)
        return super().wait(timeout)


def gated_ticket():
    t = MS._Ticket()
    t.wake = GatedWake()
    return t


class AWriteIsCollectedInEveryOrder(_Mcp):
    """#4 widened (B1 verifier M1) — a write is collected, never re-run, in whatever order the
    cancel, the finish and the re-call arrive. cancel→finish→re-call is #4 above."""

    def _write(self, runs, started=None, release=None):
        def remember_probe(path=".", text=""):
            runs.append(text)
            if started is not None:
                started.set()
            if release is not None:
                release.wait(HANG_S)
            return {"status": "ok", "ok": True, "proposals": len(runs)}
        return MS._serve(remember_probe, name="remember_probe", kind="write")

    def _finished_and_parked(self, w, text):
        ticket = gated_ticket()
        _t, th, box = self.request(w, ticket=ticket, path=self.d, text=text)
        self.assertTrue(ticket.call.done.wait(HANG_S))          # the write ran to its end
        self.assertTrue(ticket.wake.parked.wait(HANG_S))         # its waiter has not collected
        return ticket, th, box

    def _work_events(self):
        for th in [x for x in threading.enumerate() if x.name == "mokata-mcp-remember_probe"]:
            joined(self, th)
        return [r for op, r in self.events if op == "remember_probe"]

    def test_finish_then_cancel_then_re_call_collects(self):
        """force (verifier A1a) — the cancel lands after the finish, before the waiter collected."""
        runs = []
        w = self._write(runs)
        ticket, th, box = self._finished_and_parked(w, "x")
        ticket.cancel()
        ticket.wake.proceed.set()
        joined(self, th)
        self.assertIsNone(box.get("out"), "the cancelled request answers nothing")
        self.assertEqual(1, len(self._work_events()),
                         "the work's one event must not wait for a re-call that may never come")
        _t2, th2, b2 = self.request(w, path=self.d, text="x")
        joined(self, th2)
        self.assertEqual(["x"], runs, "the write ran twice: the first result was lost")
        self.assertEqual(1, b2["out"]["proposals"])
        self.assertIn("collected", b2["out"])
        self.assertEqual(1, len(self._work_events()), "the work's one ToolCall event")

    def test_finish_then_re_call_then_cancel_serves_the_re_call(self):
        """force — the re-call joins in the window; the first request is then cancelled."""
        runs = []
        w = self._write(runs)
        ticket, th, box = self._finished_and_parked(w, "y")
        _t2, th2, b2 = self.request(w, path=self.d, text="y")
        joined(self, th2)
        ticket.cancel()
        ticket.wake.proceed.set()
        joined(self, th)
        self.assertEqual(["y"], runs, "a re-call in the finish→collect window re-ran the write")
        self.assertEqual(1, b2["out"]["proposals"])
        self.assertIsNone(box.get("out"))

    def test_finish_then_re_call_with_no_cancel_runs_once(self):
        """force (verifier A1b) — both requests get the one result."""
        runs = []
        w = self._write(runs)
        ticket, th, b1 = self._finished_and_parked(w, "z")
        _t2, th2, b2 = self.request(w, path=self.d, text="z")
        joined(self, th2)
        ticket.wake.proceed.set()
        joined(self, th)
        self.assertEqual(["z"], runs)
        self.assertEqual(1, b1["out"]["proposals"])
        self.assertEqual(1, b2["out"]["proposals"])
        self.assertEqual(1, len(self._work_events()), "one event per piece of work")

    def test_cancel_then_re_call_while_live_joins_the_pulled_write(self):
        """force (verifier A1d; kills V-N4) — a live write whose lever is pulled is joined."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._write(runs, started, release)
        t1, th1, _ = self.request(w, path=self.d, text="q")
        self.assertTrue(started.wait(HANG_S))
        t1.cancel()
        joined(self, th1)
        self.assertTrue(t1.call.lever.is_set())
        t2, th2, b2 = self.request(w, path=self.d, text="q")
        self.assertIs(t1.call, t2.call, "a live pulled write was restarted, not joined")
        release.set()
        joined(self, th2)
        self.assertEqual(["q"], runs)
        self.assertEqual(1, b2["out"]["proposals"])

    def test_a_told_running_write_is_collected_after_the_keep_window(self):
        """force (B1 re-verifier A1i) — a write answered `running`, finished, and not re-called
        before the prune: the prune keeps it (driven by the prune, not a clock), and a later
        identical call collects it instead of running the write again."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._write(runs, started, release)
        self.budget = 0.0
        t1, th1, b1 = self.request(w, path=self.d, text="tr")
        joined(self, th1)
        self.assertEqual("running", b1["out"]["status"])
        self.budget = 600.0
        release.set()
        call = t1.call
        self.assertTrue(call.done.wait(HANG_S))
        with MS._INFLIGHT_LOCK:
            call.finished_at -= MS._RESULT_KEEP_SECONDS + 60
            MS._prune_kept(time.perf_counter())
            self.assertIs(call, MS._INFLIGHT.get(call.key), "the prune dropped an uncollected write")
        _t2, th2, b2 = self.request(w, path=self.d, text="tr")
        joined(self, th2)
        self.assertEqual(["tr"], runs, "a told-running write was pruned and RE-RUN")
        self.assertEqual(1, b2["out"]["proposals"])

    def test_two_simultaneous_re_callers_of_a_collectable_write_get_one_result(self):
        """guard (verifier A1c)."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._write(runs, started, release)
        t1, th1, _ = self.request(w, path=self.d, text="r")
        self.assertTrue(started.wait(HANG_S))
        call = t1.call
        t1.cancel()
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        joined(self, th1)
        tickets, ths, boxes = [], [], []
        for _ in range(2):                                     # both attach before either wakes
            tk = gated_ticket()
            _t, th, b = self.request(w, ticket=tk, path=self.d, text="r")
            tickets.append(tk)
            ths.append(th)
            boxes.append(b)
        for tk in tickets:
            tk.wake.proceed.set()
        for th in ths:
            joined(self, th)
        self.assertEqual(["r"], runs)
        self.assertTrue(all(b.get("out", {}).get("proposals") == 1 for b in boxes), boxes)


class KeptWritesAreBounded(_Mcp):
    """Round 4 (verifier M3): the uncollected writes the server holds are capped; the oldest is
    evicted to a tombstone that says "it already ran" once and never lets it run again; the
    tombstones are bounded by age and count; a kept result says when it ran. Every step is driven
    by the prune / eviction (`_prune_kept`, the stored times moved), never by a clock."""

    def setUp(self):
        super().setUp()
        self.runs = []
        self.hold = {}

        def remember_probe(path=".", text=""):
            self.runs.append(text)
            gate = self.hold.get(text)
            if gate is not None:
                gate.wait(HANG_S)
            return {"status": "ok", "ok": True, "text": text}
        self.w = MS._serve(remember_probe, name="remember_probe", kind="write")

    def _told_running_and_finished(self, text, finished_at=None):
        """The caller is told `running`, the write then finishes, nobody collects it. A
        `finished_at` given is written over the clock reading the finish stored (an age the test
        controls); omitted, the finish stays as it happened."""
        self.hold[text] = self.release()
        self.budget = 0.0
        t, th, b = self.request(self.w, path=self.d, text=text)
        joined(self, th)
        self.budget = 600.0
        self.assertEqual("running", b["out"]["status"])
        self.hold.pop(text).set()
        self.assertTrue(t.call.done.wait(HANG_S))
        if finished_at is not None:
            with MS._INFLIGHT_LOCK:
                t.call.finished_at = finished_at
        return t.call

    def _call(self, text):
        _t, th, b = self.request(self.w, path=self.d, text=text)
        joined(self, th)
        return b["out"]

    def test_past_the_cap_the_oldest_becomes_a_tombstone_and_is_never_re_run(self):
        """force — cap 2, three kept: the oldest is evicted; its re-call is told it ran, once.
        The order is the order the three FINISHED (each waits for the one before), never a time
        the test writes into the table (X1: stamps of 100..102 sat ABOVE a fresh Linux runner's
        `perf_counter`, so the real third finish looked oldest and was the one evicted)."""
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 2):
            for text in ("a", "b", "c"):
                self._told_running_and_finished(text)
            with MS._INFLIGHT_LOCK:
                MS._prune_kept(time.perf_counter())
                held = sorted(c.out["text"] for c in MS._INFLIGHT.values() if c.op == "remember_probe")
            self.assertEqual(["b", "c"], held, "the oldest-finished must be the one evicted")
            out = self._call("a")
            self.assertEqual(["a", "b", "c"], self.runs, "an evicted write was RE-RUN")
            self.assertEqual("already_ran", out["reason_code"])
            self.assertEqual("ok", out["outcome"])
            self.assertIn("already ran at", out["reason"])
            self.assertIn("no longer held", out["reason"])
            out = self._call("a")                              # told once: a new request now
            self.assertEqual(["a", "b", "c", "a"], self.runs)
            self.assertEqual("ok", out["status"])

    def test_eviction_follows_the_finish_order_never_the_clock_reading(self):
        """force (X1, ubuntu CI 43869782006) — what is evicted at the cap is the write that
        finished FIRST, whatever the clock read when each finished. The two earlier finishes carry
        readings far above anything the clock will read for the third (a clock that is reset, coarse
        or simply lower than a stored value): the first-finished is still the one evicted."""
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 2):
            ahead = time.perf_counter() + 1e9
            for i, text in enumerate(("a", "b")):
                self._told_running_and_finished(text, finished_at=ahead + i)
            self._told_running_and_finished("c")          # the cap is enforced as it finishes (L3)
            with MS._INFLIGHT_LOCK:
                held = sorted(c.out["text"] for c in MS._INFLIGHT.values() if c.op == "remember_probe")
            self.assertEqual(["b", "c"], held, "evicted by clock reading, not by finish order")
            self.assertEqual("already_ran", self._call("a")["reason_code"])
            self.assertEqual(["a", "b", "c"], self.runs)

    def test_eviction_follows_the_finish_order_not_the_start_order(self):
        """force (X1) — "a" starts first and finishes second: "b", the first to FINISH, is the one
        evicted at the cap. Each finish is waited for before the next is released. "a" runs on a
        second repo: one repo's writes run one at a time, so "b" could not finish while "a" held it."""
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        paths = {"a": os.path.realpath(other.name), "b": self.d}
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 2):
            started = {}
            for text in ("a", "b"):
                self.hold[text] = self.release()
                self.budget = 0.0
                t, th, b = self.request(self.w, path=paths[text], text=text)
                joined(self, th)
                self.assertEqual("running", b["out"]["status"])
                started[text] = t.call
            self.budget = 600.0
            for text in ("b", "a"):
                self.hold.pop(text).set()
                self.assertTrue(started[text].done.wait(HANG_S))
            self._told_running_and_finished("c")
            with MS._INFLIGHT_LOCK:
                held = sorted(c.out["text"] for c in MS._INFLIGHT.values() if c.op == "remember_probe")
            self.assertEqual(["a", "c"], held, "evicted by start order, not by finish order")

    def test_tombstones_expire_by_age(self):
        """force — past 7 days an identical call is a new request."""
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 0):
            self._told_running_and_finished("old", finished_at=1.0)
            with MS._INFLIGHT_LOCK:
                now = time.perf_counter()
                MS._prune_kept(now)
                self.assertEqual(1, len(MS._TOMBSTONES))
                for t in MS._TOMBSTONES.values():
                    t.evicted_at = now - MS._TOMBSTONE_MAX_AGE_S - 1
                MS._prune_kept(now)
                self.assertEqual({}, MS._TOMBSTONES)
            out = self._call("old")
            self.assertEqual(["old", "old"], self.runs)
            self.assertEqual("ok", out["status"])

    def test_tombstones_are_bounded_by_count_oldest_first(self):
        """force — "oldest" is the order they were evicted in, not the clock reading each one
        stored (X1): the first tombstone carries a reading far ABOVE the second's, and is still the
        one the count bound drops."""
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 0), \
                mock.patch.object(MS, "_TOMBSTONES_MAX", 1):
            self._told_running_and_finished("x", finished_at=1.0)
            with MS._INFLIGHT_LOCK:
                MS._prune_kept(time.perf_counter())
                first = list(MS._TOMBSTONES.values())[0]
                first.evicted_at += 1e9                         # a reading above any later one
            self._told_running_and_finished("y", finished_at=2.0)
            with MS._INFLIGHT_LOCK:
                MS._prune_kept(time.perf_counter())
                self.assertEqual(1, len(MS._TOMBSTONES))
            self.assertEqual("already_ran", self._call("y")["reason_code"])
            self.assertEqual("ok", self._call("x")["status"])  # its tombstone was the one dropped
            self.assertEqual(["x", "y", "x"], self.runs)

    def test_a_kept_result_says_when_it_ran(self):
        """force — a result collected late carries `kept.ran_at` / `age_s`."""
        self._told_running_and_finished("k", finished_at=time.perf_counter())
        out = self._call("k")
        self.assertEqual(["k"], self.runs)
        self.assertEqual("k", out["text"])
        self.assertIn("kept", out, "a late result served unlabelled")
        self.assertRegex(out["kept"]["ran_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertIsInstance(out["kept"]["age_s"], int)

    def test_the_docs_state_the_bounds_the_code_uses(self):
        """guard — the reference page's numbers are the constants'."""
        from mokata import mcp_admin
        days = int(MS._TOMBSTONE_MAX_AGE_S // 86400)
        for text in (f"at most {MS._KEPT_WRITES_MAX} such", f"kept\nfor {days} days",
                     f"at most {MS._TOMBSTONES_MAX} of them"):
            self.assertIn(text, mcp_admin._DOCS_HEAD)

    def test_a_result_the_caller_waited_for_is_not_labelled_kept(self):
        """guard — only a result that finished BEFORE the call came is `kept`."""
        out = self._call("now")
        self.assertNotIn("kept", out)


class RefusedLinesAreNeverSilent(unittest.TestCase):
    """Round 5 (round-4 verifier M1; ruling D16's conditions 2 and 3): a Windows statusLine or
    baseline line the cmd guard refuses is never silent — `mokata doctor` names the line, the reason
    and the fix; the wrapped statusLine shows a short visible refusal instead of a blank; the docs
    state the rule. The matrix: a refused statusLine, a refused baseline, an accepted line, each
    against doctor, the wrap output and the docs. Injected (`nt=True`), so it runs on every OS."""

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="b29-d16-"))
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.root = os.path.join(self.d, "repo")
        self.home = os.path.join(self.d, "home")
        self.bin = os.path.join(self.d, "bin")
        for p in (self.root, self.home, self.bin, os.path.join(self.root, ".claude")):
            os.makedirs(p, exist_ok=True)
        open(os.path.join(self.bin, "mytool.exe"), "w", encoding="utf-8").close()
        self.env = {"PATH": self.bin, "PATHEXT": ".COM;.EXE;.BAT;.CMD"}

    def _statusline(self, wrapped):
        cmd = f'"C:\\\\p\\\\mokata-hook.exe" statusline --wrap {shlex.quote(wrapped)}'
        with open(os.path.join(self.root, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"statusLine": {"type": "command", "command": cmd}}, fh)

    def _doctor(self, baseline=None):
        from mokata.govern.doctor import windows_command_line_findings
        return windows_command_line_findings(self.root, self.home, baseline, nt=True, env=self.env)

    def test_doctor_names_a_refused_statusline_with_reason_and_fix(self):
        """force — the line itself, why, and the exact fix."""
        self._statusline("%PROMPT% -q")
        f = self._doctor()
        self.assertEqual(1, len(f), f)
        self.assertEqual("windows-line-refused", f[0].code)
        for text in ("statusLine", "%PROMPT% -q", "a cmd variable", "write the program",
                     "settings.json"):
            self.assertIn(text, f[0].detail)

    def test_doctor_names_a_refused_baseline_with_reason_and_fix(self):
        """force."""
        f = self._doctor(baseline="call pytest -q")
        self.assertEqual(1, len(f), f)
        for text in ("baseline", "call pytest -q", "runs another command",
                     "name the program directly"):
            self.assertIn(text, f[0].detail)

    def test_doctor_is_quiet_for_accepted_lines_and_off_windows(self):
        """guard — an accepted line says nothing; off Windows nothing is refused."""
        self._statusline("mytool -q")
        self.assertEqual([], self._doctor(baseline="mytool && echo ok"))
        from mokata.govern.doctor import windows_command_line_findings
        self._statusline("%X%")
        self.assertEqual([], windows_command_line_findings(self.root, self.home, "call x",
                                                           nt=False, env=self.env))

    def test_doctor_is_wired_into_diagnose(self):
        """force — the finding reaches `mokata doctor`, not only the helper, and `diagnose` hands
        it the manifest's baseline line (round-5 verifier L2: a None there went unseen)."""
        from mokata.govern import doctor as D
        seen = {}

        def spy(root, home=None, baseline=None, **kw):
            seen["baseline"] = baseline
            return [D.DoctorFinding("warning", "windows-line-refused", "spy")]
        m = mock.Mock()
        m.setting.side_effect = lambda key, default=None: (
            {"test_command": "call pytest"} if key == "baseline" else default)
        with mock.patch.object(D, "windows_command_line_findings", spy):
            src = open(D.__file__, encoding="utf-8").read()
            body = src[src.index("def diagnose("):]
            self.assertIn("findings.extend(windows_command_line_findings(", body)
            call = body[body.index("windows_command_line_findings("):]
            call = call[:call.index("))") + 2]
            self.assertIn("baseline_command(m)", call)
        from mokata import baseline as B
        self.assertEqual("call pytest", B.baseline_command(m))

    def test_the_doctor_reader_of_the_wrap_line_runs_nothing(self):
        """guard — the second reader of `--wrap` (test_stage5's list) parses and judges; it never
        reaches a spawn, so the nosec caller-graph claim is unchanged."""
        from mokata.govern import doctor as D
        tree = ast.parse(open(D.__file__, encoding="utf-8").read())
        for name in ("_wrapped_statusline", "windows_command_line_findings"):
            fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
            called = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                      for c in ast.walk(fn) if isinstance(c, ast.Call)}
            self.assertFalse(called & {"run_jobbed", "spawn_jobbed", "Popen", "run", "system"},
                             (name, called))

    def _wrap(self, line):
        from mokata import hook_cli
        with mock.patch.object(Q, "_windows", lambda: True), \
                mock.patch.dict(os.environ, self.env):
            return hook_cli._run_wrapped(line, json.dumps({"cwd": self.root}))

    def test_a_refused_wrapped_statusline_is_visible_not_blank(self):
        """force — `_run_wrapped` used to swallow the refusal into ""."""
        out = self._wrap("%PROMPT% -q")
        self.assertIn("statusLine refused", out)
        self.assertIn("doctor", out)
        self.assertNotIn("\n", out)
        self.assertLess(len(out), 80)

    def test_the_refusal_names_the_installed_doctor_command_never_a_literal(self):
        """force (round-5 verifier L1) — where `mokata init` wrote `/mokata-doctor`, the marker
        says that: the text comes from `plugin_runtime.slash_command`, never a literal."""
        cmds = os.path.join(self.root, ".claude", "commands")
        os.makedirs(cmds, exist_ok=True)
        with open(os.path.join(cmds, "mokata-doctor.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ndescription: mokata · doctor\n---\n")   # what setup writes (C1 mark)
        from mokata import plugin_runtime
        want = plugin_runtime.slash_command("doctor", self.root, env={})
        self.assertEqual("/mokata-doctor", want)
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": ""}):
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            out = self._wrap("%PROMPT% -q")
        self.assertTrue(out.endswith(want), out)
        self.assertNotIn("/mokata:doctor", out)

    def test_an_ordinary_failure_still_degrades_to_blank(self):
        """guard — only the guard's refusal is shown; any other failure stays "" (best-effort)."""
        from mokata import hook_cli
        with mock.patch.object(Q, "run_jobbed", side_effect=OSError("boom")):
            self.assertEqual("", hook_cli._run_wrapped("whatever", "{}"))

    def test_the_accepted_line_still_runs(self):
        """guard — off Windows the wrapped line runs as before."""
        from mokata import hook_cli
        self.assertEqual("hi", hook_cli._run_wrapped("echo hi", "{}"))

    def test_the_docs_state_the_windows_rule(self):
        """force — D16 condition 3: the reference page states resolved-or-refused."""
        with open(os.path.join(SRC, "..", "docs", "reference", "platform-support.md"),
                  encoding="utf-8") as fh:
            doc = fh.read()
        for text in ("resolved or refused", "`%VAR%`", "`call`", "not on PATH",
                     "statusLine refused", "mokata doctor", "baseline"):
            self.assertIn(text, doc)


class DeliveryConsumes(_Mcp):
    """Round 5 (round-4 verifier M2): a kept result or a tombstone answer is consumed only when the
    answer was DELIVERED — the response written to the client. A join that is cancelled or fails
    before delivery leaves it in place, so the next identical call is answered, never re-run. Event
    driven: the cancel, the delivery and the crash are called, never timed."""

    def setUp(self):
        super().setUp()
        self.runs = []
        self.hold = {}

        def remember_probe(path=".", text=""):
            self.runs.append(text)
            gate = self.hold.get(text)
            if gate is not None:
                gate.wait(HANG_S)
            return {"status": "ok", "ok": True, "text": text}
        self.w = MS._serve(remember_probe, name="remember_probe", kind="write")

    def _kept(self, text):
        """Told running, then finished: a kept (uncollected) write."""
        self.hold[text] = self.release()
        self.budget = 0.0
        t, th, b = self.request(self.w, path=self.d, text=text)
        joined(self, th)
        self.budget = 600.0
        self.assertEqual("running", b["out"]["status"])
        self.hold.pop(text).set()
        self.assertTrue(t.call.done.wait(HANG_S))

    def _tombstoned(self, text):
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 0):
            self._kept(text)
            with MS._INFLIGHT_LOCK:
                MS._prune_kept(time.perf_counter())
                self.assertEqual(1, len(MS._TOMBSTONES))

    def _deferred(self, text):
        """A request as the SDK path makes it: its answer is delivered only on `delivered()`."""
        ticket = MS._Ticket()
        ticket.deferred = True
        _t, th, box = self.request(self.w, ticket=ticket, path=self.d, text=text)
        joined(self, th)
        return ticket, box

    def _call(self, text):
        _t, th, b = self.request(self.w, path=self.d, text=text)
        joined(self, th)
        return b["out"]

    def test_the_verifier_probe_a_tombstone_join_cancelled_is_not_lost(self):
        """force (ports verify-r4 `tomb_probe.py` section A) — join the tombstone, cancel, re-call."""
        self._tombstoned("A")
        t = MS._Ticket()
        call, joined_ = MS._join_or_start("remember_probe", "write", None, (),
                                          {"path": self.d, "text": "A"}, self.d, t, None)
        self.assertTrue(joined_)
        self.assertEqual("already_ran", call.out.get("reason_code"))
        t.cancel()
        out = self._call("A")
        self.assertEqual(["A"], self.runs, "the cancelled join consumed the tombstone: RE-RUN")
        self.assertEqual("already_ran", out["reason_code"])

    def test_cancel_before_delivery_keeps_a_tombstone(self):
        """force — the answer was computed, never written: the next call is still told."""
        self._tombstoned("B")
        ticket, box = self._deferred("B")
        self.assertEqual("already_ran", box["out"]["reason_code"])
        ticket.cancel()                                   # the SDK never wrote it
        self.assertEqual("already_ran", self._call("B")["reason_code"])
        self.assertEqual(["B"], self.runs)

    def test_cancel_before_delivery_keeps_a_kept_result(self):
        """force — the same rule for a kept (uncollected) result."""
        self._kept("C")
        ticket, box = self._deferred("C")
        self.assertEqual("C", box["out"]["text"])
        ticket.cancel()
        out = self._call("C")
        self.assertEqual(["C"], self.runs, "a kept result lost to a cancel: RE-RUN")
        self.assertEqual("C", out["text"])

    def test_delivery_consumes(self):
        """force — once written, it is gone: the next identical call is a new request."""
        for text, prep in (("D", self._kept), ("E", self._tombstoned)):
            with self.subTest(text=text):
                prep(text)
                ticket, _box = self._deferred(text)
                ticket.delivered()
                out = self._call(text)
                self.assertEqual("ok", out["status"])
                self.assertEqual(2, self.runs.count(text))

    def test_two_joiners_one_cancelled_one_delivered(self):
        """force — the cancelled joiner does not consume; the delivered one does."""
        self._tombstoned("F")
        t1, b1 = self._deferred("F")
        t2, b2 = self._deferred("F")
        self.assertEqual("already_ran", b1["out"]["reason_code"])
        self.assertEqual("already_ran", b2["out"]["reason_code"])
        t1.cancel()
        self.assertEqual("already_ran", self._call_deferred_peek("F"))
        t2.delivered()
        self.assertEqual("ok", self._call("F")["status"])
        self.assertEqual(["F", "F"], self.runs)

    def _call_deferred_peek(self, text):
        t, box = self._deferred(text)
        t.cancel()                                        # a peek that is never delivered
        return box["out"]["reason_code"]

    def test_a_joiner_that_crashes_before_delivery_consumes_nothing(self):
        """force — the request failed (the SDK answers an error, not this result)."""
        self._kept("G")
        ticket, _box = self._deferred("G")
        ticket.undelivered()
        self.assertEqual("G", self._call("G")["text"])
        self.assertEqual(["G"], self.runs)

    @_mcp_sdk.requires_mcp_sdk
    def test_the_sdk_send_is_the_delivery_point(self):
        """force — the server's patched `_send_response` resolves the receipt: a result frame
        delivers, a `Request cancelled` error frame (or a failed write) does not."""
        import asyncio
        from mcp.types import ErrorData
        self._kept("H")
        sess = mock.Mock()
        sent = []

        async def orig(self_, request_id, response):
            sent.append(request_id)
        for rid, response, delivered in ((1, ErrorData(code=0, message="Request cancelled"), False),
                                         (2, mock.Mock(), True)):
            ticket, _box = self._deferred("H")
            MS._RECEIPTS[(id(sess), rid)] = ticket
            asyncio.run(MS._delivering(orig)(sess, rid, response))
            with MS._INFLIGHT_LOCK:
                held = any(c.op == "remember_probe" for c in MS._INFLIGHT.values())
            self.assertEqual(not delivered, held, (rid, delivered))
        self.assertEqual([1, 2], sent)
        self.assertEqual(["H"], self.runs)

    @_mcp_sdk.requires_mcp_sdk
    def test_a_write_that_raises_is_not_a_delivery(self):
        """force — the transport broke while writing: the result stays."""
        import asyncio
        import anyio
        self._kept("K")
        sess = mock.Mock()

        async def broken(self_, request_id, response):
            raise anyio.BrokenResourceError()
        ticket, _box = self._deferred("K")
        MS._RECEIPTS[(id(sess), 3)] = ticket
        with self.assertRaises(anyio.BrokenResourceError):
            asyncio.run(MS._delivering(broken)(sess, 3, mock.Mock()))
        self.assertEqual("K", self._call("K")["text"])
        self.assertEqual(["K"], self.runs)

    def test_a_tombstone_answer_evicted_again_keeps_the_original_outcome(self):
        """force — told `already_ran` (ok), not delivered, evicted again: still `ok`."""
        self._tombstoned("L")
        ticket, box = self._deferred("L")
        self.assertEqual("ok", box["out"]["outcome"])
        ticket.cancel()                                   # never written: stays in the table
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 0), MS._INFLIGHT_LOCK:
            MS._prune_kept(time.perf_counter())            # evicted a second time
        out = self._call("L")
        self.assertEqual("already_ran", out["reason_code"])
        self.assertEqual("ok", out["outcome"], "the second tombstone lost the write's outcome")
        self.assertEqual(["L"], self.runs)

    @_mcp_sdk.requires_mcp_sdk
    def test_the_hook_fires_through_a_real_sdk_session(self):
        """force (round-5 verifier L3) — the INSTALLED SDK's own ServerSession writes a response
        through the hooked `_send_response`: a drift that kept the method but moved the write (the
        hook attached, never firing) reds here."""
        import anyio
        from mcp.server.session import ServerSession
        from mcp.server.models import InitializationOptions
        from mcp.shared.message import SessionMessage
        from mcp.types import ServerCapabilities, EmptyResult
        MS._install_delivery_hook()
        self._kept("R")
        fired = []

        async def main():
            read_send, read_recv = anyio.create_memory_object_stream(10)
            write_send, write_recv = anyio.create_memory_object_stream(10)
            opts = InitializationOptions(server_name="t", server_version="0",
                                         capabilities=ServerCapabilities())
            async with ServerSession(read_recv, write_send, opts) as sess:
                ticket, _box = self._deferred("R")
                MS._RECEIPTS[(id(sess), 41)] = ticket
                await sess._send_response(41, EmptyResult())
                msg = write_recv.receive_nowait()
                fired.append(isinstance(msg, SessionMessage))
        anyio.run(main)
        self.assertEqual([True], fired)
        with MS._INFLIGHT_LOCK:
            held = any(c.op == "remember_probe" for c in MS._INFLIGHT.values())
        self.assertFalse(held, "the real SDK write did not reach the delivery hook")
        self.assertEqual({}, MS._RECEIPTS)

    @_mcp_sdk.requires_mcp_sdk
    def test_the_server_installs_the_delivery_hook(self):
        """force — `build_server` wires `_send_response` through `_delivering`."""
        from mcp.server.session import ServerSession
        MS._install_delivery_hook()
        self.assertTrue(getattr(ServerSession._send_response, "_mokata_delivery", False))
        src = open(MS.__file__, encoding="utf-8").read()
        body = src[src.index("def build_server("):]
        self.assertIn("    _install_delivery_hook()", body[:body.index("\ndef ")])

    @_mcp_sdk.requires_mcp_sdk
    def test_the_handler_undelivers_a_crashed_request(self):
        """force — on the SDK path (a request context), a request that fails after its result was
        computed leaves the kept result for the next identical call."""
        import asyncio
        from mcp.server.lowlevel.server import request_ctx
        self._kept("J")
        w = self.w

        def served(**kw):
            w(**kw)                                       # computed, deferred, not delivered
            raise RuntimeError("the request died before its response was written")
        handler = MS._offloop(served)
        token = request_ctx.set(mock.Mock(session=object(), request_id=9))
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(handler(path=self.d, text="J"))
        finally:
            request_ctx.reset(token)
        self.assertEqual("J", self._call("J")["text"])
        self.assertEqual(["J"], self.runs)
        self.assertEqual({}, MS._RECEIPTS)

    def test_the_cap_is_enforced_when_a_write_finishes(self):
        """force (L3) — the bound holds between calls, not only at the next call."""
        with mock.patch.object(MS, "_KEPT_WRITES_MAX", 1):
            self._kept("x")
            self._kept("y")
            with MS._INFLIGHT_LOCK:
                held = [c for c in MS._INFLIGHT.values() if c.op == "remember_probe"]
            self.assertEqual(1, len(held), "two held past a cap of 1 with no call in between")
            self.assertEqual(1, len(MS._TOMBSTONES))


class TheTreeChangedDuringTheRun(_Mcp):
    """#11 widened (B1 verifier M2) — a told-running result is keyed to the tree it RAN on. When
    the tree changed while it ran (an edit, or the run's own writes — the key cannot tell them
    apart), the result is never served as current: a re-call on the changed tree gets it LABELLED
    `tree_changed`, once, and the call after that is a new run."""

    def setUp(self):
        super().setUp()
        _git(self.d, "init", "-q")
        with open(os.path.join(self.d, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("one\n")
        _git(self.d, "add", "-A")
        _git(self.d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c0")

    def _baseline(self, runs, started, release, writes=None):
        def baseline(path="."):
            runs.append(1)
            started.set()
            release.wait(HANG_S)
            if writes is not None:                             # the run writes the tree itself
                with open(os.path.join(self.d, writes), "w", encoding="utf-8") as fh:
                    fh.write(f"report {len(runs)}\n")
            return {"status": "ok", "ok": True, "run": len(runs)}
        return MS._serve(baseline, name="baseline")

    def _told_running(self, w):
        self.budget = 0.0
        t1, th1, b1 = self.request(w, path=self.d)
        joined(self, th1)
        self.assertEqual("running", b1["out"]["status"])
        self.budget = 600.0
        return t1.call

    def _re_call(self, w):
        _t, th, box = self.request(w, path=self.d)
        joined(self, th)
        return box["out"]

    def test_an_edit_during_the_run_is_never_served_as_current(self):
        """force (verifier EditDuringTheRun) — the pre-edit result comes back labelled, once."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._baseline(runs, started, release)
        call = self._told_running(w)
        self.assertTrue(started.wait(HANG_S))
        with open(os.path.join(self.d, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("EDITED\n")                               # the user edits mid-run
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        out = self._re_call(w)
        self.assertEqual(1, out["run"])
        self.assertIn("tree_changed", out, "the pre-edit result was served as current")
        self.assertIs(True, out["tree_changed"]["changed_while_running"])
        out = self._re_call(w)
        self.assertEqual(2, len(runs), "the call after the labelled answer must run on the new tree")
        self.assertEqual(2, out["run"])
        self.assertNotIn("tree_changed", out)

    def test_a_run_that_writes_the_tree_is_collected_labelled_not_re_run(self):
        """force (kills V-N3: the end key) — the suite's own write does not fork a second run."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._baseline(runs, started, release, writes="report.txt")
        call = self._told_running(w)
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        out = self._re_call(w)
        self.assertEqual(1, len(runs), "the run's own write forked a second run")
        self.assertEqual(1, out["run"])
        self.assertIn("tree_changed", out)

    def test_the_first_answer_is_labelled_too(self):
        """force (B1 re-verifier: the label on the starter's own answer, not only a re-call's)."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._baseline(runs, started, release, writes="report.txt")
        release.set()
        out = self._re_call(w)                                 # the first and only request
        self.assertEqual(1, len(runs))
        self.assertIn("tree_changed", out, "the request that ran it was told the tree held still")

    def test_an_untouched_tree_is_served_as_current(self):
        """guard — no label when nothing changed (#12 on a committed tree)."""
        runs, started, release = [], threading.Event(), self.release()
        w = self._baseline(runs, started, release)
        call = self._told_running(w)
        release.set()
        self.assertTrue(call.done.wait(HANG_S))
        out = self._re_call(w)
        self.assertEqual(1, len(runs))
        self.assertNotIn("tree_changed", out)


class TheTreeKeySeesThese(unittest.TestCase):
    """The fingerprint (B1 verifier item 2)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        _git(self.d, "init", "-q")
        with open(os.path.join(self.d, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("one\n")
        _git(self.d, "add", "-A")
        _git(self.d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c0")

    def test_an_edit_inside_an_untracked_directory_changes_the_key(self):
        """force (kills V-N5: `-uall` → `-unormal`)."""
        os.makedirs(os.path.join(self.d, "sub", "deep"))
        p = os.path.join(self.d, "sub", "deep", "n.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
        k0 = MS.tree_key(self.d)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("a longer body")
        self.assertNotEqual(k0, MS.tree_key(self.d))

    @unittest.skipIf(os.name == "nt", "a POSIX mode bit (Windows has only read-only)")
    def test_a_chmod_of_an_already_modified_file_changes_the_key(self):
        """force — same status entry, size and mtime; only the mode moved."""
        p = os.path.join(self.d, "a.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("two\n")
        k0 = MS.tree_key(self.d)
        os.chmod(p, 0o755)
        self.assertNotEqual(k0, MS.tree_key(self.d))


class MergeRedeemSeals(unittest.TestCase):
    def test_the_merge_redeem_seals_the_callers_lever(self):
        """#13 force (at the seal seam) — `worktree_merge` hands `run_merge` the lever's seal: a
        cancel before it stops the merge (seal False); after it, a pull is too late."""
        from mokata import merge_gate as MG
        from mokata.mcp import tools_worktree as TW
        seen = {}

        def fake_run_merge(path, pid, redeem=None, seal=None):
            seen["seal"] = seal
            seen["first"] = seal()
            seen["pull_after"] = C.current().pull()
            return mock.Mock(merged=seen["first"], state="cancelled",
                             to_dict=lambda: {"merged": seen["first"]})
        trust = mock.Mock()
        trust.can_write.return_value = True
        with mock.patch.object(MG, "ready_to_run", return_value=(True, "", "")), \
                mock.patch.object(MG, "run_merge", fake_run_merge), \
                mock.patch("mokata.mcp.consent._trust", return_value=trust):
            lever = C.Lever()
            with C.scope(lever):
                out = TW.worktree_merge(".", proposal_id="p1")
            self.assertTrue(seen["first"])
            self.assertFalse(seen["pull_after"], "a pull after the seal must be too late")
            self.assertTrue(out["committed"])
            lever = C.Lever()
            lever.pull()
            with C.scope(lever):
                out = TW.worktree_merge(".", proposal_id="p1")
            self.assertFalse(seen["first"], "a cancel before the seal must stop the merge")
            self.assertFalse(out["committed"])


class WriteGateWaitCancellable(_Mcp):
    def test_a_write_queued_on_the_gate_leaves_on_cancel_without_writing(self):
        """#14 force."""
        hold, started, wrote = self.release(), threading.Event(), []

        def probe_write(path=".", n=0):
            wrote.append(n)
            if n == 1:
                started.set()
                hold.wait(HANG_S)
            return {"status": "ok", "ok": True}
        w = MS._serve(probe_write, name="probe_write", kind="write")
        _t1, _th1, _ = self.request(w, path=self.d, n=1)
        self.assertTrue(started.wait(HANG_S))
        t2, th2, b2 = self.request(w, path=self.d, n=2)
        call2 = t2.call
        t2.cancel()
        self.assertTrue(call2.done.wait(HANG_S), "the queued write never left the gate")
        joined(self, th2)
        self.assertTrue(call2.cancelled)
        self.assertEqual([1], wrote, "the cancelled write ran")


class CancelThroughFreshness(unittest.TestCase):
    def test_a_cancel_in_the_freshness_pass_unwinds_not_degrades(self):
        """#15 force — `ensure_fresh` lets `Cancelled` through (N8)."""
        from mokata.knowledge import freshness as F
        ctl = F.FreshnessController.__new__(F.FreshnessController)
        with mock.patch.object(F.FreshnessController, "_reconcile",
                               side_effect=C.Cancelled("cancelled")):
            with self.assertRaises(C.Cancelled):
                ctl.ensure_fresh(object())


# ====================================================================== the MCP server, over stdio
def _stdio():
    import _mcp_sdk  # noqa: F401
    from test_o14_mcp_stability import _Stdio, _repo
    return _Stdio, _repo


class _Server(unittest.TestCase):
    def setUp(self):
        try:
            from mokata.mcp.server import mcp_available
            if not mcp_available():
                self.skipTest("the MCP SDK is required")
        except ImportError:
            self.skipTest("the MCP SDK is required")
        Stdio, repo = _stdio()
        import shutil
        self.repo = repo()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.srv = Stdio(self.repo, "run-b29")
        self.addCleanup(self._close)
        self.wit = Witness(self)

    def _close(self):
        try:
            self.srv.close()
        except Exception:  # noqa: BLE001 - teardown
            pass

    def start_baseline(self):
        handle = self.srv.tool_start("baseline", {"path": self.repo,
                                                  "cmd": fake_line(self.wit.port, 3)})
        self.wit.members(3)
        return handle


class McpCancelEndsTheTree(_Server):
    def test_notifications_cancelled_ends_the_baseline_tree(self):
        """#2 force (W: probe) — a real stdio `notifications/cancelled`."""
        rid, _ev, _sent = self.start_baseline()
        self.srv._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                        "params": {"requestId": rid, "reason": "user"}})
        self.assertTrue(self.wit.gone(), "the baseline tree outlived the cancel")
        msg, _dt = self.srv.call("ping", {}, timeout=HANG_S)
        self.assertIsNotNone(msg, "the server stopped answering after a cancel")


class ServerExitEndsTrees(_Server):
    def test_stdin_eof_ends_the_tree(self):
        """#16 force (W: probe)."""
        self.start_baseline()
        self.srv.p.stdin.close()
        self.assertTrue(self.wit.gone(), "the tree outlived the server's exit")

    def test_sigterm_ends_the_tree(self):
        """#16 force (W: probe; there SIGTERM is TerminateProcess — the job's close ends it)."""
        self.start_baseline()
        Q.signal_pid(self.srv.p.pid, signal.SIGTERM)
        self.assertTrue(self.wit.gone(), "the tree outlived a SIGTERM'd server")


class EndAllEndsEveryTree(unittest.TestCase):
    def test_end_all_ends_and_forgets_every_live_tree(self):
        """#16 force (the server's exit path itself, apart from the lifeline that backs it)."""
        wit = Witness(self)
        tree = Q.spawn_jobbed(fake_argv(wit.port, 3))
        wit.members(3)
        self.assertGreaterEqual(Q.end_all(), 1)
        self.assertTrue(wit.gone(), "end_all left a tree running")
        with Q._LIVE_LOCK:
            self.assertNotIn(tree, Q._LIVE)


class LifelineEndsTreesOnHardKill(_Server):
    def test_sigkill_of_the_server_ends_the_tree(self):
        """#17 force (POSIX: the lifeline; W: probe, KILL_ON_JOB_CLOSE)."""
        self.start_baseline()
        Q.signal_pid(self.srv.p.pid, SIGKILL)
        self.assertTrue(self.wit.gone(), "a hard-killed server left its tree running")


# ====================================================================== the door
_LIFELINE_PROBE = r"""
import json, signal, sys
from mokata import quiet_spawn as Q
def run(code):
    t = Q.spawn_jobbed([sys.executable, "-c", code])
    try:
        return t.wait_exit(), list(t.proc.args)
    finally:
        t.close()
out = {}
term = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
out["off_term"], args = run(term)
out["off_shim"] = Q.lifeline_path() in args
out["off_exit4"], _ = run("import sys; sys.exit(4)")
try:
    Q.spawn_jobbed(["/nonexistent/b29-x"])
    out["off_fnf"] = False
except FileNotFoundError:
    out["off_fnf"] = True
out["enabled"] = Q.enable_lifeline()
out["on_term"], args = run(term)
out["on_shim"] = Q.lifeline_path() in args
out["on_exit4"], _ = run("import sys; sys.exit(4)")
try:
    Q.spawn_jobbed(["/nonexistent/b29-x"])
    out["on_fnf"] = False
except FileNotFoundError:
    out["on_fnf"] = True
print(json.dumps(out))
"""


@unittest.skipUnless(POSIX, "N/A on Windows: KILL_ON_JOB_CLOSE is the lifeline there")
class LifelineOffAndOn(unittest.TestCase):
    def test_off_no_shim_and_on_the_same_exit_codes(self):
        """#18 force — the CLI/hooks start no shim; with it on, `-N`, `4` and a missing program
        are what Popen itself would have said."""
        r = subprocess.run([sys.executable, "-c", _LIFELINE_PROBE], capture_output=True,
                           text=True, env=dict(os.environ, PYTHONPATH=SRC))
        self.assertEqual(0, r.returncode, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertFalse(out["off_shim"])
        self.assertTrue(out["enabled"])
        self.assertTrue(out["on_shim"])
        self.assertEqual(-signal.SIGTERM, out["off_term"])
        self.assertEqual(-signal.SIGTERM, out["on_term"])
        self.assertEqual(4, out["off_exit4"])
        self.assertEqual(4, out["on_exit4"])
        self.assertTrue(out["off_fnf"])
        self.assertTrue(out["on_fnf"])


class RunJobbedUnderALever(unittest.TestCase):
    def test_a_pulled_lever_ends_the_tree_and_raises_cancelled(self):
        """#19 force (W: probe)."""
        wit = Witness(self)
        lever = C.Lever()

        def work():
            with C.scope(lever):
                return Q.run_jobbed(fake_argv(wit.port, 3), timeout=None)
        t, box = in_thread(work)
        wit.members(3)
        lever.pull()
        self.assertTrue(wit.gone(), "the tree outlived the pull")
        joined(self, t)
        self.assertIsInstance(box.get("exc"), C.Cancelled)
        with Q._LIVE_LOCK:
            self.assertFalse([t for t in Q._LIVE if FAKE_PATH in list(t.proc.args)],
                             "an ended tree is still registered")

    def test_the_door_imports_nothing_new_on_the_hook_graph(self):
        """#19 force — the flag is read through `sys.modules`; importing the door never pulls in
        `knowledge.cancel`."""
        r = subprocess.run([sys.executable, "-c",
                            "import sys; import mokata.quiet_spawn; "
                            "print('mokata.knowledge.cancel' in sys.modules)"],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=SRC))
        self.assertEqual("False", r.stdout.strip(), r.stderr)


class ProgressSeam(unittest.TestCase):
    def test_progress_sees_growing_bytes_and_its_raise_ends_the_tree(self):
        """#20 force (W: probe) — lane G's seam."""
        wit = Witness(self)
        seen = []

        class Enough(Exception):
            pass

        def progress(total):
            seen.append(total)
            if len(set(x for x in seen if x > 0)) >= 2:
                raise Enough()
        t, box = in_thread(Q.run_jobbed, fake_argv(wit.port, 2, "chatty"), timeout=None,
                           progress=progress)
        wit.members(2)
        self.assertTrue(wit.gone(), "a raising progress callback left the tree running")
        joined(self, t)
        self.assertIsInstance(box.get("exc"), Enough)
        self.assertEqual(seen, sorted(seen), "the byte count went backwards")


# ====================================================================== Ctrl-C, SIGTERM, SIGHUP
from mokata import ci_check as CC  # noqa: E402


@unittest.skipUnless(threading.current_thread() is threading.main_thread(),
                     "signal handlers install on the main thread only")
class _Sig(unittest.TestCase):
    """`cancel_on_sigint` on the main thread with real signals (`signal.raise_signal`, or a helper
    thread's `os.kill` of this process). `pulled` is an Event set when the watcher's pull returns.
    A door run on the main thread here is given `timeout=HANG_S` — hang detection only: the cell's
    stop must come from the signal, and a `TimeoutExpired` instead of `Cancelled` is the red."""

    def setUp(self):
        self.err = []
        p = mock.patch.object(sys, "stderr", mock.Mock(write=self.err.append, flush=lambda: None))
        p.start()
        self.addCleanup(p.stop)
        # The handler's byte to the watcher: the event that a signal was HANDLED. Two signals sent
        # back to back before the main thread runs Python are one pending signal (CPython keeps a
        # flag, not a count) — a helper waits for this between them, as a human's two presses are.
        self.poked = threading.Event()
        real_poke = CC._poke

        def poke(st, byte):
            real_poke(st, byte)
            self.poked.set()
        q = mock.patch.object(CC, "_poke", poke)
        q.start()
        self.addCleanup(q.stop)

    def run_door(self, argv):
        """A door run on the main thread, with hang detection: reaching `HANG_S` is recorded and
        fails the test however the block then unwinds (a pending escalation can still turn it into
        `Cancelled` on the way out — that is not a stop that came from the signal)."""
        try:
            return Q.run_jobbed(argv, timeout=HANG_S)
        except subprocess.TimeoutExpired:
            self.addCleanup(self.fail, "the door run was never stopped by the signal (hang)")
            raise

    def arm(self, lever):
        pulled = threading.Event()
        real = lever.pull

        def pull():
            try:
                return real()
            finally:
                pulled.set()
        lever.pull = pull
        return pulled

    def said(self, text):
        return any(text in s for s in self.err)


def _kill_self_after(wit, n, sigs, poked=None):
    """A helper thread: once the tree's `n` members are up, signal this process with `sigs`, each
    after the one before it was handled (`poked`)."""
    def go():
        wit.members(n)
        for i, s in enumerate(sigs):
            if poked is not None:
                poked.clear()
            os.kill(os.getpid(), s)
            if poked is not None and i < len(sigs) - 1:
                poked.wait(HANG_S)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


class CliCtrlC(_Sig):
    @unittest.skipUnless(POSIX, "Windows `os.kill(pid, SIGINT)` is TerminateProcess, not a Ctrl-C: "
                         "the Windows cell is #31 (a real CTRL_C_EVENT to a child)")
    def test_ctrl_c_in_the_suite_cancels_and_ends_the_tree(self):
        """#21 guard (in-process: the CLI block around a door run) — W: probe CTRL_C_EVENT."""
        wit = Witness(self)
        _kill_self_after(wit, 3, [signal.SIGINT])
        with self.assertRaises(C.Cancelled) as cm:
            with CC.cancel_on_sigint(planned=("the suite run",)):
                self.run_door(fake_argv(wit.port, 3))
        self.assertTrue(wit.gone())
        self.assertEqual("cancelled — nothing recorded", str(cm.exception))

    @unittest.skipUnless(POSIX, "Windows `os.kill(pid, SIGINT)` is TerminateProcess (#31)")
    def test_the_first_ctrl_c_names_what_it_is_ending(self):
        """force (B1 verifier Low) — the first line names the work and the trees it ends."""
        said, seen = [], threading.Event()
        real = CC.say_cancelling

        def spy(planned=(), trees=0):
            said.append((tuple(planned), trees))
            seen.set()
            return real(planned, trees)
        p = mock.patch.object(CC, "say_cancelling", spy)
        p.start()
        self.addCleanup(p.stop)
        wit = Witness(self)
        _kill_self_after(wit, 3, [signal.SIGINT])
        with self.assertRaises(C.Cancelled):
            with CC.cancel_on_sigint(planned=("the suite run",)):
                self.run_door(fake_argv(wit.port, 3))
        self.assertTrue(seen.wait(HANG_S))
        self.assertTrue(wit.gone())
        self.assertEqual([(("the suite run",), 1)], said)
        self.assertEqual("cancelling the suite run, ending the 1 process tree it started — "
                         "Ctrl-C again to stop at once", real(("the suite run",), 1))
        self.assertEqual("cancelling this command — Ctrl-C again to stop at once", real())

    def test_ctrl_c_after_the_suite_was_recorded_says_so(self):
        """#22 force (in-process; the CLI end-to-end is `test_f_spec_leg_in_merge_gate.CliSigint`)."""
        with self.assertRaises(C.Cancelled) as cm:
            with CC.cancel_on_sigint(planned=("the suite run", "the spec legs")) as lever:
                pulled = self.arm(lever)
                with C.commit("the suite run"):
                    pass
                signal.raise_signal(signal.SIGINT)
                self.assertTrue(pulled.wait(HANG_S))
                C.checkpoint()
        self.assertEqual("cancelled — recorded: the suite run; not recorded: the spec legs",
                         str(cm.exception))

    @unittest.skipUnless(POSIX, "a second SIGINT from a helper thread is POSIX; W: probe")
    def test_a_second_ctrl_c_while_stuck_stops_at_once(self):
        """#23 force — stuck in a wait that has no checkpoint (a sleeping `git`): the 2nd Ctrl-C
        raises at once and the tree is ended on the way out."""
        wit = Witness(self)
        _kill_self_after(wit, 2, [signal.SIGINT, signal.SIGINT], self.poked)
        timed_out = []
        with self.assertRaises(C.Cancelled):
            with CC.cancel_on_sigint():
                tree = Q.spawn_jobbed(fake_argv(wit.port, 2))
                try:
                    # No checkpoint here: only the 2nd signal ends it. `HANG_S` is hang detection —
                    # reaching it (the wait was never broken into) is the red, however the block
                    # then unwinds.
                    tree.wait_exit(HANG_S)
                except subprocess.TimeoutExpired:
                    timed_out.append(1)
                    raise
                finally:
                    tree.close()
        self.assertEqual([], timed_out, "the 2nd Ctrl-C did not stop the stuck wait at once")
        self.assertTrue(wit.gone())


class CommitWindows(_Sig):
    def test_one_ctrl_c_inside_a_commit_is_deferred_and_the_record_stands(self):
        """#24 force — the pull waits for the commit; the record is whole; the user is told."""
        body = []
        with self.assertRaises(C.Cancelled) as cm:
            with CC.cancel_on_sigint(planned=("rec",)) as lever:
                pulled = self.arm(lever)
                with C.commit("rec"):
                    signal.raise_signal(signal.SIGINT)
                    self.assertTrue(pulled.wait(HANG_S))
                    self.assertFalse(lever.is_set(), "a pull broke into the commit")
                    body.append("whole")
                self.assertTrue(lever.is_set(), "the deferred pull was never delivered")
                C.checkpoint()
        self.assertEqual(["whole"], body)
        self.assertEqual("cancelled — recorded: rec", str(cm.exception))
        self.assertTrue(self.said(CC.SAY_RECORDED_ANYWAY), self.err)

    def _two_signals_at(self, where):
        """Two SIGINTs raised at `where` (entry/body/exit of a commit): no hang, the record is
        whole or absent — never half — and the block ends `Cancelled`."""
        body = []
        real_enter, real_leave = C.enter_critical, C.leave_critical
        fired = []

        def fire():
            if not fired:
                fired.append(1)
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)

        def enter():
            real_enter()
            if where == "entry":
                fire()

        def leave():
            if where == "exit":
                fire()
            real_leave()
        with mock.patch.object(C, "enter_critical", enter), \
                mock.patch.object(C, "leave_critical", leave):
            with self.assertRaises(C.Cancelled) as cm:
                with CC.cancel_on_sigint(planned=("rec",)) as lever:
                    with C.commit("rec"):
                        if where == "body":
                            fire()
                        body.append("whole")
                    C.checkpoint()
        self.assertEqual(bool(body), lever.recorded == ["rec"], (where, body, lever.recorded))
        return str(cm.exception), lever

    def test_two_ctrl_c_inside_a_commit_body_finish_the_record_then_stop(self):
        """#24 force — the 2nd Ctrl-C is delivered when the window ends."""
        text, lever = self._two_signals_at("body")
        self.assertEqual(["rec"], lever.recorded)
        self.assertEqual("cancelled — recorded: rec", text)

    def test_two_ctrl_c_at_commit_entry_and_exit_never_hang(self):
        """#24 force."""
        for where in ("entry", "exit"):
            self._two_signals_at(where)

    def test_a_pull_from_another_thread_returns_while_a_commit_body_blocks(self):
        """#25 force — `pull()` never waits for the commit body (it defers)."""
        lever = C.Lever()
        returned, result = threading.Event(), {}

        def puller():
            result["pull"] = lever.pull()
            returned.set()
        with C.scope(lever):
            with C.commit("rec"):
                threading.Thread(target=puller, daemon=True).start()
                self.assertTrue(returned.wait(HANG_S), "pull() blocked on the commit body")
                self.assertFalse(result["pull"])
                self.assertTrue(lever.deferred)
        self.assertTrue(lever.is_set())
        self.assertEqual(["rec"], lever.recorded)


class DoorWindows(_Sig):
    @unittest.skipUnless(POSIX, "W: probe")
    def test_a_second_ctrl_c_between_popen_and_register_ends_the_tree_after_it(self):
        """#26 force — deferred to the end of the door's start window; the tree is registered,
        then ended; nothing is left in `_LIVE`."""
        wit = Witness(self)
        real_hold = Q._hold
        fired = []

        def hold(*a, **kw):
            tree = real_hold(*a, **kw)
            if not fired:
                fired.append(1)
                wit.members(3)
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)
            return tree
        with mock.patch.object(Q, "_hold", hold):
            with self.assertRaises(C.Cancelled):
                with CC.cancel_on_sigint():
                    self.run_door(fake_argv(wit.port, 3))
        self.assertTrue(wit.gone())
        with Q._LIVE_LOCK:
            self.assertEqual(set(), {t for t in Q._LIVE if t.proc.args[1:2] == [FAKE_PATH]})

    @unittest.skipUnless(POSIX, "W: probe")
    def test_a_third_ctrl_c_during_the_end_does_not_stop_the_end(self):
        """#27 force."""
        wit = Witness(self)
        real_end = Q.Jobbed._end_posix
        fired = []

        def end(self_, leftovers_only):
            if not fired:
                fired.append(1)
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)
            return real_end(self_, leftovers_only)
        _kill_self_after(wit, 3, [signal.SIGINT])
        with mock.patch.object(Q.Jobbed, "_end_posix", end):
            with self.assertRaises(C.Cancelled):
                with CC.cancel_on_sigint():
                    self.run_door(fake_argv(wit.port, 3))
        self.assertTrue(wit.gone())


class AfterTheLastCheckpoint(_Sig):
    def test_ctrl_c_before_any_record_writes_nothing(self):
        """#29 force — the commit's entry check: `Cancelled`, nothing written."""
        wrote = []
        with self.assertRaises(C.Cancelled) as cm:
            with CC.cancel_on_sigint(planned=("rec",)) as lever:
                pulled = self.arm(lever)
                C.checkpoint()                 # the last one
                signal.raise_signal(signal.SIGINT)
                self.assertTrue(pulled.wait(HANG_S))
                with C.commit("rec"):
                    wrote.append(1)
        self.assertEqual([], wrote)
        self.assertEqual("cancelled — nothing recorded", str(cm.exception))


@unittest.skipUnless(POSIX, "N/A on Windows (console close: #17 probe)")
class HangupAndTerm(_Sig):
    def test_sigterm_and_sighup_stop_at_once_and_end_the_tree(self):
        """#30 force. SIGHUP starts at its default here: a suite started under `nohup` inherits
        SIG_IGN, which the block keeps by design (`test_nohup_keeps_sighup_ignored`) — that is the
        round-3 verifier's L3 (test_b29 red only when run from a background job)."""
        prev = signal.signal(signal.SIGHUP, signal.SIG_DFL)
        self.addCleanup(signal.signal, signal.SIGHUP, prev)
        for sig in (signal.SIGTERM, signal.SIGHUP):
            wit = Witness(self)
            _kill_self_after(wit, 2, [sig])
            with self.assertRaises(C.Cancelled) as cm:
                with CC.cancel_on_sigint():
                    self.run_door(fake_argv(wit.port, 2))
            self.assertTrue(wit.gone(), sig)
            self.assertEqual("cancelled — nothing recorded", str(cm.exception))

    def test_sigterm_inside_a_commit_is_deferred(self):
        """#30 force."""
        with self.assertRaises(C.Cancelled) as cm:
            with CC.cancel_on_sigint(planned=("rec",)):
                with C.commit("rec"):
                    signal.raise_signal(signal.SIGTERM)
        self.assertEqual("cancelled — recorded: rec", str(cm.exception))

    def test_nohup_keeps_sighup_ignored(self):
        """#30 force — SIG_IGN is the user's choice (`nohup`) and survives the block."""
        prev = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGHUP, prev)
        with CC.cancel_on_sigint():
            self.assertEqual(signal.SIG_IGN, signal.getsignal(signal.SIGHUP))
        self.assertEqual(signal.SIG_IGN, signal.getsignal(signal.SIGHUP))


_WIN_CHILD = r"""
import ctypes, sys
sys.path.insert(0, {src!r})
ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
from mokata import ci_check as CC, quiet_spawn as Q
from mokata.knowledge import cancel as C
try:
    with CC.cancel_on_sigint():
        print("IN", flush=True)
        Q.run_jobbed({argv!r}, timeout=None)
except C.Cancelled as exc:
    print("CANCELLED", exc, flush=True)
    sys.exit(2)
"""


@unittest.skipUnless(os.name == "nt", "Windows console events (W: probe)")
class WindowsConsoleEvents(unittest.TestCase):
    def _run(self, event):
        wit = Witness(self)
        p = subprocess.Popen([sys.executable, "-c", _WIN_CHILD.format(
            src=SRC, argv=fake_argv(wit.port, 3))], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.addCleanup(lambda: p.poll() is None and Q.signal_pid(p.pid, SIGKILL))
        self.assertEqual("IN", p.stdout.readline().strip())
        wit.members(3)
        try:
            os.kill(p.pid, event)
        except OSError as exc:
            self.skipTest(f"console event undeliverable on this box: {exc}")
        self.assertTrue(wit.gone(), "the tree outlived the console event")
        rest = p.stdout.read()
        self.assertEqual(2, p.wait(HANG_S), rest)
        self.assertIn("CANCELLED", rest)

    def test_ctrl_c_event_ends_the_suite(self):
        """#31 force (probe)."""
        self._run(signal.CTRL_C_EVENT)

    def test_ctrl_break_event_is_ctrl_c(self):
        """#32 force (probe)."""
        self._run(signal.CTRL_BREAK_EVENT)


# ====================================================================== scale
@unittest.skipUnless(hasattr(os, "mkfifo"), "the FIFO witness is POSIX; W: probe (job completion port)")
class Scale(unittest.TestCase):
    def test_cancel_a_suite_of_150_processes_after_200_mb(self):
        """#33 force — witness EOF and the output stops growing (no latency claim)."""
        d = tempfile.mkdtemp(prefix="b29-scale-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        fifo, out = os.path.join(d, "w"), os.path.join(d, "out")
        os.mkfifo(fifo)
        r = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, r)
        hold = os.open(fifo, os.O_WRONLY)   # ours: no EOF before the members open it
        os.set_blocking(r, True)
        # The grower ends once this test process is gone or a write fails: a suite killed before
        # the cancel must not leave it appending (it once filled an 843 GB disk through a deleted
        # stderr file). While this test runs, only the cancel can stop it — the claim is unchanged.
        line = (f'exec 3>"{fifo}"; i=0; while [ $i -lt 150 ]; do sleep 1000 & i=$((i+1)); done; '
                f'head -c 209715200 /dev/zero > "{out}"; printf R >&3; '
                f'while kill -0 {os.getpid()} 2>/dev/null && printf x >> "{out}" 2>/dev/null; do :; done')
        lever = C.Lever()

        def work():
            with C.scope(lever):
                return Q.run_jobbed(shell_line=line, timeout=None)
        t, box = in_thread(work)
        import select
        ready, _, _ = select.select([r], [], [], HANG_S)
        self.assertTrue(ready, "the suite never got going")
        self.assertEqual(b"R", os.read(r, 1))
        os.close(hold)
        lever.pull()
        ready, _, _ = select.select([r], [], [], HANG_S)
        self.assertTrue(ready and os.read(r, 1) == b"", "a member of the 150 outlived the cancel")
        joined(self, t)
        self.assertIsInstance(box.get("exc"), C.Cancelled)
        size = os.path.getsize(out)
        self.assertGreaterEqual(size, 209715200)
        self.assertEqual(size, os.path.getsize(out), "the output grew after the tree ended")


# ====================================================================== the door's lows
class EndWinWaitsWhileItFalls(unittest.TestCase):
    """#35 force (inj on every OS: a fake job and a fake clock)."""

    def _end(self, counts):
        clock = {"t": 0.0}
        job = mock.Mock()
        job.active.side_effect = lambda: counts(clock["t"])
        proc = mock.Mock(pid=4242, returncode=0, args=["x"])
        proc.wait.return_value = 0
        tree = Q.Jobbed(proc, job, True)
        with mock.patch.object(Q, "_monotonic", lambda: clock["t"]), \
                mock.patch.object(Q, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + s)):
            return tree._end_win(), clock["t"]

    def test_a_count_that_keeps_falling_is_waited_for_past_the_flat_bound(self):
        """#35 force (inj: a fake job and clock) — RED under M26."""
        ended, t = self._end(lambda now: max(0, 5 - int(now // 25)))
        self.assertTrue(ended.ok, ended.why)
        self.assertGreater(t, Q.END_BOUND_S)

    def test_a_count_that_stalls_is_reported(self):
        """#35 guard — a stall is still reported, never waited on forever."""
        ended, _t = self._end(lambda now: 3)
        self.assertFalse(ended.ok)
        self.assertIn("after their number last fell", ended.why)


class NulByte(unittest.TestCase):
    def test_a_nul_byte_is_could_not_start_never_a_traceback(self):
        """#36 force — the door, the baseline (NOT_RUN) and the MCP tool (`ok: false`)."""
        with self.assertRaises(OSError) as cm:
            Q.run_jobbed(["echo", "a\0b"], timeout=None)
        self.assertIn("could not start", str(cm.exception))
        from mokata import baseline as B
        res = B.baseline_status("echo a\0b", cwd=tempfile.gettempdir())
        self.assertEqual(B.NOT_RUN, res.state)
        # Windows (round 4): the cmd line is refused, naming the NUL, before the door is reached.
        self.assertIn("U+0000" if os.name == "nt" else "could not start", res.detail)


_PARSING_CALLERS = ("mokata/branch_protection.py", "mokata/project_env.py",
                    "mokata/crg_resolve.py", "mokata/cli_commands/diagnostics.py",
                    "mokata/mcp_admin.py", "mokata/hook_cli.py")


class OutputWhole(unittest.TestCase):
    def test_more_than_2_mib_comes_back_whole(self):
        """#37 force — the door returns every byte by default."""
        n = 3 * 1024 * 1024
        rc, out, _err = Q.run_jobbed([sys.executable, "-c",
                                      f"import sys; sys.stdout.write('a' * {n - 1} + 'Z')"],
                                     timeout=None)
        self.assertEqual(0, rc)
        self.assertEqual(n, len(out))
        self.assertTrue(out.endswith("Z") and out.startswith("a"))

    def test_the_six_parsing_callers_never_ask_for_a_tail(self):
        """#37 force — each parsing caller calls the door WITHOUT `tail_bytes`, so it gets the
        whole answer (a mid-cut JSON is a wrong answer, not a short one)."""
        for rel in _PARSING_CALLERS:
            with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
                getattr(n.func, "attr", None) == "run_jobbed"
                or getattr(n.func, "id", None) == "run_jobbed")]
            self.assertTrue(calls, f"{rel} no longer calls the door")
            for c in calls:
                self.assertNotIn("tail_bytes", [k.arg for k in c.keywords], rel)

    def test_tail_bytes_is_the_exact_last_n_at_a_character_boundary(self):
        """#37 force."""
        rc, out, _ = Q.run_jobbed([sys.executable, "-c",
                                   "import sys; sys.stdout.write('0123456789' * 100)"],
                                  timeout=None, tail_bytes=7)
        self.assertEqual("3456789", out)
        rc, out, _ = Q.run_jobbed([sys.executable, "-c",
                                   "import sys; sys.stdout.buffer.write(b'ab' + 'é'.encode() * 4)"],
                                  timeout=None, tail_bytes=5)
        self.assertEqual("éé", out)

    def test_a_windows_code_page_decodes_through_the_locale(self):
        """#37 force (OEM on the probe) — cp1252 on Windows; never the locale elsewhere."""
        with mock.patch("locale.getpreferredencoding", return_value="cp1252"):
            self.assertEqual("café", Q._decode(b"caf\xe9", True))
            self.assertEqual("caf\ufffd", Q._decode(b"caf\xe9", False))


class MovedLaunchersUseTheDoor(unittest.TestCase):
    def test_a_grandchild_holding_stdout_does_not_hold_the_run(self):
        """#39 force — the leader exits; the run returns; what it left is ended."""
        wit = Witness(self)
        rc, out, _ = Q.run_jobbed(fake_argv(wit.port, 2, "leave"), timeout=None)
        self.assertEqual(0, rc)
        self.assertIn("leader done", out)
        wit.members(2)
        self.assertTrue(wit.gone(), "the grandchild outlived the run")

    def test_the_updater_step_returns_at_its_leaders_exit(self):
        """#39 force — `_updater._run` (stdlib, not the door): a temp file, so a grandchild that
        holds the output cannot hold the step."""
        from mokata import _updater
        wit = Witness(self)
        t, box = in_thread(_updater._run, fake_argv(wit.port, 2, "leave"))
        wit.members(2)                         # the grandchild is up (the cleanup ends it)
        t.join(HANG_S)
        self.assertFalse(t.is_alive(), "the step waited on the grandchild that held its output")
        rc, text = box["out"]
        self.assertEqual(0, rc)
        self.assertIn("leader done", text)

    def test_the_moved_launchers_call_the_door(self):
        """#39 force — none of them is back on `subprocess`."""
        for rel, fn in (("mokata/claude_code_version.py", "installed_version"),
                        ("mokata/install_isolation.py", "_ask_version"),
                        ("mokata/ide/handoff.py", "_update"),
                        ("mokata/govern/domain_gates.py", "_run_tool"),
                        ("mokata/knowledge/crg_client.py", "_default_run_cli"),
                        ("mokata/ide/health.py", "_stream")):
            with open(os.path.join(SRC, rel), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            body = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == fn)
            names = {getattr(c.func, "attr", None) or getattr(c.func, "id", None)
                     for c in ast.walk(body) if isinstance(c, ast.Call)}
            self.assertTrue(names & {"run_jobbed", "spawn_jobbed"}, (rel, fn))
            self.assertFalse(names & {"Popen", "check_output", "check_call"}, (rel, fn))
            self.assertNotIn("run", {c.func.attr for c in ast.walk(body)
                                     if isinstance(c, ast.Call)
                                     and isinstance(c.func, ast.Attribute)
                                     and isinstance(c.func.value, ast.Name)
                                     and c.func.value.id == "subprocess"}, (rel, fn))


class IdeCancelEndsTheTree(unittest.TestCase):
    def test_the_ide_update_cancel_ends_pip_and_its_children(self):
        """#41 force (W: probe) — `handoff._update` under a cancelled job."""
        from mokata.ide import handoff
        wit = Witness(self)
        plan = mock.Mock(argv=fake_argv(wit.port, 3), command="x")
        ev = threading.Event()

        def work():
            with C.scope(ev):
                return handoff._update(".", {})
        with mock.patch("mokata.update.plan_update", return_value=plan):
            t, box = in_thread(work)
            wit.members(3)
            ev.set()
            self.assertTrue(wit.gone())
            joined(self, t)
        self.assertIsInstance(box.get("exc"), C.Cancelled)

    def test_the_ide_install_stream_cancel_ends_the_whole_tree(self):
        """#41 force — `health._stream`: the door tree is ended, not just the leader."""
        from mokata.ide import health
        wit = Witness(self)
        ev = threading.Event()
        buf = []

        def work():
            with C.scope(ev):
                return health._stream(fake_argv(wit.port, 3), "pip install", buf)
        t, box = in_thread(work)
        wit.members(3)
        ev.set()
        self.assertTrue(wit.gone(), "the install's children outlived its Cancel")
        joined(self, t)
        self.assertIsInstance(box.get("exc"), C.Cancelled)


# ====================================================================== Windows first word
class RunnerRefusal(unittest.TestCase):
    def test_every_shell_host_spelling_is_refused(self):
        """#43 force (inj: names are judged on every OS; links on the probe)."""
        from mokata import exe_path as X
        for name in ("cmd.exe", "CMD", "cmd.exe.", "cmd .exe ", "powershell_ise.exe", "pwsh",
                     "wsl.exe", "cscript", "mshta.exe", "cmd", r"C:\Windows\System32\cmd.exe"):
            self.assertIsNotNone(X.win_refusal(name), name)
        self.assertIsNone(X.win_refusal(r"C:\tools\pytest.exe"))

    @unittest.skipUnless(os.name == "nt", "a link to cmd.exe (W: probe)")
    def test_a_link_to_cmd_is_refused_after_resolution(self):
        """#43 force (W: probe) — a link is judged by what it resolves to."""
        from mokata import exe_path as X
        d = tempfile.mkdtemp(prefix="b29-link-")
        link = os.path.join(d, "runner.exe")
        try:
            os.symlink(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                    "System32", "cmd.exe"), link)
        except OSError as exc:
            self.skipTest(f"cannot make a symlink here: {exc}")
        found, why = X.win_runnable(link)
        self.assertIsNone(found)
        self.assertTrue(why)


class FirstWordResolution(unittest.TestCase):
    """#44/#45 force — `win_shell_line` (inj on every OS: it judges files; W: probe runs it)."""

    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="b29-cmd-"))
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.repo = os.path.join(self.d, "repo")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.repo)
        os.makedirs(self.bin)
        self.env = {"PATH": self.bin, "PATHEXT": ".COM;.EXE;.BAT;.CMD"}

    def _touch(self, *parts):
        p = os.path.join(*parts)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("")
        return p

    def test_a_planted_cmd_loses_to_the_program_on_path(self):
        """#44 force."""
        self._touch(self.repo, "pytest.cmd")
        real = self._touch(self.bin, "pytest.exe")
        line = Q.win_shell_line("pytest -q", self.repo, self.env)
        self.assertEqual(f'"{real}" -q', line)

    def test_a_planted_only_program_is_refused_by_name(self):
        """#44 force."""
        self._touch(self.repo, "pytest.cmd")
        with self.assertRaises(OSError) as cm:
            Q.win_shell_line("pytest -q", self.repo, self.env)
        self.assertIn("pytest.cmd", str(cm.exception))

    def test_repo_local_gradlew_bat_is_refused_with_the_remedy(self):
        """#45 force."""
        self._touch(self.repo, "gradlew.bat")
        with self.assertRaises(OSError) as cm:
            Q.win_shell_line("gradlew test", self.repo, self.env)
        self.assertIn(".\\gradlew.bat", str(cm.exception))

    def test_the_project_env_below_the_repo_is_on_path_and_the_repo_root_is_not(self):
        """force (round 4) — a program found nowhere is now REFUSED, so the project env's
        `Scripts\\` (on PATH by name, below the repo) must resolve; the repo root on PATH must not
        (it is cmd's implicit search spelled as a PATH entry)."""
        scripts = os.path.join(self.repo, ".venv", "Scripts")
        os.makedirs(scripts)
        real = self._touch(scripts, "pytest.exe")
        env = dict(self.env, PATH=os.pathsep.join((self.repo, scripts)))
        self._touch(self.repo, "pytest.cmd")
        self.assertEqual(f'"{real}" -q', Q.win_shell_line("pytest -q", self.repo, env))
        self._touch(self.repo, "only.exe")
        with self.assertRaises(OSError):
            Q.win_shell_line("only", self.repo, dict(self.env, PATH=self.repo))

    def test_pathext_order_decides_between_two_on_path(self):
        """force (round-4 verifier L4) — cmd tries PATHEXT in order; so does the resolution."""
        cmd = self._touch(self.bin, "dual.cmd")
        exe = self._touch(self.bin, "dual.exe")
        for pathext, want in ((".CMD;.EXE", cmd), (".EXE;.CMD", exe), (".BAT;.CMD;.EXE", cmd),
                              (".COM;.EXE;.BAT;.CMD", exe)):
            with self.subTest(pathext=pathext):
                env = dict(self.env, PATHEXT=pathext)
                self.assertEqual(f'"{want}"', Q.win_shell_line("dual", self.repo, env))

    def test_a_builtin_first_word_runs_as_written(self):
        """#45 guard — the builtin as written; the program after it resolved (round 4)."""
        self._touch(self.repo, "pytest.cmd")
        real = self._touch(self.bin, "pytest.exe")
        line = "cd sub && pytest"
        self.assertEqual(f'cd sub && "{real}"', Q.win_shell_line(line, self.repo, self.env))

    @unittest.skipUnless(os.name == "nt", "the baseline through cmd.exe (W: probe)")
    def test_the_baseline_names_the_planted_program(self):
        """#44 force (W: probe) — the baseline says NOT_RUN and names the file."""
        from mokata import baseline as B
        self._touch(self.repo, "pytest.cmd")
        res = B.baseline_status("pytest -q", cwd=self.repo, environ=dict(os.environ, **self.env))
        self.assertEqual(B.NOT_RUN, res.state)
        self.assertIn("pytest.cmd", res.detail)


class FirstWordAsCmdReadsIt(unittest.TestCase):
    """#44 widened (B2 verifier M1, its V7) — the first word ends where cmd.exe ends it: at its
    token delimiters (blank, tab, `,` `;` `=`, VT, FF, NBSP), its specials (`&|<>()`), a quote
    boundary or a `/` switch; a leading run of delimiters or `@` is skipped; `^` escapes the next
    character into the word. Injected (it judges file names), so it runs on every OS."""

    def _line(self, line, on_path=("mytool.exe",), planted=("mytool.cmd",)):
        d = os.path.realpath(tempfile.mkdtemp(prefix="b29-fw-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        repo, ub = os.path.join(d, "repo"), os.path.join(d, "bin")
        os.makedirs(repo)
        os.makedirs(ub)
        for n in on_path:
            open(os.path.join(ub, n), "w", encoding="utf-8").close()
        for n in planted:
            open(os.path.join(repo, n), "w", encoding="utf-8").close()
        env = {"PATH": ub, "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
        return Q.win_shell_line(line, repo, env), ub

    def test_every_cmd_delimiter_after_the_word_is_resolved_on_path(self):
        """force (V7) — `mytool;/q` must not pass through: cmd would run the planted file."""
        for sep in (",", ";", "=", "/", " ", "\t", "<", ">"):
            with self.subTest(sep=repr(sep)):
                out, ub = self._line(f"mytool{sep}q")
                self.assertTrue(out.startswith(f'"{os.path.join(ub, "mytool.exe")}"'), repr(out))
                self.assertTrue(out.endswith(f"{sep}q"), repr(out))

    def test_every_cmd_delimiter_with_only_a_planted_file_is_refused(self):
        """force (V7) — nothing on PATH, a planted `mytool.cmd`: refused, whatever ends the word."""
        for sep in (",", ";", "=", "\x0b", "\x0c", "\xa0", "/", " "):
            with self.subTest(sep=repr(sep)):
                with self.assertRaises(OSError):
                    self._line(f"mytool{sep}q", on_path=())

    def test_leading_delimiters_and_at_are_skipped(self):
        """force — `;,=@ mytool` runs `mytool` in cmd."""
        for lead in (";", ",", "=", " ;@", "@;", "\t,"):
            with self.subTest(lead=repr(lead)):
                out, ub = self._line(f"{lead}mytool -q")
                self.assertEqual(f'{lead}"{os.path.join(ub, "mytool.exe")}" -q', out)

    def test_an_unquoted_caret_is_refused_and_a_quoted_one_is_text(self):
        """force (round 4) — an escape changes where commands begin: refused, not read."""
        for line in ("my^tool -q", "mytool^;x -q", "mytool ^& x"):
            with self.subTest(line=line):
                with self.assertRaises(OSError) as cm:
                    self._line(line)
                self.assertIn("`^`", str(cm.exception))
        out, ub = self._line('mytool "a^b"')
        self.assertEqual(f'"{os.path.join(ub, "mytool.exe")}" "a^b"', out)

    def test_quotes_inside_the_word_join_it(self):
        """force — cmd drops the quotes: `my"tool"` is `mytool`."""
        out, ub = self._line('my"tool" -q')
        self.assertEqual(f'"{os.path.join(ub, "mytool.exe")}" -q', out)

    def test_an_explicit_path_is_left_as_written(self):
        """guard — a path names the file the user meant (round 4: disclosed in the docstring)."""
        for line in ("tools\\mytool -q", "C:mytool", ".\\mytool", "mytool & .\\gradlew.bat"):
            with self.subTest(line=line):
                out, ub = self._line(line)
                self.assertEqual(line.replace("mytool &", f'"{os.path.join(ub, "mytool.exe")}" &'),
                                 out)


class ResolvedOrRefused(unittest.TestCase):
    """Round 4 (round-3 verifier H1 + M2): the whole class, not one character at a time — every
    program cmd would search for is resolved from PATH or the line is refused by name. Injected
    (it judges file names), so it runs on every OS; `FirstWordAgainstRealCmd` replays the
    verifier's fuzz corpus against real cmd.exe."""

    def _line(self, line, on_path=("mytool.exe",), planted=()):
        d = os.path.realpath(tempfile.mkdtemp(prefix="b29-rr-"))
        self.addCleanup(__import__("shutil").rmtree, d, True)
        repo, ub = os.path.join(d, "repo"), os.path.join(d, "bin")
        os.makedirs(repo)
        os.makedirs(ub)
        for n in on_path:
            open(os.path.join(ub, n), "w", encoding="utf-8").close()
        for n in planted:
            open(os.path.join(repo, n), "w", encoding="utf-8").close()
        self.mt = os.path.join(ub, "mytool.exe")
        env = {"PATH": ub, "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
        return Q.win_shell_line(line, repo, env)

    def _refused(self, line, says, **kw):
        with self.assertRaises(OSError, msg=repr(line)) as cm:
            self._line(line, **kw)
        self.assertIn(says, str(cm.exception), repr(line))
        return str(cm.exception)

    def test_a_control_character_anywhere_is_refused_by_name(self):
        """force (H1) — CR, LF, NUL, every C0 but tab, DEL, every C1: refused, named."""
        for o in list(range(0, 9)) + list(range(10, 32)) + list(range(0x7F, 0xA0)):
            c = chr(o)
            for line in (f"mytool{c}q", f"{c}mytool", f"mytool -q {c}", f'mytool "{c}"'):
                with self.subTest(line=repr(line)):
                    self._refused(line, f"U+{o:04X}")

    def test_tab_is_a_delimiter_and_the_no_break_space_is_part_of_the_word(self):
        """force (round 4, real cmd: probe 43464625189 ran a planted `\xa0.cmd`) — tab ends the
        word; NBSP does NOT, so `mytool\xa0q` is a name not on PATH: refused, never passed."""
        out = self._line("mytool\t-q")
        self.assertEqual(f'"{self.mt}"\t-q', out)
        for line in ("mytool\xa0q", "\xa0mytool", "\xa0"):
            with self.subTest(line=repr(line)):
                self._refused(line, "")

    def test_a_command_with_text_but_no_program_is_refused(self):
        """force (round 4, real cmd: `, ;` ran a planted `,.cmd`) — only blanks are "nothing"."""
        for line in (", ;", ",", "=", "@", ";;", "mytool & ,", "mytool && @"):
            with self.subTest(line=repr(line)):
                self._refused(line, "no program")
        out = self._line("mytool &  ")
        self.assertEqual(f'"{self.mt}" &  ', out)

    def test_a_program_not_on_path_is_refused_with_the_fix(self):
        """force (M2) — nothing on PATH and nothing planted is NOT passed through: cmd would try
        `mytool q.cmd` in the cwd."""
        msg = self._refused("mytool q", "not on PATH", on_path=())
        self.assertIn("add its directory to PATH", msg)
        self._refused("mytool q", "not on PATH", on_path=(), planted=("mytool q.cmd",))
        self._refused("nosuch", "not on PATH")

    def test_a_cmd_variable_anywhere_is_refused(self):
        """force — `%` / `!` expand before cmd parses (disclosed choice: refuse, not read)."""
        for line in ("%X% -q", "%X:~0,0%mytool", "mytool%X:~0,0%", "mytool %X%", "!X! -q",
                     "mytool -k \"a!\"", "%NOPE%", "%COMSPEC% /c echo hi"):
            with self.subTest(line=line):
                self._refused(line, "a cmd variable")

    def test_a_group_is_refused_and_a_quoted_paren_is_text(self):
        """force — `(mytool)` / `@(mytool)` / `mytool (x)`."""
        for line in ("(mytool)", "(mytool -q)", "@(mytool)", "mytool (x)", "mytool )"):
            with self.subTest(line=line):
                self._refused(line, "group")
        out = self._line('mytool "(x)"')
        self.assertEqual(f'"{self.mt}" "(x)"', out)

    def test_a_builtin_that_runs_another_command_is_refused(self):
        """force — `call mytool`, `start mytool`, `if 1==1 mytool`, `for … do mytool`."""
        for line in ("call mytool", "start mytool", "if 1==1 mytool", "for %i in (1) do mytool",
                     "CALL mytool", "echo hi & call mytool"):
            with self.subTest(line=line):
                with self.assertRaises(OSError):
                    self._line(line)
        self._refused("call mytool", "runs another command")
        self._refused("if 1==1 mytool", "runs another command")

    def test_every_command_of_a_chain_is_resolved(self):
        """force — cmd searches the cwd for EVERY command, not only the first."""
        for line, want in (("echo hi & mytool", 'echo hi & "{m}"'),
                           ("mytool && mytool -q", '"{m}" && "{m}" -q'),
                           ("mytool|mytool", '"{m}"|"{m}"'),
                           ("mytool || echo failed", '"{m}" || echo failed'),
                           ("cd sub&&mytool", 'cd sub&&"{m}"'),
                           ("mytool > out.txt 2>&1", '"{m}" > out.txt 2>&1'),
                           ("mytool 2>&1 | mytool", '"{m}" 2>&1 | "{m}"'),
                           ('mytool "a & b"', '"{m}" "a & b"')):
            with self.subTest(line=line):
                out = self._line(line)
                self.assertEqual(want.format(m=self.mt), out)
        self._refused("mytool && nosuch", "`nosuch`, which is not on PATH")
        self._refused("echo hi & mytool", "would run `mytool.cmd`", on_path=(),
                      planted=("mytool.cmd",))

    def test_a_redirection_first_or_an_empty_name_is_refused(self):
        """force — `>out mytool` / `"" mytool`."""
        self._refused(">out mytool", "redirection")
        self._refused("mytool & <in mytool", "redirection")
        self._refused('"" mytool', "empty program name")

    def test_a_shell_host_is_refused_even_on_path(self):
        """force — `cmd /c x` would re-parse `x` and search the cwd again."""
        for name, line in (("cmd.exe", "cmd /c mytool"), ("powershell.exe", "powershell -c mytool"),
                           ("wsl.exe", "mytool & wsl mytool")):
            with self.subTest(line=line):
                self._refused(line, "runs the shell", on_path=("mytool.exe", name))

    def test_a_quoted_builtin_is_a_program_not_a_builtin(self):
        """force — cmd treats `"cd"` as a program name and searches for it."""
        self._refused('"cd" sub', "not on PATH")
        self._refused('"cd" sub', "would run `cd.cmd`", planted=("cd.cmd",))


def _fuzz_corpus():
    """The round-3 verifier's fuzz corpus, line for line (its `test_verify_b3_cmd_fuzz.py`): code
    points 1..255 in five positions, its hand lines, and a seeded random fill to 2200 lines."""
    import random
    delims = [" ", "\t", ",", ";", "=", "\x0b", "\x0c", "\xa0"]
    pieces = ["mytool", "my", "tool", "q", "-q", "@", "^", '"', "/", "\\", ".", ":", "%X%",
              "%X:~0,0%", "!X!", "%NOPE%", "(", ")", "&", "|", "<", ">", "&&", "+", "~", "\x85",
              "\xad", "\x1a", "\n", "\r", "\x7f", "\x01"] + delims * 2
    lines = []
    for c in range(1, 256):
        ch = chr(c)
        lines += [f"mytool{ch}q", f"{ch}mytool q", f"mytool{ch}", f'"mytool{ch}q"', f"my{ch}tool"]
    lines += ["%X% -q", "%X:~0,0%mytool", "mytool%X:~0,0%", "%COMSPEC% /c echo hi", "%NOPE%",
              "(mytool)", "(mytool -q)", "@(mytool)", "call mytool", "echo hi & mytool",
              "if 1==1 mytool", '"mytool', 'mytool"', '""mytool', '"" mytool', "mytool\nq",
              "mytool\rq", "\nmytool", "mytool^\nq", 'my"to"ol', '"my"tool', "mytool.",
              "mytool ..", "MYTOOL", "mytool.cmd", "mytool.exe", "mytool.CMD", ".mytool",
              "mytool.bat"]
    rnd = random.Random(29)
    while len(lines) < 2200:
        lines.append("".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 6))))
    return lines


def _fuzz_plants(line):
    """Every file name a hostile repo could plant for `line` (the verifier's `cands`, which
    includes the `<word> <args>` names cmd tries when a word is found nowhere)."""
    bad = set('"<>|/\\?*:') | {chr(i) for i in range(32)}
    out = {"mytool", "my", "tool", "mytool q"}
    for view in (line, line.replace("^", ""), line.replace('"', ""),
                 line.replace("^", "").replace('"', "")):
        s = "".join(c for c in view if c not in bad and c not in "%&!()")
        for k in range(1, min(len(s), 14) + 1):
            n = s[:k]
            if n and n[-1] not in " ." and n[0] != " ":
                out.add(n)
    return out


@unittest.skipUnless(os.name == "nt", "real cmd.exe (W: probe)")
class FirstWordAgainstRealCmd(unittest.TestCase):
    """Round 4 (round-3 verifier H1 + M2): the verifier's WHOLE fuzz corpus as a matrix against
    REAL cmd.exe. For every line, with `mytool.exe` on PATH and without it, and every name a repo
    could plant for that line sitting in the cwd as a `.cmd`: mokata refuses the line, or cmd runs
    it with no planted file — every program it would have searched for is the quoted absolute path
    mokata resolved. An explicit path (`.\\x`, `C:x`) is the user's own choice of file (disclosed)
    and is exempt."""

    def setUp(self):
        import shutil
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="b29-fz-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ub = os.path.join(self.root, "bin")
        os.makedirs(self.ub)
        self.tool = os.path.join(self.ub, "mytool.exe")
        shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe"), self.tool)
        self.with_path = dict(os.environ, X="mytool")
        self.with_path["PATH"] = self.ub + os.pathsep + self.with_path["PATH"]
        self.no_path = dict(os.environ, X="mytool")

    def _repo(self, names):
        import shutil
        d = os.path.join(self.root, "repo")
        shutil.rmtree(d, True)
        os.makedirs(d)
        for n in names:
            try:
                with open(os.path.join(d, n + ".cmd"), "w", encoding="ascii") as fh:
                    fh.write("@echo PLANTED:%~n0\r\n")
            except (OSError, ValueError):
                pass
        return d

    @staticmethod
    def _explicit(line):
        def path(w):
            w = w or ""
            return "\\" in w or (len(w) > 1 and w[1] == ":")
        split = getattr(Q, "_cmd_commands", None)       # absent on the RED control's tree
        if split is None:
            return path(Q._cmd_first_word(line)[0])
        try:
            return any(path(Q._cmd_first_word(line[a:b])[0]) for a, b in split(line))
        except OSError:
            return False

    def test_every_corpus_line_is_resolved_or_refused(self):
        """force (H1, M2) — 2200 lines x {PATH, no PATH}; the planted file never runs."""
        import subprocess as sp
        lines = _fuzz_corpus()
        ran, refused, resolved = [], 0, 0
        for line in lines:
            repo = self._repo(_fuzz_plants(line))
            for env, tag in ((self.with_path, "path"), (self.no_path, "nopath")):
                try:
                    g = Q.win_shell_line(line, repo, env)
                except OSError:
                    refused += 1
                    continue
                resolved += 1
                if self._explicit(line):
                    continue
                p = sp.run(g, shell=True, cwd=repo, env=env, capture_output=True, timeout=60,
                           stdin=sp.DEVNULL)
                if b"PLANTED" in (p.stdout or b"") + (p.stderr or b""):
                    ran.append((tag, line, g))
        print(f"\nCORPUS lines={len(lines)} refused={refused} resolved={resolved} "
              f"planted_ran={len(ran)}")
        for r in ran[:40]:
            print("PLANTED-RAN", repr(r))
        self.assertEqual([], ran, "cmd ran a planted file for these lines")
        self.assertGreater(resolved, 100, "the matrix must exercise lines mokata hands to cmd")

    def test_cr_lf_and_args_ext_through_the_door(self):
        """force (H1, M2) — the product's door, `run_jobbed(shell_line=)`, refuses them."""
        for line, plant in (("mytool\nq", {"q"}), ("mytool\rq", {"q"}), ("mytool q", {"mytool q"})):
            with self.subTest(line=repr(line)):
                repo = self._repo(plant)
                with self.assertRaises(OSError):
                    Q.run_jobbed(shell_line=line, timeout=HANG_S, cwd=repo, env=self.no_path)

    def test_a_resolved_line_runs_the_program_on_path(self):
        """guard — the rewrite still runs the real program (whoami prints a user name)."""
        repo = self._repo({"mytool"})
        rc, out, _err = Q.run_jobbed(shell_line="mytool", timeout=HANG_S, cwd=repo,
                                     env=self.with_path)
        self.assertEqual(0, rc)
        self.assertNotIn("PLANTED", out)
        self.assertIn("\\", out)                            # DOMAIN\user


class AskOsNeverWaitsUnbounded(unittest.TestCase):
    def test_ask_os_on_a_handle_host_always_hands_popen_wait_a_finite_slice(self):
        """force (B2 verifier V5; kills M17) — whatever the caller passes, the OS wait on a
        handle-waiting host (Windows) is a bounded slice: `Popen.wait(None)` is
        `WaitForSingleObject(INFINITE)`, which Ctrl-C cannot interrupt (N1)."""
        waits = []

        class FakeProc:
            returncode = None
            args = ["x"]
            pid = 4242

            def wait(self, timeout=None):
                waits.append(timeout)
                raise subprocess.TimeoutExpired("x", timeout or 0)

            def poll(self):
                return None

        tree = Q.Jobbed.__new__(Q.Jobbed)
        tree.proc = FakeProc()
        tree._by_handle = True
        tree._status = None
        tree._exited = threading.Event()
        tree._waiter = threading.Lock()
        for t in (None, 0.1, 5.0, 1e6):
            self.assertFalse(tree._ask_os(t))
        self.assertEqual(4, len(waits))
        self.assertTrue(all(w is not None and w <= Q._POLL_MAX_S for w in waits), waits)

    @unittest.skipIf(hasattr(os, "waitid"), "the kqueue host (macOS); Linux slices by construction")
    def test_ask_os_on_a_kqueue_host_always_hands_kevent_a_finite_slice(self):
        """force (kills M17, the macOS form) — a cancel is a pull from another thread, which would
        not wake a blocking kevent; PEP 475 helps signals only."""
        waits = []

        class FakeKq:
            def control(self, changes, n, timeout=None):
                waits.append(timeout)
                return []

        class FakeProc:
            returncode = None
            pid = 4242

        tree = Q.Jobbed.__new__(Q.Jobbed)
        tree.proc = FakeProc()
        tree._by_handle = False
        tree._kq = FakeKq()
        tree._status = None
        tree._exited = threading.Event()
        for t in (None, 0.1, 5.0, 1e6):
            self.assertFalse(tree._ask_os(t))
        self.assertEqual(4, len(waits))
        self.assertTrue(all(w is not None and w <= Q._POLL_MAX_S for w in waits), waits)


class TheMovedLaunchersKeepTheirArguments(unittest.TestCase):
    """B2 verifier L2 (MV3/MV4/MV5): the door call keeps the launcher's `cwd` / `env`."""

    def _spy(self):
        seen = []

        def run_jobbed(argv=None, **kw):
            seen.append((list(argv or []), kw))
            return 0, "", ""
        p = mock.patch.object(Q, "run_jobbed", run_jobbed)
        p.start()
        self.addCleanup(p.stop)
        return seen

    def test_domain_gates_runs_the_tool_in_its_cwd(self):
        """force (kills MV3)."""
        from mokata.govern import domain_gates as DG
        seen = self._spy()
        DG._run_tool(["tool", "-x"], cwd="/the/repo", timeout=5.0)
        self.assertEqual([(["tool", "-x"], {"cwd": "/the/repo", "timeout": 5.0})], seen)

    def test_the_crg_cli_runs_in_the_repo_root(self):
        """force (kills MV4)."""
        from mokata.knowledge import crg_client as CRG
        seen = self._spy()
        client = CRG.CodeReviewGraphClient.__new__(CRG.CodeReviewGraphClient)
        client.name, client.root, client.timeout = "code-review-graph", "/the/repo", 7.0
        with mock.patch.object(CRG.CodeReviewGraphClient, "_command", lambda self: "crg"):
            client._default_run_cli(["--version"])
            client._default_run_cli(["embed"])
        self.assertEqual([(["crg", "--version"], {"timeout": 7.0, "cwd": "/the/repo"}),
                          (["crg", "embed"], {"timeout": None, "cwd": "/the/repo"})], seen)

    def test_the_approver_passes_its_env_and_root(self):
        """force (kills MV5)."""
        from mokata import harness_cert as HC
        seen = self._spy()
        env = {"ONLY": "this"}
        self.assertTrue(HC.default_approver(env=env)("/the/repo", "p-1"))
        self.assertEqual(1, len(seen))
        argv, kw = seen[0]
        self.assertEqual(["approve", "p-1", "--yes", "--path", "/the/repo"], argv[3:])
        self.assertEqual({"cwd": "/the/repo", "env": env, "timeout": 120}, kw)


class HandshakeLeftRunning(unittest.TestCase):
    def test_a_server_mokata_could_not_end_is_left_running_not_ok(self):
        """#47 force."""
        from mokata import mcp_admin as MA
        res = MA._said(MA.HandshakeResult(True, "connected"),
                       Q.Ended(False, False, "2 process(es) were still running"))
        self.assertFalse(res.ok)
        self.assertEqual("left_running", res.code)
        self.assertIn("2 process(es) were still running", res.detail)
        res = MA._said(MA.HandshakeResult(False, "timeout", "no answer", "fix"),
                       Q.Ended(False, False, "why"))
        self.assertEqual("timeout", res.code)
        self.assertIn("could not end the server", res.detail)
        ok = MA.HandshakeResult(True, "connected")
        self.assertIs(ok, MA._said(ok, Q.Ended(True)))


if __name__ == "__main__":
    unittest.main()
