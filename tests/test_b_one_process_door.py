"""0.0.28 lane B — ONE process door (`quiet_spawn.spawn_jobbed` / `run_jobbed` / `Jobbed`).

The user-visible rules (design note `0028-design/B-windows-process-runner.md` §1):
  * the gated merge's suite run ends when the suite ends: nothing it started survives it, holds the
    worktree, or keeps it alive by writing output (`WIN-MERGE-SUITE-NO-JOB`);
  * mokata never ends a process it did not start — no pid is ever chosen by enumeration, there is
    no pid-walk fallback (`WIN-TREEWATCH-PID-REUSE`, deleted as a class: Jas D2);
  * on Windows a test runner is a real `.exe`/`.com` however its name is spelled, or the panel says
    why not BEFORE the click; a test that never ran is never recorded RED (`WIN-RUNNER-LOWS`);
  * a language server present only as a `.cmd`/`.bat`/`.ps1` is "found and not run, and why";
  * a run that did not happen never reads as ok (`baseline` NOT_RUN, refute H1).

The matrix rows of §5 are named on each class. Cells marked **probe** in the note run only on real
Windows (`skipUnless(os.name == "nt")`) and are counted only from a mirror run id; "inj" cells run
the Windows code path with an injected kernel32 and prove the logic, never the OS.

Business-level: every "fake" is a REAL child process (`sys.executable`) with an injected seam; no
fake pid ever reaches a kill path. Waits follow state, returning the moment it holds, bounded at
`GONE_S` (>= 4x slack) — never a short wall clock.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import quiet_spawn as Q

NT = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
#: How long a process gets to be seen gone / a bounded call gets past its own bound.
GONE_S = 30.0


# ============================================================================== helpers
def _alive(pid):
    """Look-only liveness (never `os.kill(pid, 0)` on Windows: that is TerminateProcess there)."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x1000, False, int(pid))             # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259                              # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    else:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _wait(pred, within=GONE_S, step=0.05):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def _gone(*pids):
    return _wait(lambda: not any(_alive(p) for p in pids))


def _tmp(case, prefix="mk-door-"):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(shutil.rmtree, d, True)
    return d


def _script(d, name, body):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _read_pids(path):
    with open(path, encoding="utf-8") as fh:
        return [int(x) for x in fh.read().split()]


