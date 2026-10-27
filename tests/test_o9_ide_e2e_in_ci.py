"""0.0.26 O9 — IDE-E2E-IN-CI: the IDE's browser end-to-end runs on every PR, and cannot go quiet.

What must be true (for every contributor whose change touches the page, and every user who opens
`mokata ide`):

  * ci.yml has an `ide-e2e` job that runs on every PR, installs a HASH-PINNED set, installs the
    Chromium that playwright pins, and runs `tests/e2e_ide/run_e2e.py` with `MOKATA_REQUIRE_E2E=1`;
  * the e2e set carries EVERY `[ide]` runtime requirement and mokata's own runtime dependency (the
    job runs the checkout's src/ with no mokata install — a missing one is an IDE nobody ships);
  * with the browser required, a missing playwright is a RED, never a green run of skips; without
    it required (a dev box), the flows SKIP and are still counted;
  * the runner reds (exit 2) when it finds no flow;
  * the core flows exist as flows (palette, tabs, find, search paging, jobs, worktrees, a bare
    repo), each one a harness flow (so each gets the zero-console-errors step).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest

from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E2E = os.path.join(ROOT, "tests", "e2e_ide")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
RUNNER = os.path.join(E2E, "run_e2e.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _req_names(lines):
    out = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if m and not line.startswith("-"):
            out.add(m.group(1).lower().replace("_", "-"))
    return out


class TheJobRunsTheE2eOnEveryPr(unittest.TestCase):
    def setUp(self):
        self.ci = safe_load(_read(CI_YML), "grade the ide-e2e job")
        self.job = self.ci["jobs"]["ide-e2e"]
        self.runs = [str(s.get("run", "")) for s in self.job["steps"]]

    def test_it_runs_on_pull_requests(self):
        on = self.ci.get("on") or self.ci.get(True)
        self.assertIn("pull_request", on)
        self.assertNotIn("if", self.job, "the e2e job became conditional")

    def test_it_installs_hash_pinned_then_the_pinned_chromium_then_runs_every_flow_required(self):
        self.assertIn("pip install --require-hashes -r requirements/e2e.txt", self.runs)
        self.assertTrue(any("playwright install" in r and "chromium" in r for r in self.runs))
        step = next(s for s in self.job["steps"] if "run_e2e.py" in str(s.get("run", "")))
        self.assertEqual("python tests/e2e_ide/run_e2e.py -v", step["run"].strip())
        self.assertEqual("1", str(step.get("env", {}).get("MOKATA_REQUIRE_E2E")))
        self.assertTrue(self.job.get("timeout-minutes"), "no ceiling on a job that drives a browser")


class TheE2eSetIsTheIdeMokataShips(unittest.TestCase):
    def test_every_ide_requirement_and_the_runtime_dependency_is_pinned(self):
        try:
            import tomllib
        except ImportError:                       # pragma: no cover - py3.10
            self.skipTest("tomllib needs Python 3.11+")
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            proj = tomllib.load(fh)["project"]
        want = _req_names(proj["dependencies"]) | _req_names(proj["optional-dependencies"]["ide"])
        e2e_in = _req_names(_read(os.path.join(ROOT, "requirements", "e2e.in")).splitlines())
        e2e_txt = _req_names(_read(os.path.join(ROOT, "requirements", "e2e.txt")).splitlines())
        self.assertEqual(set(), want - e2e_in, "requirements/e2e.in lacks these [ide]/runtime deps")
        self.assertEqual(set(), (want | {"playwright"}) - e2e_txt,
                         "requirements/e2e.txt is stale — run scripts/gen-pinned-requirements.sh")

    def test_the_pinned_playwright_is_the_one_the_input_names(self):
        pin = re.search(r"^playwright==(\S+)", _read(os.path.join(ROOT, "requirements", "e2e.in")),
                        re.M).group(1)
        self.assertRegex(_read(os.path.join(ROOT, "requirements", "e2e.txt")),
                         r"(?m)^playwright==%s \\" % re.escape(pin))


def _run_runner(env_extra):
    """run_e2e.py in a child, playwright made un-importable (as on a box without it)."""
    code = ("import sys, runpy\n"
            "sys.modules['playwright'] = None\n"
            "sys.modules['playwright.sync_api'] = None\n"
            "sys.argv = ['run_e2e.py']\n"
            "runpy.run_path(%r, run_name='__main__')\n" % RUNNER)
    env = dict(os.environ)
    env.pop("MOKATA_REQUIRE_E2E", None)
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=120)


class NoBrowserIsRedWhenRequiredAndCountedWhenNot(unittest.TestCase):
    def test_required_and_no_playwright_is_red(self):
        r = _run_runner({"MOKATA_REQUIRE_E2E": "1"})
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("MOKATA_REQUIRE_E2E=1 but playwright is not installed", r.stdout)

    def test_not_required_skips_every_step_and_still_counts_them(self):
        r = _run_runner({})
        self.assertEqual(0, r.returncode, r.stdout)
        ran = int(re.search(r"Ran (\d+) test", r.stdout).group(1))
        skipped = int(re.search(r"skipped=(\d+)", r.stdout).group(1))
        self.assertGreater(ran, 20, "the flows vanished from the count instead of skipping")
        self.assertEqual(ran, skipped)

    def test_a_runner_that_finds_no_flow_is_not_green(self):
        sys.path.insert(0, E2E)
        self.addCleanup(sys.path.remove, E2E)
        import run_e2e
        empty = tempfile.mkdtemp(prefix="e2e-empty-")
        self.addCleanup(os.rmdir, empty)
        old = run_e2e.HERE
        run_e2e.HERE = empty
        self.addCleanup(setattr, run_e2e, "HERE", old)
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            self.assertEqual(2, run_e2e.main([]))


class TheCoreFlowsAreHarnessFlows(unittest.TestCase):
    #: exit criterion 10's flows that exist today -> the class that drives each.
    FLOWS = {"first minute (render, ⌘P, graph, health)": "TheFirstMinuteWorks",
             "palette": "ThePaletteRunsCommandsAndJumps", "tabs": "TabsOpenCloseAndReopen",
             "find": "FindInTheCurrentFile", "search + Load more": "SearchPagesPastTheFirstThousandHits",
             "jobs drawer": "TheActivityDrawerShowsJobs", "worktrees": "WorktreesListAndMergeIsGated",
             "uninitialised repo": "AStrangersRepoOpens"}

    def _flows(self):
        found = {}
        # CORPUS: THE WORKING TREE — the flows ci.yml's `ide-e2e` job runs from the checkout; an
        # untracked flow module would run there too, so it must be graded here.
        for name in sorted(os.listdir(E2E)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            tree = ast.parse(_read(os.path.join(E2E, name)))
            for node in tree.body:
                self.assertFalse(isinstance(node, ast.ImportFrom) and node.module == "_harness",
                                 "%s: `from _harness import` re-collects the base flow" % name)
                if isinstance(node, ast.ClassDef):
                    bases = [ast.unparse(b) for b in node.bases]
                    self.assertEqual(["H.IdeFlow"], bases,
                                     "%s.%s is not a harness flow (no console-error step)"
                                     % (name, node.name))
                    found[node.name] = [f.name for f in node.body
                                        if isinstance(f, ast.FunctionDef)
                                        and f.name.startswith("test_")]
        return found

    def test_every_core_flow_exists_with_steps(self):
        found = self._flows()
        for what, cls in self.FLOWS.items():
            with self.subTest(flow=what):
                self.assertIn(cls, found)
                self.assertGreaterEqual(len(found[cls]), 2, "%s has no steps" % cls)


if __name__ == "__main__":
    unittest.main()
