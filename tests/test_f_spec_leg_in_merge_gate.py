"""0.0.28 F `SPEC-LEG-IN-MERGE-GATE` — the spec legs are merge evidence, at the head being merged.

What must be true for the user:
  * A merge is refused unless the spec-awareness and spec-drift legs of `ci_check` RAN in this
    worktree, at this exact head, against this base, on a clean tree, over this change, against the
    spec corpus as it is now — and neither came back `block` or `degraded`.
  * A leg that did not look (`absent`: nothing to check) does not block (ruling F-Q1) but is said
    as NOT CHECKED on the card, in the message and in the approval — never as passed.
  * No record, an unreadable record or a stale one blocks, each by its own name (§7g three states).
  * The leg reads the WORKTREE at H — never the main checkout or the caller's cwd.
  * Cancelling records nothing: the IDE job ends `cancelled`, the CLI exits 2 on Ctrl-C.
  * The evidence override waives only "no run bound"; it never waives a spec leg.
  * `completeness` is never part of the merge's spec leg (P3 owns that question).

Real git repos (P21's fixture); cancellation by counting hooks, never a timer.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p21_gated_merge import _Merge, _state
from test_p31_session_worktrees import _git, _write
from mokata import approval
from mokata import ci_check as CI
from mokata import merge_gate as MG
from mokata import merge_spec as MSL
from mokata.knowledge import cancel as C

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

#: A committed spec the change does not touch: both legs PASS (its anchor resolves, it names
#: nothing the change touches, it declares its tests).
QUIET_SPEC = {"title": "Invoice totals",
              "criteria": [{"id": "AC1", "text": "the invoice total is the sum of its lines",
                            "anchors": [{"file": "src/app.py", "symbol": "VALUE"}]}],
              "tests": []}


def _spec_file(tree, name, data):
    _write(tree, f".mokata/specs/{name}.json", json.dumps(data, indent=2) + "\n")
    _git(tree, "add", "-f", f".mokata/specs/{name}.json")
    _git(tree, "commit", "-qm", f"spec {name}")


class _Leg(_Merge):

    def codes(self, wt):
        return {b["code"] for b in MG.plan_merge(self.main, wt).blocked}

    def rec(self, run="R1"):
        return _state(self.main).read(MSL.key(run))

    def put(self, data, run="R1"):
        _state(self.main).write(MSL.key(run), data)

    def quiet(self, name="a", sid="S1", run="R1"):
        """A session whose change both legs PASS (a committed spec it does not touch)."""
        wt = self.session(name=name, sid=sid, run=run)
        _spec_file(wt, "s1", QUIET_SPEC)
        return wt


# ======================================================================= the plan reads one record
class ThePlan(_Leg):

    def test_a_leg_that_passed_at_this_head_lets_the_merge_through_and_says_passed(self):
        wt = self.quiet()
        self.evidence(wt)
        rec = self.rec()
        self.assertEqual({"spec-awareness": "pass", "spec-drift": "pass"},
                         {leg["name"]: leg["status"] for leg in rec["legs"]}, rec)
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        joined = "\n".join(plan.card["evidence"])
        self.assertIn("spec-awareness passed", joined)
        self.assertIn("spec-drift passed", joined)
        self.assertEqual([], plan.card["unchecked"])
        self.assertNotIn("NOT CHECKED", plan.render())
        self.assertEqual([], plan.args["evidence"]["spec_unchecked"])

    def test_the_plan_never_runs_the_leg_and_reads_no_changed_file(self):
        """`plan_merge` stays pure and fast: it reads the ONE record, never the change. A
        1M-line changed file is never opened by the plan (measured in the report)."""
        big = "".join(f"def f{i}():\n    return {i}\n" for i in range(500_000))
        wt = self.session(files={"src/big.py": big})
        self.evidence(wt)
        opened = []
        real_open = open

        def spy(path, *a, **k):
            if str(path).endswith("big.py"):
                opened.append(path)
            return real_open(path, *a, **k)
        with mock.patch.object(CI, "run_ci_check", side_effect=AssertionError("plan ran a leg")), \
                mock.patch("builtins.open", spy):
            t0 = time.perf_counter()
            plan = MG.plan_merge(self.main, wt)
            ms = (time.perf_counter() - t0) * 1000.0
        sys.stderr.write(f"\n[F] plan_merge over a 1M-line change: {ms:.0f} ms\n")
        self.assertTrue(plan.ok, plan.render())
        self.assertEqual([], opened, "the plan read the changed file")


class NotRun(_Leg):

    def test_no_record_blocks_by_name_never_ok(self):
        wt = self.session()
        self.evidence(wt)
        _state(self.main).delete(MSL.key("R1"))
        plan = MG.plan_merge(self.main, wt)
        self.assertFalse(plan.ok)
        self.assertIn(MG.SPEC_NOT_RUN, {b["code"] for b in plan.blocked})
        self.assertIn("mokata worktree test", plan.reason())


class Unreadable(_Leg):

    def test_a_damaged_record_is_unknown_never_absent(self):
        wt = self.session()
        self.evidence(wt)
        with open(_state(self.main).path(MSL.key("R1")), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        codes = self.codes(wt)
        self.assertIn("unknown-evidence", codes)
        self.assertNotIn(MG.SPEC_NOT_RUN, codes)

    def test_a_record_of_the_wrong_shape_is_unknown(self):
        wt = self.session()
        self.evidence(wt)
        for bad in ({"legs": "nope"}, {**self.rec(), "legs": [{"name": "spec-drift"}]},
                    {**self.rec(), "legs": [{"name": "spec-awareness", "status": "pass",
                                             "degraded": False}]}):
            with self.subTest(bad=str(bad)[:60]):
                self.put(bad)
                self.assertIn("unknown-evidence", self.codes(wt))


class Stale(_Leg):

    def test_anything_moved_since_the_leg_ran_is_stale(self):
        wt = self.session()
        self.evidence(wt)
        good = self.rec()

        def stale(rec):
            self.put(rec)
            return MG.SPEC_STALE in self.codes(wt)
        cases = {
            # each field moved ALONE (its partner too, so only this compare can see it — the
            # mutant pass found "head moved" caught by head_before instead, and base by base_after)
            "head moved": dict(good, head="0" * 40, head_before="0" * 40),
            "head moved during the leg": dict(good, head_before="0" * 40),
            "base moved": dict(good, base="0" * 40, base_after="0" * 40),
            "base moved during the leg": dict(good, base_after="0" * 40),
            "dirty before": dict(good, clean_before=False),
            "dirty after": dict(good, clean_after=None),
            "another worktree": dict(good, worktree=self.main),
            "change digest differs": dict(good, files_sha="0" * 64),
            "spec corpus changed": dict(good, corpus_fp="0" * 64),
            "older than the freshness bound": dict(good, at=time.time() - 25 * 3600),
        }
        for name, rec in cases.items():
            with self.subTest(name):
                self.assertTrue(stale(rec), name)
        self.put(good)
        self.assertEqual(set(), self.codes(wt))

    def test_the_same_worktree_spelled_another_way_is_not_stale(self):
        """Identity/aliasing: `a/./b`, a symlink, and on Windows `\\` and a case-folded spelling
        all name ONE worktree (`_same`, the file system's answer)."""
        wt = self.session()
        self.evidence(wt)
        good = self.rec()
        spellings = [os.path.join(os.path.dirname(wt), ".", os.path.basename(wt))]
        if os.name == "nt":
            spellings += [wt.replace("/", "\\"), wt.upper(), wt.lower().replace("\\", "/")]
        else:
            link = os.path.join(self._t.name, "alias")
            os.symlink(wt, link)
            spellings.append(link)
        for sp in spellings:
            with self.subTest(sp):
                self.put(dict(good, worktree=sp))
                self.assertNotIn(MG.SPEC_STALE, self.codes(wt))

    def test_an_empty_commit_moves_the_head_and_the_leg_is_stale(self):
        """The change set is identical (same files, same digest) but H moved: only the head
        compare can see it (added at the mutant pass)."""
        wt = self.session()
        self.evidence(wt)
        _git(wt, "commit", "-q", "--allow-empty", "-m", "nothing")
        self.assertIn(MG.SPEC_STALE, self.codes(wt))

    def test_a_new_commit_after_the_leg_needs_it_again(self):
        wt = self.session()
        self.evidence(wt)
        self.commit(wt, "src/late.py", "L = 1\n", "late")
        MSL.run_spec_leg(self.main, wt)               # only the leg: the suite is stale too
        codes = self.codes(wt)
        self.assertNotIn(MG.SPEC_STALE, codes)
        self.assertIn("suite-stale", codes)


class Absent(_Leg):

    def test_an_absent_leg_allows_the_merge_and_is_said_not_checked(self):
        """F-Q1: no committed specs and no saved corpus — both legs looked at nothing. The merge
        may proceed, and every surface says NOT CHECKED; none says passed."""
        wt = self.session()
        self.evidence(wt)
        self.assertEqual({"spec-awareness": "absent", "spec-drift": "absent"},
                         {leg["name"]: leg["status"] for leg in self.rec()["legs"]})
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        self.assertEqual(["spec-awareness", "spec-drift"],
                         plan.args["evidence"]["spec_unchecked"])
        joined = "\n".join(plan.card["evidence"])
        self.assertNotIn("spec-drift passed", joined)
        self.assertNotIn("spec-awareness passed", joined)
        self.assertTrue(any(u.startswith("NOT CHECKED: spec-drift") for u in plan.card["unchecked"]),
                        plan.card)
        self.assertIn("NOT CHECKED: spec-drift", plan.render())
        msg = plan.args["message"]
        self.assertIn("NOT CHECKED: spec-awareness, spec-drift", msg)
        self.assertNotIn("spec legs passed", msg)


class ApprovalBinds(_Leg):

    def test_an_approval_of_an_older_verdict_cannot_land_a_newer_one(self):
        wt = self.session()
        self.evidence(wt)
        pid, plan = self.approved(wt)
        self.assertIn("spec_leg_at", plan.args["evidence"])
        base = self.head()
        MSL.run_spec_leg(self.main, wt)               # the leg re-ran: a newer verdict
        out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.CONTENT_CHANGED, out.state, out.message)
        self.assertEqual(base, self.head())


