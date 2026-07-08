"""TM.S2 — the REAL fail-closed team preflight (upgrades the S1 stub).

Team activates ONLY when: a run identity + `$MOKATA_PG_DSN` (S1) + the DB is reachable within
the probe + the schema version is compatible. Every failure mode is fail-closed with its own
NAMED fix:

  * missing DSN          → export MOKATA_PG_DSN (S1)
  * unreachable/timeout  → reachability fix + the pooler-trap hint (LISTEN/NOTIFY dies behind a
                           transaction-mode pooler → use a direct/session DSN)
  * schema absent        → run `mokata team init`
  * incompatible version → the named version mismatch

The probe is injectable so this doesn't need psycopg — the real probe is covered in
test_tm_s2_connection_probe.py.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import run_mode
from mokata import teamdb
from mokata.config import Constitution, Surface
from mokata.detect import Detector
from mokata.manifest import Manifest


def _surface(settings=None):
    data = sample_manifest_data()
    if settings is not None:
        data.setdefault("settings", {}).update(settings)
    m = Manifest.from_dict(data)
    return Surface(m, Constitution("", None), root=".", detector=Detector())


def _preflight(env, probe_result, identity="alice"):
    return run_mode.team_preflight(_surface(), environ=env, identity=identity,
                                   probe=lambda dsn, **kw: probe_result)


_GOOD = {"MOKATA_PG_DSN": "postgres://h/db"}


class TestPreflightActivation(unittest.TestCase):
    def test_reachable_compatible_activates(self):
        res = teamdb.ProbeResult(reachable=True, schema_present=True,
                                 schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True,
                                 detail="reachable + schema v1 compatible")
        report = _preflight(_GOOD, res)
        self.assertTrue(report.activatable, report.render())
        self.assertFalse(report.blockers)

    def test_no_probe_when_dsn_missing(self):
        called = {"n": 0}

        def _probe(dsn, **kw):
            called["n"] += 1
            return teamdb.ProbeResult(reachable=True, compatible=True)
        report = run_mode.team_preflight(_surface(), environ={}, identity="alice",
                                         probe=_probe)
        self.assertFalse(report.activatable)
        self.assertEqual(called["n"], 0, "must not probe without a DSN")
        cred = [c for c in report.blockers if c.name == "credentials"][0]
        self.assertIn("MOKATA_PG_DSN", cred.fix)


class TestPreflightFailClosed(unittest.TestCase):
    def test_unreachable_names_reachability_and_pooler_trap(self):
        res = teamdb.ProbeResult(reachable=False, compatible=False, error="timeout",
                                 detail="unreachable — no response within 500ms")
        report = _preflight(_GOOD, res)
        self.assertFalse(report.activatable)
        db = [c for c in report.blockers if c.name == "shared-database"][0]
        self.assertIn("pooler", db.fix.lower())        # the pooler-trap hint
        self.assertIn("direct", db.fix.lower())         # use a direct/session DSN

    def test_schema_absent_names_team_init(self):
        res = teamdb.ProbeResult(reachable=True, schema_present=False, compatible=False,
                                 detail="reachable, but not provisioned")
        report = _preflight(_GOOD, res)
        self.assertFalse(report.activatable)
        sch = [c for c in report.blockers if c.name == "team-schema"][0]
        self.assertIn("team init", sch.fix)

    def test_incompatible_version_named(self):
        res = teamdb.ProbeResult(reachable=True, schema_present=True, schema_version=99,
                                 compatible=False,
                                 detail="reachable, but schema v99 != required v1")
        report = _preflight(_GOOD, res)
        self.assertFalse(report.activatable)
        sch = [c for c in report.blockers if c.name == "team-schema"][0]
        self.assertIn("99", sch.detail + sch.fix)

    def test_driver_absent_names_the_extra(self):
        res = teamdb.ProbeResult(driver_present=False, reachable=False, compatible=False,
                                 error="driver-absent")
        report = _preflight(_GOOD, res)
        self.assertFalse(report.activatable)
        drv = [c for c in report.blockers if c.name in ("driver", "shared-database")][0]
        self.assertIn("postgres", drv.fix.lower())

    def test_every_blocker_has_a_fix(self):
        res = teamdb.ProbeResult(reachable=False, compatible=False, error="timeout")
        report = _preflight({}, res)                    # no DSN + a failing probe
        for c in report.blockers:
            self.assertTrue(c.fix, f"blocker {c.name} has no fix")


class TestPreflightNoDoubleActivation(unittest.TestCase):
    def test_identity_missing_blocks_even_when_db_ok(self):
        res = teamdb.ProbeResult(reachable=True, schema_present=True,
                                 schema_version=teamdb.TEAM_SCHEMA_VERSION, compatible=True)
        report = _preflight(_GOOD, res, identity="")
        self.assertFalse(report.activatable)
        self.assertIn("run-identity", [c.name for c in report.blockers])


if __name__ == "__main__":
    unittest.main()
