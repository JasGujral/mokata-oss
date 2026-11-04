"""Verifier Lows-B attack probe (macOS + Windows). Prints evidence lines starting 'VLB '. Not shipped."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401
import test_p7_ide_review_context as P7

RUN = P7.RUN


def say(*a):
    print("VLB", *a, flush=True)


def _tmp(case, prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(lambda: shutil.rmtree(d, True))
    return d


def _exe(directory, name):
    os.makedirs(directory, exist_ok=True)
    n = name + (".exe" if os.name == "nt" else "")
    p = os.path.join(directory, n)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(p, 0o755)
    return p


class TrustRule(unittest.TestCase):
    """Item 7: the stated rule is 'a runner whose realpath is INSIDE the repo runs only from the
    project's env AND trusted; anything else inside the repo is refused whatever the trust'."""

    def setUp(self):
        home = _tmp(self, "vlb-home-")
        p = mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home})
        p.start()
        self.addCleanup(p.stop)

    def _repo(self, cmd):
        from mokata.init import init_repo
        d = _tmp(self, "vlb-repo-")
        init_repo(root=d, profile="standard", assume_yes=True, out=P7._silent)
        P7._set_command(d, cmd)
        return d

    def test_explicit_relative_path_into_repo_bypasses_trust(self):
        from mokata.ide import review_context as RC
        from mokata.ide import trust
        for spelled in ("tools/mk-planted", "./tools/mk-planted", ".venv/bin/pytest"):
            d = self._repo(spelled + " -q")
            _exe(os.path.join(d, "tools"), "mk-planted")
            _exe(os.path.join(d, ".venv", "bin"), "pytest")
            tc = RC.test_command(d)
            head, why = RC.resolve_runner(d, spelled, os.environ.get("PATH"))
            say("explicit", repr(spelled), "trusted=", trust.status(d)["trusted"],
                "runnable=", tc["runnable"], "head=", head, "why=", why)
            with self.subTest(spelled=spelled):
                self.assertFalse(tc["runnable"] and head is not None,
                                 f"{spelled}: an in-repo runner runs untrusted when written as a path")

    def test_bare_venv_runner_symlinked_out_of_repo_needs_no_trust(self):
        if os.name == "nt":
            self.skipTest("symlink probe is POSIX")
        from mokata.ide import review_context as RC
        d = self._repo("pytest -q")
        b = os.path.join(d, ".venv", "bin")
        os.makedirs(b)
        os.symlink("/bin/echo", os.path.join(b, "pytest"))
        head, why = RC.resolve_runner(d, "pytest", b, project_bins=[b])
        say("symlink .venv/bin/pytest -> /bin/echo:", head, why)
        self.assertIsNone(head, "the repo chose /bin/echo as the runner with no trust")


class CancelledAllGreen(unittest.TestCase):
    def test_cancelled_after_greens_stop_gate_says(self):
        from mokata import stop_gate
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        d = _tmp(self, "vlb-cancel-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "green", "rc": 0}], False)
        suite = read_test_record(d, RUN)["last_suite"]
        reasons = " | ".join(stop_gate.missing_evidence(d, RUN))
        say("cancelled-all-green suite:", {k: suite.get(k) for k in ("passed", "reason", "not_run", "failed")})
        say("cancelled-all-green stop gate:", reasons)
        self.assertNotIn("FAILED", reasons, "a cancelled run in which nothing failed reads FAILED")


class TreeWatchReuse(unittest.TestCase):
    def test_child_of_a_process_that_reused_a_dead_descendants_pid_is_spared(self):
        from mokata.ide.lsp import TreeWatch
        # t=100 leader L=10; t=110 descendant K=20 (child of L)
        procs = {10: (1, 100), 20: (10, 110)}
        killed = []
        w = TreeWatch(10, table=lambda: [(p, pp) for p, (pp, _c) in procs.items()],
                      created=lambda p: procs[p][1] if p in procs else None,
                      kill=killed.append)
        w.observe()
        self.assertIn(20, w.known)
        # K exits; an UNRELATED process Y (parent 4, the system) is created with K's pid at t=500,
        # and Y starts its own child Z=30 at t=510.
        procs[20] = (4, 500)
        procs[30] = (20, 510)
        ended = w.end()
        say("pid reuse: known=", w.known, "ended=", ended, "killed=", killed)
        self.assertNotIn(30, killed, "a child of an unrelated process that reused a dead "
                                     "descendant's pid was KILLED")


class FromUriDevices(unittest.TestCase):
    def test_device_spellings(self):
        from mokata.ide.lsp import from_uri
        out = {}
        for n in ("CON .txt", "nul .py", "COM\u00b9", "LPT\u00b2.txt", "CON", "con.txt", "aux.py",
                  "a.py::$DATA", "a.py.", "a.py ", "COM0", "LPT0"):
            from urllib.parse import quote
            out[n] = from_uri("file:///C:/repo/" + quote(n), nt=True)
        say("from_uri:", out)
        missed = [n for n in ("CON .txt", "nul .py", "COM\u00b9", "LPT\u00b2.txt") if out[n]]
        self.assertEqual([], missed, "device names read as files")


class SameStat(unittest.TestCase):
    def test_ino_zero_volumes_are_not_one_directory(self):
        from mokata import exe_path
        a = _tmp(self, "vlb-a-")
        b = _tmp(self, "vlb-b-")
        real = os.stat

        def fake(p, *k, **kw):
            st = real(p, *k, **kw)
            return os.stat_result((st.st_mode, 0, st.st_dev) + tuple(st)[3:])
        with mock.patch.object(exe_path.os, "stat", fake):
            same = exe_path.same_place(a, b)
            under = exe_path._under(os.path.join(a, "x"), b)
        say("st_ino=0 (FAT/SMB style): same_place=", same, "_under=", under)
        self.assertFalse(same or under, "with no file ids every directory on a volume is 'the same'")


if __name__ == "__main__":
    unittest.main()


def _w(root, name, text):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


@unittest.skipUnless(os.name == "nt", "Windows probe")
class WinSpellings(unittest.TestCase):
    def test_stream_and_short_name_spellings_never_start_cmd_and_never_record_red(self):
        import ctypes
        from mokata import tdd_state
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        bad = []
        for label in ("::$DATA", "CMD::$data", "bat::$DATA", "short", ". .", " .", "::$DATA."):
            d = _tmp(self, "vlbsp-")
            P7.plant(d, tests={"t_ok": ["AC-1"]}, green=["t_ok"], suite=(True, ["t_ok"]))
            mark = os.path.join(d, "HIT.txt")
            body = "@echo off\r\necho %%* >> \"%s\"\r\nexit /b 1\r\n" % mark
            _w(d, "run.cmd", body)
            _w(d, "run.bat", body)
            if label == "short":
                buf = ctypes.create_unicode_buffer(600)
                n = ctypes.windll.kernel32.GetShortPathNameW(os.path.join(d, "run.cmd"), buf, 600)
                spelled = buf.value if n else os.path.join(d, "run.cmd")
            elif label == "CMD::$data":
                spelled = os.path.join(d, "run.CMD::$data")
            elif label == "bat::$DATA":
                spelled = os.path.join(d, "run.bat::$DATA")
            else:
                spelled = os.path.join(d, "run.cmd" + label)
            P7._set_command(d, f'"{spelled}" -q')
            tc = RC.test_command(d)
            before = read_test_record(d, RUN)["last_suite"]
            got = RC.run_tests(d, [spelled, "-q"], ["t_ok"],
                               on_done=lambda res, done: RC.record_rerun(d, RUN, "x", res, done))
            red, _green = tdd_state.load(P7._store(d, RUN), RUN)
            after = read_test_record(d, RUN)["last_suite"]
            t = got["tests"][0]
            ran = os.path.exists(mark)
            say("spell", repr(label), repr(spelled[-40:]), "preflight", tc["runnable"], "| state",
                t["state"], "rc", t.get("rc"), "| cmd ran", ran, "| red", sorted(red),
                "| passed", (after or {}).get("passed"), "changed", before != after, "|",
                (t.get("note") or "")[:140])
            if ran or "t_ok" in red or t["state"] == "red":
                bad.append(label)
        self.assertEqual([], bad, "cmd.exe started or a RED was recorded")


LEADER = r'''
import subprocess, sys
kid = subprocess.Popen([sys.executable, "-c", sys.argv[2]])
open(sys.argv[1], "w").write(str(kid.pid))
print("leader done", flush=True)
'''
CHATTY = "import time,sys\nfor i in range(150):\n    print('tick', i, flush=True); time.sleep(0.3)\n"
QUIET = "import time\ntime.sleep(45)\n"


@unittest.skipUnless(os.name == "nt", "Windows probe")
class WinMergeSuiteSpawn(unittest.TestCase):
    """merge_suite.run_full_suite: Popen(**detached()) + _drive(proc, take, tick) — no Job, no watch."""

    def _alive(self, pid):
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259

    def _run(self, body):
        import subprocess
        import sys
        import threading
        import time
        from mokata.ide import review_context as RC
        from mokata.quiet_spawn import detached
        d = _tmp(self, "vlbms-")
        pidf = os.path.join(d, "kid.pid")
        proc = subprocess.Popen([sys.executable, "-c", LEADER, pidf, body], cwd=d,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **detached())
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("rc", RC._drive(proc, lambda c: None,
                                                                            lambda: None)),
                              daemon=True)
        t0 = time.time()
        th.start()
        th.join(25)
        took = time.time() - t0
        kid = int(open(pidf).read()) if os.path.exists(pidf) else None
        alive = kid is not None and self._alive(kid)
        if kid:
            subprocess.run(["taskkill", "/F", "/PID", str(kid)], capture_output=True)
        th.join(10)
        return th.is_alive() or "rc" not in out, took, alive

    def test_a_chatty_leftover_hangs_the_full_suite_run(self):
        hung, took, alive = self._run(CHATTY)
        say("merge-suite chatty leftover: drive still running after", round(took, 1), "s =", hung,
            "| leftover alive after drive =", alive)
        self.assertFalse(hung, "the gated-merge suite run did not return while a leftover wrote")

    def test_a_quiet_leftover_outlives_the_full_suite_run(self):
        hung, took, alive = self._run(QUIET)
        say("merge-suite quiet leftover: drive returned in", round(took, 1), "s hung=", hung,
            "| leftover alive after drive =", alive)
        self.assertFalse(alive, "a process the suite started outlived the gated-merge run")