class Degraded(_Leg):

    def test_a_leg_that_read_no_decisions_blocks(self):
        from mokata.memory.store import MemoryError as MemErr
        wt = self.quiet()
        with mock.patch("mokata.memory.MemoryStore.from_surface", side_effect=MemErr("locked")):
            self.evidence(wt)
        leg = {x["name"]: x for x in self.rec()["legs"]}["spec-awareness"]
        self.assertTrue(leg["degraded"], leg)
        plan = MG.plan_merge(self.main, wt)
        self.assertFalse(plan.ok)
        self.assertIn(MG.SPEC_DEGRADED, {b["code"] for b in plan.blocked})
        self.assertEqual(MG.SPEC_DEGRADED, plan.state)

    def test_a_blocking_leg_blocks_naming_the_leg_and_its_unblock(self):
        wt = self.session()
        _spec_file(wt, "gone", {"title": "Ledger", "criteria": [
            {"id": "AC1", "text": "ledger rows balance",
             "anchors": [{"file": "src/ledger.py", "symbol": "post"}]}], "tests": []})
        self.evidence(wt)
        blocks = [b for b in MG.plan_merge(self.main, wt).blocked if b["code"] == MG.SPEC_BLOCKED]
        self.assertEqual(1, len(blocks), blocks)
        self.assertIn("spec-drift", blocks[0]["text"])
        self.assertIn("mokata spec amend", blocks[0]["text"])