def _cleanup_pids(case, pidfile):
    """A test that leaves a grandchild on purpose (the "already reaped" cell) ends it through the
    door's pid signal — never a raw kill — when it is done."""
    def end():
        import signal
        try:
            for pid in _read_pids(pidfile):
                if _alive(pid):
                    Q.signal_pid(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (OSError, ValueError):
            pass
    case.addCleanup(end)


#: A launcher that starts one grandchild (same group / job), records both pids, and then either
#: exits (`linger=0`) or keeps running; the grandchild sleeps, prints (`chatty`) or holds a file.
LAUNCHER = """\
import os, subprocess, sys, time
pidfile, linger, mode = sys.argv[1], float(sys.argv[2]), sys.argv[3]
child = {
    "sleep": "import time; time.sleep(600)",
    "chatty": "import sys, time\\nwhile True:\\n    print('tick', flush=True); time.sleep(0.3)",
    "hold": "import os, sys, time; os.chdir(sys.argv[1]); f = open('held.txt', 'w'); time.sleep(600)",
}[mode]
args = [sys.executable, "-c", child] + sys.argv[4:]
gc = subprocess.Popen(args, stdout=None if mode == "chatty" else subprocess.DEVNULL)
with open(pidfile + ".tmp", "w") as fh:
    fh.write("%d %d" % (os.getpid(), gc.pid))
os.replace(pidfile + ".tmp", pidfile)
if mode == "hold":
    time.sleep(0.5)
time.sleep(linger)
sys.exit(0)
"""


class _Kernel32:
    """An injected kernel32 for the Windows code path off Windows. It holds a REAL child (`proc`)
    so TerminateJobObject really ends it (through the Popen handle, never a pid the fake chose);
    `fail` names a call to refuse; `stuck` keeps the accounting non-empty."""

    last_error = "WinError 5: Access is denied."

    def __init__(self, fail=None, stuck=False):
        self.fail, self.stuck = fail, stuck
        self.calls = []
        self.proc = None

    def CreateJobObjectW(self, *a):
        self.calls.append("CreateJobObjectW")
        return 0 if self.fail == "CreateJobObjectW" else 4242

    def SetInformationJobObject(self, job, cls, info, size):
        self.calls.append(("SetInformationJobObject", job, cls,
                           info._obj.BasicLimitInformation.LimitFlags, size))
        return 0 if self.fail == "SetInformationJobObject" else 1

    def AssignProcessToJobObject(self, job, handle):
        self.calls.append(("AssignProcessToJobObject", job, handle))
        return 0 if self.fail == "AssignProcessToJobObject" else 1

    def QueryInformationJobObject(self, job, cls, info, size, ret):
        self.calls.append(("QueryInformationJobObject", cls))
        alive = self.proc is not None and self.proc.poll() is None
        info._obj.ActiveProcesses = 1 if (self.stuck or alive) else 0
        info._obj.TotalProcesses = 1
        return 1

    def TerminateJobObject(self, job, code):
        self.calls.append(("TerminateJobObject", job))
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        return 1

    def CloseHandle(self, h):
        self.calls.append(("CloseHandle", h))
        return 1


def _inj_spawn(k32, argv, **kw):
    """The Windows path (`nt=True`) on any OS, with a real child and an injected kernel32."""
    def resume(proc):
        k32.proc = proc
    return Q.spawn_jobbed(argv, nt=True, _kernel32=k32, _resume=resume, **kw)


# ======================================================================= rows 1, 2, 42 · merge
from test_p21_gated_merge import _Merge  # noqa: E402


class MergeSuiteEndsItsTree(_Merge):
    """Rows 1, 2, 42. The gated merge's suite run ends when the suite ends."""

    def suite(self, cmd_tail):
        mf = os.path.join(self.main, ".mokata", "manifest.json")
        with open(mf, encoding="utf-8") as fh:
            data = json.load(fh)
        data["settings"]["baseline"]["test_command"] = f'"{sys.executable}" {cmd_tail}' if NT \
            else f"{sys.executable} {cmd_tail}"
        with open(mf, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def launcher(self, mode, linger=0, *extra):
        d = _tmp(self)
        script = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        self.suite(" ".join([script, pidfile, str(linger), mode, *extra]))
        return pidfile

    def test_a_chatty_grandchild_does_not_keep_the_run_alive_and_dies_with_it(self):
        from mokata import merge_suite as MS
        pidfile = self.launcher("chatty")
        _cleanup_pids(self, pidfile)
        wt = self.session()
        got = {}
        # On a daemon thread with a bounded join: a door that fails to end the leftover leaves
        # the run reading its output forever, and that must be a red, not a hung suite.
        th = threading.Thread(target=lambda: got.update(res=MS.run_full_suite(self.main, wt)),
                              daemon=True)
        th.start()
        th.join(GONE_S * 2)
        self.assertFalse(th.is_alive(), "the chatty leftover kept the run alive")
        self.assertTrue(got["res"]["passed"], got)
        self.assertTrue(_gone(*_read_pids(pidfile)), "a process the suite started outlived it")

    def test_a_quiet_grandchild_holding_the_worktree_is_gone_when_the_run_returns(self):
        from mokata import merge_suite as MS
        wt = self.session()
        hold = os.path.join(wt, "hold")
        os.makedirs(hold)
        pidfile = self.launcher("hold", 0, hold)
        res = MS.run_full_suite(self.main, wt)
        self.assertTrue(res["passed"], res)

        def removable():
            try:
                if os.path.exists(os.path.join(hold, "held.txt")):
                    os.remove(os.path.join(hold, "held.txt"))
                os.rmdir(hold)
                return True
            except OSError:
                return False
        self.assertTrue(_wait(removable), "a leftover still holds the worktree")
        self.assertTrue(_gone(*_read_pids(pidfile)))

    def test_huge_output_is_held_as_a_tail_and_the_run_ends_with_the_suite(self):
        from mokata import merge_suite as MS
        d = _tmp(self)
        mb = 200
        script = _script(d, "flood.py", (
            "import sys\nchunk = b'x' * (1 << 20)\n"
            f"for _ in range({mb}):\n    sys.stdout.buffer.write(chunk)\n"))
        self.suite(script)
        wt = self.session()
        res = MS.run_full_suite(self.main, wt)
        self.assertTrue(res["passed"], res)
        self.assertEqual(mb << 20, res["output_bytes"])
        self.assertLessEqual(len(res["output"].encode("utf-8")), MS.OUTPUT_TAIL)

    def test_a_tree_that_cannot_be_emptied_is_reported_not_counted(self):
        """Row 3 at the merge: the door's `end` said the tree is not gone — the merge says so and
        records nothing (a run whose processes may hold the worktree is no result)."""
        from mokata import merge_suite as MS
        wt = self.session()
        real_end = Q.Jobbed.end

        def stuck(tree, leftovers_only=False):
            real_end(tree, leftovers_only)
            tree.last_end = Q.Ended(False, False, "2 process(es) mokata started were still running")
            return tree.last_end
        with mock.patch.object(Q.Jobbed, "end", stuck):
            with self.assertRaises(MS.SuiteError) as caught:
                MS.run_full_suite(self.main, wt)
        self.assertIn("still running", str(caught.exception))
        self.assertIn("not counted", str(caught.exception))


# ============================================================================ row 3 · the bound
class EndReportsItsBound(unittest.TestCase):
    """Row 3. A job that never empties: `end()` returns `ok=False` with why once
    `END_BOUND_S` has passed (an injected clock, so the bound costs no wall time)."""

    def test_a_job_that_never_empties_is_reported_never_swallowed(self):
        k32 = _Kernel32(stuck=True)
        tree = _inj_spawn(k32, [sys.executable, "-c", "import time; time.sleep(600)"])
        self.addCleanup(tree.close)
        clock = [1000.0]
        with mock.patch.object(Q, "_monotonic", lambda: clock[0]), \
                mock.patch.object(Q, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s)):
            ended = tree.end()
        self.assertFalse(ended.ok)
        self.assertIn("still running", ended.why)
        self.assertGreaterEqual(clock[0] - 1000.0, Q.END_BOUND_S)
        self.assertIn(("TerminateJobObject", 4242), k32.calls)

    def test_an_emptied_job_returns_the_moment_it_is_empty(self):
        k32 = _Kernel32()
        tree = _inj_spawn(k32, [sys.executable, "-c", "import time; time.sleep(600)"])
        t0 = time.monotonic()
        ended = tree.end()
        tree.close()
        self.assertTrue(ended.ok, ended.why)
        self.assertLess(time.monotonic() - t0, GONE_S)
        self.assertIn(("CloseHandle", 4242), k32.calls)

    def test_the_job_is_kill_on_close_with_the_windows_x64_layout(self):
        """verify2 N5, carried from `ide/lsp.py`: KILL_ON_JOB_CLOSE is set (the tree dies if
        mokata does) and JOBOBJECT_EXTENDED_LIMIT_INFORMATION has its 144-byte x64 layout."""
        import ctypes
        k32 = _Kernel32()
        tree = _inj_spawn(k32, [sys.executable, "-c", "pass"])
        tree.close()
        lim = [c for c in k32.calls if c[0] == "SetInformationJobObject"]
        self.assertEqual((4242, 9, 0x2000), lim[0][1:4])
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(144, lim[0][4])


class RunJobbedReportsAnUnemptiedTree(unittest.TestCase):
    """Row 3 at `run_jobbed` (round 2, verifier M3): the command exited, but the door could not
    say its tree is gone. That is no result — `run_jobbed` raises with the door's why, and the
    baseline, the one-shot caller users read a verdict from, says NOT RUN with it, never GREEN."""

    def _stuck(self):
        real_end = Q.Jobbed.end

        def stuck(tree, leftovers_only=False):
            real_end(tree, leftovers_only)
            tree.last_end = Q.Ended(False, False, "3 process(es) mokata started were still running")
            return tree.last_end
        return mock.patch.object(Q.Jobbed, "end", stuck)

    def test_run_jobbed_raises_the_why(self):
        with self._stuck(), self.assertRaises(OSError) as caught:
            Q.run_jobbed([sys.executable, "-c", "print('done')"], timeout=GONE_S)
        self.assertIn("still running", str(caught.exception))

    def test_the_baseline_is_not_run_not_green(self):
        from mokata import baseline as B
        with self._stuck():
            res = B.baseline_status(f'"{sys.executable}" -c "pass"', cwd=_tmp(self))
        self.assertEqual(B.NOT_RUN, res.state, res)
        self.assertFalse(res.ok)
        self.assertIn("still running", res.render())


class AFailedHandleLeavesNothingRunning(unittest.TestCase):
    """Round 2, verifier M1. A door that refuses leaves nothing running: when building the handle
    fails AFTER the process exists (macOS: `kqueue` hits EMFILE under fd pressure), the process —
    which nobody could otherwise end — is killed and reaped before the error reaches the caller,
    exactly as the Windows path kills a process it could not put in a job."""

    def _child(self):
        d = _tmp(self)
        pidfile = os.path.join(d, "pid")
        mark = os.path.join(d, "STILL_RAN")
        argv = [sys.executable, "-c",
                f"import os, time; open({pidfile!r} + '.t', 'w').write(str(os.getpid())); "
                f"os.replace({pidfile!r} + '.t', {pidfile!r}); time.sleep(3); "
                f"open({mark!r}, 'w').close(); time.sleep(600)"]
        _cleanup_pids(self, pidfile)                   # a red run must not leave it behind
        return argv, pidfile, mark

    def _assert_nothing_left(self, pidfile, mark):
        if os.path.exists(pidfile):
            self.assertTrue(_gone(*_read_pids(pidfile)), "the refused process kept running")
        time.sleep(4)                                  # past the child's 3 s mark
        self.assertFalse(os.path.exists(mark), "the refused process ran on")

    def test_any_failure_building_the_handle_kills_the_process(self):
        argv, pidfile, mark = self._child()
        real_init = Q.Jobbed.__init__
        made = []

        def init(tree, proc, job, nt):
            made.append(proc)
            _wait(lambda: os.path.exists(pidfile))     # it is really running
            real_init(tree, proc, job, nt)
            raise OSError(24, "Too many open files")
        with mock.patch.object(Q.Jobbed, "__init__", init), self.assertRaises(OSError):
            Q.spawn_jobbed(argv, stdin=subprocess.DEVNULL)
        self.assertIsNotNone(made[0].returncode, "the refused process was not reaped")
        self._assert_nothing_left(pidfile, mark)

    def test_on_the_windows_path_the_job_is_closed_and_the_process_ended(self):
        """The same rule where the job WAS made (inj off Windows): its handle is closed —
        KILL_ON_JOB_CLOSE ends the tree on Windows — and the process itself is ended."""
        argv, pidfile, mark = self._child()
        k32 = _Kernel32()
        real_init = Q.Jobbed.__init__

        def init(tree, proc, job, nt):
            _wait(lambda: os.path.exists(pidfile))
            real_init(tree, proc, job, nt)
            raise OSError(24, "Too many open files")
        with mock.patch.object(Q.Jobbed, "__init__", init), self.assertRaises(OSError):
            _inj_spawn(k32, argv)
        self.assertIn(("CloseHandle", 4242), k32.calls)
        self._assert_nothing_left(pidfile, mark)

    @unittest.skipUnless(not hasattr(os, "waitid") and hasattr(__import__("select"), "kqueue"),
                         "the kqueue exit watch is macOS's")
    def test_a_kqueue_that_cannot_be_made_kills_the_process(self):
        import select
        argv, pidfile, mark = self._child()

        def no_fds():
            raise OSError(24, "Too many open files")
        with mock.patch.object(select, "kqueue", no_fds), self.assertRaises(OSError):
            Q.spawn_jobbed(argv, stdin=subprocess.DEVNULL)
        self._assert_nothing_left(pidfile, mark)


# ========================================================================= rows 4, 5 · refusal
class NoJobRefuses(unittest.TestCase):
    """Rows 4-5. A job Windows refuses: nothing runs, `NO_JOB` + remedy is raised, and the process
    (created suspended) is killed — also when the failure is not an OSError. Each caller turns the
    refusal into its own not-run, not-ok outcome."""

    def _child(self):
        d = _tmp(self)
        pidfile = os.path.join(d, "pid")
        return [sys.executable, "-c",
                f"import os, time; open({pidfile!r}, 'w').write(str(os.getpid())); time.sleep(600)"
                ], pidfile

    def _assert_killed(self, pidfile):
        # Off Windows the injected path cannot create the child SUSPENDED, so it may have written
        # its pid before the refusal; it must be dead either way.
        if os.path.exists(pidfile):
            self.assertTrue(_gone(*_read_pids(pidfile)), "a refused process was left running")

    def test_each_refused_call_raises_no_job_with_the_remedy_and_kills_the_process(self):
        for call in ("CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"):
            argv, pidfile = self._child()
            spawned = []
            real_popen = subprocess.Popen

            def popen(*a, **k):
                p = real_popen(*a, **k)
                spawned.append(p)
                return p
            with mock.patch.object(Q.subprocess, "Popen", popen), self.assertRaises(OSError) as c:
                _inj_spawn(_Kernel32(fail=call), argv)
            msg = str(c.exception)
            self.assertTrue(msg.startswith(Q.NO_JOB), msg)
            self.assertIn(call, msg)
            self.assertIn("Access is denied", msg)
            self.assertIn(Q.NO_JOB_REMEDY, msg, "the user cannot act on a bare Windows error")
            self.assertEqual(1, len(spawned))
            self.assertIsNotNone(spawned[0].returncode, "the refused process is still running")
            self._assert_killed(pidfile)

    def test_a_non_oserror_while_held_also_kills_it(self):
        argv, pidfile = self._child()
        spawned = []
        real_popen = subprocess.Popen

        def popen(*a, **k):
            spawned.append(real_popen(*a, **k))
            return spawned[-1]

        def resume(proc):
            raise KeyboardInterrupt
        with mock.patch.object(Q.subprocess, "Popen", popen), self.assertRaises(KeyboardInterrupt):
            Q.spawn_jobbed(argv, nt=True, _kernel32=_Kernel32(), _resume=resume)
        self.assertIsNotNone(spawned[0].returncode)
        self._assert_killed(pidfile)

    def test_a_create_refused_by_a_full_parent_job_is_the_same_refusal(self):
        """Row 7 (inj): inside a job at its process limit CreateProcess itself fails with
        ERROR_NOT_ENOUGH_QUOTA (1816). Same fact, same words; any other launch error is untouched."""
        def full(*a, **k):
            exc = OSError(1816, "Not enough quota is available to process this command")
            exc.winerror = 1816
            raise exc
        with mock.patch.object(Q.subprocess, "Popen", full), self.assertRaises(OSError) as c:
            Q.spawn_jobbed([sys.executable, "-c", "pass"], nt=True, _kernel32=_Kernel32())
        self.assertTrue(str(c.exception).startswith(Q.NO_JOB), c.exception)
        self.assertIn("CreateProcess", str(c.exception))
        self.assertIn(Q.NO_JOB_REMEDY, str(c.exception))

        def missing(*a, **k):
            raise FileNotFoundError(2, "No such file")
        with mock.patch.object(Q.subprocess, "Popen", missing), \
                self.assertRaises(FileNotFoundError) as c:
            Q.spawn_jobbed(["nope"], nt=True, _kernel32=_Kernel32())
        self.assertNotIn(Q.NO_JOB, str(c.exception))

    @unittest.skipUnless(NT, "probe: a real limited-rights handle on Windows")
    def test_on_windows_a_real_access_denied_refuses_and_nothing_ran(self):
        """The REAL refusal: AssignProcessToJobObject on a handle opened with SYNCHRONIZE only."""
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        d = _tmp(self)
        mark = os.path.join(d, "RAN")
        made = []
        real_popen = subprocess.Popen

        def popen(*a, **k):
            p = real_popen(*a, **k)
            made.append(p)
            return p

        from ctypes import wintypes
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        real_make = Q.WinJob.make
        weak = []

        def make(cls, handle, kernel32=None):
            # The door's own handle has full rights; hand Windows one with SYNCHRONIZE only, so
            # AssignProcessToJobObject is REALLY refused (ERROR_ACCESS_DENIED) by the OS.
            weak.append(k32.OpenProcess(0x00100000, False, made[0].pid))
            return real_make(weak[-1], kernel32)
        with mock.patch.object(Q.subprocess, "Popen", popen), \
                mock.patch.object(Q.WinJob, "make", classmethod(make)), \
                self.assertRaises(OSError) as c:
            Q.spawn_jobbed([sys.executable, "-c", f"open({mark!r}, 'w').close()"])
        for h in weak:
            k32.CloseHandle(h)
        msg = str(c.exception)
        print(f"\n[probe] real refusal text: {msg}")
        self.assertTrue(msg.startswith(Q.NO_JOB), msg)
        self.assertIn("AssignProcessToJobObject", msg)
        self.assertIn("WinError 5", msg)
        self.assertIsNotNone(made[0].returncode)
        time.sleep(1.0)
        self.assertFalse(os.path.exists(mark), "the refused process ran an instruction")

    # ---- each caller's outcome (row 5) -----------------------------------------------------
    def _refused(self, *a, **k):
        raise Q._no_job("AssignProcessToJobObject", "WinError 5: Access is denied.")

    def test_each_caller_ide_rerun_is_not_run_never_red(self):
        from mokata.ide import review_context as RC
        d = _tmp(self)
        with mock.patch.object(RC, "spawn_jobbed", self._refused):
            res = RC.run_tests(d, [sys.executable, "-m", "pytest"], ["t_a", "t_b"])
        self.assertEqual(["not-run", "not-run"], [r["state"] for r in res["tests"]])
        self.assertEqual(0, res["failed"])
        self.assertIn(Q.NO_JOB, res["tests"][0]["note"])
        self.assertEqual({}, RC.record_rerun(d, "R1", "x", res["tests"], True),
                         "a run in which nothing ran recorded an outcome")

    def test_each_caller_merge_is_a_suite_error(self):
        case = _MergeRefusal("run")
        case.setUp()
        try:
            case.run_it(self._refused)
        finally:
            case.doCleanups()

    def test_each_caller_language_server_is_a_note(self):
        from mokata.ide import lsp as L
        d = _tmp(self)
        srv = L.Server(d, "python", "fake", [sys.executable, "-c", "pass"])
        with mock.patch.object(L, "spawn_jobbed", self._refused):
            with self.assertRaises(OSError) as c:
                srv.start()
        self.assertIn(Q.NO_JOB, str(c.exception))
        self.assertIsNone(srv.tree)

    def test_each_caller_baseline_is_not_run_and_not_ok(self):
        from mokata import baseline as B
        with mock.patch.object(Q, "spawn_jobbed", self._refused):
            res = B.baseline_status("echo hi", cwd=_tmp(self))
        self.assertEqual(B.NOT_RUN, res.state)
        self.assertFalse(res.ok)
        self.assertIn(Q.NO_JOB, res.render())

    def test_each_caller_crg_probe_is_refused_with_the_reason(self):
        from mokata import crg_resolve as CR
        d = _tmp(self)
        exe = _script(d, "code-review-graph", "")
        CR._PROBED.clear()
        self.addCleanup(CR._PROBED.clear)
        with mock.patch.object(Q, "spawn_jobbed", self._refused):
            version, why = CR.probe_version(exe, timeout=GONE_S)
        self.assertIsNone(version)
        res = CR._checked(CR.CrgResolution(origin="MOKATA_CRG"), exe, version, why)
        self.assertIn(Q.NO_JOB, res.refused)

    def test_each_caller_statusline_wrap_is_empty(self):
        from mokata import hook_cli as H
        with mock.patch.object(Q, "spawn_jobbed", self._refused):
            self.assertEqual("", H._run_wrapped("echo hi", "{}"))

    def test_each_caller_ask_tool_is_none(self):
        from mokata import project_env as P
        d = _tmp(self)
        with mock.patch("mokata.exe_path.which_in", lambda *a, **k: sys.executable), \
                mock.patch.object(Q, "spawn_jobbed", self._refused):
            self.assertIsNone(P._ask_tool(["poetry", "env", "info", "-p"], d))


class _MergeRefusal(_Merge):
    def runTest(self):  # pragma: no cover - driven by NoJobRefuses
        pass

    def run_it(self, refused):
        from mokata import merge_suite as MS
        wt = self.session()
        with mock.patch.object(Q, "spawn_jobbed", refused):
            with self.assertRaises(MS.SuiteError) as caught:
                MS.run_full_suite(self.main, wt)
        self.assertIn(Q.NO_JOB, str(caught.exception))
        self.assertIn("could not start", str(caught.exception))


# =================================================================== rows 6, 7 · nested jobs
_NESTED = r"""
import ctypes, json, os, subprocess, sys
from ctypes import wintypes
sys.path.insert(0, sys.argv[1])
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateJobObjectW.restype = wintypes.HANDLE
k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
from mokata import quiet_spawn as Q
_B, _E, _A = Q.WinJob._types()
job = k32.CreateJobObjectW(None, None)
info = _E()
flags = 0x2000                                     # KILL_ON_JOB_CLOSE, no breakaway of any kind
if sys.argv[2] == "limit":
    flags |= 0x0008                                # ACTIVE_PROCESS limit
    info.BasicLimitInformation.ActiveProcessLimit = 1
info.BasicLimitInformation.LimitFlags = flags
assert k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
assert k32.AssignProcessToJobObject(job, k32.GetCurrentProcess())
out = {}
try:
    rc, so, se = Q.run_jobbed([sys.executable, sys.argv[3], sys.argv[4], "0", "sleep"], timeout=120)
    out["rc"] = rc
except OSError as exc:
    out["error"] = str(exc)
    out["winerror"] = getattr(exc, "winerror", None)
print(json.dumps(out))
"""


@unittest.skipUnless(NT, "probe: Job Objects exist on Windows only")
class NestedJob(unittest.TestCase):
    """Rows 6-7. mokata runs inside Claude Code's tree, Windows Terminal, an MCP server, a CI
    runner — each possibly a job. From inside a KILL_ON_JOB_CLOSE job with no breakaway the door
    still nests its own job and ends its tree; inside a job whose process limit is reached it
    refuses with the Windows text and the remedy, and never hangs."""

    def _run(self, kind):
        d = _tmp(self)
        helper = _script(d, "nested.py", _NESTED)
        launcher = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        p = subprocess.run([sys.executable, helper, os.path.join(SRC), kind, launcher, pidfile],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        print(f"\n[probe] nested {kind}: rc={p.returncode} out={p.stdout.strip()} "
              f"err={p.stderr.strip()[-400:]}")
        return json.loads(p.stdout.strip().splitlines()[-1]), pidfile

    def test_inside_a_job_that_forbids_breakaway_the_door_nests_and_ends_its_tree(self):
        out, pidfile = self._run("nested")
        self.assertEqual(0, out.get("rc"), out)
        self.assertTrue(_gone(*_read_pids(pidfile)))

    def test_inside_a_job_at_its_process_limit_the_door_refuses_with_the_remedy(self):
        out, _pidfile = self._run("limit")
        self.assertIn("error", out, out)
        self.assertTrue(out["error"].startswith(Q.NO_JOB), out)
        self.assertIn(Q.NO_JOB_REMEDY, out["error"])


# ======================================================================= row 11 · launchers
@unittest.skipUnless(NT, "probe: a venv's Scripts\\python.exe is a real launcher on Windows only")
class LauncherRace(unittest.TestCase):
    """Row 11. A venv's `Scripts\\python.exe` re-launches the base interpreter (a grandchild). 50
    runs through the door: the launcher's own child — started before anything else could be
    assigned — never escapes the job; every grandchild is gone when `run_jobbed` returns."""

    RUNS = 50

    def test_fifty_launcher_runs_leave_no_escaped_grandchild(self):
        d = _tmp(self)
        venv = os.path.join(d, "venv")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True,
                       stdin=subprocess.DEVNULL, timeout=300)
        exe = os.path.join(venv, "Scripts", "python.exe")
        launcher = _script(d, "launch.py", LAUNCHER)
        left = []
        for i in range(self.RUNS):
            pidfile = os.path.join(d, f"pids{i}")
            rc, _out, _err = Q.run_jobbed([exe, launcher, pidfile, "0", "sleep"], timeout=120)
            self.assertEqual(0, rc)
            left += [p for p in _read_pids(pidfile) if _alive(p)]
        self.assertTrue(_gone(*left), f"{len(left)} launcher grandchild(ren) escaped")
        print(f"\n[probe] launcher race: {self.RUNS} runs, still alive at return: {len(left)}, "
              f"after the bound: {sum(1 for p in left if _alive(p))}")


# ============================================================== rows 8, 9 · derived sweeps
_SPAWNERS = frozenset({"Popen", "run", "call", "check_call", "check_output"})


#: Every standard-library call that creates a process, by module (round 2, verifier M2: `os.system`
#: and `os.posix_spawn` were invisible to the round-1 sweep).
_PROC_APIS = {
    "subprocess": _SPAWNERS | {"getoutput", "getstatusoutput"},
    "os": frozenset({"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
                     "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp",
                     "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve",
                     "execvp", "execvpe"}),
    "asyncio": frozenset({"create_subprocess_exec", "create_subprocess_shell"}),
    "pty": frozenset({"fork", "spawn"}),
}


def spawn_sites(sources):
    """Pure: `{relpath: source}` -> `[(relpath, qualname, line)]` for EVERY process-creating call
    (`_PROC_APIS`), however its module is spelled (alias, from-import), one entry per call site."""
    out = []
    for rel, src in sources.items():
        tree = ast.parse(src)
        mods = {m: {m} for m in _PROC_APIS}
        names = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name in _PROC_APIS:
                        mods[a.name].add(a.asname or a.name)
            elif isinstance(n, ast.ImportFrom) and n.module in _PROC_APIS:
                for a in n.names:
                    if a.name in _PROC_APIS[n.module]:
                        names[a.asname or a.name] = (n.module, a.name)

        def is_spawn(f):
            if isinstance(f, ast.Attribute):
                v = f.value
                if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                        and v.value.id in mods["asyncio"] and v.attr == "subprocess":
                    return f.attr in _PROC_APIS["asyncio"]       # asyncio.subprocess.create_*
                return isinstance(v, ast.Name) and any(
                    v.id in aliases and f.attr in _PROC_APIS[m] for m, aliases in mods.items())
            return isinstance(f, ast.Name) and f.id in names

        def walk(node, qual):
            for ch in ast.iter_child_nodes(node):
                q = qual + [ch.name] if isinstance(
                    ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else qual
                if isinstance(ch, ast.Call) and is_spawn(ch.func):
                    out.append((rel, ".".join(q) or "<module>", ch.lineno))
                walk(ch, q)
        walk(tree, [])
    return out


# CORPUS: THE WORKING TREE — the sweep judges the source a build would ship, edits included.
def _src_sources():
    out = {}
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in ("__pycache__",) and not d.endswith(".egg-info")]
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8") as fh:
                    out[p[len(SRC) + 1:].replace(os.sep, "/")] = fh.read()   # under SRC by os.walk
    return out


def unclassified(sources, register, counts=None):
    """The spawn SITES that are neither the door nor covered by a `TREELESS` row. A row covers as
    many call sites in its function as `TREELESS_SITES` says (default 1): a second spawn added to
    a registered function is a red, not a free ride (round 2, verifier M2)."""
    counts = Q.TREELESS_SITES if counts is None else counts
    door = ("mokata/quiet_spawn.py", "spawn_jobbed")
    seen = {}
    bad = []
    for site in spawn_sites(sources):
        key = (site[0], site[1])
        seen[key] = seen.get(key, 0) + 1
        allowed = 1 if key == door else (counts.get(key, 1) if key in register else 0)
        if seen[key] > allowed:
            bad.append(site)
    return bad


class EverySpawnIsClassified(unittest.TestCase):
    """Row 8 (R1 derived, refute M1). Every `Popen` / `subprocess.run|call|check_*` in `src/` is the
    door or a `TREELESS` row with a kind and a reason. A sixth spawn is a red, not a comment."""

    def test_every_spawn_in_src_is_the_door_or_a_registered_row(self):
        self.assertEqual([], unclassified(_src_sources(), Q.TREELESS),
                         "route it through quiet_spawn.spawn_jobbed/run_jobbed, or register WHY not")

    def test_every_registered_function_holds_exactly_the_sites_it_declares(self):
        live = {}
        for site in spawn_sites(_src_sources()):
            live[(site[0], site[1])] = live.get((site[0], site[1]), 0) + 1
        wrong = {k: (live.get(k, 0), Q.TREELESS_SITES.get(k, 1)) for k in Q.TREELESS
                 if live.get(k, 0) != Q.TREELESS_SITES.get(k, 1)}
        self.assertEqual({}, wrong, "(found, declared) spawn sites per registered function")
        self.assertEqual(set(), set(Q.TREELESS_SITES) - set(Q.TREELESS),
                         "a site count for a function that has no register row")

    def test_a_second_spawn_inside_a_registered_function_reds(self):
        reg = {("mokata/git_safe.py", "Git._exec"): (Q.SINGLE, "git", "x")}
        one = "import subprocess\nclass Git:\n    def _exec(self):\n        subprocess.run(['git'])\n"
        two = one + "        subprocess.Popen(['sh'])\n"
        self.assertEqual([], unclassified({"mokata/git_safe.py": one}, reg, {}))
        self.assertEqual([("mokata/git_safe.py", "Git._exec", 5)],
                         unclassified({"mokata/git_safe.py": two}, reg, {}))

    def test_every_process_creating_api_is_seen(self):
        for src in ("import os\ndef f():\n    os.system('x')\n",
                    "import os\ndef f():\n    os.posix_spawn('x', ['x'], {})\n",
                    "import os as o\ndef f():\n    o.execvp('x', ['x'])\n",
                    "from os import popen\ndef f():\n    popen('x')\n",
                    "import os\ndef f():\n    os.spawnv(0, 'x', ['x'])\n",
                    "import subprocess\ndef f():\n    subprocess.getoutput('x')\n",
                    "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('x')\n",
                    "import asyncio\nasync def f():\n    await asyncio.subprocess.create_subprocess_shell('x')\n",
                    "import pty\ndef f():\n    pty.spawn('x')\n"):
            self.assertEqual(1, len(unclassified({"mokata/new.py": src}, {}, {})), src)
        self.assertEqual([], unclassified({"mokata/new.py": "import os\ndef f():\n    os.kill(1, 0)\n"},
                                          {}, {}))

    def test_the_register_has_no_stale_row_and_every_row_says_why(self):
        live = {(s[0], s[1]) for s in spawn_sites(_src_sources())}
        kinds = {Q.SINGLE, Q.OUTLIVES, Q.OWN_TREE, Q.FILED}
        for key, (kind, program, why) in Q.TREELESS.items():
            self.assertIn(key, live, f"stale TREELESS row {key}: no spawn there any more")
            self.assertIn(kind, kinds, key)
            self.assertTrue(program and why, key)
        # 0.0.28 lane I routed its sites (`mcp_admin.handshake`, `_probe_version`,
        # `branch_protection._default_gh_runner`, the doctor pin probe) through the door, so the
        # "waits on lane I" kind is deleted with its rows.
        for site in (("mokata/mcp_admin.py", "handshake"), ("mokata/mcp_admin.py", "_probe_version"),
                     ("mokata/branch_protection.py", "_default_gh_runner")):
            self.assertNotIn(site, Q.TREELESS, site)

    def test_a_synthetic_unregistered_spawn_reds_however_it_is_spelled(self):
        for src in ("import subprocess\ndef f():\n    subprocess.run(['x'], timeout=1)\n",
                    "import subprocess as sp\ndef f():\n    sp.Popen(['x'])\n",
                    "from subprocess import check_output as co\ndef f():\n    co(['x'])\n"):
            self.assertEqual([("mokata/new.py", "f", 3)], unclassified({"mokata/new.py": src},
                                                                       Q.TREELESS), src)
        door = "import subprocess\ndef spawn_jobbed():\n    subprocess.Popen(['x'])\n"
        self.assertEqual([], unclassified({"mokata/quiet_spawn.py": door}, Q.TREELESS))

    def test_the_callers_this_lane_moved_are_the_door_not_register_rows(self):
        moved = {"mokata/baseline.py", "mokata/merge_suite.py", "mokata/crg_resolve.py",
                 "mokata/project_env.py", "mokata/ide/lsp.py"}
        self.assertEqual(set(), {s[0] for s in spawn_sites(_src_sources())} & moved)
        self.assertNotIn(("mokata/hook_cli.py", "_run_wrapped"), Q.TREELESS)


#: (Not headed by `taskkill`: a tuple starting with a System32 name reads as an argv to the
#: Windows-portability sweep.)
_BANNED = ("TreeWatch", "CreateJobObjectW", "TerminateJobObject", "NtResumeProcess",
           "CreateToolhelp32Snapshot", "taskkill")
#: Things that END a process. A module allowed to walk the process table must contain none.
_KILLS = ("signal_pid", "signal_group", "kill", "killpg", "terminate", "TerminateProcess",
          "send_signal")
#: The note's row 9 bans `CreateToolhelp32Snapshot` everywhere outside the door; the code proves
#: one READ-ONLY use the note did not list — `session_ancestry.py` walks parent pids to find which
#: Claude Code session owns this process, and ends nothing. It is admitted ONLY while it holds no
#: kill primitive at all (asserted below), so a pid it enumerates can never reach a kill path.
_READ_ONLY_WALKERS = {"mokata/session_ancestry.py": "CreateToolhelp32Snapshot"}


def _calls_named(tree, names):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr in names)
        or (isinstance(n.func, ast.Name) and n.func.id in names))]


