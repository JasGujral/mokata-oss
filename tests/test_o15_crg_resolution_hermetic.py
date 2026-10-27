"""0.0.26 O15 `CRG-RESOLUTION-HERMETIC` — mokata runs ITS OWN code-review-graph, and the suite
never borrows the developer's.

Measured on Jas's Mac at checkpoint 2: a core venv (`.[dev,ide]`, no code-review-graph) ran the
unit suite at a real terminal while a `uv tool` mokata[full] had put a code-review-graph on PATH.
`detect.find_command` found that copy through `shutil.which`, the router used it unpinned and
unchecked, the suite printed `code-graph: DEGRADED … (the backend is unreachable)` naming no binary,
a test failed, and the run sat silent. Ruling (Jas): "if full install this should never have
happened" — fix the product AND the tests.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from unittest import mock

import _support
from _support import crg_present_failing

from mokata import crg_resolve, detect
from mokata.crg_resolve import CrgResolution, resolve_crg

NAME = "code-review-graph"


def _fake(dirpath, version="2.3.9", serve_hangs=True, log=None):
    """A stand-in code-review-graph: answers `--version`, records every call to `log`, and (by
    default) HANGS on anything else — the worst case an unscoped binary can be."""
    os.makedirs(dirpath, exist_ok=True)
    script = os.path.join(dirpath, "fake_crg.py")
    with open(script, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(textwrap.dedent(f"""\
            import sys, time
            if {log!r}:
                with open({log!r}, "a", encoding="utf-8") as fh:
                    fh.write(" ".join(sys.argv[1:]) + "\\n")
            if "--version" in sys.argv:
                print("code-review-graph {version}")
                sys.exit(0)
            if {serve_hangs!r}:
                time.sleep(3600)
            """))
    if os.name == "nt":
        tool = os.path.join(dirpath, NAME + ".cmd")
        with open(tool, "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{script}" %*\n')
    else:
        tool = os.path.join(dirpath, NAME)
        with open(tool, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        os.chmod(tool, 0o755)
    return tool


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="o15-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env_bin = os.path.join(self.tmp, "env-bin")
        self.outside_bin = os.path.join(self.tmp, "outside-bin")
        os.makedirs(self.env_bin)
        os.makedirs(self.outside_bin)
        p = mock.patch.dict(os.environ, {"PATH": self.outside_bin + os.pathsep
                                         + os.environ.get("PATH", "")})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("MOKATA_CRG", None)
        e = mock.patch.object(detect, "env_scripts_dirs", lambda: (self.env_bin,))
        e.start()
        self.addCleanup(e.stop)


class TheProductRunsOnlyItsOwnCodeReviewGraph(_Dirs):

    def test_R_a_copy_on_PATH_outside_mokatas_env_is_reported_and_never_used(self):
        """Jas's machine: no CRG in the env, one on PATH from a uv tool. FIRING CONTROL: revert
        `find_command`'s env-bound branch and this returns the outside path."""
        outside = _fake(self.outside_bin)
        res = resolve_crg(version_of=lambda: None)
        self.assertFalse(res.ok)
        self.assertEqual(os.path.realpath(outside), os.path.realpath(res.outside))
        self.assertIsNone(detect.find_command(NAME),
                          "a code-review-graph from another environment was adopted silently")
        said = res.describe()
        self.assertIn("outside mokata's environment", said)
        self.assertIn(outside, said)
        self.assertIn("NOT used", said)
        self.assertIn("mokata[full]", said)

    def test_R_with_full_the_in_env_copy_wins_even_when_another_is_ahead_on_PATH(self):
        _fake(self.outside_bin)
        own = _fake(self.env_bin)
        res = resolve_crg(version_of=lambda: "2.3.9")
        self.assertTrue(res.ok)
        self.assertEqual(own, res.path)
        self.assertEqual("2.3.9", res.version)
        self.assertIsNotNone(res.outside, "the shadowing copy on PATH must still be named")

    def test_R_a_version_outside_the_grounded_range_is_refused_with_the_reason(self):
        _fake(self.env_bin)
        for bad in ("1.9.0", "3.0.0"):
            res = resolve_crg(version_of=lambda b=bad: b)
            self.assertFalse(res.ok, bad)
            self.assertIn(bad, res.refused)
            self.assertIn("outside the supported range", res.refused)
            self.assertIn("refused", res.describe())

    def test_R_an_in_env_binary_with_no_installed_package_is_refused(self):
        _fake(self.env_bin)
        res = resolve_crg(version_of=lambda: None)
        self.assertFalse(res.ok)
        self.assertIn("cannot be checked", res.refused)

    def test_the_version_comes_from_THIS_envs_metadata_not_PYTHONPATH(self):
        site = os.path.join(self.tmp, "site")
        dist = os.path.join(site, "code_review_graph-2.4.1.dist-info")
        os.makedirs(dist)
        with open(os.path.join(dist, "METADATA"), "w", encoding="utf-8") as fh:
            fh.write("Metadata-Version: 2.1\nName: code-review-graph\nVersion: 2.4.1\n")
        self.assertEqual("2.4.1", crg_resolve.env_dist_version(site_dirs=[site]))
        self.assertIsNone(crg_resolve.env_dist_version(site_dirs=[self.tmp]))

    def test_an_explicit_MOKATA_CRG_is_used_after_a_bounded_version_check(self):
        named = _fake(os.path.join(self.tmp, "named"))
        with mock.patch.dict(os.environ, {"MOKATA_CRG": named}):
            res = resolve_crg()
        self.assertTrue(res.ok, res.describe())
        self.assertEqual("MOKATA_CRG", res.origin)
        old = _fake(os.path.join(self.tmp, "old"), version="1.0.0")
        with mock.patch.dict(os.environ, {"MOKATA_CRG": old}):
            self.assertIn("refused", resolve_crg().describe())

    def test_a_named_binary_that_never_answers_version_is_refused_not_waited_on(self):
        mute = os.path.join(self.tmp, "mute")
        os.makedirs(mute)
        script = os.path.join(mute, "m.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(3600)\n")
        tool = os.path.join(mute, NAME + (".cmd" if os.name == "nt" else ""))
        with open(tool, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f'@"{sys.executable}" "{script}" %*\n' if os.name == "nt" else
                     f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n')
        os.chmod(tool, 0o755)
        import time
        t0 = time.monotonic()
        with mock.patch.dict(os.environ, {"MOKATA_CRG": tool}), \
                mock.patch.object(crg_resolve, "VERSION_PROBE_TIMEOUT", 1.0), \
                mock.patch.object(crg_resolve, "probe_version",
                                  lambda p, timeout=1.0, _r=crg_resolve.probe_version: _r(p, 1.0)):
            res = resolve_crg()
        self.assertLess(time.monotonic() - t0, 20)
        self.assertFalse(res.ok)
        self.assertIn("did not report a version", res.refused)

    def test_the_client_refuses_to_spawn_an_unresolved_crg_and_says_why(self):
        from mokata.knowledge.crg_client import CodeReviewGraphClient, CrgUnavailable
        outside = _fake(self.outside_bin)
        client = CodeReviewGraphClient(root=self.tmp)
        with self.assertRaises(CrgUnavailable) as cm:
            client._default_run_cli(["--version"])
        self.assertIn(outside, str(cm.exception))
        with self.assertRaises(CrgUnavailable):
            client._default_call_tool("query_graph_tool", {})

    def test_a_build_never_runs_a_bare_name_off_PATH(self):
        from mokata.knowledge import crg_build
        log = os.path.join(self.tmp, "calls.log")
        _fake(self.outside_bin, log=log)
        res = crg_build.run_build(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn("outside mokata's environment", res.error)
        self.assertFalse(os.path.exists(log), "the outside binary was spawned")


class DoctorAndSetupSayWhichBinary(_Dirs):

    def test_doctor_names_the_outside_copy(self):
        from mokata.govern.doctor import crg_resolution_findings
        outside = _fake(self.outside_bin)
        f = crg_resolution_findings()
        self.assertEqual(["code-review-graph-outside-env"], [x.code for x in f])
        self.assertEqual("warning", f[0].severity)
        self.assertIn(outside, f[0].detail)

    def test_doctor_names_a_refused_version(self):
        from mokata.govern.doctor import crg_resolution_findings
        f = crg_resolution_findings(lambda: CrgResolution(refused="code-review-graph 3.1.0 is "
                                                          "outside the supported range — refused"))
        self.assertEqual(["code-review-graph-refused"], [x.code for x in f])

    def test_doctor_is_silent_on_a_clean_core_install(self):
        from mokata.govern.doctor import crg_resolution_findings
        self.assertEqual([], crg_resolution_findings(lambda: CrgResolution()))
        self.assertEqual([], crg_resolution_findings(lambda: CrgResolution(disabled=True)))

    def test_MOKATA_CRG_off_switches_it_off_even_with_one_in_the_env(self):
        _fake(self.env_bin)
        with mock.patch.dict(os.environ, {"MOKATA_CRG": "off"}):
            res = resolve_crg(version_of=lambda: "2.3.9")
            self.assertTrue(res.disabled)
            self.assertFalse(res.ok)
            self.assertIsNone(detect.find_command(NAME))

    def test_setup_missing_row_names_the_outside_copy(self):
        from mokata import adoption_modes
        outside = _fake(self.outside_bin)
        self.assertFalse(adoption_modes._present_crg())
        cause = adoption_modes._crg_missing_cause("code-review-graph is not in the environment")
        self.assertIn(outside, cause)
        self.assertIn("not used", cause)


class TheDegradedLineNamesTheBinaryAndWhy(unittest.TestCase):
    """Jas's line said `(the backend is unreachable)` and nothing else."""

    def setUp(self):
        from mokata.init import init_repo
        from mokata import degrade
        self.root = tempfile.mkdtemp(prefix="o15-deg-")
        self.addCleanup(shutil.rmtree, self.root, True)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda *_a: None)
        with open(os.path.join(self.root, "core.py"), "w", encoding="utf-8") as fh:
            fh.write("def parse(t):\n    return t\n\n\ndef total(t):\n    return parse(t)\n")
        crg_present_failing(self)
        degrade.reset_degrade_notices()
        self.addCleanup(degrade.reset_degrade_notices)

    def test_the_notice_carries_the_binary_path_and_the_cause(self):
        from mokata.config import Surface
        from mokata.knowledge import KnowledgeLayer
        err = io.StringIO()
        with redirect_stderr(err):
            layer = KnowledgeLayer.from_surface(Surface.load(self.root))
            layer.callers("parse")
        line = [ln for ln in err.getvalue().splitlines() if "code-graph: DEGRADED" in ln]
        self.assertEqual(1, len(line), err.getvalue())
        self.assertIn(os.path.join(os.sep, "opt", "crg", "bin", NAME), line[0])
        self.assertIn("returned an error", line[0])


class TheSuiteIsHermetic(unittest.TestCase):

    def test_the_import_scrubbed_PATH_of_any_crg_not_in_this_env(self):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d and shutil.which(NAME, path=d):
                self.assertIn(os.path.normcase(os.path.realpath(d)), _support._own_scripts_dirs(),
                              f"{d} still offers a code-review-graph from another environment")
        self.assertEqual("off", os.environ.get("MOKATA_CRG"),
                         "the suite must default code-review-graph OFF unless a test asks")
        self.assertIsNone(detect.find_command(NAME))

    def test_hermetic_path_mirrors_a_dir_keeping_its_other_tools(self):
        tmp = tempfile.mkdtemp(prefix="o15-path-")
        self.addCleanup(shutil.rmtree, tmp, True)
        _fake(tmp)
        other = os.path.join(tmp, "sometool")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(other, 0o755)
        new, scrubbed = _support.hermetic_path(tmp, own_dirs=[])
        self.assertEqual([tmp], scrubbed)
        self.assertIsNone(shutil.which(NAME, path=new))
        if new:                                      # mirrored (symlinks allowed here)
            self.assertIsNotNone(shutil.which("sometool", path=new))

    def test_a_spawn_of_an_unnamed_outside_crg_raises_at_the_spawn(self):
        outside = tempfile.mkdtemp(prefix="o15-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        tool = _fake(outside, serve_hangs=False)
        with mock.patch.object(_support._tempfile, "gettempdir", lambda: "/nowhere-o15"):
            with self.assertRaises(_support.HermeticViolation):
                subprocess.Popen([tool, "--version"], stdout=subprocess.DEVNULL)
        with mock.patch.dict(os.environ, {"MOKATA_CRG": tool}), \
                mock.patch.object(_support._tempfile, "gettempdir", lambda: "/nowhere-o15"):
            subprocess.run([tool, "--version"], stdout=subprocess.DEVNULL, timeout=30)

    def test_the_watchdog_dumps_a_hung_test_instead_of_sitting_silent(self):
        tmp = tempfile.mkdtemp(prefix="o15-wd-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "test_o15_planted_hang.py"), "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(f"""\
                import sys, time, unittest
                sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
                import _support
                class T(unittest.TestCase):
                    def test_planted_hang_here(self):
                        time.sleep(4)
                """))
        env = dict(os.environ, MOKATA_TEST_WATCHDOG_S="1")
        env.pop("PYTHONPATH", None)   # an outer harness's sitecustomize must not re-arm the timer
        proc = subprocess.run([sys.executable, "-m", "unittest", "test_o15_planted_hang"],
                              cwd=tmp, env=env, capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)
        self.assertIn("test_planted_hang_here", proc.stderr,
                      "a test past the watchdog left no stack behind:\n" + proc.stderr[-2000:])
        self.assertIn("OK", proc.stderr)


class NoCrgTouchingModuleBorrowsAnOutsideBinary(unittest.TestCase):
    """THE SWEEP (the a36 shape): the modules that reach the code graph, run as a nested suite
    with a HANGING, call-recording code-review-graph planted on PATH outside the venv — Jas's
    machine, made deterministic. The run must finish OK and the planted binary must never be run.
    Narrowed to modules that name the graph's entry points; the full-suite reading under a pty is
    the report's evidence."""

    ENTRY_POINTS = ("Surface.load", "KnowledgeLayer", "select_backends", "crg_", "graph_adopt",
                    "find_command", "code-review-graph")

    # CORPUS: THE WORKING TREE — the modules on disk are the ones `unittest` would run.
    def _modules(self):
        here = os.path.dirname(os.path.abspath(__file__))
        mods = []
        for fn in sorted(os.listdir(here)):
            if not (fn.startswith("test_o1") or fn.startswith("test_gr_s2")
                    or fn.startswith("test_o11") or fn.startswith("test_g1_")):
                continue
            if not fn.endswith(".py") or fn == os.path.basename(__file__):
                continue
            with open(os.path.join(here, fn), encoding="utf-8") as fh:
                body = fh.read()
            if "relax_factor" in body:
                continue            # timing-graded: a load spike there is not a hermeticity fact
            if any(e in body for e in self.ENTRY_POINTS):
                mods.append(fn[:-3])
        return mods

    def test_the_graph_modules_pass_with_a_hanging_crg_on_PATH_and_never_run_it(self):
        mods = self._modules()
        self.assertGreaterEqual(len(mods), 3, mods)
        tmp = tempfile.mkdtemp(prefix="o15-sweep-")
        self.addCleanup(shutil.rmtree, tmp, True)
        log = os.path.join(tmp, "outside-calls.log")
        bindir = os.path.join(tmp, "outside-bin")
        _fake(bindir, log=log)
        here = os.path.dirname(os.path.abspath(__file__))
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
        env.pop("MOKATA_CRG", None)
        proc = subprocess.run([sys.executable, "-m", "unittest", *mods], cwd=here, env=env,
                              capture_output=True, text=True, timeout=1500,
                              stdin=subprocess.DEVNULL)
        self.assertRegex(proc.stderr, r"\nOK", proc.stderr[-3000:])
        self.assertFalse(os.path.exists(log),
                         "a test ran the code-review-graph planted OUTSIDE the venv: "
                         + (open(log, encoding="utf-8").read() if os.path.exists(log) else ""))


if __name__ == "__main__":
    unittest.main()
