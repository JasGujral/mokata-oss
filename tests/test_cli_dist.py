"""CLI for J2/J3 — `mokata export`, `mokata import`, `mokata harness`."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestExportImportCLI(unittest.TestCase):
    def test_export_then_import_on_clean_repo(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            init_repo(root=src, profile="full", assume_yes=True, out=silent)
            stack = os.path.join(src, "stack.json")
            rc, _ = run_cli(["export", stack, "--path", src])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(stack))

            rc, out = run_cli(["import", stack, "--yes", "--path", dst])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(
                os.path.join(dst, MOKATA_DIR, MANIFEST_FILENAME)))

    def test_import_rejects_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                json.dump({"manifest_version": 1}, fh)   # missing required keys
            rc, _ = run_cli(["import", bad, "--yes", "--path", d])
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(
                os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)))


class TestHarnessCLI(unittest.TestCase):
    def test_harness_lists_capabilities(self):
        rc, out = run_cli(["harness"])
        self.assertEqual(rc, 0)
        self.assertIn("claude-code", out)
        self.assertIn("subagents", out)


if __name__ == "__main__":
    unittest.main()