def second_doors(sources):
    """Pure: identifiers and non-docstring string constants naming a tree-kill primitive, and every
    `shell=` other than `False` on a subprocess spawn, outside `quiet_spawn.py`."""
    bad = []
    for rel, src in sources.items():
        if rel.endswith("quiet_spawn.py"):
            continue
        tree = ast.parse(src)
        walker_ok = rel in _READ_ONLY_WALKERS and not _calls_named(tree, _KILLS)
        docs = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and n.body and isinstance(n.body[0], ast.Expr) \
                    and isinstance(n.body[0].value, ast.Constant):
                docs.add(id(n.body[0].value))
        for n in ast.walk(tree):
            text = None
            if isinstance(n, ast.Name):
                text = n.id
            elif isinstance(n, ast.Attribute):
                text = n.attr
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                text = n.name
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                text = n.value
            hits = [b for b in _BANNED if text and b.lower() in text.lower()]
            if walker_ok:
                hits = [b for b in hits if b != _READ_ONLY_WALKERS[rel]]
            if hits:
                bad.append(f"{rel}:{getattr(n, 'lineno', '?')} {text[:40]!r}")
        for call in _calls_named(tree, _SPAWNERS):
            for kw in call.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant)
                                              and kw.value.value is False):
                    bad.append(f"{rel}:{kw.value.lineno} shell=")
    return bad


