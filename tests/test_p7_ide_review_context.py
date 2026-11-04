"""0.0.26 P7 `ID-REVIEW-CONTEXT` — the review context beside the diff.

Each test states the user-visible rule it protects. The rules:
  * the testing panel groups a run's tests BY ITS SPEC'S ACs, from the run's AC -> test map, and
    shows each test's latest status and its red/green history from the run's own record — an AC
    no test covers is flagged, a test seen only green is flagged, a test no AC claims is listed;
  * the panel names the approval that let the change in (who approved, which spec version);
  * re-running a test is the project's OWN test command as a visible, cancellable job — never a
    shell, never a test outside the run's map, and never written into the run's record;
  * any two spec versions render as Markdown, with the AC-level change list, and spec text that
    looks like markup stays text;
  * a Markdown file is served from the working tree or from a revision, and only a Markdown file
    inside the repository;
  * blame and line history are bounded by the hunk under review, not the file's history;
  * every one of it pages by the request, and holds at hundreds of ACs and thousands of tests.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _plan import commit_planned

from mokata.init import init_repo

RUN = "c" * 32


def _silent(*_a, **_k):
    pass


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _client(root, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, "tok", 9999, None, demo)
    c = TestClient(build_app(state), base_url="http://127.0.0.1:9999")
    c.get("/pair?t=tok")
    c.state = state
    return c


ORIGIN = {"Origin": "http://127.0.0.1:9999"}

#: The project's "test runner" in the fixture: exits 1 for an id containing "fail", sleeps for
#: one containing "slow" (the cancel test), and prints a line naming the id.
RUNNER = (
    "import subprocess, sys, time\n"
    "tid = sys.argv[1]\n"
    "print('running', tid, flush=True)\n"
    "if 'tree' in tid or 'orphan' in tid:\n"
    "    # a test that starts a helper process sharing stdout (xdist worker, a booted server)\n"
    "    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "    open(tid + '.pid', 'w').write(str(p.pid))\n"
    "    if 'tree' in tid:\n"
    "        p.wait()\n"
    "    sys.exit(0)\n"
    "if 'slow' in tid:\n"
    "    time.sleep(120)\n"
    "sys.exit(1 if 'fail' in tid else 0)\n")


def _gone(pid, within=10.0):
    """True once `pid` is dead (a zombie awaiting its reaper counts as dead)."""
    t0 = time.time()
    if os.name == "nt":
        import subprocess
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe")
        while time.time() - t0 < within:
            out = subprocess.run([exe, "/FI", f"PID eq {pid}", "/NH"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace").stdout
            if f" {pid} " not in f" {out} ":
                return True
            time.sleep(0.1)
        return False
    while time.time() - t0 < within:
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
                if "zombie" in fh.read():
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


def _store(root, rid):
    from mokata.session_state import scoped_store
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    return scoped_store(StateStore(state_dir(root)), rid)


def _set_command(root, cmd):
    path = os.path.join(root, ".mokata", "manifest.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = cmd
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def plant(root, criteria=None, tests=None, red=(), green=(), suite=None):
    """A git repo with mokata set up and one run: an approved plan, a spec at v2 (v1 archived),
    the AC -> test map seeded at emit (`tests`: {test: [ACs]}), and the RED/GREEN record."""
    from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.spec_scope import archive_key
    from mokata import tdd_state
    from mokata.test_runs import record_test_run, seed_ac_map
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    init_repo(root=root, profile="standard", assume_yes=True, out=_silent)
    _write(root, "runner.py", RUNNER)
    st = _store(root, RUN)
    PipelineCheckpoint(st, RUN).ensure_registered()
    st.write("approved_approach", {
        "topic": "parser", "approach": {"name": "Tokenize first", "summary": "", "pros": [],
                                        "cons": [], "targets": [], "decisions": []},
        "answered_questions": [], "approver": "jas", "approved_at": "2026-10-20T09:00:00+00:00",
        "domains": []})
    crit = criteria or [("AC-1", "an empty input parses to []"),
                        ("AC-2", "whitespace is dropped"),
                        ("AC-3", "a trailing comma is an error")]
    commit_planned(st, Spec("parser", [AcceptanceCriterion(crit[0][0], crit[0][1])]),
                   version=1, run_id=RUN)
    commit_planned(st, Spec("parser", [AcceptanceCriterion(a, t) for a, t in crit]),
                   version=2, archive_key=archive_key(RUN, 1), run_id=RUN)
    tests = {"t_empty": ["AC-1"], "t_strip": ["AC-2"], "t_keep": ["AC-2"]} \
        if tests is None else tests
    seed_ac_map(root, RUN, [TestRef(name=t, ac_ids=acs) for t, acs in tests.items()])
    if red or green:
        tdd_state.record(st, RUN, red=list(red), green=list(green))
    if suite is not None:
        record_test_run(root, RUN, passed=suite[0], command="pytest", tests=suite[1])
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    return st


def wait_job(c, jid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["job"]["state"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish")


class TestsGroupedByAc(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        plant(self.d, red=["t_empty", "t_strip"], green=["t_empty", "t_keep"])
        self.c = _client(self.d)

    def tearDown(self):
        self._t.cleanup()

    def panel(self, **params):
        r = self.c.get("/api/review/tests", params={"run": RUN, **params})
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    def test_each_ac_lists_the_tests_its_map_names_with_status_and_red_green_history(self):
        """A reviewer sees, under each acceptance criterion, the tests the run mapped to it, each
        with its latest status and its RED -> GREEN history from the run's own TDD record."""
        p = self.panel()
        self.assertEqual("ok", p["state"])
        by = {c["id"]: c for c in p["criteria"]}
        self.assertEqual(["AC-1", "AC-2", "AC-3"], [c["id"] for c in p["criteria"]])
        self.assertEqual(["t_empty"], [t["id"] for t in by["AC-1"]["tests"]])
        empty = by["AC-1"]["tests"][0]
        self.assertEqual("green", empty["status"])
        self.assertEqual(["red", "green"], [h["state"] for h in empty["history"]])
        self.assertEqual("green", by["AC-1"]["status"])
        strip = {t["id"]: t for t in by["AC-2"]["tests"]}["t_strip"]
        self.assertEqual(("red", ["red"]), (strip["status"], [h["state"] for h in strip["history"]]))
        self.assertEqual("red", by["AC-2"]["status"], "one red test makes its AC red")

    def test_an_ac_with_no_test_is_flagged_untested(self):
        """An acceptance criterion no test covers is flagged — the review can see the gap."""
        p = self.panel()
        ac3 = {c["id"]: c for c in p["criteria"]}["AC-3"]
        self.assertTrue(ac3["untested"])
        self.assertEqual(("untested", []), (ac3["status"], ac3["tests"]))
        self.assertEqual(1, p["totals"]["untested"])

    def test_a_test_only_ever_seen_green_is_flagged(self):
        """A test that went GREEN without ever being recorded RED was never seen failing — the
        panel says so, because a test that cannot fail proves nothing."""
        p = self.panel()
        keep = {t["id"]: t for t in {c["id"]: c for c in p["criteria"]}["AC-2"]["tests"]}["t_keep"]
        self.assertTrue(keep["never_red"])
        self.assertEqual(1, p["totals"]["never_red"])

    def test_the_approval_that_let_the_change_in_is_named(self):
        """The panel names who approved the plan and which spec version is in force."""
        a = self.panel()["approval"]
        self.assertEqual(("ok", "jas", "Tokenize first", 2),
                         (a["state"], a["approver"], a["approach"], a["spec_version"]))

    def test_a_later_failed_suite_run_makes_the_test_red_again(self):
        """When the last recorded suite run failed, a test that was green is shown red, with the
        suite run in its history — the latest evidence wins."""
        from mokata.test_runs import record_test_run
        record_test_run(self.d, RUN, passed=False, command="pytest", tests=["t_empty"])
        t = {c["id"]: c for c in self.panel()["criteria"]}["AC-1"]["tests"][0]
        self.assertEqual("red", t["status"])
        self.assertEqual("last suite run", t["history"][-1]["basis"])

    def test_a_test_on_record_that_no_ac_claims_is_listed(self):
        """A test the run recorded that no AC maps is code without a criterion — listed apart."""
        from mokata import tdd_state
        tdd_state.record(_store(self.d, RUN), RUN, red=["t_orphan"])
        p = self.panel()
        self.assertEqual((1, ["t_orphan"]), (p["unmapped"]["total"], p["unmapped"]["tests"]))

    def test_the_panel_can_show_only_the_untested_or_the_red_acs(self):
        """Filtering by status is done before paging, so 'Untested' lists every untested AC of
        the spec, not just those on the page in view."""
        p = self.panel(status="untested")
        self.assertEqual((["AC-3"], 1), ([c["id"] for c in p["criteria"]], p["matching"]))
        self.assertEqual(["AC-2"], [c["id"] for c in self.panel(status="red")["criteria"]])
        self.assertEqual(400, self.c.get("/api/review/tests",
                                         params={"run": RUN, "status": "purple"}).status_code)

    def test_a_run_id_that_is_not_a_run_is_refused(self):
        """A run id that names no run (or a path) is a 400, and nothing is read."""
        for bad in ("nope", "../../etc", ""):
            self.assertEqual(400, self.c.get("/api/review/tests", params={"run": bad}).status_code)


