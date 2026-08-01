"""DB.S1 — `mokata doctor` DSN DEEP-CHECK (doc 84 §7; ADR-54 H1, doc 85 §1).

Freezes the contract that doctor tells you WHICH layer of a team-DB connection failed — driver
vs network vs auth vs pooler vs schema-version — each a NAMED, actionable, SECRET-FREE finding:

  1. the classifier maps ONE probe attempt to exactly one primary finding (auth ≠ network ≠
     schema), with a POOLER co-report orthogonal to an otherwise-OK connection (injected doubles,
     no real network);
  2. `teamdb.probe` now carries a TYPED connection reason (auth vs network from SQLSTATE class 28),
     so the classifier never string-sniffs an exception message;
  3. doctor wires the section in: a team repo renders `database (team DSN)`, a hard failure flips
     doctor's exit through the SAME `report.ok`, and a local / no-DSN repo is SILENT (zero probe);
  4. the check is wall-clock bounded (a hanging connect never hangs doctor);
  5. secret-safety: the DSN value / password / host / user never appear in any render or stdout.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, db_doctor, dsn_inspect, teamdb
from mokata.config import Surface
from mokata.init import init_repo

_ENV = "MOKATA_PG_DSN"

# A DSN carrying a real password / host / user — used to prove NONE of them ever leak into a
# finding render or doctor stdout. Its SHAPE is a Supabase direct/session string.
_SECRET_PW = "s3cr3t-Pw-42"
_SECRET_HOST = "db.myprivateref.supabase.co"
_SECRET_USER = "admin_role"
_SECRET_DSN = f"postgres://{_SECRET_USER}:{_SECRET_PW}@{_SECRET_HOST}:5432/postgres"
# A pooled Supabase transaction-mode string (the DB.S0 trap shape).
_POOLED_DSN = f"postgres://{_SECRET_USER}:{_SECRET_PW}@aws-0-eu.pooler.supabase.com:6543/postgres"
_SECRETS = (_SECRET_PW, _SECRET_HOST, _SECRET_USER, _SECRET_DSN)


# --------------------------------------------------------------- ProbeResult / inspection doubles
def _res(**kw):
    """A ProbeResult with healthy defaults, overridable per case (no network involved)."""
    base = dict(driver_present=True, reachable=True, schema_present=True,
                schema_version=teamdb.TEAM_SCHEMA_VERSION,
                schema_min_supported=teamdb.TEAM_SCHEMA_MIN_SUPPORTED,
                compatible=True, elapsed_ms=12.0, detail="reachable + schema in range")
    base.update(kw)
    return teamdb.ProbeResult(**base)


def _direct():
    return dsn_inspect.inspect_dsn(_SECRET_DSN)


def _pooled():
    return dsn_inspect.inspect_dsn(_POOLED_DSN)


# ============================================================== 1) the classifier (pure, no network)
class TestClassify(unittest.TestCase):
    def _check(self, res, inspection=None):
        return db_doctor.classify(res, inspection or _direct(), _ENV)

    def test_db_s1_classify_driver_absent(self):
        c = self._check(_res(driver_present=False, reachable=False, compatible=False,
                             conn_reason=teamdb.CONN_DRIVER_ABSENT))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_DRIVER)
        self.assertFalse(c.ok)
        self.assertIn("mokata[postgres]", c.primary.detail)

    def test_db_s1_classify_network_unreach(self):
        c = self._check(_res(reachable=False, compatible=False,
                             conn_reason=teamdb.CONN_NETWORK_UNREACHABLE))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_NETWORK)
        self.assertFalse(c.ok)
        self.assertIn("UNREACHABLE", c.primary.detail)

    def test_db_s1_classify_timeout(self):
        c = self._check(_res(reachable=False, compatible=False, elapsed_ms=500,
                             conn_reason=teamdb.CONN_TIMEOUT))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_NETWORK)
        self.assertFalse(c.ok)
        self.assertIn("budget", c.primary.detail.lower())

    def test_db_s1_classify_auth_fail(self):
        c = self._check(_res(reachable=False, compatible=False,
                             conn_reason=teamdb.CONN_AUTH_FAILED))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_AUTH)
        self.assertFalse(c.ok)
        self.assertIn("AUTHENTICATION FAILED", c.primary.detail)
        self.assertIn(_ENV, c.primary.detail)          # names the env var, never the value

    def test_db_s1_classify_auth_is_distinct_from_network(self):
        auth = self._check(_res(reachable=False, compatible=False,
                                conn_reason=teamdb.CONN_AUTH_FAILED))
        net = self._check(_res(reachable=False, compatible=False,
                               conn_reason=teamdb.CONN_NETWORK_UNREACHABLE))
        self.assertNotEqual(auth.primary.axis, net.primary.axis)

    def test_db_s1_classify_schema_absent(self):
        c = self._check(_res(schema_present=False, schema_version=None, compatible=False,
                             reason=teamdb.REASON_SCHEMA_ABSENT))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_SCHEMA_ABSENT)
        self.assertFalse(c.ok)
        self.assertIn("mokata team init", c.primary.detail)

    def test_db_s1_classify_schema_old(self):
        c = self._check(_res(schema_version=1, compatible=False,
                             reason=teamdb.REASON_SCHEMA_TOO_OLD))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_SCHEMA_OLD)
        self.assertFalse(c.ok)
        self.assertIn("mokata team init", c.primary.detail)

    def test_db_s1_classify_client_too_old(self):
        c = self._check(_res(schema_version=99, compatible=False,
                             reason=teamdb.REASON_CLIENT_TOO_OLD))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_CLIENT_OLD)
        self.assertFalse(c.ok)
        self.assertIn("mokata", c.primary.detail.lower())
        self.assertIn("-U", c.primary.detail)          # upgrade the client

    def test_db_s1_classify_client_newer_in_range_warns_but_ok(self):
        # schema behind this build but IN RANGE → the "client-newer-than-schema" warn case.
        c = self._check(_res(schema_version=2, compatible=True,
                             warning="the shared schema is v2, behind this build (v3) — run "
                                     "`mokata team init` to upgrade it (working normally meanwhile)"))
        self.assertEqual(c.primary.axis, db_doctor.AXIS_SCHEMA_WARN)
        self.assertEqual(c.primary.severity, db_doctor.SEV_WARNING)
        self.assertTrue(c.ok)                          # a version difference in range never fails

    def test_db_s1_classify_all_ok(self):
        c = self._check(_res())
        self.assertEqual(c.primary.axis, db_doctor.AXIS_OK)
        self.assertEqual(c.primary.severity, db_doctor.SEV_INFO)
        self.assertTrue(c.ok)
        self.assertIn(f"schema v{teamdb.TEAM_SCHEMA_VERSION}", c.primary.detail)
        self.assertIn("Supabase", c.primary.detail)    # provider LABEL, never the host

    def test_db_s1_classify_pooled_but_ok_co_reports(self):
        c = self._check(_res(), inspection=_pooled())
        axes = [f.axis for f in c.findings]
        self.assertEqual(c.primary.axis, db_doctor.AXIS_OK)          # connection is OK
        self.assertIn(db_doctor.AXIS_POOLER, axes)                   # …and pooler co-reports
        self.assertTrue(c.ok)                                        # pooler warns, never fails
        pooler = [f for f in c.findings if f.axis == db_doctor.AXIS_POOLER][0]
        self.assertEqual(pooler.severity, db_doctor.SEV_WARNING)
        self.assertIn("POOLER", pooler.detail)

    def test_db_s1_classify_secret_free_every_axis(self):
        cases = [
            _res(driver_present=False, reachable=False, compatible=False,
                 conn_reason=teamdb.CONN_DRIVER_ABSENT),
            _res(reachable=False, compatible=False, conn_reason=teamdb.CONN_NETWORK_UNREACHABLE),
            _res(reachable=False, compatible=False, conn_reason=teamdb.CONN_AUTH_FAILED),
            _res(schema_present=False, schema_version=None, compatible=False,
                 reason=teamdb.REASON_SCHEMA_ABSENT),
            _res(),
        ]
        for res in cases:
            for inspection in (_direct(), _pooled()):
                c = db_doctor.classify(res, inspection, _ENV)
                blob = "\n".join(f.render() + f.detail for f in c.findings)
                for secret in _SECRETS:
                    self.assertNotIn(secret, blob,
                                     f"secret {secret!r} leaked into a finding render")


# ==================================================== 2) teamdb typed conn_reason (auth vs network)
class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, *, rows_for=None, error_for=None):
        self.closed = 0
        self.executed = []
        self._rows_for = rows_for or {}
        self._error_for = error_for or {}

    def execute(self, sql, *args):
        self.executed.append(sql)
        for key, exc in self._error_for.items():
            if key in sql:
                raise exc
        for key, rows in self._rows_for.items():
            if key in sql:
                return _FakeCursor(rows)
        return _FakeCursor([(1,)])

    def close(self):
        self.closed = 1


class _FakePsycopg:
    def __init__(self, conn=None, *, connect_error=None, connect_sleep=0.0):
        self.conn = conn if conn is not None else _FakeConn()
        self.connect_calls = []
        self._connect_error = connect_error
        self._connect_sleep = connect_sleep

    def connect(self, dsn, **kwargs):
        self.connect_calls.append((dsn, kwargs))
        if self._connect_sleep:
            time.sleep(self._connect_sleep)
        if self._connect_error is not None:
            raise self._connect_error
        return self.conn


@contextmanager
def _mock_psycopg(fake):
    old = sys.modules.get("psycopg")
    sys.modules["psycopg"] = fake
    from mokata.memory import _pg
    _pg.reset_manager()
    try:
        yield fake
    finally:
        if old is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = old
        _pg.reset_manager()


class _AuthError(Exception):
    sqlstate = "28P01"          # Postgres invalid_password — a query-phase auth error carries this


class _RealAuthError(Exception):
    """What the LIVE psycopg driver actually raises for a wrong password at CONNECT time (verified
    against psycopg 3.3): `.sqlstate is None`, the only signal is the message. NO password in it —
    libpq refuses to echo it — but host + user are present, so it must stay internal."""
    sqlstate = None

    def __init__(self):
        super().__init__('connection failed: connection to server at "127.0.0.1", port 5432 '
                         'failed: FATAL:  password authentication failed for user "postgres"')


class _NetError(Exception):
    sqlstate = None             # no SQLSTATE — the connect never reached a server


class TestProbeConnReason(unittest.TestCase):
    def test_db_s1_probe_conn_reason_auth(self):
        with _mock_psycopg(_FakePsycopg(connect_error=_AuthError("password authentication failed"))):
            res = teamdb.probe("postgres://h/db")
        self.assertFalse(res.reachable)
        self.assertEqual(res.conn_reason, teamdb.CONN_AUTH_FAILED)

    def test_db_s1_probe_conn_reason_auth_from_connect_message(self):
        # The live-driver case: NO sqlstate, auth is only in the message. Must still be AUTH.
        with _mock_psycopg(_FakePsycopg(connect_error=_RealAuthError())):
            res = teamdb.probe("postgres://h/db")
        self.assertEqual(res.conn_reason, teamdb.CONN_AUTH_FAILED)
        # …and the auth-vs-network split must be visible to the classifier as distinct axes.
        self.assertEqual(db_doctor.classify(res, _direct(), _ENV).primary.axis, db_doctor.AXIS_AUTH)

    def test_db_s1_probe_conn_reason_network(self):
        with _mock_psycopg(_FakePsycopg(connect_error=_NetError("could not translate host name"))):
            res = teamdb.probe("postgres://h/db")
        self.assertFalse(res.reachable)
        self.assertEqual(res.conn_reason, teamdb.CONN_NETWORK_UNREACHABLE)

    def test_db_s1_probe_conn_reason_network_for_bare_oserror(self):
        with _mock_psycopg(_FakePsycopg(connect_error=OSError("connection refused"))):
            res = teamdb.probe("postgres://h/db")
        self.assertEqual(res.conn_reason, teamdb.CONN_NETWORK_UNREACHABLE)

    def test_db_s1_probe_conn_reason_timeout(self):
        with _mock_psycopg(_FakePsycopg(connect_sleep=2.0)):
            res = teamdb.probe("postgres://h/db", budget_ms=120)
        self.assertEqual(res.conn_reason, teamdb.CONN_TIMEOUT)

    def test_db_s1_probe_conn_reason_empty_when_reachable(self):
        conn = _FakeConn(rows_for={"mokata_schema_version":
                                   [(teamdb.TEAM_SCHEMA_VERSION, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)]})
        with _mock_psycopg(_FakePsycopg(conn=conn)):
            res = teamdb.probe("postgres://h/db")
        self.assertTrue(res.reachable)
        self.assertEqual(res.conn_reason, "")


# ============================================================ 3) doctor wiring (section + exit + solo)
def _repo(d, *, mode=None):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if mode is not None:
        import json
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return Surface.load(d)


@contextmanager
def _doctor(d, home, env):
    """Run cmd_doctor over repo `d`, capture (rc, stdout). `env` overrides os.environ transiently."""
    from mokata.cli_commands.diagnostics import cmd_doctor
    args = argparse.Namespace(path=d, home=home, matrix=False)
    buf = io.StringIO()
    old_env = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(buf):
            rc = cmd_doctor(args)
        yield rc, buf.getvalue()
    finally:
        os.environ.clear()
        os.environ.update(old_env)


class TestDoctorWiring(unittest.TestCase):
    def test_db_s1_doctor_section_team(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d, mode="team")
            conn = _FakeConn(rows_for={"mokata_schema_version":
                                       [(teamdb.TEAM_SCHEMA_VERSION,
                                         teamdb.TEAM_SCHEMA_MIN_SUPPORTED)]})
            with _mock_psycopg(_FakePsycopg(conn=conn)):
                with _doctor(d, home, {_ENV: _SECRET_DSN, "NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertIn("database (team DSN):", out)
            self.assertIn("team DB OK", out)
            self.assertEqual(rc, 0, out)                  # healthy → doctor OK
            for secret in _SECRETS:                        # secret-safety on real stdout
                self.assertNotIn(secret, out)

    def test_db_s1_doctor_exit_auth_non_ok(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d, mode="team")
            with _mock_psycopg(_FakePsycopg(connect_error=_AuthError("auth failed"))):
                with _doctor(d, home, {_ENV: _SECRET_DSN, "NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertIn("database (team DSN):", out)
            self.assertIn(db_doctor.AXIS_AUTH, out)        # the terse table row (db-auth)
            self.assertIn("AUTHENTICATION FAILED", out)    # the actionable section line
            self.assertEqual(rc, 1)                        # a hard auth failure flips the exit

    def test_db_s1_doctor_exit_network_non_ok(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d, mode="team")
            with _mock_psycopg(_FakePsycopg(connect_error=OSError("refused"))):
                with _doctor(d, home, {_ENV: _SECRET_DSN, "NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertIn(db_doctor.AXIS_NETWORK, out)
            self.assertEqual(rc, 1)

    def test_db_s1_doctor_exit_schema_absent_non_ok(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d, mode="team")

            class _Undef(Exception):
                sqlstate = "42P01"

            conn = _FakeConn(error_for={"mokata_schema_version": _Undef("no table")})
            with _mock_psycopg(_FakePsycopg(conn=conn)):
                with _doctor(d, home, {_ENV: _SECRET_DSN, "NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertIn(db_doctor.AXIS_SCHEMA_ABSENT, out)
            self.assertEqual(rc, 1)

    def test_db_s1_doctor_exit_pooler_only_ok(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d, mode="team")
            conn = _FakeConn(rows_for={"mokata_schema_version":
                                       [(teamdb.TEAM_SCHEMA_VERSION,
                                         teamdb.TEAM_SCHEMA_MIN_SUPPORTED)]})
            with _mock_psycopg(_FakePsycopg(conn=conn)):
                with _doctor(d, home, {_ENV: _POOLED_DSN, "NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertIn(db_doctor.AXIS_POOLER, out)      # the pooler warning shows…
            self.assertEqual(rc, 0, out)                   # …but a reachable+healthy DB stays OK

    def test_db_s1_doctor_solo_silent(self):
        # A solo / no-DSN repo must render NO db section and never reach the DB.S1 probe (P8).
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            _repo(d)                                       # default = local mode
            probed = []
            orig = teamdb.probe

            def _spy(dsn, **kw):
                probed.append(dsn)
                return orig(dsn, **kw)

            with mock.patch.object(teamdb, "probe", _spy):
                # no team DSN in the environment → a truly solo repo.
                with _doctor(d, home, {"NO_COLOR": "1"}) as (rc, out):
                    pass
            self.assertNotIn("database (team DSN):", out)  # zero section noise (P8)
            self.assertEqual(probed, [])                   # zero network probe (no DSN to probe)
            self.assertEqual(rc, 0, out)


# ================================================================= 4) bounded (never hangs doctor)
class TestBounded(unittest.TestCase):
    def test_db_s1_probe_bounded(self):
        # A hanging CONNECT (psycopg sleeps far past the budget) → the deep-check still returns
        # promptly, reusing teamdb.probe's daemon-thread bound, and names a NETWORK/timeout failure.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            with _mock_psycopg(_FakePsycopg(connect_sleep=2.0)):
                start = time.monotonic()
                check = db_doctor.deep_check(surface, environ={_ENV: _SECRET_DSN}, budget_ms=150)
                elapsed = (time.monotonic() - start) * 1000
        self.assertIsNotNone(check)
        self.assertEqual(check.primary.axis, db_doctor.AXIS_NETWORK)
        self.assertLess(elapsed, 1200, "deep-check exceeded the probe's wall-clock budget")


# ============================================================ 5) deep_check gating (team vs solo)
class TestDeepCheckGating(unittest.TestCase):
    def test_db_s1_deep_check_local_returns_none_no_probe(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                             # local
            called = []
            self.assertIsNone(db_doctor.deep_check(
                surface, environ={_ENV: _SECRET_DSN},
                probe=lambda dsn: called.append(dsn) or _res()))
            self.assertEqual(called, [])

    def test_db_s1_deep_check_team_unset_env_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            self.assertIsNone(db_doctor.deep_check(surface, environ={}))

    def test_db_s1_deep_check_team_probes_and_classifies(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            check = db_doctor.deep_check(surface, environ={_ENV: _SECRET_DSN},
                                         probe=lambda dsn: _res())
            self.assertIsNotNone(check)
            self.assertEqual(check.primary.axis, db_doctor.AXIS_OK)


if __name__ == "__main__":
    unittest.main()
