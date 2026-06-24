"""CLI for B4/B5 — `mokata index` (freshness) and `mokata lat-check` (drift)."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data, write_sample_repo

from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestIndexCLI(unittest.TestCase):
    def test_index_builds_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["index", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("index", out.lower())


class TestLatCheckCLI(unittest.TestCase):
    def test_lat_check_flags_drift_with_nonzero_rc(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            with open(os.path.join(d, "ghost.py"), "w", encoding="utf-8") as fh:
                fh.write("# @lat: ghost\nx = 1\n")
            with open(os.path.join(d, "lat.md"), "w", encoding="utf-8") as fh:
                fh.write("- auth\n")
            rc, out = run_cli(["lat-check", "--path", d])
            self.assertEqual(rc, 1)            # drift -> flagged
            self.assertIn("drift", out.lower())

    def test_lat_check_clean_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["lat-check", "--path", d])
            self.assertEqual(rc, 0)            # no anchors/registry -> degrade clean


if __name__ == "__main__":
    unittest.main()