class AtScale(unittest.TestCase):

    def test_hundreds_of_acs_and_thousands_of_tests_page_without_a_cap(self):
        """A spec with 600 ACs and 6,000 mapped tests answers page by page; every AC is reachable
        by paging, the totals count all of them, and one AC's long test list pages too."""
        crit = [(f"AC-{i}", f"criterion {i}") for i in range(600)]
        tests = {f"t_{i}_{k}": [f"AC-{i}"] for i in range(600) for k in range(10)}
        tests.update({f"t_big_{k}": ["AC-0"] for k in range(450)})
        with tempfile.TemporaryDirectory() as d:
            plant(d, criteria=crit, tests=tests, red=list(tests)[:3000], green=list(tests)[:2000],
                  suite=(True, list(tests)[:4000]))
            c = _client(d)
            t0 = time.time()
            first = c.get("/api/review/tests", params={"run": RUN}).json()
            elapsed = time.time() - t0
            self.assertEqual(600, first["totals"]["acs"])
            self.assertEqual(len(tests), first["totals"]["tests"])
            self.assertEqual(6, first["pages"])
            seen = []
            for page in range(1, first["pages"] + 1):
                seen += [x["id"] for x in c.get("/api/review/tests",
                                                params={"run": RUN, "page": page}).json()["criteria"]]
            self.assertEqual([a for a, _ in crit], seen)
            ac0 = first["criteria"][0]
            self.assertEqual(460, ac0["tests_total"])
            self.assertEqual(3, ac0["tests_pages"])
            ids = []
            for tp in range(1, 4):
                one = c.get("/api/review/tests", params={"run": RUN, "ac": "AC-0", "tpage": tp}).json()
                ids += [t["id"] for t in one["criteria"][0]["tests"]]
            self.assertEqual(sorted(t for t, a in tests.items() if a == ["AC-0"]), sorted(ids))
            self.assertLess(elapsed, 30, "one page of a 600-AC spec took %.1fs" % elapsed)