class NoSecondTreeDoor(unittest.TestCase):
    """Row 9 (R6). Outside the door: no taskkill, no Job Object, no resume, no process-table walk,
    no TreeWatch, no `shell=True`."""

    def test_nothing_outside_the_door_ends_a_tree_or_opens_a_shell(self):
        self.assertEqual([], second_doors(_src_sources()))

    def test_the_sweep_catches_each_primitive_and_acquits_prose(self):
        for src in ("import os\nX = os.path.join('a', 'taskkill.exe')\n",
                    "def f(k):\n    k.TerminateJobObject(1, 1)\n",
                    "class TreeWatch:\n    pass\n",
                    "import subprocess\nsubprocess.run('x', shell=True)\n",
                    "import subprocess\nsubprocess.run('x', shell=flag)\n"):
            self.assertEqual(1, len(second_doors({"mokata/x.py": src})), src)
        prose = '"""taskkill and TreeWatch were deleted."""\n# TerminateJobObject\nX = 1\n'
        self.assertEqual([], second_doors({"mokata/x.py": prose}))
        self.assertEqual([], second_doors({"mokata/x.py": "import subprocess\n"
                                           "subprocess.run(['x'], shell=False)\n"}))
        self.assertEqual([], second_doors({"mokata/x.py": "W = Hook(shell=str(s))\n"}),
                         "a dataclass field called shell is not a shell")

    def test_a_process_table_walker_is_admitted_only_while_it_ends_nothing(self):
        walk = "def f(k):\n    return k.CreateToolhelp32Snapshot(2, 0)\n"
        self.assertEqual([], second_doors({"mokata/session_ancestry.py": walk}))
        self.assertEqual(1, len(second_doors({"mokata/session_ancestry.py":
                                              walk + "def g(p):\n    p.kill()\n"})),
                         "a walker that can also kill is the pid-reuse class back again")
        self.assertEqual(1, len(second_doors({"mokata/other.py": walk})))


