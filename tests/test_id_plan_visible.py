"""0.0.25 — "the local plan and spec are not visible under Plan" (user report, 0.0.23).

Measured on the reporter's own repo: 2,340 runs with a TDD phase, 2,375 with an approved approach,
0 with an emitted spec. Three separate reasons the Plan view showed nothing useful, each asserted:

  * BURIED RUN — the run chooser was ordered by recency alone and cut at `MAX_RUNS`, and the
    default was the newest run. A run with a spec, older than 200 runs that only ever had an
    approach, was not listed at all, and the default was an empty run. Now: runs with a spec
    (emitted, or drafted) first, then runs with a plan, then the rest; the default is the
    resolver's run only when it has something to show; and the answer carries the counts so the
    page can say "showing 200 of N".
  * DRAFTED SPEC — `spec_emit` only PROPOSES; nothing is saved until a human runs
    `mokata approve <id>`. The run then has no spec, and the view said "no spec" while the user
    could see the draft. Now the live proposal is shown with the exact approve command.
  * SAVED PLAN FILES — the brainstorm plan file (`.mokata/temp_local/plans/<slug>.md`) and its
    exported copy (`plans/<slug>.md`) were never read by the IDE. Now they are listed and readable,
    read-only, by name only (no path), never through a symlink, bounded in size.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import re
import shutil
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")

SPEC_RUN = "a" * 32
DRAFT_RUN = "c" * 32


def _silent(*_a, **_k):
    pass


def _store(root, rid):
    from mokata.session_state import scoped_store
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    return scoped_store(StateStore(state_dir(root)), rid)


def _approach(topic):
    return {"topic": topic, "approach": {"name": "flat-rate", "summary": "a flat fee",
                                         "pros": [], "cons": [], "targets": [], "decisions": []},
            "answered_questions": [], "approver": "jas",
            "approved_at": "2026-10-19T09:00:00+00:00", "domains": []}


def _repo(root, *, empty_runs=0, spec=True, draft=False):
    """A repo whose run with a SPEC is the OLDEST, behind `empty_runs` newer approach-only runs —
    the reporter's shape."""
    from mokata.engine.emit import spec_commit
    from mokata.engine.spec import AcceptanceCriterion, Spec
    init_repo(root=root, profile="standard", assume_yes=True, out=_silent)
    st = _store(root, SPEC_RUN)
    st.write("approved_approach", _approach("add shipping to checkout"))
    if spec:
        commit_planned(st, Spec("shipping fee", [AcceptanceCriterion("AC1", "50+ ships free")]),
                    version=1, run_id=SPEC_RUN)
    old = time.time() - 3600
    from mokata.tdd_state import state_dir
    for name in os.listdir(state_dir(root)):
        if SPEC_RUN in name:
            os.utime(os.path.join(state_dir(root), name), (old, old))
    for i in range(empty_runs):
        _store(root, f"b{i:031d}").write("approved_approach", _approach(f"self-test {i}"))
    if draft:
        _store(root, DRAFT_RUN).write("approved_approach", _approach("refunds"))
        from mokata import approval
        approval.propose(root, tool="spec_emit", args={"title": "refunds"}, run_id=DRAFT_RUN,
                         summary="spec 'refunds' — 1 acceptance criterion", preview="AC1 refund")


