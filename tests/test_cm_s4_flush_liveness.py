"""CM.S4 — FLUSH LIVENESS: retry/backoff + pending count surfaced (C-4 / C-5 liveness half).

The bug this closes: a failed flush was silent and passive — it just waited for some future
incidental call to retry, and nothing counted or reported the backlog. A team could accumulate
approved-but-unflushed writes indefinitely (C-4) with zero signal.

These tests pin the fix:
  (a) C-4 regression: an unreachable PG leaves entries PENDING; `pending_count()` reports them;
      the next touchpoint AFTER the backoff window retries and (PG now reachable) DRAINS to 0.
  (b) Backoff honesty: within the backoff window a touchpoint does NOT re-attempt (no connect
      call), even if health looks OK mid-window; after the window it does; the retry cap is
      respected (a capped backlog stops auto-retrying until `mokata sync` / a drain resets it).
  (c) Surfacing agreement: doctor + badge + the MCP field show the SAME count and agree with each
      other AND with the CM.S2 degrade verdict (one routing input — no second probe).
  (d) Negatives: a healthy team flush engages NO retry machinery (no liveness state written) and
      is output-identical; local mode is byte-identical (no pending concept surfaced at all).
  (e) Secret-safety: no DSN VALUE and no memory CONTENT ever appears in the badge / doctor / MCP
      surfacing (env NAME + counts + failure classes only).

All proven behind fakes / injected connect + health — no live Postgres.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import (MANIFEST_FILENAME, MOKATA_DIR, degrade, flush_liveness,
                    run_mode, team, team_health, team_journal, teamdb)
from mokata.config import Surface
from mokata.init import init_repo
from mokata.team_journal import OP_PUT, TeamJournal, record_team_write

CUSTOM = "MOKATA_TEAM_CUSTOM_DSN"
_DSN = "postgres://secret-host/db"
_SECRET_VALUE = "ghp_this_is_not_a_real_token_value_00000000"


# --------------------------------------------------------------------------- fakes / helpers
class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePg:
    """A minimal in-memory `mokata_memory` stand-in — enough for the flush's CAS SQL (id→doc)."""

    def __init__(self):
        self.rows = {}

    def execute(self, sql, params=None):
        h = " ".join(sql.split()).upper()
        params = params or ()
        if h.startswith(("CREATE TABLE", "ALTER TABLE")):
            return _Cur()
        if h.startswith("INSERT"):
            self.rows[params[0]] = params[4]
            return _Cur(rowcount=1)
        if h.startswith("SELECT DOC, REVISION"):
            if "WHERE ID=" in h:
                doc = self.rows.get(params[0])
                return _Cur([(doc, 1)] if doc is not None else [])
            return _Cur([(doc, 1) for doc in self.rows.values()])
        if h.startswith("DELETE"):
            existed = self.rows.pop(params[0], None) is not None
            return _Cur(rowcount=1 if existed else 0)
        return _Cur()

    def close(self):
        pass


def _set_mode(root, mode):
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _force_postgres_present(root):
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["tools"]["postgres"]["detect"] = {"type": "always"}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _team_repo(root, dsn_env=CUSTOM, mode="team", postgres_present=True):
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    team.team_connect(root, Surface.load(root), dsn_env, assume_yes=True, out=lambda _m: None)
    if postgres_present:
        _force_postgres_present(root)
    _set_mode(root, mode)
    return Surface.load(root)


def _journal_write(surface, key, value="v"):
    payload = {"id": key, "mtype": "persistent", "subject": key, "status": "active",
               "doc": json.dumps({"id": key, "subject": key, "value": value,
                                  "mtype": "persistent", "status": "active"}),
               "project": None}
    return record_team_write(surface, op=OP_PUT, table=teamdb.MEMORY_TABLE, key=key,
                             payload=payload, ledger_id="approval-" + key)


