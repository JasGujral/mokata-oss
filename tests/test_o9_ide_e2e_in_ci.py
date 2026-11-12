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
import shutil
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


#: The e2e jobs: id -> (the browser it runs, the name that must say so). One job per browser: each
#: gets its own check, its own red and its own measured ceiling (0.0.28 H, CI-E2E-FIREFOX-LEG).
E2E_JOBS = {"ide-e2e": ("chromium", "Chromium"), "ide-e2e-firefox": ("firefox", "Firefox")}
BROWSER_WORDS = ("chromium", "firefox", "webkit", "chrome", "safari")


def _job_text(job_id):
    """The raw text of one ci.yml job (comments included — the ceiling's run id lives in one)."""
    text = _read(CI_YML)
    m = re.search(r"(?m)^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(job_id), text, re.S)
    assert m, "no job %s in ci.yml" % job_id
    return m.group(1)


class TheJobRunsTheE2eOnEveryPr(unittest.TestCase):
    def setUp(self):
        self.ci = safe_load(_read(CI_YML), "grade the ide-e2e jobs")

    def _each(self):
        for job_id, (browser, word) in E2E_JOBS.items():
            job = self.ci["jobs"][job_id]
            yield job_id, browser, word, job, [str(s.get("run", "")) for s in job["steps"]]

    def test_they_run_on_pull_requests(self):
        on = self.ci.get("on") or self.ci.get(True)
        self.assertIn("pull_request", on)
        self.assertIn("push", on)
        for job_id, _b, _w, job, _r in self._each():
            self.assertNotIn("if", job, "%s became conditional" % job_id)

    def test_each_installs_hash_pinned_then_its_pinned_browser_then_runs_every_flow_required(self):
        for job_id, browser, _w, job, runs in self._each():
            with self.subTest(job=job_id):
                self.assertIn("pip install --require-hashes -r requirements/e2e.txt", runs)
                self.assertTrue(any("playwright install" in r and browser in r for r in runs))
                self.assertFalse(any("playwright install" in r and
                                     any(o in r for o in ("chromium", "firefox") if o != browser)
                                     for r in runs), "%s installs a browser it does not run" % job_id)
                step = next(s for s in job["steps"] if "run_e2e.py" in str(s.get("run", "")))
                self.assertEqual("python tests/e2e_ide/run_e2e.py -v", step["run"].strip(),
                                 "%s runs a subset (-k) or a different runner" % job_id)
                self.assertEqual("1", str(step.get("env", {}).get("MOKATA_REQUIRE_E2E")))
                self.assertTrue(job.get("timeout-minutes"),
                                "no ceiling on a job that drives a browser")


class TheFirefoxJobRunsEveryFlow(unittest.TestCase):
    """CI-E2E-FIREFOX-LEG: the whole suite in real Firefox, blocking, in a job of its own."""

    def setUp(self):
        self.ci = safe_load(_read(CI_YML), "grade the Firefox job")
        self.job = self.ci["jobs"]["ide-e2e-firefox"]

    def test_it_selects_firefox_for_the_run_step_and_nothing_else_does(self):
        for job_id in E2E_JOBS:
            steps = [s for s in self.ci["jobs"][job_id]["steps"]
                     if "run_e2e.py" in str(s.get("run", ""))]
            self.assertEqual(1, len(steps), job_id)
            want = "firefox" if job_id == "ide-e2e-firefox" else None
            self.assertEqual(want, steps[0].get("env", {}).get("MOKATA_E2E_BROWSER"), job_id)

    def test_it_blocks_like_every_other_leg(self):
        # tests/test_ci_matrix_floor.POLICY_BLOCKING: no continue-on-error, from the first run.
        self.assertNotIn("continue-on-error", self.job)
        for step in self.job["steps"]:
            self.assertNotIn("continue-on-error", step, step.get("name"))

    def test_it_runs_the_same_image_and_python_as_the_chromium_job(self):
        chromium = self.ci["jobs"]["ide-e2e"]
        self.assertEqual(chromium["runs-on"], self.job["runs-on"])
        py = lambda j: [str(s["with"]["python-version"]) for s in j["steps"]
                        if str(s.get("uses", "")).startswith("actions/setup-python")]
        self.assertEqual(py(chromium), py(self.job))


class TheJobNamesAreTrue(unittest.TestCase):
    def test_each_name_names_the_browser_it_runs_and_no_other(self):
        ci = safe_load(_read(CI_YML), "grade the job names")
        for job_id, (browser, word) in E2E_JOBS.items():
            name = ci["jobs"][job_id]["name"]
            with self.subTest(job=job_id):
                self.assertIn(word, name)
                others = [w for w in BROWSER_WORDS if w != browser and w in name.lower()]
                self.assertEqual([], others, "%r names a browser the job does not run" % name)
        names = [ci["jobs"][j]["name"] for j in E2E_JOBS]
        self.assertEqual(len(names), len(set(names)), "two e2e jobs share one check name")


class EachJobGradesItsCeiling(unittest.TestCase):
    """The ceiling is a runner measurement with its run id beside it, and the job grades itself."""

    def setUp(self):
        self.ci = safe_load(_read(CI_YML), "grade the ceilings")

    def test_the_clock_starts_before_the_install_and_the_last_step_grades_this_job(self):
        for job_id in E2E_JOBS:
            steps = self.ci["jobs"][job_id]["steps"]
            runs = [str(s.get("run", "")) for s in steps]
            with self.subTest(job=job_id):
                clock = next(i for i, r in enumerate(runs) if "MOKATA_CLOCK_T0=" in r)
                install = next(i for i, r in enumerate(runs) if "requirements/e2e.txt" in r)
                self.assertLess(clock, install)
                last = runs[-1]
                self.assertIn("scripts/ci_ceiling.py", last)
                self.assertIn('--since "$MOKATA_CLOCK_T0"', last)
                self.assertIn("--workflow .github/workflows/ci.yml", last)
                self.assertRegex(last, r"--job %s\s*$" % re.escape(job_id),
                                 "the self-grade names another job's ceiling")

    def test_each_ceiling_cites_the_runner_run_it_was_derived_from(self):
        for job_id in E2E_JOBS:
            text = _job_text(job_id)
            with self.subTest(job=job_id):
                self.assertRegex(text, r"(?i)run \d{10,}.*job \d{10,}",
                                 "%s's timeout-minutes cites no mirror run id + job id: a ceiling "
                                 "typed from a laptop time is the L-c defect" % job_id)

    def test_the_grader_the_jobs_call_exists_and_is_stdlib_only(self):
        # The e2e interpreter has no PyYAML: the grader must run on it.
        src = _read(os.path.join(ROOT, "scripts", "ci_ceiling.py"))
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+yaml\b")


class TheHarnessNamesItsBrowser(unittest.TestCase):
    def _harness(self, env_extra, code="import _harness as H; print('BROWSER=' + H.BROWSER)"):
        env = dict(os.environ)
        env.pop("MOKATA_E2E_BROWSER", None)
        env.pop("MOKATA_REQUIRE_E2E", None)
        env.update(env_extra)
        return subprocess.run([sys.executable, "-c", code], cwd=E2E, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=120)

    def test_the_default_is_chromium_and_firefox_is_selectable(self):
        self.assertIn("BROWSER=chromium", self._harness({}).stdout)
        self.assertIn("BROWSER=firefox", self._harness({"MOKATA_E2E_BROWSER": "firefox"}).stdout)

    def test_an_unknown_browser_is_an_error_naming_it_never_chromium(self):
        r = self._harness({"MOKATA_E2E_BROWSER": "webkitt"})
        self.assertNotEqual(0, r.returncode, r.stdout)
        self.assertIn("webkitt", r.stdout)
        self.assertNotIn("BROWSER=chromium", r.stdout)

    def test_an_empty_browser_name_is_not_chromium_either(self):
        r = self._harness({"MOKATA_E2E_BROWSER": ""})
        self.assertNotEqual(0, r.returncode, r.stdout)

    def test_the_page_is_pinned_to_en_us_and_a_firefox_clipboard_needs_no_grant(self):
        src = _read(os.path.join(E2E, "_harness.py"))
        self.assertIn("locale=cls.LOCALE", src)
        self.assertIn('LOCALE = "en-US"', src)
        self.assertIn("dom.events.asyncClipboard.readText", src)
        self.assertNotIn("grant_permissions", _read(os.path.join(E2E, "test_usages_copy.py")),
                         "a Chromium-only permission grant in a flow Firefox must run")

    def test_the_clipboard_helper_grants_on_chromium_only(self):
        code = ("import _harness as H\n"
                "class C:\n"
                "    calls = []\n"
                "    def grant_permissions(self, *a, **k): self.calls.append(a)\n"
                "class P:\n"
                "    context = C()\n"
                "H.grant_clipboard(P, 'http://x')\n"
                "print('GRANTS=%d' % len(C.calls))\n")
        self.assertIn("GRANTS=1", self._harness({"MOKATA_E2E_BROWSER": "chromium"}, code).stdout)
        self.assertIn("GRANTS=0", self._harness({"MOKATA_E2E_BROWSER": "firefox"}, code).stdout)

    def test_firefoxs_own_abort_of_the_live_stream_is_noise_and_nothing_else_is(self):
        code = ("import _harness as H\n"
                "f = H.IdeFlow._expected_console\n"
                "msg = 'The connection to http://127.0.0.1:1/api/events?client=x was interrupted "
                "while the page was loading.'\n"
                "print('STREAM=%s' % f(msg))\n"
                "print('OTHER=%s' % f(msg.replace('/api/events', '/api/query')))\n"
                "print('REAL=%s' % f('TypeError: x is undefined /api/events'))\n")
        ff = self._harness({"MOKATA_E2E_BROWSER": "firefox"}, code).stdout
        self.assertIn("STREAM=True", ff)
        self.assertIn("OTHER=False", ff)
        self.assertIn("REAL=False", ff)
        self.assertIn("STREAM=False", self._harness({"MOKATA_E2E_BROWSER": "chromium"}, code).stdout,
                      "the filter is Firefox's alone: Chromium never logs it, so there it is a real error")

    def test_required_and_no_playwright_names_the_browser(self):
        r = _run_runner({"MOKATA_REQUIRE_E2E": "1", "MOKATA_E2E_BROWSER": "firefox"})
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("cannot run firefox", r.stdout)

    def test_required_and_a_browser_that_is_not_installed_is_red_naming_it(self):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed on this box (the e2e job's interpreter has it)")
        empty = tempfile.mkdtemp(prefix="pw-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        env = dict(os.environ, MOKATA_REQUIRE_E2E="1", MOKATA_E2E_BROWSER="firefox",
                   PLAYWRIGHT_BROWSERS_PATH=empty)
        r = subprocess.run([sys.executable, RUNNER, "-k", "TheFirstMinuteWorks"], cwd=ROOT, env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=300)
        self.assertEqual(1, r.returncode, r.stdout[-2000:])
        self.assertIn("firefox did not launch", r.stdout)


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
             "uninitialised repo": "AStrangersRepoOpens",
             "explorer polish (P11)": "TheExplorerIsPolished",
             "path:line:col launch (P11)": "AnEditorOpensAtPathLineCol",
             "proof (P13 AC → test → code)": "TheProofScreenShowsTheTrace",
             "explainer (P46 cited review page)": "TheExplainerShowsCitedPagesSafely",
             "image diff (P9 side by side, swipe, onion, pixels, SVG inert)": "TheImageDiffWorks"}

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
