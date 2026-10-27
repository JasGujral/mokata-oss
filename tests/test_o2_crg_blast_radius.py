"""0.0.26 O2 — `CRG-BLAST-EMPTY` (ruling U9): code-review-graph's blast radius, mapped correctly.

Through 0.0.24 mokata asked `traverse_graph_tool` for a blast radius. Its rows live under a
`traversal` key the parser never read (so EVERY CRG blast radius was empty) and it walks edges in
BOTH directions (callees, the containing File, that file's other functions). 0.0.25 routed impact
to the AST floor. 0.0.26 maps it to what a blast radius IS — a directed, transitive walk over
`query_graph_tool(pattern="callers_of")` — and keeps the AST floor beneath it for a symbol the
graph does not index, or a graph that fails.

The fixtures below are RECORDED from code-review-graph 2.3.9 (`tools/query.py::query_graph`) on
the same tiny repo the integration leg builds (tests/integration/test_o2_real_crg_blast_radius.py):

    pkg/core.py:  leaf()   mid() -> leaf()   unused()
    pkg/app.py:   from pkg.core import mid;  top() -> mid();  entry() -> top() + top()

Only the absolute root is re-rooted into a temp dir so the paths are real on every OS.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import crg_client
from mokata.knowledge.crg_client import CodeReviewGraphClient, CrgUnavailable
from mokata.knowledge.graph_backend import CodeReviewGraphBackend
from mokata.knowledge.layer import KnowledgeLayer, impact_backend_line
from mokata.knowledge.query import (BASIS_STRUCTURAL, GraphBackend, GraphNotIndexed,
                                    QueryResult, Reference)


def _recorded(root: str):
    """The real 2.3.9 `callers_of` payloads, keyed by the target mokata sends."""
    core = os.path.join(root, "pkg", "core.py")
    app = os.path.join(root, "pkg", "app.py")

    def node(nid, name, fp, ls, le, call_line):
        return {"id": nid, "kind": "Function", "name": name, "qualified_name": f"{fp}::{name}",
                "file_path": fp, "line_start": ls, "line_end": le, "language": "python",
                "parent_name": None, "is_test": False, "call_site": {"line": call_line}}

    def edge(eid, src, tgt, fp, line):
        return {"id": eid, "kind": "CALLS", "source": src, "target": tgt, "file_path": fp,
                "line": line, "confidence": 1.0, "confidence_tier": "EXTRACTED"}

    def ok(target, results, edges, confidence=None):
        p = {"status": "ok", "pattern": "callers_of", "target": target,
             "description": "Find all functions that call a given function",
             "summary": f"Found {len(results)} call site(s)",
             "result_count": len(results), "results_omitted": 0,
             "results": results, "edges": edges, "distinct_nodes": len(results),
             "resolution_split": {"direct": len(results), "unresolved": 0}}
        if confidence:
            p["confidence"] = confidence
        return p

    leaf, mid, top, entry, unused = (f"{core}::leaf", f"{core}::mid", f"{app}::top",
                                     f"{app}::entry", f"{core}::unused")
    leaf_p = ok(leaf, [node(7, "mid", core, 5, 6, 6)], [edge(8, mid, leaf, core, 6)])
    mid_p = ok(mid, [node(3, "top", app, 4, 5, 5)], [edge(3, top, mid, app, 5)])
    top_p = ok(top, [node(4, "entry", app, 8, 9, 9), node(4, "entry", app, 8, 9, 9)],
               [edge(5, entry, top, app, 9), edge(6, entry, top, app, 9)])
    top_p["results"][1]["call_site"] = {"line": 9}
    entry_p = ok(entry, [], [], confidence="python getattr dispatch and decorator-based "
                                            "registration are not statically traced, so callers "
                                            "can be missing here")
    unused_p = ok(unused, [], [], confidence="python getattr dispatch and decorator-based "
                                             "registration are not statically traced")
    not_found = {"status": "not_found", "summary": "No node found matching 'nosuch'.",
                 "confidence": "target not indexed: no node matching 'nosuch', so this 0 is "
                               "not evidence that none exist"}
    return {"leaf": leaf_p, leaf: leaf_p, mid: mid_p, top: top_p, entry: entry_p,
            "unused": unused_p, "nosuch": not_found}


class _Recorder:
    def __init__(self, root):
        self.payloads = _recorded(root)
        self.calls = []

    def __call__(self, tool, params):
        self.calls.append((tool, dict(params)))
        if tool != "query_graph_tool":
            raise AssertionError(f"blast radius must not call {tool}")
        assert params["pattern"] == "callers_of", params
        return self.payloads.get(params["target"],
                                 {"status": "not_found", "summary": "no node"})


class _Case(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="o2-crg-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.rec = _Recorder(self.root)
        self.client = CodeReviewGraphClient(root=self.root, call_tool=self.rec,
                                            run_cli=lambda a: (0, "code-review-graph 2.3.9", ""))


class TestTheMapping(_Case):
    def test_walks_callers_transitively_and_only_callers(self):
        rows = self.client.query("blast_radius", "leaf", root=self.root, depth=3)
        got = [(r["path"], r["line"], r["symbol"]) for r in rows]
        self.assertEqual(got, [("pkg/core.py", 6, "pkg/core.py::mid"),
                               ("pkg/app.py", 5, "pkg/app.py::top"),
                               ("pkg/app.py", 9, "pkg/app.py::entry")])
        # `unused` shares leaf's FILE — the undirected traverse reached it via CONTAINS.
        self.assertNotIn("unused", " ".join(r["symbol"] for r in rows))
        self.assertEqual({t for t, _ in self.rec.calls}, {"query_graph_tool"})

    def test_rows_are_rich_and_relative(self):
        rows = self.client.query("blast_radius", "leaf", root=self.root, depth=3)
        self.assertEqual([r["metadata"]["hop"] for r in rows], [1, 2, 3])
        self.assertTrue(all(r["edge_type"] == "CALLS" for r in rows))
        self.assertFalse(any(os.path.isabs(r["path"]) for r in rows))
        # The tool's own id is kept verbatim — it is what the next hop sends back.
        self.assertEqual(self.rec.calls[1][1]["target"],
                         os.path.join(self.root, "pkg", "core.py") + "::mid")

    def test_depth_bounds_the_walk(self):
        rows = self.client.query("blast_radius", "leaf", root=self.root, depth=1)
        self.assertEqual([r["symbol"] for r in rows], ["pkg/core.py::mid"])
        self.assertEqual(len(self.rec.calls), 1)

    def test_an_indexed_leaf_is_a_real_zero(self):
        rows = self.client.query("blast_radius", "unused", root=self.root, depth=2)
        self.assertEqual(list(rows), [])
        self.assertFalse(rows.truncated)

    def test_an_unindexed_target_is_not_a_zero(self):
        with self.assertRaises(GraphNotIndexed) as cm:
            self.client.query("blast_radius", "nosuch", root=self.root, depth=2)
        self.assertIn("not indexed", str(cm.exception))

    def test_an_ambiguous_target_is_not_a_zero(self):
        self.rec.payloads["handle"] = {"status": "ambiguous", "summary": "'handle' matches 3",
                                       "candidates": []}
        with self.assertRaises(GraphNotIndexed):
            self.client.query("blast_radius", "handle", root=self.root, depth=2)

    def test_the_expansion_cap_is_disclosed(self):
        with mock.patch.object(crg_client, "CRG_BLAST_MAX_EXPANSIONS", 2):
            rows = self.client.query("blast_radius", "leaf", root=self.root, depth=3)
        self.assertTrue(rows.truncated)
        self.assertEqual(len(rows), 2)
        self.assertIn("2-symbol expansion cap", rows.cut_why)
        backend = CodeReviewGraphBackend("code-review-graph", self.root, client=self.client)
        with mock.patch.object(crg_client, "CRG_BLAST_MAX_EXPANSIONS", 2):
            res = backend.query("blast_radius", "leaf", depth=3)
        self.assertTrue(res.truncated)
        self.assertIn("expansion cap", res.note)

    def test_normalize_reads_the_real_edge_keys_and_call_site(self):
        """The real edges carry `source`/`target`, and a call-site row's line is the CALL."""
        rows = CodeReviewGraphClient._normalize(
            self.rec.payloads["leaf"], self.root)
        self.assertEqual(rows[0]["edge_type"], "CALLS")
        self.assertEqual(rows[0]["line"], 6)            # call site, not `def mid` on line 5


