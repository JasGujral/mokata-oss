"""Verifier Lows-B round-2 attack probe (macOS + Windows). Prints evidence lines 'VLB2 '. Not shipped."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import _support  # noqa: F401
import test_p7_ide_review_context as P7
import test_p21_gated_merge as P21

from mokata import merge_suite as MS

RUN = P7.RUN


def say(*a):
    print("VLB2", *a, flush=True)


def _tmp(case, prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(lambda: shutil.rmtree(d, True))
    return d


# ===================================================================== H1 residuals (injected)
class TreeWatchResiduals(unittest.TestCase):
    def _watch(self, procs, killed, leader=10):
        from mokata.ide.lsp import TreeWatch
        return TreeWatch(leader, table=lambda: [(p, pp) for p, (pp, _c) in procs.items()],
                         created=lambda p: procs[p][1] if p in procs else None,
                         kill=killed.append)

    def test_reused_descendant_pid_whose_new_holder_has_since_exited(self):
        """K (pid 20) recorded; K exits; Y takes pid 20, starts Z, and EXITS. Z still names 20."""
        procs = {10: (1, 100), 20: (10, 110)}
        killed = []
        w = self._watch(procs, killed)
        w.observe()
        procs[20] = (4, 500)          # Y holds pid 20
        procs[30] = (20, 510)         # Y's child Z
        w.observe()                   # Y alive: Z correctly refused here
        del procs[20]                 # Y exits; Z lives on, ppid still 20
        ended = w.end()
        say("H1 dead-new-holder: known=", w.known, "killed=", killed)
        self.assertEqual([], killed, "the child of an unrelated process that reused a dead "
                                     "descendant's pid (and then exited) was ended")

    def test_reused_leader_pid_whose_new_holder_has_since_exited(self):
        procs = {10: (1, 100)}
        killed = []
        w = self._watch(procs, killed)
        del procs[10]
        procs[10] = (4, 500)
        procs[30] = (10, 510)
        del procs[10]                 # the new holder of the leader's pid exits too
        w.end()
        say("H1 dead-new-holder (leader pid): killed=", killed)
        self.assertEqual([], killed)

    def test_pid_reused_between_snapshot_and_creation_read(self):
        """The ToolHelp row (30, parent 20) is a real child of K; between the snapshot and
        created(30) that child exits and pid 30 goes to an unrelated W born later."""
        procs = {10: (1, 100), 20: (10, 110), 30: (20, 120)}
        killed = []
        from mokata.ide.lsp import TreeWatch
        snap = {}

        def table():
            rows = [(p, pp) for p, (pp, _c) in procs.items()]
            procs[30] = (777, 900)    # ... and right after the snapshot, pid 30 is W's
            return rows
        w = TreeWatch(10, table=table, created=lambda p: procs[p][1] if p in procs else None,
                      kill=killed.append)
        w.observe()
        say("H1 snapshot race: known=", w.known)
        self.assertNotIn(30, w.known, "an unrelated process that took a pid after the snapshot "
                                      "was adopted as a descendant")


# ===================================================================== L1 label consistency
class CancelledWithARed(unittest.TestCase):
    def test_a_cancelled_run_with_a_red_is_labelled_failed_by_every_consumer(self):
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        d = _tmp(self, "vlb2-cr-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "red", "rc": 1}], False)
        suite = read_test_record(d, RUN)["last_suite"]
        # proof.js: passed ? "passed" : passed === null ? "did not run every test" : "FAILED"
        label = ("passed" if suite["passed"] else
                 "did not run every test" if suite["passed"] is None else "FAILED")
        say("L1 cancelled+red: passed=", suite["passed"], "failed=", suite.get("failed"),
            "proof.js label=", label)
        self.assertEqual("FAILED", label, "Proof says a run with a failing test 'did not run "
                                          "every test' while the Stop gate says FAILED")

    def test_not_run_with_a_red_is_labelled_failed_too(self):
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        d = _tmp(self, "vlb2-nr-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "red", "rc": 1},
                                      {"id": "t_b", "state": "not-run"}], True)
        suite = read_test_record(d, RUN)["last_suite"]
        say("L1 not-run+red: passed=", suite["passed"], "failed=", suite.get("failed"))
        self.assertIsNot(suite["passed"], None)


# ===================================================================== L4 false refusal
class StreamSpellingFalseRefusal(unittest.TestCase):
    def test_extended_length_and_device_namespace_paths_are_not_streams(self):
        from mokata.exe_path import is_stream
        bad = [p for p in ("\\\\?\\C:\\tools\\py.exe", "\\\\.\\C:\\tools\\py.exe",
                           "//?/C:/tools/py.exe") if is_stream(p)]
        say("L4 extended-length paths read as streams:", bad)
        self.assertEqual([], bad)


# ===================================================================== M2 output/exit fidelity
LEADER_BIG = r'''
import subprocess, sys
kid = subprocess.Popen([sys.executable, "-c", "import time\ntime.sleep(300)"])
open(sys.argv[1], "w").write(str(kid.pid))
line = "x" * 99 + "\n"
for i in range(50000):
    sys.stdout.write(line)
sys.stdout.flush()
sys.exit(3)
'''

ESCAPEE = r'''
import os, subprocess, sys
kw = {"start_new_session": True} if os.name != "nt" else {}
kid = subprocess.Popen([sys.executable, "-c",
    "import time\nfor i in range(400):\n    print('esc', i, flush=True); time.sleep(0.3)\n"], **kw)
open(sys.argv[1], "w").write(str(kid.pid))
print("suite done", flush=True)
'''


class MergeSuiteFidelity(P21._Merge):
    def _setcmd(self, wt_files, script_name, pidfile):
        wt = self.session(files=wt_files)
        mf = os.path.join(self.main, ".mokata", "manifest.json")
        with open(mf, encoding="utf-8") as fh:
            data = json.load(fh)
        data["settings"]["baseline"]["test_command"] = f"{sys.executable} {script_name} {pidfile}"
        with open(mf, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return wt

    def _kill(self, pid):
        if pid is None:
            return
        if os.name == "nt":
            subprocess.run([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                                         "taskkill.exe"), "/F", "/PID", str(pid)],
                           capture_output=True)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    def _alive(self, pid):
        if os.name == "nt":
            from mokata.ide.lsp import _win_created
            return _win_created(pid) is not None and _still_running_nt(pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        return bool(r.stdout.strip()) and not r.stdout.strip().startswith("Z")

    def test_output_complete_and_exit_code_faithful_with_a_leftover(self):
        pidfile = os.path.join(_tmp(self, "vlb2-pid-"), "kid.pid")
        wt = self._setcmd({"src/a.py": "A = 1\n", "big_suite.py": LEADER_BIG}, "big_suite.py",
                          pidfile)
        t0 = time.time()
        res = MS.run_full_suite(self.main, wt)
        took = time.time() - t0
        pid = int(open(pidfile).read()) if os.path.exists(pidfile) else None
        alive = pid is not None and self._alive(pid)
        self._kill(pid)
        nl = 2 if os.name == "nt" else 1
        say("M2 fidelity: exit=", res["exit_code"], "passed=", res["passed"], "bytes=",
            res["output_bytes"], "expected=", 50000 * (99 + nl), "took=", round(took, 1),
            "leftover alive after=", alive)
        self.assertEqual(3, res["exit_code"])
        self.assertFalse(res["passed"])
        self.assertEqual(50000 * (99 + nl), res["output_bytes"], "output truncated")
        self.assertFalse(alive, "the quiet leftover outlived the suite run")

    def test_a_leftover_that_leaves_the_group_and_keeps_writing(self):
        """POSIX: setsid()ed grandchild (escapes killpg) on the inherited pipe. Windows: a plain
        child (the job holds it)."""
        pidfile = os.path.join(_tmp(self, "vlb2-pid-"), "kid.pid")
        wt = self._setcmd({"src/a.py": "A = 1\n", "esc_suite.py": ESCAPEE}, "esc_suite.py",
                          pidfile)
        out = {}

        def go():
            try:
                out["res"] = MS.run_full_suite(self.main, wt)
            except Exception as exc:  # pragma: no cover
                out["exc"] = exc
        t0 = time.time()
        th = threading.Thread(target=go, daemon=True)
        th.start()
        th.join(90)
        took = time.time() - t0
        hung = th.is_alive()
        pid = int(open(pidfile).read()) if os.path.exists(pidfile) else None
        self._kill(pid)
        th.join(30)
        say("M2 escapee chatty: hung=", hung, "after", round(took, 1), "s; returned after kill=",
            not th.is_alive(), out.get("exc"))
        self.assertFalse(hung, "a leftover that left the process group and kept writing kept the "
                               "gated merge's suite run alive")


def _still_running_nt(pid):
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259


# ===================================================================== Windows: real creation times
@unittest.skipUnless(os.name == "nt", "Windows probe")
class WinCreationTimes(unittest.TestCase):
    def test_creation_time_resolution(self):
        from mokata.ide.lsp import _win_created
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        exe = os.path.join(sysdir, "whoami.exe")
        times = []
        for _ in range(60):
            p = subprocess.Popen([exe], stdout=subprocess.DEVNULL, creationflags=0x4)  # suspended
            times.append(_win_created(p.pid))
            p.kill()
            p.wait()
        diffs = sorted({b - a for a, b in zip(times, times[1:])})
        dup = len(times) - len(set(times))
        say("WIN creation times: n=", len(times), "duplicates=", dup, "min diffs (100ns)=",
            diffs[:8], "mod 156250 set=", sorted({t % 10000 for t in times})[:10])

    def test_real_pid_reuse_dead_holder(self):
        """K is a real grandchild recorded by a real TreeWatch; K exits; we try to land a new
        python Y on K's pid; Y starts Z and exits; does end() kill Z?"""
        from mokata.ide.lsp import TreeWatch, _win_created
        d = _tmp(self, "vlb2-reuse-")
        kfile = os.path.join(d, "k.pid")
        leader_src = ("import subprocess, sys, time\n"
                      "k = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])\n"
                      "open(sys.argv[1], 'w').write(str(k.pid))\n"
                      "k.wait(); del k\n"
                      "time.sleep(600)\n")
        leader = subprocess.Popen([sys.executable, "-c", leader_src, kfile])
        self.addCleanup(lambda: (leader.kill(), leader.wait()))
        w = TreeWatch(leader.pid)
        t0 = time.time()
        while not os.path.exists(kfile) and time.time() - t0 < 60:
            time.sleep(0.05)
        time.sleep(0.3)
        k = int(open(kfile).read())
        for _ in range(40):
            w.observe()
            if k in w.known:
                break
            time.sleep(0.1)
        say("WIN reuse: K=", k, "recorded=", k in w.known)
        while _win_created(k) is not None and time.time() - t0 < 60:
            time.sleep(0.05)
        zfile = os.path.join(d, "z.pid")
        y_src = ("import os, subprocess, sys\n"
                 "if os.getpid() == int(sys.argv[1]):\n"
                 "    z = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                 "    open(sys.argv[2], 'w').write(str(z.pid))\n")
        tries, hit = 0, False
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        t1 = time.time()
        while time.time() - t1 < 240 and not hit:
            # cheap suspended probes to see whether K's pid comes back; launch Y only on a hit
            p = subprocess.Popen([os.path.join(sysdir, "whoami.exe")], stdout=subprocess.DEVNULL,
                                 creationflags=0x4)
            tries += 1
            got = p.pid
            p.kill(); p.wait(); p._handle.Close()
            if got == k:
                # the pid is free again right now: the next process likely gets it
                y = subprocess.Popen([sys.executable, "-c", y_src, str(k), zfile])
                y.wait()
                ypid = y.pid
                y._handle.Close()
                hit = os.path.exists(zfile)
                say("WIN reuse: probe hit after", tries, "tries; Y pid=", ypid, "Y took K pid=", hit)
        if not hit:
            say("WIN reuse: K's pid never came back to Y in", tries, "tries")
            self.skipTest("pid reuse not reproduced")
        z = int(open(zfile).read())
        self.addCleanup(lambda: subprocess.run([os.path.join(sysdir, "taskkill.exe"), "/F", "/PID",
                                                str(z)], capture_output=True))
        ended = w.end()
        say("WIN reuse: Z=", z, "ended=", ended, "Z killed=", z in ended)
        self.assertNotIn(z, ended, "a real unrelated process's child was killed")


if __name__ == "__main__":
    unittest.main()
