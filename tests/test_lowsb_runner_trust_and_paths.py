"""0.0.27 CI-FIX "Lows B" — the Windows/LSP lane's disclosed Lows, each as the rule it protects.

  * a batch file is a batch file however Windows spells it (`run.cmd.`, `run.bat `): never started,
    never recorded RED;
  * a job in which some tests could not start is neither a pass nor a failure — the record says
    why, the Stop gate says "not all tests ran", and a test that ran green is never shown red;
  * a runner the REPO supplied (inside its own tree) runs only from the project's own Python env
    in a workspace the user trusted — the same rule `ide/lsp.py` applies to the project's venv;
  * "inside the repo" is decided by identity, not by spelling (a case-flipped path is the repo);
  * corepack's pnpm/yarn are used only when the user enabled them; names fold case on Windows;
  * a user-configured server that exists only as a `.cmd` says so; PATHEXT without `.EXE` still
    finds an `.exe`; device and stream names are not source locations;
  * with no Job Object the runner's descendants are still ended — also after the runner exited.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import test_p7_ide_review_context as P7

from mokata.init import init_repo

RUN = P7.RUN
_write = P7._write


def _tmp(case, prefix):
    d = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    case.addCleanup(lambda: shutil.rmtree(d, True))
    return d


def _repo(case, cmd):
    d = _tmp(case, "mk-lowsb-")
    init_repo(root=d, profile="standard", assume_yes=True, out=P7._silent)
    P7._set_command(d, cmd)
    return d


def _exe(directory, name):
    """A file the finder reads as a program on this OS (only its NAME is resolved here)."""
    n = name + (".exe" if os.name == "nt" else "")
    _write(directory, n, "#!/bin/sh\nexit 0\n")
    os.chmod(os.path.join(directory, n), 0o755)
    return os.path.join(directory, n)


def _case_insensitive_fs(d):
    probe = os.path.join(d, "CaseProbe")
    open(probe, "w", encoding="utf-8").close()
    return os.path.exists(os.path.join(d, "cASEpROBE"))


class ABatchFileIsABatchFileHoweverWindowsSpellsIt(unittest.TestCase):
    """LB4-1 / LB6-1. Windows strips trailing dots and spaces from a path, so `run.cmd.` IS
    `run.cmd`: CreateProcess starts cmd.exe for it, which printed "not recognized", exited 1 — and
    that exit was recorded as a RED test that never ran."""

    SPELLINGS = ("run.cmd", "run.cmd.", "run.cmd..", "run.bat.", "RUN.BAT.", "run.cmd ", "run.cmd. .")

    def test_every_spelling_is_refused_up_front_with_the_remedy(self):
        from mokata.ide import review_context as RC
        for spelling in self.SPELLINGS:
            root = _repo(self, "x")
            quoted = '"C:/tools/%s"' % spelling
            P7._set_command(root, quoted + " test")
            got = RC.test_command(root, nt=True)
            self.assertFalse(got["runnable"], spelling)
            self.assertIn("batch file", got["note"], spelling)
            self.assertIn("real executable", got["note"], spelling)

    def test_a_real_exe_with_a_dot_in_its_name_is_not_mistaken_for_one(self):
        from mokata.exe_path import is_batch
        self.assertFalse(is_batch("C:/tools/py.thon.exe"))
        self.assertFalse(is_batch("C:/tools/runner.cmd.exe"))
        self.assertTrue(is_batch("C:/tools/run.cmd. "))

    @unittest.skipUnless(os.name == "nt", "Windows: the real cmd.exe route")
    def test_on_windows_a_trailing_dot_spelling_starts_nothing_and_records_nothing_red(self):
        from mokata import tdd_state
        from mokata.ide import review_context as RC
        from mokata.test_runs import read_test_record
        d = _tmp(self, "mk-lowsb-dot-")
        P7.plant(d, tests={"t_ok": ["AC-1"]}, green=["t_ok"])
        mark = os.path.join(d, "BATCH_RAN.txt")
        _write(d, "run.cmd", "@echo off\r\necho ran> \"%s\"\r\n" % mark)
        before = read_test_record(d, RUN)["last_suite"]
        for spelling in ("run.cmd.", "run.cmd..", "run.cmd "):
            got = RC.run_tests(d, [os.path.join(d, spelling)], ["t_ok"],
                               on_done=lambda res, done: RC.record_rerun(d, RUN, "x", res, done))
            self.assertEqual("not-run", got["tests"][0]["state"], spelling)
            self.assertFalse(os.path.exists(mark), "the batch file ran")
        red, green = tdd_state.load(P7._store(d, RUN), RUN)
        self.assertNotIn("t_ok", red)
        self.assertEqual(before, read_test_record(d, RUN)["last_suite"])


class AJobThatPartlyRanIsNeitherAPassNorAFailure(unittest.TestCase):
    """LB6-2 / LB6-3."""

    def _mixed(self):
        from mokata.ide import review_context as RC
        d = _tmp(self, "mk-lowsb-mixed-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "green", "rc": 0},
                                      {"id": "t_b", "state": "not-run", "rc": None}], True)
        return d

    def test_the_record_says_why_the_suite_is_not_a_pass(self):
        from mokata.test_runs import read_test_record
        suite = read_test_record(self._mixed(), RUN)["last_suite"]
        self.assertIsNone(suite["passed"])
        self.assertEqual("1 test did not run", suite["reason"])
        self.assertEqual(["t_b"], suite["not_run"])

    def test_the_stop_gate_says_not_all_tests_ran_not_failed(self):
        from mokata import stop_gate
        reasons = " | ".join(stop_gate.missing_evidence(self._mixed(), RUN))
        self.assertIn("not all tests ran", reasons)
        self.assertIn("1 test did not run", reasons)
        self.assertNotIn("FAILED", reasons)

    def test_a_failing_test_in_the_job_is_still_a_failure_at_the_gate(self):
        from mokata import stop_gate
        from mokata.ide import review_context as RC
        d = _tmp(self, "mk-lowsb-red-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"], "t_c": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "red", "rc": 1},
                                      {"id": "t_b", "state": "not-run", "rc": None},
                                      {"id": "t_c", "state": "green", "rc": 0}], True)
        self.assertIn("FAILED", " | ".join(stop_gate.missing_evidence(d, RUN)))

    def test_a_test_that_ran_green_is_not_shown_red_beside_one_that_failed_or_did_not_run(self):
        from mokata.ide import review_context as RC
        d = _tmp(self, "mk-lowsb-shown-")
        P7.plant(d, tests={"t_a": ["AC-1"], "t_b": ["AC-1"], "t_c": ["AC-1"]})
        RC.record_rerun(d, RUN, "x", [{"id": "t_a", "state": "green", "rc": 0},
                                      {"id": "t_b", "state": "red", "rc": 1},
                                      {"id": "t_c", "state": "not-run", "rc": None}], True)
        panel = RC.tests_panel(d, RUN)
        status = {t["id"]: t["status"] for c in panel["criteria"] for t in c["tests"]}
        self.assertEqual("green", status["t_a"], "a test that passed is shown red")
        self.assertEqual("red", status["t_b"])
        self.assertEqual("unrun", status["t_c"])
        self.assertEqual("green", RC.run_tests_of(d, RUN)["latest"]["t_a"])

    def test_the_panel_summary_counts_not_run_and_styles_its_history_entry(self):
        base = os.path.join(os.path.dirname(__file__), "..", "src", "mokata", "ide", "static",
                            "features")
        with open(os.path.join(base, "review.js"), encoding="utf-8") as fh:
            js = fh.read()
        with open(os.path.join(base, "review.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn("${j.result.not_run || 0} not run", js)
        self.assertIn('x.state === "not-run" ? "N"', js)
        self.assertIn(".rv-h-not-run", css)
        self.assertIn("j.result.not_run > 0", js, "a job where tests did not run opens its output")


class ARepoDoesNotChooseTheRunner(unittest.TestCase):
    """LB6-4 / LB4-5 / LB5-1."""

    def test_a_poetry_the_repo_planted_on_an_absolute_path_entry_makes_no_claim(self):
        """The preflight says "looked up in the poetry/pipenv env" only when the USER has such a
        tool. A `poetry` in `<repo>/tools`, reachable through an absolute PATH entry, is the
        repo's: with `refuse_under` it is ignored and the plain "not found" is said."""
        from mokata.ide import review_context as RC
        d = _repo(self, "mk-lowsb-no-such-runner")
        P7._write(d, "pyproject.toml", "[project]\nname = 'x'\n")
        _exe(os.path.join(d, "tools"), "poetry")
        _exe(os.path.join(d, "tools"), "pipenv")
        empty = _tmp(self, "mk-lowsb-empty-")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([os.path.join(d, "tools"), empty])}):
            for venv in ("VIRTUAL_ENV", "CONDA_PREFIX"):
                os.environ.pop(venv, None)
            got = RC.test_command(d)
        self.assertFalse(got["runnable"], got)
        self.assertNotIn("poetry", got["note"])
        self.assertIn("not an executable on the project's PATH", got["note"])

    def test_a_case_flipped_spelling_of_the_repo_is_still_the_repo(self):
        """LB5-1: on a case-insensitive filesystem `/Repo/tools` is `/repo/tools`. A string compare
        of real paths let the flipped spelling through `refuse_under` and the planted tool ran."""
        from mokata import project_env
        from mokata.exe_path import _under, which_in
        d = _tmp(self, "mk-lowsb-Case-")
        if not _case_insensitive_fs(d):
            self.skipTest("this filesystem is case-sensitive: a flipped spelling is another path")
        tools = os.path.join(d, "tools")
        planted = _exe(tools, "poetry")
        head, tail = os.path.split(d)
        flipped = os.path.join(head, tail.swapcase(), "tools")
        self.assertTrue(os.path.isdir(flipped))
        self.assertTrue(_under(os.path.join(flipped, os.path.basename(planted)), d))
        self.assertIsNone(which_in("poetry", [flipped], refuse_under=d))
        mark = os.path.join(d, "RAN")
        if os.name != "nt":
            _write(tools, "poetry", "#!/bin/sh\necho x > '%s'\nexit 1\n" % mark)
            os.chmod(planted, 0o755)
            project_env._ask_tool(["poetry", "env", "info", "-p"], d, {"PATH": flipped})
            self.assertFalse(os.path.exists(mark), "the repo's poetry ran")

    def test_a_directory_that_is_the_cwd_under_another_spelling_is_still_the_cwd(self):
        from mokata.exe_path import path_dirs
        d = _tmp(self, "mk-lowsb-Cwd-")
        if not _case_insensitive_fs(d):
            self.skipTest("this filesystem is case-sensitive")
        head, tail = os.path.split(d)
        flipped = os.path.join(head, tail.swapcase())
        self.assertEqual([], path_dirs({"PATH": flipped}, cwd=d))

    def _env_runner(self, trusted):
        """A repo with its own venv holding `mk-runner`; returns (repo, resolve result)."""
        from mokata.ide import review_context as RC
        from mokata.ide import trust as T
        d = _repo(self, "mk-runner")
        bin_dir = os.path.join(d, ".venv", "Scripts" if os.name == "nt" else "bin")
        runner = _exe(bin_dir, "mk-runner")
        home = _tmp(self, "mk-lowsb-home-")
        patch = mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home})
        patch.start()
        self.addCleanup(patch.stop)
        if trusted:
            T._write(d, {"trusted": True, "root": os.path.realpath(d), "by": "test"})
        return d, runner, bin_dir, RC

    def test_the_projects_own_env_runner_needs_a_trusted_workspace(self):
        """LB4-5, the rule: `<repo>/.venv/bin/pytest` is where a project's runner legitimately
        lives, but it is a file the repo supplied — it runs only once the USER trusted the
        workspace (the decision that lets a language server start from that same venv), and the
        refusal names the file and the remedy."""
        d, runner, bin_dir, RC = self._env_runner(trusted=False)
        head, why = RC.resolve_runner(d, "mk-runner", bin_dir, project_bins=[bin_dir])
        self.assertIsNone(head)
        self.assertIn(runner, why)
        self.assertIn("mokata ide --trust", why)

    def test_a_trusted_workspace_runs_the_projects_own_env_runner(self):
        d, runner, bin_dir, RC = self._env_runner(trusted=True)
        head, why = RC.resolve_runner(d, "mk-runner", bin_dir, project_bins=[bin_dir])
        self.assertEqual(([runner], None), (head, why))

    def test_a_trusted_workspace_still_refuses_a_runner_outside_the_projects_env(self):
        """Trust does not make `<repo>/tools/pytest` the user's: only the project's own Python
        environment's bin directory is a legitimate home for a runner inside the repo."""
        d, _runner, bin_dir, RC = self._env_runner(trusted=True)
        planted = _exe(os.path.join(d, "tools"), "mk-planted")
        head, why = RC.resolve_runner(d, "mk-planted", os.path.dirname(planted),
                                      project_bins=[bin_dir])
        self.assertIsNone(head)
        self.assertIn("a repo does not get to choose what runs", why)

    def test_a_runner_outside_the_repo_needs_no_trust(self):
        from mokata.ide import review_context as RC
        d = _repo(self, "mk-outside")
        outside = _tmp(self, "mk-lowsb-outside-")
        runner = _exe(outside, "mk-outside")
        head, why = RC.resolve_runner(d, "mk-outside", outside)
        self.assertEqual(([runner], None), (head, why))


