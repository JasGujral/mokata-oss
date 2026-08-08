"""GR.S3 — the three decision consumers REFUSE a degraded blast radius (graph.required default).

Lens-1 (brainstorm blast radius), spec-check (the regression guard's touch-set), and domain
classification each stop presenting a degraded radius as decision input once `graph.required` is on
(the default). The escape is the ledgered `--allow-degraded`; the honesty (the degraded marking)
survives it. This file covers:

  * the query-level `ApproachImpact.graph_degraded` signal (AST-with-evidence is NOT it);
  * per-consumer refusal (Lens-1 approve, spec-check guard, domain classify) + the MCP-parity twin;
  * the ledgered override proceeds + marking survives (the honesty negative);
  * explicit graph.required=false ⇒ byte-identical; a real graph ⇒ no refusal (healthy negative);
  * the empty-AST refusal cites AST-zero + the lexical mention count (the GR.S1 hand-off);
  * the release exit-criterion sweep: no consumer renders a degraded radius without the escape.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import Approach, BrainstormGateError, BrainstormSession
from mokata.brainstorm_impact import DesignFitVerdict, compute_impact
from mokata.knowledge.query import QueryResult, Reference
from mokata.knowledge.query import BASIS_LEXICAL, BASIS_STRUCTURAL


# ---------------------------------------------------------------- deterministic fake layers
class _Layer:
    """A stand-in graph. `uses_graph` marks a REAL adopted graph; `backend_name` names the floor;
    `degraded` is the QUERY-level flag (the grep floor / empty-AST fallthrough answered)."""

    def __init__(self, table, *, uses_graph=True, backend_name="fake", degraded=False):
        self.table = table
        self.uses_graph = uses_graph
        self.backend_name = backend_name
        self._degraded = degraded

    def blast_radius(self, symbol, depth=2):
        return QueryResult("blast_radius", symbol,
                           references=list(self.table.get(symbol, [])),
                           backend=self.backend_name,
                           basis=(BASIS_LEXICAL if self._degraded else BASIS_STRUCTURAL))


def _ref(path, line, sym):
    return Reference(path, line, snippet="", symbol=sym)


_TABLE = {"pay": [_ref("app/pay.py", 5, "charge"), _ref("app/api.py", 9, "handler"),
                  _ref("tests/test_pay.py", 2, "test_charge")]}


# ================================================================ ApproachImpact.graph_degraded
class TestQueryLevelSignal(unittest.TestCase):
    """`graph_degraded` is the QUERY-level floor signal — distinct from the display `degraded`
    caveat, so AST answering with evidence (query degraded=False) is NOT refused (GR.S1)."""

    def test_real_graph_with_evidence_is_not_graph_degraded(self):
        layer = _Layer(_TABLE, uses_graph=True, degraded=False)
        imp = compute_impact("a", ["pay"], layer=layer)
        self.assertFalse(imp.graph_degraded)
        self.assertFalse(imp.degraded)

    def test_ast_with_evidence_is_not_graph_degraded_though_display_degraded(self):
        # AST floor: uses_graph=False (is_graph=False) BUT the query answered with evidence
        # (degraded=False). The display caveat stays (degraded=True, uses_graph term) but the
        # refusal signal is False — so AST-with-evidence must NOT refuse.
        layer = _Layer(_TABLE, uses_graph=False, backend_name="ast", degraded=False)
        imp = compute_impact("a", ["pay"], layer=layer)
        self.assertFalse(imp.graph_degraded)               # refusal signal
        self.assertTrue(imp.degraded)                      # display caveat, unchanged (byte-identical)

    def test_grep_floor_is_graph_degraded(self):
        layer = _Layer(_TABLE, uses_graph=False, backend_name="grep", degraded=True)
        imp = compute_impact("a", ["pay"], layer=layer)
        self.assertTrue(imp.graph_degraded)

    def test_no_layer_is_graph_degraded(self):
        imp = compute_impact("a", ["pay"], layer=None)
        self.assertTrue(imp.graph_degraded)

    def test_graph_degraded_round_trips(self):
        imp = compute_impact("a", ["pay"], layer=None)
        from mokata.brainstorm_impact import ApproachImpact
        self.assertTrue(ApproachImpact.from_dict(imp.to_dict()).graph_degraded)


# ---------------------------------------------------------------- brainstorm session helper
def _session_with_impact(layer):
    s = BrainstormSession("how to bill")
    s.propose_approaches([
        Approach("a", "approach a", pros=["fast"], cons=["risky"], targets=["pay"]),
        Approach("b", "approach b", pros=["safe"], cons=["slow"], targets=["pay"]),
    ])
    s.assess_impacts(layer=layer)
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    return s


# ================================================================ Lens-1 refusal (approve gate)
class TestLensOneRefusal(unittest.TestCase):

    def test_degraded_approve_is_refused(self):
        from mokata.govern.graph_required import check_graph_required
        s = _session_with_impact(_Layer(_TABLE, uses_graph=False, backend_name="grep",
                                         degraded=True))
        gate = check_graph_required(degraded=s.impacts["a"].graph_degraded, required=True,
                                    overridden=False, consumer="blast radius (Lens 1)",
                                    backend="grep")
        with self.assertRaises(BrainstormGateError) as ctx:
            s.approve("jas", "a", graph_gate=gate)
        self.assertIn("REFUSED", str(ctx.exception))
        self.assertIn("allow-degraded", str(ctx.exception))

    def test_override_lets_approval_proceed(self):
        from mokata.govern.graph_required import check_graph_required
        s = _session_with_impact(_Layer(_TABLE, uses_graph=False, degraded=True))
        gate = check_graph_required(degraded=True, required=True, overridden=True,
                                    consumer="blast radius (Lens 1)")
        s.approve("jas", "a", graph_gate=gate)             # does not raise
        self.assertTrue(s.approved)

    def test_real_graph_never_refuses(self):
        from mokata.govern.graph_required import check_graph_required
        s = _session_with_impact(_Layer(_TABLE, uses_graph=True, degraded=False))
        gate = check_graph_required(degraded=s.impacts["a"].graph_degraded, required=True,
                                    overridden=False, consumer="blast radius (Lens 1)")
        s.approve("jas", "a", graph_gate=gate)
        self.assertTrue(s.approved)

    def test_no_gate_supplied_preserves_old_behavior(self):
        # explicit graph.required=false path: callers pass no gate → byte-identical approve.
        s = _session_with_impact(_Layer(_TABLE, uses_graph=False, degraded=True))
        s.approve("jas", "a")
        self.assertTrue(s.approved)

    def test_helper_computes_the_gate_from_the_session(self):
        # brainstorm_impact_gate wires session → verdict (the shared entry both CLI + MCP use).
        from mokata.govern import graph_required as GR
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(_TABLE, uses_graph=False, backend_name="grep", degraded=True)
            s = _session_with_impact(layer)
            gate = GR.brainstorm_impact_gate(s, "a", surface=surface, run_id="run-x", layer=layer)
            self.assertTrue(gate.refused)


# ================================================================ MCP-parity twin (Lens-1)
class TestMcpParity(unittest.TestCase):
    """The refusal + consented escape behave IDENTICALLY through the MCP loop as through the CLI —
    both route through the shared verdict, and the MCP `spec_emit` enforces it at the durable
    boundary by reading the persisted brainstorm's chosen-approach radius."""

    def test_spec_emit_refuses_a_degraded_chosen_radius(self):
        from mokata.mcp import tools_write
        from mokata.brainstorm import save_brainstorm_progress
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(_TABLE, uses_graph=False, backend_name="grep", degraded=True)
            s = _session_with_impact(layer)
            s.approve("jas", "a")                          # approved with a degraded radius
            save_brainstorm_progress(s, surface.state)
            out = tools_write.spec_emit(
                path=root, title="bill users",
                criteria=[{"id": "AC1", "text": "charge on submit"}],
                tests=[{"name": "test_charge", "ac_ids": ["AC1"]}], approach="a")
            self.assertFalse(out.get("committed"))
            self.assertEqual(out.get("gate"), "graph-required")
            self.assertIn("REFUSED", out.get("reason", ""))

    def test_spec_emit_proceeds_when_radius_is_healthy(self):
        from mokata.mcp import tools_write
        from mokata.brainstorm import save_brainstorm_progress
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            layer = _Layer(_TABLE, uses_graph=True, degraded=False)     # a real graph
            s = _session_with_impact(layer)
            s.approve("jas", "a")
            save_brainstorm_progress(s, surface.state)
            out = tools_write.spec_emit(
                path=root, title="bill users",
                criteria=[{"id": "AC1", "text": "charge on submit"}],
                tests=[{"name": "test_charge", "ac_ids": ["AC1"]}], approach="a")
            self.assertNotEqual(out.get("gate"), "graph-required")       # not refused by GR.S3


