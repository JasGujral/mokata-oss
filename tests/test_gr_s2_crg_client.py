"""GR.S2 (j)+(l) — the REAL code-review-graph client + full-capacity backend, on a fake double.

GROUNDING DIVERGENCE (recorded): the 0.0.13 `SubprocessGraphClient` assumed
`<tool> query <kind> <target> --json`. The REAL code-review-graph (v2.3.x) has NO `query`
subcommand — the command is `code-review-graph` (not `crg`), symbol queries are MCP-only
(`code-review-graph serve` + tools like `query_graph_tool(pattern=..., target=...)`,
`get_impact_radius_tool`, `traverse_graph_tool`, `semantic_search_nodes_tool`), the ONE CLI
JSON path is `detect-changes`, refresh is `update`/`build`, and version is `--version`. The
REAL interface wins: this stage maps mokata's typed queries onto it and consumes its rich
result fields (edge kinds, qualified names, line ranges, is_test).

The contract suite runs on a FAKE double covering the full interface (all query kinds, rich
fields, semantic query, refresh/rebuild, version report). The real transport (MCP stdio to
`code-review-graph serve`) is exercised only by the opt-in real-CRG CI leg; its kind->CRG
MAPPING is unit-tested here via injected transports, so the grounded mapping is verified
without a live server.
"""

import unittest

from mokata.knowledge import (
    CodeReviewGraphBackend,
    KnowledgeLayer,
    QueryResult,
    Reference,
)
from mokata.knowledge.crg_client import CodeReviewGraphClient
from mokata.knowledge.query import QUERY_KINDS


class FakeCrgClient:
    """A full-interface code-review-graph double: query kinds, rich rows, semantic,
    refresh/rebuild, version. Injected everywhere the real client would be."""

    supports_semantic = True

    def __init__(self, rows=None, semantic_rows=None, version="2.3.6"):
        self.rows = rows or {}
        self.semantic_rows = semantic_rows or []
        self._version = version
        self.calls = []
        self.refreshes = []
        self.alive = True

    def query(self, kind, target, root, depth=1):
        self.calls.append(("query", kind, target, depth))
        if not self.alive:
            raise RuntimeError("code-review-graph serve is not running")
        return list(self.rows.get(kind, []))

    def semantic(self, query, root, kind=None, limit=20):
        self.calls.append(("semantic", query, kind, limit))
        if not self.alive:
            raise RuntimeError("code-review-graph serve is not running")
        return list(self.semantic_rows)

    def refresh(self, root, full=False):
        self.refreshes.append(full)
        self.alive = True          # a rebuild brings a dead index back
        return True

    def version(self, root=None):
        return self._version

    def health(self, root):
        return self.alive


def _rich_rows():
    # The normalized rows a client yields from CRG's query_graph_tool results+edges.
    return {
        "callers": [
            {"path": "svc/api.py", "line": 40, "symbol": "svc.api.handle",
             "edge_type": "CALLS",
             "metadata": {"qualified_name": "svc.api.handle", "kind": "Function",
                          "is_test": False}},
            {"path": "tests/test_api.py", "line": 8, "symbol": "tests.test_api.test_handle",
             "edge_type": "TESTED_BY",
             "metadata": {"qualified_name": "tests.test_api.test_handle",
                          "kind": "Function", "is_test": True}},
        ],
        "callees": [{"path": "svc/db.py", "line": 5, "symbol": "svc.db.fetch"}],
        "imports": [{"path": "svc/api.py", "line": 1, "symbol": "svc.db"}],
        "implementers": [{"path": "svc/impl.py", "line": 3, "symbol": "svc.impl.Impl"}],
        "blast_radius": [{"path": "svc/caller.py", "line": 9, "symbol": "svc.caller.run"}],
    }


class TestBackendRichFields(unittest.TestCase):
    """The backend consumes the richer result fields CRG offers (edge type, symbol metadata)."""

    def test_edge_type_and_metadata_reach_the_reference(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=FakeCrgClient(rows=_rich_rows()))
        r = backend.query("callers", "svc.api.handle")
        self.assertFalse(r.degraded)
        self.assertEqual(r.count, 2)
        self.assertEqual(r.references[0].edge_type, "CALLS")
        self.assertEqual(r.references[0].metadata["kind"], "Function")
        self.assertTrue(r.references[1].metadata["is_test"])

    def test_all_query_kinds_answer(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".",
                                         client=FakeCrgClient(rows=_rich_rows()))
        for kind in QUERY_KINDS:
            r = backend.query(kind, "svc.api.handle")
            self.assertIsInstance(r, QueryResult)
            self.assertFalse(r.degraded, f"{kind} should be a real graph answer")
            self.assertTrue(r.references, f"{kind} should return references")


class TestReferenceAdditiveBackCompat(unittest.TestCase):
    """Reference's new fields are additive — an old row dict (no edge_type/metadata) parses."""

    def test_old_row_still_parses(self):
        ref = Reference.from_dict({"path": "a.py", "line": 3, "symbol": "x"})
        self.assertIsNone(ref.edge_type)
        self.assertEqual(ref.metadata, {})
        # round-trips
        self.assertEqual(Reference.from_dict(ref.to_dict()).path, "a.py")