class JsToolsOnWindows(unittest.TestCase):
    """LB4-2 / LB4-4 / round-2 L4: pnpm/yarn through corepack only if enabled; names fold case;
    the global stores of pnpm and yarn are found."""

    def _node_dir(self):
        d = _tmp(self, "mk-lowsb-node-")
        _exe(d, "node")
        return d

    def _corepack(self, prefix):
        """A node install's corepack: `node_modules/corepack` with `pnpm`/`yarn` entries."""
        _write(prefix, "node_modules/corepack/package.json", json.dumps(
            {"name": "corepack", "bin": {"pnpm": "dist/pnpm.js", "yarn": "dist/yarn.js"}}))
        _write(prefix, "node_modules/corepack/dist/pnpm.js", "// pnpm\n")
        _write(prefix, "node_modules/corepack/dist/yarn.js", "// yarn\n")

    def test_corepack_is_not_used_for_a_pnpm_the_user_never_enabled(self):
        """LB4-2. Corepack downloads whatever the repo's `packageManager` names, with no prompt;
        it is the answer only when `corepack enable` put a pnpm shim on PATH."""
        from mokata.ide import review_context as RC
        node_dir = self._node_dir()
        self._corepack(node_dir)
        root = _repo(self, "pnpm test")
        for tool in ("pnpm", "yarn"):
            head, why = RC.resolve_runner(root, tool, node_dir, nt=True)
            self.assertIsNone(head, tool)
            self.assertIn("not an executable on the project's PATH", why)
            self.assertIn(f"corepack enable {tool}", why, "says how to enable it")

    def test_corepack_is_used_once_its_shim_is_on_path(self):
        from mokata.ide import review_context as RC
        node_dir = self._node_dir()
        self._corepack(node_dir)
        _write(node_dir, "pnpm.cmd", "@echo off\r\n")
        head, why = RC.resolve_runner(_repo(self, "pnpm test"), "pnpm", node_dir, nt=True)
        self.assertIsNone(why)
        self.assertEqual(os.path.join(node_dir, "node_modules", "corepack", "dist", "pnpm.js"),
                         head[1])

    def test_names_fold_case_on_windows(self):
        """LB4-4: `NPM` and `Npm` are npm on Windows."""
        from mokata.ide import review_context as RC
        prefix = _tmp(self, "mk-lowsb-npm-")
        node_dir = self._node_dir()
        P7._fake_npm(prefix, "// npm\n")
        path = os.pathsep.join([prefix, node_dir])
        for spelling in ("NPM", "Npm", "npm.CMD", "npm.cmd", "npm.cmd."):
            head, why = RC.resolve_runner(_repo(self, "npm test"), spelling, path, nt=True)
            self.assertIsNone(why, spelling)
            self.assertEqual(os.path.join(prefix, "node_modules", "npm", "bin", "npm-cli.js"),
                             head[1], spelling)

    def test_a_bare_cmd_name_with_no_js_entry_says_what_to_do(self):
        """LB4-4: `npm.cmd` found only as a shim used to say "not an executable on the PATH" —
        true of an executable and no remedy."""
        from mokata.ide import review_context as RC
        bindir = _tmp(self, "mk-lowsb-shim-")
        _write(bindir, "mvn.cmd", "@echo off\r\n")
        head, why = RC.resolve_runner(_repo(self, "mvn test"), "mvn.cmd", bindir, nt=True)
        self.assertIsNone(head)
        self.assertIn(os.path.join(bindir, "mvn.cmd"), why)
        self.assertIn("real executable", why)
        self.assertNotIn("is not an executable on the project's PATH", why)

    def test_a_global_pnpm_and_yarn_store_is_found_through_its_path_entry(self):
        """Round-2 L4. pnpm keeps global packages in `<PATH entry>/global/<n>/node_modules`, yarn
        v1 in `<...>/Yarn/Data/global/node_modules` with `<...>/Yarn/bin` on PATH."""
        from mokata.ide import lsp
        node_dir = self._node_dir()
        pnpm_home = _tmp(self, "mk-lowsb-pnpm-")
        yarn_home = _tmp(self, "mk-lowsb-yarn-")
        for store, pkg in ((os.path.join(pnpm_home, "global", "5", "node_modules"), "pnpm"),
                           (os.path.join(yarn_home, "Data", "global", "node_modules"), "yarn")):
            _write(store, f"{pkg}/package.json", json.dumps({"name": pkg, "bin": {pkg: "bin/x.js"}}))
            _write(store, f"{pkg}/bin/x.js", "// x\n")
        yarn_bin = os.path.join(yarn_home, "bin")
        os.makedirs(yarn_bin)
        env = {"PATH": os.pathsep.join([pnpm_home, yarn_bin, node_dir])}
        root = _tmp(self, "mk-lowsb-root-")
        head, why = lsp._global_node_package_bin(root, "pnpm", "pnpm", env)
        self.assertEqual(os.path.join(pnpm_home, "global", "5", "node_modules", "pnpm", "bin", "x.js"),
                         head[1], why)
        head, why = lsp._global_node_package_bin(root, "yarn", "yarn", env)
        self.assertEqual(os.path.join(yarn_home, "Data", "global", "node_modules", "yarn", "bin",
                                      "x.js"), head[1], why)