# ================================================================ spec-check refusal
class TestSpecCheckRefusal(unittest.TestCase):

    def test_degraded_touch_set_is_refused(self):
        from mokata.engine.spec_awareness import ChangeSet, guard_change
        from mokata.engine.spec import Spec
        specs = [Spec(title="charge flow", source="s", criteria=[])]
        change = ChangeSet(symbols=["charge"], files=["app/pay.py"])
        # layer=None → graph_degraded=True in the report.
        outcome = guard_change(change, specs=specs, decisions=[], layer=None,
                               graph_required=True, graph_overridden=False)
        self.assertFalse(outcome.proceeded)
        self.assertTrue(outcome.blocked)
        self.assertIn("REFUSED", outcome.render())

    def test_override_lets_spec_check_proceed(self):
        from mokata.engine.spec_awareness import ChangeSet, guard_change
        change = ChangeSet(symbols=["charge"], files=["app/pay.py"])
        outcome = guard_change(change, specs=[], decisions=[], layer=None,
                               graph_required=True, graph_overridden=True)
        self.assertTrue(outcome.proceeded)                 # nothing to guard + override → clean

    def test_required_false_is_byte_identical(self):
        from mokata.engine.spec_awareness import ChangeSet, guard_change
        change = ChangeSet(symbols=["charge"], files=["app/pay.py"])
        a = guard_change(change, specs=[], decisions=[], layer=None)                      # default off
        b = guard_change(change, specs=[], decisions=[], layer=None, graph_required=False)
        self.assertEqual(a.render(), b.render())
        self.assertTrue(a.proceeded)


