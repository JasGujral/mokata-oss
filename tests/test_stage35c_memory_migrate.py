"""Stage 35c — `mokata memory migrate` (port the store between backends).

Both jsonschema states. Uses a second REAL local SQLite store as the cross-backend test double
(no live Postgres in CI). Proves: sqlite -> the second store -> back preserves items +
provenance; human-gated (decline -> no write); idempotent on re-run; --drop-source removes
the source only after a gated confirm; an unreachable destination (Postgres) degrades cleanly
with the source intact.

WARNING - 0.0.18 stage 10 (lane D slice 1) removed `obsidian`, which WAS that second local store,
and the local cross-backend migration is no longer expressible through the public API: every
remaining destination except `sqlite` needs a live database, and `sqlite` cannot be its own
destination. Skipping these without a DB would be `PYYAML-SKIP-CLUSTER` — a suite that stops
testing and still reports OK — so the second store is SUPPLIED and DECLARED in
`_local_second_store`, which also states what a green here does not prove (nothing about
Postgres; the destination is SQLite and the name selects a branch).

MANUAL VERIFICATION (the named live-PG gap): with psycopg installed + a reachable DB and
`tools.postgres.config.dsn_env` set, `mokata memory migrate --to postgres` ports the local
SQLite store into the shared Postgres (and `--from postgres` back) — exercised live only
where a DB exists (see test_stage35a_shared_memory.py's live note).
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)
from _local_second_store import DESTINATION_TOOL, second_store

from mokata.cli import main
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import (
    MemoryItem,
    MemoryStore,
    MigrateError,
    build_named_backend,
    migrate_memory,
)


def _silent(_):
    pass


def _migrate(surface, **kw):
    kw.setdefault("out", _silent)          # keep test output clean (migrate prints a preview)
    return migrate_memory(surface, **kw)


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


def _seed(surface, items):
    store = MemoryStore.from_surface(surface)
    for subject, value, source in items:
        store.remember(MemoryItem.create(subject, value, source=source, author=source),
                       assume_yes=True)
    store.close()


def _snapshot(backend):
    """{subject: (value, author-provenance)} for a built backend."""
    return {i.subject: (i.value, i.provenance.get("author")) for i in backend.all()}


# ---------------------------------------------------------------- round-trip fidelity

class TestRoundTrip(unittest.TestCase):
    def test_sqlite_out_and_back_preserves_items_and_provenance(self):
        from mokata.memory.backends import SQLiteBackend
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("auth", "jwt", "alice"), ("db", "postgres", "bob")])
            root = surface.mokata_dir
            expected = {"auth": ("jwt", "alice"), "db": ("postgres", "bob")}

            with second_store(d) as dest_path:
                # leg 1: sqlite -> the second store
                res = _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                               assume_yes=True)
                self.assertEqual(res.migrated, 2)
                dest = SQLiteBackend(dest_path)
                self.assertEqual(_snapshot(dest), expected)
                dest.close()

                # wipe the source, then leg 2 back — the round trip restores it
                sq = build_named_backend("sqlite", root, {})
                for it in sq.all():
                    sq.delete(it.id)
                self.assertEqual(_snapshot(sq), {})
                sq.close()
                res2 = _migrate(surface, to_backend="sqlite", from_backend=DESTINATION_TOOL,
                                assume_yes=True)
            self.assertEqual(res2.migrated, 2)
            restored = MemoryStore.from_surface(Surface.load(d))
            vals = {i.subject: (i.value, i.provenance.get("author"))
                    for i in restored.backend.all()}
            self.assertEqual(vals, expected)
            restored.close()


# ---------------------------------------------------------------- gating + idempotency

class TestGatingAndIdempotency(unittest.TestCase):
    def test_human_gated_decline_writes_nothing(self):
        from mokata.memory.backends import SQLiteBackend
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("x", "1", "alice")])
            with second_store(d) as dest_path:
                res = _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                               confirm=lambda _t: False)
            self.assertTrue(res.aborted)
            dest = SQLiteBackend(dest_path)
            self.assertEqual(_snapshot(dest), {})
            dest.close()

    def test_idempotent_on_rerun(self):
        from mokata.memory.backends import SQLiteBackend
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("a", "1", "alice"), ("b", "2", "bob")])
            with second_store(d) as dest_path:
                _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                         assume_yes=True)
                _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                         assume_yes=True)          # re-run
            dest = SQLiteBackend(dest_path)
            self.assertEqual(len(dest.all()), 2)   # upsert by id — no duplicates
            dest.close()


# ---------------------------------------------------------------- --drop-source (gated)

class TestDropSource(unittest.TestCase):
    def test_drop_source_only_after_gated_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("k", "v", "alice")])
            # migrate approved, but the DROP is declined -> source intact
            with second_store(d):
                res = _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                               assume_yes=False, confirm=lambda _t: True,
                               drop_source=True, drop_confirm=lambda _t: False)
            self.assertEqual(res.migrated, 1)
            self.assertEqual(res.dropped, 0)
            self.assertEqual(len(build_named_backend("sqlite", surface.mokata_dir,
                                                     {}).all()), 1)   # source kept

    def test_drop_source_removes_source_when_confirmed(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("k", "v", "alice")])
            from mokata.memory.backends import SQLiteBackend
            with second_store(d) as dest_path:
                res = _migrate(surface, to_backend=DESTINATION_TOOL, from_backend="sqlite",
                               assume_yes=True, drop_source=True)   # assume_yes approves both
            self.assertEqual(res.dropped, 1)
            self.assertEqual(build_named_backend("sqlite", surface.mokata_dir, {}).all(), [])
            dest = SQLiteBackend(dest_path)
            self.assertEqual(len(dest.all()), 1)                    # dest has it
            dest.close()

    def test_self_migrate_refuses_drop(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("k", "v", "alice")])
            res = _migrate(surface, to_backend="sqlite", from_backend="sqlite",
                                 assume_yes=True, drop_source=True)
            self.assertEqual(res.dropped, 0)       # refused — would wipe just-written data
            self.assertEqual(len(build_named_backend("sqlite", surface.mokata_dir,
                                                     {}).all()), 1)


# ---------------------------------------------------------------- degrade-clean (postgres)

class TestDegradeClean(unittest.TestCase):
    def test_unreachable_destination_aborts_source_intact(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("k", "v", "alice")])
            # postgres with no dsn_env -> unbuildable -> abort, NOTHING written, source kept
            res = _migrate(surface, to_backend="postgres", from_backend="sqlite",
                                 assume_yes=True)
            self.assertTrue(res.aborted)
            self.assertEqual(res.migrated, 0)
            self.assertIn("postgres", res.error)
            self.assertEqual(len(build_named_backend("sqlite", surface.mokata_dir,
                                                     {}).all()), 1)   # source intact

    def test_build_named_backend_is_non_degrading_for_postgres(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(MigrateError):
                build_named_backend("postgres", d, {})    # no silent SQLite floor

    def test_unsupported_destination_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            res = _migrate(surface, to_backend="redis", assume_yes=True)
            self.assertTrue(res.aborted)
            self.assertIn("unsupported", res.error)


# ---------------------------------------------------------------- CLI

class TestMigrateCLI(unittest.TestCase):
    def test_cli_migrate_to_a_second_store(self):
        from mokata.memory.backends import SQLiteBackend
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("auth", "jwt", "alice")])
            buf = io.StringIO()
            with second_store(d) as dest_path, redirect_stdout(buf):
                rc = main(["memory", "migrate", "--to", DESTINATION_TOOL, "--yes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn(f"-> {DESTINATION_TOOL}", buf.getvalue())
            dest = SQLiteBackend(dest_path)
            self.assertEqual(len(dest.all()), 1)
            dest.close()

    def test_cli_rejects_a_REMOVED_destination(self):
        # REVERSED at stage 10: `--to obsidian` used to be the happy path of this class.
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(["memory", "migrate", "--to", "obsidian", "--yes", "--path", d])
            self.assertNotEqual(rc, 0)
            said = out.getvalue() + err.getvalue()
            self.assertIn("unsupported destination", said)
            self.assertIn("obsidian", said)

    def test_cli_migrate_requires_to(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            buf = io.StringIO()
            with redirect_stdout(io.StringIO()):
                rc = main(["memory", "migrate", "--path", d])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