class _Floor(GraphBackend):
    name = "ast"

    def __init__(self):
        self.asked = []

    def query(self, kind, target, depth=1):
        self.asked.append((kind, target, depth))
        return QueryResult(kind=kind, target=target, backend="ast", basis=BASIS_STRUCTURAL,
                           references=[Reference("pkg/x.py", 3, "", "x")], note="ast floor")


class TestTheLayerRoutes(_Case):
    def _layer(self, client=None):
        self.floor = _Floor()
        primary = CodeReviewGraphBackend("code-review-graph", self.root,
                                         client=client or self.client)
        primary.recover = mock.Mock(side_effect=AssertionError("no recovery for a healthy graph"))
        return KnowledgeLayer(primary, self.floor)

    def test_crg_answers_when_active(self):
        res = self._layer().blast_radius("leaf", depth=3)
        self.assertEqual(res.backend, "code-review-graph")
        self.assertFalse(res.degraded)
        self.assertEqual(res.count, 3)
        self.assertEqual(self.floor.asked, [], "the floor must not answer what CRG can")

    def test_an_indexed_zero_stays_crg_and_structural(self):
        res = self._layer().blast_radius("unused", depth=2)
        self.assertEqual((res.backend, res.count, res.degraded), ("code-review-graph", 0, False))
        self.assertEqual(self.floor.asked, [])

    def test_unindexed_falls_to_the_floor_without_an_alarm(self):
        with mock.patch("mokata.degrade.note_degraded") as alarm:
            res = self._layer().blast_radius("nosuch", depth=2)
        self.assertEqual(res.backend, "ast")
        self.assertEqual(self.floor.asked, [("blast_radius", "nosuch", 2)])
        self.assertIn("does not index `nosuch`", res.note)
        self.assertIn("answered by the 'ast' floor", res.note)
        alarm.assert_not_called()

    def test_a_failing_graph_falls_to_the_floor_loudly(self):
        def boom(_tool, _params):
            raise CrgUnavailable("serve died")
        layer = self._layer(CodeReviewGraphClient(root=self.root, call_tool=boom,
                                                  run_cli=lambda a: (1, "", "")))
        layer.primary.recover = lambda: False
        with mock.patch("mokata.degrade.note_degraded") as alarm:
            res = layer.blast_radius("leaf", depth=2)
        self.assertEqual(res.backend, "ast")
        self.assertTrue(res.degraded, "a floor standing in for a failed graph is demoted")
        alarm.assert_called_once()


