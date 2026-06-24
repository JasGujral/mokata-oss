"""CLI for K5/K6/L5/L6 — doctor, reset, suggest, chain."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestLifecycleCLI(unittest.TestCase):
    def test_doctor_runs(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["doctor", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("doctor", out.lower())

    def test_reset_removes_state(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            mdir = os.path.join(d, MOKATA_DIR)
            self.assertTrue(os.path.exists(mdir))
            rc, _ = run_cli(["reset", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(mdir))

    def test_suggest_lists_without_running(self):
        rc, out = run_cli(["suggest", "--diff"])
        self.assertEqual(rc, 0)
        self.assertIn("review", out)
        self.assertIn("not run", out.lower())

    def test_chain_shows_gates(self):
        rc, out = run_cli(["chain", "spec", "test"])
        self.assertEqual(rc, 0)
        self.assertIn("spec", out)
        self.assertIn("gate", out.lower())


if __name__ == "__main__":
    unittest.main()
