"""GR.S3-FU — spec-check consumer symmetry: the touch-set expansion consumes AST evidence.

GR.S3 refused a degraded blast radius as decision input, and Lens-1 correctly tolerated the AST
floor answering WITH evidence via the query-level `ApproachImpact.graph_degraded` signal. But
`spec_awareness.expand_touch_set` sat behind the LAYER-level `uses_graph` gate, so it refused on
EVERY AST-only Python repo — even when AST answered with real structural evidence. This closes
GR.S3's deviation 2: spec-check now keys its `graph.required` refusal on the SAME query-level signal
Lens-1 uses (AST-with-evidence ⇒ expand + no refusal, caveat shown; no-layer / failed-query /
empty-AST ⇒ refusal retained), and the three consumers key on query-level evidence in symmetry.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm_impact import compute_impact
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_awareness import ChangeSet, check_change, guard_change
from mokata.init import init_repo
from mokata.knowledge.layer import KnowledgeLayer
from mokata.knowledge.query import QueryResult, Reference


# --------------------------------------------------------------- real AST-repo fixtures
def _init(root):
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(root)


def _write_ast_repo(root):
    """A Python repo where `charge` has a real caller (`process` calls it), so the AST floor
    answers `callers`/`blast_radius` WITH evidence (degraded=False) — the AST-with-evidence case."""
    app = os.path.join(root, "app")
    os.makedirs(app, exist_ok=True)
    with open(os.path.join(app, "pay.py"), "w", encoding="utf-8") as fh:
        fh.write("def charge(amount):\n    return amount\n\n"
                 "def process(order):\n    return charge(order)\n")


# ================================================================ the named FU regression
class TestFuRegression(unittest.TestCase):

    def test_gr_s3_fu_regression(self):
        """AST-with-evidence repo + graph.required DEFAULT (true) ⇒ spec-check EXPANDS the touch-set
        (charge → its caller `process`) and does NOT refuse. Fails on pre-FU code, which refused on
        every AST-only repo because expand_touch_set never consumed AST evidence."""
        from mokata.cli_commands.knowledge import cmd_spec_check
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)                          # graph.required default true (no block)
            _write_ast_repo(root)
            # a saved spec about `process` — a CALLER of the changed `charge`. Only AST expansion of
            # the touch-set reaches it, so a surfaced conflict PROVES the expansion happened.
            spec = Spec(title="order-processing", source="s",
                        criteria=[AcceptanceCriterion("AC1", "process must stay atomic")])
            surface.state.write("spec_corpus", [spec.to_dict()])

            args = argparse.Namespace(path=root, symbols="charge", files="", text=None,
                                      phase="develop", yes=True, allow_degraded=False, reason="")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_spec_check(args)
            out = buf.getvalue()
            self.assertEqual(rc, 0, out)                   # proceeded (not refused)
            self.assertNotIn("REFUSED", out)               # AST-with-evidence is NOT refused
            self.assertIn("process", out)                  # the touch-set expanded to the caller


# ================================================================ check_change over a real AST layer
class TestCheckChangeConsumesAst(unittest.TestCase):

    def test_ast_with_evidence_expands_and_is_not_graph_degraded(self):
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            _write_ast_repo(root)
            layer = KnowledgeLayer.from_surface(surface)
            self.assertFalse(layer.uses_graph)             # AST floor, is_graph=False
            specs = [Spec(title="order-processing", source="s",
                          criteria=[AcceptanceCriterion("AC1", "process must stay atomic")])]
            rep = check_change(ChangeSet(symbols=["charge"]), specs, [], layer=layer)
            self.assertFalse(rep.graph_degraded)           # query-level: AST answered with evidence
            self.assertIn("process", rep.touch_set)        # the touch-set expanded to the caller
            self.assertTrue(rep.has_conflicts)             # so the impacted spec is caught

    def test_ast_with_evidence_guard_does_not_refuse_and_shows_caveat(self):
        # AST-with-evidence + graph.required on ⇒ NO refusal, and the AST caveat (honesty, GR.S1)
        # travels into the rendered output — it is NEVER stripped.
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            _write_ast_repo(root)
            layer = KnowledgeLayer.from_surface(surface)
            change = ChangeSet(symbols=["charge"])
            # a non-overlapping spec: the guard runs (renders a head) but finds no conflict, so the
            # only thing under test is that the AST caveat travels into the rendered head.
            specs = [Spec(title="unrelated login flow", source="s",
                          criteria=[AcceptanceCriterion("AC1", "the session token rotates")])]
            outcome = guard_change(change, specs=specs, decisions=[], layer=layer,
                                   graph_required=True, graph_overridden=False)
            self.assertIsNone(outcome.graph_refusal)       # not refused
            self.assertTrue(outcome.proceeded)
            self.assertIn("AST floor", outcome.render())   # the AST_NOTE caveat survives


# ================================================================ the GR.S1 hand-off negatives
class TestFloorStillRefuses(unittest.TestCase):
    """No-layer / failed-query / empty-AST ⇒ the refusal is RETAINED (a degraded touch-set cannot
    vouch for a clean corpus). The GR.S1 hand-off honesty."""

    def _refused(self, layer):
        change = ChangeSet(symbols=["charge"], files=["app/pay.py"])
        outcome = guard_change(change, specs=[Spec(title="charge flow", source="s", criteria=[])],
                               decisions=[], layer=layer,
                               graph_required=True, graph_overridden=False)
        return outcome

    def test_no_layer_is_refused(self):
        out = self._refused(None)
        self.assertFalse(out.proceeded)
        self.assertIn("REFUSED", out.render())

    def test_failed_query_is_refused(self):
        class _Raises:
            uses_graph = False
            backend_name = "ast"
            def callers(self, s): raise RuntimeError("boom")
            def callees(self, s): raise RuntimeError("boom")
            def blast_radius(self, s, depth=1): raise RuntimeError("boom")
        out = self._refused(_Raises())
        self.assertFalse(out.proceeded)
        self.assertIn("REFUSED", out.render())

    def test_empty_ast_evidence_is_refused(self):
        # a Python repo where `charge` has NO caller ⇒ the AST floor finds no structural evidence
        # and falls through to grep (degraded=True) ⇒ the refusal is retained.
        with tempfile.TemporaryDirectory() as root:
            surface = _init(root)
            app = os.path.join(root, "app")
            os.makedirs(app, exist_ok=True)
            with open(os.path.join(app, "pay.py"), "w", encoding="utf-8") as fh:
                fh.write("def charge(amount):\n    return amount\n")   # nobody calls charge
            layer = KnowledgeLayer.from_surface(surface)
            change = ChangeSet(symbols=["charge"])
            outcome = guard_change(change, specs=[Spec(title="c", source="s", criteria=[])],
                                   decisions=[], layer=layer,
                                   graph_required=True, graph_overridden=False)
            self.assertFalse(outcome.proceeded)
            self.assertIn("REFUSED", outcome.render())


# ================================================================ graph.required=false byte-identity
class TestRequiredFalseByteIdentical(unittest.TestCase):

    def test_required_false_is_byte_identical(self):
        change = ChangeSet(symbols=["charge"], files=["app/pay.py"])
        a = guard_change(change, specs=[], decisions=[], layer=None)                  # default off
        b = guard_change(change, specs=[], decisions=[], layer=None, graph_required=False)
        self.assertEqual(a.render(), b.render())
        self.assertTrue(a.proceeded)


# ---------------------------------------------------------------- fake layers (uses_graph FIXED)
class _FloorLayer:
    """A NON-graph layer (uses_graph=False) whose QUERY-level degraded flag is what varies — the
    AST floor answering with evidence (degraded=False) vs the grep floor (degraded=True). Both have
    uses_graph=False, so a consumer keying on the layer flag can't tell them apart; one keying on
    query evidence can."""

    def __init__(self, *, degraded):
        self.uses_graph = False
        self.backend_name = "ast" if not degraded else "grep"
        self._degraded = degraded

    def _res(self, kind, target):
        refs = [Reference("app/pay.py", 5, "", "process")]
        return QueryResult(kind, target, references=refs, backend=self.backend_name,
                           degraded=self._degraded, note="" if self._degraded else "ast note")

    def callers(self, s): return self._res("callers", s)
    def callees(self, s): return self._res("callees", s)
    def blast_radius(self, s, depth=1): return self._res("blast_radius", s)


# ================================================================ three-consumer symmetry sweep
class TestThreeConsumerSymmetry(unittest.TestCase):
    """Lens-1, spec-check, and domain classification ALL key their degraded verdict on QUERY-level
    evidence, never the layer-level `uses_graph` flag. Both layers below have uses_graph=False; a
    consumer that regressed to the layer flag would give the SAME verdict for both and fail these
    assertions — this is the guard that catches a future FOURTH consumer regressing that way."""

    def test_all_three_key_on_query_evidence_not_the_layer_flag(self):
        from mokata.domains import classify_from_impact
        from mokata.govern.graph_required import GraphDegradedError, check_graph_required

        evidence = _FloorLayer(degraded=False)   # AST-with-evidence (uses_graph=False)
        floor = _FloorLayer(degraded=True)       # grep floor         (uses_graph=False)

        # Lens-1
        imp_ev = compute_impact("a", ["charge"], layer=evidence)
        imp_fl = compute_impact("a", ["charge"], layer=floor)
        self.assertFalse(imp_ev.graph_degraded)
        self.assertTrue(imp_fl.graph_degraded)

        # spec-check (the regression guard's touch-set) — a non-empty corpus so the guard actually
        # computes the touch-set (an empty corpus short-circuits before expansion).
        corpus = [Spec(title="charge flow", source="s", criteria=[])]
        rep_ev = check_change(ChangeSet(symbols=["charge"]), corpus, [], layer=evidence)
        rep_fl = check_change(ChangeSet(symbols=["charge"]), corpus, [], layer=floor)
        self.assertFalse(rep_ev.graph_degraded)
        self.assertTrue(rep_fl.graph_degraded)

        # domain classification — refuses iff the impact's query-level signal is degraded
        def _classifies(imp):
            gate = check_graph_required(degraded=imp.graph_degraded, required=True,
                                        overridden=False, consumer="domain classification")
            try:
                classify_from_impact(imp, graph_gate=gate)
                return True
            except GraphDegradedError:
                return False
        self.assertTrue(_classifies(imp_ev))    # AST-with-evidence classifies
        self.assertFalse(_classifies(imp_fl))   # grep floor is refused


if __name__ == "__main__":
    unittest.main()