class TheTestCommandAsTyped(unittest.TestCase):
    def test_a_trailing_newline_is_not_shell_syntax(self):
        """LB5-3: a hand-edited manifest ends the command with a newline; on Windows that was
        refused as "shell syntax" naming none of the characters found."""
        from mokata.ide import review_context as RC
        for cmd in ("pytest -q\n", "  pytest -q\r\n", "\tpytest -q  "):
            root = _repo(self, cmd)
            got = RC.test_command(root, nt=True)
            self.assertNotIn("shell syntax", got["note"], repr(cmd))
            self.assertEqual("pytest -q", got["text"])

    def test_a_newline_inside_the_command_is_still_refused_on_windows(self):
        from mokata.ide import review_context as RC
        got = RC.test_command(_repo(self, "pytest -q\nmkdir X"), nt=True)
        self.assertFalse(got["runnable"])
        self.assertIn("shell syntax", got["note"])

    def test_the_baseline_keeps_running_the_users_one_liner_in_a_shell_and_says_so(self):
        """LB5-2. `mokata baseline` is the one test run that stays a shell: `&&` works there, and
        the batch-file claim is made only for the two shell-free paths."""
        from mokata import baseline
        py = '"%s" -c "import sys; sys.exit(0)"' % sys.executable
        got = baseline.baseline_status(f"{py} && {py}", cwd=tempfile.gettempdir())
        self.assertEqual(baseline.GREEN, got.state, got.detail)
        from mokata.ide import review_context as RC
        why = RC._shim_why("mvn", "C:/b/mvn.cmd", None)
        self.assertNotIn("mokata never runs a", why, "a claim only the IDE re-run and the merge keep")
        self.assertIn("re-run and gated merge", why)


