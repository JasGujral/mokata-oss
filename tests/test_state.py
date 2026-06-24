"""Governed pipeline-state store (JSON under .mokata/state) — the persistence
surface the brainstorm phase writes its approved approach through."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.state import StateStore


class TestStateStore(unittest.TestCase):
    def test_write_then_read_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            store.write("thing", {"a": 1, "b": ["x", "y"]})
            self.assertEqual(store.read("thing"), {"a": 1, "b": ["x", "y"]})

    def test_read_absent_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            self.assertIsNone(store.read("missing"))
            self.assertFalse(store.exists("missing"))

    def test_write_creates_directory_and_reports_path(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            path = store.write("k", {"ok": True})
            self.assertTrue(os.path.exists(path))
            self.assertTrue(store.exists("k"))

    def test_written_file_is_human_readable_json(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            path = store.write("k", {"ok": True})
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("\n", text)            # indented, reviewable
            self.assertTrue(text.endswith("\n"))  # trailing newline

    def test_delete(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            store.write("k", {"ok": True})
            self.assertTrue(store.delete("k"))
            self.assertFalse(store.exists("k"))
            self.assertFalse(store.delete("k"))  # idempotent


if __name__ == "__main__":
    unittest.main()