class ReRun(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        plant(self.d, tests={"t_ok": ["AC-1"], "t_fail": ["AC-2"], "t_slow": ["AC-3"]},
              red=["t_ok", "t_fail"], green=["t_ok"])
        self.c = _client(self.d)

    def tearDown(self):
        # Windows will not delete a directory a process still has open, and a process that was
        # just ended releases its working directory a moment later: retry for a generous while
        # (the product ends the tree; this only waits out the OS releasing the handle).
        end = time.time() + 40
        while True:
            try:
                self._t.cleanup()
                return
            except OSError:
                if os.name != "nt" or time.time() > end:
                    raise
                time.sleep(0.25)

    def start(self, body):
        return self.c.post("/api/review/tests/run", json={"run": RUN, **body}, headers=ORIGIN)

    def test_a_re_run_is_the_projects_own_command_as_a_job_and_shows_in_the_history(self):
        """Re-running an AC's tests runs the project's own test command once per test as a job;
        each test comes back red or green, the output streams with it, and the panel's history
        then carries the re-run."""
        _set_command(self.d, f'"{sys.executable}" runner.py')
        r = self.start({"tests": ["t_ok", "t_fail"]})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual("tests.run", r.json()["job"]["kind"])
        res = wait_job(self.c, r.json()["id"])["result"]
        self.assertEqual({"t_ok": "green", "t_fail": "red"},
                         {t["id"]: t["state"] for t in res["tests"]})
        self.assertIn("running t_fail", res["output"])
        self.assertEqual((1, 1), (res["passed"], res["failed"]))
        p = self.c.get("/api/review/tests", params={"run": RUN}).json()
        ok = {c["id"]: c for c in p["criteria"]}["AC-1"]["tests"][0]
        self.assertEqual("re-run here", ok["history"][-1]["basis"])
        fail = {c["id"]: c for c in p["criteria"]}["AC-2"]["tests"][0]
        self.assertEqual(("red", "red"), (fail["status"], fail["history"][-1]["state"]))

    def test_an_ide_re_run_is_recorded_as_evidence_with_its_provenance(self):
        """An IDE re-run is written to the run's test record — each test RED or GREEN by its own
        exit, the run with source "ide", the human as actor, the command, the exit code and the
        time — so it counts as evidence yet can always be told apart from an agent's run."""
        from mokata.tdd_state import load
        from mokata.test_runs import read_test_record
        from mokata.ide.review_context import ide_actor
        cmd = f'"{sys.executable}" runner.py'
        _set_command(self.d, cmd)
        t0 = time.time()
        r = self.start({"tests": ["t_ok", "t_fail"]})
        wait_job(self.c, r.json()["id"])
        suite = read_test_record(self.d, RUN)["last_suite"]
        self.assertEqual(("ide", ide_actor(), cmd, 1, False, ["t_fail", "t_ok"]),
                         (suite["source"], suite["actor"], suite["command"], suite["exit_code"],
                          suite["passed"], suite["tests"]))
        self.assertGreaterEqual(suite["at"], t0)
        red, green = load(_store(self.d, RUN), RUN)
        self.assertIn("t_fail", red)
        self.assertIn("t_ok", green)
        hist = {c["id"]: c for c in self.c.get("/api/review/tests", params={"run": RUN})
                .json()["criteria"]}["AC-2"]["tests"][0]["history"]
        self.assertIn("ide", [h.get("source") for h in hist])

    def test_the_stop_gate_sees_an_ide_re_run(self):
        """The Stop gate's evidence check reads an IDE re-run like any recorded run: before it,
        'no test run is recorded'; after a passing re-run of every mapped test, no test reason
        stands; a run recorded through the CLI says source "cli"."""
        from mokata.stop_gate import missing_evidence
        from mokata.test_runs import read_test_record, record_test_run
        _set_command(self.d, f'"{sys.executable}" runner.py')
        self.assertIn("no test run is recorded for this run", missing_evidence(self.d, RUN))
        r = self.start({"tests": ["t_ok"]})
        wait_job(self.c, r.json()["id"])
        reasons = missing_evidence(self.d, RUN)
        self.assertNotIn("no test run is recorded for this run", reasons)
        self.assertNotIn("the last recorded test run FAILED", reasons)
        r = self.start({"tests": ["t_fail"]})
        wait_job(self.c, r.json()["id"])
        self.assertIn("the last recorded test run FAILED", missing_evidence(self.d, RUN))
        record_test_run(self.d, RUN, passed=True, command="pytest", tests=["t_fail"])
        self.assertEqual(("cli", None), (read_test_record(self.d, RUN)["last_suite"]["source"],
                                         read_test_record(self.d, RUN)["last_suite"]["exit_code"]))

    def test_cancel_ends_the_tests_whole_process_tree_and_frees_the_lane(self):
        """Cancel ends a test that started a helper process sharing its output: the job stops at
        once, the helper is gone, and the next re-run is not queued behind the cancelled one."""
        _set_command(self.d, f'"{sys.executable}" runner.py')
        plant_more = {"t_tree": ["AC-3"]}
        from mokata.engine.spec import TestRef
        from mokata.test_runs import seed_ac_map
        seed_ac_map(self.d, RUN, [TestRef(name=t, ac_ids=a) for t, a in
                                  {"t_ok": ["AC-1"], "t_fail": ["AC-2"], **plant_more}.items()])
        r = self.start({"tests": ["t_tree"]})
        jid = r.json()["id"]
        pidf = os.path.join(self.d, "t_tree.pid")
        t0 = time.time()
        while not os.path.exists(pidf):
            self.assertLess(time.time() - t0, 30, "the test never started its helper")
            time.sleep(0.05)
        time.sleep(0.2)
        with open(pidf, encoding="utf-8") as fh:
            helper = int(fh.read())
        t1 = time.time()
        self.c.post(f"/api/jobs/{jid}/cancel", headers=ORIGIN)
        self.assertTrue(self.c.state.jobs.get(jid).stopped.wait(10), "the job's work never stopped")
        self.assertLess(time.time() - t1, 10)
        self.assertTrue(_gone(helper), "the test's helper process outlived Cancel")
        nxt = self.start({"tests": ["t_ok"]})
        self.assertEqual("done", wait_job(self.c, nxt.json()["id"], timeout=30)["job"]["state"])

    def test_on_windows_what_a_test_left_behind_is_ended_through_the_runners_job(self):
        """`taskkill /T` walks the tree from a LIVE root, so once the runner has exited it reaches
        nothing and a leftover (a server a test booted) held the project directory open. The
        runner is put in a Job Object at spawn and the leftovers' kill terminates the JOB — also
        when the runner is long dead — and never needs `taskkill` then. (Faked off Windows.)"""
        from unittest import mock
        from mokata.ide import review_context as RC
        ended = []

        class Job:
            ok = True

            def terminate(self):
                ended.append("job")

        dead = mock.Mock(pid=77)
        dead.poll.return_value = 0                         # the runner exited long ago
        with mock.patch("mokata.ide.review_context.os.name", "nt"), \
                mock.patch("mokata.crg_resolve._end_tree", side_effect=lambda p: ended.append("taskkill")):
            RC._kill_tree(dead, leftovers_only=True, job=Job())
            self.assertEqual(["job"], ended)
            ended.clear()
            RC._kill_tree(dead, job=Job())                 # a cancel: the job as well, not taskkill
            self.assertEqual(["job"], ended)
            ended.clear()
            bad = Job()
            bad.ok = False                                 # no job could be made: the old route
            RC._kill_tree(dead, job=bad)
            RC._kill_tree(dead, leftovers_only=True, job=bad)
            self.assertEqual(["taskkill"], ended)

    def test_a_test_that_leaves_a_process_behind_still_finishes(self):
        """A test whose runner exits but leaves a helper holding its output still finishes (the
        lane is never held by a leftover), and the leftover is ended with the run."""
        from mokata.engine.spec import TestRef
        from mokata.test_runs import seed_ac_map
        seed_ac_map(self.d, RUN, [TestRef(name="t_orphan", ac_ids=["AC-3"])])
        _set_command(self.d, f'"{sys.executable}" runner.py')
        r = self.start({"tests": ["t_orphan"]})
        j = wait_job(self.c, r.json()["id"], timeout=30)
        self.assertEqual(("done", "green"), (j["job"]["state"], j["result"]["tests"][0]["state"]))
        with open(os.path.join(self.d, "t_orphan.pid"), encoding="utf-8") as fh:
            self.assertTrue(_gone(int(fh.read())), "the leftover outlived its run")

    @unittest.skipIf(os.name == "nt", "a POSIX shell script stands in for the env's runner")
    def test_the_runner_is_the_one_in_the_projects_own_env(self):
        """A bare runner name (`pytest`) is the one in the project's env (its bin/ first on PATH),
        not whatever is first on the IDE server's PATH."""
        env = os.path.join(self.d, ".venv")
        os.makedirs(os.path.join(env, "bin"))
        _write(self.d, ".venv/pyvenv.cfg", "home = /usr/bin\n")
        _write(self.d, ".venv/bin/mk-p7-runner", "#!/bin/sh\necho from-the-project-env $1\n")
        os.chmod(os.path.join(env, "bin", "mk-p7-runner"), 0o755)
        _set_command(self.d, "mk-p7-runner")
        from unittest import mock
        clean = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
        with mock.patch.dict(os.environ, clean, clear=True):   # no activated env: the repo's wins
            r = self.start({"tests": ["t_ok"]})
            self.assertEqual(200, r.status_code, r.text)
            res = wait_job(self.c, r.json()["id"])["result"]
        self.assertEqual("green", res["tests"][0]["state"])
        self.assertIn("from-the-project-env t_ok", res["output"])

    def test_an_ac_or_the_failing_set_can_be_re_run_by_name(self):
        """'Re-run this AC' runs the AC's tests; 'Re-run failing' runs the red ones."""
        _set_command(self.d, f'"{sys.executable}" runner.py')
        r = self.start({"ac": "AC-1"})
        self.assertEqual(["t_ok"], [t["id"] for t in wait_job(self.c, r.json()["id"])["result"]["tests"]])
        r = self.start({"failing": True})
        self.assertEqual(["t_fail"], [t["id"] for t in wait_job(self.c, r.json()["id"])["result"]["tests"]])
        self.assertEqual(400, self.start({"ac": "AC-9"}).status_code)

    def test_cancel_kills_the_running_test(self):
        """Cancel stops a running test: the job is cancelled and its subprocess is gone."""
        _set_command(self.d, f'"{sys.executable}" runner.py')
        r = self.start({"tests": ["t_slow"]})
        jid = r.json()["id"]
        t0 = time.time()
        while "running t_slow" not in str((self.c.get(f"/api/jobs/{jid}").json().get("partial")
                                           or {}).get("output", "")):
            self.assertLess(time.time() - t0, 30, "the slow test never started")
            time.sleep(0.05)
        self.assertEqual(200, self.c.post(f"/api/jobs/{jid}/cancel", headers=ORIGIN).status_code)
        self.assertEqual("cancelled", wait_job(self.c, jid)["job"]["state"])
        job = self.c.state.jobs.get(jid)
        self.assertTrue(job.stopped.wait(10), "the work did not stop after cancel")

    def test_no_command_shell_syntax_strays_and_options_are_refused_with_the_remedy(self):
        """Without a test command the re-run says how to set one; a command that needs a shell is
        refused (the IDE never runs `sh -c`); a test the run does not map, or an id that would be
        an option to the runner, is refused — nothing is started."""
        r = self.start({"tests": ["t_ok"]})
        self.assertEqual(400, r.status_code)
        self.assertIn("settings.baseline.test_command", r.json()["note"])
        _set_command(self.d, "pytest -q && rm -rf /")
        r = self.start({"tests": ["t_ok"]})
        self.assertEqual(400, r.status_code)
        self.assertIn("without a shell", r.json()["note"])
        _set_command(self.d, f'"{sys.executable}" runner.py')
        for bad in (["t_unknown"], ["--collect-only"], ["a b"], ["t&calc"], ["t%PATH%"], []):
            self.assertEqual(400, self.start({"tests": bad}).status_code, bad)
        self.assertEqual([], [j for j in self.c.state.jobs.listing()["active"]
                              if j["kind"] == "tests.run"])


class SpecAsMarkdown(unittest.TestCase):

    def test_two_spec_versions_render_with_the_ac_change_list(self):
        """The spec diff gives both versions as Markdown and names the ACs added, removed and
        changed between them; spec text that looks like markup stays literal text."""
        with tempfile.TemporaryDirectory() as d:
            plant(d, criteria=[("AC-1", "an empty input parses to []"),
                               ("AC-2", "<script>alert(1)</script> is shown as text")])
            r = _client(d).get("/api/review/spec-diff", params={"run": RUN})
            self.assertEqual(200, r.status_code, r.text)
            s = r.json()
            self.assertEqual((1, 2), (s["old"]["version"], s["new"]["version"]))
            self.assertEqual(["AC-2"], [a["id"] for a in s["criteria"]["added"]])
            self.assertEqual(1, s["criteria"]["same"])
            self.assertIn("**AC-1**", s["old"]["markdown"])
            self.assertNotIn("<script>", s["new"]["markdown"])
            self.assertIn("\\<script\\>", s["new"]["markdown"])
            self.assertEqual("jas", s["approval"]["approver"])

    def test_a_version_that_does_not_exist_is_said_not_invented(self):
        """Asking for a spec version the run never had answers that side as absent."""
        with tempfile.TemporaryDirectory() as d:
            plant(d)
            s = _client(d).get("/api/review/spec-diff", params={"run": RUN, "a": 7}).json()
            self.assertEqual(("absent", ""), (s["old"]["state"], s["old"]["markdown"]))


class DocText(unittest.TestCase):

    def test_a_markdown_file_comes_from_the_working_tree_or_a_revision(self):
        """A changed README renders both sides: HEAD's text and the working tree's."""
        with tempfile.TemporaryDirectory() as d:
            plant(d)
            _write(d, "README.md", "# old\n")
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "readme")
            _write(d, "README.md", "# new\r\n")
            c = _client(d)
            now = c.get("/api/review/doc", params={"path": "README.md"}).json()
            then = c.get("/api/review/doc", params={"path": "README.md", "rev": "HEAD"}).json()
            self.assertEqual(("# new\n", "# old\n"), (now["text"], then["text"]))

    def test_only_a_markdown_file_inside_the_repository_is_served(self):
        """Not Markdown, outside the repo, or a revision that is an option: refused (400)."""
        with tempfile.TemporaryDirectory() as d:
            plant(d)
            c = _client(d)
            for params in ({"path": "runner.py"}, {"path": "../x.md"},
                           {"path": "README.md", "rev": "--output=/tmp/x"}):
                self.assertEqual(400, c.get("/api/review/doc", params=params).status_code, params)


