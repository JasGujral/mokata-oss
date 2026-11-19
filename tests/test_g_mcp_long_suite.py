"""0.0.29 lane G (#82-1b) — a long suite over MCP: the reply budget is not the suite's life.

The design is `docs/build/handoff/0029-design/G-gates-mcp-recording.md` §3.1 (+ §9), consuming lane B's
door and MCP lifecycle (`run_jobbed(timeout=None, progress=…)`, the told-running join, the tree key,
the I4 progress sink). Each test's docstring carries its label: **force** = red on the base
(`9045ad7`), **guard** = pins behaviour that already held (lane B's half, or a road the text names).

**Every wait here is an event the test controls, never an interval:**
  * a suite is a `python` script that writes its pid, then blocks until the TEST creates a gate file
    (or writes a line only when the test creates a numbered step file) — "still running at the reply
    budget" is a state, not a race;
  * the silence policy reads an INJECTED clock the test moves; "the policy looked at time T" is the
    progress report carrying T, delivered to a sink the test arms (`knowledge.cancel.reporting`);
  * the reply budget is `_mcp_timeout_for`, a seam on a constant: 0 answers `running` at once
    (the suite is blocked on its gate), `HANG_S` collects the result;
  * `HANG_S` is hang DETECTION (how long an event may take before the test says it never came),
    never a deadline on the product. `MOKATA_TEST_HANG_S` shortens it for RED and mutant runs.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import baseline as B  # noqa: E402
from mokata import quiet_spawn as Q  # noqa: E402
from mokata.knowledge import cancel as C  # noqa: E402
from mokata.mcp import server as MS  # noqa: E402
from mokata.mcp import tools_read as TR  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
HANG_S = float(os.environ.get("MOKATA_TEST_HANG_S", "300"))
POSIX = os.name != "nt"

# Every fake that can OUTLIVE its test starts with this guard (round-2 re-verify M-V1). A SIGKILLed
# test never removes its temp dir, so "my directory is gone" alone left the fake polling forever
# (ppid 1, its spool held). `_orphaned()` is true once the parent it started with is gone (the
# reparent: `getppid()` compared with the value at start, never with 1 — launchd and subreapers
# differ) or once the TEST process stamped in as `__OWNER__` is gone (a `| cat` pipe keeps `sh`
# alive as the parent, so the parent check alone misses it). Not stdin EOF: the door gives every
# suite an empty stdin file, which is EOF from the first read. On Windows the door's job object
# ends the tree with its owner, so only the parent check applies there (`os.kill(pid, 0)` would
# TERMINATE the process on Windows, so it is never called there).
GUARD = r'''
import os as _os
_OWNER = __OWNER__
_PPID = _os.getppid()


def _orphaned():
    if _os.getppid() != _PPID:
        return True
    if _os.name != "nt":
        try:
            _os.kill(_OWNER, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
    return False
'''


def _fake(body, owner=None):
    """The fake's source: the guard, stamped with the test process's pid, then the body."""
    return GUARD.replace("__OWNER__", str(owner if owner is not None else os.getpid())) + body


# The fake suite. argv: log gate rc [steps_dir] [touch]. Appends its pid to `log`; for every
# `steps_dir/step-N` the test creates it writes one line (in order); exits `rc` once `gate` exists.
# `touch` (a path) is rewritten first — a suite that writes the working tree.
SUITE = r'''
import os, sys, time
log, gate, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
steps = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
touch = sys.argv[5] if len(sys.argv) > 5 else None
if touch:
    with open(touch, "a", encoding="utf-8") as fh:
        fh.write("changed by the suite\n")
with open(log, "a", encoding="utf-8") as fh:
    fh.write("%d\n" % os.getpid())
n = 1
home = os.path.dirname(gate)
while not os.path.exists(gate):
    if not os.path.isdir(home) or _orphaned():    # the test is gone: never an orphan
        sys.exit(3)
    if steps and os.path.exists(os.path.join(steps, "step-%d" % n)):
        sys.stdout.write("line %d\n" % n)
        n += 1
    time.sleep(0.02)
sys.exit(rc)
'''

# Writes 128 MiB (2x the injected 64 MiB bound) and then STAYS ALIVE, never exiting on its own,
# until its own directory is gone (the test ended) or its test is (`GUARD`). Round 2 (verifier M-1): a writer that exits
# after its last byte can finish inside one 0.25 s wait slice on a fast disk, and the door makes no
# progress call after an exit, so whether the bound was seen depended on how fast the machine
# writes. Alive past the bound, the next slice sees it whatever the speed. A broken bound (mutant
# M5) is then a hang-detected RED, and the disk never holds more than 128 MiB.
RUNAWAY = r'''
import os, sys, time
home = os.path.dirname(os.path.abspath(__file__))
chunk = "x" * 65535 + "\n"
for i in range(2048):
    sys.stdout.write(chunk)
    if i % 64 == 0 and (not os.path.isdir(home) or _orphaned()):
        sys.exit(3)
sys.stdout.flush()
while os.path.isdir(home) and not _orphaned():
    time.sleep(0.05)
'''

MANY = r'''
import sys
w = sys.stdout.write
for i in range(2000000):
    w("line %d\n" % i)
w("THE LAST LINE\n")
'''


def _q(parts):
    return " ".join(shlex.quote(p) for p in parts) if POSIX else subprocess.list2cmdline(parts)


def joined(test, t):
    t.join(HANG_S)
    test.assertFalse(t.is_alive(), "the thread never finished (hang)")


def in_thread(fn, *a, **kw):
    box = {}

    def go():
        try:
            box["out"] = fn(*a, **kw)
        except BaseException as exc:          # noqa: BLE001 - the test inspects it
            box["exc"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t, box


def wait_until(test, pred, what):
    """Hang DETECTION for an event the suite or the door produces (a pid written, a report made)."""
    end = time.perf_counter() + HANG_S
    while not pred():
        if time.perf_counter() > end:
            test.fail(f"never happened (hang): {what}")
        time.sleep(0.01)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Suite(unittest.TestCase):
    """A tmp git repo initialized for mokata, and an aux dir OUTSIDE it for the suite's pid log, its
    gate and its step files (so the suite's own bookkeeping never moves the tree key)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        base = os.path.realpath(self._t.name)
        self.root = os.path.join(base, "repo")
        self.aux = os.path.join(base, "aux")
        os.makedirs(self.root)
        os.makedirs(self.aux)
        from mokata.init import init_repo
        init_repo(root=self.root, profile="minimal", assume_yes=True, out=lambda *_a: None)
        with open(os.path.join(self.root, "app.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        _git(self.root, "init", "-q")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.script = os.path.join(self.aux, "suite.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(_fake(SUITE))
        self.log = os.path.join(self.aux, "pids.log")
        self.gate = os.path.join(self.aux, "gate")
        self.steps = os.path.join(self.aux, "steps")
        os.makedirs(self.steps)
        self.addCleanup(self.release)          # never leave a suite blocked on its gate
        self._n = 0

    def cmd(self, rc=0, touch=None, steps=True, pipe=""):
        parts = [sys.executable, self.script, self.log, self.gate, str(rc),
                 self.steps if steps else "-"]
        if touch:
            parts.append(touch)
        return _q(parts) + pipe

    def release(self):
        with open(self.gate, "w", encoding="utf-8"):
            pass

    def step(self):
        self._n += 1
        with open(os.path.join(self.steps, f"step-{self._n}"), "w", encoding="utf-8"):
            pass

    def pids(self):
        try:
            with open(self.log, encoding="utf-8") as fh:
                return [ln for ln in fh.read().split() if ln]
        except FileNotFoundError:
            return []

    def started(self, n=1):
        wait_until(self, lambda: len(self.pids()) >= n, f"suite run #{n} started")


class _Mcp(_Suite):
    """`baseline` served through `_serve` in-process (the real tool body), registry and event log
    replaced by spies; the reply budget is the `budget` attribute, read per call."""

    def setUp(self):
        super().setUp()
        self.budget = 0.0
        self.events = []
        self._attached = {}
        self._lock = threading.Lock()
        real_join = MS._join_or_start

        def spy_join(*a, **kw):
            out = real_join(*a, **kw)
            ticket = a[6] if len(a) > 6 else kw.get("ticket")
            self._attach_event(ticket).set()
            return out
        for name, val in {"_spawn_registration": lambda _p: None,
                          "_emit_tool_call": self._event,
                          "_mcp_timeout_for": lambda _op, _path=".": self.budget,
                          "_join_or_start": spy_join}.items():
            p = mock.patch.object(MS, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.tool = MS._serve(TR.baseline, name="baseline", kind="read")
        self.addCleanup(self._drain)

    def _event(self, path, op, kind, result, elapsed_ms):
        with self._lock:
            self.events.append((op, result))

    def _attach_event(self, ticket):
        # Keyed by the TICKET, never `id(ticket)` (0.0.29 G round 2, verifier M-1): the dict's
        # strong reference keeps every ticket alive for the test, so a freed request's address can
        # never be reused by a later ticket and hand it an Event that is already set.
        with self._lock:
            return self._attached.setdefault(ticket, threading.Event())

    def _drain(self):
        self.release()
        with MS._INFLIGHT_LOCK:
            calls = list(MS._INFLIGHT.values())
        for c in calls:
            c.lever.pull()
            c.done.wait(HANG_S)
        with MS._INFLIGHT_LOCK:
            MS._INFLIGHT.clear()
            getattr(MS, "_TOMBSTONES", {}).clear()

    def call(self, cmd, budget=0.0, fmt="detailed"):
        self.budget = budget
        return self.tool(path=self.root, cmd=cmd, response_format=fmt)

    def the_call(self):
        with MS._INFLIGHT_LOCK:
            live = [c for c in MS._INFLIGHT.values() if c.op == "baseline"]
        self.assertEqual(1, len(live), "exactly one baseline call is held")
        return live[0]


class ReplyBudgetIsNotTheSuitesLife(_Mcp):
    """RED mode (note §5 row 1): the base's subprocess kill was the old knob
    `settings.mcp.test_timeout_seconds`. These cells set it to 1 s in the manifest and hold the
    suite until EITHER the run ended (the base: killed → call 2 is NOT RUN, an assertion, not a hang)
    OR the product itself reported the suite still alive and quiet for 2 s (the head: the knob is
    inert). Both are events the product produces; no interval decides the verdict."""

    OLD_KILL_S = 1

    def _outlive_the_old_kill(self):
        path = os.path.join(self.root, ".mokata", "manifest.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mcp"] = {"test_timeout_seconds": self.OLD_KILL_S}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def _held_past_the_old_kill(self):
        call = self.the_call()
        wait_until(self, lambda: call.done.is_set()
                   or (call.progress.get("quiet_s") or 0) >= 2 * self.OLD_KILL_S,
                   "the run ended, or reported itself alive past the old kill bound")

    def test_running_then_the_recall_returns_that_runs_green(self):
        """force — call 1 answers `running` (the suite is blocked on its gate) and the suite is
        still alive past the old knob's kill; the gate is released; the re-call joins and returns
        GREEN from the SAME run (one pid)."""
        self._outlive_the_old_kill()
        cmd = self.cmd(rc=0)
        first = self.call(cmd, budget=0.0)
        self.assertEqual("running", first["status"])
        self.assertEqual("baseline", first["operation"])
        self.started()
        self._held_past_the_old_kill()
        self.release()
        second = self.call(cmd, budget=HANG_S)
        self.assertIs(True, second.get("ok"), second)
        self.assertIn("GREEN", second["report"])
        self.assertEqual(1, len(self.pids()), "the suite ran once")

    def test_a_red_suite_answers_red_not_not_run(self):
        """force — rc 1 is RED, never NOT RUN, after a `running` answer and past the old kill."""
        self._outlive_the_old_kill()
        cmd = self.cmd(rc=1)
        self.assertEqual("running", self.call(cmd)["status"])
        self.started()
        self._held_past_the_old_kill()
        self.release()
        out = self.call(cmd, budget=HANG_S)
        self.assertIs(False, out.get("ok"))
        self.assertIn("RED", out["report"])
        self.assertNotIn("NOT RUN", out["report"])
        self.assertEqual(1, len(self.pids()))

    def test_the_door_gets_no_clock_and_the_progress_hook(self):
        """force — the run is handed to the door with `timeout=None` and a progress callback (on
        the base the reply budget was ALSO the subprocess kill: `timeout=120`)."""
        seen = {}
        real = Q.run_jobbed

        def spy(*a, **kw):
            seen.update(kw)
            return real(*a, **kw)
        with mock.patch.object(Q, "run_jobbed", spy):
            out = TR.baseline(self.root, cmd=_q([sys.executable, "-c", "pass"]),
                              response_format="detailed")
        self.assertIn("GREEN", out["report"])
        self.assertIsNone(seen.get("timeout", "absent"))
        self.assertTrue(callable(seen.get("progress")))
        self.assertIsInstance(seen.get("tail_bytes"), int, "a bounded tail, never the whole spool")


class ANewCallAfterAnEditIsANewRun(_Mcp):

    def _finished_told_running(self, cmd):
        self.assertEqual("running", self.call(cmd)["status"])
        self.started()
        call = self.the_call()
        self.release()
        self.assertTrue(call.done.wait(HANG_S), "the suite never finished")

    def test_unedited_tree_joins_the_finished_run(self):
        """guard (lane B's key) — no edit: the re-call collects that run, nothing is re-run."""
        cmd = self.cmd(rc=0)
        self._finished_told_running(cmd)
        out = self.call(cmd, budget=HANG_S)
        self.assertIn("GREEN", out["report"])
        self.assertEqual(1, len(self.pids()))

    def test_an_edit_between_calls_is_a_new_run(self):
        """guard (lane B's key) — a tracked file edited after the run: the re-call is a NEW run
        (two pids), never the old GREEN."""
        cmd = self.cmd(rc=0)
        self._finished_told_running(cmd)
        with open(os.path.join(self.root, "app.py"), "a", encoding="utf-8") as fh:
            fh.write("y = 2\n")
        out = self.call(cmd, budget=HANG_S)          # gate already exists: the new run exits at once
        self.assertIn("GREEN", out["report"])
        self.assertEqual(2, len(self.pids()), "an edited tree must be tested again")
        self.assertNotIn("kept", out)


class ATreeThatMovedIsSaid(_Mcp):

    def test_a_run_that_changed_the_tree_is_labelled(self):
        """guard (lane B's `tree_changed` = the note's `changed_since_start`) — the tree moved while
        it ran: the result says it describes the tree at the start."""
        cmd = self.cmd(rc=0, touch=os.path.join(self.root, "app.py"))
        self.assertEqual("running", self.call(cmd)["status"])
        self.started()
        call = self.the_call()
        self.release()
        self.assertTrue(call.done.wait(HANG_S))
        out = self.call(cmd, budget=HANG_S)
        self.assertIn("GREEN", out["report"])
        self.assertIs(True, out.get("tree_changed", {}).get("changed_while_running"), out)
        self.assertIn("as it was when the run started", out["tree_changed"]["note"])


class _Policy(_Suite):
    """`baseline_status` itself on a thread, with an injected clock and a progress sink the test
    reads (lane B's `knowledge.cancel.reporting`)."""

    def setUp(self):
        super().setUp()
        self.now = [0.0]
        self.reports = []
        self.cv = threading.Condition()

    def sink(self, **kw):
        with self.cv:
            self.reports.append((self.now[0], dict(kw)))
            self.cv.notify_all()

    def run_status(self, cmd, silence=600.0, **kw):
        def go():
            with C.reporting(self.sink):
                return B.baseline_status(cmd, cwd=self.root, silence=silence,
                                         clock=lambda: self.now[0], **kw)
        self._run = in_thread(go)
        return self._run

    def started(self, n=1):
        """The suite wrote its pid — or the policy call already ENDED, which is said as itself
        (an assertion naming what it returned or raised), never left to hang detection."""
        t, box = self._run
        wait_until(self, lambda: len(self.pids()) >= n or not t.is_alive(),
                   f"suite run #{n} started")
        if len(self.pids()) < n:
            self.fail(f"the baseline call ended before the suite started: {box}")

    def report_where(self, pred, what):
        """Wait for the policy to have REPORTED a state that satisfies `pred` (hang detection)."""
        end = time.perf_counter() + HANG_S
        with self.cv:
            while not any(pred(t, r) for t, r in self.reports):
                left = end - time.perf_counter()
                if left <= 0:
                    self.fail(f"the policy never reported: {what}")
                self.cv.wait(left)

    def at(self, t):
        """Move the injected clock and return once the policy has evaluated it TWICE (two slices):
        a raise in the first evaluation would have ended the run before the second."""
        with self.cv:
            mark = len(self.reports)
            self.now[0] = t
        self.report_where(lambda _t, _r: len([1 for x in self.reports[mark:] if x[0] == t]) >= 2,
                          f"two evaluations at t={t}")


class SilenceIsTheReposKnob(_Policy):

    def test_no_output_for_the_bound_is_not_run_naming_the_knob_and_both_roads(self):
        """force — a blocked, silent suite at quiet == silence is NOT RUN (`silent`), and the text
        names the setting, its value, how to change it and the record-it-yourself road."""
        t, box = self.run_status(self.cmd(steps=False))
        self.started()
        self.at(599.0)
        self.assertTrue(t.is_alive(), "ended before the silence bound")
        self.now[0] = 600.0
        joined(self, t)
        res = box["out"]
        self.assertEqual(B.NOT_RUN, res.state)
        self.assertEqual("silent", res.ending)
        text = res.render()
        from mokata.awaiting import TEST_RUN_RED_CMD
        for needle in ("settings.baseline.silence_seconds", "now 600", "wrote nothing for 600 s",
                       "mokata config set settings.baseline.silence_seconds", "0 = never",
                       "quiet by design", TEST_RUN_RED_CMD, "--passed"):
            self.assertIn(needle, text)
        self.assertFalse(res.ok)

    def test_growth_resets_the_quiet_clock(self):
        """force — output at t=500 moves the quiet start: t=1000 is only 500 s quiet (alive); 1100 is
        600 s quiet (ended)."""
        t, box = self.run_status(self.cmd())
        self.started()
        self.at(500.0)
        self.step()
        self.report_where(lambda _t, r: (r.get("done") or 0) > 0, "growth seen")
        self.at(1000.0)
        self.assertTrue(t.is_alive(), "a suite that wrote 500 s ago was ended")
        quiet = [r["quiet_s"] for tt, r in self.reports if tt == 1000.0]
        self.assertEqual(500.0, quiet[-1])
        self.now[0] = 1100.0
        joined(self, t)
        self.assertEqual("silent", box["out"].ending)

    def test_zero_never_ends_on_silence(self):
        """force — `silence_seconds: 0`: any quiet length runs on; the suite's own exit decides."""
        t, box = self.run_status(self.cmd(steps=False), silence=0.0)
        self.started()
        self.at(10.0 ** 9)
        self.assertTrue(t.is_alive())
        self.release()
        joined(self, t)
        self.assertEqual(B.GREEN, box["out"].state)

    def test_the_setting_is_read_and_a_bad_value_is_the_default(self):
        """force — `settings.baseline.silence_seconds` from the manifest; bad values never fatal."""
        class M:
            def __init__(self, v):
                self.v = v

            def setting(self, key, default=None):
                return {"silence_seconds": self.v} if key == "baseline" else default
        self.assertEqual(600.0, B.BASELINE_SILENCE_SECONDS)
        self.assertEqual(600.0, B.silence_seconds(None))
        for good, want in ((0, 0.0), (300, 300.0), ("120", 120.0), (1.5, 1.5)):
            self.assertEqual(want, B.silence_seconds(M(good)), good)
        for bad in ("soon", -5, True, False, None, float("nan"), float("inf"), [], {}):
            self.assertEqual(600.0, B.silence_seconds(M(bad)), bad)

    def test_both_callers_pass_the_repos_knob(self):
        """force — the CLI `mokata baseline` and the MCP tool both hand `baseline_status` the repo's
        `silence_seconds`."""
        import argparse
        from mokata.cli_commands import diagnostics as D
        from mokata.config_cmd import config_set
        res = config_set(self.root, "settings.baseline.silence_seconds", "321", assume_yes=True)
        self.assertTrue(getattr(res, "ok", True))
        seen = []

        def fake(command, cwd=None, **kw):
            seen.append(kw.get("silence"))
            return B.BaselineResult(state=B.GREEN, command=command, returncode=0)
        with mock.patch.object(B, "baseline_status", side_effect=fake):
            TR.baseline(self.root, cmd="true")
            with mock.patch("builtins.print"):
                D.cmd_baseline(argparse.Namespace(path=self.root, cmd="true"))
        self.assertEqual([321.0, 321.0], seen)


class PipedSuitesGoTheWayTheyGo(_Policy):

    @unittest.skipUnless(POSIX, "POSIX pipes (`cat`, `tail`); the Windows `cmd` pipe is a mirror cell")
    def test_a_streaming_pipe_stays_alive(self):
        """guard (pins the limitation's good half; red on the base only through the absent `clock=`
        seam) — `| cat` passes output through as it comes."""
        t, box = self.run_status(self.cmd(pipe=" | cat"))
        self.started()
        self.at(100.0)
        self.step()
        self.report_where(lambda _t, r: (r.get("done") or 0) > 0, "growth through the pipe")
        self.at(699.0)
        self.assertTrue(t.is_alive(), "a suite streaming through `cat` was ended as stalled")
        self.release()
        joined(self, t)
        self.assertEqual(B.GREEN, box["out"].state)

    @unittest.skipUnless(POSIX, "POSIX pipes (`cat`, `tail`); the Windows `cmd` pipe is a mirror cell")
    def test_a_tail_pipe_is_ended_as_stalled_with_the_quiet_by_design_remedy(self):
        """force — `| tail -n 1` writes nothing until EOF: it is ended at the bound, and the text
        names that case and the knob (the limitation is pinned, not accidental)."""
        t, box = self.run_status(self.cmd(pipe=" | tail -n 1"))
        self.started()
        self.step()
        self.at(599.0)
        self.assertTrue(all((r.get("done") or 0) == 0 for _t, r in self.reports),
                        "tail wrote before EOF")
        self.now[0] = 600.0
        joined(self, t)
        res = box["out"]
        self.assertEqual("silent", res.ending)
        self.assertIn("piped through `tail`/`grep`", res.render())


class RunawayOutputIsBoundedOnDisk(_Policy):

    def test_a_runaway_writer_is_stopped_at_the_derived_bound(self):
        """force — free space injected at 256 MiB: the bound is a quarter (64 MiB); a writer that
        never stops is ended there and says why; nothing past the bound is read into memory."""
        script = os.path.join(self.aux, "runaway.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_fake(RUNAWAY))
        usage = mock.Mock(return_value=mock.Mock(free=256 * 1024 ** 2))
        t, box = self.run_status(_q([sys.executable, script]), disk_usage=usage)
        joined(self, t)
        res = box["out"]
        self.assertEqual(B.NOT_RUN, res.state, res.render())
        self.assertEqual("spooled", res.ending)
        text = res.render()
        self.assertIn("64.0 MiB", text)
        self.assertIn("a quarter of the free space", text)
        self.assertIn("runaway", text)
        done = max((r.get("done") or 0) for _t, r in self.reports)
        self.assertGreater(done, 64 * 1024 ** 2)

    def test_the_bound_is_derived_and_capped(self):
        """force — `min(16 GiB, free // 4)`; an unreadable volume gets the fixed fallback."""
        self.assertEqual(100, B.spool_bound(disk_usage=lambda _p: mock.Mock(free=400)))
        self.assertEqual(16 * 1024 ** 3,
                         B.spool_bound(disk_usage=lambda _p: mock.Mock(free=10 ** 15)))

        def boom(_p):
            raise OSError("no such volume")
        self.assertEqual(B.SPOOL_FALLBACK_BYTES, B.spool_bound(disk_usage=boom))


class AStoppedSuiteSaysSo(_Mcp):

    def test_a_told_running_suite_stopped_through_a_cancelled_recall(self):
        """guard (lane B's road; G's half is the answer) — after `running`, a re-call attaches and
        its cancel stops the suite: the run ends `cancelled` claiming nothing (never GREEN/RED), and
        the next identical call is a NEW run."""
        cmd = self.cmd(steps=False)
        self.assertEqual("running", self.call(cmd)["status"])
        self.started()
        call = self.the_call()
        self.budget = HANG_S
        ticket = MS._Ticket()
        t, box = in_thread(MS._with_ticket, ticket, self.tool, path=self.root, cmd=cmd,
                           response_format="detailed")
        self.assertTrue(self._attach_event(ticket).wait(HANG_S), "the re-call never attached")
        self.assertIs(call, ticket.call)
        ticket.cancel()
        joined(self, t)
        self.assertIsNone(box.get("out"), "a cancelled request answers nothing")
        self.assertTrue(call.done.wait(HANG_S), "the suite was never stopped")
        self.assertEqual("cancelled", call.out.get("reason_code"))
        self.assertIs(False, call.out.get("committed"))
        self.assertNotIn("report", call.out)
        self.release()
        out = self.call(cmd, budget=HANG_S)
        self.assertIn("GREEN", out["report"])
        self.assertEqual(2, len(self.pids()), "a stopped run is never re-served")


class RunningSaysProgressNotArgs(_Mcp):

    def test_running_carries_numeric_progress_and_never_the_command(self):
        """force — the `running` answer carries the suite's bytes so far; never a note, the
        arguments or a path."""
        canary = os.path.join(self.aux, "CANARY-SECRET-PATH")
        os.makedirs(canary)
        script = os.path.join(canary, "suite.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_fake(SUITE))
        cmd = _q([sys.executable, script, self.log, self.gate, "0", self.steps])
        self.assertEqual("running", self.call(cmd)["status"])
        self.started()
        call = self.the_call()
        self.step()
        wait_until(self, lambda: (call.progress.get("done") or 0) > 0, "progress reported")
        out = self.call(cmd)
        self.assertEqual("running", out["status"])
        self.assertGreater(out["progress"]["done"], 0)
        self.assertEqual("bytes of test output written", out["progress"]["unit"])
        self.assertIn("quiet_s", out["progress"])
        blob = json.dumps(out)
        self.assertNotIn("CANARY-SECRET-PATH", blob)
        self.assertNotIn("note", out["progress"])

    def test_the_render_is_an_allow_list(self):
        """force — a progress dict carrying a note/path renders only numbers and a short unit."""
        out = MS._still_running("baseline", 60, 61, "c",
                                {"done": 5, "total": "x", "note": "/secret/path", "unit": "u" * 99,
                                 "quiet_s": True})
        self.assertEqual({"done": 5, "unit": "u" * 40}, out["progress"])
        self.assertNotIn("/secret/path", json.dumps(out))
        self.assertNotIn("progress", MS._still_running("spec_check", 60, 61, "c", {}))


class TheHintIsTrue(unittest.TestCase):

    def test_the_hint_is_true_for_every_tool(self):
        """force — the old hint said only a WRITE could run twice via the CLI; the new one is true
        for every tool, and `baseline` adds how it ends and how it is stopped."""
        for op in ("spec_check", "baseline", "remember"):
            hint = MS._still_running(op, 60, 61, "c")["hint"]
            self.assertIn("never started twice", hint)
            self.assertIn("starts a second run", hint)
            self.assertNotIn("WOULD run it twice", hint)
        b = MS._still_running("baseline", 60, 61, "c")["hint"]
        for needle in ("no wall clock", "settings.baseline.silence_seconds", "cancel",
                       "end this MCP server"):
            self.assertIn(needle, b)
        self.assertNotIn("silence_seconds", MS._still_running("spec_check", 60, 61, "c")["hint"])

    def test_liveness_lines_say_the_same(self):
        """force — doctor's liveness line: one reply budget; `baseline` has no wall clock."""
        from mokata.awaiting import liveness_lines
        text = "\n".join(liveness_lines())
        self.assertIn("answers within 60s", text)
        self.assertIn("no wall clock", text)
        self.assertIn("settings.baseline.silence_seconds", text)
        self.assertNotIn("for `baseline` (it runs the test suite)", text)


class TheOldKnobIsGoneAndSaid(_Suite):

    OLD = ("mcp_test_timeout", "BASELINE_MCP_TIMEOUT_SECONDS", "MCP_TEST_TIMEOUT_SETTING",
           "_manifest_or_none", "BASELINE_TIMEOUT_SECONDS")

    def test_no_old_name_in_src(self):
        """force — the deleted names are gone from the product."""
        # CORPUS: THE WORKING TREE. "Is a deleted name still in the product?" — the product that
        # ships and runs is src/ on disk (rsynced to the mirror), so an untracked file counts.
        hits = []
        for dirpath, _dirs, files in os.walk(os.path.join(SRC, "mokata")):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    hits += [f"{f}:{n}" for n in self.OLD if n in text]
        self.assertEqual([], hits)

    def test_the_old_key_moves_nothing_and_doctor_names_it(self):
        """force — `settings.mcp.test_timeout_seconds` left in a manifest: the reply budget is the
        one budget, and doctor says the key is no longer read and which knob replaced it."""
        from mokata.config import Surface
        from mokata.govern.doctor import unused_settings_findings
        path = os.path.join(self.root, ".mokata", "manifest.json")
        self.assertEqual([], unused_settings_findings(Surface.load(self.root)))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mcp"] = {"test_timeout_seconds": 5}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertEqual(MS.MCP_SURFACE_TIMEOUT_SECONDS, MS._mcp_timeout_for("baseline", self.root))
        found = unused_settings_findings(Surface.load(self.root))
        self.assertEqual(1, len(found))
        for needle in ("settings.mcp.test_timeout_seconds", "no longer read",
                       "settings.baseline.silence_seconds", ".mokata/manifest.json"):
            self.assertIn(needle, found[0].detail)


class DoctorSaysARefusedBridgeRecord(_Suite):

    def test_a_refused_ide_bridge_record_is_a_line_never_a_traceback(self):
        """force (D9, carried for lane H) — `read_bridge` refusing its record (`RecordRefused`, H4)
        is a doctor finding carrying the refusal's own reason, never an exception out of doctor."""
        from mokata import harness_setup as HS
        from mokata.config import Surface
        from mokata.errors import RecordRefused
        from mokata.govern.doctor import http_gate_findings
        from mokata.ide import bus

        def refused(root):
            raise RecordRefused(bus.bridge_path(root), "not private (fix: mokata doctor --fix)")
        with mock.patch.object(HS, "http_gates_wired", return_value=True), \
                mock.patch.object(bus, "read_bridge", refused):
            found = http_gate_findings(Surface.load(self.root), listening=lambda _p: False)
        self.assertEqual(1, len(found))
        self.assertIn("IDE bridge record:", found[0].detail)
        self.assertIn("not private (fix: mokata doctor --fix)", found[0].detail)

    def test_any_other_error_from_the_read_is_not_called_a_refusal(self):
        """H-a merge: the catch is NARROW — only `RecordRefused` is a refusal; another error from
        the read is a real one and propagates (it is never reported as "refused")."""
        from mokata import harness_setup as HS
        from mokata.config import Surface
        from mokata.errors import MokataError
        from mokata.govern.doctor import http_gate_findings
        from mokata.ide import bus

        def broken(_root):
            raise MokataError("something else broke")
        with mock.patch.object(HS, "http_gates_wired", return_value=True), \
                mock.patch.object(bus, "read_bridge", broken), \
                self.assertRaises(MokataError):
            http_gate_findings(Surface.load(self.root), listening=lambda _p: False)

    def test_plain_mokata_doctor_names_a_refused_ide_record(self):
        """force (round 2, lane H-a's reader sweep: `doctor.py:987` on the base) — the real CLI
        `mokata doctor`, with every IDE record it reads refused by the private reader (as lane H's
        `read_private` RAISES `RecordRefused` for one): rc
        unchanged, the refusal is a warning line, never a traceback and never silence. Records
        other modules read are passed through untouched: their guards are lane H's."""
        import contextlib
        import io
        from mokata import cli
        from mokata import harness_setup as HS
        from mokata import winacl
        from mokata.errors import RecordRefused
        real = winacl.read_private
        refused = []

        def refuse_ide_records(path, *a, **kw):
            if os.path.basename(path) not in ("bridge.json", "server.json"):
                return real(path, *a, **kw)
            refused.append(os.path.basename(path))
            raise RecordRefused(path, "the record is not private (fix: mokata doctor --fix)")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(HS, "http_gates_wired", return_value=True), \
                mock.patch.object(winacl, "read_private", refuse_ide_records), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["doctor", "--path", self.root])
        self.assertIn("bridge.json", refused, "doctor never read the IDE record (the cell is void)")
        self.assertIsInstance(rc, int)
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("Traceback", text)
        self.assertIn("ide-bridge-record-refused", text)
        self.assertIn("IDE bridge record:", text)
        self.assertIn("refused (the record is not private", text)


class NoEnvLeak(_Suite):

    def _env_seen(self, environ):
        seen = {}
        real = Q.run_jobbed

        def spy(*a, **kw):
            seen.update(kw.get("env") or {})
            return real(*a, **kw)
        with mock.patch.object(Q, "run_jobbed", spy):
            B.baseline_status(_q([sys.executable, "-c", "pass"]), cwd=self.root, environ=environ)
        return seen

    def test_unbuffered_is_added_only_when_absent_and_never_leaks(self):
        """force — `PYTHONUNBUFFERED=1` reaches the child when the caller has none (or an empty
        one); a user's value is kept; the caller's environment is unchanged."""
        before = dict(os.environ)
        base = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        self.assertEqual("1", self._env_seen(dict(base)).get("PYTHONUNBUFFERED"))
        self.assertEqual("1", self._env_seen(dict(base, PYTHONUNBUFFERED="")).get(
            "PYTHONUNBUFFERED"))
        self.assertEqual("0", self._env_seen(dict(base, PYTHONUNBUFFERED="0")).get(
            "PYTHONUNBUFFERED"))
        mine = dict(base)
        self._env_seen(mine)
        self.assertNotIn("PYTHONUNBUFFERED", mine)
        self.assertEqual(before, dict(os.environ))


class NulRendersAsNotRun(_Suite):

    def test_a_nul_in_the_command_is_not_run(self):
        """guard (the door is lane B's) — a NUL byte: NOT RUN "could not run", never a crash."""
        res = B.baseline_status("echo a\x00b", cwd=self.root)
        self.assertEqual(B.NOT_RUN, res.state)
        self.assertIn("could not run the test command", res.render())

    def test_two_million_lines_render_their_tail(self):
        """guard (scale) — a 2·10^6-line suite: GREEN, and the report's tail is the real last line
        (the door returns a bounded tail, not the whole spool)."""
        script = os.path.join(self.aux, "many.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(MANY)
        res = B.baseline_status(_q([sys.executable, script]), cwd=self.root)
        self.assertEqual(B.GREEN, res.state)
        self.assertTrue(res.detail.endswith("THE LAST LINE"), res.detail[-80:])
        self.assertLessEqual(len(res.detail), 500)


# A stand-in for a test process (argv: template fake mode wfd args…): writes the fake from the
# template with ITS OWN pid as the owner, starts it in a new session (as the door does, so killing
# this process does not signal the fake) with stdout on the inherited fd, says the fake's pid, then
# waits to be killed. `pipe` runs it as `fake | cat` under `sh`, the parent that does NOT die.
PARENT = r"""
import os, shlex, subprocess, sys
tpl, fake, mode, wfd = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
with open(tpl, encoding="utf-8") as fh:
    src = fh.read().replace("__OWNER__", str(os.getpid()))
with open(fake, "w", encoding="utf-8") as fh:
    fh.write(src)
argv = [sys.executable, fake] + sys.argv[5:]
kw = dict(stdout=wfd, stdin=subprocess.DEVNULL, pass_fds=(wfd,), start_new_session=True,
          env=dict(os.environ, PYTHONUNBUFFERED="1"))          # as the door runs a suite
if mode == "pipe":
    p = subprocess.Popen(" ".join(shlex.quote(a) for a in argv) + " | cat", shell=True, **kw)
else:
    p = subprocess.Popen(argv, **kw)
os.close(wfd)
sys.stdout.write("%d\n" % p.pid)
sys.stdout.flush()
sys.stdin.read()
"""


@unittest.skipUnless(POSIX, "a SIGKILLed owner; on Windows the door's job object ends the tree with it")
class AFakeDiesWithItsTest(unittest.TestCase):
    """Round-2 re-verify M-V1 — a fake whose test is SIGKILLed (its temp dir left behind, its gate
    never made) exits on its own. The exit is observed as an EVENT: EOF on the pipe that is the
    fake's stdout, which only the fake (and its `cat`) hold once the killed parent is gone. No
    interval decides the verdict; `MOKATA_TEST_HANG_S` only detects a hang."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()      # removed only AFTER the verdict (and frees a
        self.addCleanup(self._t.cleanup)            # survivor: its home is then gone)
        self.d = os.path.realpath(self._t.name)

    def _killed_owner_leaves_no_fake(self, body, mode, *args):
        tpl = os.path.join(self.d, "template.py")
        with open(tpl, "w", encoding="utf-8") as fh:
            fh.write(GUARD + body)
        parent_py = os.path.join(self.d, "parent.py")
        with open(parent_py, "w", encoding="utf-8") as fh:
            fh.write(PARENT)
        r, w = os.pipe()
        parent = subprocess.Popen([sys.executable, parent_py, tpl, os.path.join(self.d, "fake.py"),
                                   mode, str(w), *args],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, pass_fds=(w,))
        os.close(w)

        def reap():
            if parent.poll() is None:
                parent.kill()
            parent.wait()
            for fh in (parent.stdin, parent.stdout):
                fh.close()
        self.addCleanup(reap)
        self.assertTrue(parent.stdout.readline().strip().isdigit(), "the fake never started")
        first, eof = threading.Event(), threading.Event()

        def drain():
            try:
                while True:
                    chunk = os.read(r, 1 << 16)
                    if not chunk:
                        break
                    first.set()
            finally:
                os.close(r)
                eof.set()
        threading.Thread(target=drain, daemon=True).start()
        self.assertTrue(first.wait(HANG_S), "the fake never wrote (hang)")   # it is running
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        self.assertTrue(eof.wait(HANG_S), f"the fake ({mode}) outlived its killed test (hang)")
        self.assertTrue(os.path.isdir(self.d), "the verdict must not come from the temp dir")

    def _suite_args(self):
        steps = os.path.join(self.d, "steps")
        os.makedirs(steps)
        with open(os.path.join(steps, "step-1"), "w", encoding="utf-8"):
            pass                                     # it writes one line at once: "running"
        return (os.path.join(self.d, "pids.log"), os.path.join(self.d, "gate-never"), "0", steps)

    def test_the_fake_suite_exits_when_its_parent_is_killed(self):
        """force — run directly (the door's `sh -c` execs it): the parent it started with dies."""
        self._killed_owner_leaves_no_fake(SUITE, "direct", *self._suite_args())

    def test_the_fake_suite_exits_when_its_test_is_killed_behind_a_pipe(self):
        """force — `| cat`: its parent is `sh`, which lives on; the stamped owner is what died."""
        self._killed_owner_leaves_no_fake(SUITE, "pipe", *self._suite_args())

    def test_the_runaway_writer_exits_when_its_parent_is_killed(self):
        """force — the 128 MiB writer, past its last byte and waiting: it exits, spool released."""
        self._killed_owner_leaves_no_fake(RUNAWAY, "direct")

    def test_the_runaway_writer_exits_when_its_test_is_killed_behind_a_pipe(self):
        """force — the same behind `| cat`."""
        self._killed_owner_leaves_no_fake(RUNAWAY, "pipe")


class AnAttachEventIsNeverInherited(_Mcp):
    """Round-2 L (re-verify V3): the attach Events are keyed by the ticket, so a freed request's
    address can never hand a later ticket an Event that is already set."""

    def test_a_reused_address_never_inherits_a_set_event(self):
        """guard (pins round 2's M-1 fix) — the helper holds the ticket itself (so its address
        cannot be reused while the test runs), and no fresh ticket, at any address, starts with a
        set Event."""
        first = MS._Ticket()
        addr = id(first)
        self._attach_event(first).set()
        self.assertTrue(any(k is first for k in self._attached), "the helper must hold the ticket")
        del first
        fresh = []
        for _ in range(10000):
            t = MS._Ticket()
            fresh.append(t)
            self.assertFalse(self._attach_event(t).is_set(), "a new ticket inherited a set Event")
            if id(t) == addr:
                break


class CiCheckExitCodeIsTheClis(unittest.TestCase):

    def test_mcp_exit_code_equals_the_cli_return_for_pass_block_absent(self):
        """guard (an ungraded field) — the MCP `ci_check` `exit_code` is the CLI's return code."""
        import argparse
        from mokata import ci_check as CI
        from mokata.cli_commands import knowledge as K
        cases = {
            "pass": [CI.CheckLeg("completeness", "pass", "ok")],
            "block": [CI.CheckLeg("completeness", "block", "missing")],
            "absent": [CI.CheckLeg("completeness", "absent", "nothing")],
        }
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        got = {}
        for name, legs in cases.items():
            res = CI.CICheckResult(legs=legs, initialized=True)
            with mock.patch.object(CI, "run_ci_check", return_value=res):
                mcp = TR.ci_check(d.name, files="a.py")
                args = argparse.Namespace(path=d.name, files="a.py", base=None, symbols=None,
                                          comment_file=None, evidence_file=None, ascii=True,
                                          link_sha="x", link_base="", no_fail=False)
                with mock.patch("builtins.print"):
                    cli = K.cmd_ci_check(args)
            self.assertEqual(cli, mcp["exit_code"], name)
            got[name] = cli
        self.assertEqual({"pass": 0, "block": 1, "absent": 0}, got)


if __name__ == "__main__":
    unittest.main()
