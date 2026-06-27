"""Stage 35a — Postgres as the team's shared memory (owned schema), proven.

HONEST TESTING NOTE — there is no live Postgres in CI (psycopg is an optional extra and no
`MOKATA_TEST_PG_DSN` is set), so this file proves the shared-store *behaviour* at the
`MemoryBackend` CONTRACT level using a second backend as the test double: two `MemoryStore`
instances over ONE shared SQLite file stand in for two clients on one shared DB. That proves
CRUD round-trip, two-client visibility (B sees A's committed write), human-gated writes, and
self-healing/dedup against a shared store — all of which are backend-agnostic, so they hold
for Postgres too (mokata owns the schema; the store logic is identical). The Postgres
backend's own SQL (CREATE TABLE / INSERT…ON CONFLICT / SELECT / DELETE) is exercised live
only when a DB is present (the guarded `TestPostgresLive` below) — otherwise that one path is
verified MANUALLY.

MANUAL VERIFICATION (the named gap): with psycopg installed and a reachable DB —
    export MOKATA_TEST_PG_DSN="postgresql://user:pass@host:5432/mokata_test"
    python -m pytest tests/test_stage35a_shared_memory.py  # TestPostgresLive runs
…confirms the real round-trip + two-client visibility against live Postgres.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.detect import Detector
from mokata.manifest import Manifest
from mokata.memory import (
    ACTIVE,
    CONTRADICTION,
    DECISION,
    MemoryItem,
    MemoryStore,
    PostgresBackend,
    PostgresUnavailable,
    SQLiteBackend,
    build_backend,
    build_postgres_backend,
    select_memory_backend,
)
from mokata.router import Router

_HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None


def _client(path):
    """A 'client' = a MemoryStore over its own connection to the SHARED backend file."""
    return MemoryStore(SQLiteBackend(path))


# ---------------------------------------------- shared-store behaviour (contract level)

class TestSharedStoreContract(unittest.TestCase):
    def test_crud_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            be = SQLiteBackend(os.path.join(d, "shared.db"))
            item = MemoryItem.create("db engine", "postgres", source="alice")
            be.put(item)
            self.assertEqual(be.get(item.id).value, "postgres")
            # upsert (update) via the contract
            item2 = MemoryItem.create("db engine", "postgres-15", source="alice",
                                      id=item.id)
            be.update(item2)
            self.assertEqual(be.get(item.id).value, "postgres-15")
            self.assertEqual(len(be.all()), 1)              # upsert, not insert
            self.assertTrue(be.delete(item.id))
            self.assertIsNone(be.get(item.id))
            self.assertFalse(be.delete(item.id))            # idempotent delete

    def test_two_client_visibility_with_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            shared = os.path.join(d, "shared.db")
            a, b = _client(shared), _client(shared)
            res = a.remember(MemoryItem.create("api style", "REST", source="alice",
                                               author="alice"), assume_yes=True)
            self.assertTrue(res.committed)
            # B (a separate connection to the same store) sees A's committed write…
            seen = b.recall("api style")
            self.assertEqual([i.value for i in seen], ["REST"])
            # …with provenance intact (who/source survive the round-trip)
            self.assertEqual(seen[0].provenance.get("author"), "alice")
            a.close(); b.close()

    def test_human_gated_write_holds_on_shared_store(self):
        with tempfile.TemporaryDirectory() as d:
            shared = os.path.join(d, "shared.db")
            a, b = _client(shared), _client(shared)
            res = a.remember(MemoryItem.create("secret choice", "x"),
                             confirm=lambda _t: False)   # declined at the gate
            self.assertFalse(res.committed)
            self.assertEqual(b.all_active(), [])          # nothing reached the shared store
            a.close(); b.close()

    def test_contradiction_surfaced_not_silently_merged(self):
        with tempfile.TemporaryDirectory() as d:
            shared = os.path.join(d, "shared.db")
            a, b = _client(shared), _client(shared)
            a.remember(MemoryItem.create("database", "postgres", mtype=DECISION,
                                         source="alice"), assume_yes=True)
            b.remember(MemoryItem.create("database", "mysql", mtype=DECISION,
                                         source="bob"), assume_yes=True)
            # the shared store now holds two disagreeing ACTIVE facts — surfaced, not merged
            issues = b.detect_issues()
            contradictions = [p for p in issues if p.kind == CONTRADICTION
                              and p.subject == "database"]
            self.assertEqual(len(contradictions), 1)
            # both remain ACTIVE until a human-gated resolution (nothing auto-applied)
            active = b.backend.all(statuses=(ACTIVE,))
            self.assertEqual(sorted(i.value for i in active), ["mysql", "postgres"])
            a.close(); b.close()


# ---------------------------------------------------------------- Postgres degrade paths

class TestPostgresDegrade(unittest.TestCase):
    def test_no_dsn_env_degrades_to_none(self):
        self.assertIsNone(build_postgres_backend({}))
        self.assertIsNone(build_postgres_backend({"dsn_env": "MOKATA_NO_SUCH_VAR_35A"}))

    def test_dsn_env_set_but_unbuildable_degrades_to_none(self):
        # env var present, but psycopg absent (CI) or DB unreachable → None (→ SQLite floor)
        with mock.patch.dict(os.environ,
                             {"MOKATA_TEST_DSN_35A": "postgresql://x/db"}):
            self.assertIsNone(build_postgres_backend({"dsn_env": "MOKATA_TEST_DSN_35A"}))

    @unittest.skipIf(_HAS_PSYCOPG, "psycopg installed — the absent-dep path can't be shown")
    def test_backend_raises_unavailable_without_psycopg(self):
        with self.assertRaises(PostgresUnavailable):
            PostgresBackend("postgresql://x/db")

    def test_build_backend_postgres_falls_to_sqlite_floor(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("postgres", d, config={"dsn_env": "MOKATA_NO_SUCH_VAR_35A"})
            self.assertIsInstance(be, SQLiteBackend)

    def test_router_routes_postgres_then_degrades_to_floor(self):
        # postgres is detected present (forced) and routed, but the build degrades to SQLite
        with tempfile.TemporaryDirectory() as d:
            m = Manifest.from_dict({
                "manifest_version": 1, "mokata": {"version": "0"}, "profile": "custom",
                "layers": {"engine": {"enabled": True}, "knowledge": {"enabled": True},
                           "memory": {"enabled": True}, "governance": {"enabled": True}},
                "capabilities": {"memory_store": {
                    "description": "m", "layer": "memory",
                    "fallback": ["postgres", "sqlite"]}},
                "tools": {
                    "postgres": {"provides": "memory_store", "kind": "external",
                                 "detect": {"type": "python_module", "name": "psycopg"},
                                 "enabled": True,
                                 "config": {"dsn_env": "MOKATA_NO_SUCH_VAR_35A"}},
                    "sqlite": {"provides": "memory_store", "kind": "library",
                               "detect": {"type": "python_module", "name": "sqlite3"},
                               "enabled": True}},
            })
            router = Router(m, Detector(overrides={"postgres": True}, cache=False))
            be = select_memory_backend(router, d)
            self.assertIsInstance(be, SQLiteBackend)   # degraded, never a hard failure


# ---------------------------------------------------- live Postgres (guarded; CI skips)

def _live_dsn():
    dsn = os.environ.get("MOKATA_TEST_PG_DSN")
    if not (_HAS_PSYCOPG and dsn):
        return None
    try:
        PostgresBackend(dsn).close()   # connectable?
        return dsn
    except Exception:
        return None


@unittest.skipUnless(_live_dsn(), "no live Postgres (set MOKATA_TEST_PG_DSN + install psycopg)")
class TestPostgresLive(unittest.TestCase):
    def setUp(self):
        self.dsn = _live_dsn()
        # isolate: clear the mokata-owned table before each run
        a = PostgresBackend(self.dsn)
        for it in a.all():
            a.delete(it.id)
        a.close()

    def test_live_round_trip_and_two_client_visibility(self):
        a = MemoryStore(PostgresBackend(self.dsn))
        b = MemoryStore(PostgresBackend(self.dsn))
        a.remember(MemoryItem.create("live decision", "ship it", source="alice"),
                   assume_yes=True)
        seen = b.recall("live decision")
        self.assertEqual([i.value for i in seen], ["ship it"])
        self.assertEqual(seen[0].provenance.get("source"), "alice")
        # contradiction surfaced against the live shared store
        b.remember(MemoryItem.create("live decision", "hold", source="bob"),
                   assume_yes=True)
        self.assertTrue(any(p.kind == CONTRADICTION for p in b.detect_issues()))
        a.close(); b.close()


if __name__ == "__main__":
    unittest.main()
