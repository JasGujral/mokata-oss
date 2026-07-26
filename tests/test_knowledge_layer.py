"""B3 — retrieval-instead-of-grep policy: the layer selects its backend THROUGH the
existing capability router (no second detection path), prefers the graph when present,
degrades to grep when absent, and returns the same typed shape either way."""

import dataclasses
import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import KnowledgeLayer, QueryResult
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def full_router(overrides):
    m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
    return Router(m, Detector(overrides=overrides))


class FakeGraphClient:
    def __init__(self, rows):
        self.rows = rows

    def query(self, kind, target, root, depth=1):
        return list(self.rows.get(kind, []))


class FailingGraphClient:
    def query(self, kind, target, root, depth=1):
        raise RuntimeError("graph down")


class TestBackendSelectionViaRouter(unittest.TestCase):
    def test_prefers_graph_when_present(self):
        router = full_router({"code-review-graph": True})
        layer = KnowledgeLayer.from_router(
            router, root=".", client=FakeGraphClient({"callers": []}))
        self.assertTrue(layer.uses_graph)
        self.assertEqual(layer.backend_name, "code-review-graph")

    def test_python_repo_uses_ast_floor_when_graph_absent(self):
        # GR.S1: graph absent + Python present -> the embedded AST floor (a floor, NOT the
        # adopted graph: uses_graph stays False), preferred over grep.
        router = full_router({"code-review-graph": False, "serena": False,
                              "ripgrep": False})
        layer = KnowledgeLayer.from_router(router, root=".")
        self.assertFalse(layer.uses_graph)
        self.assertEqual(layer.backend_name, "ast")

    def test_non_python_repo_stays_on_grep_floor_when_graph_absent(self):
        # GR.S1: a repo with zero .py files behaves exactly as before -> the grep floor.
        with tempfile.TemporaryDirectory() as d:
            with open(f"{d}/app.js", "w", encoding="utf-8") as fh:
                fh.write("function main(){ return compute(); }\n")
            router = full_router({"code-review-graph": False, "serena": False,
                                  "ripgrep": False})
            layer = KnowledgeLayer.from_router(router, root=d)
            self.assertFalse(layer.uses_graph)
            self.assertEqual(layer.backend_name, "grep")

    def test_query_works_end_to_end_on_ast_floor(self):
        # GR.S1: the SAME query that used to be the degraded grep floor on a Python repo is now
        # answered by the AST floor with degraded=False (the headline honesty change, P22).
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            router = full_router({"code-review-graph": False, "serena": False,
                                  "ripgrep": False})
            layer = KnowledgeLayer.from_router(router, root=d)
            r = layer.callers("compute")
            self.assertEqual({ref.path for ref in r.references},
                             {"mod_a.py", "mod_b.py"})
            self.assertFalse(r.degraded)


class TestSameTypedShapeRegardlessOfBackend(unittest.TestCase):
    def test_floor_and_graph_return_identical_shape(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            floor_layer = KnowledgeLayer.from_router(
                full_router({"code-review-graph": False, "serena": False,
                             "ripgrep": False}), root=d)
            graph_layer = KnowledgeLayer.from_router(
                full_router({"code-review-graph": True}), root=d,
                client=FakeGraphClient({"callers": [
                    {"path": "mod_a.py", "line": 14, "snippet": "compute()",
                     "symbol": "run"}]}))

            g1 = floor_layer.callers("compute")
            g2 = graph_layer.callers("compute")
            self.assertIsInstance(g1, QueryResult)
            self.assertIsInstance(g2, QueryResult)
            self.assertEqual(
                {f.name for f in dataclasses.fields(g1)},
                {f.name for f in dataclasses.fields(g2)},
            )
            # GR.S1: both authoritative — the AST floor answers non-degraded, like the graph
            self.assertFalse(g2.degraded)
            self.assertFalse(g1.degraded)
            self.assertEqual(floor_layer.backend_name, "ast")


class TestGracefulFallbackOnGraphFailure(unittest.TestCase):
    def test_graph_failure_falls_back_to_grep_without_error(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = KnowledgeLayer.from_router(
                full_router({"code-review-graph": True}), root=d,
                client=FailingGraphClient())
            r = layer.callers("compute")          # primary blows up
            self.assertEqual({ref.path for ref in r.references},
                             {"mod_a.py", "mod_b.py"})
            self.assertTrue(r.degraded)
            self.assertIn("fell back", r.note)


if __name__ == "__main__":
    unittest.main()