class _Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-planvis-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self._env = os.environ.pop("MOKATA_SESSION_ID", None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._env is not None:
            os.environ["MOKATA_SESSION_ID"] = self._env


class TheRunWithTheSpecIsNeverBuried(_Repo):

    def test_an_old_spec_run_behind_more_than_max_runs_is_listed_first_and_is_the_default(self):
        from mokata.ide import plans as P
        _repo(self.root, empty_runs=P.MAX_RUNS + 20)
        out = P.local_runs(self.root)
        ids = [r["id"] for r in out["runs"]]
        self.assertIn(SPEC_RUN, ids, "the run with the spec fell off the list")
        self.assertEqual(ids[0], SPEC_RUN, "the run with the spec is not listed first")
        self.assertEqual(out["default"], SPEC_RUN, "the default is a run with nothing to show")
        self.assertEqual(out["runs"][0]["spec"], "ok")

    def test_the_answer_says_how_many_runs_exist_and_how_many_are_shown(self):
        from mokata.ide import plans as P
        _repo(self.root, empty_runs=P.MAX_RUNS + 5)
        c = P.local_runs(self.root)["counts"]
        self.assertEqual(c["total"], P.MAX_RUNS + 6)
        self.assertEqual(c["listed"], P.MAX_RUNS)
        self.assertEqual(c["with_spec"], 1)
        self.assertEqual(c["with_plan"], P.MAX_RUNS + 6)

    def test_a_run_with_only_a_tdd_phase_never_outranks_a_run_with_a_plan(self):
        from mokata.ide import plans as P
        from mokata import tdd_state
        _repo(self.root, spec=False)
        tdd_state.record(_store(self.root, "d" * 32), "d" * 32, red=["AC1"], green=[])
        out = P.local_runs(self.root)
        self.assertEqual([r["id"] for r in out["runs"]], [SPEC_RUN, "d" * 32])
        self.assertEqual(out["default"], SPEC_RUN)
        self.assertEqual(out["runs"][1]["approach"], "absent")


class ADraftedSpecIsShownWithItsApproveCommand(_Repo):

    def test_the_run_row_and_the_plan_name_the_draft(self):
        from mokata.ide import plans as P
        _repo(self.root, draft=True)
        rows = {r["id"]: r for r in P.local_runs(self.root)["runs"]}
        self.assertEqual(rows[DRAFT_RUN]["spec"], "awaiting approval")
        view = P.plan(self.root, DRAFT_RUN)
        self.assertEqual(view["spec"]["state"], "absent", "a draft is NOT the run's spec")
        [d] = view["pending_spec"]
        self.assertEqual(d["tool"], "spec_emit")
        self.assertEqual(d["approve"], f"mokata approve {d['proposal_id']}")
        self.assertIn("AC1 refund", d["preview"])

    def test_an_expired_draft_is_not_shown(self):
        from mokata.ide import plans as P
        from mokata import approval
        _repo(self.root)
        _store(self.root, DRAFT_RUN).write("approved_approach", _approach("refunds"))
        approval.propose(self.root, tool="spec_emit", args={"t": 1}, run_id=DRAFT_RUN,
                         now=time.time() - approval.DEFAULT_TTL_SECONDS - 5)
        self.assertEqual(P.plan(self.root, DRAFT_RUN)["pending_spec"], [])

    def test_a_non_spec_proposal_is_not_a_spec_draft(self):
        from mokata.ide import plans as P
        from mokata import approval
        _repo(self.root)
        approval.propose(self.root, tool="memory_write", args={"t": 1}, run_id=SPEC_RUN)
        self.assertEqual(P.plan(self.root, SPEC_RUN)["pending_spec"], [])


class SavedPlanFilesAreReadable(_Repo):

    def _write(self, rel, text="# plan\n\n- a\n"):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_both_places_are_listed_and_the_run_links_its_file(self):
        from mokata.ide import plans as P
        _repo(self.root)
        self._write(".mokata/temp_local/plans/add-shipping-to-checkout.md")
        self._write("plans/add-shipping-to-checkout.md")
        self._write("plans/notes.txt")                       # not a plan file
        got = {(r["where"], r["slug"]) for r in P.saved_plans(self.root)["plans"]}
        self.assertEqual(got, {("internal", "add-shipping-to-checkout"),
                               ("exported", "add-shipping-to-checkout")})
        links = P.plan(self.root, SPEC_RUN)["plan_files"]
        self.assertEqual({x["where"] for x in links}, {"internal", "exported"})

    def test_the_real_approval_path_writes_a_file_the_ide_lists(self):
        """The plan file comes from `persist_approach(plans_dir=...)` — the IDE reads THAT."""
        from mokata.brainstorm import Approach, BrainstormSession, persist_approach
        from mokata.brainstorm_impact import FITS, DesignFitVerdict
        from mokata.config import Surface
        from mokata.ide import plans as P
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        s = BrainstormSession("add shipping to checkout")
        s.propose_approaches([Approach(name="flat", summary="flat fee", pros=["a"], cons=["b"]),
                              Approach(name="weight", summary="by weight", pros=["c"], cons=["d"])])
        s.assess_impacts(layer=None, memory_items=[])
        for a in s.approaches:
            s.record_design_fit(a.name, DesignFitVerdict(a.name, FITS, [], rationale="fits"))
        s.assess_prior_art(layer=None)
        s.approve("jas", "flat")
        persist_approach(s, _store(self.root, SPEC_RUN), plans_dir=Surface.load(self.root).plans_dir)
        [row] = P.saved_plans(self.root)["plans"]
        text = P.saved_plan(self.root, row["slug"], row["where"])["text"]
        self.assertIn("add shipping to checkout", text)
        self.assertEqual(P.plan(self.root, SPEC_RUN)["plan_files"][0]["slug"], row["slug"])

    def test_a_name_with_a_path_or_a_bad_place_is_refused(self):
        from mokata.ide import plans as P
        from mokata.ide.server import BadRequest
        _repo(self.root)
        self._write("plans/ok.md")
        for slug, where in (("../../etc/passwd", "exported"), ("a/b", "exported"),
                            (".hidden", "exported"), ("ok", "anywhere"), ("", "exported"),
                            ("missing", "exported")):
            with self.subTest(slug=slug, where=where), self.assertRaises(BadRequest):
                P.saved_plan(self.root, slug, where)
        self.assertIn("plan", P.saved_plan(self.root, "ok", "exported")["text"])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_plan_is_never_followed(self):
        from mokata.ide import plans as P
        from mokata.ide.server import BadRequest
        _repo(self.root)
        outside = self._write("secret.txt", "TOKEN=1")
        os.makedirs(os.path.join(self.root, "plans"), exist_ok=True)
        os.symlink(outside, os.path.join(self.root, "plans", "leak.md"))
        self.assertEqual(P.saved_plans(self.root)["plans"], [])
        with self.assertRaises(BadRequest):
            P.saved_plan(self.root, "leak", "exported")

    def test_a_huge_plan_is_bounded_and_says_so(self):
        from mokata.ide import plans as P
        _repo(self.root)
        self._write("plans/big.md", "x" * (P.MAX_PLAN_BYTES + 100))
        got = P.saved_plan(self.root, "big", "exported")
        self.assertTrue(got["truncated"])
        self.assertEqual(len(got["text"]), P.MAX_PLAN_BYTES)


class TheRoutesAreGuardedAndAnswer(_Repo):

    def _client(self, paired=True):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.root, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999")
        if paired:
            c.get("/pair?t=tok")
        return c

    def test_unpaired_requests_are_refused(self):
        _repo(self.root)
        c = self._client(paired=False)
        for url in ("/api/saved-plans", "/api/saved-plan?plan=x&where=exported"):
            with self.subTest(url=url):
                self.assertIn(c.get(url).status_code, (401, 403))

    def test_paired_requests_list_read_and_refuse(self):
        _repo(self.root)
        os.makedirs(os.path.join(self.root, "plans"))
        # newline="\n": text mode on Windows writes "\r\n", and the route serves the file's own bytes.
        with open(os.path.join(self.root, "plans", "p.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# P\n")
        c = self._client()
        self.assertEqual(c.get("/api/saved-plans").json()["plans"][0]["slug"], "p")
        self.assertEqual(c.get("/api/saved-plan?plan=p&where=exported").json()["text"], "# P\n")
        self.assertEqual(c.get("/api/saved-plan?plan=../x&where=exported").status_code, 400)

    def test_the_sample_answers_without_touching_the_repo(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.root, "tok", 9999, None, True)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        got = c.get("/api/saved-plans").json()
        self.assertTrue(got["sample"])
        self.assertIn("SAMPLE", c.get("/api/saved-plan?plan=sample-parser-fix&where=internal")
                      .json()["text"])


class ThePageShowsIt(unittest.TestCase):
    """The page wiring the API needs — read from the shipped static files."""

    def _read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_plan_view_has_the_saved_files_list_and_renders_drafts(self):
        html, js = self._read("index.html"), self._read("plan.js")
        for ident in ('id="plan-files"', 'id="plan-file"', 'id="plan-run-note"'):
            self.assertIn(ident, html)
        for needle in ("/api/saved-plans", "/api/saved-plan?", "pending_spec", "renderDrafts"):
            self.assertIn(needle, js)

    def test_the_plan_file_is_rendered_as_text_nodes_not_markup(self):
        js = self._read("plan.js")
        self.assertNotIn("innerHTML", js)


class TheGraphPageExplainsItself(unittest.TestCase):
    """0.0.25 — "make it easy to understand how to select a symbol and what that means". The
    browser run (docs/build/handoff/0025-reports/ide-e2e) drives these; here the wiring is pinned."""

    def _read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_guide_the_start_card_and_the_picker_are_on_the_graph_view(self):
        html = self._read("index.html")
        view = html[html.index('id="view-graph"'):html.index('id="view-plan"')]
        for ident in ('id="g-guide"', 'id="g-help"', 'id="g-hint"', 'id="g-sug"',
                      'id="g-suggest"', "Pick a symbol to start"):
            self.assertIn(ident, view, f"{ident} is not on the graph view (the left panel is "
                                       "hidden in graph mode, so help there is never seen)")

    def test_the_picker_offers_real_symbols_with_kind_and_place(self):
        js = self._read("graph.js")
        self.assertIn("/api/quickopen", js)
        self.assertIn("sy.kind", js)
        self.assertIn("sy.path", js)

    def test_a_node_width_never_waits_for_a_layout_to_measure_it(self):
        """`width: "label"` is resolved only when a layout measures the nodes; the ring layout
        places nodes by position, so every node but the centre was zero-width and invisible."""
        js = self._read("graph.js")
        self.assertIsNone(re.search(r"width:\s*[\"']label[\"']", js))


if __name__ == "__main__":
    unittest.main()