# ============================================================================ row 10 · one door
class OneDoor(unittest.TestCase):
    """Row 10. Each moved site spawns through the door (a spy that delegates, so the real thing
    still runs). `mcp_admin.handshake` / `_probe_version` are lane I's (routed after rebase)."""

    def _spy(self, target, name):
        real = getattr(target, name)
        seen = []

        def spy(*a, **k):
            seen.append((a, k))
            return real(*a, **k)
        p = mock.patch.object(target, name, spy)
        p.start()
        self.addCleanup(p.stop)
        return seen

    def test_ide_rerun_and_language_server_use_spawn_jobbed(self):
        from mokata.ide import lsp as L
        from mokata.ide import review_context as RC
        self.assertIs(RC.spawn_jobbed, Q.spawn_jobbed)
        self.assertIs(L.spawn_jobbed, Q.spawn_jobbed)
        seen = self._spy(RC, "spawn_jobbed")
        d = _tmp(self)
        res = RC.run_tests(d, [sys.executable, "-c", "import sys; sys.exit(0)"], ["t1"])
        self.assertEqual("green", res["tests"][0]["state"])
        self.assertEqual(1, len(seen))

    def test_baseline_crg_wrap_and_ask_tool_use_run_jobbed(self):
        from mokata import baseline as B, crg_resolve as CR, hook_cli as H, project_env as P
        seen = self._spy(Q, "run_jobbed")
        d = _tmp(self)
        self.assertEqual(B.GREEN, B.baseline_status("echo ok", cwd=d).state)
        CR._PROBED.clear()
        self.addCleanup(CR._PROBED.clear)
        CR.probe_version(sys.executable, timeout=GONE_S)
        self.assertEqual("x", H._run_wrapped("echo x", "{}"))
        with mock.patch("mokata.exe_path.which_in", lambda *a, **k: sys.executable):
            P._ask_tool(["poetry", "-c", "print(1)"], d)
        self.assertEqual(4, len(seen), [s[1] for s in seen])

    def test_the_merge_suite_uses_spawn_jobbed(self):
        case = _MergeSpy("run")
        case.setUp()
        try:
            case.check(self)
        finally:
            case.doCleanups()


class _MergeSpy(_Merge):
    def runTest(self):  # pragma: no cover
        pass

    def check(self, outer):
        from mokata import merge_suite as MS
        seen = []
        real = Q.spawn_jobbed

        def spy(*a, **k):
            seen.append(a)
            return real(*a, **k)
        wt = self.session()
        with mock.patch.object(Q, "spawn_jobbed", spy):
            outer.assertTrue(MS.run_full_suite(self.main, wt)["passed"])
        outer.assertEqual(1, len(seen))


# ============================================================ rows 12-16 · baseline is bounded
class BaselineIsBounded(unittest.TestCase):
    """Row 12. A suite whose grandchild holds stdout and outlives the timeout: `baseline` returns
    within its bound + slack, says NOT_RUN "timed out after N s", and the grandchild is dead. On
    Windows this is THE hang (`subprocess.run` then `communicate()`d with no bound)."""

    def test_a_timeout_with_a_grandchild_holding_stdout_is_not_run_and_ends_the_tree(self):
        from mokata import baseline as B
        d = _tmp(self)
        launcher = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        cmd = f'"{sys.executable}" "{launcher}" "{pidfile}" 600 sleep'
        t0 = time.monotonic()
        res = B.baseline_status(cmd, cwd=d, timeout=3)
        self.assertLess(time.monotonic() - t0, 3 + GONE_S)
        self.assertEqual(B.NOT_RUN, res.state)
        self.assertIn("timed out after 3 s", res.detail)
        self.assertTrue(_gone(*_read_pids(pidfile)), "the baseline's tree outlived its timeout")


class BaselineNotRun(unittest.TestCase):
    """Rows 13-14. NOT_RUN is not ok on every surface; UNKNOWN still means only "no command"."""

    def _refuse(self, *a, **k):
        raise Q._no_job("AssignProcessToJobObject", "WinError 5: Access is denied.")

    def test_not_run_is_not_ok_and_says_why_for_a_refusal_a_launch_failure_and_a_timeout(self):
        from mokata import baseline as B
        d = _tmp(self)
        with mock.patch.object(Q, "spawn_jobbed", self._refuse):
            refused = B.baseline_status("echo hi", cwd=d)
        launch = B.baseline_status("echo hi", cwd=os.path.join(d, "no-such-dir"))
        timed = B.baseline_status(f'"{sys.executable}" -c "import time; time.sleep(60)"', cwd=d,
                                  timeout=1)
        for res, words in ((refused, Q.NO_JOB), (launch, "could not run"),
                           (timed, "timed out after 1 s")):
            self.assertEqual(B.NOT_RUN, res.state, res)
            self.assertFalse(res.ok)
            self.assertIn(words, res.render())
            self.assertNotIn(B.NO_COMMAND_MESSAGE, res.render())
            self.assertIn("NOT RUN", res.render())

    def test_the_cli_exits_one_and_the_mcp_tool_says_not_ok(self):
        from mokata.cli import main
        from mokata.mcp import tools_read as TR
        d = _tmp(self)
        with mock.patch.object(Q, "spawn_jobbed", self._refuse):
            with mock.patch("sys.stdout", new=__import__("io").StringIO()) as out:
                rc = main(["baseline", "--path", d, "--cmd", "echo hi"])
            got = TR.baseline(path=d, cmd="echo hi")
        self.assertEqual(1, rc, out.getvalue())
        self.assertIn("NOT RUN", out.getvalue())
        self.assertFalse(got["ok"], got)

    def test_unknown_still_means_no_command_and_is_ok(self):
        from mokata import baseline as B
        from mokata.cli import main
        res = B.baseline_status(None)
        self.assertEqual(B.UNKNOWN, res.state)
        self.assertTrue(res.ok)
        self.assertEqual(B.NO_COMMAND_MESSAGE, res.render())
        d = _tmp(self)
        with mock.patch("sys.stdout", new=__import__("io").StringIO()):
            self.assertEqual(0, main(["baseline", "--path", d]))

    def test_ok_is_an_allow_list(self):
        from mokata import baseline as B
        self.assertFalse(B.BaselineResult(state="some-future-state").ok)
        self.assertTrue(B.BaselineResult(state=B.GREEN).ok)
        self.assertFalse(B.BaselineResult(state=B.RED).ok)


class BaselineShell(unittest.TestCase):
    """Rows 15-16. The baseline shell is Python's own: `/bin/sh` on POSIX — a repo-planted
    `.venv/bin/sh` first on the project-activated PATH is never run; on Windows `ComSpec /c` with
    today's quoting, byte for byte."""

    @unittest.skipIf(NT, "POSIX: /bin/sh vs a planted sh")
    def test_a_repo_planted_sh_in_the_project_env_is_not_the_shell(self):
        from mokata import baseline as B
        d = _tmp(self)
        venv_bin = os.path.join(d, ".venv", "bin")
        os.makedirs(venv_bin)
        _script(os.path.join(d, ".venv"), "pyvenv.cfg", "home = /usr/bin\n")
        mark = os.path.join(d, "PLANTED_SH_RAN")
        sh = _script(venv_bin, "sh", f"#!/bin/sh\ntouch '{mark}'\nexit 0\n")
        os.chmod(sh, 0o755)
        res = B.baseline_status("echo ok", cwd=d, environ={"PATH": "/usr/bin:/bin"})
        self.assertEqual(B.GREEN, res.state, res.detail)
        self.assertIn(".venv", res.env_line, "the planted env must be the active project env")
        self.assertFalse(os.path.exists(mark), "a repo-planted sh ran as the baseline shell")

    @unittest.skipUnless(NT, "probe: cmd.exe quoting on Windows")
    def test_same_as_shell_true(self):
        d = _tmp(self)
        spaced = os.path.join(d, "dir with spaces")
        os.makedirs(spaced)
        py = os.path.join(spaced, "py.exe")
        shutil.copy(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                 "where.exe"), py)
        cmds = [f'"{sys.executable}" -c "import sys; print(sys.argv[1:])" -k "a and b"',
                'echo one && echo two',
                f'"{py}" /?']
        for cmd in cmds:
            old = subprocess.run(cmd, shell=True, capture_output=True, cwd=d,
                                 stdin=subprocess.DEVNULL, timeout=120)
            rc, out, _err = Q.run_jobbed(shell_line=cmd, cwd=d, timeout=120)
            print(f"\n[probe] {cmd!r}: shell=True rc={old.returncode} door rc={rc}")
            self.assertEqual(old.returncode, rc, cmd)
            self.assertEqual(old.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
                             out.replace("\r\n", "\n"), cmd)


# ================================================================ rows 17, 18 · wrap, ask_tool
class WrapIsBounded(unittest.TestCase):
    """Row 17. The statusLine wrap — run on every statusline tick — with a hanging grandchild
    returns "" within its bound + slack, and nothing it started survives."""

    def test_a_hanging_grandchild_returns_empty_and_dies(self):
        from mokata import hook_cli as H
        d = _tmp(self)
        launcher = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        t0 = time.monotonic()
        got = H._run_wrapped(f'"{sys.executable}" "{launcher}" "{pidfile}" 600 sleep', "{}")
        self.assertEqual("", got)
        self.assertLess(time.monotonic() - t0, H._WRAP_TIMEOUT_SECS + GONE_S)
        self.assertTrue(_gone(*_read_pids(pidfile)))

    def test_the_payload_reaches_the_command_and_its_first_line_comes_back(self):
        from mokata import hook_cli as H
        cmd = f'"{sys.executable}" -c "import sys; print(sys.stdin.read().upper()); print(2)"'
        self.assertEqual('{"A": 1}', H._run_wrapped(cmd, '{"a": 1}'))