class LanguageServerLookups(unittest.TestCase):
    """B3 Lows."""

    def _user_server(self, name):
        from mokata.ide import lsp
        home = _tmp(self, "mk-lowsb-lsphome-")
        _write(home, ".mokata/lsp.json", json.dumps({"languages": {"mylang": {
            "exts": [".my"], "id": "mylang", "servers": [[name, [], f"install {name}"]]}}}))
        patch = mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home})
        patch.start()
        self.addCleanup(patch.stop)
        return lsp

    def test_a_server_installed_only_as_a_cmd_says_what_was_found_and_why_it_is_not_run(self):
        lsp = self._user_server("myls")
        bindir = _tmp(self, "mk-lowsb-lspbin-")
        _write(bindir, "myls.cmd", "@echo off\r\n")
        root = _tmp(self, "mk-lowsb-lsproot-")
        with mock.patch.dict(os.environ, {"PATH": bindir}):
            found = lsp.find_server(root, "mylang", nt=True)
        self.assertFalse(found["found"])
        self.assertIn(os.path.join(bindir, "myls.cmd"), found["note"])
        self.assertIn("never runs", found["note"])
        self.assertIn("lsp.json", found["note"])
        self.assertNotIn("on PATH", found["note"].split("never runs")[0])

    def test_with_nothing_found_the_note_is_still_the_install_line(self):
        lsp = self._user_server("myls")
        root = _tmp(self, "mk-lowsb-lsproot-")
        with mock.patch.dict(os.environ, {"PATH": _tmp(self, "mk-lowsb-lspbin-")}):
            found = lsp.find_server(root, "mylang", nt=True)
        self.assertIn("install myls", found["note"])

    def test_a_pathext_without_exe_still_finds_an_exe(self):
        from mokata.exe_path import which_in
        d = _tmp(self, "mk-lowsb-pathext-")
        if not _case_insensitive_fs(d):
            self.skipTest("the .EXE spelling is looked up on a case-insensitive filesystem")
        _write(d, "tool.exe", "x")
        hit = which_in("tool", [d], nt=True, pathext=".PY;.BAT;.CMD")
        self.assertEqual(os.path.join(d, "tool.exe"), hit)

    def test_the_path_shown_is_the_spelling_on_disk_not_pathext_s(self):
        """Round-2 Low: the trust prompt showed `node.EXE` for `node.exe`."""
        from mokata.exe_path import which_in
        d = _tmp(self, "mk-lowsb-spell-")
        if not _case_insensitive_fs(d):
            self.skipTest("the .EXE spelling is looked up on a case-insensitive filesystem")
        _write(d, "node.exe", "x")
        self.assertEqual(os.path.join(d, "node.exe"),
                         which_in("node", [d], nt=True, pathext=".COM;.EXE;.BAT;.CMD"))

    def test_device_and_stream_names_are_not_source_locations(self):
        from mokata.ide import lsp
        for bad in ("file:///C:/repo/src/CON", "file:///C:/repo/src/nul.txt",
                    "file:///C:/repo/src/Com1", "file:///C:/repo/aux.py",
                    "file:///C:/repo/src/a.py::$DATA", "file:///C:/repo/src/a.py:stream",
                    "file:///C:/repo/src/CONOUT$", "file:///C:/repo/src/LPT9.log"):
            self.assertIsNone(lsp.from_uri(bad, nt=True), bad)
        for good in ("file:///C:/repo/src/console.py", "file:///C:/repo/src/CONFIG",
                     "file:///C:/repo/src/aux_helpers.py", "file:///c%3A/repo/src/a.py"):
            self.assertIsNotNone(lsp.from_uri(good, nt=True), good)


