"""TM.S4 — the fail-closed `activate_team` primitive (reach CONNECTED, then activate).

The joiner and `mokata mode set team` share ONE primitive: run the TM.S2 preflight; on a
reachable+compatible DB persist `settings.mode=team` (human-gated); on ANY fail-closed verdict
write NOTHING and surface the S2 named fix. Every fail-closed surface links the canonical
"Team mode: setup & operations" page.

The probe is injected so this needs no live Postgres.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, run_mode, team_docs, teamdb
from mokata.config import Surface
from mokata.init import init_repo


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest_bytes(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), "rb") as fh:
        return fh.read()


def _mode(d):
    return (json.loads(_manifest_bytes(d)).get("settings") or {}).get("mode")


_GOOD_ENV = {"MOKATA_PG_DSN": "postgres://h/db"}
_REACHABLE = teamdb.ProbeResult(reachable=True, schema_present=True,
                                schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                                detail="reachable + schema compatible")
_UNREACHABLE = teamdb.ProbeResult(reachable=False, compatible=False, error="timeout",
                                  detail="unreachable — no response within 500ms")
_UNPROVISIONED = teamdb.ProbeResult(reachable=True, schema_present=False, compatible=False,
                                    detail="reachable, but not provisioned")
_INCOMPATIBLE = teamdb.ProbeResult(reachable=True, schema_present=True, schema_version=99,
                                   compatible=False, detail="reachable, but v99 != v1")


def _activate(d, surface, probe_result, *, environ=_GOOD_ENV, assume_yes=True, out=None):
    return run_mode.activate_team(d, surface, environ=environ, assume_yes=assume_yes,
                                  identity="alice", out=out,
                                  probe=lambda dsn, **kw: probe_result)


class TestActivateGreen(unittest.TestCase):
    def test_reachable_compatible_connects_and_writes_team(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            res = _activate(d, s, _REACHABLE)
            self.assertTrue(res.connected, res.message)
            self.assertTrue(res.activated)
            self.assertEqual(_mode(d), "team")     # durable mode write landed


class TestActivateFailClosed(unittest.TestCase):
    def _refuses(self, probe_result, needle):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            before = _manifest_bytes(d)
            out = []
            res = _activate(d, s, probe_result, out=out.append)
            text = "\n".join(out)
            self.assertFalse(res.connected)
            self.assertFalse(res.activated)
            self.assertEqual(before, _manifest_bytes(d))          # NOTHING written
            self.assertIn(needle, text.lower())
            self.assertIn(team_docs.TEAM_DOCS_URL, text)          # links the page
            return text

    def test_unreachable_names_pooler_trap_and_writes_nothing(self):
        text = self._refuses(_UNREACHABLE, "direct")
        self.assertIn("pooler", text.lower())

    def test_schema_absent_names_team_init_and_writes_nothing(self):
        self._refuses(_UNPROVISIONED, "team init")

    def test_incompatible_version_named_and_writes_nothing(self):
        self._refuses(_INCOMPATIBLE, "99")


class TestPreflightRenderLinksDocs(unittest.TestCase):
    def test_render_footer_has_the_canonical_docs_link_on_any_blocker(self):
        report = run_mode.team_preflight(_surface_only(), environ={}, identity="")
        self.assertFalse(report.activatable)
        self.assertIn(team_docs.TEAM_DOCS_URL, report.render())


def _surface_only():
    with tempfile.TemporaryDirectory() as d:
        return _repo(d)


if __name__ == "__main__":
    unittest.main()
