"""0.0.27 P13 — ID-VIS-PROOF: the AC → test → code trace and its risk overlay (ruling U15, the launch
demo). Business rules — what a reviewer must be able to SEE about a change, from the record alone:

  * every acceptance criterion of the run's spec, the tests recorded for it, and the code those
    tests exercise — one trace, built from stores that already exist, deterministic;
  * an AC with no test is flagged; a test that never went RED before GREEN is "not TDD-proven";
    a changed definition no test reaches is flagged; the blast radius (callers) of each change
    is shown; gates red or skipped are listed;
  * the trace is bounded by the CHANGE SET, never by the repository: one graph question per changed
    definition, and no repo walk — a million-line repo with a three-file change costs three files;
  * thousands of ACs and tests are PAGED, never capped; when the picture is reduced the answer says
    so and the data is still there;
  * it works with NO code-review-graph installed (the floor answers, and the answer says so) and
    with no run attached (a stranger's commit range still traces code → tests);
  * every route is in the IDE contract (`test_p0_ide_api_contract` holds the shape); the view is
    one feature file under static/features/ that passes the page's no-markup bar.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import sealed

from mokata import progress_events as PE
from mokata import run_intent as RI
from mokata import test_runs as TR
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.govern.resume import PipelineCheckpoint
from mokata.ide import nav
from mokata.ide import proof as P
from mokata.ide import snapshots as S
from mokata.init import init_repo
from mokata.tdd_state import state_dir

STATIC = Path(__file__).resolve().parents[1] / "src" / "mokata" / "ide" / "static"
TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"

CORE_OLD = ("def parse(text):\n    return text.split(',')\n\n\ndef total(text):\n"
            "    return len(parse(text))\n")
CORE_NEW = ("def parse(text):\n    if not text:\n        return []\n"
            "    return [p.strip() for p in text.split(',')]\n\n\ndef total(text):\n"
            "    return len(parse(text))\n\n\ndef report_error(pos):\n"
            "    return 'bad item at %d' % pos\n")
TEST_OLD = "from app.core import parse\n\n\ndef test_parse():\n    assert parse('a') == ['a']\n"
TEST_NEW = (TEST_OLD + "\n\ndef test_total():\n    from app.core import total\n"
            "    assert total('a,b') == 2\n")


def _silent(*_a, **_k):
    pass


def _git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def fixture(d: str, *, acs=("AC-1", "AC-2", "AC-3")) -> None:
    """A git repo, mokata initialised, run `r1` with an emitted spec (AC-1 anchored to
    `src/app/core.py:parse`), bound to session `S1`, whose recorded writes changed `parse`, added
    `report_error` (no test reaches it) and added `test_total`. The record: `test_parse` went RED
    then GREEN for AC-1 (TDD-proven); `test_total` only ever GREEN for AC-2; AC-3 has no test.
    The ledger: a redeemed write approval on core.py and a gate override; the progress log: one
    gate block."""
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    _write(d, "src/app/core.py", CORE_OLD)
    _write(d, "tests/test_core.py", TEST_OLD)
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, "r1").ensure_registered()
    crit = []
    for a in acs:
        c = {"id": a, "text": f"behaviour {a}"}
        if a == "AC-1":
            c["anchors"] = [{"file": "src/app/core.py", "symbol": "parse", "kind": "impl",
                             "source": "spec", "confidence": "high"}]
        crit.append(c)
    surface.state.write("emitted_spec__r1", sealed({"title": "parser", "criteria": crit,
                                                     "approach": "a", "domains": []}))
    RI.bind_registered(d, "S1", "r1")
    for rel, new in (("src/app/core.py", CORE_NEW), ("tests/test_core.py", TEST_NEW)):
        path = os.path.join(d, *rel.split("/"))
        S.capture_before_first_write(d, path, "S1", "r1")
        _write(d, rel, new)
        S.capture(d, path, S.AFTER, "S1", "r1")
    # the record: RED then GREEN for test_parse (AC-1); GREEN only for test_total (AC-2)
    # full ids (round 4: identity = path::Class::name; a bare id never proves)
    TR.record_test_run(d, "r1", passed=False, command="python -m unittest",
                       tests=["tests/test_core.py::test_parse"],
                       ac_map={"AC-1": ["tests/test_core.py::test_parse"]}, at=1000.0)
    TR.record_test_run(d, "r1", passed=True, command="python -m unittest",
                       tests=["tests/test_core.py::test_parse", "tests/test_core.py::test_total"],
                       ac_map={"AC-2": ["tests/test_core.py::test_total"]}, at=2000.0)
    led = AuditLedger.from_mokata_dir(os.path.join(d, ".mokata"))
    led.record("write_approval", decision="redeemed", proposal="p-1", tool="write_file",
               target="src/app/core.py", approved_by="jas", run="r1", committed=True)
    led.record("gate_override", gate="stop-evidence", run="r1", actor="jas",
               decision="override", scope="session", reason="demo")
    PE.record_gate_block(state_dir(d), "r1", "no-code-without-failing-test", "src/app/core.py")


def client(root: str, demo: bool = False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, TOKEN, PORT, None, demo)
    app = build_app(state)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    c.get(f"/pair?t={TOKEN}")
    return c, app, state


class _Repo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        fixture(cls.d)
        cls.trace = P.trace(cls.d, run="r1")

    @classmethod
    def tearDownClass(cls):
        nav.close_layers()
        cls._t.cleanup()

    def ac(self, ac):
        return next(a for a in self.trace["nodes"]["acs"] if a["ac"] == ac)

    def code(self, sym):
        return next(c for c in self.trace["nodes"]["code"] if c["symbol"] == sym)

    def tnode(self, name):
        return next(t for t in self.trace["nodes"]["tests"] if t["symbol"] == name)


# ===================================================================== the trace, AC → test → code

class TheTraceShowsACToTestToCode(_Repo):

    def test_every_AC_of_the_spec_is_a_row_with_its_tests_and_the_code_they_exercise(self):
        self.assertEqual(["AC-1", "AC-2", "AC-3"], sorted(a["ac"] for a in self.trace["nodes"]["acs"]))
        ac1 = self.ac("AC-1")
        self.assertEqual(["test:tests/test_core.py::test_parse"], ac1["tests"])
        self.assertIn("code:src/app/core.py#parse", ac1["code"])
        kinds = {(e["source"], e["target"], e["kind"], e["basis"]) for e in self.trace["edges"]}
        self.assertIn(("ac:AC-1", "test:tests/test_core.py::test_parse", "tested-by", "recorded"), kinds)
        self.assertIn(("test:tests/test_core.py::test_parse", "code:src/app/core.py#parse",
                       "exercises", "structural"), kinds)
        self.assertIn(("ac:AC-1", "code:src/app/core.py#parse", "anchors", "anchor"), kinds)

    def test_a_test_that_went_RED_then_GREEN_is_TDD_proven_with_both_times(self):
        t = self.tnode("test_parse")
        self.assertEqual("proven", t["tdd"])
        self.assertEqual((1000.0, 2000.0, "green"), (t["red_at"], t["green_at"], t["last"]))
        self.assertEqual("timed", t["basis"])
        self.assertEqual("proven", self.ac("AC-1")["status"])

    def test_the_approvals_and_gate_verdicts_that_let_each_part_in_are_on_the_trace(self):
        parse = self.code("parse")
        self.assertEqual([("redeemed", "jas", "p-1")],
                         [(a["decision"], a["actor"], a["proposal"]) for a in parse["approvals"]])
        verdicts = {(g["gate"], g["verdict"]) for g in self.trace["gates"]}
        self.assertIn(("no-code-without-failing-test", "red"), verdicts)
        self.assertIn(("stop-evidence", "skipped"), verdicts)

    def test_the_trace_is_deterministic(self):
        again = P.trace(self.d, run="r1")
        self.assertEqual(json.dumps(self.trace, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_the_change_set_is_the_sessions_recorded_writes(self):
        self.assertEqual(["src/app/core.py", "tests/test_core.py"],
                         [f["path"] for f in self.trace["files"]])
        self.assertEqual("run", self.trace["target"]["kind"])
        self.assertTrue(self.trace["files"][1]["is_test"])


# ===================================================================== the risk overlay

class TheRiskOverlayFlagsWhatIsNotProven(_Repo):

    def test_an_AC_with_no_test_is_flagged_untested(self):
        self.assertEqual("untested", self.ac("AC-3")["status"])
        self.assertEqual(["AC-3"], self.trace["risk"]["untested_acs"])

    def test_a_test_that_never_went_red_is_not_TDD_proven(self):
        t = self.tnode("test_total")
        self.assertEqual("green-only", t["tdd"])
        self.assertIsNone(t["red_at"])
        self.assertIn(t["id"], self.trace["risk"]["tests_not_tdd_proven"])
        self.assertEqual("green-not-tdd", self.ac("AC-2")["status"])

    def test_code_changed_without_a_covering_test_is_flagged(self):
        err = self.code("report_error")
        self.assertEqual("untested", err["coverage"])
        self.assertEqual([], err["tests"])
        self.assertIn(err["id"], self.trace["risk"]["code_without_test"])
        self.assertGreater(err["risk"], 0)
        self.assertEqual(0, self.code("parse")["risk"], "a covered definition carries no risk")

    def test_the_blast_radius_of_each_change_appears(self):
        parse = self.code("parse")
        self.assertGreaterEqual(parse["fan_in"], 1, "total() calls parse()")
        self.assertGreaterEqual(parse["blast"], 1)
        self.assertIn("parse", {c["id"].split("#")[1] for c in self.trace["nodes"]["code"]})
        self.assertIsInstance(self.trace["risk"]["blast"]["fan_in_total"], int)

    def test_gates_red_or_skipped_are_on_the_overlay(self):
        self.assertEqual(["no-code-without-failing-test"],
                         [g["gate"] for g in self.trace["risk"]["gates_red"]])
        self.assertEqual(["stop-evidence"], [g["gate"] for g in self.trace["risk"]["gates_skipped"]])
        self.assertGreater(self.trace["risk"]["score"], 0)


# ===================================================================== without a graph, without a run

class ItWorksWithNoGraphAndNoRun(_Repo):

    def test_with_no_code_review_graph_the_floor_answers_and_says_so(self):
        """No `code-review-graph` is installed on this box's fixture: the AST floor answers the
        Python question and the trace NAMES its backend and basis, never pretending to a graph."""
        g = self.trace["graph"]
        self.assertTrue(g["backend"], "the backend that answered is named")
        self.assertNotIn("code-review-graph", g["backend"])
        self.assertIn(g["basis"], ("structural", "floor", "approximate"))
        self.assertTrue(g["bases"], "the bases of the answers are counted")

    def test_a_commit_range_on_a_repo_with_no_run_traces_code_to_tests_and_says_no_run(self):
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "change")
        try:
            t = P.trace(self.d, base="HEAD~1", head="HEAD")
        finally:
            _git(self.d, "reset", "-q", "--soft", "HEAD~1")
            _git(self.d, "reset", "-q")
        self.assertEqual("range", t["target"]["kind"])
        self.assertEqual([], t["nodes"]["acs"])
        self.assertIn("no run is attached", t["note"])
        self.assertIn("report_error", {c["symbol"] for c in t["nodes"]["code"]})
        self.assertIn("code:src/app/core.py#report_error", t["risk"]["code_without_test"])

    def test_the_working_tree_is_a_target_a_stranger_can_ask_before_any_run(self):
        """The launch demo's first click on a fresh repo: `live=1` traces the uncommitted change
        against HEAD — code → tests, blast radius, flags — with no run, no spec, no API key."""
        t = P.trace(self.d, live=True)
        self.assertEqual("live", t["target"]["kind"])
        self.assertEqual(["src/app/core.py", "tests/test_core.py"], [f["path"] for f in t["files"]])
        self.assertIn("report_error", {c["symbol"] for c in t["nodes"]["code"]})
        self.assertIn("no run is attached", t["note"])
        self.assertIn("test_total", {x["symbol"] for x in t["nodes"]["tests"]},
                      "a changed test file's tests are located, changed tests")

    def test_a_session_target_resolves_its_bound_run(self):
        t = P.trace(self.d, session="S1")
        self.assertEqual(("session", "r1"), (t["target"]["kind"], t["target"]["run"]))
        self.assertEqual(3, t["page"]["total_acs"])

    def test_a_target_nobody_named_or_a_run_without_state_is_refused(self):
        with self.assertRaises(P.TraceRefused):
            P.trace(self.d)
        with self.assertRaises(P.TraceRefused):
            P.trace(self.d, run="nope")
        with self.assertRaises(P.TraceRefused):
            P.trace(self.d, base="HEAD")


# ===================================================================== scale: bounded, paged, said

class ScaleIsBoundedByTheChangeSetAndPaged(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)

    def tearDown(self):
        nav.close_layers()
        self._t.cleanup()

    def test_the_graph_is_asked_once_per_changed_file_and_the_repo_is_never_walked(self):
        fixture(self.d)
        calls = []
        real = nav.impact

        def counting(root, path, lines="", symbols=None, budget="default"):
            calls.append(path)
            return real(root, path, lines, symbols, budget=budget)
        with mock.patch.object(nav, "impact", counting), \
                mock.patch.object(nav, "walk_files", side_effect=AssertionError("repo walked")):
            P.trace(self.d, run="r1")
        self.assertEqual(["src/app/core.py"], calls, "one question per changed non-test file")

    def test_thousands_of_ACs_are_paged_never_capped_and_a_reduced_picture_says_so(self):
        acs = tuple(f"AC-{i}" for i in range(1, 601))
        fixture(self.d, acs=acs)
        real = ["tests/test_core.py::test_parse", "tests/test_core.py::test_total"]
        TR.record_test_run(self.d, "r1", passed=True, command="x",
                           ac_map={a: [real[i % 4 == 1]] for i, a in enumerate(acs) if i % 2},
                           at=3000.0)
        p1 = P.trace(self.d, run="r1", size=250)
        self.assertEqual((1, 250, 2), (p1["page"]["n"], p1["page"]["rows"], p1["page"]["next"]))
        self.assertEqual(600, p1["page"]["total_acs"])
        self.assertGreater(p1["page"]["total_rows"], 600, "code no AC reaches is a row too")
        self.assertTrue(p1["draw"]["reduced"])
        self.assertIn("of", p1["draw"]["note"])
        self.assertLessEqual(len(p1["draw"]["shown"]), p1["draw"]["limit"])
        self.assertEqual(250, len(p1["nodes"]["acs"]), "the data is all there; only the picture is reduced")
        p3 = P.trace(self.d, run="r1", size=250, page=3)
        self.assertIsNone(p3["page"]["next"])
        seen = set()
        for n in (1, 2, 3):
            seen.update(a["ac"] for a in P.trace(self.d, run="r1", size=250, page=n)["nodes"]["acs"])
        self.assertEqual(set(acs), seen)
        self.assertEqual(299, len(p1["risk"]["untested_acs"]), "the overlay counts the WHOLE run (AC-1 is tested by the fixture)")


# ===================================================================== ambiguity is said, never guessed

class ASameNamedTestIsAmbiguousNeverGuessed(unittest.TestCase):
    """Coordinator ruling 1, in its round-5 form (the fallback rule): a recorded test id with no
    file is never PROOF — not when two tests answer to it, not when one does. It is listed with
    the full ids that could be meant, lit on none of them, and the AC stays untested."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        fixture(self.d)
        # a SECOND changed test file with a function of the SAME name as tests/test_core.py's
        path = os.path.join(self.d, "tests", "test_more.py")
        S.capture_before_first_write(self.d, path, "S1", "r1")
        _write(self.d, "tests/test_more.py",
               "from app.core import parse\n\n\ndef test_parse():\n    assert parse('') == []\n")
        S.capture(self.d, path, S.AFTER, "S1", "r1")
        TR.record_test_run(self.d, "r1", passed=False, tests=["test_parse"],
                           ac_map={"AC-3": ["test_parse"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, tests=["test_parse"], at=3100.0)

    def tearDown(self):
        nav.close_layers()
        self._t.cleanup()

    def test_a_bare_name_two_tests_answer_to_is_one_ambiguous_node_with_both_candidates(self):
        t = P.trace(self.d, run="r1")
        node = next(x for x in t["nodes"]["tests"] if x["id"] == "test:test_parse")
        self.assertEqual("unlocated", node["state"])
        self.assertIn("bare id: run with full ids to prove", node["reason"])
        self.assertIn("tests/test_core.py::test_parse", node["reason"])
        self.assertIn("tests/test_more.py::test_parse", node["reason"])
        self.assertFalse(node["located"])
        self.assertEqual([], node["code"], "an unlocated test links to no code")
        ac3 = next(a for a in t["nodes"]["acs"] if a["ac"] == "AC-3")
        self.assertEqual(["test:test_parse"], ac3["tests"])
        self.assertEqual(("untested", []), (ac3["status"], ac3["proven_by"]))
        for cand in ("test:tests/test_core.py::test_parse", "test:tests/test_more.py::test_parse"):
            located = next(x for x in t["nodes"]["tests"] if x["id"] == cand)
            self.assertNotIn("ac:AC-3", located["acs"], "a candidate was lit up by the bare name")
        self.assertIn("test:test_parse", t["risk"]["tests_non_proving"])

    def test_naming_the_file_disambiguates(self):
        TR.record_test_run(self.d, "r1", passed=True, tests=["tests/test_more.py::test_parse"],
                           ac_map={"AC-3": ["tests/test_more.py::test_parse"]}, at=4000.0)
        t = P.trace(self.d, run="r1")
        ac3 = next(a for a in t["nodes"]["acs"] if a["ac"] == "AC-3")
        self.assertIn("test:tests/test_more.py::test_parse", ac3["tests"])
        more = next(x for x in t["nodes"]["tests"] if x["id"] == "test:tests/test_more.py::test_parse")
        self.assertEqual("present", more["state"])
        self.assertNotIn(more["id"], t["risk"]["tests_non_proving"])


# ===================================================================== verification F1–F8 (constructions reused)

def _ac(tr, a):
    return next(x for x in tr["nodes"]["acs"] if x["ac"] == a)


def _t(tr, nid):
    return next(x for x in tr["nodes"]["tests"] if x["id"] == nid)


class AProofSurfaceNeverSaysProvenFalsely(unittest.TestCase):
    """The independent verification's constructions, as the rules they protect (F1, F2, F7, F8):
    a test's state is its LATEST outcome; two spellings of one test are one test; an ambiguous id
    proves nothing; a never-run twin inherits nothing; a deleted test reads deleted; a test whose
    first recorded run was green was never test-first."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def test_F1_a_test_whose_latest_run_is_red_is_red_and_so_is_its_AC(self):
        TR.record_test_run(self.d, "r1", passed=False, command="pytest",
                           tests=["tests/test_core.py::test_parse"],
                           ac_map={"AC-1": ["tests/test_core.py::test_parse"]}, at=3000.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_core.py::test_parse")
        self.assertEqual("red", node["tdd"], "the bare id's proof must not beat the node id's red")
        self.assertEqual(["tests/test_core.py::test_parse"], node["recorded_ids"])
        self.assertEqual("red", _ac(tr, "AC-1")["status"])
        self.assertEqual(["AC-1"], tr["risk"]["red_acs"])
        self.assertNotIn("test:test_parse", {t["id"] for t in tr["nodes"]["tests"]})

    def test_F1_a_red_under_another_spelling_of_the_full_id_is_the_tests_red(self):
        """`tests\\test_core.py::test_parse` IS `tests/test_core.py::test_parse`: its latest red —
        mapped to no AC — still makes the test (and its AC) red, never proven."""
        TR.record_test_run(self.d, "r1", passed=False, command="pytest",
                           tests=["tests\\test_core.py::test_parse"], at=2500.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_core.py::test_parse")
        self.assertEqual(("red", "red", 2500.0), (node["tdd"], node["last"], node["last_at"]))
        self.assertEqual("red", _ac(tr, "AC-1")["status"])
        TR.record_test_run(self.d, "r1", passed=True, command="pytest",
                           tests=["./tests/test_core.py::test_parse"], at=2600.0)
        node = _t(P.trace(self.d, run="r1"), "test:tests/test_core.py::test_parse")
        self.assertNotEqual("proven", node["tdd"],
                            "every spelling needs its own red→green; ./… was only ever green")

    def test_F2_an_ambiguous_id_proves_nothing_and_a_never_run_twin_inherits_nothing(self):
        """A same-named twin in another file is another test: it never ran, so it borrows no
        history; the bare name proves nothing; the full id still proves its own AC."""
        _write(self.d, "tests/test_more.py",
               "from app.core import parse\n\n\ndef test_parse():\n    assert parse('') == []\n")
        TR.record_test_run(self.d, "r1", passed=False, tests=["test_parse"],
                           ac_map={"AC-3": ["test_parse"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, tests=["test_parse"], at=3100.0)
        tr = P.trace(self.d, run="r1")
        bare = _t(tr, "test:test_parse")
        self.assertEqual(("unlocated", "unlocated"), (bare["state"], bare["tdd"]))
        self.assertEqual("untested", _ac(tr, "AC-3")["status"],
                         "an AC proven only by an id the trace cannot identify is not proven")
        self.assertEqual(["test:tests/test_core.py::test_parse"], _ac(tr, "AC-1")["proven_by"])
        live = P.trace(self.d, live=True)
        twin = _t(live, "test:tests/test_more.py::test_parse")
        self.assertEqual("unrecorded", twin["tdd"], "the twin never ran and borrows no history")
        self.assertEqual([], twin["recorded_ids"])

    def test_F7_a_deleted_test_reads_deleted_and_proves_nothing(self):
        os.remove(os.path.join(self.d, "tests", "test_core.py"))
        tr = P.trace(self.d, run="r1")
        gone = [t for t in tr["nodes"]["tests"] if t["symbol"] == "test_parse"]
        self.assertEqual(["deleted"], [t["state"] for t in gone])
        self.assertEqual(["deleted"], [t["tdd"] for t in gone])
        self.assertIn(gone[0]["id"], tr["risk"]["tests_deleted"])
        self.assertNotEqual("proven", _ac(tr, "AC-1")["status"])
        self.assertEqual("untested", _ac(tr, "AC-1")["status"])

    def test_F7_an_id_that_never_existed_is_unlocated_not_deleted(self):
        """Coordinator ruling: "deleted" only on positive evidence. A recorded id whose function
        git never shows removed is UNLOCATED — it proves nothing, and the UI says why."""
        TR.record_test_run(self.d, "r1", passed=True, command="x",
                           tests=["tests/test_core.py::test_gone"],
                           ac_map={"AC-3": ["tests/test_core.py::test_gone"]}, at=3000.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_core.py::test_gone")
        self.assertEqual(("unlocated", "unlocated"), (node["state"], node["tdd"]))
        self.assertNotIn(node["id"], tr["risk"]["tests_deleted"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])
        js = (STATIC / "features" / "proof.js").read_text(encoding="utf-8")
        self.assertIn("not located in this tree", js)

    def test_F7_a_definition_the_change_set_removed_reads_deleted(self):
        """Positive evidence: the diff took `test_parse` out of tests/test_core.py (the file
        stays) — the recorded test is DELETED, by file::name and by bare name alike."""
        _write(self.d, "tests/test_core.py", "from app.core import parse\n\n\ndef test_other():\n"
                                            "    assert parse('a') == ['a']\n")
        TR.record_test_run(self.d, "r1", passed=True, command="x", tests=["test_parse"],
                           ac_map={"AC-3": ["test_parse"]}, at=3000.0)
        tr = P.trace(self.d, run="r1")
        states = {t["id"]: t["state"] for t in tr["nodes"]["tests"]}
        self.assertEqual("deleted", states["test:tests/test_core.py::test_parse"])
        self.assertEqual("deleted", states["test:test_parse"], "the bare id: its definition is gone")
        self.assertNotEqual("proven", _ac(tr, "AC-1")["status"])
        self.assertIn("test:tests/test_core.py::test_parse", tr["risk"]["tests_deleted"])
        self.assertEqual("unlocated", states["test:tests/test_core.py::test_total"],
                         "test_total was never committed: no evidence of removal")

    def test_a_locator_miss_on_a_repo_the_floor_cannot_outline_is_unlocated(self):
        """The lexical floor on a non-Python repo (no CRG): a bare recorded id that no outline can
        place is UNLOCATED, never DELETED, and never proof."""
        _write(self.d, "src/lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
        TR.record_test_run(self.d, "r1", passed=False, command="cargo test", tests=["test_add"],
                           ac_map={"AC-3": ["test_add"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, command="cargo test", tests=["test_add"],
                           at=3100.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:test_add")
        self.assertEqual("unlocated", node["state"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])

    def test_a_bare_id_not_in_the_change_set_is_located_in_the_tree(self):
        """The common case: only code changed, the test file did not — the test's FULL id is
        located in the tree (git + its file's AST) and proves its AC; its bare name never does,
        and its AC says why (K1)."""
        _write(self.d, "tests/test_errs.py",
               "from app.core import report_error\n\n\ndef test_err():\n    assert report_error(1)\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "errs")
        for tid, ac in (("tests/test_errs.py::test_err", "AC-3"), ("test_err", "AC-2")):
            TR.record_test_run(self.d, "r1", passed=False, command="x", tests=[tid],
                               ac_map={ac: [tid]}, at=3000.0)
            TR.record_test_run(self.d, "r1", passed=True, command="x", tests=[tid], at=3100.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_errs.py::test_err")
        self.assertEqual(("present", True, "proven"), (node["state"], node["located"], node["tdd"]))
        self.assertEqual(4, node["line"])
        self.assertEqual("proven", _ac(tr, "AC-3")["status"])
        self.assertEqual("unlocated", _t(tr, "test:test_err")["state"])
        self.assertEqual("green-not-tdd", _ac(tr, "AC-2")["status"],
                         "AC-2 keeps only test_total's green; the bare test_err adds nothing")
        self.assertIn("bare id: run with full ids to prove (test_err)", _ac(tr, "AC-2")["note"])

    def test_F8_green_then_red_then_green_was_never_test_first(self):
        h = {}
        TR._advance(h, ["gbr"], True, 1.0)
        TR._advance(h, ["gbr"], False, 2.0)
        TR._advance(h, ["gbr"], True, 3.0)
        self.assertEqual("green-only", P.tdd_verdict("gbr", set(), set(), h)["tdd"])
        self.assertEqual("green", h["gbr"]["first"])


class ACommitRangeIsJudgedAtItsHead(unittest.TestCase):
    """Verification F3: a range `base..head` is judged at `head`. An uncommitted test must never
    make code `covered`; a working tree that is not `head` makes coverage `unknown`, said."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "c2")

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def test_an_uncommitted_test_never_counts_for_a_range(self):
        _write(self.d, "tests/test_err.py",
               "from app.core import report_error\n\n\ndef test_err():\n    assert report_error(1)\n")
        tr = P.trace(self.d, base="HEAD~1", head="HEAD")
        cov = {c["symbol"]: c["coverage"] for c in tr["nodes"]["code"]}
        self.assertNotEqual("covered", cov["report_error"])
        self.assertEqual("unknown", cov["report_error"])
        self.assertIn("tests/test_err.py is untracked", tr["graph"]["note"])
        self.assertIn("judged at HEAD", tr["graph"]["note"])

    def test_an_uncommitted_change_to_a_file_the_walk_crosses_makes_coverage_unknown(self):
        """Coordinator ruling: not only changed and test paths — a dirty file the blast walk
        CROSSED (a caller of a changed definition) also makes coverage unknown, said."""
        _write(self.d, "src/app/use.py", "from app.core import parse\n\n\ndef use(t):\n"
                                        "    return parse(t)\n")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "c3")
        _write(self.d, "src/app/core.py", CORE_NEW.replace("return []", "return list()"))
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "c4")
        clean = P.trace(self.d, base="HEAD~1", head="HEAD")
        self.assertEqual({"parse": "covered"}, {c["symbol"]: c["coverage"] for c in clean["nodes"]["code"]})
        _write(self.d, "src/app/use.py", "from app.core import parse\n\n\ndef use(t):\n"
                                        "    return parse(t) + ['x']\n")
        tr = P.trace(self.d, base="HEAD~1", head="HEAD")
        self.assertEqual({"unknown"}, {c["coverage"] for c in tr["nodes"]["code"]})
        self.assertIn("src/app/use.py is modified in the working tree and the blast walk crossed it",
                      tr["graph"]["note"])
        _write(self.d, "README.md", "unrelated\n")
        _git(self.d, "checkout", "-q", "--", "src/app/use.py")
        tr2 = P.trace(self.d, base="HEAD~1", head="HEAD")
        self.assertIn("covered", {c["coverage"] for c in tr2["nodes"]["code"]},
                      "a dirty file the walk never crossed is not a reason")

    def test_a_clean_tree_at_head_answers_coverage(self):
        tr = P.trace(self.d, base="HEAD~1", head="HEAD")
        cov = {c["symbol"]: c["coverage"] for c in tr["nodes"]["code"]}
        self.assertEqual("untested", cov["report_error"])
        self.assertEqual("covered", cov["parse"])
        self.assertNotIn("judged at", tr["graph"]["note"])

    def test_a_working_tree_at_another_commit_or_with_a_deleted_file_is_said(self):
        os.remove(os.path.join(self.d, "src", "app", "core.py"))
        tr = P.trace(self.d, base="HEAD~1", head="HEAD")
        syms = {c["symbol"]: c["coverage"] for c in tr["nodes"]["code"]}
        self.assertIn("report_error", syms, "definitions come from head, not the (deleted) file")
        self.assertEqual({"unknown"}, set(syms.values()))
        self.assertIn("src/app/core.py is deleted", tr["graph"]["note"])


class GatesAndApprovalsAreNeverSilentlyDropped(unittest.TestCase):
    """Verification F5: no cap on the ledger read — a skipped gate buried under 5 MB of other
    runs' rows is still on the overlay; a Karpathy row that names no run is attached by the
    run's time window and labelled; a row with no verdict reads unknown, never passed."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def test_F5_a_gate_override_under_5MB_of_other_rows_is_still_on_the_overlay(self):
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata"))
        pad = "x" * 2000
        for _ in range(2500):
            led.record("write_gate", run="other", decision="allowed", target="f.py", reason=pad)
        tr = P.trace(self.d, run="r1")
        self.assertEqual(["stop-evidence"], [g["gate"] for g in tr["risk"]["gates_skipped"]])
        self.assertEqual(1, len(tr["approvals"]))
        self.assertNotIn("other", {g["path"] for g in tr["gates"]} | {"other"} - {"other"})

    def test_F5_a_karpathy_row_with_no_run_is_attached_by_window_and_labelled(self):
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata"))
        led.record("karpathy_gate", gate="think-before-code", phase="x", passed=False,
                   detail="failed")
        led.record("gate", gate="spec-completeness", run="r1", phase="checked")
        tr = P.trace(self.d, run="r1")
        k = next(g for g in tr["gates"] if g["gate"] == "think-before-code")
        self.assertEqual(("red", "time-window"), (k["verdict"], k["attached"]))
        self.assertIn("names no run", k["detail"])
        self.assertIn(k, tr["risk"]["gates_red"])
        sp = next(g for g in tr["gates"] if g["gate"] == "spec-completeness")
        self.assertEqual("unknown", sp["verdict"], "a row that says neither way is not passed")
        self.assertIn(sp, tr["risk"]["gates_unknown"])

    def test_F5_a_row_before_the_run_started_is_not_attached_by_window(self):
        from mokata.govern.resume import CHECKPOINT_PREFIX
        from mokata.ide.plans import _run_store
        st = _run_store(self.d, "r1")
        cp = dict(st.read(CHECKPOINT_PREFIX + "r1"))
        cp["created_at"] = 4_000_000_000.0          # the run "starts" in the far future
        st.write(CHECKPOINT_PREFIX + "r1", cp)
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata"))
        led.record("karpathy_gate", gate="think-before-code", phase="x", passed=False)
        tr = P.trace(self.d, run="r1")
        self.assertNotIn("think-before-code", {g["gate"] for g in tr["gates"]})


class TestsReachedByNothingAreStillPaged(unittest.TestCase):
    """Verification F6: a test with no AC and no changed code is a row of its own — counted AND
    on a page, never only in the totals."""

    def test_F6_lone_tests_are_rows(self):
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            fixture(d)
            _write(d, "tests/test_misc.py",
                   "def test_alpha():\n    assert 1\n\n\ndef test_beta():\n    assert 1\n")
            tr = P.trace(d, live=True, size=2000)
            seen = {t["id"] for t in tr["nodes"]["tests"]}
            self.assertEqual(tr["page"]["total_tests"], len(seen))
            self.assertIn("test:tests/test_misc.py::test_alpha", seen)
            self.assertIsNone(tr["page"]["next"])
            small = P.trace(d, live=True, size=2)
            pages, got = 0, set()
            n = 1
            while n:
                pg = P.trace(d, live=True, size=2, page=n)
                got |= {t["id"] for t in pg["nodes"]["tests"]}
                pages += 1
                n = pg["page"]["next"]
            self.assertEqual(seen, got, "every test appears on some page")
            self.assertGreater(pages, 1)
            self.assertNotIn("next page continues", small["draw"]["note"] if small["page"]["next"] is None else "")
            nav.close_layers()


# ===================================================================== THE RULE (re-verification G1–G6)

class ProofNeedsExactlyOnePositivelyLocatedDefinition(unittest.TestCase):
    """THE RULE (proof.py docstring; `resolve_test`): a recorded test proves only when it resolves
    to exactly one definition, located with positive evidence in the tree being judged. The
    re-verification's constructions (probe5.py X2 / X1b / X5 / X1 / X6) as the rules they protect."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def test_G1_a_bare_name_resolves_against_the_whole_tree_not_the_change_set(self):
        """One hit in the change set plus a same-named committed twin elsewhere: the bare name
        proves nothing (it never does); each full id is its own test."""
        _write(self.d, "tests/test_other.py", "def test_parse():\n    assert int('1') == 1\n")
        _git(self.d, "add", "tests/test_other.py")
        _git(self.d, "commit", "-qm", "twin")
        TR.record_test_run(self.d, "r1", passed=False, tests=["test_parse"],
                           ac_map={"AC-3": ["test_parse"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, tests=["test_parse"], at=3100.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("unlocated", _t(tr, "test:test_parse")["state"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])
        self.assertEqual("proven", _ac(tr, "AC-1")["status"], "the full id is not the bare name")

    def test_G2_a_file_the_floor_cannot_outline_is_unparsed_never_present(self):
        _write(self.d, "tests/test_lib.rs", "// test_add was removed\n")
        TR.record_test_run(self.d, "r1", passed=False, command="cargo",
                           tests=["tests/test_lib.rs::test_add"],
                           ac_map={"AC-3": ["tests/test_lib.rs::test_add"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, command="cargo",
                           tests=["tests/test_lib.rs::test_add"], at=3500.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_lib.rs::test_add")
        self.assertEqual(("unparsed", "unparsed"), (node["state"], node["tdd"]))
        self.assertIn("no outline", node["reason"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])
        self.assertIn(node["id"], tr["risk"]["tests_non_proving"])

    def test_G3_a_gitignored_test_the_walk_crosses_never_counts(self):
        d = os.path.realpath(tempfile.mkdtemp())
        try:
            _git(d, "init", "-q", "-b", "main")
            _git(d, "config", "user.email", "t@t")
            _git(d, "config", "user.name", "t")
            _write(d, ".gitignore", "tests/test_local.py\n")
            _write(d, "src/app/__init__.py", "")
            _write(d, "src/app/core.py", "def keep():\n    return 1\n")
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "base")
            _write(d, "src/app/core.py", "def keep():\n    return 1\n\n\ndef report_error(pos):\n"
                                        "    return 'bad %d' % pos\n")
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "head")
            _write(d, "tests/test_local.py", "from app.core import report_error\n\n\n"
                                            "def test_local():\n    assert report_error(1)\n")
            tr = P.trace(d, base="HEAD~1", head="HEAD")
            cov = {c["symbol"]: c["coverage"] for c in tr["nodes"]["code"]}
            self.assertNotEqual("covered", cov["report_error"])
            self.assertIn("tests/test_local.py is ignored", tr["graph"]["note"])
            _write(d, "src/app/core.py", "def keep():\n    return 2\n\n\ndef report_error(pos):\n"
                                        "    return 'bad %d!' % pos\n")
            live = P.trace(d, live=True)
            t = next(x for x in live["nodes"]["tests"] if x["symbol"] == "test_local")
            self.assertEqual("ignored", t["state"], "an ignored test file never counts, live either")
            self.assertNotEqual("covered", {c["symbol"]: c["coverage"]
                                            for c in live["nodes"]["code"]}["report_error"])
        finally:
            nav.close_layers()
            shutil.rmtree(d, ignore_errors=True)

    def test_G5_a_parametrised_id_maps_to_its_base_test_and_every_param_must_meet_the_rule(self):
        self.assertEqual(("test_total", "a"), (P.test_name_of("tests/test_core.py::test_total[a]"),
                                               P.test_param_of("tests/test_core.py::test_total[a]")))
        self.assertEqual("tests/test_core.py", P.test_path_of("tests/test_core.py::test_total[1-2]"))
        TR.record_test_run(self.d, "r1", passed=False, command="pytest",
                           tests=["tests/test_core.py::test_parse[a]", "tests/test_core.py::test_parse[b]"],
                           ac_map={"AC-3": ["tests/test_core.py::test_parse[a]",
                                            "tests/test_core.py::test_parse[b]"]}, at=3000.0)
        TR.record_test_run(self.d, "r1", passed=True, command="pytest",
                           tests=["tests/test_core.py::test_parse[a]"], at=3500.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_core.py::test_parse")
        self.assertEqual("present", node["state"])
        self.assertEqual(["a", "b"], node["params"])
        self.assertEqual("red", node["tdd"], "param [b] is still red: the test is not proven")
        TR.record_test_run(self.d, "r1", passed=True, command="pytest",
                           tests=["tests/test_core.py::test_parse[b]"], at=3600.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("proven", _t(tr, "test:tests/test_core.py::test_parse")["tdd"])
        self.assertEqual("proven", _ac(tr, "AC-3")["status"])

    def test_G6_a_rename_in_git_status_z_is_parsed_old_path_deleted(self):
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "c2")
        _git(self.d, "mv", "tests/test_core.py", "tests/test_core2.py")
        st = P.status_paths(self.d)
        self.assertEqual("deleted", st.get("tests/test_core.py"))
        self.assertEqual("modified", st.get("tests/test_core2.py"))
        self.assertNotIn("ts/test_core.py", st)

    def test_a_bare_id_never_proves_where_the_backend_cannot_enumerate_definitions(self):
        """Coordinator ruling: uniqueness the backend cannot prove is not uniqueness. A JS repo on
        the lexical floor (no CRG/AST) with same-named twins: a bare id is `unlocated` with the
        reason; a file-qualified id is judged by its file (here: unparsed — JS has no outline on
        the floor), and nothing proves."""
        d = os.path.realpath(tempfile.mkdtemp())
        try:
            _git(d, "init", "-q", "-b", "main")
            _git(d, "config", "user.email", "t@t")
            _git(d, "config", "user.name", "t")
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            _write(d, "src/add.js", "function add(a, b) { return a + b; }\nmodule.exports = { add };\n")
            _write(d, "tests/a.test.js", "const { add } = require('../src/add');\n"
                                          "function test_add() { return add(1, 2) === 3; }\n")
            _write(d, "tests/b.test.js", "function test_add() { return true; }\n")
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "base")
            surface = Surface.load(d)
            PipelineCheckpoint(surface.state, "r1").ensure_registered()
            surface.state.write("emitted_spec__r1", sealed({"title": "t", "approach": "a", "domains": [],
                                                             "criteria": [{"id": "AC-1", "text": "adds"},
                                                                          {"id": "AC-2", "text": "adds too"}]}))
            RI.bind_registered(d, "S1", "r1")
            path = os.path.join(d, "src", "add.js")
            S.capture_before_first_write(d, path, "S1", "r1")
            _write(d, "src/add.js", "function add(a, b) { return a + b + 0; }\nmodule.exports = { add };\n")
            S.capture(d, path, S.AFTER, "S1", "r1")
            TR.record_test_run(d, "r1", passed=False, command="npm test", tests=["test_add"],
                               ac_map={"AC-1": ["test_add"]}, at=1000.0)
            TR.record_test_run(d, "r1", passed=True, command="npm test", tests=["test_add"], at=2000.0)
            TR.record_test_run(d, "r1", passed=False, command="npm test", tests=["tests/a.test.js::test_add"],
                               ac_map={"AC-2": ["tests/a.test.js::test_add"]}, at=3000.0)
            TR.record_test_run(d, "r1", passed=True, command="npm test", tests=["tests/a.test.js::test_add"],
                               at=4000.0)
            tr = P.trace(d, run="r1")
            bare = _t(tr, "test:test_add")
            self.assertEqual("unlocated", bare["state"])
            self.assertIn("bare id: run with full ids to prove", bare["reason"])
            self.assertNotEqual("proven", _ac(tr, "AC-1")["status"])
            q = _t(tr, "test:tests/a.test.js::test_add")
            self.assertEqual("unparsed", q["state"], "JS has no outline on the floor: not present")
            self.assertNotEqual("proven", _ac(tr, "AC-2")["status"])
            self.assertEqual([], [a for a in tr["nodes"]["acs"] if a["proven_by"]])
        finally:
            nav.close_layers()
            shutil.rmtree(d, ignore_errors=True)

    def test_G4_the_progress_log_is_streamed_and_cancellable(self):
        """A log of other runs' events is read line by line: memory holds this run's rows only,
        and a Cancel between lines stops the read."""
        from mokata.knowledge import cancel as C
        from mokata.progress_events import ProgressLog
        from mokata.tdd_state import state_dir
        log = ProgressLog.from_state_dir(state_dir(self.d))
        with open(log.path, "a", encoding="utf-8") as fh:
            for i in range(6000):
                fh.write(json.dumps({"type": "stage_enter", "stage": "develop", "run_id": "other",
                                     "ts": "2026-01-01T00:00:00Z", "data": {}}) + "\n")
        with mock.patch.object(ProgressLog, "read_events",
                               side_effect=AssertionError("the whole log was materialised")):
            events = P._log_events(self.d, "r1")
        self.assertTrue(all(e["run_id"] == "r1" for e in events))
        flag = threading.Event()
        flag.set()
        with C.scope(flag), self.assertRaises(C.Cancelled):
            P._log_events(self.d, "r1")


# ===================================================================== a test IS its full id (re-verification 3, H1–H3)

CLS_TWINS = ("from app.core import parse\n\n\nclass TestA:\n    def test_x(self):\n"
             "        assert parse('a')\n\n\nclass TestB:\n    def test_x(self):\n"
             "        assert parse('b')\n")


class ATestIsItsFullQualifiedId(unittest.TestCase):
    """THE RULE (round 5, the fallback form — `proof.resolve_test`): a test is identified by its
    full id `path::Class::name`; only such an id, its file tracked and its AST binding that key
    exactly once, can prove. The re-verification's constructions (probe8.py R1, R1b, R2b) and the
    edges of the same rule, each as the user-visible rule it protects."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def _commit(self, rel, text):
        _write(self.d, rel, text)
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", rel)

    def _red_green(self, tid, ac, at=3000.0):
        TR.record_test_run(self.d, "r1", passed=False, command="pytest", tests=[tid],
                           ac_map={ac: [tid]}, at=at)
        TR.record_test_run(self.d, "r1", passed=True, command="pytest", tests=[tid], at=at + 500)

    def test_R1_two_classes_with_one_method_name_are_two_tests_and_the_one_that_never_ran_proves_nothing(self):
        """H1: `TestA::test_x` went red→green for AC-1; `TestB::test_x` is only PLANNED for AC-3
        and never ran. They are two tests with two histories: AC-3 is not proven."""
        self._commit("tests/test_cls.py", CLS_TWINS)
        self._red_green("tests/test_cls.py::TestA::test_x", "AC-1")
        from types import SimpleNamespace
        TR.seed_ac_map(self.d, "r1", [SimpleNamespace(name="tests/test_cls.py::TestB::test_x",
                                                      ac_ids=["AC-3"])])
        tr = P.trace(self.d, run="r1")
        a = _t(tr, "test:tests/test_cls.py::TestA::test_x")
        b = _t(tr, "test:tests/test_cls.py::TestB::test_x")
        self.assertEqual(("present", "proven"), (a["state"], a["tdd"]))
        self.assertEqual(["tests/test_cls.py::TestA::test_x"], a["recorded_ids"])
        self.assertEqual(("present", "unrecorded"), (b["state"], b["tdd"]),
                         "TestB never ran: it borrows nothing from TestA")
        self.assertEqual(["tests/test_cls.py::TestB::test_x"], b["recorded_ids"])
        ac3 = _ac(tr, "AC-3")
        self.assertEqual(("unrun", []), (ac3["status"], ac3["proven_by"]))
        self.assertIn("test:tests/test_cls.py::TestA::test_x", _ac(tr, "AC-1")["proven_by"])
        self.assertNotIn("test:tests/test_cls.py::test_x", {t["id"] for t in tr["nodes"]["tests"]},
                         "never collapsed onto one path::name node")

    def test_R1_a_qualifier_less_id_of_a_name_the_file_binds_twice_is_ambiguous(self):
        self._commit("tests/test_cls.py", CLS_TWINS)
        self._red_green("tests/test_cls.py::test_x", "AC-3")
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_cls.py::test_x")
        self.assertEqual(("ambiguous", "ambiguous"), (node["state"], node["tdd"]))
        self.assertEqual(["test:tests/test_cls.py::TestA::test_x",
                          "test:tests/test_cls.py::TestB::test_x"], node["ambiguous"])
        self.assertEqual("ambiguous", _ac(tr, "AC-3")["status"])
        self.assertIn(node["id"], tr["risk"]["tests_ambiguous"])

    def test_R1b_a_class_that_does_not_exist_attaches_to_nothing(self):
        """H2: `TestZ::test_x` (no class TestZ) went red→green — it is UNLOCATED, never the
        file's TestA/TestB `test_x`, and its AC is untested."""
        self._commit("tests/test_cls.py", CLS_TWINS)
        self._red_green("tests/test_cls.py::TestZ::test_x", "AC-3")
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_cls.py::TestZ::test_x")
        self.assertEqual(("unlocated", False), (node["state"], node["located"]))
        self.assertIn("has no TestZ::test_x", node["reason"])
        self.assertEqual(("untested", []), (_ac(tr, "AC-3")["status"], _ac(tr, "AC-3")["proven_by"]))
        for other in ("test:tests/test_cls.py::TestA::test_x", "test:tests/test_cls.py::test_x"):
            self.assertNotIn(other, _ac(tr, "AC-3")["tests"])

    def test_R2b_a_bare_id_with_a_gitignored_copy_proves_nothing(self):
        """H3: a gitignored copy of the tests answers to the bare name too (pytest collects it):
        a bare id never proves, and the copy's own full id is `ignored`."""
        _write(self.d, ".gitignore", "tests/test_core_copy.py\n")
        _write(self.d, "tests/test_core_copy.py", TEST_NEW)
        self._red_green("test_parse", "AC-3")
        self._red_green("tests/test_core_copy.py::test_total", "AC-2", at=5000.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual(("untested", []), (_ac(tr, "AC-3")["status"], _ac(tr, "AC-3")["proven_by"]))
        self.assertEqual("unlocated", _t(tr, "test:test_parse")["state"])
        self.assertEqual("ignored", _t(tr, "test:tests/test_core_copy.py::test_total")["state"])
        self.assertEqual([], _ac(tr, "AC-2")["proven_by"])

    def test_a_partial_id_naming_a_method_without_its_class_is_unlocated(self):
        self._commit("tests/test_one.py", "class TestA:\n    def test_y(self):\n        assert 1\n")
        self._red_green("tests/test_one.py::test_y", "AC-3")
        self._red_green("tests/test_one.py::TestA::test_y", "AC-2", at=5000.0)
        tr = P.trace(self.d, run="r1")
        part = _t(tr, "test:tests/test_one.py::test_y")
        self.assertEqual("unlocated", part["state"])
        self.assertIn("tests/test_one.py::TestA::test_y", part["reason"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])
        self.assertIn("test:tests/test_one.py::TestA::test_y", _ac(tr, "AC-2")["proven_by"])

    def test_a_name_rebound_or_star_imported_in_its_scope_is_never_unique(self):
        """A def later rebound (an assignment, a second def, an import of the same name) or a
        scope with `from x import *` — the AST cannot say which object the runner collected."""
        self._commit("tests/test_rebind.py", "def test_a():\n    assert 1\n\n\ntest_a = None\n\n\n"
                                             "class TestS:\n    from os.path import *\n\n"
                                             "    def test_b(self):\n        assert 1\n")
        self._red_green("tests/test_rebind.py::test_a", "AC-3")
        self._red_green("tests/test_rebind.py::TestS::test_b", "AC-2", at=5000.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("ambiguous", _t(tr, "test:tests/test_rebind.py::test_a")["state"])
        self.assertEqual("ambiguous", _t(tr, "test:tests/test_rebind.py::TestS::test_b")["state"])
        self.assertEqual([], _ac(tr, "AC-3")["proven_by"] + _ac(tr, "AC-2")["proven_by"])

    def test_a_unittest_dotted_id_is_a_bare_id_and_never_proves(self):
        self._red_green("test_core.TestX.test_parse", "AC-3")
        tr = P.trace(self.d, run="r1")
        self.assertEqual("unlocated", _t(tr, "test:test_core.TestX.test_parse")["state"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])

    def test_a_full_id_of_a_function_that_is_not_a_test_never_proves(self):
        """`src/app/core.py::parse` went "red then green" on the record — but `parse` is code,
        not a test a runner collects: unlocated, and its AC untested."""
        self._red_green("src/app/core.py::parse", "AC-3")
        tr = P.trace(self.d, run="r1")
        self.assertEqual("unlocated", _t(tr, "test:src/app/core.py::parse")["state"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])

    def test_the_walk_keeps_each_class_twin_as_its_own_test(self):
        """A changed test file holding TestA.test_x and TestB.test_x shows TWO tests, each by its
        full id — the walk never collapses them onto one `path::test_x`."""
        path = os.path.join(self.d, "tests", "test_cls.py")
        S.capture_before_first_write(self.d, path, "S1", "r1")
        _write(self.d, "tests/test_cls.py", CLS_TWINS)
        S.capture(self.d, path, S.AFTER, "S1", "r1")
        tr = P.trace(self.d, run="r1")
        ids = {t["id"] for t in tr["nodes"]["tests"]}
        self.assertLessEqual({"test:tests/test_cls.py::TestA::test_x",
                              "test:tests/test_cls.py::TestB::test_x"}, ids)
        self.assertNotIn("test:tests/test_cls.py::test_x", ids)
        a = _t(tr, "test:tests/test_cls.py::TestA::test_x")
        self.assertEqual(("present", "TestA", 5), (a["state"], a["qualifier"], a["line"]))


def _oracle_label(tid):
    """The oracle's OWN spelling of a test's identity (independent of proof.py): `/`, no `./`,
    no `[param]`; "" for an id that names no file."""
    import re
    import posixpath
    base = re.sub(r"\[.*\]$", "", tid.strip())
    head, sep, rest = base.partition("::")
    head = head.replace("\\", "/")
    if not sep or not ("/" in head or head.endswith((".py", ".js", ".ts", ".go", ".rs"))):
        return ""
    head = posixpath.normpath(head)            # `./`, `//`, `/./`, `x/../x` — one file
    return f"{head}::{rest.replace('.', '::')}"


def _oracle_same(a, b, fold):
    """Two oracle labels name the same test: equal, the path compared case-blind where the
    filesystem folds case (`fold`)."""
    if not a or not b:
        return False
    if not fold:
        return a == b
    pa, _s, ra = a.partition("::")
    pb, _s, rb = b.partition("::")
    return pa.casefold() == pb.casefold() and ra == rb


def _oracle_could_be(spelling, path, key):
    """The oracle's own reading of J2: a spelling naming NO file could be the test `path::key`
    when its last segment is the test's name and what precedes it is [module segments ending
    the file's path] + [exactly the test's classes]."""
    import re
    segs = [x for x in re.split(r"[.:]+", re.sub(r"\[.*\]$", "", spelling.strip())) if x]
    parts = key.split(".")
    if not segs or segs[-1] != parts[-1]:
        return False
    pre, quals = segs[:-1], parts[:-1]
    mods = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    for cut in range(len(pre) + 1):
        if pre[cut:] == quals and (cut == 0 or mods[-cut:] == pre[:cut]):
            return True
    return False


def _oracle_bindings(text):
    """The oracle's OWN count of the names a Python file binds per class scope — recursive,
    parent-chain based, written apart from `proof.py_test_defs`: every def / class / assignment /
    import / loop or with target / del at any statement depth that is not inside a function,
    keyed `Cls.name`; `None` key-set member `*` for a star-import scope."""
    import ast as _ast
    out = {}

    def visit(stmts, scope):
        for n in stmts:
            names = []
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                names = [n.name]
            elif isinstance(n, _ast.ClassDef):
                names = [n.name]
                visit(n.body, scope + (n.name,))
            elif isinstance(n, (_ast.Import, _ast.ImportFrom)):
                names = [(a.asname or a.name.split(".")[0]) for a in n.names]
            else:
                for t in _ast.walk(n):
                    if isinstance(t, _ast.Name) and isinstance(t.ctx, (_ast.Store, _ast.Del)):
                        names.append(t.id)
                for fld in ("body", "orelse", "finalbody", "handlers", "cases"):
                    sub = getattr(n, fld, None) or []
                    for x in sub:
                        if hasattr(x, "body") and not isinstance(x, _ast.stmt):
                            if getattr(x, "name", None):
                                names.append(x.name)
                            visit(x.body, scope)
                    visit([x for x in sub if isinstance(x, _ast.stmt)], scope)
            for nm in names:
                k = ".".join(scope + (nm,))
                out[k] = out.get(k, 0) + 1
    visit(_ast.parse(text).body, ())
    return out


TEST_X = ("import unittest\n\n\nclass TestA(unittest.TestCase):\n    def test_x(self):\n"
          "        assert 1\n")


class RoundSixOneTestOneProof(unittest.TestCase):
    """Round 6 (re-verification J1-J8), each from the verifier's own construction: every alias of
    a full id is ONE test; a RED under any spelling that could be the test blocks its proof; a
    proof does not outlive an edit to the test; a test a runner would never collect is not
    present; a rootdir-relative id resolves against the pytest rootdir; a key bound twice and a
    spelling that only ever went green prove nothing."""

    def setUp(self):
        self._t_ = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t_.name)
        fixture(self.d)

    def tearDown(self):
        nav.close_layers()
        self._t_.cleanup()

    def _commit(self, rel, text):
        _write(self.d, rel, text)
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", rel)

    def _rec(self, tid, passed, at, ac=None):
        TR.record_test_run(self.d, "r1", passed=passed, command="pytest", tests=[tid],
                           ac_map={ac: [tid]} if ac else None, at=at)

    def _ids(self, tr):
        return {t["id"] for t in tr["nodes"]["tests"]}

    # -- J1: path aliases are ONE node, and a RED under any alias counts -------------------------
    def test_J1_every_path_alias_of_a_full_id_is_one_test_and_its_red_counts(self):
        """`tests/./x.py`, `tests//x.py`, `tests/../tests/x.py` name ONE file: one node, and a RED
        recorded under any of them after the green makes the test — and its AC — red."""
        self._commit("tests/test_w.py", TEST_X)
        self._rec("tests/test_w.py::TestA::test_x", False, 3000.0, "AC-3")
        self._rec("tests/test_w.py::TestA::test_x", True, 3100.0)
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])
        for i, alias in enumerate(("tests/./test_w.py", "tests//test_w.py",
                                   "tests/../tests/test_w.py")):
            self._rec(f"{alias}::TestA::test_x", False, 4000.0 + 100 * i)
            tr = P.trace(self.d, run="r1")
            self.assertEqual({"test:tests/test_w.py::TestA::test_x"},
                             {n for n in self._ids(tr) if "test_w" in n}, alias)
            self.assertEqual(("red", "red"), (_t(tr, "test:tests/test_w.py::TestA::test_x")["tdd"],
                                              _ac(tr, "AC-3")["status"]), alias)
            self._rec(f"{alias}::TestA::test_x", True, 4050.0 + 100 * i)

    def test_J1_a_case_variant_is_the_same_file_where_the_filesystem_folds_case(self):
        """On macOS / Windows (`core.ignorecase`), `Tests/test_w.py` IS `tests/test_w.py`: its RED
        after the green is the test's RED — never a second, untracked-but-present node."""
        if subprocess.run(["git", "-C", self.d, "config", "--bool", "core.ignorecase"],
                          capture_output=True, text=True).stdout.strip() != "true":
            self.skipTest("this filesystem does not fold case")
        self._commit("tests/test_w.py", TEST_X)
        self._rec("tests/test_w.py::TestA::test_x", False, 3000.0, "AC-3")
        self._rec("tests/test_w.py::TestA::test_x", True, 3100.0)
        self._rec("Tests/test_w.py::TestA::test_x", False, 3200.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual({"test:tests/test_w.py::TestA::test_x"},
                         {n for n in self._ids(tr) if "test_w" in n.lower()})
        self.assertEqual("red", _ac(tr, "AC-3")["status"])

    # -- J2: a RED under a bare / dotted spelling that could be the test blocks proven -----------
    def test_J2_a_later_bare_red_the_writer_could_not_qualify_blocks_the_proof(self):
        """`test_parse` is bound in two of the run's test files, so the writer keeps it bare; a
        RED under it AFTER the full id's green could be this test — the AC is not proven, and the
        test says why."""
        _write(self.d, "tests/test_more.py", "def test_parse():\n    assert 1\n")
        self._rec("test_parse", False, 3000.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:tests/test_core.py::test_parse")
        self.assertEqual("red", node["tdd"])
        self.assertIn("test_parse", node["tdd_note"])
        self.assertNotEqual("proven", _ac(tr, "AC-1")["status"])

    def test_J2_a_later_dotted_red_through_the_mcp_gate_blocks_the_proof(self):
        """The TDD gate (`record_outcome`) recording a dotted unittest id RED after the green: it
        could be this test, so the proof is blocked."""
        _write(self.d, "tests/test_more.py", "def test_parse():\n    assert 1\n")
        from mokata.tdd_state import state_dir as _sd
        from mokata.state import StateStore
        TR.record_outcome(StateStore(_sd(self.d)), "r1", ["tests.test_core.test_parse"],
                          passed=False, at=3000.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("red", _t(tr, "test:tests/test_core.py::test_parse")["tdd"])
        self.assertNotEqual("proven", _ac(tr, "AC-1")["status"])

    def test_J2_an_earlier_bare_red_or_another_tests_name_does_not_block(self):
        """The rule is about a LATER red that COULD be the test: a bare red before the green, or
        a red of a different name, leaves the proof standing."""
        _write(self.d, "tests/test_more.py", "def test_parse():\n    assert 1\n")
        self._rec("test_parse", False, 500.0)
        self._rec("test_unrelated", False, 3000.0)
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-1")["status"])

    # -- J3: the writers record full ids -------------------------------------------------------
    # -- K1: a bare name is qualified ONLY from the runner's own report ---------------------------
    GOOD_ZZZ = "def test_zzz():\n    assert 1\n"

    def test_K1a_a_never_run_sibling_twin_never_proves_a_bare_id(self):
        """q1 1a: the run touched tests/, `tests/test_sib.py::test_zzz` never ran, the agent ran
        `other/test_zzz.py` and recorded the BARE `test_zzz` red then green. Nothing the runner
        reported names a file — it stays bare, the AC is untested and SAYS why."""
        _write(self.d, "tests/test_sib.py", self.GOOD_ZZZ)
        self._commit("other/test_zzz.py", self.GOOD_ZZZ)
        self._rec("test_zzz", False, 3000.0, "AC-3")
        self._rec("test_zzz", True, 3100.0)
        rec = TR.read_test_record(self.d, "r1")
        self.assertEqual({}, rec["qualified"])
        self.assertEqual(["test_zzz"], rec["ac_tests"]["AC-3"])
        tr = P.trace(self.d, run="r1")
        ac = _ac(tr, "AC-3")
        self.assertEqual(("untested", []), (ac["status"], ac["proven_by"]))
        self.assertIn("bare id: run with full ids to prove", ac["note"])

    def test_K1b_a_failing_sibling_twin_never_proves_a_bare_id(self):
        """q1 1b: the sibling really fails (`assert 0`) — still never the bare id's proof."""
        _write(self.d, "tests/test_sib.py", "def test_zzz():\n    assert 0\n")
        self._commit("other/test_zzz.py", self.GOOD_ZZZ)
        self._rec("test_zzz", False, 3000.0, "AC-3")
        self._rec("test_zzz", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])
        self.assertNotIn("test:tests/test_sib.py::test_zzz", _ac(tr, "AC-3")["tests"])

    def test_K1_a_bare_id_the_runner_reported_once_is_its_full_id(self):
        """The runner's own output (`pytest -rA`) said it ran `other/test_zzz.py::test_zzz`: a
        bare `test_zzz` recorded with that report IS that test, and proves its AC."""
        _write(self.d, "tests/test_sib.py", self.GOOD_ZZZ)
        self._commit("other/test_zzz.py", self.GOOD_ZZZ)
        from mokata.test_ids import runner_ids
        for passed, at, out in ((False, 3000.0, "FAILED other/test_zzz.py::test_zzz - assert"),
                                (True, 3100.0, "PASSED other/test_zzz.py::test_zzz")):
            TR.record_test_run(self.d, "r1", passed=passed, command="pytest -rA",
                               tests=["test_zzz"], ac_map={"AC-3": ["test_zzz"]},
                               at=at, reported=runner_ids(out))
        tr = P.trace(self.d, run="r1")
        self.assertEqual(["test:other/test_zzz.py::test_zzz"], _ac(tr, "AC-3")["proven_by"])
        self.assertEqual(["test_zzz"], _t(tr, "test:other/test_zzz.py::test_zzz")["qualified_from"])

    def test_K1_a_name_the_runner_reported_twice_stays_bare(self):
        from mokata.test_ids import runner_ids
        rep = runner_ids("PASSED tests/test_sib.py::test_zzz\nPASSED other/test_zzz.py::test_zzz")
        self.assertEqual({}, TR.full_ids(self.d, "r1", ["test_zzz"], rep))

    def test_K1_a_dotted_unittest_id_is_qualified_by_the_module_it_names(self):
        """q1 1c: `other.test_zzz.test_zzz` NAMES `other/test_zzz.py` — qualified, proven."""
        _write(self.d, "tests/test_sib.py", self.GOOD_ZZZ)
        self._commit("other/test_zzz.py", self.GOOD_ZZZ)
        self._rec("other.test_zzz.test_zzz", False, 3000.0, "AC-3")
        self._rec("other.test_zzz.test_zzz", True, 3100.0)
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])

    def test_K1_the_runner_report_reader_takes_pytest_text_and_junit(self):
        from mokata.test_ids import runner_ids
        self.assertEqual(["tests/t.py::TestA::test_a", "tests/t.py::test_b[x]"],
                         runner_ids("FAILED tests/t.py::TestA::test_a - E\nPASSED tests/t.py::test_b[x]"))
        self.assertEqual(["tests/t.py::TestA::test_a"], runner_ids(
            '<testsuite><testcase file="tests/t.py" classname="tests.t.TestA" name="test_a"/>'
            "</testsuite>"))
        self.assertEqual([], runner_ids('<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>'))

    def test_J3_the_skill_and_the_command_template_tell_the_agent_to_record_full_ids(self):
        root = Path(__file__).resolve().parents[1] / "src" / "mokata"
        for rel in ("skills/develop/SKILL.md", "templates/commands/develop.md"):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("::", text.split("--test", 1)[1].splitlines()[0], rel)
        # L4: the phases that produce the RED (test) and a fix's GREEN (bug) record it too
        for rel in ("skills/test/SKILL.md", "templates/commands/test.md",
                    "skills/bug/SKILL.md", "templates/commands/bug.md"):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertIn("mokata progress test-run", text, rel)
            self.assertIn("--runner-report", text, rel)

    # -- J4: a proof does not outlive the test ----------------------------------------------------
    def _proven_then_edit(self, new_text):
        self._commit("tests/test_s.py", "def test_a():\n    assert 1\n")
        self._rec("tests/test_s.py::test_a", False, 3000.0, "AC-3")
        self._rec("tests/test_s.py::test_a", True, 3100.0)
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])
        self._commit("tests/test_s.py", new_text)
        return P.trace(self.d, run="r1")

    def test_J4_a_skip_added_after_green_ends_the_proof(self):
        tr = self._proven_then_edit("import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n"
                                    "    assert 1\n")
        node = _t(tr, "test:tests/test_s.py::test_a")
        self.assertNotEqual("proven", node["tdd"])
        self.assertIn("changed since it ran green", node["tdd_note"])
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])

    def test_J4_an_assert_0_added_after_green_ends_the_proof(self):
        tr = self._proven_then_edit("def test_a():\n    assert 0\n")
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])

    def test_J4_dunder_test_false_after_green_makes_the_test_uncollected(self):
        tr = self._proven_then_edit("__test__ = False\n\n\ndef test_a():\n    assert 1\n")
        node = _t(tr, "test:tests/test_s.py::test_a")
        self.assertEqual("unlocated", node["state"])
        self.assertIn("never collected", node["reason"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])

    def test_J4_shapes_a_runner_never_collects_are_not_present(self):
        """A non-Test class with no base, a class with `__init__`, `__test__ = False`, a fixture
        named test_*, a def under `if __name__ == "__main__"` — never collected, never present."""
        self._commit("tests/test_shapes.py", (
            "import pytest\n\n\nclass Helper:\n    def test_h(self):\n        pass\n\n\n"
            "class TestInit:\n    def __init__(self):\n        pass\n\n    def test_i(self):\n"
            "        pass\n\n\nclass TestNo:\n    __test__ = False\n\n    def test_n(self):\n"
            "        pass\n\n\n@pytest.fixture\ndef test_fx():\n    return 1\n\n\n"
            "if __name__ == '__main__':\n    def test_main():\n        pass\n"))
        for tid in ("tests/test_shapes.py::Helper::test_h", "tests/test_shapes.py::TestInit::test_i",
                    "tests/test_shapes.py::TestNo::test_n", "tests/test_shapes.py::test_fx",
                    "tests/test_shapes.py::test_main"):
            self._rec(tid, False, 3000.0, "AC-3")
            self._rec(tid, True, 3100.0)
        tr = P.trace(self.d, run="r1")
        for tid in ("Helper::test_h", "TestInit::test_i", "TestNo::test_n", "test_fx", "test_main"):
            node = _t(tr, "test:tests/test_shapes.py::" + tid)
            self.assertEqual("unlocated", node["state"], tid)
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])

    # -- J5: the pytest rootdir ------------------------------------------------------------------
    def test_J5_a_rootdir_relative_id_with_a_root_twin_is_ambiguous(self):
        """`sub/` is a pytest rootdir (`sub/pytest.ini`); `test_core2.py::test_a` was printed
        from there. A root-level `test_core2.py` exists too: which one ran is unknown — never the
        root file by default."""
        _write(self.d, "sub/pytest.ini", "[pytest]\n")
        _write(self.d, "sub/test_core2.py", "def test_a():\n    assert 1\n")
        self._commit("test_core2.py", "def test_a():\n    assert 1\n")
        self._rec("test_core2.py::test_a", False, 3000.0, "AC-3")
        self._rec("test_core2.py::test_a", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:test_core2.py::test_a")
        self.assertEqual("ambiguous", node["state"])
        self.assertIn("sub/test_core2.py", node["reason"])
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])

    def test_J5_a_monorepo_id_relative_to_its_rootdir_resolves_there(self):
        """Only `backend/tests/test_only.py` exists; pytest run in `backend/` printed
        `tests/test_only.py::test_a`. It resolves to the backend file and proves its AC."""
        _write(self.d, "backend/pyproject.toml", "[tool.pytest.ini_options]\n")
        self._commit("backend/tests/test_only.py", "def test_a():\n    assert 1\n")
        self._rec("tests/test_only.py::test_a", False, 3000.0, "AC-3")
        self._rec("tests/test_only.py::test_a", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        node = _t(tr, "test:backend/tests/test_only.py::test_a")
        self.assertEqual(("present", "proven"), (node["state"], node["tdd"]))
        self.assertEqual("proven", _ac(tr, "AC-3")["status"])

    # -- L1: a symlinked spelling of a test file is the SAME test ------------------------------
    def _symlinked(self, make_link, alias):
        """r1: tests/test_w.py committed with a symlink to it; red@3000 → green@3500 recorded
        under the ALIAS for AC-3, then the real latest run RED @4000 under the canonical id."""
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks here")
        _write(self.d, "tests/test_w.py", "def test_a():\n    assert 1\n")
        try:
            make_link()
        except OSError:
            self.skipTest("symlinks need privileges here")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "links")
        self._rec(alias, False, 3000.0, "AC-3")
        self._rec(alias, True, 3500.0)
        self._rec("tests/test_w.py::test_a", False, 4000.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual({"test:tests/test_w.py::test_a"},
                         {n for n in self._ids(tr) if n.endswith("::test_a") and "core" not in n},
                         "every symlink spelling is one node")
        self.assertEqual("red", _t(tr, "test:tests/test_w.py::test_a")["tdd"])
        self.assertEqual("red", _ac(tr, "AC-3")["status"])
        self.assertIn("AC-3", tr["risk"]["red_acs"])

    def test_L1_a_file_symlink_alias_is_the_same_test_and_its_latest_red_counts(self):
        self._symlinked(lambda: os.symlink("test_w.py", os.path.join(self.d, "tests", "alias.py")),
                        "tests/alias.py::test_a")

    def test_L1_a_dir_symlink_alias_is_the_same_test_and_its_latest_red_counts(self):
        self._symlinked(lambda: os.symlink("tests", os.path.join(self.d, "tlink")),
                        "tlink/test_w.py::test_a")

    def test_L1_a_symlink_leaving_the_repo_never_proves(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks here")
        outside = os.path.join(self.d + "-outside.py")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("def test_a():\n    assert 1\n")
        self.addCleanup(os.remove, outside)
        try:
            os.symlink(outside, os.path.join(self.d, "tests", "ext.py"))
        except OSError:
            self.skipTest("symlinks need privileges here")
        _git(self.d, "add", "-A")
        _git(self.d, "commit", "-qm", "ext")
        self._rec("tests/ext.py::test_a", False, 3000.0, "AC-3")
        self._rec("tests/ext.py::test_a", True, 3500.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("unparsed", _t(tr, "test:tests/ext.py::test_a")["state"],
                         "a link leaving the repo keeps its own spelling and reads unparsed")
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])
        # 0.0.28 E2: the record itself says where it ran, and the screen says why it cannot prove
        self.assertEqual("outside", TR.read_test_record(self.d, "r1")["tests"]["tests/ext.py::test_a"]["target"])
        self.assertIn("outside the repository", _t(tr, "test:tests/ext.py::test_a")["tdd_note"])

    # -- K2: the unconfigured rootdir twin -----------------------------------------------------
    def test_K2_a_rootdir_relative_id_with_a_root_twin_and_no_config_is_ambiguous(self):
        """q2: `test_core2.py` (root, passes) and `sub/test_core2.py` (`assert 0`), NO pytest
        config anywhere: `test_core2.py::test_a` may have been run from `sub/` — ambiguous."""
        _write(self.d, "sub/test_core2.py", "def test_a():\n    assert 0\n")
        self._commit("test_core2.py", "def test_a():\n    assert 1\n")
        self._rec("test_core2.py::test_a", False, 3000.0, "AC-3")
        self._rec("test_core2.py::test_a", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("ambiguous", _t(tr, "test:test_core2.py::test_a")["state"])
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])

    # -- K3: case folding is the FILESYSTEM's, not git's config --------------------------------
    def test_K3_a_case_alias_red_counts_even_when_git_says_ignorecase_false(self):
        """q4: `core.ignorecase=false` on a volume that folds case: `Tests/test_w.py` and
        `TESTS/TEST_W.PY` still name `tests/test_w.py` — one node, and the later RED counts."""
        if not (os.path.exists(os.path.join(self.d, ".git"))
                and os.path.exists(os.path.join(self.d, ".GIT"))):
            self.skipTest("this filesystem does not fold case")
        _git(self.d, "config", "core.ignorecase", "false")
        self._commit("tests/test_w.py", TEST_X)
        self._rec("tests/test_w.py::TestA::test_x", False, 3000.0, "AC-3")
        self._rec("tests/test_w.py::TestA::test_x", True, 3100.0)
        for i, alias in enumerate(("Tests/test_w.py", "tests/Test_W.py", "TESTS/TEST_W.PY")):
            self._rec(f"{alias}::TestA::test_x", False, 4000.0 + 100 * i)
            tr = P.trace(self.d, run="r1")
            self.assertEqual({"test:tests/test_w.py::TestA::test_x"},
                             {n for n in self._ids(tr) if "test_w" in n.lower()}, alias)
            self.assertEqual("red", _ac(tr, "AC-3")["status"], alias)
            self._rec(f"{alias}::TestA::test_x", True, 4050.0 + 100 * i)

    # -- K4: the stamp covers the code the test runs with, not its formatting ------------------
    def _proven_s(self, text):
        self._commit("tests/test_s.py", text)
        self._rec("tests/test_s.py::test_a", False, 3000.0, "AC-3")
        self._rec("tests/test_s.py::test_a", True, 3100.0)
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])

    def test_K4_a_changed_helper_in_the_test_file_ends_the_proof(self):
        self._proven_s("def helper():\n    return 1\n\n\ndef test_a():\n    assert helper()\n")
        self._commit("tests/test_s.py", "def helper():\n    return 0\n\n\ndef test_a():\n"
                                        "    assert helper()\n")
        self.assertNotEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])

    def test_K4_an_autouse_fixture_added_in_conftest_ends_the_proof(self):
        self._proven_s("def test_a():\n    assert 1\n")
        self._commit("tests/conftest.py", "import pytest\n\n\n@pytest.fixture(autouse=True)\n"
                                          "def boom():\n    raise RuntimeError\n")
        self.assertNotEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])

    def test_K4_formatting_comments_and_a_sibling_test_leave_the_proof_standing(self):
        self._proven_s("def test_a():\n    assert 1\n")
        self._commit("tests/test_s.py", "# a comment\n\n\n\ndef test_a():\n    # why\n"
                                        "    assert   1\n\n\ndef test_other():\n    assert 0\n")
        self.assertEqual("proven", _ac(P.trace(self.d, run="r1"), "AC-3")["status"])

    # -- K5: the job's counter is truthful -------------------------------------------------------
    def test_K5_the_trace_never_reports_more_done_than_total(self):
        """The cold index's 50,000 files are words in the note — `done` and `total` stay the
        change set's files, one unit."""
        from mokata.knowledge.cancel import reporting
        for i in range(1100):                  # enough source files for the index to report
            _write(self.d, f"src/gen/m{i}.py", f"def f{i}():\n    return {i}\n")
        nav.close_layers()
        seen, state = [], {"done": None, "total": None, "unit": None}

        def sink(done=None, total=None, unit=None, note=None, **_kw):
            for k, v in (("done", done), ("total", total), ("unit", unit)):
                if v is not None:
                    state[k] = v
            seen.append(dict(state))
        with reporting(sink):
            P.trace(self.d, run="r1")
        counted = [x for x in seen if x["done"] is not None and x["total"] is not None]
        self.assertTrue(counted)
        for x in counted:
            self.assertLessEqual(x["done"], x["total"], x)
            self.assertEqual("files", x["unit"], x)

    # -- J7 / J8: the "exactly once" and "every spelling" clauses, pinned -------------------------
    def test_M4_a_qualified_key_bound_twice_proves_nothing(self):
        """`class TestA` defined twice, each with `test_x`: `path::TestA::test_x` names two
        definitions — ambiguous, never proven."""
        self._commit("tests/test_dup.py", TEST_X + "\n\n" + TEST_X.split("\n\n\n", 1)[1])
        self._rec("tests/test_dup.py::TestA::test_x", False, 3000.0, "AC-3")
        self._rec("tests/test_dup.py::TestA::test_x", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("ambiguous", _t(tr, "test:tests/test_dup.py::TestA::test_x")["state"])
        self.assertNotEqual("proven", _ac(tr, "AC-3")["status"])

    def test_M8_one_proven_spelling_and_one_green_only_spelling_is_not_proven(self):
        """Every spelling of a full id needs its OWN red→green: `tests\\test_s.py::test_a` only
        ever went green, so the test is not proven though `tests/test_s.py::test_a` is."""
        self._commit("tests/test_s.py", "def test_a():\n    assert 1\n")
        self._rec("tests/test_s.py::test_a", False, 3000.0, "AC-3")
        self._rec("tests/test_s.py::test_a", True, 3100.0)
        self._rec("tests\\test_s.py::test_a", True, 3050.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("green-only", _t(tr, "test:tests/test_s.py::test_a")["tdd"])
        self.assertEqual("green-not-tdd", _ac(tr, "AC-3")["status"])

    def test_J8_a_dotted_class_qualifier_naming_no_class_is_unlocated(self):
        """`path::TestX.test_a` with no TestX is never the module-level `test_a`."""
        self._commit("tests/test_s.py", "def test_a():\n    assert 1\n")
        self._rec("tests/test_s.py::TestX.test_a", False, 3000.0, "AC-3")
        self._rec("tests/test_s.py::TestX.test_a", True, 3100.0)
        tr = P.trace(self.d, run="r1")
        self.assertEqual("unlocated", _t(tr, "test:tests/test_s.py::TestX::test_a")["state"])
        self.assertEqual("untested", _ac(tr, "AC-3")["status"])


class NothingNonProvingEverReachesProven(unittest.TestCase):
    """PROPERTY (H4): over randomly generated trees — same-named twins across files, CLASS twins
    (two classes, one method name; a module function and a method of one name), a class named
    twice, a rebinding, gitignored and untracked copies, committed and uncommitted renames,
    removed definitions, parametrised / backslash / `./` / dotted / bare / class-qualified ids
    (some naming a class that does not exist), planned ids that never ran — every AC the trace
    calls `proven` rests only on tests that are `present`, in a file git tracks (or, live, an
    untracked file git does not ignore), whose AST — counted by THIS oracle, not proof.py — binds
    the test's qualified key exactly once (a qualifier-less id: the name once in the whole file,
    at module scope), recorded only under full ids, and EVERY recorded id and every recorded
    spelling of that full id went red, then green, and is green now."""

    SEEDS = tuple(range(1, 25))
    #: seeds whose tree is JavaScript: with no structural outline a JS test never proves
    JS_SEEDS = (55, 89)

    def _tree(self, d, rng, lang="py"):
        """One random repository: base commit, then a change; returns the record it writes."""
        if lang == "js":
            return self._js_tree(d, rng)
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        _write(d, "src/app/__init__.py", "")
        _write(d, "src/app/core.py", CORE_OLD)
        names = ["test_parse", "test_total", "test_extra"]
        classes = ["TestA", "TestB"]
        pyfiles = ["tests/test_a.py", "tests/test_b.py", "tests/test_ign.py", "tests/test_unt.py"]
        self.doubled = []                       # `file::Class::name` bound twice (M4)
        for f in pyfiles:                       # module defs, class twins, redefinitions, at random
            body = "from app.core import parse, total\n"
            for n in names:
                if rng.random() < 0.4:
                    body += f"\n\ndef {n}():\n    assert parse('a')\n"
            for c in classes:
                if rng.random() < 0.6:
                    meths = [n for n in names if rng.random() < 0.5] or [rng.choice(names)]
                    twice = rng.random() < 0.35                         # a class named twice
                    for _rep in range(2 if twice else 1):               # (copy-pasted: same body)
                        body += f"\n\nclass {c}:\n" + "".join(
                            f"    def {n}(self):\n        assert total('a')\n" for n in meths)
                    if twice and f != "tests/test_ign.py":
                        self.doubled += [f"{f}::{c}::{n}" for n in meths]
            if rng.random() < 0.15:
                body += f"\n\n{rng.choice(names)} = None\n"                # a rebinding
            _write(d, f, body)
        _write(d, "tests/test_c.rs", "#[test]\nfn test_extra() {}\n")
        _write(d, ".gitignore", "tests/test_ign.py\n")
        unt = open(os.path.join(d, "tests", "test_unt.py"), encoding="utf-8").read()
        os.remove(os.path.join(d, "tests", "test_unt.py"))
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "base")
        _write(d, "tests/test_unt.py", unt)                                   # untracked copy
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "r1").ensure_registered()
        acs = ["AC-1", "AC-2", "AC-3", "AC-4"]
        surface.state.write("emitted_spec__r1", sealed({"title": "t", "approach": "a", "domains": [],
                                                         "criteria": [{"id": a, "text": a} for a in acs]}))
        RI.bind_registered(d, "S1", "r1")
        path = os.path.join(d, "src", "app", "core.py")
        S.capture_before_first_write(d, path, "S1", "r1")
        _write(d, "src/app/core.py", CORE_NEW)
        S.capture(d, path, S.AFTER, "S1", "r1")
        if rng.random() < 0.3:                                    # a committed rename
            _git(d, "mv", "tests/test_b.py", "tests/test_b2.py")
            _git(d, "commit", "-qm", "mv")
        elif rng.random() < 0.3:                                  # an uncommitted rename
            _git(d, "mv", "tests/test_b.py", "tests/test_b2.py")
        if rng.random() < 0.3:                                    # definitions removed
            _write(d, "tests/test_a.py", "from app.core import parse\n")
        # the record: random ids in random spellings, red/green in random order
        spellings, qualified = [], []
        for n in names:
            spellings += [n, f"{n}[x]", f"test_core.TestA.{n}", f"tests.test_a.TestA.{n}",
                          f"tests/test_c.rs::{n}"]
            for f in pyfiles + ["tests/test_b2.py"]:
                spellings += [f"{f}::{n}", f"{f}::{n}[y]", f"./{f}::{n}", f.replace("/", "\\") + f"::{n}"]
                for c in classes + ["TestZ"]:
                    spellings += [f"{f}::{c}::{n}", f"{f}::{c}::{n}[p]"]
                    qualified.append(f"{f}::{c}::{n}")

        def alias(tid):
            """Another spelling of the SAME full id: `./`, `\\`, `//`, `..`, `.`, a case variant."""
            head, _sep, rest = tid.partition("::")
            d_, _s, b_ = head.rpartition("/")
            return rng.choice([f"./{head}", head.replace("/", "\\"), f"{d_}//{b_}",
                               f"{d_}/../{d_}/{b_}", f"{d_}/./{b_}", f"{d_.upper()}/{b_}"]) + "::" + rest

        t = 1000.0
        for ac in acs:
            for _ in range(rng.randint(1, 3)):
                # a key the file binds twice (M4), a class-qualified id, or any spelling
                r = rng.random()
                tid = rng.choice(self.doubled if self.doubled and r < 0.3 else
                                 qualified if r < 0.65 else spellings)
                for outcome in rng.choice([(False, True), (True,), (False,), (True, False),
                                           (False, True, False, True), (False, True, True)]):
                    TR.record_test_run(d, "r1", passed=outcome, command="x", tests=[tid],
                                       ac_map={ac: [tid]}, at=t)
                    t += 10
                if "::" in tid and rng.random() < 0.5:           # an ALIAS of it, its own story (M8, J1)
                    other = alias(tid)
                    for outcome in rng.choice([(True,), (False,), (False, True), (True, False)]):
                        TR.record_test_run(d, "r1", passed=outcome, command="x", tests=[other], at=t)
                        t += 10
            if rng.random() < 0.3:                                # a twin spelling, unmapped
                TR.record_test_run(d, "r1", passed=rng.random() < 0.5, command="x",
                                   tests=[rng.choice(spellings)], at=t)
                t += 10
        if rng.random() < 0.4:                    # a LATE red under a bare / dotted spelling (J2)
            TR.record_test_run(d, "r1", passed=False, command="x",
                               tests=[rng.choice([n for n in spellings if "/" not in n])], at=t + 1000)
        self.tainted = set()
        if rng.random() < 0.3:                    # a test file edited AFTER its green (J4)
            f = rng.choice([x for x in pyfiles + ["tests/test_b2.py"]
                            if os.path.exists(os.path.join(d, *x.split("/")))])
            p_ = os.path.join(d, *f.split("/"))
            with open(p_, encoding="utf-8") as fh:
                body = fh.read()
            _write(d, f, body.replace("    assert", "    assert 0 and"))
            self.tainted.add(f)
        from types import SimpleNamespace
        TR.seed_ac_map(d, "r1", [SimpleNamespace(name=rng.choice(spellings), ac_ids=[rng.choice(acs)])
                                 for _ in range(3)])               # planned, never run
        return names

    def _js_tree(self, d, rng):
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
        _write(d, "src/add.js", "function add(a, b) { return a + b; }\nmodule.exports = { add };\n")
        names = ["test_add", "test_sub"]
        files = ["tests/a.test.js", "tests/b.test.js", "tests/ign.test.js"]
        for f in files:
            defs = [n for n in names if rng.random() < 0.6]
            _write(d, f, "const { add } = require('../src/add');\n" + "".join(
                f"function {n}() {{ return add(1, 1) === 2; }}\n" for n in defs))
        _write(d, ".gitignore", "tests/ign.test.js\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "base")
        surface = Surface.load(d)
        PipelineCheckpoint(surface.state, "r1").ensure_registered()
        acs = ["AC-1", "AC-2"]
        surface.state.write("emitted_spec__r1", sealed({"title": "t", "approach": "a", "domains": [],
                                                         "criteria": [{"id": a, "text": a} for a in acs]}))
        RI.bind_registered(d, "S1", "r1")
        path = os.path.join(d, "src", "add.js")
        S.capture_before_first_write(d, path, "S1", "r1")
        _write(d, "src/add.js", "function add(a, b) { return a + b + 0; }\nmodule.exports = { add };\n")
        S.capture(d, path, S.AFTER, "S1", "r1")
        spellings = [n for n in names] + [f"{f}::{n}" for f in files for n in names]
        t = 1000.0
        for ac in acs:
            for _ in range(rng.randint(1, 3)):
                tid = rng.choice(spellings)
                for outcome in rng.choice([(False, True), (True,), (False,), (False, True, False, True)]):
                    TR.record_test_run(d, "r1", passed=outcome, command="x", tests=[tid],
                                       ac_map={ac: [tid]}, at=t)
                    t += 10
        return names

    def _git_ok(self, d, *a):
        return subprocess.run(["git", "-C", d, *a], capture_output=True,
                              stdin=subprocess.DEVNULL).returncode == 0

    def _check(self, d, tr, where):
        tests = {t["id"]: t for t in tr["nodes"]["tests"]}
        history = TR.read_test_record(d, "r1")["tests"]
        fold = subprocess.run(["git", "-C", d, "config", "--bool", "core.ignorecase"],
                              capture_output=True, text=True).stdout.strip() == "true"
        tainted = getattr(self, "tainted", set())
        for a in tr["nodes"]["acs"]:
            if a["status"] != "proven":
                self.assertEqual([], a["proven_by"], where)
                continue
            self.assertTrue(a["proven_by"], f"{where}: proven with no proving test named")
            for nid in a["proven_by"]:
                t = tests[nid]
                w = f"{where}: {nid}"
                self.assertEqual(("present", "proven"), (t["state"], t["tdd"]), w)
                self.assertTrue(t["located"] and t["path"], w)
                # git: tracked — or, judged live, untracked and NOT ignored
                tracked = self._git_ok(d, "ls-files", "--error-unmatch", "--", t["path"])
                ignored = self._git_ok(d, "check-ignore", "-q", "--", t["path"])
                self.assertFalse(ignored, f"{w}: an ignored file proved")
                self.assertTrue(tracked or tr["target"]["kind"] in ("run", "live"), w)
                with open(os.path.join(d, *t["path"].split("/")), encoding="utf-8") as fh:
                    text = fh.read()
                label = t["id"][len("test:"):]
                self.assertEqual(label, _oracle_label(label), w)
                rest = label.split("::")[1:]
                key = ".".join(rest)
                if t["path"].endswith(".py"):
                    b = _oracle_bindings(text)
                    self.assertEqual(1, b.get(key, 0), f"{w}: {key} bound {b.get(key, 0)} times")
                    if len(rest) == 1:
                        same = sum(v for k, v in b.items() if k.split(".")[-1] == rest[0])
                        self.assertEqual(1, same, f"{w}: qualifier-less id, name bound {same}x")
                    self.assertNotIn("import *", text, w)
                else:
                    import re as _re
                    self.assertEqual(1, len(rest), w)
                    self.assertEqual(1, len(_re.findall(rf"\b{_re.escape(rest[0])}\b", text)), w)
                # every id — recorded for it, or any recorded spelling of its full id — has its
                # OWN red, then green, and is green now; never a bare id
                self.assertTrue(t["recorded_ids"], w)
                self.assertNotIn(t["path"], tainted, f"{w}: its test was edited after its green")
                spelled = {h for h in history if _oracle_same(_oracle_label(h), label, fold)}
                for pid in set(t["recorded_ids"]) | spelled:
                    self.assertTrue(_oracle_same(label, _oracle_label(pid), fold),
                                    f"{w}: {pid} is not its full id")
                    h = history.get(pid)
                    self.assertIsNotNone(h, f"{w}: {pid} has no history of its own")
                    self.assertEqual(("red", "green"), (h["first"], h["last"]), f"{w}: {pid} {h}")
                    self.assertTrue(h["red_at"] is not None and h["green_at"] is not None
                                    and h["red_at"] < h["green_at"], f"{w}: {pid} {h}")
                # no LATER red under a spelling that names no file but could be this test (J2)
                green_at = max(history[p]["last_at"] for p in set(t["recorded_ids"]) | spelled)
                for hid, h in history.items():
                    if not _oracle_label(hid) and _oracle_could_be(hid, t["path"], key) \
                            and h["last"] == "red":
                        self.assertLess(h["last_at"], green_at, f"{w}: a later red as {hid}")

    def test_property_random_trees(self):
        import random
        proven = 0
        for seed in self.SEEDS + self.JS_SEEDS:
            rng = random.Random(seed)
            with tempfile.TemporaryDirectory() as d:
                d = os.path.realpath(d)
                self._tree(d, rng, lang="js" if seed in self.JS_SEEDS else "py")
                try:
                    tr = P.trace(d, run="r1")
                    self._check(d, tr, f"seed {seed}")
                    proven += sum(1 for a in tr["nodes"]["acs"] if a["status"] == "proven")
                    live = P.trace(d, live=True)
                    self._check(d, live, f"seed {seed} live")
                finally:
                    nav.close_layers()
        self.assertGreater(proven, 0, "the generator never produced a provable AC — it tests nothing")

    def test_property_fixed_constructions(self):
        """The verifier's fixed cases (F1, F2, F7, G1, G2, R1, R1b, R2b) through the same check."""
        def r1(d):
            _write(d, "tests/test_cls.py", CLS_TWINS)
            _git(d, "add", "-A")
            _git(d, "commit", "-qm", "cls")
            for tid, ac, at in (("tests/test_cls.py::TestA::test_x", "AC-1", 3000.0),
                                ("tests/test_cls.py::TestZ::test_x", "AC-2", 4000.0),
                                ("tests/test_cls.py::test_x", "AC-3", 5000.0)):
                TR.record_test_run(d, "r1", passed=False, command="x", tests=[tid],
                                   ac_map={ac: [tid]}, at=at)
                TR.record_test_run(d, "r1", passed=True, command="x", tests=[tid], at=at + 1)
            from types import SimpleNamespace
            TR.seed_ac_map(d, "r1", [SimpleNamespace(name="tests/test_cls.py::TestB::test_x",
                                                     ac_ids=["AC-3"])])

        def r2b(d):
            _write(d, ".gitignore", "tests/test_core_copy.py\n")
            _write(d, "tests/test_core_copy.py", TEST_NEW)
            TR.record_test_run(d, "r1", passed=False, command="x", tests=["test_parse"],
                               ac_map={"AC-3": ["test_parse"]}, at=3000.0)
            TR.record_test_run(d, "r1", passed=True, command="x", tests=["test_parse"], at=3001.0)

        cases = {
            "F1": lambda d: TR.record_test_run(d, "r1", passed=False, command="x",
                                               tests=["tests/test_core.py::test_parse"],
                                               ac_map={"AC-1": ["tests/test_core.py::test_parse"]}, at=3000.0),
            "F2": lambda d: _write(d, "tests/test_more.py", "def test_parse():\n    assert 1\n"),
            "F7": lambda d: os.remove(os.path.join(d, "tests", "test_core.py")),
            "G1": lambda d: (_write(d, "tests/test_other.py", "def test_parse():\n    assert 1\n"),
                             _git(d, "add", "-A"), _git(d, "commit", "-qm", "twin")),
            "G2": lambda d: (_write(d, "tests/test_lib.rs", "// gone\n"),
                             TR.record_test_run(d, "r1", passed=False, command="x",
                                                tests=["tests/test_lib.rs::test_add"],
                                                ac_map={"AC-3": ["tests/test_lib.rs::test_add"]}, at=3000.0),
                             TR.record_test_run(d, "r1", passed=True, command="x",
                                                tests=["tests/test_lib.rs::test_add"], at=3500.0)),
            "R1": r1,
            "R2b": r2b,
        }
        for name, setup in cases.items():
            with tempfile.TemporaryDirectory() as d:
                d = os.path.realpath(d)
                fixture(d)
                setup(d)
                try:
                    self._check(d, P.trace(d, run="r1"), name)
                finally:
                    nav.close_layers()


# ===================================================================== the record keeps red-then-green times

class TheTestRecordKeepsRedThenGreenTimes(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=_silent)

    def tearDown(self):
        self._t.cleanup()

    def test_first_red_first_green_after_it_and_a_regression_starts_the_proof_over(self):
        TR.record_test_run(self.d, "r", passed=False, tests=["t"], at=1.0)
        TR.record_test_run(self.d, "r", passed=False, tests=["t"], at=2.0)
        TR.record_test_run(self.d, "r", passed=True, tests=["t"], at=3.0)
        TR.record_test_run(self.d, "r", passed=True, tests=["t"], at=4.0)
        h = TR.read_test_record(self.d, "r")["tests"]["t"]
        self.assertEqual({"red_at": 1.0, "green_at": 3.0, "last": "green", "last_at": 4.0,
                          "first": "red", "stamp": None}, h, "a bare `t` names no file: no stamp")
        self.assertEqual("proven", P.tdd_verdict("t", set(), set(), {"t": h})["tdd"])
        TR.record_test_run(self.d, "r", passed=False, tests=["t"], at=5.0)
        h = TR.read_test_record(self.d, "r")["tests"]["t"]
        self.assertEqual({"red_at": 5.0, "green_at": None, "last": "red", "last_at": 5.0,
                          "first": "red", "stamp": None}, h)
        self.assertEqual("red", P.tdd_verdict("t", set(), set(), {"t": h})["tdd"])

    def test_a_run_whose_tests_ended_differently_records_each_by_its_own_outcome(self):
        """An IDE re-run of several tests (`results`) folds EACH test's own outcome into its timed
        history — one that failed is red, never green because the run's verdict said so."""
        TR.record_test_run(self.d, "r1", passed=False, command="x",
                           results={"tests/t.py::test_a": True, "tests/t.py::test_b": False},
                           source="ide", at=10.0)
        h = TR.read_test_record(self.d, "r1")["tests"]
        self.assertEqual(("green", "red"), (h["tests/t.py::test_a"]["last"],
                                            h["tests/t.py::test_b"]["last"]))
        self.assertEqual(10.0, h["tests/t.py::test_b"]["red_at"])

    def test_the_MCP_gate_writes_the_same_timed_history_and_order_is_the_proof(self):
        """Coordinator ruling 2: `TddGuard.record_red/record_green` write the per-test times; with
        timestamps, proven requires red_at < green_at — a green recorded BEFORE the red is not a
        proof, whatever the sets say."""
        from mokata.config import Surface
        from mokata.govern.tdd import TddGuard
        guard = TddGuard(store=Surface.load(self.d).state, run_id="g1")
        guard.record_red("t_gate")
        guard.record_green("t_gate")
        h = TR.read_test_record(self.d, "g1")["tests"]["t_gate"]
        self.assertIsNotNone(h["red_at"])
        self.assertLess(h["red_at"], h["green_at"])
        self.assertEqual(("proven", "timed"),
                         tuple(P.tdd_verdict("t_gate", {"t_gate"}, {"t_gate"}, {"t_gate": h})[k]
                               for k in ("tdd", "basis")))
        # green first, then red: the sets hold both, the order says "not proven"
        guard.record_green("t_back")
        guard.record_red("t_back")
        h2 = TR.read_test_record(self.d, "g1")["tests"]["t_back"]
        self.assertEqual("red", P.tdd_verdict("t_back", {"t_back"}, {"t_back"}, {"t_back": h2})["tdd"])
        # a (hand-made) history whose green precedes its red is not a proof either
        v = P.tdd_verdict("x", set(), set(), {"x": {"red_at": 5.0, "green_at": 2.0, "last": "green",
                                                     "last_at": 2.0}})
        self.assertEqual("green-only", v["tdd"])

    def test_a_record_from_before_timed_history_is_judged_by_the_sets_and_labelled_so(self):
        """The user's own `.mokata/` data from before this change has no times: it is kept and
        judged by membership, and the answer carries `basis: "sets"` so the UI can say so."""
        v = P.tdd_verdict("old", {"old"}, {"old"}, {})
        self.assertEqual(("proven", "sets"), (v["tdd"], v["basis"]))
        self.assertIsNone(v["red_at"])
        js = (STATIC / "features" / "proof.js").read_text(encoding="utf-8")
        self.assertIn("recorded before timed history", js)

    def test_green_with_no_red_on_record_is_green_only_and_the_sets_alone_still_judge(self):
        TR.record_test_run(self.d, "r", passed=True, tests=["g"], at=1.0)
        h = TR.read_test_record(self.d, "r")["tests"]
        self.assertEqual("green-only", P.tdd_verdict("g", set(), set(), h)["tdd"])
        self.assertEqual("proven", P.tdd_verdict("x", {"x"}, {"x"}, {})["tdd"])
        self.assertEqual("sets", P.tdd_verdict("x", {"x"}, {"x"}, {})["basis"])
        self.assertEqual("unrecorded", P.tdd_verdict("y", set(), set(), {})["tdd"])


# ===================================================================== the routes

class TheRoutesAnswerTheTraceAsAJob(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        fixture(cls.d)

    @classmethod
    def tearDownClass(cls):
        nav.close_layers()
        cls._t.cleanup()

    def test_the_trace_route_answers_the_contract_shape_for_a_run(self):
        from mokata.ide import contract as C
        c, _a, _s = client(self.d)
        r = c.get("/api/proof?run=r1&sync_ms=20000")
        self.assertEqual(200, r.status_code, r.text[:300])
        con = C.load()
        entry = C.route_index(con)[("/api/proof", "GET")]
        self.assertEqual([], C.validate(r.json(), entry["responses"]["200"]["schema"],
                                        con["schemas"]))
        self.assertEqual(P.TRACE_VERSION, r.json()["version"])
        self.assertEqual(["AC-3"], r.json()["risk"]["untested_acs"])

    def test_long_work_is_a_visible_job_with_the_proof_kind(self):
        c, _a, _s = client(self.d)
        r = c.get("/api/proof?run=r1&sync_ms=0")
        self.assertEqual(202, r.status_code)
        self.assertEqual("proof.trace", r.json()["kind"])
        self.assertIn("proof · run r1", r.json()["label"])

    def test_targets_lists_the_run_with_its_AC_count_and_a_default(self):
        c, _a, _s = client(self.d)
        t = c.get("/api/proof/targets").json()
        self.assertEqual([("r1", 3)], [(x["run"], x["acs"]) for x in t["runs"]])
        self.assertEqual({"run": "r1"}, t["default"])
        self.assertTrue(t["git"])

    def test_the_sample_answers_labelled_and_carries_every_flag_kind(self):
        c, _a, _s = client(tempfile.gettempdir(), demo=True)
        j = c.get("/api/proof?run=run-parser-fix").json()
        self.assertTrue(j["sample"])
        self.assertEqual(["AC-3"], j["risk"]["untested_acs"])
        self.assertTrue(j["risk"]["tests_not_tdd_proven"])
        self.assertTrue(j["risk"]["code_without_test"])
        self.assertTrue(j["risk"]["gates_red"] and j["risk"]["gates_skipped"])


# ===================================================================== the view file

class TheViewIsOneFeatureFile(unittest.TestCase):

    def test_the_feature_script_and_style_exist_and_use_no_markup_injection(self):
        js = (STATIC / "features" / "proof.js").read_text(encoding="utf-8")
        self.assertTrue((STATIC / "features" / "proof.css").exists())
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("document.write", js)
        self.assertNotIn("cdn.", js)
        self.assertIn('id: "proof"', js)
        for word in ("untested", "not TDD-proven", "show all", "risk"):
            self.assertIn(word, js)

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH — the layout runs in a browser")
    def test_the_layout_places_every_drawn_node_in_three_columns_and_lists_the_rest(self):
        """The pure layout (`MK.proof.layout`) is testable without a DOM: ACs left, tests middle,
        code right; a node the server did not put in `draw.shown` is not drawn but is counted."""
        from mokata.ide.features.proof import sample_trace
        src = (STATIC / "features" / "proof.js").read_text(encoding="utf-8")
        js = ("globalThis.window = globalThis; window.MK = {}; window.mokataIDE = {register(){}};\n"
              + src + "\nconst t = " + json.dumps(sample_trace()) + ";\n"
              "t.draw.shown = t.draw.shown.filter((id) => id !== 'code:src/parser.py#report_error');\n"
              "const L = MK.proof.layout(t, 900, 400);\n"
              "console.log(JSON.stringify({cols: L.nodes.map((n) => n.col), n: L.nodes.length,"
              " hidden: L.hidden, edges: L.edges.length}));\n")
        p = subprocess.run(_support.node_argv(js), capture_output=True, text=True, timeout=60)
        self.assertEqual(0, p.returncode, p.stderr)
        o = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(6, o["n"])
        self.assertEqual(1, o["hidden"])
        self.assertEqual({0, 1, 2}, set(o["cols"]))
        self.assertEqual(5, o["edges"])


if __name__ == "__main__":
    unittest.main()