_OFFLINE = team_health.HealthVerdict(team_health.OFFLINE, "unreachable — could not connect")
_HEALTHY = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
_NO_SCAN = lambda _entry: []  # noqa: E731 — hermetic: never treat a test payload as a secret


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ===================================================================== (a) C-4 regression
class TestC4Regression(unittest.TestCase):
    def test_unreachable_keeps_pending_then_drains_after_backoff(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d)
            for i in range(3):
                _journal_write(s, f"k{i}")
            self.assertEqual(TeamJournal.for_surface(s).pending_count(), 3)

            clock = _Clock()
            calls = []
            pg = _FakePg()

            def connect(_surface, _environ):
                calls.append(clock.t)
                return pg

            # attempt 1: unreachable → skipped, pending stays, connect NOT called, backoff set
            r1 = flush_liveness.flush_with_liveness(
                s, health=_OFFLINE, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertTrue(r1.skipped)
            self.assertEqual(calls, [], "unreachable → no connection attempt")
            self.assertEqual(TeamJournal.for_surface(s).pending_count(), 3)
            st = flush_liveness.load_state(s)
            self.assertEqual(st.attempts, 1)
            self.assertGreater(st.next_retry_at, clock.t)

            ps = flush_liveness.pending_status(s, now=clock)
            self.assertIsNotNone(ps)
            self.assertEqual(ps.pending, 3)

            # PG reachable now, but PAST the backoff window → retry runs and drains to 0
            clock.t = st.next_retry_at + 1.0
            r2 = flush_liveness.flush_with_liveness(
                s, health=_HEALTHY, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertFalse(r2.skipped)
            self.assertEqual(len(calls), 1, "past the window → exactly one connection attempt")
            self.assertEqual(TeamJournal.for_surface(s).pending_count(), 0)
            self.assertIsNone(flush_liveness.pending_status(s, now=clock),
                              "drained → nothing pending → no surface")
            self.assertEqual(flush_liveness.load_state(s).attempts, 0,
                             "a successful drain resets the retry machinery")


# ===================================================================== (b) backoff honesty
class TestBackoffHonesty(unittest.TestCase):
    def test_within_window_does_not_reattempt(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d)
            _journal_write(s, "k0")
            clock = _Clock()
            calls = []

            def connect(_surface, _environ):
                calls.append(clock.t)
                return _FakePg()

            flush_liveness.flush_with_liveness(
                s, health=_OFFLINE, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            st = flush_liveness.load_state(s)

            # mid-window, even with health flipped HEALTHY, the backoff schedule is respected:
            # NO connection attempt (we do not hammer just because health momentarily looks ok).
            clock.t = st.next_retry_at - 0.01
            flush_liveness.flush_with_liveness(
                s, health=_HEALTHY, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertEqual(calls, [], "within the backoff window → no re-attempt")

            # just past the window → it attempts.
            clock.t = st.next_retry_at + 0.01
            flush_liveness.flush_with_liveness(
                s, health=_HEALTHY, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertEqual(len(calls), 1, "past the window → one attempt")

    def test_retry_cap_respected(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d)
            _journal_write(s, "k0")
            clock = _Clock()
            calls = []

            def connect(_surface, _environ):
                calls.append(clock.t)
                return _FakePg()

            # pin the state at the cap with an already-elapsed window.
            flush_liveness.store_state(s, flush_liveness.LivenessState(
                attempts=flush_liveness.RETRY_CAP, next_retry_at=0.0,
                last_failure_class=degrade.FAILURE_UNREACHABLE, backlog_since=1.0))
            clock.t = 10_000_000.0  # far past any window

            r = flush_liveness.flush_with_liveness(
                s, health=_HEALTHY, connect=connect, scan=_NO_SCAN, now=clock,
                jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertTrue(r.skipped)
            self.assertEqual(calls, [], "at the retry cap → auto-retry stops (no attempt)")
            self.assertTrue(flush_liveness.load_state(s).capped)
            ps = flush_liveness.pending_status(s, now=clock)
            self.assertTrue(ps.capped)


# ===================================================================== (c) surfacing agreement
class TestSurfacingAgreement(unittest.TestCase):
    def _seed_backlog(self, s, n):
        for i in range(n):
            _journal_write(s, f"k{i}")
        clock = _Clock()
        flush_liveness.flush_with_liveness(
            s, health=_OFFLINE, connect=lambda *_a: None, scan=_NO_SCAN, now=clock,
            jitter=lambda: 0.0, environ={CUSTOM: _DSN})

    def test_doctor_badge_mcp_agree_on_count_and_class(self):
        env_backup = os.environ.pop(CUSTOM, None)   # keep DSN unset → offline, no network
        try:
            with tempfile.TemporaryDirectory() as d:
                s = _team_repo(d)
                self._seed_backlog(s, 2)

                # the ONE routing input (CM.S2), reused by every surface.
                routing = degrade.resolve_read_routing(s, environ=os.environ, force=True)
                self.assertTrue(routing.degraded)
                ps = flush_liveness.pending_status(s, routing=routing)
                self.assertEqual(ps.pending, 2)
                self.assertEqual(ps.last_failure_class, routing.notice.failure_class,
                                 "the pending status agrees with the CM.S2 degrade verdict")

                # badge: pending count present, zero-network (cached verdict only).
                team_health.store(s, team_health.HealthVerdict(
                    team_health.OFFLINE, "offline", checked_at=time.time()))
                badge = run_mode.mode_badge(s)
                self.assertIn("2 pending", badge)
                self.assertIn("team", badge)

                # doctor: same count (verbose phrasing) + reuses the SAME routing verdict.
                from mokata.govern.doctor import diagnose
                report = diagnose(s).render()
                self.assertIn("team pending:", report)
                self.assertIn("2 approved write(s)", report)

                # MCP recall: structured pending field, same count + class as routing.
                from mokata.mcp.tools_read import recall
                resp = recall(path=d)
                self.assertIn("pending", resp)
                self.assertEqual(resp["pending"]["pending"], 2)
                self.assertEqual(resp["pending"]["last_failure_class"],
                                 routing.notice.failure_class)
        finally:
            if env_backup is not None:
                os.environ[CUSTOM] = env_backup


# ===================================================================== (d) negatives / identity
class TestNegatives(unittest.TestCase):
    def test_healthy_flush_engages_no_retry_machinery(self):
        with tempfile.TemporaryDirectory() as d:
            s = _team_repo(d)
            _journal_write(s, "k0")
            clock = _Clock()
            r = flush_liveness.flush_with_liveness(
                s, health=_HEALTHY, connect=lambda *_a: _FakePg(), scan=_NO_SCAN,
                now=clock, jitter=lambda: 0.0, environ={CUSTOM: _DSN})
            self.assertFalse(r.skipped)
            self.assertEqual(TeamJournal.for_surface(s).pending_count(), 0)
            # a clean healthy drain writes NO liveness state file (no machinery engaged).
            state_path = os.path.join(s.mokata_dir, "temp_local",
                                      flush_liveness.STATE_FILENAME)
            self.assertFalse(os.path.exists(state_path),
                             "healthy flush must not write retry/backoff state")
            self.assertIsNone(flush_liveness.pending_status(s, now=clock))

    def test_local_mode_has_no_pending_concept(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            s = Surface.load(d)                              # local (default)
            self.assertEqual(run_mode.read_mode(s), run_mode.LOCAL)
            self.assertIsNone(flush_liveness.pending_status(s))
            self.assertEqual(flush_liveness.badge_segment(s), "")
            self.assertNotIn("pending", run_mode.mode_badge(s))

    def test_local_recall_has_no_pending_field(self):
        from mokata.mcp.tools_read import recall
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            resp = recall(path=d)
            self.assertTrue(resp["enabled"])
            self.assertNotIn("pending", resp)


# ===================================================================== (e) secret-safety
class TestSecretSafety(unittest.TestCase):
    def test_no_dsn_value_or_memory_content_in_surfaces(self):
        env_backup = os.environ.get(CUSTOM)
        os.environ[CUSTOM] = _DSN
        try:
            with tempfile.TemporaryDirectory() as d:
                s = _team_repo(d)
                _journal_write(s, "creds.token", value=_SECRET_VALUE)
                clock = _Clock()
                flush_liveness.flush_with_liveness(
                    s, health=_OFFLINE, connect=lambda *_a: None, scan=_NO_SCAN, now=clock,
                    jitter=lambda: 0.0, environ={CUSTOM: _DSN})
                team_health.store(s, team_health.HealthVerdict(
                    team_health.OFFLINE, "offline", checked_at=time.time()))

                # The CM.S4 SURFACING output (badge, doctor line, the structured pending + degrade
                # fields) — NOT the recall items themselves, which legitimately hand the owner back
                # their own stored memory. The observability surfaces carry counts + classes only.
                badge = run_mode.mode_badge(s)
                from mokata.govern.doctor import diagnose
                report = diagnose(s).render()
                from mokata.mcp.tools_read import recall
                resp = recall(path=d)
                ps = flush_liveness.pending_status(
                    s, routing=degrade.resolve_read_routing(s, environ={CUSTOM: _DSN},
                                                            force=True))
                surfaced = (badge + report
                            + json.dumps(resp.get("pending"))
                            + json.dumps(resp.get("degraded"))
                            + json.dumps(ps.to_dict()))
                self.assertNotIn("secret-host", surfaced)   # never the DSN VALUE
                self.assertNotIn("postgres://", surfaced)
                self.assertNotIn(_SECRET_VALUE, surfaced)   # never the memory CONTENT
        finally:
            if env_backup is None:
                os.environ.pop(CUSTOM, None)
            else:
                os.environ[CUSTOM] = env_backup


if __name__ == "__main__":
    unittest.main()
