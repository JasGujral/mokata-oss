"""CLI for E8 — `mokata exec` reports the chosen execution mode (default sequential)."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support
from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestExecCLI(unittest.TestCase):
    def test_default_is_sequential(self):
        """⚠ THE STDIN IS THIS TEST'S, NOT THE MACHINE'S (0.0.20 stage 10).

        `mokata exec` asks a human which mode to run — `_cli_ask`, `cli_commands/_common.py:127` —
        and only takes the default when stdin is not a terminal. Without the double this test
        graded whichever arm the runner's terminal happened to select, and at a real one it reached
        `input()` with stdout redirected two lines up: a silent hang. The sibling below already
        forces a non-TTY explicitly and says why; this one inherited the ambient. Filed as
        `SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS`, found by `tests/_tty_prompt_sweep.py`."""
        with _support.stdin_is(_support.NoTtyStdin()):
            rc, out = run_cli(["exec"])
        self.assertEqual(rc, 0)
        self.assertIn("sequential", out.lower())

    def test_non_tty_logs_default_decision(self):
        # Stage 1b — with a non-interactive stdin (not a TTY, as under CI/pytest),
        # `mokata exec` must not prompt; it silently defaults to sequential BUT logs
        # to stderr that the default was taken, so the decision is visible not silent.
        out_buf, err_buf = io.StringIO(), io.StringIO()
        # Force a non-TTY / EOF stdin (StringIO reports isatty()==False and yields EOF)
        # so the non-TTY decision is exercised regardless of the ambient terminal.
        with redirect_stdout(out_buf), redirect_stderr(err_buf), \
                mock.patch("sys.stdin", io.StringIO("")):
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