class RootIsTheWorktree(_Leg):

    def test_the_leg_reads_the_worktree_at_h_not_the_main_checkout_or_cwd(self):
        """A committed spec that exists ONLY in the worktree's commit, pointing at a file that is
        not there: graded in the worktree it BLOCKS; graded in the main checkout (or the caller's
        cwd) it would be `absent`."""
        wt = self.session()
        _spec_file(wt, "only-here", {"title": "Ledger", "criteria": [
            {"id": "AC1", "text": "ledger rows balance",
             "anchors": [{"file": "src/ledger.py", "symbol": "post"}]}], "tests": []})
        cwd = os.getcwd()
        os.chdir(self.main)
        try:        # restored HERE: _Repo.tearDown removes the tree before any addCleanup runs, and
            rec = MSL.run_spec_leg(self.main, wt)   # Windows will not delete a process's cwd
        finally:
            os.chdir(cwd)
        legs = {x["name"]: x["status"] for x in rec["legs"]}
        self.assertEqual("block", legs["spec-drift"], rec)
        self.assertEqual(os.path.realpath(wt), os.path.realpath(rec["worktree"]))


class OverrideScope(_Leg):

    def test_the_override_never_waives_the_spec_leg(self):
        wt = self.session()
        _spec_file(wt, "gone", {"title": "Ledger", "criteria": [
            {"id": "AC1", "text": "ledger rows balance",
             "anchors": [{"file": "src/ledger.py", "symbol": "post"}]}], "tests": []})
        self.evidence(wt)
        MG.file_override(self.main, wt, "a human says land it anyway", actor="jas")
        codes = self.codes(wt)
        self.assertIn(MG.SPEC_BLOCKED, codes, "an override waived a blocking spec leg")

    def test_an_override_merge_names_the_spec_legs_as_not_checked(self):
        wt = self.worktree("o")
        self.commit(wt, "src/o.py", "O = 1\n", "o")
        MG.file_override(self.main, wt, "a hotfix the run never covered", actor="jas")
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        self.assertTrue(any("spec" in u for u in MG.OVERRIDE_UNMET), MG.OVERRIDE_UNMET)
        self.assertIn("NOT CHECKED: spec-awareness, spec-drift", plan.render())
        for unmet in MG.OVERRIDE_UNMET:
            self.assertIn(unmet, plan.args["message"])