# ================================================================ domain-classification refusal
class TestDomainRefusal(unittest.TestCase):

    def test_classify_from_degraded_impact_is_refused(self):
        from mokata.domains import classify_from_impact
        from mokata.govern.graph_required import GraphDegradedError, check_graph_required
        imp = compute_impact("a", ["pay"], layer=None)     # graph_degraded=True
        gate = check_graph_required(degraded=imp.graph_degraded, required=True, overridden=False,
                                    consumer="domain classification")
        with self.assertRaises(GraphDegradedError):
            classify_from_impact(imp, graph_gate=gate)

    def test_classify_proceeds_without_a_gate(self):
        from mokata.domains import classify_from_impact
        imp = compute_impact("a", ["app/routes/pay.py"], layer=None)
        self.assertIsInstance(classify_from_impact(imp), list)   # byte-identical when no gate

    def test_classify_proceeds_when_overridden(self):
        from mokata.domains import classify_from_impact
        from mokata.govern.graph_required import check_graph_required
        imp = compute_impact("a", ["app/routes/pay.py"], layer=None)
        gate = check_graph_required(degraded=True, required=True, overridden=True,
                                    consumer="domain classification")
        self.assertIsInstance(classify_from_impact(imp, graph_gate=gate), list)


# ================================================================ empty-AST refusal (GR.S1 hand-off)
class TestEmptyAstRefusal(unittest.TestCase):

    def test_empty_ast_refusal_cites_ast_zero_and_mentions(self):
        # empty AST evidence falls through to grep → degraded=True; the refusal cites AST-zero +
        # the lexical mention count (backend == "ast").
        from mokata.govern.graph_required import check_graph_required
        layer = _Layer(_TABLE, uses_graph=False, backend_name="ast", degraded=True)
        imp = compute_impact("a", ["pay"], layer=layer)
        self.assertTrue(imp.graph_degraded)
        gate = check_graph_required(degraded=True, required=True, overridden=False,
                                    consumer="blast radius (Lens 1)", backend="ast",
                                    mentions=imp.caller_count, files=imp.file_count,
                                    targets=imp.targets)
        msg = gate.render()
        self.assertIn("AST", msg)
        self.assertIn(str(imp.caller_count), msg)
        self.assertIn("pay", msg)


# ================================================================ the release exit-criterion sweep
class TestExitCriterionSweep(unittest.TestCase):
    """The grep floor is reachable as a DECISION INPUT only via the ledgered escape (or
    graph.required=false): no consumer renders a degraded radius without an override or the flag."""

    def test_no_consumer_presents_degraded_without_escape(self):
        from mokata.govern.graph_required import check_graph_required
        # every consumer, on a degraded radius, with required-on and no override → refused.
        for consumer in ("blast radius (Lens 1)", "spec-check (regression guard)",
                         "domain classification"):
            gate = check_graph_required(degraded=True, required=True, overridden=False,
                                        consumer=consumer)
            self.assertTrue(gate.refused, f"{consumer} presented a degraded radius unguarded")
        # the ONLY two roads that let a degraded radius through:
        self.assertFalse(check_graph_required(degraded=True, required=True, overridden=True,
                                              consumer="x").refused)       # the ledgered escape
        self.assertFalse(check_graph_required(degraded=True, required=False, overridden=False,
                                              consumer="x").refused)       # graph.required=false


# ---------------------------------------------------------------- init helper
def _init(root):
    from mokata.init import init_repo
    from mokata.config import Surface
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(root)


if __name__ == "__main__":
    unittest.main()
