"""GR.S2 (m) — REVIEW joins the `uses_graph` consumers as a THIN verification gate.

doc-67 shift-left: review stays the thin gate — bounded VERIFICATION only (never discovery;
discovery stays in develop). Two halves:
  * pass 2 (live from GR.S2 day one): callers-of-changed-symbols + guards on touched paths,
    top-N + hop-bounded, sharing the existing budget (no second channel).
  * pass 1 (DORMANT): the diff's actual blast radius vs the approach's DECLARED `about_code`
    anchors (AP-SD `decisions[]`). AP-SD builds AFTER GR.S2, so this half is behind a
    capability check that activates only when `decisions` is supplied — a named hook + this
    test assert the dormant path.

Graph-absent review is byte-identical to today's review (the whole point of the thin gate).
"""

import unittest

from mokata.engine import ChangeSet
from mokata.execmode.review import two_stage_review
from mokata.execmode.review_graph import graph_verify
from mokata.knowledge import GrepBackend, KnowledgeLayer, QueryResult, Reference
from mokata.knowledge.query import BASIS_STRUCTURAL


class FakeGraphLayer:
    """A uses_graph layer that records the depth its blast_radius was called with."""
    uses_graph = True

    def __init__(self, callers=None, radius=None):
        self._callers = callers or {}
        self._radius = radius or {}
        self.depths = []

    def callers(self, sym):
        refs = [Reference(**r) for r in self._callers.get(sym, [])]
        return QueryResult(kind="callers", target=sym, references=refs,
                           backend="code-review-graph", basis=BASIS_STRUCTURAL)

    def blast_radius(self, sym, depth=2):
        self.depths.append(depth)
        refs = [Reference(**r) for r in self._radius.get(sym, [])]
        return QueryResult(kind="blast_radius", target=sym, references=refs,
                           backend="code-review-graph", basis=BASIS_STRUCTURAL)


class TestPass2Callers(unittest.TestCase):
    def test_flags_a_caller_outside_the_diff(self):
        layer = FakeGraphLayer(callers={
            "handle": [{"path": "other.py", "line": 5, "symbol": "other.run"},
                       {"path": "changed.py", "line": 9, "symbol": "changed.helper"}]})
        change = ChangeSet(symbols=["handle"], files=["changed.py"])
        res = graph_verify(layer, change)
        risks = [f for f in res.findings if f.kind == "caller-at-risk"]
        # only the caller OUTSIDE the touched files is an at-risk (silently-breakable) caller.
        self.assertEqual(len(risks), 1)
        self.assertEqual(risks[0].detail_path, "other.py")

    def test_top_n_bounds_symbols(self):
        layer = FakeGraphLayer(callers={f"s{i}": [] for i in range(50)})
        change = ChangeSet(symbols=[f"s{i}" for i in range(50)], files=[])
        res = graph_verify(layer, change, top_n=10)
        self.assertLessEqual(res.checked_symbols, 10)

    def test_budget_is_shared(self):
        layer = FakeGraphLayer(callers={f"s{i}": [] for i in range(50)})
        change = ChangeSet(symbols=[f"s{i}" for i in range(50)], files=[])
        res = graph_verify(layer, change, budget=3)
        self.assertLessEqual(res.checked_symbols, 3)


class TestBoundedDepth(unittest.TestCase):
    def test_blast_radius_is_hop_bounded(self):
        layer = FakeGraphLayer(radius={"handle": []})
        change = ChangeSet(symbols=["handle"], files=[])
        graph_verify(layer, change, max_depth=2, decisions=[{"about_code": ["x"]}])
        # pass 1 (here activated by decisions) uses the bounded blast radius.
        self.assertTrue(all(d <= 2 for d in layer.depths))
        self.assertIn(2, layer.depths)


class TestDormantPass1(unittest.TestCase):
    """Pass 1 activates ONLY when AP-SD `decisions[]` is supplied (the capability check)."""

    def test_dormant_no_decisions(self):
        layer = FakeGraphLayer(radius={"handle": [
            {"path": "reached.py", "line": 1, "symbol": "reached.thing"}]})
        change = ChangeSet(symbols=["handle"], files=[])
        res = graph_verify(layer, change)                 # no decisions -> pass 1 dormant
        self.assertEqual([f for f in res.findings if f.kind == "undeclared-reach"], [])

    def test_active_flags_undeclared_reach(self):
        layer = FakeGraphLayer(radius={"handle": [
            {"path": "reached.py", "line": 1, "symbol": "reached.thing"}]})
        change = ChangeSet(symbols=["handle"], files=[])
        # the approach declared it would only touch `declared.thing` — `reached.thing` is undeclared.
        decisions = [{"about_code": ["declared.thing"]}]
        res = graph_verify(layer, change, decisions=decisions)
        undeclared = [f for f in res.findings if f.kind == "undeclared-reach"]
        self.assertTrue(undeclared)
        self.assertEqual(undeclared[0].pass_no, 1)


class TestGraphAbsent(unittest.TestCase):
    def test_verify_degrades_loud_no_findings(self):
        change = ChangeSet(symbols=["x"], files=[])
        res = graph_verify(None, change)
        self.assertTrue(res.degraded)
        self.assertEqual(res.findings, [])

    def test_floor_is_not_a_graph_consumer(self):
        change = ChangeSet(symbols=["x"], files=[])
        res = graph_verify(KnowledgeLayer(primary=GrepBackend(root=".")), change)
        self.assertTrue(res.degraded)
        self.assertEqual(res.findings, [])

    def test_two_stage_review_unchanged(self):
        class T:  # a task/result duck
            output = "did the thing"
            ok = True
        baseline = two_stage_review(T(), T())
        withnone = two_stage_review(T(), T(), layer=None, change=None)
        self.assertEqual(baseline.summary(), withnone.summary())
        self.assertEqual([s.passed for s in baseline.stages],
                         [s.passed for s in withnone.stages])


if __name__ == "__main__":
    unittest.main()
