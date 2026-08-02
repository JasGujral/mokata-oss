"""TM.S2 — shared-DB connection manager + the real ≤500ms preflight probe.

Freezes the connection layer's contract (psycopg is mocked — there is no live Postgres in CI):

  1. every connect carries a `connect_timeout` AND a `statement_timeout` (doc 48 E2 — no
     unbounded hangs; `memory/_pg.py` passed neither before this stage);
  2. the per-process manager shares ONE named connection per DSN across all consumers (doc 48
     E4), and reconnects a dead cached connection;
  3. the probe classifies reachable / unreachable-timeout / schema-absent / incompatible-version
     — each a distinct, bounded verdict — and NEVER runs DDL (SELECT only);
  4. the probe is wall-clock bounded to its budget even when connect hangs.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib.util
import sys
import time
import unittest
from contextlib import contextmanager
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)


# ------------------------------------------------------------- a tiny psycopg stand-in
class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _UndefinedTable(Exception):
    sqlstate = "42P01"          # Postgres "relation does not exist"


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
        if sql.strip().upper() == "SELECT 1":
            return _FakeCursor([(1,)])     # the reachability round-trip — the ONE untaught answer
        # A double that answers questions it was never taught is a LIAR. The old silent
        # `_FakeCursor([(1,)])` fallback let an untaught (or plain WRONG) connection fabricate
        # "the shared schema is v1" — see WIN-DOCTOR-SCHEMA. Do NOT restore it.
        raise AssertionError(
            f"_FakeConn was asked SQL it was never taught: {sql!r}. Teach it via "
            f"rows_for=/error_for= — do NOT restore a silent default.")

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


_SCHEMA = "mokata_schema_version"


@contextmanager
def _no_psycopg():
    """Simulate the optional driver being ABSENT, deterministically — regardless of whether
    psycopg is actually installed. `sys.modules['psycopg'] = None` forces `import psycopg` to
    raise ImportError; patching `find_spec` makes discovery report it missing too."""
    from mokata.memory import _pg
    old = sys.modules.get("psycopg")
    sys.modules["psycopg"] = None
    _pg.reset_manager()
    real_find_spec = importlib.util.find_spec

    def _find_spec(name, *a, **k):
        return None if name == "psycopg" else real_find_spec(name, *a, **k)

    with mock.patch("importlib.util.find_spec", side_effect=_find_spec):
        try:
            yield
        finally:
            if old is None:
                sys.modules.pop("psycopg", None)
            else:
                sys.modules["psycopg"] = old
            _pg.reset_manager()


# =========================================================== the connection manager (E4 + E2)
class TestConnectionManager(unittest.TestCase):
    def test_connect_carries_both_timeouts(self):
        from mokata.memory import _pg
        fake = _FakePsycopg()
        with _mock_psycopg(fake):
            _pg.get_connection("postgres://h/db", RuntimeError)
        self.assertEqual(len(fake.connect_calls), 1)
        _dsn, kwargs = fake.connect_calls[0]
        # doc 48 E2 — a connect_timeout AND a statement_timeout on EVERY connect.
        self.assertIn("connect_timeout", kwargs)
        self.assertGreater(kwargs["connect_timeout"], 0)
        self.assertIn("options", kwargs)
        self.assertIn("statement_timeout", kwargs["options"])

    def test_one_shared_connection_per_dsn(self):
        from mokata.memory import _pg
        fake = _FakePsycopg()
        with _mock_psycopg(fake):
            a = _pg.get_connection("postgres://h/db", RuntimeError)
            b = _pg.get_connection("postgres://h/db", RuntimeError)
        self.assertIs(a, b)                              # E4 — one managed connection
        self.assertEqual(len(fake.connect_calls), 1)     # not reconnected

    def test_distinct_dsns_get_distinct_connections(self):
        from mokata.memory import _pg
        fake = _FakePsycopg(conn=None)
        # each DSN yields its own connect call; use two fakes to prove separateness.
        c1, c2 = _FakeConn(), _FakeConn()

        class _Multi:
            def __init__(self):
                self.connect_calls = []
                self._conns = [c1, c2]

            def connect(self, dsn, **kwargs):
                self.connect_calls.append((dsn, kwargs))
                return self._conns[len(self.connect_calls) - 1]

        with _mock_psycopg(_Multi()):
            a = _pg.get_connection("postgres://h/a", RuntimeError)
            b = _pg.get_connection("postgres://h/b", RuntimeError)
        self.assertIsNot(a, b)

    def test_dead_cached_connection_is_replaced(self):
        from mokata.memory import _pg
        dead, live = _FakeConn(), _FakeConn()

        class _Reconnect:
            def __init__(self):
                self.connect_calls = []
                self._conns = [dead, live]

            def connect(self, dsn, **kwargs):
                self.connect_calls.append((dsn, kwargs))
                return self._conns[len(self.connect_calls) - 1]

        with _mock_psycopg(_Reconnect()):
            a = _pg.get_connection("postgres://h/db", RuntimeError)
            dead.closed = 1                              # the cached conn died
            b = _pg.get_connection("postgres://h/db", RuntimeError)
        self.assertIs(a, dead)
        self.assertIs(b, live)                           # reconnected, not the dead one

    def test_missing_driver_raises_unavailable(self):
        from mokata.memory import _pg
        # psycopg absent → the typed unavailable, never a hard crash.
        with _no_psycopg():
            with self.assertRaises(RuntimeError):
                _pg.get_connection("postgres://h/db", RuntimeError)

    def test_connect_psycopg_runs_NO_ddl(self):
        # D1 — this test used to assert the OPPOSITE: that `connect_psycopg(setup_sql=…)` ran the
        # caller's DDL on every connect. That contract WAS the bug (a DML-only role is denied
        # CREATE even on a provisioned DB → silent SQLite fallback). The parameter is gone; a
        # runtime connect VERIFIES the schema and writes none of it.
        import inspect

        from mokata import teamdb
        from mokata.memory import _pg
        self.assertNotIn("setup_sql", inspect.signature(_pg.connect_psycopg).parameters)

        conn = _FakeConn(rows_for={_SCHEMA: [(teamdb.TEAM_SCHEMA_VERSION,
                                              teamdb.TEAM_SCHEMA_MIN_SUPPORTED)]})
        teamdb.reset_schema_cache()
        with _mock_psycopg(_FakePsycopg(conn=conn)):
            got = _pg.connect_psycopg("postgres://h/db", RuntimeError)
        teamdb.reset_schema_cache()
        self.assertIs(got, conn)
        ddl = [s for s in conn.executed
               if s.strip().upper().startswith(("CREATE ", "ALTER ", "DROP "))]
        self.assertEqual([], ddl, f"a runtime connect must run ZERO DDL, ran: {ddl}")


# =========================================================== the probe (reachability + schema)
class TestProbe(unittest.TestCase):
    def test_reachable_and_compatible(self):
        from mokata import teamdb
        conn = _FakeConn(rows_for={_SCHEMA: [(teamdb.TEAM_SCHEMA_VERSION,)]})
        with _mock_psycopg(_FakePsycopg(conn=conn)):
            res = teamdb.probe("postgres://h/db")
        self.assertTrue(res.driver_present)
        self.assertTrue(res.reachable)
        self.assertTrue(res.schema_present)
        self.assertEqual(res.schema_version, teamdb.TEAM_SCHEMA_VERSION)
        self.assertTrue(res.compatible)
        # the probe NEVER runs DDL — SELECT only.
        self.assertTrue(all("SELECT" in s.upper() for s in conn.executed), conn.executed)

    def test_unreachable_connect_error(self):
        from mokata import teamdb
        with _mock_psycopg(_FakePsycopg(connect_error=OSError("connection refused"))):
            res = teamdb.probe("postgres://h/db")
        self.assertFalse(res.reachable)
        self.assertFalse(res.compatible)

    def test_unreachable_is_wall_clock_bounded_when_connect_hangs(self):
        from mokata import teamdb
        # connect sleeps far past the budget → the probe must still return promptly.
        # Its OWN DSN, never the shared `postgres://h/db`: the abandoned worker outlives the
        # budget and installs a bare `_FakeConn` into the process-global `_pg._MANAGER` ~2s
        # later, defeating the `reset_manager()` teardown already ran. Sharing the key poisons
        # every later probe on it (WIN-DOCTOR-SCHEMA, same landmine, different file).
        with _mock_psycopg(_FakePsycopg(connect_sleep=2.0)):
            start = time.monotonic()
            res = teamdb.probe("postgres://hang-h/db", budget_ms=150)
            elapsed = (time.monotonic() - start) * 1000
        self.assertFalse(res.reachable)
        self.assertLess(elapsed, 1000, "probe exceeded its wall-clock budget")

    def test_schema_absent(self):
        from mokata import teamdb
        conn = _FakeConn(error_for={_SCHEMA: _UndefinedTable("no such table")})
        with _mock_psycopg(_FakePsycopg(conn=conn)):
            res = teamdb.probe("postgres://h/db")
        self.assertTrue(res.reachable)          # the DB answered SELECT 1
        self.assertFalse(res.schema_present)    # but the schema-version table is absent
        self.assertFalse(res.compatible)

    def test_incompatible_version(self):
        from mokata import teamdb
        bad = teamdb.TEAM_SCHEMA_VERSION + 41
        conn = _FakeConn(rows_for={_SCHEMA: [(bad,)]})
        with _mock_psycopg(_FakePsycopg(conn=conn)):
            res = teamdb.probe("postgres://h/db")
        self.assertTrue(res.reachable)
        self.assertTrue(res.schema_present)
        self.assertEqual(res.schema_version, bad)
        self.assertFalse(res.compatible)        # version mismatch

    def test_driver_absent(self):
        from mokata import teamdb
        with _no_psycopg():
            res = teamdb.probe("postgres://h/db")
        self.assertFalse(res.driver_present)
        self.assertFalse(res.reachable)


if __name__ == "__main__":
    unittest.main()
