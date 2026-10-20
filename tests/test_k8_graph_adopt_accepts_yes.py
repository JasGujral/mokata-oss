"""0.0.25 K8 — `mokata graph adopt --yes` works, because the refusal tells the user to type it.

In a non-interactive shell the human gate refuses with "Re-run with `--yes`", and the `adopt`
subcommand rejected `--yes` as an unknown argument (argparse exit 2): the one instruction the user
was given could not be followed. `cmd_graph_adopt` already read `args.yes`; only the flag was missing.

Mutation this module was checked against (went RED, then was restored):
  M1 the `--yes` argument removed from the `adopt` parser -> test_the_advice_in_the_refusal_can_be_followed
"""

import _support  # noqa: F401

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from mokata.cli import main
from mokata.init import init_repo


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err), \
            mock.patch("sys.stdin", io.StringIO("")):          # not a terminal
        try:
            rc = main(argv)
        except SystemExit as exc:                               # argparse rejection
            rc = exc.code
    return rc, out.getvalue() + err.getvalue()


class TestGraphAdoptYes(unittest.TestCase):
    def test_the_advice_in_the_refusal_can_be_followed(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            rc, out = run(["graph", "adopt", "--path", d])
            self.assertEqual(rc, 1)
            self.assertIn("--yes", out, "the refusal names the flag")
            rc, out = run(["graph", "adopt", "--yes", "--path", d])
            self.assertEqual(rc, 0, out)
            self.assertIn("adopted code-review-graph", out)
            with open(os.path.join(d, ".mokata", "manifest.json"), encoding="utf-8") as fh:
                self.assertIn("code-review-graph", json.dumps(json.load(fh)))


if __name__ == "__main__":
    unittest.main()
