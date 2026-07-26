"""GR.S2 (j) — the richer client + semantic reach the consumers/surfaces.

Every `uses_graph` consumer already speaks the typed `QueryResult` shape, so the richer client
flows through them transparently (edge_type/metadata are additive on `Reference`). This file
pins the NEW reach:
  * a single query router that both the MCP tool and the CLI use, so `kind="semantic"` is
    exposed through the typed query API (not just callers/callees/...).
  * the adopt flow PROVISIONS CRG's own bundled semantic index in the same consented flow.
  * the richer result fields survive `.to_dict()` (what the MCP `query` tool returns).
"""

import json
import os
import tempfile
import unittest

from mokata.knowledge import (
    CodeReviewGraphBackend,
    GrepBackend,
    KnowledgeLayer,
    Reference,
    graph_adopt,
)
from mokata.knowledge.layer import run_query
from mokata.profiles import build_manifest_data


class FakeSemBackend:
    is_graph = True
    name = "code-review-graph"
    supports_semantic = True

    def query(self, kind, target, depth=1):
        from mokata.knowledge import QueryResult
        return QueryResult(kind=kind, target=target,
                           references=[Reference("a.py", 1, symbol="a.b")], backend=self.name)

    def semantic(self, query, kind=None, limit=20):
        from mokata.knowledge import QueryResult
        return QueryResult(kind="semantic", target=query,
                           references=[Reference("svc.py", 9, symbol="svc.handle")],
                           backend=self.name)


class TestQueryRouterSurfacesSemantic(unittest.TestCase):
    """A single router routes `semantic` to the typed semantic API, everything else to `_run`."""

    def test_semantic_kind_routes_to_semantic(self):
        layer = KnowledgeLayer(primary=FakeSemBackend())
        r = run_query(layer, "semantic", "where do we handle requests?")
        self.assertEqual(r.kind, "semantic")
        self.assertEqual(r.references[0].symbol, "svc.handle")

    def test_structural_kind_routes_to_run(self):
        layer = KnowledgeLayer(primary=FakeSemBackend())
        r = run_query(layer, "callers", "a.b")
        self.assertEqual(r.kind, "callers")

    def test_semantic_on_floor_degrades_not_crashes(self):
        layer = KnowledgeLayer(primary=GrepBackend(root="."))
        r = run_query(layer, "semantic", "anything")
        self.assertTrue(r.degraded)
        self.assertEqual(r.count, 0)


class TestRichFieldsSurviveToDict(unittest.TestCase):
    """The MCP `query` tool returns `.to_dict()` — the richer fields must survive it."""

    def test_edge_type_and_metadata_in_to_dict(self):
        class RichClient:
            def query(self, kind, target, root, depth=1):
                return [{"path": "a.py", "line": 3, "symbol": "a.b", "edge_type": "CALLS",
                         "metadata": {"kind": "Function", "is_test": False}}]
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".", client=RichClient())
        d = backend.query("callers", "a.b").to_dict()
        self.assertEqual(d["references"][0]["edge_type"], "CALLS")
        self.assertEqual(d["references"][0]["metadata"]["kind"], "Function")


class TestAdoptProvisionsSemantic(unittest.TestCase):
    """(j) adopt enables + provisions CRG's OWN bundled semantic index in the consented flow."""

    def test_adopt_provisions_semantic_index(self):
        with tempfile.TemporaryDirectory() as d:
            mdir = os.path.join(d, ".mokata")
            os.makedirs(mdir)
            with open(os.path.join(mdir, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump(build_manifest_data("standard", "0.1.0"), fh)

            class ProvClient:
                provisions = []

                def version(self, root=None):
                    return "2.3.6"

                def provision_semantic(self, root):
                    ProvClient.provisions.append(root)
                    return True
            res = graph_adopt.adopt_graph(d, tool="code-review-graph", assume_yes=True,
                                          client=ProvClient())
            self.assertTrue(res.committed)
            self.assertEqual(len(ProvClient.provisions), 1)


class TestRealClientProvisionSemantic(unittest.TestCase):
    """The real client provisions via CRG's local embedding (`embed --provider local`)."""

    def test_provision_runs_embed_local(self):
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        cli_calls = []

        def run_cli(args):
            cli_calls.append(list(args))
            return (0, "", "")
        c = CodeReviewGraphClient(name="code-review-graph", root=".", run_cli=run_cli)
        self.assertTrue(c.provision_semantic("."))
        self.assertIn(["embed", "--provider", "local"], cli_calls)


if __name__ == "__main__":
    unittest.main()
