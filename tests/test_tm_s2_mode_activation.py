"""TM.S2 — `mokata mode set team` really activates against a reachable, compatible DB.

The connection/probe now decides activation. Fail-closed paths write NOTHING (never a
half-activation); the reachable+compatible path performs the human-gated mode write. The probe
is patched so this doesn't need a live Postgres (real-probe coverage is elsewhere).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata import cli, teamdb
from mokata.init import init_repo


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


def _manifest_bytes(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), "rb") as fh:
        return fh.read()


def _mode_setting(d):
    data = json.loads(_manifest_bytes(d))
    return (data.get("settings") or {}).get("mode")


def _run(argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}, clear=False):
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(argv)
    return rc, out.getvalue() + err.getvalue()


_REACHABLE = teamdb.ProbeResult(reachable=True, schema_present=True,
                                schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                                detail="reachable + schema compatible")
_UNREACHABLE = teamdb.ProbeResult(reachable=False, compatible=False, error="timeout",
                                  detail="unreachable — no response within 500ms")
_UNPROVISIONED = teamdb.ProbeResult(reachable=True, schema_present=False, compatible=False,
                                    detail="reachable, but not provisioned")


class TestActivateTeam(unittest.TestCase):
    def test_reachable_compatible_activates_and_writes_team(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with mock.patch("mokata.teamdb.probe", lambda dsn, **kw: _REACHABLE):
                rc, text = _run(["mode", "set", "team", "--path", d, "--yes"],
                                env={"MOKATA_PG_DSN": "postgres://h/db"})
            self.assertEqual(rc, 0, text)
            self.assertEqual(_mode_setting(d), "team")   # the durable mode write landed
            # and `mode` now reads team.
            rc2, show = _run(["mode", "--path", d], env={"MOKATA_PG_DSN": "postgres://h/db"})
            self.assertIn("team", show)


class TestFailClosedWritesNothing(unittest.TestCase):
    def test_unreachable_refuses_within_budget_and_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            before = _manifest_bytes(d)
            with mock.patch("mokata.teamdb.probe", lambda dsn, **kw: _UNREACHABLE):
                rc, text = _run(["mode", "set", "team", "--path", d, "--yes"],
                                env={"MOKATA_PG_DSN": "postgres://h/db"})
            self.assertNotEqual(rc, 0)
            self.assertIn("direct", text.lower())        # the pooler-trap fix
            self.assertEqual(before, _manifest_bytes(d))  # nothing written

    def test_unprovisioned_names_team_init_and_writes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            before = _manifest_bytes(d)
            with mock.patch("mokata.teamdb.probe", lambda dsn, **kw: _UNPROVISIONED):
                rc, text = _run(["mode", "set", "team", "--path", d, "--yes"],
                                env={"MOKATA_PG_DSN": "postgres://h/db"})
            self.assertNotEqual(rc, 0)
            self.assertIn("team init", text)
            self.assertEqual(before, _manifest_bytes(d))


if __name__ == "__main__":
    unittest.main()
