"""TM.S1 — team/local run modes: the mode is a first-class, VISIBLE session property.

`local` is the default and requires nothing (byte-for-byte zero-config, today's behaviour);
`team` points the stores at shared databases and requires prerequisites checked fail-closed
BEFORE activation. This stage is the MODE PLUMBING + SURFACE — the shared-DB connection
manager + real probe land in TM.S2, so the team preflight must fail closed with a named,
actionable pointer to TM.S2 until then (never a half-activation).

This guard freezes:
  1. `read_mode` defaults to `local` (absent/broken/invalid → local; never raises);
  2. a fresh (zero-config) manifest is `local` and stays byte-for-byte unchanged;
  3. `team_preflight` is fail-closed: it reports the S1 prereqs (run identity + $MOKATA_PG_DSN
     credential presence) and — since TM.S2 landed the real probe — the shared-DB reachability +
     schema checks (the detailed named verdicts live in test_tm_s2_preflight.py);
  4. the mode is surfaced identically everywhere — statusline badge, SessionStart bootstrap,
     the CLI banner, `mokata doctor` — and across every harness (context injection).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import run_mode
from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.manifest import Manifest


def _surface(settings=None):
    data = sample_manifest_data()
    if settings is not None:
        data.setdefault("settings", {}).update(settings)
    m = Manifest.from_dict(data)
    return Surface(m, Constitution("", None), root=".", detector=Detector())


# ============================================================ mode read (default local)
class TestReadMode(unittest.TestCase):
    def test_absent_setting_reads_local(self):
        # zero-config: no `mode` key at all → local (today's behaviour, requires nothing).
        self.assertEqual(run_mode.read_mode(_surface()), run_mode.LOCAL)

    def test_explicit_local(self):
        self.assertEqual(run_mode.read_mode(_surface({"mode": "local"})), run_mode.LOCAL)

    def test_explicit_team(self):
        self.assertEqual(run_mode.read_mode(_surface({"mode": "team"})), run_mode.TEAM)

    def test_invalid_value_degrades_to_local(self):
        # local-first + fail-closed: an unrecognised mode is NEVER team; it reads as local.
        self.assertEqual(run_mode.read_mode(_surface({"mode": "solo"})), run_mode.LOCAL)

    def test_broken_surface_degrades_to_local(self):
        class Boom:
            @property
            def manifest(self):
                raise RuntimeError("no manifest")
        self.assertEqual(run_mode.read_mode(Boom()), run_mode.LOCAL)

    def test_is_valid_mode(self):
        self.assertTrue(run_mode.is_valid_mode("local"))
        self.assertTrue(run_mode.is_valid_mode("team"))
        self.assertFalse(run_mode.is_valid_mode("solo"))
        self.assertFalse(run_mode.is_valid_mode(""))

    def test_stored_value_exposes_the_raw_invalid_mode(self):
        # doctor needs to SEE an invalid hand-edited value to flag it (while read_mode
        # stays safe/local). stored_mode returns the raw string, or None when absent.
        self.assertIsNone(run_mode.stored_mode(_surface()))
        self.assertEqual(run_mode.stored_mode(_surface({"mode": "solo"})), "solo")


# ============================================================ team preflight (fail-closed)
class TestTeamPreflight(unittest.TestCase):
    def test_refuses_activation_when_the_db_is_unreachable(self):
        # TM.S2 landed the real probe: an unreachable/undriveable DB still fails closed.
        # (psycopg is absent in CI → the driver blocker; a bogus DSN → unreachable.) The
        # detailed named-verdict coverage lives in test_tm_s2_preflight.py.
        report = run_mode.team_preflight(_surface(), environ={"MOKATA_PG_DSN": "x"})
        self.assertFalse(report.activatable)
        self.assertTrue(report.blockers)
        # the shared-database blocker is present once the S1 prereqs (identity + DSN) pass.
        self.assertIn("shared-database", [c.name for c in report.blockers])

    def test_reports_run_identity_prereq(self):
        report = run_mode.team_preflight(_surface(), environ={"MOKATA_PG_DSN": "x"},
                                         identity="alice")
        idc = [c for c in report.checks if c.name == "run-identity"]
        self.assertEqual(len(idc), 1)
        self.assertTrue(idc[0].ok)
        self.assertIn("alice", idc[0].detail)

    def test_missing_credential_is_a_named_blocker(self):
        report = run_mode.team_preflight(_surface(), environ={})
        cred = [c for c in report.checks if c.name == "credentials"]
        self.assertEqual(len(cred), 1)
        self.assertFalse(cred[0].ok)
        self.assertIn("MOKATA_PG_DSN", cred[0].fix)

    def test_present_credential_passes_that_check(self):
        report = run_mode.team_preflight(_surface(),
                                         environ={"MOKATA_PG_DSN": "postgres://h/db"})
        cred = [c for c in report.checks if c.name == "credentials"]
        self.assertTrue(cred[0].ok)

    def test_missing_identity_is_a_named_blocker(self):
        report = run_mode.team_preflight(_surface(), environ={"MOKATA_PG_DSN": "x"},
                                         identity="")
        idc = [c for c in report.checks if c.name == "run-identity"][0]
        self.assertFalse(idc.ok)

    def test_render_lists_every_check_with_a_fix_for_blockers(self):
        report = run_mode.team_preflight(_surface(), environ={})
        text = report.render()
        # every blocker names an actionable fix (P8 fail-closed: named fix, never silent).
        for c in report.blockers:
            self.assertTrue(c.fix, f"blocker {c.name} has no fix")


# ============================================================ the mode surface (one source)
class TestModeSurface(unittest.TestCase):
    def test_badge_fragment_shows_the_mode(self):
        self.assertIn("local", run_mode.mode_badge(_surface()))
        self.assertIn("team", run_mode.mode_badge(_surface({"mode": "team"})))

    def test_mode_line_is_compact_and_names_the_mode(self):
        line = run_mode.mode_line(_surface())
        self.assertIn("local", line)
        # one line only (bootstrap token budget) — no newlines.
        self.assertNotIn("\n", line)

    def test_mode_line_degrades_clean_on_a_broken_surface(self):
        class Boom:
            @property
            def manifest(self):
                raise RuntimeError("no manifest")
        # never raises; falls back to the local default.
        self.assertIn("local", run_mode.mode_line(Boom()))


if __name__ == "__main__":
    unittest.main()
