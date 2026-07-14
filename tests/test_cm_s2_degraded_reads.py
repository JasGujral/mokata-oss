"""CM.S2 — LOUD team-mode degraded reads (C-2).

Before this stage, a team-mode memory READ that was actually served from the local SQLite
floor (shared Postgres unreachable/unset) was presented SILENTLY as team state, and the "what
mode" vs "which backend served the read" answers were computed from different inputs in
different places — so they could DISAGREE.

These tests pin the fix:
  (a) team mode + unreachable PG → the read still succeeds from the fallback AND is loudly
      marked degraded (the marker names the resolved env-var NAME + failure class, never the
      DSN value);
  (b) team mode + healthy PG → NO degrade notice;
  (c) mode/backend coherence: `served_by_team` derives from the SAME health verdict that the
      badge renders — the probe that says CONNECTED is the same input that routes the read, so
      they cannot diverge;
  (d) the loud CLI notice appears ONCE per subsystem, not once per read.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import (MANIFEST_FILENAME, MOKATA_DIR, degrade, run_mode, team,
                    team_health, teamdb)
from mokata.config import Surface
from mokata.init import init_repo

# A custom env-var NAME (no default-name substring) + a fake DSN whose VALUE must never leak
# into any notice/marker.
CUSTOM = "MOKATA_TEAM_CUSTOM_DSN"
_DSN = "postgres://secret-host/db"


def _set_mode(root, mode):
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _force_postgres_present(root):
    """Make the router resolve memory_store→postgres regardless of whether psycopg is installed,
    by flipping the postgres tool's detect to `always`. Lets these tests exercise the SHARED-tool
    degrade path on a machine without the optional driver."""
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["tools"]["postgres"]["detect"] = {"type": "always"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _team_repo(root, dsn_env=CUSTOM, mode="team", postgres_present=False):
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    team.team_connect(root, Surface.load(root), dsn_env, assume_yes=True, out=lambda _m: None)
    if postgres_present:
        _force_postgres_present(root)
    _set_mode(root, mode)
    return Surface.load(root)


def _probe(*, driver=True, reachable=False, schema=False, version=None, compatible=False,
           detail="fake"):
    def p(_dsn):
        return teamdb.ProbeResult(driver_present=driver, reachable=reachable,
                                  schema_present=schema, schema_version=version,
                                  compatible=compatible, elapsed_ms=5.0, detail=detail)
    return p


_HEALTHY = _probe(reachable=True, schema=True, version=teamdb.TEAM_SCHEMA_VERSION,
                  compatible=True, detail="reachable")
_UNREACHABLE = _probe(reachable=False, detail="unreachable — could not connect")
_SCHEMA_ABSENT = _probe(reachable=True, schema=False, version=None,
                        detail="reachable, but the shared schema is not provisioned")


# --------------------------------------------------------------- (c) mode/backend coherence
class TestCoherence(unittest.TestCase):
    """The probe that says CONNECTED is the SAME input that routes the read — they cannot
    diverge. The invariant: for the shared tool, `served_by_team == verdict.ok`, and `verdict`
    is the very verdict `team_health` renders for the badge/doctor."""

    def test_healthy_routes_to_team_and_is_not_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_HEALTHY,
                                             force=True)
            self.assertEqual(r.tool, "postgres")
            self.assertTrue(r.verdict.ok)
            self.assertEqual(r.served_by_team, r.verdict.ok)   # cannot diverge
            self.assertTrue(r.served_by_team)
            self.assertFalse(r.degraded)
            self.assertIsNone(r.notice)

    def test_unreachable_routes_local_and_is_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_UNREACHABLE,
                                             force=True)
            self.assertFalse(r.verdict.ok)
            self.assertEqual(r.served_by_team, r.verdict.ok)   # False == False, cannot diverge
            self.assertFalse(r.served_by_team)
            self.assertTrue(r.degraded)

    def test_routing_verdict_is_the_same_as_the_badge_verdict(self):
        # Both the routing decision and the health surface consult team_health.check with the
        # SAME inputs → the SAME state. The badge/doctor cannot claim CONNECTED while the read
        # routed local (or vice versa).
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            health = team_health.check(s, environ={CUSTOM: _DSN}, probe=_HEALTHY, force=True)
            routing = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_HEALTHY,
                                                   force=True)
            self.assertEqual(routing.verdict.state, health.state)
            self.assertEqual(routing.served_by_team, health.ok)


# --------------------------------------------------------- (a) unreachable → fallback + marker
class TestDegradeMarker(unittest.TestCase):
    def test_unreachable_notice_names_env_and_class_never_the_value(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_UNREACHABLE,
                                             force=True)
            self.assertTrue(r.degraded)
            self.assertIsNotNone(r.notice)
            self.assertEqual(r.notice.env_name, CUSTOM)
            self.assertEqual(r.notice.failure_class, degrade.FAILURE_UNREACHABLE)
            rendered = r.notice.render()
            self.assertIn(CUSTOM, rendered)             # the NAME the user configured
            self.assertIn("DEGRADED", rendered)         # loud
            self.assertNotIn("secret-host", rendered)   # never the DSN VALUE
            self.assertNotIn("postgres://", rendered)

    def test_unset_var_failure_class(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={}, probe=_UNREACHABLE, force=True)
            self.assertTrue(r.degraded)
            self.assertEqual(r.notice.failure_class, degrade.FAILURE_UNSET)
            self.assertEqual(r.notice.env_name, CUSTOM)

    def test_schema_failure_class(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_SCHEMA_ABSENT,
                                             force=True)
            self.assertTrue(r.degraded)
            self.assertEqual(r.notice.failure_class, degrade.FAILURE_SCHEMA)

    def test_read_still_succeeds_from_fallback_and_mcp_marks_degraded(self):
        # END-TO-END: a real team-mode store with the shared PG unreachable serves the read from
        # the LOCAL floor (read succeeds) AND the MCP recall response carries the degraded marker.
        from mokata.mcp.tools_read import recall
        env_backup = os.environ.get(CUSTOM)
        os.environ[CUSTOM] = _DSN
        try:
            with tempfile.TemporaryDirectory() as d:
                s = _team_repo(d, postgres_present=True)
                # pre-seed the SHARED E2 cache with a fresh OFFLINE verdict (no probe/network) —
                # from_surface's routing reuses this same cached verdict (E2).
                team_health.store(s, team_health.HealthVerdict(
                    team_health.OFFLINE, f"${CUSTOM}: unreachable", checked_at=time.time()))
                resp = recall(path=d)
                self.assertTrue(resp["enabled"])
                self.assertIn("items", resp)            # the read SUCCEEDED (from the floor)
                self.assertIn("degraded", resp)         # loudly surfaced, not silent
                self.assertTrue(resp["degraded"]["degraded"])
                self.assertEqual(resp["degraded"]["env_name"], CUSTOM)
                self.assertNotIn("secret-host", json.dumps(resp))   # never the DSN VALUE
        finally:
            if env_backup is None:
                os.environ.pop(CUSTOM, None)
            else:
                os.environ[CUSTOM] = env_backup


# ------------------------------------------------------------------ (b) healthy → no notice
class TestNoFalseDegrade(unittest.TestCase):
    def test_local_mode_never_degraded_and_never_probes(self):
        called = {"n": 0}

        def _counting_probe(_dsn):
            called["n"] += 1
            return _HEALTHY(_dsn)

        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            s = Surface.load(d)                          # local mode (the default)
            r = degrade.resolve_read_routing(s, probe=_counting_probe, force=True)
            self.assertEqual(r.mode, run_mode.LOCAL)
            self.assertFalse(r.degraded)
            self.assertIsNone(r.notice)
            self.assertEqual(called["n"], 0)             # zero-network local hot path

    def test_healthy_team_read_has_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d, postgres_present=True)
            r = degrade.resolve_read_routing(s, environ={CUSTOM: _DSN}, probe=_HEALTHY,
                                             force=True)
            self.assertFalse(r.degraded)
            self.assertIsNone(r.notice)

    def test_local_recall_has_no_degraded_key(self):
        from mokata.mcp.tools_read import recall
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            resp = recall(path=d)
            self.assertTrue(resp["enabled"])
            self.assertNotIn("degraded", resp)           # local reads are never marked degraded


# ------------------------------------------------------------- (d) notice once, not per-read
class TestNoticeOncePerSubsystem(unittest.TestCase):
    def test_notice_prints_once_not_per_read(self):
        seen = set()
        notice = degrade.DegradeNotice(subsystem="memory", env_name=CUSTOM,
                                       failure_class=degrade.FAILURE_UNREACHABLE)
        printed = []
        first = degrade.emit_degrade_notice(notice, printed.append, seen=seen)
        second = degrade.emit_degrade_notice(notice, printed.append, seen=seen)
        third = degrade.emit_degrade_notice(notice, printed.append, seen=seen)
        self.assertTrue(first)                           # printed once
        self.assertFalse(second)                         # suppressed as a repeat
        self.assertFalse(third)
        self.assertEqual(len(printed), 1)

    def test_distinct_subsystems_each_print_once(self):
        seen = set()
        printed = []
        mem = degrade.DegradeNotice("memory", CUSTOM, degrade.FAILURE_UNREACHABLE)
        know = degrade.DegradeNotice("knowledge", CUSTOM, degrade.FAILURE_UNREACHABLE)
        degrade.emit_degrade_notice(mem, printed.append, seen=seen)
        degrade.emit_degrade_notice(know, printed.append, seen=seen)
        degrade.emit_degrade_notice(mem, printed.append, seen=seen)
        self.assertEqual(len(printed), 2)                # one per subsystem


if __name__ == "__main__":
    unittest.main()
