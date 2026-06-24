"""CLI for E8 — `mokata exec` reports the chosen execution mode (default sequential)."""

import io
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestExecCLI(unittest.TestCase):
    def test_default_is_sequential(self):
        rc, out = run_cli(["exec"])
        self.assertEqual(rc, 0)
        self.assertIn("sequential", out.lower())

    def test_parallel_fanout_honored(self):
        rc, out = run_cli(["exec", "--parallel", "--fanout"])
        self.assertEqual(rc, 0)
        self.assertIn("parallel", out.lower())
        self.assertIn("fan-out", out.lower())


if __name__ == "__main__":
    unittest.main()