class TestSemantic(unittest.TestCase):
    """Semantic search is the ADOPTED tool's capability, exposed through the typed API."""

    def test_backend_semantic_returns_typed_result(self):
        client = FakeCrgClient(semantic_rows=[
            {"path": "svc/api.py", "line": 40, "symbol": "svc.api.handle"}])
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".", client=client)
        self.assertTrue(backend.supports_semantic)
        r = backend.semantic("where do we handle requests?")
        self.assertEqual(r.kind, "semantic")
        self.assertFalse(r.degraded)
        self.assertEqual(r.references[0].symbol, "svc.api.handle")
        self.assertEqual(client.calls[-1][0], "semantic")

    def test_layer_semantic_capability_gated_on_floor(self):
        # No real graph -> semantic is unavailable; the layer degrades honestly (empty, degraded).
        from mokata.knowledge import GrepBackend
        layer = KnowledgeLayer(primary=GrepBackend(root="."))
        r = layer.semantic("anything")
        self.assertTrue(r.degraded)
        self.assertEqual(r.count, 0)

    def test_layer_semantic_uses_graph_when_present(self):
        client = FakeCrgClient(semantic_rows=[{"path": "a.py", "line": 1, "symbol": "a.b"}])
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".", client=client)
        layer = KnowledgeLayer(primary=backend, fallback=None)
        r = layer.semantic("q")
        self.assertFalse(r.degraded)
        self.assertEqual(r.references[0].symbol, "a.b")


class TestRealClientGroundedMapping(unittest.TestCase):
    """The REAL client maps mokata's typed queries onto CRG's ACTUAL surface — verified via
    injected transports (no live server needed). This is the grounded interface, not the
    0.0.13 assumption."""

    def _client(self):
        self.tool_calls = []
        self.cli_calls = []

        def call_tool(tool, params):
            self.tool_calls.append((tool, params))
            if tool == "query_graph_tool":
                return {"status": "ok", "results": [
                    {"name": "handle", "qualified_name": "svc.api.handle",
                     "kind": "Function", "file_path": "svc/api.py",
                     "line_start": 40, "line_end": 45, "is_test": False}],
                    "edges": [{"source_qualified": "svc.api.handle",
                               "target_qualified": params["target"], "kind": "CALLS",
                               "file_path": "svc/api.py"}]}
            if tool == "traverse_graph_tool":
                return {"status": "ok", "results": [
                    {"name": "run", "qualified_name": "svc.caller.run", "kind": "Function",
                     "file_path": "svc/caller.py", "line_start": 9, "line_end": 12}]}
            if tool == "semantic_search_nodes_tool":
                return {"status": "ok", "results": [
                    {"name": "handle", "qualified_name": "svc.api.handle", "kind": "Function",
                     "file_path": "svc/api.py", "line_start": 40, "line_end": 45}]}
            return {"status": "ok", "results": []}

        def run_cli(args):
            self.cli_calls.append(list(args))
            if args and args[0] == "--version":
                return (0, "code-review-graph 2.3.6\n", "")
            return (0, "", "")

        return CodeReviewGraphClient(name="code-review-graph", root=".",
                                     call_tool=call_tool, run_cli=run_cli)

    def test_callers_maps_to_callers_of_pattern(self):
        c = self._client()
        rows = c.query("callers", "svc.api.handle", root=".")
        tool, params = self.tool_calls[-1]
        self.assertEqual(tool, "query_graph_tool")
        self.assertEqual(params["pattern"], "callers_of")
        self.assertEqual(params["target"], "svc.api.handle")
        # normalized rich row
        self.assertEqual(rows[0]["path"], "svc/api.py")
        self.assertEqual(rows[0]["line"], 40)
        self.assertEqual(rows[0]["symbol"], "svc.api.handle")
        self.assertEqual(rows[0]["edge_type"], "CALLS")
        self.assertTrue(rows[0]["metadata"]["qualified_name"])

    def test_implementers_maps_to_inheritors_of(self):
        c = self._client()
        c.query("implementers", "Base", root=".")
        self.assertEqual(self.tool_calls[-1][1]["pattern"], "inheritors_of")

    def test_imports_maps_to_imports_of(self):
        c = self._client()
        c.query("imports", "svc.db", root=".")
        self.assertEqual(self.tool_calls[-1][1]["pattern"], "imports_of")

    def test_blast_radius_uses_traverse(self):
        c = self._client()
        rows = c.query("blast_radius", "svc.api.handle", root=".", depth=2)
        self.assertEqual(self.tool_calls[-1][0], "traverse_graph_tool")
        self.assertEqual(self.tool_calls[-1][1]["depth"], 2)
        self.assertEqual(rows[0]["symbol"], "svc.caller.run")

    def test_semantic_maps_to_semantic_search(self):
        c = self._client()
        rows = c.semantic("handle requests", root=".", limit=5)
        self.assertEqual(self.tool_calls[-1][0], "semantic_search_nodes_tool")
        self.assertEqual(self.tool_calls[-1][1]["limit"], 5)
        self.assertEqual(rows[0]["symbol"], "svc.api.handle")

    def test_version_parses_the_cli_string(self):
        c = self._client()
        self.assertEqual(c.version(root="."), "2.3.6")
        self.assertEqual(self.cli_calls[-1], ["--version"])

    def test_refresh_incremental_calls_update(self):
        c = self._client()
        c.refresh(root=".", full=False)
        self.assertIn(["update"], self.cli_calls)

    def test_refresh_full_calls_build(self):
        c = self._client()
        c.refresh(root=".", full=True)
        self.assertIn(["build"], self.cli_calls)


if __name__ == "__main__":
    unittest.main()
