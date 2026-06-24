"""B6 — per-story analysis -> persistent bridge: a story's structural analysis is
persisted (via the existing state surface) and retrievable later, so the brain is
enriched instead of recomputed."""

import tempfile
import unittest

from _support import write_sample_repo

from mokata.config import Surface
from mokata.detect import Detector
from mokata.init import init_repo
from mokata.knowledge import (
    KnowledgeLayer,
    build_story_analysis,
    load_story_analysis,
    persist_story_analysis,
)
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def silent(_):
    pass


class TestStoryBridge(unittest.TestCase):
    def test_history_records_queries(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            router = Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                            Detector(overrides={"code-review-graph": False,
                                                "serena": False, "ripgrep": False}))
            layer = KnowledgeLayer.from_router(router, root=d)
            layer.callers("compute")
            layer.implementers("Base")
            self.assertEqual([q.kind for q in layer.history],
                             ["callers", "implementers"])

    def test_persist_and_reload_through_surface(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            surface = Surface.load(d)
            layer = KnowledgeLayer.from_surface(surface)
            layer.callers("compute")
            layer.imports("mod_a")

            analysis = build_story_analysis("STORY-1", "add caching", layer)
            persist_story_analysis(surface.state, analysis)

            reloaded = load_story_analysis(Surface.load(d).state, "STORY-1")
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.story_id, "STORY-1")
            self.assertEqual(reloaded.summary, "add caching")
            self.assertIn("compute", reloaded.symbols)
            self.assertIn("mod_a", reloaded.symbols)
            self.assertEqual({q["kind"] for q in reloaded.queries},
                             {"callers", "imports"})

    def test_missing_story_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            surface = Surface.load(d)
            self.assertIsNone(load_story_analysis(surface.state, "NOPE"))


if __name__ == "__main__":
    unittest.main()
