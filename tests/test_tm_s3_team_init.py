"""TM.S3 — `mokata team init`: first-time team setup (the SOLE owner of DDL).

Freezes the contract (psycopg is mocked with a faithful in-memory Postgres — there is no live
DB in CI):

  1. `team init` provisions the shared schema the backends use (mokata_memory /
     mokata_session_bundle / mokata_audit_log / mokata_events) + INSERTs the
     mokata_schema_version row (doc 48 E5 / C4) — all DDL owned HERE;
  2. it is IDEMPOTENT — a re-run errors nothing, duplicates no rows/tables (IF NOT EXISTS /
     ON CONFLICT);
  3. the DSN VALUE is never written to the manifest (env-var only; secret-scanned);
  4. team project identity is PINNED (settings.project.id) so clients don't split by path-hash
     (doc 48 C2 / P-7);
  5. after provisioning, the TM.S2 probe reports reachable + compatible = CONNECTED;
  6. fail-closed with a NAMED fix when $MOKATA_PG_DSN is unset — nothing written.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR
from mokata.init import init_repo


# ------------------------------------------------------ a faithful in-memory Postgres fake
class _UndefinedTable(Exception):
    sqlstate = "42P01"


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakePgConn:
    """Enough of a Postgres to drive provision → probe end-to-end: tracks which tables exist
    and the schema-version row, honours IF NOT EXISTS / ON CONFLICT idempotently, and raises
    42P01 for a SELECT against an absent schema-version table."""

    def __init__(self):
        self.closed = 0
        self.executed = []
        self._tables = set()
        self._version = None
        self._min_supported = None      # D2 — the artifact's declared floor
        self._scope_backfilled = False  # DB.S2b — set true by the provisioning version row

    def execute(self, sql, *args):
        self.executed.append(sql)
        low = sql.strip().lower()
        m = re.search(r"create table if not exists (\w+)", low)
        if m:
            self._tables.add(m.group(1))
            return _Cursor([])
        if low.startswith("alter table"):
            return _Cursor([])                       # ADD COLUMN IF NOT EXISTS — idempotent
        if low.startswith("insert into mokata_schema_version"):
            # D2 — the RANGE artifact: VALUES (current, min_supported), ON CONFLICT DO UPDATE.
            # DB.S2b added a third value, the backfill stamp, so the shape is (v, min, TRUE).
            num = re.search(r"values\s*\((\d+),\s*(\d+)(?:,\s*(\w+))?\)", low)
            if num:
                self._version = int(num.group(1))
                self._min_supported = int(num.group(2))
                self._scope_backfilled = (num.group(3) or "false") == "true"
            return _Cursor([])
        if low.startswith("select 1"):
            return _Cursor([(1,)])
        if "from mokata_schema_version" in low:
            if "mokata_schema_version" not in self._tables:
                raise _UndefinedTable("relation \"mokata_schema_version\" does not exist")
            if self._version is None:
                return _Cursor([])
            if "min_supported" in low:
                return _Cursor([(self._version, self._min_supported)])
            return _Cursor([(self._version,)])
        return _Cursor([])

    def close(self):
        self.closed = 1


class _FakePsycopg:
    def __init__(self, conn):
        self.conn = conn
        self.connect_calls = []
        # a real ModuleSpec so `importlib.util.find_spec("psycopg")` (driver detection) sees it.
        import importlib.machinery
        self.__spec__ = importlib.machinery.ModuleSpec("psycopg", loader=None)

    def connect(self, dsn, **kwargs):
        self.connect_calls.append((dsn, kwargs))
        return self.conn


@contextmanager
def _mock_pg(conn=None):
    conn = conn or _FakePgConn()
    old = sys.modules.get("psycopg")
    sys.modules["psycopg"] = _FakePsycopg(conn)
    from mokata.memory import _pg
    _pg.reset_manager()
    try:
        yield conn
    finally:
        if old is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = old
        _pg.reset_manager()


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


def _manifest(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return json.load(fh)


def _manifest_bytes(d):
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), "rb") as fh:
        return fh.read()


_DSN = "postgres://app_user:tok3n_pw@db.internal:5432/team"


# =============================================================== the DDL (teamdb.provision_sql)
class TestProvisionSql(unittest.TestCase):
    def test_provisions_every_shared_table(self):
        from mokata import teamdb
        sql = "\n".join(teamdb.provision_sql())
        for table in (teamdb.MEMORY_TABLE, teamdb.SESSION_TABLE, teamdb.AUDIT_TABLE,
                      teamdb.EVENTS_TABLE, teamdb.SCHEMA_VERSION_TABLE):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql,
                          f"{table} not provisioned")

    def test_inserts_the_schema_version_row(self):
        from mokata import teamdb
        sql = "\n".join(teamdb.provision_sql())
        self.assertRegex(sql, rf"INSERT INTO {teamdb.SCHEMA_VERSION_TABLE}")
        self.assertIn(str(teamdb.TEAM_SCHEMA_VERSION), sql)

    def test_every_statement_is_idempotent_shaped(self):
        """`team init` is re-run to upgrade, so EVERY statement must survive a re-run untouched.

        FOUR shapes qualify. `IF NOT EXISTS` and `ON CONFLICT` are the DDL ones. DB.S2b adds the
        third: a data-migration `UPDATE` cannot use either, so it is made idempotent by PREDICATE
        instead — a `WHERE` that describes the un-migrated state, so a second run matches zero
        rows. The `IS DISTINCT FROM` requirement is what makes that check meaningful rather than a
        keyword rubber-stamp: an unconditional `UPDATE … SET` (no WHERE) would rewrite every row on
        every init, and must still fail this test.

        DB.S7a adds the fourth, and it is a genuinely new SHAPE rather than a loosening. The v5 edge
        migration inserts DERIVED rows (one per ref in one doc-JSON array), so there is nothing to
        `UPDATE` and no key to `ON CONFLICT` on — the natural target is the PARTIAL unique index,
        which the two engines spell differently. Its idempotency is the same idea as DB.S2b's
        expressed for an INSERT: `WHERE NOT EXISTS (the row this would create already exists and is
        still open)`, so a second run matches zero rows. The check stays meaningful for exactly the
        DB.S2b reason — a bare `INSERT INTO … SELECT` with no `NOT EXISTS` re-inserts the whole
        projection on every init and still fails here.

        M-1/R9 adds the fifth: the HOLE-FILL update. Its backfill gives a migrated edge the approval
        id its item carries, and it deliberately cannot use DB.S2b's `IS DISTINCT FROM` — that
        predicate means "make this column match the source", which would OVERWRITE the id a
        live-projected edge already inherited from the flush with a derived one. It is idempotent
        the strictly stronger way instead: it only ever writes into a NULL (`SET col = … WHERE col
        IS NULL`), so a second run matches zero rows AND no run can ever change a value that is
        already there. The check stays meaningful for the same reason as the other two — the WHERE
        must constrain THE COLUMN BEING SET, so an unconditional `UPDATE … SET` (or one guarded on
        some unrelated column) still fails here."""
        import re

        from mokata import teamdb
        for stmt in teamdb.provision_sql():
            up = " ".join(stmt.upper().split())
            set_cols = re.findall(r"SET\s+([A-Z_][A-Z0-9_]*)\s*=", up)
            hole_fill = (up.startswith("UPDATE") and bool(set_cols)
                         and all(re.search(rf"\b{col}\s+IS NULL\b", up) for col in set_cols))
            guarded_update = up.startswith("UPDATE") and ("IS DISTINCT FROM" in up or hole_fill)
            guarded_insert = (up.startswith("INSERT") and " SELECT " in up
                              and "NOT EXISTS" in up)
            ok = (("IF NOT EXISTS" in up) or ("ON CONFLICT" in up)
                  or guarded_update or guarded_insert)
            self.assertTrue(ok, f"non-idempotent DDL: {stmt}")

    def test_no_extensions_on_the_golden_path(self):
        from mokata import teamdb
        sql = "\n".join(teamdb.provision_sql()).upper()
        self.assertNotIn("CREATE EXTENSION", sql)     # vanilla Postgres — no pgvector required


class TestProvision(unittest.TestCase):
    def test_provision_then_probe_reports_connected(self):
        from mokata import teamdb
        with _mock_pg() as conn:
            prov = teamdb.provision(_DSN, project_id="p_test")
            self.assertEqual(prov.version, teamdb.TEAM_SCHEMA_VERSION)
            # DDL actually ran (the version table + the version row landed).
            self.assertTrue(any("mokata_schema_version" in s.lower() for s in conn.executed))
            res = teamdb.probe(_DSN)
        self.assertTrue(res.reachable)
        self.assertTrue(res.compatible)
        self.assertEqual(res.schema_version, teamdb.TEAM_SCHEMA_VERSION)

    def test_reprovision_is_idempotent(self):
        from mokata import teamdb
        with _mock_pg() as conn:
            teamdb.provision(_DSN, project_id="p_test")
            n_version_rows_first = sum(
                1 for s in conn.executed if s.lower().startswith("insert into mokata_schema_version"))
            teamdb.provision(_DSN, project_id="p_test")     # re-run: no error
            res = teamdb.probe(_DSN)
        # the fake keeps ONE version row despite a second insert (ON CONFLICT DO NOTHING).
        self.assertEqual(res.schema_version, teamdb.TEAM_SCHEMA_VERSION)
        self.assertGreaterEqual(n_version_rows_first, 1)


# ==================================================================== team_init orchestration
class TestTeamInit(unittest.TestCase):
    def test_dsn_unset_fails_closed_and_writes_nothing(self):
        from mokata import team as T
        from mokata.config import Surface
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            before = _manifest_bytes(d)
            out = []
            res = T.team_init(d, Surface.load(d), environ={}, assume_yes=True,
                              out=out.append)
            self.assertFalse(res.ok)
            self.assertEqual(before, _manifest_bytes(d))   # nothing written
            self.assertIn("MOKATA_PG_DSN", "\n".join(out))  # the named fix

    def test_success_provisions_pins_project_id_and_connects(self):
        from mokata import team as T, teamdb
        from mokata.config import Surface
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _mock_pg():
                res = T.team_init(d, Surface.load(d), environ={"MOKATA_PG_DSN": _DSN},
                                  assume_yes=True, out=lambda _s: None)
            self.assertTrue(res.ok, res.message)
            self.assertTrue(res.connected)
            # project.id pinned into the manifest (C2 / P-7).
            pid = (_manifest(d).get("settings") or {}).get("project", {}).get("id")
            self.assertTrue(pid, "settings.project.id not pinned")
            self.assertEqual(res.project_id, pid)

    def test_dsn_value_never_written_to_the_manifest(self):
        from mokata import team as T
        from mokata.config import Surface
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _mock_pg():
                T.team_init(d, Surface.load(d), environ={"MOKATA_PG_DSN": _DSN},
                            assume_yes=True, out=lambda _s: None)
            blob = _manifest_bytes(d).decode()
            # neither the secret nor the host lands in the committed manifest.
            self.assertNotIn("tok3n_pw", blob)
            self.assertNotIn("db.internal", blob)

    def test_rerun_is_idempotent(self):
        from mokata import team as T
        from mokata.config import Surface
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with _mock_pg():
                T.team_init(d, Surface.load(d), environ={"MOKATA_PG_DSN": _DSN},
                            assume_yes=True, out=lambda _s: None)
                after1 = _manifest_bytes(d)
                res2 = T.team_init(d, Surface.load(d), environ={"MOKATA_PG_DSN": _DSN},
                                   assume_yes=True, out=lambda _s: None)
            self.assertTrue(res2.ok)
            self.assertEqual(after1, _manifest_bytes(d))    # already pinned → no re-write


if __name__ == "__main__":
    unittest.main()
