"""D1 + D2 — NO RUNTIME DDL + a SCHEMA-VERSION RANGE (one contract: `team init` owns DDL).

D1. Every runtime connect used to run its own hand-mirrored `CREATE TABLE …` / `ALTER TABLE …
    ADD COLUMN …` (memory/backends, memory/vector, session_transport, team_audit) through
    `_pg.connect_psycopg(setup_sql=…)`. A properly-locked-down DML-only runtime role — the very
    two-role model `mokata team init` RECOMMENDS — is denied CREATE (SQLSTATE 42501) even on a
    fully provisioned, current-schema database (Postgres checks the schema ACL *before* the
    IF-NOT-EXISTS short-circuit, and ADD COLUMN IF NOT EXISTS demands table ownership). The
    denial surfaced as `PostgresUnavailable("database unavailable: …")` → the memory selection
    caught it → the SQLite floor. The team believed they shared memory; they did not.

    After D1: runtime connections are VERIFY-ONLY. One cheap, cached schema probe (E2), ZERO DDL
    statements reachable from any runtime path (AST-guarded below), and a missing/incompatible
    schema is a LOUD CM.S2 degrade naming the exact `mokata team init` remediation — never a
    silent SQLite fallback. The hand-mirrored schema copies are deleted: `teamdb` is the single
    source of schema truth, and it lives in the init path.

D2. The compatibility check was `version == TEAM_SCHEMA_VERSION` — so ANY schema bump partitioned
    the team into two islands mid-upgrade (the upgraded client refuses until the DB migrates; the
    migrated DB then refuses every client still on the old build). After D2 the version artifact
    carries a RANGE `(min_supported, current)` and a runtime accepts any version in range —
    warning when behind, degrading LOUDLY below min, and REFUSING loudly (never silently) when the
    shared schema is ahead of what this build speaks.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import importlib.machinery
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, degrade, team, teamdb
from mokata.config import Surface
from mokata.init import init_repo

_DSN = "postgres://secret-host/db"
CUSTOM = "MOKATA_TEAM_D1_DSN"


# ============================================================== a psycopg stand-in with ROLES
class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _InsufficientPrivilege(Exception):
    """What a DML-only role actually gets back from Postgres on CREATE/ALTER."""
    sqlstate = "42501"


class _UndefinedTable(Exception):
    sqlstate = "42P01"          # relation does not exist


class _UndefinedColumn(Exception):
    sqlstate = "42703"          # column does not exist (the LEGACY single-version artifact)


_DDL_VERBS = ("CREATE ", "ALTER ", "DROP ")


class _DmlOnlyConn:
    """A connection held by a least-privilege DML-only role against a FULLY PROVISIONED,
    CURRENT-schema database: SELECT/INSERT/UPDATE/DELETE succeed, any DDL is denied 42501.

    `version_row` is what `mokata_schema_version` holds. `legacy_artifact=True` simulates a
    database provisioned by an OLDER mokata: the `min_supported` column does not exist, so a
    SELECT naming it raises 42703 (undefined column)."""

    def __init__(self, *, version_row=None, legacy_artifact=False, schema_absent=False,
                 vector_present=True):
        self.closed = 0
        self.executed = []
        self.ddl_attempts = []
        self._version_row = version_row
        self._legacy = legacy_artifact
        self._absent = schema_absent
        self._vector = vector_present

    def execute(self, sql, *args):
        self.executed.append(sql)
        head = sql.strip().upper()
        if head.startswith(_DDL_VERBS):
            self.ddl_attempts.append(sql)
            raise _InsufficientPrivilege("permission denied for schema public")
        if teamdb.SCHEMA_VERSION_TABLE in sql:
            if self._absent:
                raise _UndefinedTable("relation \"mokata_schema_version\" does not exist")
            if self._legacy and "min_supported" in sql:
                raise _UndefinedColumn("column \"min_supported\" does not exist")
            return _Cursor([self._version_row] if self._version_row else [])
        if "to_regclass" in sql:
            return _Cursor([(self._vector,)])
        if teamdb.MEMORY_TABLE in sql:
            return _Cursor([])          # a real, permitted SELECT — this row simply isn't there
        return _Cursor([(1,)])

    def close(self):
        self.closed = 1


class _FakePsycopg:
    def __init__(self, conn):
        self.conn = conn
        self.connect_calls = []
        # `driver_present()` / the probe ask `importlib.util.find_spec("psycopg")`, which reads
        # `__spec__` off an already-imported module — a fake without one reads as ABSENT.
        self.__spec__ = importlib.machinery.ModuleSpec("psycopg", None)

    def connect(self, dsn, **kwargs):
        self.connect_calls.append((dsn, kwargs))
        return self.conn


@contextmanager
def _psycopg(conn):
    """Install the fake driver + start from cold caches (the connection manager AND the D1
    per-process schema-verify cache), so each test's probe count is its own."""
    from mokata.memory import _pg
    old = sys.modules.get("psycopg")
    sys.modules["psycopg"] = _FakePsycopg(conn)
    _pg.reset_manager()
    teamdb.reset_schema_cache()
    try:
        yield conn
    finally:
        if old is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = old
        _pg.reset_manager()
        teamdb.reset_schema_cache()