class BlameOnTheHunk(unittest.TestCase):

    def test_blame_and_history_cover_the_hunks_lines_only(self):
        """Blame names who last wrote each line OF THE HUNK, and the line history lists only the
        commits that touched those lines — a commit that changed other lines of the file is not
        in it."""
        with tempfile.TemporaryDirectory() as d:
            _git(d, "init", "-q", "-b", "main")
            _git(d, "config", "user.email", "t@t")
            _git(d, "config", "user.name", "t")
            lines = [f"line {i}\n" for i in range(1, 41)]
            _write(d, "f.py", "".join(lines))
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "base")
            lines[4] = "line 5 changed\n"
            _write(d, "f.py", "".join(lines))
            _git(d, "commit", "-qam", "touch line 5")
            lines[34] = "line 35 changed\n"
            _write(d, "f.py", "".join(lines))
            _git(d, "commit", "-qam", "touch line 35")
            lines[5] = "line 6 edited now\n"
            _write(d, "f.py", "".join(lines))
            c = _client(d)
            r = c.get("/api/review/blame", params={"path": "f.py", "hunk": "@@ -3,6 +3,6 @@",
                                                   "sync_ms": "60000"})
            self.assertEqual(200, r.status_code, r.text)
            b = r.json()
            self.assertEqual(list(range(3, 9)), [x["line"] for x in b["new"]["lines"]])
            self.assertTrue({x["line"]: x for x in b["new"]["lines"]}[6]["uncommitted"])
            self.assertEqual(["touch line 5", "base"], [h["summary"] for h in b["history"]])
            self.assertNotIn("touch line 35", [h["summary"] for h in b["history"]])
            self.assertEqual({"touch line 5", "base"}, {g["summary"] for g in b["old"]["commits"]})

    def test_a_bad_hunk_or_revision_is_refused(self):
        """A malformed hunk header, or a revision that would be a git option, is a 400."""
        with tempfile.TemporaryDirectory() as d:
            plant(d)
            c = _client(d)
            for params in ({"path": "runner.py", "hunk": "nope"},
                           {"path": "runner.py", "hunk": "@@ -1 +1 @@", "base": "--all"},
                           {"path": "runner.py", "hunk": "@@ -1 +1 @@", "head": "-p"}):
                self.assertEqual(400, c.get("/api/review/blame", params=params).status_code)


