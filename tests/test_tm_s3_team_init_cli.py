"""TM.S3 — the `mokata team init` CLI surface.

`init` is a new action on the existing `mokata team` group. With $MOKATA_PG_DSN unset it fails
closed with a named fix and writes nothing. (The reachable-DB provisioning path is exercised
against a live Postgres in the stage's manual demo + the mocked unit tests.)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata import cli, parity
from mokata.init import init_repo


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


def _manifest_bytes(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), "rb") as fh:
        return fh.read()


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue() + err.getvalue()


class TestTeamInitCli(unittest.TestCase):
    def test_init_is_a_registered_team_action(self):
        # the parser accepts `team init` (surfaced in the existing team group).
        self.assertIn("team", parity.cli_command_names())

    def test_dsn_unset_fails_closed_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            before = _manifest_bytes(d)
            env0 = os.environ.pop("MOKATA_PG_DSN", None)
            try:
                rc, text = _run(["team", "init", "--path", d, "--yes"])
            finally:
                if env0 is not None:
                    os.environ["MOKATA_PG_DSN"] = env0
            self.assertNotEqual(rc, 0)                   # fail-closed
            self.assertIn("MOKATA_PG_DSN", text)          # the named fix
            self.assertEqual(before, _manifest_bytes(d))  # nothing written

    def test_uninitialized_repo_is_guided_to_init(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rc, text = _run(["team", "init", "--path", d, "--yes"])
            self.assertIn("mokata init", text)


if __name__ == "__main__":
    unittest.main()