def _current_row():
    """The version artifact a CURRENT `team init` writes: (current, min_supported)."""
    return (teamdb.TEAM_SCHEMA_VERSION, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)


# ==================================================================== D1 · zero runtime DDL
class TestNoRuntimeDDLGuard(unittest.TestCase):
    """The grep/AST guard (SI.4's pattern): DDL may exist ONLY in the init path.

    Allowed: `teamdb.py` — the single source of schema truth, run by `team init` alone; and
    `SQLiteBackend` in memory/backends.py — the LOCAL floor, a per-repo file mokata wholly owns
    (no roles, no shared DB, nothing to lock down). ANY other DDL site is a D1 regression."""

    ALLOWED = {("teamdb.py", None), ("memory/backends.py", "SQLiteBackend")}

    def _ddl_sites(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata")
        sites = []
        for root, _dirs, files in os.walk(src):
            for fn in sorted(f for f in files if f.endswith(".py")):
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, src).replace(os.sep, "/")
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for cls, node in _walk_strings(tree):
                    for part in _string_parts(node):
                        up = part.upper()
                        if any(v in up for v in ("CREATE TABLE", "CREATE INDEX",
                                                 "CREATE UNIQUE INDEX", "CREATE EXTENSION",
                                                 "ALTER TABLE", "DROP TABLE")):
                            sites.append((rel, cls, part.strip()[:60]))
        return sites

    def test_no_ddl_outside_the_init_path(self):
        offenders = [(rel, cls, snip) for rel, cls, snip in self._ddl_sites()
                     if (rel, cls) not in self.ALLOWED]
        self.assertEqual([], offenders,
                         "DDL is reachable outside `team init` (teamdb) / the local SQLite floor. "
                         "A DML-only runtime role would be denied CREATE here (D1).")

    def test_the_guard_can_actually_see_ddl(self):
        # the guard is only worth something if it FINDS the DDL it allows.
        found = {(rel, cls) for rel, cls, _s in self._ddl_sites()}
        self.assertIn(("teamdb.py", None), found)
        self.assertIn(("memory/backends.py", "SQLiteBackend"), found)

    def test_the_deleted_mirrors_are_gone(self):
        # the hand-mirrored schema copies the D1 row says to delete: no runtime backend may
        # carry its own copy of the shared schema any more.
        from mokata import session_transport, team_audit
        from mokata.memory import backends, vector
        for cls in (backends.PostgresBackend, vector.PgVectorBackend,
                    session_transport.PostgresTransport, team_audit.SharedAuditLog):
            self.assertFalse(hasattr(cls, "_setup_sql"),
                             f"{cls.__name__} still hand-mirrors the schema (_setup_sql)")
            self.assertFalse(hasattr(cls, "_create_sql"),
                             f"{cls.__name__} still hand-mirrors the schema (_create_sql)")

    def test_connect_psycopg_cannot_carry_ddl(self):
        # the vehicle itself is gone: the `setup_sql` parameter that used to smuggle DDL onto
        # every runtime connect no longer exists.
        import inspect

        from mokata.memory import _pg
        params = inspect.signature(_pg.connect_psycopg).parameters
        self.assertNotIn("setup_sql", params)


def _walk_strings(tree):
    """Yield (enclosing-class-name, node) for every str Constant / f-string, SKIPPING docstrings
    (prose that merely NAMES a DDL statement is documentation, not an executable site)."""
    def walk(node, cls):
        cls = node.name if isinstance(node, ast.ClassDef) else cls
        body = getattr(node, "body", None)
        skip = None       # the docstring STATEMENT (an Expr), not its inner Constant
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip = body[0]
        for child in ast.iter_child_nodes(node):
            if child is skip:
                continue
            if isinstance(child, (ast.Constant, ast.JoinedStr)):
                yield cls, child
            yield from walk(child, cls)
    yield from walk(tree, None)


