"""TM.S1 — the `mokata mode` CLI surface + zero-config regression.

`mokata mode` shows the current mode + the team-readiness preflight; `mokata mode set
local|team` changes it. `set team` runs the fail-closed preflight and REFUSES at S1,
naming TM.S2 — never a half-activation, never a durable write on refusal. `set local` on a
fresh (already-local) repo is a no-op that writes NOTHING, so local stays byte-for-byte
zero-config.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata import cli
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


class TestModeShow(unittest.TestCase):
    def test_fresh_repo_shows_local(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, text = _run(["mode", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("local", text)

    def test_show_includes_team_readiness(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, text = _run(["mode", "--path", d])
            # a session is never ambiguous — `mode` surfaces the team preflight too.
            self.assertIn("team mode", text.lower())


class TestModeSetTeam(unittest.TestCase):
    def test_set_team_without_dsn_fails_closed_and_names_the_fix(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            env0 = os.environ.pop("MOKATA_PG_DSN", None)
            try:
                rc, text = _run(["mode", "set", "team", "--path", d, "--yes"])
            finally:
                if env0 is not None:
                    os.environ["MOKATA_PG_DSN"] = env0
            self.assertNotEqual(rc, 0)                  # refused (fail-closed)
            self.assertIn("MOKATA_PG_DSN", text)         # names the missing-DSN fix

    def test_set_team_writes_nothing_on_refusal(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            env0 = os.environ.pop("MOKATA_PG_DSN", None)
            try:
                before = _manifest_bytes(d)
                _run(["mode", "set", "team", "--path", d, "--yes"])
                # never half-activate: a refused activation leaves the manifest byte-for-byte.
                self.assertEqual(before, _manifest_bytes(d))
                # and the mode is still local.
                rc, text = _run(["mode", "--path", d])
                self.assertIn("local", text)
            finally:
                if env0 is not None:
                    os.environ["MOKATA_PG_DSN"] = env0


class TestModeSetLocalZeroConfig(unittest.TestCase):
    def test_set_local_on_fresh_repo_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            before = _manifest_bytes(d)
            rc, text = _run(["mode", "set", "local", "--path", d, "--yes"])
            self.assertEqual(rc, 0)
            # already local (the zero-config default) → a no-op, nothing written.
            self.assertEqual(before, _manifest_bytes(d))

    def test_fresh_manifest_has_no_mode_key(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            data = json.loads(_manifest_bytes(d))
            # zero-config: `mode` is NOT a required key in a fresh manifest.
            self.assertNotIn("mode", data.get("settings", {}))


if __name__ == "__main__":
    unittest.main()