class TheRendererIsVendoredAndInert(unittest.TestCase):
    """The Markdown + Mermaid renderers ship IN the package, pinned, licensed, and served by the
    IDE itself; the page never loads them from the network, and the frame Mermaid lays out in is
    the only document that may be framed — by this origin only."""

    STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "mokata",
                          "ide", "static")

    def pins(self):
        with open(os.path.join(self.STATIC, "vendor", "VERSIONS.json"), encoding="utf-8") as fh:
            return {r["file"]: r for r in json.load(fh)["files"]}

    def test_markdown_it_and_mermaid_are_pinned_by_version_and_sha256_with_their_licences(self):
        """Each renderer file matches its pinned sha256 and ships its licence text."""
        import hashlib
        pins = self.pins()
        for name, pkg in (("markdown-it.min.js", "markdown-it"), ("mermaid.min.js", "mermaid")):
            row = pins[name]
            self.assertEqual((pkg, "render", "MIT"), (row["package"], row["group"], row["license"]))
            with open(os.path.join(self.STATIC, "vendor", name), "rb") as fh:
                self.assertEqual(row["sha256"], hashlib.sha256(fh.read()).hexdigest())
            with open(os.path.join(self.STATIC, "vendor", row["license_file"]),
                      encoding="utf-8") as fh:
                self.assertIn("Permission is hereby granted", fh.read())
        with open(os.path.join(self.STATIC, "vendor", "LICENSE.entities.txt"),
                  encoding="utf-8") as fh:
            self.assertIn("Felix B", fh.read())
        with open(os.path.join(self.STATIC, "..", "..", "..", "..", "NOTICE"),
                  encoding="utf-8") as fh:
            notice = fh.read()
        for name in ("markdown-it", "mermaid", "entities"):
            self.assertIn(name, notice)

    def test_the_renderers_load_from_the_ide_never_a_cdn(self):
        """render.js and the Mermaid frame name only /static/ paths."""
        import re
        for rel in ("features/render.js", "features/review.js", "mermaid-frame.html",
                    "mermaid-frame.js"):
            with open(os.path.join(self.STATIC, *rel.split("/")), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIsNone(re.search(r"(src|href)\s*=\s*[\"']https?://", text), rel)
            for cdn in ("cdnjs", "jsdelivr", "unpkg", "googleapis"):
                self.assertNotIn(cdn, text, rel)
        with open(os.path.join(self.STATIC, "features", "render.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('"/static/vendor/markdown-it.min.js"', js)
        self.assertIn("html: false", js, "raw HTML in Markdown must stay text")
        self.assertIn("new DOMParser()", js, "rendered HTML is parsed in an inert document")
        self.assertNotIn("innerHTML", js, "no markup string is ever assigned into the page")

    def test_a_diagram_cannot_set_its_own_css_fonts_or_html_labels(self):
        """Mermaid refuses, at any depth of a diagram's `%%{init}%%` or front-matter config, every
        key that writes into the diagram's CSS or turns labels into HTML."""
        import re
        with open(os.path.join(self.STATIC, "mermaid-frame.js"), encoding="utf-8") as fh:
            js = fh.read()
        listed = set(re.findall(r'"(\w+)"', js[js.index("const SECURE"):js.index("];")]))
        for key in ("secure", "securityLevel", "themeCSS", "themeVariables", "fontFamily",
                    "htmlLabels", "dompurifyConfig", "maxTextSize"):
            self.assertIn(key, listed)
        self.assertIn("secure: SECURE", js)

    def test_the_vendored_renderers_are_the_pinned_versions_and_clear_of_known_advisories(self):
        """Offline: each renderer file is byte-for-byte its pin, mermaid's build names the pinned
        version, and no pin is below the version that fixed a published advisory."""
        import hashlib
        pins = self.pins()
        for name in ("markdown-it.min.js", "mermaid.min.js"):
            with open(os.path.join(self.STATIC, "vendor", name), "rb") as fh:
                data = fh.read()
            self.assertEqual(pins[name]["sha256"], hashlib.sha256(data).hexdigest(), name)
            self.assertIn(pins[name]["version"], pins[name]["tarball"], name)
        with open(os.path.join(self.STATIC, "vendor", "mermaid.min.js"), encoding="utf-8") as fh:
            self.assertIn('version:"%s"' % pins["mermaid.min.js"]["version"], fh.read())
        # package -> (first fixed version, the advisory it fixed)
        floors = {"markdown-it": ((15, 0, 3), "GHSA-gfr2-rv44-r23c")}
        for row in pins.values():
            floor = floors.get(row["package"])
            if floor:
                have = tuple(int(x) for x in row["version"].split("."))
                self.assertGreaterEqual(have, floor[0], f"{row['package']} {row['version']}: "
                                                        f"{floor[1]}")

    def test_a_vendored_file_skips_the_secret_lint_only_while_its_bytes_match_the_pin(self):
        """Pinned upstream bytes are not linted for credential shapes; one changed byte and the
        file is linted like any other shipped file."""
        import hashlib
        scripts = os.path.normpath(os.path.join(self.STATIC, "..", "..", "..", "..", "scripts"))
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import security_lint as L
        with tempfile.TemporaryDirectory() as d:
            vend = os.path.join(d, *L.VENDOR_DIR.split("/"))
            os.makedirs(vend)
            body = b"var t='AKIAQWERTYUIOPASDFGH';"
            with open(os.path.join(vend, "lib.min.js"), "wb") as fh:
                fh.write(body)
            with open(os.path.join(vend, "VERSIONS.json"), "w", encoding="utf-8") as fh:
                json.dump({"files": [{"file": "lib.min.js", "package": "lib", "version": "1.0.0",
                                      "sha256": hashlib.sha256(body).hexdigest()}]}, fh)
            self.assertIn(L.VENDOR_DIR + "/lib.min.js", L.pinned_vendor_files(d))
            with open(os.path.join(vend, "lib.min.js"), "ab") as fh:
                fh.write(b" ")
            self.assertEqual({}, L.pinned_vendor_files(d))

    def test_the_mermaid_frame_is_framable_by_this_origin_only_and_the_page_by_none(self):
        """The page cannot be framed at all; the Mermaid frame only by the IDE's own origin, runs
        only the IDE's scripts, and loads nothing else."""
        with tempfile.TemporaryDirectory() as d:
            c = _client(d, demo=True)
            page = c.get("/")
            self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
            r = c.get("/static/mermaid-frame.html")
            self.assertEqual(200, r.status_code)
            csp = r.headers["content-security-policy"]
            for part in ("default-src 'none'", "script-src 'self'", "frame-ancestors 'self'",
                         "img-src data:", "font-src data:", "style-src 'unsafe-inline'"):
                self.assertIn(part, csp)
            self.assertNotIn("unsafe-eval", csp)
            # P7 verification F1: a diagram's CSS `url(/api/…)` must not become a request with
            # the pairing cookie — the frame may load no image, font or stylesheet from ANY
            # origin, its own included.
            for directive in csp.split(";"):
                name = directive.split()[0] if directive.split() else ""
                if name in ("img-src", "font-src", "style-src", "default-src"):
                    self.assertNotIn("'self'", directive, directive)
            self.assertEqual("SAMEORIGIN", r.headers["x-frame-options"])
            for name in ("markdown-it.min.js", "mermaid.min.js"):
                v = c.get("/static/vendor/" + name)
                self.assertEqual(200, v.status_code, name)
                self.assertIn("javascript", v.headers["content-type"])
            self.assertEqual(404, c.get("/static/vendor/LICENSE.mermaid.txt").status_code)


if __name__ == "__main__":
    unittest.main()