def _string_parts(node):
    """The literal text of a Constant / the literal chunks of an f-string."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    return [v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)]


class TestRuntimeConnectIsVerifyOnly(unittest.TestCase):
    """A runtime connect PROBES the schema (SELECT) and runs nothing else."""

    def test_connect_runs_zero_ddl_and_verifies(self):
        from mokata.memory import _pg
        conn = _DmlOnlyConn(version_row=_current_row())
        with _psycopg(conn):
            got = _pg.connect_psycopg(_DSN, RuntimeError)
        self.assertIs(got, conn)
        self.assertEqual([], conn.ddl_attempts)
        self.assertTrue(any(teamdb.SCHEMA_VERSION_TABLE in s for s in conn.executed),
                        "a runtime connect must VERIFY the schema it depends on")

    def test_the_verify_probe_is_cached_per_process(self):
        # E2 one-probe discipline: N backend builds on one DSN cost ONE schema probe.
        from mokata.memory import _pg
        conn = _DmlOnlyConn(version_row=_current_row())
        with _psycopg(conn):
            for _ in range(4):
                _pg.connect_psycopg(_DSN, RuntimeError)
        probes = [s for s in conn.executed if teamdb.SCHEMA_VERSION_TABLE in s]
        self.assertEqual(1, len(probes), f"expected ONE cached schema probe, got {probes}")


class TestDmlOnlyRoleRegression(unittest.TestCase):
    """The D1 regression: a DML-only runtime role against a provisioned, current-schema DB."""

    def test_dml_only_role_serves_team_memory(self):
        # THE bug: this used to raise PostgresUnavailable (CREATE denied) → SQLite floor.
        from mokata.memory.backends import PostgresBackend
        conn = _DmlOnlyConn(version_row=_current_row())
        with _psycopg(conn):
            be = PostgresBackend(_DSN, project="p")
            self.assertEqual([], conn.ddl_attempts, "runtime must not attempt DDL")
            be.get("some-id")                     # a real read works on a DML-only role
        self.assertTrue(any(s.strip().upper().startswith("SELECT") for s in conn.executed))

    def test_dml_only_role_serves_every_shared_backend(self):
        from mokata import session_transport, team_audit
        from mokata.memory.vector import PgVectorBackend
        for build in (
            lambda: team_audit.SharedAuditLog(_DSN),
            lambda: session_transport.PostgresTransport(_DSN, project="p"),
            lambda: PgVectorBackend(_DSN, embedder=lambda _t: [0.0] * 8, dim=8),
        ):
            conn = _DmlOnlyConn(version_row=_current_row())
            with _psycopg(conn):
                build()
            self.assertEqual([], conn.ddl_attempts)

    def test_absent_schema_degrades_LOUDLY_naming_team_init(self):
        # not a silent fallback: the typed unavailable names the failure class + the fix.
        from mokata.memory.backends import PostgresBackend, PostgresUnavailable
        conn = _DmlOnlyConn(schema_absent=True)
        with _psycopg(conn):
            with self.assertRaises(PostgresUnavailable) as ctx:
                PostgresBackend(_DSN)
        exc = ctx.exception
        self.assertEqual(degrade.FAILURE_SCHEMA, getattr(exc, "failure_class", None))
        self.assertIn("mokata team init", str(exc))

    def test_the_store_marks_a_schema_failure_as_SCHEMA_not_unreachable(self):
        """The residual-race honesty hook (CM.S2): health said OK, the live build hit a schema
        failure → the read really did fall back, and the notice must name the SCHEMA class + the
        `mokata team init` fix — never `mokata sync` (the connection is perfectly healthy)."""
        from mokata.memory.backends import SQLiteBackend
        from mokata.memory.store import build_backend

        with tempfile.TemporaryDirectory() as root:
            init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
            routing = degrade.ReadRoutingDecision(
                mode="team", tool="postgres", served_by_team=True, degraded=False,
                verdict=None, env_name=CUSTOM)
            conn = _DmlOnlyConn(schema_absent=True)
            with _psycopg(conn):
                with _env({CUSTOM: _DSN}):
                    be = build_backend("postgres", root, {}, {"dsn_env": CUSTOM},
                                       routing=routing)
        self.assertIsInstance(_unwrap(be), SQLiteBackend)      # fell back to the floor …
        self.assertTrue(routing.degraded)                      # … and said so, LOUDLY
        self.assertEqual(degrade.FAILURE_SCHEMA, routing.notice.failure_class)
        rendered = routing.notice.render()
        self.assertIn("mokata team init", rendered)
        self.assertNotIn("mokata sync", rendered)
        self.assertNotIn(_DSN, rendered)                       # never the DSN VALUE (CM.S1)


def _unwrap(backend):
    """Peel CM.S3's JournalOverlay (team mode wraps whatever was resolved)."""
    return getattr(backend, "_backend", backend) or backend


