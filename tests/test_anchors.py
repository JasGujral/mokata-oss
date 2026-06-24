"""B5 — concept-graph / drift anchors: optional `@lat:` anchors + a `lat check` that
flags drift, degrading cleanly when absent."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.knowledge import lat_check, load_concepts, scan_anchors


def write_anchor_repo(d):
    with open(os.path.join(d, "cache.py"), "w", encoding="utf-8") as fh:
        fh.write("# @lat: caching\ndef get(k):\n    return k\n")
    with open(os.path.join(d, "ghost.py"), "w", encoding="utf-8") as fh:
        fh.write("# @lat: ghost\nX = 1\n")
    with open(os.path.join(d, "lat.md"), "w", encoding="utf-8") as fh:
        fh.write("# concepts\n- caching\n- auth\n")


class TestAnchorScan(unittest.TestCase):
    def test_scans_anchors_with_concept_and_line(self):
        with tempfile.TemporaryDirectory() as d:
            write_anchor_repo(d)
            anchors = scan_anchors(d)
            concepts = {a.concept for a in anchors}
            self.assertEqual(concepts, {"caching", "ghost"})
            self.assertTrue(all(a.line > 0 for a in anchors))

    def test_load_concepts_from_lat_md(self):
        with tempfile.TemporaryDirectory() as d:
            write_anchor_repo(d)
            self.assertEqual(set(load_concepts(d)), {"caching", "auth"})


class TestLatCheck(unittest.TestCase):
    def test_flags_drift(self):
        with tempfile.TemporaryDirectory() as d:
            write_anchor_repo(d)
            report = lat_check(d)
            self.assertTrue(report.available)
            self.assertTrue(report.has_drift)
            kinds = {(f.kind, f.concept) for f in report.drift}
            self.assertIn(("orphan-anchor", "ghost"), kinds)        # anchored, unknown
            self.assertIn(("unanchored-concept", "auth"), kinds)    # known, unanchored

    def test_clean_when_consistent(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
                fh.write("# @lat: caching\nx = 1\n")
            with open(os.path.join(d, "lat.md"), "w", encoding="utf-8") as fh:
                fh.write("- caching\n")
            report = lat_check(d)
            self.assertTrue(report.available)
            self.assertTrue(report.clean)

    def test_degrades_cleanly_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "plain.py"), "w", encoding="utf-8") as fh:
                fh.write("def f():\n    return 1\n")
            report = lat_check(d)            # no anchors, no lat.md
            self.assertFalse(report.available)
            self.assertTrue(report.clean)
            self.assertEqual(report.drift, [])


if __name__ == "__main__":
    unittest.main()
