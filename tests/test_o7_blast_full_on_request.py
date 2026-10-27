"""0.0.26 O7 (BLAST-FULL-ON-REQUEST) — a blast radius the default bound cut can be walked again
with the FULL budget, on request only.

What is asserted, against the behaviour a user relies on:
  * the default bound still cuts (ruling U10 keeps 20k refs / 2 s) and a cut result OFFERS the
    full walk — the IDE impact payload carries `full_available`, the page renders the action, the
    CLI prints the `--full` line;
  * the full walk returns the COMPLETE set on a synthetic graph that exceeds the default cap, so a
    symbol the default left "not determined" gets a real verdict (untested / covered);
  * the full walk is still BOUNDED (its own ceiling marks it truncated, "even with the full
    budget") and CANCELLABLE (the checkpoint raising stops the walk);
  * `full` is refused for every kind but blast_radius, and the IDE only takes it for ONE symbol.
"""
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.init import init_repo
from mokata.knowledge import ast_backend as AB
from mokata.knowledge.layer import KnowledgeLayer, run_query
from mokata.knowledge.query import BUDGET_DEFAULT, BUDGET_FULL

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")
N = 120          # direct callers of `hub`; each has its own outer caller -> 2N refs at depth 2
SMALL_CAP = 50   # the default reference cap, shrunk so the synthetic graph exceeds it


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _hub_files(d, test_reaches=None):
    _write(d, "src/app/hub.py", "def hub(x):\n    return x\n")
    for i in range(N):
        _write(d, f"src/app/m{i:03d}.py",
               f"from app.hub import hub\n\n\ndef c{i}():\n    return hub({i})\n\n\n"
               f"def o{i}():\n    return c{i}()\n")
    if test_reaches is not None:
        # The ONLY test reaches hub three hops out, through the LAST outer caller — past the cut.
        _write(d, "tests/test_far.py",
               f"from app.m{test_reaches:03d} import o{test_reaches}\n\n\n"
               f"def test_far():\n    o{test_reaches}()\n")


class TheKnowledgeLayerWalksFullOnRequest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-o7-k-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _hub_files(self.root)
        self.b = AB.AstBackend(self.root, cache_dir=os.path.join(self.root, ".cache"))

    def test_the_default_cut_stands_and_the_full_walk_returns_the_complete_set(self):
        with mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            cut = self.b.query("blast_radius", "hub", depth=2)
            full = self.b.blast_radius_full("hub", depth=2)
        self.assertTrue(cut.truncated, "the default bound no longer cuts (U10 keeps it)")
        self.assertEqual(SMALL_CAP, cut.count)
        self.assertFalse(full.truncated, f"full walk was cut: {full.note}")
        want = {f"c{i}" for i in range(N)} | {f"o{i}" for i in range(N)}
        self.assertEqual(want, {r.symbol for r in full.references})
        self.assertEqual(2 * N, full.count)
        self.assertTrue(full.structural)
        # the default prefix is the full answer's prefix, in the same order
        self.assertEqual([(r.path, r.line) for r in cut.references],
                         [(r.path, r.line) for r in full.references][:SMALL_CAP])

    def test_the_full_walk_is_still_bounded_and_says_so(self):
        with mock.patch.object(AB, "BLAST_RADIUS_FULL_MAX_REFS", 100):
            full = self.b.blast_radius_full("hub", depth=2)
        self.assertTrue(full.truncated)
        self.assertEqual(100, full.count)
        self.assertIn(AB.FULL_BUDGET_CUT, full.note)
        self.assertGreater(AB.BLAST_RADIUS_FULL_TIME_BUDGET_S, AB.BLAST_RADIUS_TIME_BUDGET_S)
        self.assertLessEqual(AB.BLAST_RADIUS_FULL_TIME_BUDGET_S, 60.0, "the ceiling is a hard one")

    def test_the_full_walk_is_cancellable(self):
        calls = []

        class Stop(Exception):
            pass

        def checkpoint():
            calls.append(1)
            if len(calls) >= 5:
                raise Stop()
        with self.assertRaises(Stop):
            self.b.blast_radius_full("hub", depth=2, checkpoint=checkpoint)
        self.assertEqual(5, len(calls), "the walk did not stop at the checkpoint that raised")

    def test_the_layer_routes_full_and_refuses_it_for_any_other_kind(self):
        layer = KnowledgeLayer(self.b)
        with mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            self.assertTrue(run_query(layer, "blast_radius", "hub", depth=2).truncated)
            full = run_query(layer, "blast_radius", "hub", depth=2, budget=BUDGET_FULL)
        self.assertEqual(2 * N, full.count)
        for kind in ("callers", "defs", "semantic"):
            with self.assertRaises(ValueError):
                run_query(layer, kind, "hub", budget=BUDGET_FULL)
        with self.assertRaises(ValueError):
            run_query(layer, "blast_radius", "hub", budget="huge")

    def test_a_backend_without_a_budget_answers_full_as_it_answers_default(self):
        from mokata.knowledge.grep_backend import GrepBackend
        g = GrepBackend(root=self.root)
        self.assertEqual([(r.path, r.line) for r in g.query("blast_radius", "hub", depth=2).references],
                         [(r.path, r.line) for r in g.blast_radius_full("hub", depth=2).references])