class TestDisclosure(_Case):
    def test_says_crg_answers_with_the_floor_beneath(self):
        layer = KnowledgeLayer(CodeReviewGraphBackend("code-review-graph", self.root,
                                                      client=self.client), _Floor())
        line = impact_backend_line(layer)
        self.assertIn("answered by 'code-review-graph'", line)
        self.assertIn("'ast' floor", line)

    def test_says_the_floor_answers_when_crg_cannot(self):
        class _NoBlast:
            def supports_kind(self, kind):
                return kind != "blast_radius"
        layer = KnowledgeLayer(CodeReviewGraphBackend("code-review-graph", self.root,
                                                      client=_NoBlast()), _Floor())
        self.assertIn("answered by the 'ast' floor", impact_backend_line(layer))

    def test_ide_health_row_names_the_impact_backend(self):
        from mokata.ide import health
        layer = KnowledgeLayer(CodeReviewGraphBackend("code-review-graph", self.root,
                                                      client=self.client), _Floor())
        with mock.patch("mokata.ide.nav.graph_health", return_value=None):
            rows = health._graph_rows(self.root, layer)
        graph = [r for r in rows if r.get("id") == "graph" or "code graph" in str(r)][0]
        self.assertIn("blast radius: answered by 'code-review-graph'", str(graph))


if __name__ == "__main__":
    unittest.main()