class AskToolIsBounded(unittest.TestCase):
    """Row 18. `poetry.exe` / `pipenv.exe` are launchers: a launcher whose grandchild hangs is
    bounded by the tool timeout and its whole tree ends. On Windows the stand-in is a venv's
    `Scripts\\python.exe` (a real launcher)."""

    def test_a_launcher_whose_grandchild_hangs_is_bounded_and_leaves_nothing(self):
        from mokata import project_env as P
        d = _tmp(self)
        launcher = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        exe = sys.executable
        if NT:
            venv = os.path.join(d, "venv")
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True,
                           stdin=subprocess.DEVNULL, timeout=300)
            exe = os.path.join(venv, "Scripts", "python.exe")
        t0 = time.monotonic()
        with mock.patch("mokata.exe_path.which_in", lambda *a, **k: exe), \
                mock.patch.object(P, "TOOL_TIMEOUT_SECONDS", 3):
            got = P._ask_tool(["poetry", launcher, pidfile, "600", "sleep"], d)
        self.assertIsNone(got)
        self.assertLess(time.monotonic() - t0, 3 + GONE_S)
        self.assertTrue(_gone(*_read_pids(pidfile)))


# =========================================================== rows 21, 22 · POSIX group, once
@unittest.skipIf(NT, "process groups are POSIX; Windows uses the Job Object")
class GroupKillOnlyUnreaped(unittest.TestCase):
    """Row 21 (refute M2). The group is signalled only while its leader is UNREAPED — a zombie
    still holds the pid, so the group id cannot have been recycled. A leader reaped behind the
    handle's back gets NO group signal, and `end` says leftovers are unknown."""

    def _spawn(self):
        d = _tmp(self)
        launcher = _script(d, "launch.py", LAUNCHER)
        pidfile = os.path.join(d, "pids")
        tree = Q.spawn_jobbed([sys.executable, launcher, pidfile, "0", "sleep"],
                              stdin=subprocess.DEVNULL)
        self.addCleanup(tree.close)
        _cleanup_pids(self, pidfile)
        self.assertTrue(_wait(lambda: os.path.exists(pidfile)))
        return tree, pidfile

    def test_a_zombie_leaders_group_is_killed_then_reaped(self):
        tree, pidfile = self._spawn()
        tree.wait_exit(GONE_S)
        self.assertIsNone(tree.proc.returncode, "wait_exit reaped the leader")
        _leader, grandchild = _read_pids(pidfile)
        self.assertTrue(_alive(grandchild))
        ended = tree.end(leftovers_only=True)
        self.assertEqual((True, False), (ended.ok, ended.leftovers_unknown))
        self.assertEqual(0, tree.proc.returncode, "end reaps the leader")
        self.assertTrue(_gone(grandchild))

    def test_an_already_reaped_leader_gets_no_group_signal(self):
        tree, pidfile = self._spawn()
        tree.proc.wait(GONE_S)                       # a stray reap behind the handle's back
        _leader, grandchild = _read_pids(pidfile)
        sent = []
        real = os.killpg
        with mock.patch.object(os, "killpg", lambda g, s: (sent.append(g), real(g, s))):
            ended = tree.end(leftovers_only=True)
        self.assertEqual([], sent, "a group was signalled after its leader was reaped")
        self.assertTrue(ended.leftovers_unknown)
        self.assertTrue(_alive(grandchild), "said, not guessed: the leftover was not reached")


class TreeEndsOnce(unittest.TestCase):
    """Row 22. The second `end` signals nothing; two threads ending / closing at once send one
    kill and raise nothing (the handle's lock, refute L3)."""

    @unittest.skipIf(NT, "process groups are POSIX")
    def test_posix_second_end_and_concurrent_end_close_signal_once(self):
        tree = Q.spawn_jobbed([sys.executable, "-c", "import time; time.sleep(600)"],
                              stdin=subprocess.DEVNULL)
        sent = []
        real = os.killpg
        gate = threading.Barrier(3)
        errors = []

        def hit(f):
            gate.wait()
            try:
                f()
            except BaseException as exc:  # noqa: BLE001 — the assertion is that nothing raises
                errors.append(exc)
        with mock.patch.object(os, "killpg", lambda g, s: (sent.append(g), real(g, s))):
            ts = [threading.Thread(target=hit, args=(f,)) for f in (tree.end, tree.close)]
            for t in ts:
                t.start()
            gate.wait()
            for t in ts:
                t.join(GONE_S)
            tree.end()
            tree.end(leftovers_only=True)
        self.assertEqual([], errors)
        self.assertEqual([tree.proc.pid], sent)

    def test_windows_job_second_end_and_concurrent_end_close_terminate_once(self):
        k32 = _Kernel32()
        tree = _inj_spawn(k32, [sys.executable, "-c", "import time; time.sleep(600)"])
        gate = threading.Barrier(3)
        errors = []

        def hit(f):
            gate.wait()
            try:
                f()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
        ts = [threading.Thread(target=hit, args=(f,)) for f in (tree.end, tree.close)]
        for t in ts:
            t.start()
        gate.wait()
        for t in ts:
            t.join(GONE_S)
        tree.end()
        tree.close()
        self.assertEqual([], errors)
        self.assertEqual(1, sum(1 for c in k32.calls if c[0] == "TerminateJobObject"))
        self.assertEqual(1, sum(1 for c in k32.calls if c[0] == "CloseHandle"))


# ======================================================================== rows 25-34 · runner
def _exe(d, name, body="#!/bin/sh\nexit 0\n"):
    path = os.path.normpath(os.path.join(d, name))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path


class ExeFold(unittest.TestCase):
    """Rows 25, 38. `.EXE` / `.COM` are always tried (CreateProcess ignores PATHEXT), on every OS,
    and the shown spelling is the disk's."""

    @staticmethod
    def _folds(d):
        probe = _exe(d, "fold-probe.exe")
        try:
            return os.path.isfile(probe[:-4] + ".EXE")
        finally:
            os.remove(probe)

    def test_an_upper_case_pathext_finds_the_lower_case_file_and_shows_the_disk_spelling(self):
        from mokata.exe_path import which_in
        d = _tmp(self)
        if not self._folds(d):
            self.skipTest("a case-sensitive file system cannot stand in for Windows' case fold "
                          "(run on macOS and on the Windows probe)")
        _exe(d, "node.exe")
        self.assertEqual(os.path.join(d, "node.exe"),
                         which_in("node", [d], nt=True, pathext=".EXE"))

    def test_a_pathext_without_exe_still_finds_a_real_program(self):
        from mokata.exe_path import which_in
        d = _tmp(self)
        name = "node.exe" if self._folds(d) else "node.EXE"   # the appended spelling, unfolded
        _exe(d, name)
        _exe(d, "node.bat")
        self.assertEqual(os.path.join(d, name),
                         which_in("node", [d], nt=True, pathext=".PY;.BAT"))
        self.assertIsNone(which_in("x", [d], nt=True, pathext=".PY;.BAT"))


class WinRunnable(unittest.TestCase):
    """Rows 26-27 (R5 allow-list). Every batch / stream / script / device spelling is refused
    BEFORE anything starts, with the remedy; extensionless is accepted only with `name.exe` on
    disk, and that resolved file is what runs."""

    BAD = ("run.cmd.", "run.cmd..", "run.bat ", "RUN~1.CMD", "run.cmd::$DATA", "run.CMD::$data",
           "x.ps1", "CON", "nul .exe", "COM\xb9")

    def test_every_batch_or_alias_spelling_is_refused_bare_and_explicit(self):
        from mokata.ide.review_context import resolve_runner
        root = _tmp(self)
        for name in self.BAD:
            for spelled in (name, "./" + name):
                head, why = resolve_runner(root, spelled, root, nt=True)
                self.assertIsNone(head, spelled)
                self.assertTrue(why, spelled)

    def test_the_preflight_says_runnable_false_and_the_rerun_never_starts_it(self):
        from mokata.ide import review_context as RC
        root = _tmp(self)
        mark = os.path.join(root, "BATCH_RAN")
        _exe(root, "run.cmd", f"#!/bin/sh\ntouch '{mark}'\nexit 1\n")
        _write_manifest(root, "./run.cmd. -q")
        pre = RC.test_command(root, nt=True)
        self.assertFalse(pre["runnable"], pre)
        self.assertIn("batch file", pre["note"])
        with mock.patch.object(RC.os, "name", "nt"):
            head, why = RC.resolve_runner(root, "./run.cmd.", root, nt=True)
        self.assertIsNone(head)
        res = RC.run_tests(root, ["./run.cmd."], ["t1"])
        self.assertNotEqual("red", res["tests"][0]["state"], "a test that never ran read RED")
        self.assertFalse(os.path.exists(mark))

    def test_extensionless_is_accepted_only_with_the_exe_on_disk(self):
        from mokata.ide.review_context import resolve_runner
        root = _tmp(self)
        exe = _exe(root, "tools/x.exe")
        _exe(root, "tools/y.bat")
        self.assertEqual(([exe], None), resolve_runner(root, "./tools/x", root, nt=True))
        self.assertEqual(([exe], None), resolve_runner(root, "./tools/x.", root, nt=True))
        head, why = resolve_runner(root, "./tools/y", root, nt=True)
        self.assertIsNone(head)
        self.assertIn("y.bat", why)
        head, why = resolve_runner(root, "./tools/z", root, nt=True)
        self.assertIsNone(head)
        self.assertIn("no `z.exe`", why)

    def test_a_stream_is_named_a_stream_even_when_it_ends_exe(self):
        """`notes.txt:evil.exe` ends `.exe` — only the stream rule stands between it and a run."""
        from mokata.exe_path import win_refusal
        for name in ("notes.txt:evil.exe", "tools\\x:y.com", "run.cmd::$DATA", "run.CMD::$data"):
            self.assertEqual("stream", (win_refusal(name) or ("none",))[0], name)

    def test_verbatim_unc_and_drive_relative_runners_are_refused(self):
        from mokata.exe_path import win_refusal
        for name, kind in (("\\\\?\\C:\\t\\x.exe", "verbatim"), ("\\\\srv\\s\\x.exe", "unc"),
                           ("C:x.exe", "drive-relative")):
            self.assertEqual(kind, win_refusal(name)[0], name)

    @unittest.skipUnless(NT, "probe: a real run.cmd on Windows")
    def test_on_windows_a_real_batch_runner_is_never_started(self):
        from mokata.ide import review_context as RC
        root = _tmp(self)
        mark = os.path.join(root, "BATCH_RAN")
        for name in ("run.cmd", "run.bat"):
            _script(root, name, f'@echo off\r\necho x> "{mark}"\r\nexit /b 1\r\n')
        for spelled in ("run.cmd.", "run.bat ", "run.cmd::$DATA", ".\\run.cmd.", ".\\run"):
            res = RC.run_tests(root, [spelled], ["t1"])
            self.assertEqual("not-run", res["tests"][0]["state"], (spelled, res["tests"]))
        self.assertFalse(os.path.exists(mark), "a batch file ran")

    @unittest.skipUnless(NT, "probe: real extensionless resolution on Windows")
    def test_on_windows_extensionless_runs_the_exe_beside_it(self):
        from mokata.ide import review_context as RC
        root = _tmp(self)
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        os.makedirs(os.path.join(root, "tools"))
        shutil.copy(os.path.join(sysdir, "whoami.exe"), os.path.join(root, "tools", "x.exe"))
        _script(os.path.join(root, "tools"), "y.bat", "@exit /b 0\r\n")
        head, why = RC.resolve_runner(root, ".\\tools\\x", None)
        self.assertEqual([os.path.join(root, "tools", "x.exe")], head, why)
        rc, out, _ = Q.run_jobbed(head, timeout=120)
        self.assertEqual(0, rc)
        head, why = RC.resolve_runner(root, ".\\tools\\y", None)
        self.assertIsNone(head, why)


