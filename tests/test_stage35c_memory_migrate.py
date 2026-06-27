"""Stage 35c — `mokata memory migrate` (port the store between backends).

Both jsonschema states. Uses a second SQLite/Obsidian backend as the cross-backend test
double (no live Postgres in CI). Proves: sqlite -> obsidian -> back preserves items +
provenance; human-gated (decline -> no write); idempotent on re-run; --drop-source removes
the source only after a gated confirm; an unreachable destination (Postgres) degrades cleanly
with the source intact.

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
    def test_sqlite_to_obsidian_and_back_preserves_items_and_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("auth", "jwt", "alice"), ("db", "postgres", "bob")])
            root = surface.mokata_dir

            # leg 1: sqlite -> obsidian
            res = _migrate(surface, to_backend="obsidian", from_backend="sqlite",
                                 assume_yes=True)
            self.assertEqual(res.migrated, 2)
            obs = build_named_backend("obsidian", root, {})
            self.assertEqual(_snapshot(obs),
                             {"auth": ("jwt", "alice"), "db": ("postgres", "bob")})

            # wipe sqlite, then leg 2: obsidian -> sqlite restores it (round-trip)
            sq = build_named_backend("sqlite", root, {})
            for it in sq.all():
                sq.delete(it.id)
            self.assertEqual(_snapshot(sq), {})
            res2 = _migrate(surface, to_backend="sqlite", from_backend="obsidian",
                                  assume_yes=True)
            self.assertEqual(res2.migrated, 2)
            restored = MemoryStore.from_surface(Surface.load(d))
            vals = {i.subject: (i.value, i.provenance.get("author"))
                    for i in restored.backend.all()}
            self.assertEqual(vals,
                             {"auth": ("jwt", "alice"), "db": ("postgres", "bob")})


# ---------------------------------------------------------------- gating + idempotency

class TestGatingAndIdempotency(unittest.TestCase):
    def test_human_gated_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("x", "1", "alice")])
            res = _migrate(surface, to_backend="obsidian", from_backend="sqlite",
                                 confirm=lambda _t: False)
            self.assertTrue(res.aborted)
            self.assertEqual(_snapshot(build_named_backend("obsidian", surface.mokata_dir,
                                                           {})), {})

    def test_idempotent_on_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("a", "1", "alice"), ("b", "2", "bob")])
            _migrate(surface, to_backend="obsidian", from_backend="sqlite",
                           assume_yes=True)
            _migrate(surface, to_backend="obsidian", from_backend="sqlite",
                           assume_yes=True)        # re-run
            obs = build_named_backend("obsidian", surface.mokata_dir, {})
            self.assertEqual(len(obs.all()), 2)    # upsert by id — no duplicates


# ---------------------------------------------------------------- --drop-source (gated)

class TestDropSource(unittest.TestCase):
    def test_drop_source_only_after_gated_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("k", "v", "alice")])
            # migrate approved, but the DROP is declined -> source intact
            res = _migrate(surface, to_backend="obsidian", from_backend="sqlite",
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
            res = _migrate(surface, to_backend="obsidian", from_backend="sqlite",
                                 assume_yes=True, drop_source=True)   # assume_yes approves both
            self.assertEqual(res.dropped, 1)
            self.assertEqual(build_named_backend("sqlite", surface.mokata_dir, {}).all(), [])
            self.assertEqual(len(build_named_backend("obsidian", surface.mokata_dir,
                                                     {}).all()), 1)   # dest has it

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
    def test_cli_migrate_to_obsidian(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _seed(surface, [("auth", "jwt", "alice")])
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["memory", "migrate", "--to", "obsidian", "--yes", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("-> obsidian", buf.getvalue())
            self.assertEqual(len(build_named_backend("obsidian", surface.mokata_dir,
                                                     {}).all()), 1)

    def test_cli_migrate_requires_to(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            buf = io.StringIO()
            with redirect_stdout(io.StringIO()):
                rc = main(["memory", "migrate", "--path", d])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