class WithNoJobObjectTheRunnersDescendantsAreStillEnded(unittest.TestCase):
    """Round-2 Low: the fallback left a test's leftovers running after the runner exited."""

    def _watch(self, table, born, killed):
        from mokata.ide.lsp import TreeWatch
        return TreeWatch(100, table=lambda: list(table), created=lambda p: born.get(p),
                         kill=killed.append)

    def test_a_grandchild_whose_parent_already_exited_is_ended(self):
        table = [(200, 100)]
        born = {100: 10, 200: 11}
        killed = []
        w = self._watch(table, born, killed)
        w.observe()                                   # the runner spawned 200
        table[:] = [(200, 100), (300, 200)]
        born[300] = 12
        w.observe()                                   # 200 spawned 300
        table[:] = [(300, 200)]                       # the runner and 200 exit: 300 names a dead parent
        del born[100], born[200]
        self.assertEqual([300], w.end())
        self.assertEqual([300], killed)

    def test_a_reused_pid_and_an_unrelated_process_are_spared(self):
        table = [(200, 100), (400, 100), (500, 999)]
        born = {100: 10, 200: 11, 400: 5, 500: 11}    # 400 is OLDER than its "parent": a reused pid
        killed = []
        w = self._watch(table, born, killed)
        w.observe()
        born[200] = 99                                # 200's pid was reused by a new process
        self.assertEqual([], w.end(), "a process that is not the one seen is never killed")
        self.assertEqual([], killed)

    def test_the_tree_is_found_below_the_leader_not_above_it(self):
        table = [(200, 100), (300, 200), (50, 1)]
        born = {100: 10, 200: 11, 300: 12, 50: 1, 1: 0}
        killed = []
        w = self._watch(table, born, killed)
        w.observe()
        self.assertEqual({200, 300}, set(w.end()))
        self.assertNotIn(50, killed)

    @unittest.skipUnless(os.name == "nt", "Windows: the real process table, with the job refused")
    def test_on_windows_a_test_s_leftover_dies_with_its_run_when_no_job_could_be_made(self):
        from mokata.ide import lsp
        from mokata.ide import review_context as RC

        class NoJob:
            ok = False

            def __init__(self, proc, kernel32=None):
                self.handle = None

            def terminate(self):
                pass

        d = _tmp(self, "mk-lowsb-nojob-")
        init_repo(root=d, profile="standard", assume_yes=True, out=P7._silent)
        _write(d, "mid.py", (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open('grand.pid', 'w').write(str(p.pid))\n"
            "time.sleep(1.5)\n"))                      # alive long enough to be seen, then gone
        _write(d, "runner.py", (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, 'mid.py']).wait()\n"
            "time.sleep(3)\n"))                        # the runner outlives its child: still alive
        with mock.patch.object(lsp, "WinJob", NoJob):
            got = RC.run_tests(d, [sys.executable, "runner.py"], ["t1"])
        self.assertEqual("green", got["tests"][0]["state"], got)
        with open(os.path.join(d, "grand.pid"), encoding="utf-8") as fh:
            pid = int(fh.read())
        self.assertTrue(P7._gone(pid, within=30), "the leftover outlived its run")


if __name__ == "__main__":
    unittest.main()