@contextmanager
def _env(mapping):
    old = {k: os.environ.get(k) for k in mapping}
    os.environ.update(mapping)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ============================================================== D2 · the schema-version RANGE
class TestSchemaVersionRange(unittest.TestCase):
    """`compatibility(db_current, db_min_supported)` — ONE predicate, both directions."""

    def _c(self, db_current, db_min, speaks=None, client_min=None):
        return teamdb.compatibility(
            db_current, db_min,
            speaks=teamdb.TEAM_SCHEMA_VERSION if speaks is None else speaks,
            min_supported=teamdb.TEAM_SCHEMA_MIN_SUPPORTED if client_min is None else client_min)

    def test_equal_versions_are_compatible_and_silent(self):
        v = self._c(5, 3, speaks=5, client_min=3)
        self.assertTrue(v.compatible)
        self.assertEqual("", v.warning)

    def test_schema_behind_but_in_range_is_compatible_with_a_WARNING(self):
        # the upgrade window: this build speaks v5, the shared schema is still v4 (>= our min).
        # Work continues — a bump must not partition the team — but say so.
        v = self._c(4, 3, speaks=5, client_min=3)
        self.assertTrue(v.compatible)
        self.assertIn("team init", v.warning)

    def test_schema_below_min_supported_is_a_LOUD_schema_degrade(self):
        v = self._c(2, 1, speaks=5, client_min=3)
        self.assertFalse(v.compatible)
        self.assertEqual(teamdb.REASON_SCHEMA_TOO_OLD, v.reason)
        self.assertIn("mokata team init", teamdb.schema_fix(v.reason))

    def test_schema_ahead_but_still_serving_us_is_compatible_with_a_WARNING(self):
        # THE de-partitioning case: a teammate ran `team init` and moved the schema to v6, whose
        # artifact still serves v5 clients. This build (v5) KEEPS WORKING and advises an upgrade.
        v = self._c(6, 5, speaks=5, client_min=3)
        self.assertTrue(v.compatible)
        self.assertIn("upgrade mokata", v.warning)

    def test_schema_ahead_of_what_we_speak_is_a_LOUD_refusal(self):
        # the shared schema dropped support for v5 clients → refuse LOUDLY (D6-class: a client
        # older than the data must never silently read/write it), fix = upgrade mokata.
        v = self._c(7, 6, speaks=5, client_min=3)
        self.assertFalse(v.compatible)
        self.assertEqual(teamdb.REASON_CLIENT_TOO_OLD, v.reason)
        self.assertIn("upgrade mokata", teamdb.schema_fix(v.reason))

    def test_this_builds_range_covers_every_schema_its_sql_touches(self):
        # grounding pin: the live SQL SELECTs `revision` (schema v2) and touches no v3 column,
        # so v2 is genuinely the oldest schema this build can serve.
        self.assertEqual(3, teamdb.TEAM_SCHEMA_VERSION)
        self.assertEqual(2, teamdb.TEAM_SCHEMA_MIN_SUPPORTED)
        self.assertLessEqual(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, teamdb.TEAM_SCHEMA_VERSION)