def _write_manifest(root, cmd):
    os.makedirs(os.path.join(root, ".mokata"), exist_ok=True)
    with open(os.path.join(root, ".mokata", "manifest.json"), "w", encoding="utf-8") as fh:
        data = _support.sample_manifest_data()
        data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = cmd
        json.dump(data, fh)


class RelativeRunner(unittest.TestCase):
    """Row 28 (refute M4). An explicit relative runner is joined to the REPO ROOT before anything
    else: CreateProcess resolves a relative image against the parent's cwd. With the parent's cwd
    elsewhere and a same-named decoy there, the file under the root is the one that runs."""

    def _cwd(self, d):
        old = os.getcwd()
        os.chdir(d)
        self.addCleanup(os.chdir, old)

    @unittest.skipIf(NT, "POSIX scripts")
    def test_the_runner_under_the_root_runs_not_the_decoy_in_the_parents_cwd(self):
        from mokata.ide import review_context as RC
        root, elsewhere = _tmp(self), _tmp(self)
        out = os.path.join(root, "WHO")
        _exe(root, "tools/runner", f"#!/bin/sh\necho ROOT > '{out}'\nexit 0\n")
        _exe(elsewhere, "tools/runner", f"#!/bin/sh\necho DECOY > '{out}'\nexit 0\n")
        self._cwd(elsewhere)
        head, why = RC.resolve_runner(root, "tools/runner", "/usr/bin:/bin")
        self.assertEqual([os.path.join(root, "tools", "runner")], head, why)
        res = RC.run_tests(root, ["tools/runner"], ["t1"])
        self.assertEqual("green", res["tests"][0]["state"], res)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual("ROOT", fh.read().strip())

    @unittest.skipUnless(NT, "probe: CreateProcess's relative-image rule")
    def test_on_windows_the_runner_under_the_root_runs_not_the_decoy(self):
        from mokata.ide import review_context as RC
        root, elsewhere = _tmp(self), _tmp(self)
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        for base, src in ((root, "whoami.exe"), (elsewhere, "hostname.exe")):
            os.makedirs(os.path.join(base, "tools"))
            shutil.copy(os.path.join(sysdir, src), os.path.join(base, "tools", "runner.exe"))
        self._cwd(elsewhere)
        head, why = RC.resolve_runner(root, "tools\\runner.exe", None)
        self.assertEqual([os.path.join(root, "tools", "runner.exe")], head, why)
        rc, out, _ = Q.run_jobbed(head, timeout=120)
        hostname = subprocess.run([os.path.join(sysdir, "hostname.exe")], capture_output=True,
                                  text=True, timeout=120).stdout.strip()
        self.assertEqual(0, rc)
        self.assertNotEqual(hostname.lower(), out.strip().lower(), "the decoy ran")
        self.assertIn("\\", out, "whoami prints domain\\user")


class JsKeysFold(unittest.TestCase):
    """Row 29. On Windows `NPM` / `Npm` / `npm.CMD` take the JS route (node + npm's own entry);
    a bare `npm.cmd` whose JS entry is missing gets the shim message with the remedy."""

    def _prefix(self, with_js=True):
        prefix = _tmp(self, "mk-npm-")
        _exe(prefix, "npm.cmd", "@echo off\r\n")
        _exe(prefix, "node", "#!/bin/sh\nexit 0\n")
        if with_js:
            _script(os.path.join(prefix), "node_modules/npm/package.json".replace("/", os.sep)
                    if False else "x", "")
            os.makedirs(os.path.join(prefix, "node_modules", "npm", "bin"), exist_ok=True)
            _script(os.path.join(prefix, "node_modules", "npm"), "package.json",
                    json.dumps({"name": "npm", "bin": {"npm": "bin/npm-cli.js"}}))
            _script(os.path.join(prefix, "node_modules", "npm", "bin"), "npm-cli.js", "")
        return prefix

    def test_case_and_cmd_spellings_take_the_js_route(self):
        from mokata.ide.review_context import resolve_runner
        prefix = self._prefix()
        root = _tmp(self)
        with mock.patch("mokata.ide.lsp.which_in",
                        lambda name, dirs, **k: os.path.join(prefix, "node")
                        if name == "node" else None):
            for name in ("npm", "NPM", "Npm", "npm.CMD"):
                head, why = resolve_runner(root, name, prefix, nt=True)
                self.assertIsNotNone(head, (name, why))
                self.assertTrue(head[1].endswith("npm-cli.js"), (name, head))

    def test_a_bare_cmd_without_its_js_entry_gets_the_shim_message(self):
        from mokata.ide.review_context import resolve_runner
        prefix = self._prefix(with_js=False)
        head, why = resolve_runner(_tmp(self), "npm.cmd", prefix, nt=True)
        self.assertIsNone(head)
        self.assertIn("batch file", why)
        self.assertIn("settings.baseline.test_command", why)


class CorepackEvidence(unittest.TestCase):
    """Row 30. corepack supplies pnpm/yarn only when the USER enabled it — evidence is a pnpm shim
    on a PATH dir outside the repo AND outside the project env; a shim the repo commits into
    `.venv/Scripts` is not evidence. Not enabled: the note names `corepack enable`."""

    def _corepack(self):
        prefix = _tmp(self, "mk-corepack-")
        _exe(prefix, "node", "#!/bin/sh\nexit 0\n")
        os.makedirs(os.path.join(prefix, "node_modules", "corepack", "dist"))
        _script(os.path.join(prefix, "node_modules", "corepack"), "package.json",
                json.dumps({"name": "corepack", "bin": {"pnpm": "dist/pnpm.js"}}))
        _script(os.path.join(prefix, "node_modules", "corepack", "dist"), "pnpm.js", "")
        return prefix

    def _resolve(self, root, path, bins=()):
        from mokata.ide.review_context import resolve_runner
        with mock.patch("mokata.ide.lsp.which_in",
                        lambda name, dirs, **k: next((os.path.join(d, "node") for d in dirs
                                                      if os.path.exists(os.path.join(d, "node"))),
                                                     None) if name == "node" else None):
            return resolve_runner(root, "pnpm", path, nt=True, project_bins=bins)

    def test_not_enabled_runs_nothing_and_says_corepack_enable(self):
        root = _tmp(self)
        prefix = self._corepack()
        head, why = self._resolve(root, prefix)
        self.assertIsNone(head)
        self.assertIn("corepack enable pnpm", why)
        self.assertIn("npm install -g pnpm", why)

    def test_a_shim_planted_in_the_repos_env_is_not_evidence(self):
        root = _tmp(self)
        prefix = self._corepack()
        scripts = os.path.join(root, ".venv", "Scripts")
        _exe(scripts, "pnpm.cmd", "@echo off\r\n")
        head, why = self._resolve(root, os.pathsep.join([scripts, prefix]), bins=[scripts])
        self.assertIsNone(head, "a committed shim switched corepack on")

    def test_an_enabled_shim_outside_the_repo_lets_corepack_supply_it(self):
        root = _tmp(self)
        prefix = self._corepack()
        _exe(prefix, "pnpm.cmd", "@echo off\r\n")
        head, why = self._resolve(root, prefix)
        self.assertIsNotNone(head, why)
        self.assertTrue(head[1].endswith("pnpm.js"))


class RefusalWords(unittest.TestCase):
    """Row 31. A non-JS batch refusal (Maven) never suggests `node`."""

    def test_maven_refusal_has_no_node_example(self):
        from mokata.ide.review_context import resolve_runner
        d = _tmp(self)
        _exe(d, "mvn.cmd", "@echo off\r\n")
        head, why = resolve_runner(_tmp(self), "mvn", d, nt=True)
        self.assertIsNone(head)
        self.assertIn("mvn.cmd", why)
        self.assertNotIn("node", why)


class RunnerFromProjectEnv(unittest.TestCase):
    """Rows 32-33 (R4). A bare runner comes from the project env first — with NO trust record —
    then from PATH entries outside the repo; a repo file on PATH never answers, and when it was the
    only hit the note names it. The poetry preflight claim needs a poetry outside the repo."""

    def _bin(self):
        return "Scripts" if NT else "bin"

    def _runner(self, d, name="pytest"):
        if NT:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, name + ".exe")
            shutil.copy(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                     "whoami.exe"), path)
            return path
        return _exe(d, name)

    def test_the_project_envs_runner_runs_with_no_trust_record(self):
        from mokata.ide import review_context as RC
        from mokata.ide import trust
        root = _tmp(self)
        env_bin = os.path.join(root, ".venv", self._bin())
        exe = self._runner(env_bin)
        head, why = RC.resolve_runner(root, "pytest", env_bin, project_bins=[env_bin])
        self.assertEqual([exe], head, why)
        self.assertFalse(trust.status(root)["trusted"], "the fixture must be untrusted")

    def test_a_repo_file_on_path_never_answers_and_is_named_when_it_was_the_only_hit(self):
        from mokata.ide import review_context as RC
        root, outside = _tmp(self), _tmp(self)
        planted = self._runner(os.path.join(root, "tools"))
        head, why = RC.resolve_runner(root, "pytest", os.path.join(root, "tools"))
        self.assertIsNone(head)
        self.assertIn(planted, why)
        self.assertIn("inside the repository", why)
        self.assertNotIn("trust", why.lower())
        real = self._runner(outside)
        head, why = RC.resolve_runner(root, "pytest",
                                      os.pathsep.join([os.path.join(root, "tools"), outside]))
        self.assertEqual([real], head, why)

    def test_a_poetry_planted_in_the_repo_makes_no_preflight_claim(self):
        from mokata.ide import review_context as RC
        root = _tmp(self)
        _write_manifest(root, "pytest -q")
        _script(root, "pyproject.toml", "[tool.poetry]\nname = 'x'\n")
        tools = os.path.join(root, "tools")
        self._runner(tools, "poetry")
        with mock.patch.dict(os.environ, {"PATH": tools, "VIRTUAL_ENV": "", "CONDA_PREFIX": ""}):
            pre = RC.test_command(root)
        self.assertFalse(pre["runnable"], pre)
        self.assertNotIn("poetry/pipenv environment", pre["note"])


