"""0.0.27 CI-FIX "Lows B" round 2 — what the verifier found in round 1, each as the rule it protects.

  * the no-Job-Object fallback never ends a process that is not ours: a pid Windows gave to an
    unrelated process is not the process we recorded, and neither are its children;
  * the gated merge's full-suite run is held in the same tree-ending spawn the IDE re-run uses:
    nothing the suite started outlives it, and a chatty leftover cannot keep the run alive;
  * a cancelled run in which nothing failed is "cancelled", never "FAILED";
  * a device name or a trailing dot/space is not a source location, however Windows spells it;
  * a filesystem that reports no file ids does not make every directory "the same";
  * a stream spelling (`run.cmd::$DATA`) is refused in the preflight, honestly;
  * a repo cannot switch corepack on by committing a shim into its own env;
  * the Windows e2e check fails by name on bytes that are not UTF-8.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import json
from unittest import mock
from urllib.parse import quote

import _support  # noqa: F401  (puts src/ on the path)
import test_p7_ide_review_context as P7
import test_p21_gated_merge as P21

from mokata import merge_suite as MS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUN = P7.RUN


def _tmp(case, prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(lambda: shutil.rmtree(d, True))
    return d


def _node(directory):
    """A file the finder reads as `node` on this OS (only its name is resolved here)."""
    n = "node" + (".exe" if os.name == "nt" else "")
    P7._write(directory, n, "#!/bin/sh\nexit 0\n")
    os.chmod(os.path.join(directory, n), 0o755)


# ================================================================ H1 — a reused pid is not ours
class TreeWatchNeverEndsAProcessThatIsNotOurs(unittest.TestCase):
    """The fallback records descendants by (pid, creation time). A recorded descendant K exits and
    Windows gives K's pid to an UNRELATED process Y; everything Y starts names K's pid as its
    parent and is born after K — and was killed as if it were ours."""

    def _watch(self, procs, killed, leader=10):
        from mokata.ide.lsp import TreeWatch
        return TreeWatch(leader, table=lambda: [(p, pp) for p, (pp, _c) in procs.items()],
                         created=lambda p: procs[p][1] if p in procs else None,
                         kill=killed.append)

    def test_the_child_of_an_unrelated_process_that_reused_a_descendants_pid_is_spared(self):
        procs = {10: (1, 100), 20: (10, 110)}        # leader 10 (t=100), its child K=20 (t=110)
        killed = []
        w = self._watch(procs, killed)
        w.observe()
        self.assertIn(20, w.known)
        procs[20] = (4, 500)                         # K exited; Y (parent 4) took pid 20 at t=500
        procs[30] = (20, 510)                        # Y started Z
        self.assertEqual([], w.end())
        self.assertEqual([], killed, "an unrelated process's child was ended")
        self.assertNotIn(30, w.known)

    def test_the_leaders_pid_reused_after_it_exits_spares_the_new_holders_children(self):
        procs = {10: (1, 100)}
        killed = []
        w = self._watch(procs, killed)
        procs[10] = (4, 500)                         # the leader exited; pid 10 is someone else's
        procs[30] = (10, 510)
        self.assertEqual([], w.end())
        self.assertEqual([], killed)

    def test_an_orphan_of_the_process_we_recorded_is_still_ended_beside_the_new_holder(self):
        """A child born BEFORE the pid's new holder existed can only be the recorded process's."""
        procs = {10: (1, 100), 20: (10, 110)}
        killed = []
        w = self._watch(procs, killed)
        w.observe()
        procs[40] = (20, 300)                        # K's child, born while K lived, not yet seen
        procs[20] = (4, 500)                         # then K exited and its pid was reused
        procs[30] = (20, 510)                        # the new holder's child: not ours
        self.assertEqual([40], w.end())
        self.assertEqual([40], killed)

    @unittest.skipUnless(os.name == "nt", "Windows: the real process table and creation times")
    def test_on_windows_a_live_unrelated_process_standing_where_a_descendant_was_is_spared(self):
        """The reuse shape with the real probes (ToolHelp, GetProcessTimes, TerminateProcess): a
        recorded creation time that is not the one the pid now has, and a live child under it."""
        from mokata.ide.lsp import TreeWatch
        sleeper = "import time; time.sleep(300)"
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import subprocess, sys; "
                                   "subprocess.Popen([sys.executable, '-c', %r]); "
                                   "import time; time.sleep(300)" % sleeper])
        self.addCleanup(lambda: subprocess.run(["taskkill", "/F", "/T", "/PID", str(holder.pid)],
                                               capture_output=True))
        me = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
        self.addCleanup(lambda: subprocess.run(["taskkill", "/F", "/PID", str(me.pid)],
                                               capture_output=True))
        w = TreeWatch(me.pid)
        # `holder` is not ours; pretend the pid we once recorded for a descendant is now its pid
        from mokata.ide.lsp import _win_created
        w.known[holder.pid] = _win_created(holder.pid) - 10_000_000 * 60
        time.sleep(2.0)                              # let holder's child exist
        w.observe()
        ended = w.end()
        self.assertEqual([], ended, "a process that is not ours was ended")
        self.assertIsNone(holder.poll(), "the unrelated holder was killed")
        self.assertEqual(1, len(w.known), "the unrelated holder's child was adopted")


# ================================================================ M2 — the gated merge's suite run
CHATTY = "import time\nfor i in range(600):\n    print('tick', i, flush=True); time.sleep(0.3)\n"
QUIET = "import time\ntime.sleep(300)\n"


class TheGatedMergeSuiteLeavesNothingBehind(P21._Merge):
    """M2. On Windows the full-suite run had no Job Object: a leftover that kept writing kept the
    run alive for as long as it wrote, and a quiet one outlived the run holding the worktree open
    (the post-merge `git worktree remove` cannot delete a directory something runs in)."""

    def _go(self, leftover_src):
        """Run the suite, which starts a leftover sharing its output pipe and exits at once.
        Returns (the run returned, seconds, the leftover's pid, outcome)."""
        pidfile = os.path.join(_tmp(self, "mk-lowsb2-pid-"), "kid.pid")
        suite = ("import subprocess, sys\n"
                 "kid = subprocess.Popen([sys.executable, '-c', %r])\n"
                 "open(%r, 'w').write(str(kid.pid))\n"
                 "print('suite done', flush=True)\n") % (leftover_src, pidfile)
        wt = self.session(files={"src/a.py": "A = 1\n", "leftover_suite.py": suite})
        mf = os.path.join(self.main, ".mokata", "manifest.json")
        with open(mf, encoding="utf-8") as fh:
            data = json.load(fh)
        data["settings"]["baseline"]["test_command"] = f"{sys.executable} leftover_suite.py"
        with open(mf, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        out = {}

        def go():
            try:
                out["res"] = MS.run_full_suite(self.main, wt)
            except Exception as exc:                          # pragma: no cover
                out["exc"] = exc
        t0 = time.time()
        th = threading.Thread(target=go, daemon=True)
        th.start()
        th.join(120)                      # the chatty leftover writes for 180 s: >= 4x of a run
        took = time.time() - t0
        pid = None
        if os.path.exists(pidfile):
            with open(pidfile, encoding="utf-8") as fh:
                pid = int(fh.read())

        def reap():
            if pid is None:
                return
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        self.addCleanup(reap)
        return (not th.is_alive()), took, pid, out

    def test_a_leftover_that_keeps_writing_does_not_keep_the_suite_run_alive(self):
        finished, took, pid, out = self._go(CHATTY)
        self.assertTrue(finished, "the suite run was still going after %.0fs — a leftover that "
                                  "kept writing held it" % took)
        self.assertNotIn("exc", out, out.get("exc"))
        self.assertTrue(out["res"]["passed"], out["res"])
        self.assertTrue(P7._gone(pid, within=60), "the leftover outlived the suite run")

    def test_a_quiet_leftover_does_not_outlive_the_suite_run(self):
        finished, took, pid, out = self._go(QUIET)
        self.assertTrue(finished)
        self.assertNotIn("exc", out, out.get("exc"))
        self.assertTrue(P7._gone(pid, within=60),
                        "a process the suite started is still running and holds the worktree")

    def test_the_suite_is_started_in_the_ide_re_runs_tree_ending_spawn(self):
        """The same spawn, on every OS: the Job Object on Windows, a new session on POSIX. A
        suite run that does not use it cannot end what it started."""
        from mokata.ide import lsp
        calls = []
        real = lsp.spawn_jobbed

        def spy(argv, stdin, nt=None, **kw):
            calls.append(list(argv))
            return real(argv, stdin, nt, **kw)
        wt = self.session()
        with mock.patch.object(lsp, "spawn_jobbed", spy):
            res = MS.run_full_suite(self.main, wt)
        self.assertTrue(res["passed"], res)
        self.assertEqual(1, len(calls))


# ================================================================ L1 — cancelled is not FAILED
class ACancelledRunWhereNothingFailedIsCancelledNotFailed(unittest.TestCase):
    def _cancelled(self, states):
        from mokata.ide import review_context as RC
        d = _tmp(self, "mk-lowsb2-cancel-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", states, False)
        return d

    def test_the_record_and_the_stop_gate_say_cancelled_when_every_test_that_ran_was_green(self):
        from mokata import stop_gate
        from mokata.test_runs import read_test_record
        d = self._cancelled([{"id": "t_a", "state": "green", "rc": 0}])
        suite = read_test_record(d, RUN)["last_suite"]
        self.assertIsNone(suite["passed"])
        self.assertIn("cancelled", suite["reason"])
        reasons = " | ".join(stop_gate.missing_evidence(d, RUN))
        self.assertIn("cancelled", reasons)
        self.assertNotIn("FAILED", reasons)

    def test_a_cancelled_run_with_a_failure_is_still_a_failure(self):
        from mokata import stop_gate
        d = self._cancelled([{"id": "t_a", "state": "red", "rc": 1}])
        self.assertIn("FAILED", " | ".join(stop_gate.missing_evidence(d, RUN)))

    def test_the_proof_panel_does_not_call_it_failed(self):
        from mokata.ide import review_context as RC
        d = self._cancelled([{"id": "t_a", "state": "green", "rc": 0}])
        panel = RC.tests_panel(d, RUN)
        status = {t["id"]: t["status"] for c in panel["criteria"] for t in c["tests"]}
        self.assertEqual("green", status["t_a"])


# ================================================================ L2 — device names, as 3.13 reads them
class DeviceAndAliasNamesAreNotSourceLocations(unittest.TestCase):
    def _uri(self, name):
        return "file:///C:/repo/src/" + quote(name)

    def test_the_spellings_python_3_13_reads_as_reserved_are_refused(self):
        from mokata.ide.lsp import from_uri
        for bad in ("CON .txt", "nul .py", "COM\u00b9", "LPT\u00b2.txt", "com\u00b3", "LPT3 .log",
                    "Con.", "a.py.", "a.py ", "a.py. .", "aux  .x", "CONIN$", "conout$.txt",
                    "a?.py", "a|b.py", 'q".py'):
            self.assertIsNone(from_uri(self._uri(bad), nt=True), repr(bad))

    def test_ordinary_names_are_still_files(self):
        from mokata.ide.lsp import from_uri
        for good in ("console.py", "CONFIG", "aux_helpers.py", "COM0.py", "LPT10.txt",
                     "com.py", "x.py", "my file.py", "a.b.c"):
            self.assertIsNotNone(from_uri(self._uri(good), nt=True), repr(good))
        self.assertIsNotNone(from_uri("file:///C:/repo/src/../lib/x.py", nt=True))


# ================================================================ L3 — no file ids
class AVolumeWithNoFileIdsDoesNotMakeEveryDirectoryTheSame(unittest.TestCase):
    def _no_ids(self, st_ino=0, st_dev=None):
        real = os.stat

        def fake(p, *k, **kw):
            st = real(p, *k, **kw)
            return os.stat_result((st.st_mode, st_ino, st.st_dev if st_dev is None else st_dev)
                                  + tuple(st)[3:])
        return fake

    def test_two_directories_on_such_a_volume_are_different_and_neither_is_under_the_other(self):
        from mokata import exe_path
        a, b = _tmp(self, "mk-lowsb2-a-"), _tmp(self, "mk-lowsb2-b-")
        for ino, dev in ((0, None), (None, 0)):
            real = os.stat

            def fake(p, *k, _i=ino, _d=dev, **kw):
                st = real(p, *k, **kw)
                return os.stat_result((st.st_mode, st.st_ino if _i is None else _i,
                                       st.st_dev if _d is None else _d) + tuple(st)[3:])
            with mock.patch.object(exe_path.os, "stat", fake):
                self.assertFalse(exe_path.same_place(a, b), (ino, dev))
                self.assertFalse(exe_path._under(os.path.join(a, "x"), b), (ino, dev))

    def test_the_same_directory_is_still_the_same_without_file_ids(self):
        from mokata import exe_path
        a = _tmp(self, "mk-lowsb2-a-")
        with mock.patch.object(exe_path.os, "stat", self._no_ids()):
            self.assertTrue(exe_path.same_place(a, a))
            self.assertTrue(exe_path._under(os.path.join(a, "x", "y"), a))

    def test_with_real_ids_identity_still_wins_over_spelling(self):
        from mokata import exe_path
        a = _tmp(self, "mk-lowsb2-a-")
        link = a + "-link"
        try:
            os.symlink(a, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("no symlinks here")
        self.addCleanup(lambda: os.path.lexists(link) and os.unlink(link))
        self.assertTrue(exe_path.same_place(a, link))


# ================================================================ L4 — a stream is not a program
class AStreamSpellingIsRefusedInThePreflight(unittest.TestCase):
    SPELLINGS = ("run.cmd::$DATA", "run.CMD::$data", "run.bat::$DATA", "run.cmd::$DATA.",
                 "run.exe:stream", "C:/tools/run.cmd::$DATA", "C:\\tools\\run.exe:s")

    def test_each_is_not_runnable_and_the_message_says_what_it_is(self):
        from mokata.ide import review_context as RC
        for spelling in self.SPELLINGS:
            d = _tmp(self, "mk-lowsb2-ads-")
            P7.plant(d, tests={"t": ["AC-1"]})
            P7._set_command(d, '"%s" -q' % spelling)
            got = RC.test_command(d, nt=True)
            self.assertFalse(got["runnable"], spelling)
            self.assertIn("alternate data stream", got["note"], spelling)
            self.assertNotIn("batch file", got["note"], spelling)

    def test_a_drive_colon_is_not_a_stream(self):
        from mokata.exe_path import is_stream
        for ok in ("C:\\tools\\py.exe", "C:/tools/py.exe", "py.exe", "d:py.exe"):
            self.assertFalse(is_stream(ok), ok)
        self.assertTrue(is_stream("py.exe:x"))


# ================================================================ L6 — a repo cannot enable corepack
class ARepoCannotSwitchCorepackOn(unittest.TestCase):
    def test_a_shim_committed_into_the_projects_env_is_not_the_users_corepack_enable(self):
        from mokata.ide import review_context as RC
        node_dir = _tmp(self, "mk-lowsb2-node-")
        _node(node_dir)
        P7._write(node_dir, "node_modules/corepack/package.json",
                  json.dumps({"name": "corepack", "bin": {"pnpm": "dist/pnpm.js"}}))
        P7._write(node_dir, "node_modules/corepack/dist/pnpm.js", "// pnpm\n")
        root = _tmp(self, "mk-lowsb2-repo-")
        scripts = os.path.join(root, ".venv", "Scripts")
        P7._write(scripts, "pnpm.cmd", "@echo off\r\n")
        path = os.pathsep.join([scripts, node_dir])
        head, why = RC.resolve_runner(root, "pnpm", path, nt=True)
        self.assertIsNone(head, "the repo's own shim turned corepack on")
        self.assertIn("pnpm.cmd", why)

    def test_a_shim_outside_the_repo_still_means_the_user_enabled_it(self):
        from mokata.ide import review_context as RC
        node_dir = _tmp(self, "mk-lowsb2-node-")
        _node(node_dir)
        P7._write(node_dir, "node_modules/corepack/package.json",
                  json.dumps({"name": "corepack", "bin": {"pnpm": "dist/pnpm.js"}}))
        P7._write(node_dir, "node_modules/corepack/dist/pnpm.js", "// pnpm\n")
        P7._write(node_dir, "pnpm.cmd", "@echo off\r\n")
        head, why = RC.resolve_runner(_tmp(self, "mk-lowsb2-repo-"), "pnpm", node_dir, nt=True)
        self.assertIsNone(why)
        self.assertTrue(head[1].endswith("pnpm.js"))


if __name__ == "__main__":
    unittest.main()
