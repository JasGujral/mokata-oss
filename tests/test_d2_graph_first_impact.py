"""0.0.23 stage D2 — `graph-first-impact` residual: (b) rides D1, (d) the degraded wording.

(d) a blast radius the LEXICAL floor produced carries the honest "degraded — install
code-review-graph for full impact" note at the floor, and every surface that hands a blast radius to a
reader (`mokata query`, the MCP `query` tool, develop's live-evidence lines) attaches the SAME
layer-aware remedy — adopt when no graph is wired, repair when one is adopted and did not answer,
check-the-target when the floor found nothing — via ONE function, `knowledge.layer.impact_degrade`.
(b) the live-evidence re-verify is graph-first: it asks the layer, never grep directly.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.engine import live_evidence as LE
from mokata.govern.graph_required import REMEDY_ADOPT, REMEDY_CHECK_TARGET, REMEDY_REPAIR
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.layer import impact_degrade
from mokata.knowledge.query import (BASIS_LEXICAL, BASIS_STRUCTURAL, GREP_FLOOR_IMPACT_NOTE,
                                    GREP_FLOOR_NAV_NOTE, QueryResult, Reference)


class _L:
    def __init__(self, uses_graph=False, pinned=None):
        self.uses_graph, self.pinned_graph_tool = uses_graph, pinned


def _qr(kind="blast_radius", refs=("a.py",), basis=BASIS_LEXICAL, target="sym"):
    return QueryResult(kind=kind, target=target, backend="grep", basis=basis,
                       references=[Reference(path=p, line=1) for p in refs])


class TheFloorSaysItIsDegraded(unittest.TestCase):

    def test_a_grep_floor_blast_radius_carries_the_impact_note(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "wb") as fh:
                fh.write(b"def amountMax():\n    pass\n\namountMax()\n")
            res = GrepBackend(d).query("blast_radius", "amountMax")
            self.assertIn(GREP_FLOOR_IMPACT_NOTE, res.note)
            self.assertNotIn(GREP_FLOOR_NAV_NOTE, res.note, "impact is not navigation")

    def test_a_navigation_answer_does_not_get_the_impact_note(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "wb") as fh:
                fh.write(b"def f():\n    pass\n")
            res = GrepBackend(d).query("callers", "f")
            self.assertNotIn(GREP_FLOOR_IMPACT_NOTE, res.note)
            self.assertIn(GREP_FLOOR_NAV_NOTE, res.note)


class OneRemedyThreeSurfaces(unittest.TestCase):

    def test_no_graph_wired_says_adopt(self):
        deg = impact_degrade(_L(), _qr())
        self.assertEqual(REMEDY_ADOPT, deg["remedy"])
        self.assertIn("install code-review-graph for full impact", deg["message"])
        self.assertIn("mokata graph adopt", deg["message"])

    def test_an_adopted_graph_that_fell_to_the_floor_says_repair_not_adopt(self):
        for layer in (_L(uses_graph=True), _L(pinned="code-review-graph")):
            deg = impact_degrade(layer, _qr())
            self.assertEqual(REMEDY_REPAIR, deg["remedy"])
            self.assertIn("mokata doctor", deg["message"])
            self.assertIn("adopting again will not fix it", deg["message"])

    def test_an_empty_floor_answer_says_check_the_target(self):
        deg = impact_degrade(_L(), _qr(refs=()))
        self.assertEqual(REMEDY_CHECK_TARGET, deg["remedy"])
        self.assertIn("mokata query defs sym", deg["message"])

    def test_a_structural_or_non_impact_answer_has_no_degrade(self):
        self.assertIsNone(impact_degrade(_L(), _qr(basis=BASIS_STRUCTURAL)))
        self.assertIsNone(impact_degrade(_L(), _qr(kind="callers")))
        self.assertIsNone(impact_degrade(_L(), None))

    def test_the_cli_prints_the_shared_message(self):
        from mokata.cli_commands import knowledge as K
        args = mock.Mock(path=".", kind="blast_radius", target="sym", depth=2, json=False)
        with mock.patch.object(K, "_load_surface"), \
                mock.patch.object(K.KnowledgeLayer, "from_surface", return_value=_L()), \
                mock.patch("mokata.knowledge.layer.run_query", return_value=_qr()):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(0, K.cmd_query(args))
        self.assertIn("⚠ " + impact_degrade(_L(), _qr())["message"], buf.getvalue())

    def test_the_mcp_tool_carries_the_shared_message_and_omits_it_when_structural(self):
        from mokata.mcp import tools_read as T
        with mock.patch.object(T, "_surface"), \
                mock.patch.object(T.KnowledgeLayer, "from_surface", return_value=_L()), \
                mock.patch("mokata.knowledge.layer.run_query", return_value=_qr()):
            out = T.query(kind="blast_radius", target="sym")
        self.assertEqual(impact_degrade(_L(), _qr()), out["impact_degraded"])
        with mock.patch.object(T, "_surface"), \
                mock.patch.object(T.KnowledgeLayer, "from_surface", return_value=_L()), \
                mock.patch("mokata.knowledge.layer.run_query",
                           return_value=_qr(basis=BASIS_STRUCTURAL)):
            out = T.query(kind="blast_radius", target="sym")
        self.assertNotIn("impact_degraded", out, "a structural answer is byte-identical")


class TheLiveEvidenceReverifyIsGraphFirst(unittest.TestCase):

    def test_a_lexical_live_evidence_finding_carries_the_same_remedy(self):
        class Layer(_L):
            def blast_radius(self, sym, depth=2):
                return _qr(refs=("src/new.py",), target=sym)
        layer = Layer()
        expected = impact_degrade(layer, _qr(refs=("src/new.py",), target="amountMax"))["message"]

        def remedy(qr):
            return impact_degrade(layer, qr)["message"]
        found = LE.reverify_consumers(["amountMax"], [], layer, remedy_for=remedy)
        self.assertIn(expected, found[0].line())

    def test_a_structural_live_evidence_finding_carries_no_remedy(self):
        class Layer(_L):
            def blast_radius(self, sym, depth=2):
                return _qr(refs=("src/new.py",), target=sym, basis=BASIS_STRUCTURAL)
        found = LE.reverify_consumers(["x"], [], Layer(),
                                      remedy_for=lambda qr: "SHOULD NOT APPEAR")
        self.assertNotIn("SHOULD NOT APPEAR", found[0].line())


if __name__ == "__main__":
    unittest.main()