class CommandStrip(unittest.TestCase):
    """Row 34. A trailing newline in `test_command` is not shell syntax; an inner one still is."""

    def test_trailing_newline_is_stripped_inner_newline_refused(self):
        from mokata.ide import review_context as RC
        root = _tmp(self)
        _write_manifest(root, f"{sys.executable} -m pytest -q\n")
        pre = RC.test_command(root, nt=True)
        self.assertNotIn("shell syntax", pre["note"])
        _write_manifest(root, f"{sys.executable} -m pytest\n-q")
        self.assertIn("shell syntax", RC.test_command(root, nt=True)["note"])


# ===================================================================== rows 36-39 · lsp lookups
class UriDevices(unittest.TestCase):
    """Row 36. A `file:` URI naming a device or an alias Windows reads as another file is no file
    of the repo (CPython 3.13's `_isreservedname`, ported)."""

    def test_device_and_alias_spellings_are_not_local_files(self):
        from mokata.ide.lsp import from_uri
        for tail in ("src/CON", "CON%20.txt", "nul%20.py", "COM%C2%B9", "LPT%C2%B2.txt", "a.py.",
                     "a.py%20", "CONOUT$", "aux.py"):
            self.assertIsNone(from_uri("file:///c:/repo/" + tail, nt=True), tail)
        self.assertEqual("C:\\repo\\console.py", from_uri("file:///c:/repo/console.py", nt=True))
        self.assertEqual("C:\\repo\\COM10.py", from_uri("file:///c:/repo/COM10.py", nt=True))


class ShimOnlyServer(unittest.TestCase):
    """Row 37. A user-configured server present only as `.cmd`/`.bat`/`.ps1` is "found only as
    …, never run, name the real executable" — never "not on PATH"."""

    def test_a_shim_only_server_is_named_and_not_run(self):
        from mokata.ide import lsp as L
        home, bindir, root = _tmp(self), _tmp(self), _tmp(self)
        os.makedirs(os.path.join(home, ".mokata"))
        _script(os.path.join(home, ".mokata"), "lsp.json", json.dumps(
            {"languages": {"mylang": {"exts": [".my"], "servers": [["myls", [], ""]]}}}))
        for ext in (".cmd", ".bat", ".ps1"):
            d = _tmp(self)
            _exe(d, "myls" + ext, "@echo off\r\n")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home, "PATH": d}):
                got = L.find_server(root, "mylang")
            self.assertFalse(got["found"])
            self.assertIn("myls" + ext, got["note"], ext)
            self.assertIn("never runs a .cmd/.bat/.ps1", got["note"])
            self.assertNotIn("on PATH", got["note"])
        self.assertTrue(bindir)


class GlobalStores(unittest.TestCase):
    """Row 39. pnpm (`<PNPM_HOME>/global/<n>/node_modules`) and yarn v1 (`…/Yarn/bin` ->
    `…/Yarn/Data/global/node_modules`) global installs are found through PATH, as npm's are."""

    def _pkg(self, modules, pkg, binname):
        d = os.path.join(modules, pkg)
        os.makedirs(os.path.join(d, "lib"), exist_ok=True)
        _script(d, "package.json", json.dumps({"name": pkg, "bin": {binname: "lib/cli.js"}}))
        _script(os.path.join(d, "lib"), "cli.js", "")
        return os.path.join(d, "lib", "cli.js")

    def test_pnpm_and_yarn_v1_global_stores_resolve_a_server(self):
        from mokata.ide import lsp as L
        root, base = _tmp(self), _tmp(self)
        node_dir = os.path.join(base, "nodebin")
        _exe(node_dir, "node.exe" if NT else "node")
        pnpm_home = os.path.join(base, "pnpm")
        ts = self._pkg(os.path.join(pnpm_home, "global", "5", "node_modules"),
                       "typescript-language-server", "typescript-language-server")
        yarn_bin = os.path.join(base, "Yarn", "bin")
        os.makedirs(yarn_bin)
        pr = self._pkg(os.path.join(base, "Yarn", "Data", "global", "node_modules"),
                       "pyright", "pyright-langserver")
        env = {"PATH": os.pathsep.join([pnpm_home, yarn_bin, node_dir])}
        head, why = L._global_node_package_bin(root, "typescript-language-server", environ=env)
        self.assertEqual(os.path.realpath(ts), head[1] if head else why)
        head, why = L._global_node_package_bin(root, "pyright-langserver", environ=env)
        self.assertEqual(os.path.realpath(pr), head[1] if head else why)


# ======================================================================= row 40 · TerminalHost
class NoHalfBuiltHost(unittest.TestCase):
    """Row 40. No test builds a `TerminalHost` with `__new__` (a half-built object whose reaper
    thread dies reading a field `__init__` never set)."""

    # CORPUS: THE WORKING TREE — every test file present, committed or not.
    def test_no_test_half_builds_a_terminal_host(self):
        bad = []
        for f in sorted(os.listdir(HERE)):
            if not f.endswith(".py"):
                continue
            with open(os.path.join(HERE, f), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                        and n.func.attr == "__new__" and n.args \
                        and "TerminalHost" in ast.dump(n.args[0]):
                    bad.append(f"{f}:{n.lineno}")
        self.assertEqual([], bad)

    def test_the_explain_hand_off_test_raises_nothing_on_a_background_thread(self):
        import test_p46_change_explainer as P46
        seen = []
        old = threading.excepthook
        threading.excepthook = lambda args: seen.append(args.exc_value)
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(
                "TheTerminalHandOffIsOneSafeArgument."
                "test_the_explain_argument_is_appended_to_the_same_claude_program", P46)
            result = unittest.TestResult()
            suite.run(result)
            time.sleep(0.3)                       # one reaper tick
        finally:
            threading.excepthook = old
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual([], seen)


# ===================================================================== rows 41, 43 · scale
def _handles():
    if NT:
        import ctypes
        n = ctypes.c_ulong()
        k32 = ctypes.WinDLL("kernel32")
        k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n))
        return n.value
    return len(os.listdir("/dev/fd"))


class ManyRunsNoLeak(unittest.TestCase):
    """Row 41. 300 IDE re-runs: handles / fds sampled every 50; growth from run 50 to 300 <= 8
    (a slope, not a count — a per-run leak shows as 250+)."""

    def test_three_hundred_reruns_leak_no_handle(self):
        from mokata.ide import review_context as RC
        d = _tmp(self)
        samples = []

        def on_test(tid, state, at):
            n = int(tid[1:])
            if n % 50 == 0:
                samples.append((n, _handles()))
        ids = [f"t{i}" for i in range(1, 301)]
        trees = []
        real = RC.spawn_jobbed

        def spawn(*a, **k):
            trees.append(real(*a, **k))
            return trees[-1]
        with mock.patch.object(RC, "spawn_jobbed", spawn):
            res = RC.run_tests(d, [sys.executable, "-c", "import sys; sys.exit(0)"], ids,
                               on_test=on_test)
        self.assertEqual(300, res["passed"], res["tests"][-1])
        # Every run released what its tree held (the Job Object handle; macOS's kqueue), not
        # left to a garbage collector — Windows has no collector for a handle.
        held = [t for t in trees if t._job is not None or t._kq is not None]
        self.assertEqual([], held, f"{len(held)} of {len(trees)} runs kept their handle")
        by = dict(samples)
        self.assertLessEqual(by[300] - by[50], 8, samples)


@unittest.skipIf(NT and not shutil.which("ping"), "no ping")
class WideTree(unittest.TestCase):
    """Row 43. One `end` ends a wide tree: a launcher starts 150 minimal sleepers. POSIX: every
    recorded pid is gone. Windows: the job's accounting saw 151 processes and `end` returns with
    `ActiveProcesses == 0`. (The mechanism, not a load claim.)"""

    WIDTH = 150

    def test_one_end_ends_a_hundred_and_fifty_processes(self):
        d = _tmp(self)
        pidfile = os.path.join(d, "pids")
        sleeper = (["ping", "-n", "600", "127.0.0.1"] if NT else ["sleep", "600"])
        script = _script(d, "wide.py", (
            "import os, subprocess, sys\n"
            f"ps = [subprocess.Popen({sleeper!r}, stdout=subprocess.DEVNULL) "
            f"for _ in range({self.WIDTH})]\n"
            f"open({pidfile!r} + '.tmp', 'w').write(' '.join(str(p.pid) for p in ps))\n"
            f"os.replace({pidfile!r} + '.tmp', {pidfile!r})\n"
            "import time; time.sleep(600)\n"))
        tree = Q.spawn_jobbed([sys.executable, script], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(tree.close)
        self.assertTrue(_wait(lambda: os.path.exists(pidfile), within=120))
        pids = _read_pids(pidfile)
        self.assertEqual(self.WIDTH, len(pids))
        if NT:
            total = tree._job.totals()
            print(f"\n[probe] job TotalProcesses = {total}")
            # The launcher, its 150 children, and Windows' own console host for the console
            # programs (conhost.exe, started inside the job): 151 is the floor, measured 152.
            self.assertGreaterEqual(total, self.WIDTH + 1)
            self.assertLessEqual(total, self.WIDTH + 3)
        ended = tree.end()
        self.assertTrue(ended.ok, ended.why)
        if NT:
            self.assertEqual(0, tree._job.active())
        self.assertTrue(_gone(*pids), "one end left part of the tree running")


if __name__ == "__main__":
    unittest.main()
