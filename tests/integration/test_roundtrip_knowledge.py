"""Stage 20 — knowledge round-trip: graph-present vs grep-fallback, same typed shape.

The knowledge layer selects its backend THROUGH the capability router. Whether a real
graph answers or the grep floor does, the caller gets the identical `QueryResult` shape —
and when the graph backend fails mid-query, the layer degrades to grep without error.
Driven end-to-end through a real manifest + router + a sample repo.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import dataclasses
import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import KnowledgeLayer, QueryResult
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def _full_router(overrides):
    manifest = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
    return Router(manifest, Detector(overrides=overrides))


class _FakeGraphClient:
    def __init__(self, rows):
        self.rows = rows

    def query(self, kind, target, root, depth=1):
        return list(self.rows.get(kind, []))


class _FailingGraphClient:
    def query(self, kind, target, root, depth=1):
        raise RuntimeError("graph down")


class TestGraphVsGrepSameTypedShape(unittest.TestCase):
    def test_both_backends_answer_with_identical_shape(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)

            grep_layer = KnowledgeLayer.from_router(
                _full_router({"code-review-graph": False, "serena": False,
                              "ripgrep": False}), root=d)
            graph_layer = KnowledgeLayer.from_router(
                _full_router({"code-review-graph": True}), root=d,
                client=_FakeGraphClient({"callers": [
                    {"path": "mod_a.py", "line": 14, "snippet": "compute()",
                     "symbol": "run"}]}))

            self.assertFalse(grep_layer.uses_graph)
            self.assertEqual(grep_layer.backend_name, "grep")
            self.assertTrue(graph_layer.uses_graph)
            self.assertEqual(graph_layer.backend_name, "code-review-graph")

            grep_result = grep_layer.callers("compute")
            graph_result = graph_layer.callers("compute")

            # identical typed shape regardless of which backend answered
            self.assertIsInstance(grep_result, QueryResult)
            self.assertIsInstance(graph_result, QueryResult)
            self.assertEqual(
                {f.name for f in dataclasses.fields(grep_result)},
                {f.name for f in dataclasses.fields(graph_result)})
            self.assertIsInstance(grep_result.count, int)
            self.assertIsInstance(graph_result.count, int)

            # only the floor flags degraded
            self.assertTrue(grep_result.degraded)
            self.assertFalse(graph_result.degraded)

    def test_graph_failure_degrades_to_grep_floor(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = KnowledgeLayer.from_router(
                _full_router({"code-review-graph": True}), root=d,
                client=_FailingGraphClient())

            result = layer.callers("compute")          # primary blows up mid-query
            self.assertIsInstance(result, QueryResult)
            self.assertTrue(result.degraded)
            self.assertIn("fell back", result.note)
            # the floor still returned real references from the sample repo
            self.assertEqual({ref.path for ref in result.references},
                             {"mod_a.py", "mod_b.py"})


if __name__ == "__main__":
    unittest.main()