class TestVersionArtifact(unittest.TestCase):
    def test_the_artifact_carries_the_range(self):
        sql = " || ".join(teamdb.provision_sql())
        self.assertIn("min_supported", sql)
        # …and the row init writes carries BOTH numbers.
        self.assertIn(f"({teamdb.TEAM_SCHEMA_VERSION}, {teamdb.TEAM_SCHEMA_MIN_SUPPORTED})", sql)

    def test_the_LEGACY_single_version_artifact_parses_as_IN_RANGE(self):
        # an existing deployment's `mokata_schema_version` has no `min_supported` column: the
        # SELECT naming it raises 42703. The probe must fall back and read it as in-range —
        # never explode, never refuse a team that is in fact perfectly compatible.
        conn = _DmlOnlyConn(version_row=(teamdb.TEAM_SCHEMA_VERSION,), legacy_artifact=True)
        with _psycopg(conn):
            res = teamdb.probe(_DSN)
        self.assertTrue(res.reachable)
        self.assertTrue(res.schema_present)
        self.assertEqual(teamdb.TEAM_SCHEMA_VERSION, res.schema_version)
        self.assertTrue(res.compatible, f"legacy artifact must parse as in-range: {res.detail}")
        # a legacy artifact declares no floor → its floor is its OWN version (it made no promise
        # about clients it never saw).
        self.assertEqual(teamdb.TEAM_SCHEMA_VERSION, res.schema_min_supported)

    def test_an_older_LEGACY_artifact_still_serves_this_build(self):
        conn = _DmlOnlyConn(version_row=(teamdb.TEAM_SCHEMA_MIN_SUPPORTED,), legacy_artifact=True)
        with _psycopg(conn):
            res = teamdb.probe(_DSN)
        self.assertTrue(res.compatible, res.detail)
        self.assertIn("team init", res.warning)

    def test_a_legacy_artifact_AHEAD_of_this_build_is_REFUSED_not_assumed_safe(self):
        # "unknown is not permission": a pre-D2 artifact 41 versions ahead declares no floor, so
        # this build must NOT infer that it is still served. Refuse loudly (D6-class) rather than
        # read/write columns it has never heard of.
        conn = _DmlOnlyConn(version_row=(teamdb.TEAM_SCHEMA_VERSION + 41,), legacy_artifact=True)
        with _psycopg(conn):
            res = teamdb.probe(_DSN)
        self.assertFalse(res.compatible)
        self.assertEqual(teamdb.REASON_CLIENT_TOO_OLD, res.reason)

    def test_probe_reports_the_range_reason_on_an_incompatible_schema(self):
        conn = _DmlOnlyConn(version_row=(1, 1))          # below this build's min (v2)
        with _psycopg(conn):
            res = teamdb.probe(_DSN)
        self.assertFalse(res.compatible)
        self.assertEqual(teamdb.REASON_SCHEMA_TOO_OLD, res.reason)


class TestPreflightHonoursTheRange(unittest.TestCase):
    def test_an_in_range_schema_ACTIVATES_instead_of_splitting_the_team(self):
        from mokata import run_mode
        with tempfile.TemporaryDirectory() as root:
            s = _team_repo(root)
            behind = teamdb.ProbeResult(
                reachable=True, schema_present=True,
                schema_version=teamdb.TEAM_SCHEMA_VERSION - 1,
                schema_min_supported=teamdb.TEAM_SCHEMA_MIN_SUPPORTED, compatible=True,
                warning="the shared schema is behind this build — `mokata team init` upgrades it",
                detail="reachable + schema in range")
            report = run_mode.team_preflight(s, environ={CUSTOM: _DSN},
                                             probe=lambda _d: behind)
            self.assertTrue(report.activatable, report.render())

    def test_a_client_too_old_refusal_names_the_mokata_upgrade(self):
        from mokata import run_mode
        with tempfile.TemporaryDirectory() as root:
            s = _team_repo(root)
            ahead = teamdb.ProbeResult(
                reachable=True, schema_present=True, schema_version=99,
                schema_min_supported=99, compatible=False,
                reason=teamdb.REASON_CLIENT_TOO_OLD,
                detail="the shared schema is v99 and no longer serves this build")
            report = run_mode.team_preflight(s, environ={CUSTOM: _DSN}, probe=lambda _d: ahead)
            self.assertFalse(report.activatable)
            self.assertIn("upgrade mokata", report.render())


def _team_repo(root, dsn_env=CUSTOM):
    from mokata import team
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda _: None)
    team.team_connect(root, Surface.load(root), dsn_env, assume_yes=True, out=lambda _m: None)
    path = os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(root)


