"""B4 — incremental re-index + staleness detection: per-file content-hash invalidation,
re-index only what changed, and surface staleness instead of serving it silently."""

import os
import tempfile
import time
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import KnowledgeIndex, KnowledgeLayer
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def grep_layer(root, index=None):
    router = Router(Manifest.from_dict(build_manifest_data("full", "0.1.0")),
                    Detector(overrides={"code-review-graph": False, "serena": False,
                                        "ripgrep": False}))
    return KnowledgeLayer.from_router(router, root=root, index=index)


def touch_change(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestIncrementalIndex(unittest.TestCase):
    def test_build_indexes_all_source_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            idx = KnowledgeIndex()
            indexed = idx.build(d)
            self.assertEqual(set(indexed), {"mod_a.py", "mod_b.py"})

    def test_change_invalidates_only_that_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            idx = KnowledgeIndex()
            idx.build(d)
            before_b = idx.entries["mod_b.py"].content_hash

            touch_change(os.path.join(d, "mod_a.py"), "def changed():\n    return 2\n")
            diff = idx.diff(d)
            self.assertEqual(set(diff["changed"]), {"mod_a.py"})
            self.assertEqual(diff["added"], [])
            self.assertEqual(diff["removed"], [])

            reindexed = idx.reindex(d)            # incremental
            self.assertEqual(set(reindexed), {"mod_a.py"})
            # mod_b's entry is untouched (same fingerprint)
            self.assertEqual(idx.entries["mod_b.py"].content_hash, before_b)

    def test_is_stale_tracks_disk_changes(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            idx = KnowledgeIndex()
            idx.build(d)
            self.assertFalse(idx.is_stale(d, "mod_a.py"))
            touch_change(os.path.join(d, "mod_a.py"), "def changed():\n    return 9\n")
            self.assertTrue(idx.is_stale(d, "mod_a.py"))
            self.assertFalse(idx.is_stale(d, "mod_b.py"))
            self.assertEqual(idx.stale_files(d), ["mod_a.py"])

    def test_roundtrips_through_dict(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            idx = KnowledgeIndex()
            idx.build(d)
            again = KnowledgeIndex.from_dict(idx.to_dict())
            self.assertEqual(set(again.entries), set(idx.entries))


class TestStalenessSurfacing(unittest.TestCase):
    def test_query_surfaces_staleness_instead_of_serving_silently(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            idx = KnowledgeIndex()
            idx.build(d)
            # the file backing the query result is edited after indexing, but still
            # contains a call site so the query references it
            touch_change(os.path.join(d, "mod_a.py"),
                         "def compute():\n    return 1\n\n\n"
                         "def again():\n    return compute()\n")
            layer = grep_layer(d, index=idx)
            result = layer.callers("compute")
            self.assertIn("STALE", result.note)
            self.assertIn("mod_a.py", result.note)

    def test_no_index_means_no_stale_annotation(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = grep_layer(d)                 # no index attached
            result = layer.callers("compute")
            self.assertNotIn("STALE", result.note)


if __name__ == "__main__":
    unittest.main()
