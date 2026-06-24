"""F1/F2 — token/cost tracker and JIT graph-backed retrieval (retrieve by identifier,
not file dumps; show the context reduction)."""

import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.govern import TokenTracker, jit_retrieve
from mokata.knowledge import KnowledgeLayer
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def grep_layer(root):
    router = Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                    Detector(overrides={"code-review-graph": False, "serena": False,
                                        "ripgrep": False}))
    return KnowledgeLayer.from_router(router, root=root)


class TestTokenTracker(unittest.TestCase):
    def test_tracks_tokens_and_cost(self):
        t = TokenTracker()
        t.add("call-1", input_text="a" * 40, output_text="b" * 80)
        self.assertGreater(t.total_input, 0)
        self.assertGreater(t.total_output, 0)
        self.assertGreater(t.cost(), 0.0)
        self.assertIn("token", t.report().lower())

    def test_explicit_token_counts_accepted(self):
        t = TokenTracker()
        t.add("c", input_tokens=100, output_tokens=50)
        self.assertEqual(t.total_input, 100)
        self.assertEqual(t.total_output, 50)


class TestJitRetrieval(unittest.TestCase):
    def test_retrieval_reduces_context_vs_dumping_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = grep_layer(d)
            result = jit_retrieve(layer, ["compute"])
            self.assertGreater(result.tokens_if_dumped, 0)
            self.assertLess(result.tokens_retrieved, result.tokens_if_dumped)
            self.assertGreater(result.saved, 0)
            self.assertGreater(result.saved_pct, 0)
            self.assertTrue(result.snippets)


if __name__ == "__main__":
    unittest.main()