class TheIdeOffersTheFullWalkOnACappedImpact(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-o7-i-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(nav.close_layers)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)

    def _sym(self, out):
        self.assertEqual(1, len(out["symbols"]))
        return out["symbols"][0]

    def test_a_capped_result_offers_the_action_and_the_full_run_proves_untested(self):
        _hub_files(self.root)
        with mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            first = self._sym(nav.impact(self.root, "src/app/hub.py", "1-2"))
            self.assertEqual("unknown", first["verdict"])
            self.assertTrue(first["blast"]["truncated"])
            self.assertTrue(first["blast"]["full_available"], "a cut result does not offer the walk")
            self.assertIn("not determined", first["blast"]["not_determined"])
            out = nav.impact(self.root, "src/app/hub.py", "1-2", ["hub"], budget=BUDGET_FULL)
        full = self._sym(out)
        self.assertEqual(BUDGET_FULL, out["budget"])
        self.assertFalse(full["blast"]["truncated"])
        self.assertFalse(full["blast"]["full_available"])
        self.assertEqual("untested", full["verdict"],
                         "the complete structural radius with no test must prove untested")

    def test_the_full_run_finds_a_test_past_the_default_cut(self):
        _hub_files(self.root, test_reaches=N - 1)
        with mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            first = self._sym(nav.impact(self.root, "src/app/hub.py", "1-2"))
            self.assertEqual("unknown", first["verdict"])
            full = self._sym(nav.impact(self.root, "src/app/hub.py", "1-2", ["hub"],
                                        budget=BUDGET_FULL))
        self.assertEqual("covered", full["verdict"])
        self.assertTrue(any("test_far" in t for t in full["tests"]), full["tests"])

    def test_an_uncut_result_offers_nothing_and_full_is_one_symbol_only(self):
        _hub_files(self.root)
        sym = self._sym(nav.impact(self.root, "src/app/hub.py", "1-2"))
        self.assertFalse(sym["blast"]["truncated"])
        self.assertFalse(sym["blast"]["full_available"])
        with self.assertRaises(ValueError):
            nav.impact(self.root, "src/app/hub.py", "1-2", None, budget=BUDGET_FULL)
        with self.assertRaises(ValueError):
            nav.impact(self.root, "src/app/hub.py", "1-2", ["hub", "c1"], budget=BUDGET_FULL)

    def test_the_route_takes_full_for_one_symbol_as_a_job_with_no_deadline(self):
        from starlette.testclient import TestClient
        from mokata.ide import server
        _hub_files(self.root)
        c = TestClient(server.build_app(server.AppState(self.root, "tok", 9999, None, False)),
                       base_url="http://127.0.0.1:9999")
        c.get("/pair?t=tok")
        base = "/api/impact?path=src/app/hub.py&lines=1-2"
        self.assertEqual(400, c.get(base + "&budget=full").status_code)
        self.assertEqual(400, c.get(base + "&symbols=hub,c1&budget=full").status_code)
        self.assertEqual(400, c.get(base + "&symbols=hub&budget=huge").status_code)
        with mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            got = c.get(base + "&symbols=hub&budget=full&sync_ms=2000").json()
        self.assertEqual(2 * N, got["symbols"][0]["blast"]["count"])
        # 0.0.26 P32 — the route has no deadline of its own any more: a long walk is a job.
        self.assertFalse(hasattr(server, "FULL_IMPACT_DEADLINE_SECS"))

    def test_the_page_renders_the_action_on_a_cut_result(self):
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("cur.blast.full_available", js)
        self.assertIn("Calculate the full blast radius", js)
        self.assertIn("&budget=full", js)


class TheCliOffersAndRunsTheFullWalk(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-o7-c-")
        self.addCleanup(shutil.rmtree, self.root, True)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        _hub_files(self.root)

    def _cli(self, *argv):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(AB, "BLAST_RADIUS_MAX_REFS", SMALL_CAP):
            rc = cli.main(["query", *argv, "--path", self.root])
        return rc, out.getvalue(), err.getvalue()

    def test_a_cut_radius_prints_the_full_line_and_full_walks_it(self):
        rc, out, _ = self._cli("blast_radius", "hub")
        self.assertEqual(0, rc)
        self.assertIn(f"{SMALL_CAP} result(s)", out)
        self.assertIn("--full", out)
        rc, out, _ = self._cli("blast_radius", "hub", "--full")
        self.assertEqual(0, rc)
        self.assertIn(f"{2 * N} result(s)", out)
        self.assertNotIn("truncated", out)

    def test_full_is_refused_for_other_kinds(self):
        rc, _out, err = self._cli("callers", "hub", "--full")
        self.assertEqual(2, rc)
        self.assertIn("blast_radius only", err)


def setUpModule():
    # 0.0.26 O11 — this module's expectations are the CORE install's: no external code graph on
    # the machine. Pinned, so a venv with code-review-graph (`mokata[full]`) means the same thing;
    # what is true WITH it present is graded in test_o11_crg_present (and the sibling below, if any).
    from _support import hide_external_graphs
    hide_external_graphs()


if __name__ == "__main__":
    unittest.main()