class CompletenessIsNotTheSpecLeg(_Leg):

    def test_completeness_is_neither_run_nor_counted(self):
        wt = self.session()
        with mock.patch.object(CI, "_completeness_leg",
                               side_effect=AssertionError("completeness ran in the spec leg")):
            self.evidence(wt)
        rec = self.rec()
        self.assertEqual(["spec-awareness", "spec-drift"], [x["name"] for x in rec["legs"]])
        rec["legs"].append({"name": "completeness", "status": "block", "degraded": False,
                            "summary": "x", "unblock": "y"})
        self.put(rec)
        self.assertNotIn(MG.SPEC_BLOCKED, self.codes(wt))

    def test_the_cli_and_mcp_check_still_run_every_leg(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
            res = CI.run_ci_check(d, [])
        self.assertEqual(["completeness", "spec-awareness", "spec-drift"],
                         [leg.name for leg in res.legs])


# ======================================================================= the card
class TheCard(_Leg):

    def test_each_leg_is_pass_block_or_not_checked_on_the_card(self):
        wt = self.quiet()
        self.evidence(wt)
        rec = self.rec()
        legs = {x["name"]: x for x in rec["legs"]}
        legs["spec-drift"] = dict(legs["spec-drift"], status="absent",
                                  summary="no committed specs")
        self.put(dict(rec, legs=[legs["spec-awareness"], legs["spec-drift"]]))
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        text = plan.render()
        self.assertIn("evidence  : spec-awareness passed", text)
        self.assertIn("NOT CHECKED: spec-drift (no committed specs)", text)
        for line in text.splitlines():
            if "spec-drift" in line:
                self.assertNotIn("passed", line)
        self.assertEqual(["spec-drift"], plan.args["evidence"]["spec_unchecked"])

    def test_the_card_escapes_nothing_new_and_the_ide_route_carries_unchecked(self):
        wt = self.session()
        self.evidence(wt)
        r = self.post(self.ide(), "/api/worktree/merge", {"path": wt})
        self.assertEqual("proposed", r["stage"], r)
        self.assertEqual(2, len(r["plan"]["card"]["unchecked"]), r["plan"]["card"])


# ======================================================================= cancel
def _counting(nth, then):
    """A stand-in for `languages.is_source_file` that runs `then()` on its `nth` call."""
    from mokata import languages
    real = languages.is_source_file
    calls = {"n": 0}

    def fake(rel):
        calls["n"] += 1
        if calls["n"] == nth:
            then()
        return real(rel)
    return fake, calls


class IdeCancel(_Leg):

    def test_a_cancelled_job_records_no_spec_leg_and_ends_cancelled(self):
        from mokata.ide import jobs as J
        files = {f"src/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(20)}
        wt = self.session(files=files)
        reg = J.JobRegistry()
        self.addCleanup(reg.close)
        holder = {}
        fake, calls = _counting(2, lambda: reg.cancel(holder["job"].id))
        with mock.patch("mokata.languages.is_source_file", fake):
            holder["job"] = reg.start("tests.run", {"path": wt},
                                      lambda: MSL.run_suite_and_leg(self.main, wt))
            self.assertTrue(holder["job"].finished.wait(600))
        self.assertEqual(J.CANCELLED, holder["job"].state, holder["job"].error)
        self.assertFalse(_state(self.main).exists(MSL.key("R1")), "a cancelled leg recorded")
        self.assertLessEqual(calls["n"], 3, "the leg kept walking after Cancel")

    def test_a_cancel_mid_leg_stops_there(self):
        """Added at the mutant pass (`Cancelled swallowed in run_spec_leg` survived: the trailing
        checkpoint still kept the record out). A Cancel mid-leg propagates from the leg itself —
        nothing after it runs, not even the after-stamp."""
        from mokata import merge_gate
        wt = self.session(files={f"src/m{i}.py": f"M{i} = {i}\n" for i in range(5)})
        flag = threading.Event()
        real = merge_gate.tree_stamp
        stamps = {"n": 0}

        def stamp(*a, **k):
            stamps["n"] += 1
            return real(*a, **k)
        fake, _calls = _counting(2, flag.set)
        with mock.patch.object(merge_gate, "tree_stamp", stamp), \
                mock.patch("mokata.languages.is_source_file", fake), C.scope(flag):
            with self.assertRaises(C.Cancelled):
                MSL.run_spec_leg(self.main, wt)
        self.assertEqual(1, stamps["n"], "work ran after the Cancel")
        self.assertFalse(_state(self.main).exists(MSL.key("R1")))

    def test_a_cancel_after_the_last_leg_records_nothing(self):
        """Added at the mutant pass (`record written before the trailing checkpoint` survived the
        matrix as first written): a Cancel that lands once the legs are done — while the tree is
        stamped after them — still records nothing."""
        from mokata import merge_gate
        wt = self.session()
        flag = threading.Event()
        real = merge_gate.tree_stamp
        calls = {"n": 0}

        def stamp(*a, **k):
            calls["n"] += 1
            out = real(*a, **k)
            if calls["n"] == 2:                 # the AFTER stamp: both legs have returned
                flag.set()
            return out
        with mock.patch.object(merge_gate, "tree_stamp", stamp), C.scope(flag):
            with self.assertRaises(C.Cancelled):
                MSL.run_spec_leg(self.main, wt)
        self.assertEqual(2, calls["n"])
        self.assertFalse(_state(self.main).exists(MSL.key("R1")), "a cancelled leg recorded")

    def test_the_ide_suite_route_runs_the_suite_then_the_leg_in_one_job(self):
        wt = self.session()
        c, state = self._ide_state()
        r = self.post(c, "/api/worktree/suite", {"path": wt})
        self.assertEqual("started", r["stage"], r)
        job = state.jobs.get(r["id"])
        self.assertTrue(job.finished.wait(600))
        self.assertEqual("done", job.state, job.error)
        self.assertIn("spec_leg", job.result)
        self.assertTrue(_state(self.main).exists(MSL.key("R1")))

    def test_a_red_suite_does_not_skip_the_leg(self):
        wt = self.session()
        _write(wt, "check.py", "import sys\nsys.exit(1)\n")      # the suite runs IN the worktree
        _git(wt, "commit", "-qam", "a red suite")
        out = MSL.run_suite_and_leg(self.main, wt)
        self.assertFalse(out["passed"])
        self.assertTrue(out["spec_leg"]["recorded"])
        self.assertTrue(_state(self.main).exists(MSL.key("R1")))

    def _ide_state(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        state = AppState(self.main, "tok", 47999, None, False)
        c = TestClient(build_app(state), base_url="http://127.0.0.1:47999",
                       follow_redirects=False)
        c.get("/pair?t=tok")
        self.addCleanup(state.jobs.close)
        return c, state


#: The child process the SIGINT cells drive: the REAL CLI, with a counting hook that says it is
#: inside the leg and then waits for the cancel flag the SIGINT handler sets (no timer).
_CHILD = r"""
import os, sys
sys.path.insert(0, {src!r})
if os.name == "nt":
    import ctypes
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)   # a new group starts deaf to ^C
from mokata import languages
from mokata.knowledge import cancel as C
real = languages.is_source_file
n = [0]
def fake(rel):
    n[0] += 1
    if n[0] == 1:
        print("IN-LEG", flush=True)
        while not C.cancelled():
            C.current().wait(1.0)
    return real(rel)
languages.is_source_file = fake
from mokata.cli import main
sys.exit(main({argv!r}))
"""


class CliSigint(_Leg):

    def _interrupt(self, argv):
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen([sys.executable, "-c", _CHILD.format(src=SRC, argv=argv)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", **flags)
        lines = []
        for line in p.stdout:
            lines.append(line)
            if line.strip() == "IN-LEG":
                break
        else:
            p.wait()
            self.fail("the child never reached the leg: " + "".join(lines))
        try:
            if os.name == "nt":
                os.kill(p.pid, signal.CTRL_C_EVENT)
            else:
                p.send_signal(signal.SIGINT)
        except OSError as exc:
            p.kill()
            p.wait()
            self.skipTest(f"ctrl_c_event_undeliverable_on_this_box: {exc}")
        try:
            rest, _ = p.communicate(timeout=600)        # hang detection, not a deadline
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            if os.name == "nt":
                self.skipTest("ctrl_c_event_undeliverable_on_this_box: the child never saw it")
            raise
        return p.returncode, "".join(lines) + rest

    def test_ctrl_c_during_the_leg_exits_2_and_records_nothing(self):
        files = {f"src/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(5)}
        wt = self.session(files=files)
        rc, out = self._interrupt(["worktree", "test", wt, "--path", self.main])
        self.assertEqual(2, rc, out)
        self.assertIn("cancelled — nothing recorded", out)
        self.assertFalse(_state(self.main).exists(MSL.key("R1")), out)

    def test_ctrl_c_during_ci_check_exits_2(self):
        wt = self.session(files={"src/x.py": "def x():\n    return 1\n"})
        rc, out = self._interrupt(["ci-check", "--files", "src/x.py", "--path", wt])
        self.assertEqual(2, rc, out)
        self.assertIn("cancelled — nothing recorded", out)


# ======================================================================= V4 / V5 / V6
class TheCheckpoints(unittest.TestCase):
    """The three checkpoints the SPEC-TRISTATE verify left ungraded, each killed by a counting hook
    under a real `C.scope(Event)` — no timer anywhere."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        from mokata.init import init_repo
        init_repo(root=self.d, profile="standard", assume_yes=True, out=lambda *_: None)

    def test_v4_the_per_file_checkpoint_stops_a_walk_of_changed_files(self):
        for n in (50, 100_000):
            with self.subTest(files=n):
                flat = os.path.join(self.d, f"flat{n}")
                os.makedirs(flat)
                names = []
                for i in range(n):
                    rel = f"flat{n}/m{i}.py"
                    with open(os.path.join(self.d, rel), "w", encoding="utf-8") as fh:
                        fh.write(f"def f{i}():\n    pass\n")
                    names.append(rel)
                ev = threading.Event()
                fake, calls = _counting(2, ev.set)
                opened = []
                real_open = open

                def spy(path, *a, **k):
                    if "flat" in str(path):
                        opened.append(path)
                    return real_open(path, *a, **k)
                with C.scope(ev), mock.patch("mokata.languages.is_source_file", fake), \
                        mock.patch("builtins.open", spy):
                    with self.assertRaises(C.Cancelled):
                        CI.symbols_in_files(self.d, names)
                self.assertLessEqual(len(opened), 3, f"{len(opened)} files opened after Cancel")
                self.assertEqual(2, calls["n"])

    def test_v4_reports_true_progress_in_files(self):
        names = []
        for i in range(7):
            rel = f"src/p{i}.py"
            _write(self.d, rel, f"def p{i}():\n    pass\n")
            names.append(rel)
        seen = []
        with C.reporting(lambda **kw: seen.append(kw)):
            CI.symbols_in_files(self.d, names)
        prog = [kw for kw in seen if kw.get("unit") == "files"]
        self.assertEqual(7, len(prog))
        self.assertTrue(all(kw["done"] <= kw["total"] == 7 for kw in prog), prog)
        self.assertEqual(7, prog[-1]["done"])

    def test_v5_the_per_spec_checkpoint_in_the_drift_leg(self):
        for i in range(3):
            _write(self.d, f".mokata/specs/s{i}.json", json.dumps(
                {"title": f"S{i}", "criteria": [{"id": "AC1", "text": "t"}], "tests": []}))
        ev = threading.Event()
        calls = {"n": 0}
        from mokata import docsync
        real = docsync.audit_spec

        def counting(spec, root):
            # the flag is set AFTER the first audit returns, so only the per-spec checkpoint can
            # stop the loop before a second spec (audit_spec's own inner checkpoints would catch a
            # flag set before it ran — the mutant pass found that masking the per-spec one)
            calls["n"] += 1
            out = real(spec, root)
            if calls["n"] == 1:
                ev.set()
            return out
        with C.scope(ev), mock.patch.object(docsync, "audit_spec", counting):
            with self.assertRaises(C.Cancelled):
                CI.run_ci_check(self.d, ["src/x.py"], changed_symbols=[], legs=("spec-drift",))
        self.assertEqual(1, calls["n"])

    def test_v6_the_trailing_checkpoint_returns_no_half_result(self):
        ev = threading.Event()

        def leg(root):
            ev.set()
            return CI.CheckLeg("spec-drift", "pass", "ok")
        with C.scope(ev), mock.patch.object(CI, "_committed_specs_leg", leg):
            with self.assertRaises(C.Cancelled):
                CI.run_ci_check(self.d, [], changed_symbols=[], legs=("spec-drift",))


# ======================================================================= scale
class HugeFile(_Leg):
    """A 200 MB single-line changed file: the leg completes and records; time and memory are
    REPORTED (no cap)."""

    def test_a_200mb_single_line_file_in_the_change(self):
        import tracemalloc
        line = "x = '" + "a" * (200 * 1024 * 1024) + "'\n"
        wt = self.session(files={"src/huge.py": line})
        del line
        tracemalloc.start()
        t0 = time.perf_counter()
        rec = MSL.run_spec_leg(self.main, wt)
        secs = time.perf_counter() - t0
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        sys.stderr.write(f"\n[F] spec leg over a 200 MB single-line file: {secs:.1f} s, "
                         f"peak traced {peak / 1048576:.0f} MiB\n")
        self.assertTrue(rec["recorded"])
        self.assertEqual(["spec-awareness", "spec-drift"], [x["name"] for x in rec["legs"]])


if __name__ == "__main__":
    unittest.main()
