"""CLI for E8 — `mokata exec` reports the chosen execution mode (default sequential)."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

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

    def test_non_tty_logs_default_decision(self):
        # Stage 1b — with a non-interactive stdin (not a TTY, as under CI/pytest),
        # `mokata exec` must not prompt; it silently defaults to sequential BUT logs
        # to stderr that the default was taken, so the decision is visible not silent.
        out_buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = main(["exec"])
        self.assertEqual(rc, 0)
        err = err_buf.getvalue().lower()
        self.assertIn("sequential", err)                       # the decision taken
        self.assertTrue("tty" in err or "stdin" in err)        # and why (non-interactive)

    def test_parallel_fanout_honored(self):
        rc, out = run_cli(["exec", "--parallel", "--fanout"])
        self.assertEqual(rc, 0)
        self.assertIn("parallel", out.lower())
        self.assertIn("fan-out", out.lower())


if __name__ == "__main__":
    unittest.main()