# ================================================== E5 · `team init` = the idempotent upgrade
class TestTeamInitIdempotence(unittest.TestCase):
    """`team init` owns DDL — and owning it means: a no-op on a current schema, a GATED upgrade
    on an old-but-in-range one, and nothing at all when the human declines."""

    def _init(self, root, conn, *, confirm, assume_yes=False):
        from mokata import team
        out = []
        with _psycopg(conn):
            with _env({CUSTOM: _DSN}):
                res = team.team_init(root, Surface.load(root), dsn_env=CUSTOM,
                                     environ={CUSTOM: _DSN}, assume_yes=assume_yes,
                                     confirm=confirm, out=out.append)
        return res, "\n".join(out)

    def test_reinit_on_a_CURRENT_schema_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as root:
            _team_repo(root)
            conn = _MigrationConn(version_row=_current_row())
            asked = []
            res, log = self._init(root, conn, confirm=lambda p: asked.append(p) or True)
            self.assertTrue(res.ok, log)
            self.assertEqual([], conn.ddl_attempts, "a current schema must not be re-provisioned")
            self.assertEqual([], [p for p in asked if "schema" in p.lower()],
                             "a no-op must not ask the human to approve a schema change")
            self.assertIn("no change", log.lower())

    def test_reinit_on_an_OLD_but_in_range_schema_upgrades_under_the_gate(self):
        with tempfile.TemporaryDirectory() as root:
            _team_repo(root)
            conn = _MigrationConn(version_row=(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, 1))
            asked = []
            res, log = self._init(root, conn, confirm=lambda p: asked.append(p) or True)
            self.assertTrue(res.ok, log)
            self.assertTrue(conn.ddl_attempts, "an old schema must be upgraded")
            self.assertTrue(any("schema" in p.lower() for p in asked),
                            f"the schema upgrade must be human-gated (prompts: {asked})")

    def test_a_DECLINED_upgrade_writes_NOTHING(self):
        with tempfile.TemporaryDirectory() as root:
            _team_repo(root)
            conn = _MigrationConn(version_row=(teamdb.TEAM_SCHEMA_MIN_SUPPORTED, 1))
            res, log = self._init(root, conn, confirm=lambda _p: False)
            self.assertFalse(res.ok)
            self.assertEqual([], conn.ddl_attempts, "a declined upgrade must run NO DDL")


class TestRoleNote(unittest.TestCase):
    """The note `team init` prints is the ONLY place an operator learns what the runtime role
    needs. Pre-D1 it read "recommended, not enforced yet" — true then (every runtime connect ran
    its own DDL, so a DML-only role broke), false now. D1 made least-privilege the enforced
    default: the AST guard above proves NO runtime path can reach DDL, so the runtime role needs
    no DDL rights at all. A note that still calls that aspirational tells an operator to
    over-grant the very role D1 exists to lock down."""

    def test_the_note_does_not_call_least_privilege_unenforced(self):
        note = team._ROLE_NOTE.lower()
        for stale in ("not enforced", "recommended, not", "later stage"):
            self.assertNotIn(stale, note,
                             f"the role note still defers least-privilege ({stale!r}) — D1 "
                             f"enforces it: no runtime path can run DDL.")

    def test_the_note_states_the_runtime_role_needs_no_ddl(self):
        note = team._ROLE_NOTE.lower()
        self.assertIn("no ddl", note,
                      f"the note must state the runtime role needs NO DDL: {team._ROLE_NOTE!r}")

    def test_the_note_says_mokata_creates_no_roles(self):
        # Verified against the source: no CREATE ROLE / GRANT / REVOKE exists anywhere in
        # src/mokata. The operator does the role setup; the note must not imply mokata does.
        note = team._ROLE_NOTE.lower()
        self.assertIn("no roles", note,
                      f"the note must say mokata creates no roles: {team._ROLE_NOTE!r}")
        self.assertIn("grant", note,
                      f"the note must say mokata issues no grants: {team._ROLE_NOTE!r}")


class _MigrationConn(_DmlOnlyConn):
    """`team init` runs as the MIGRATION role — DDL is permitted here, and only here."""

    def execute(self, sql, *args):
        head = sql.strip().upper()
        if head.startswith(_DDL_VERBS):
            self.executed.append(sql)
            self.ddl_attempts.append(sql)
            return _Cursor([])
        if head.startswith("INSERT INTO " + teamdb.SCHEMA_VERSION_TABLE.upper()):
            self.executed.append(sql)
            self.ddl_attempts.append(sql)          # the version-row upsert rides with the DDL
            return _Cursor([])
        return super().execute(sql, *args)


if __name__ == "__main__":
    unittest.main()
