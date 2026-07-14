"""CM.S3 — read-your-writes journal overlay + drop ReadThroughCache (C-3).

The bug this closes: in team mode a durable write is journaled FIRST and flushed in batches
(correct, E3), but reads went straight to the backend — a just-approved write was INVISIBLE
until the next flush. The per-process `ReadThroughCache` added a SECOND staleness layer. A user
approves a memory write, asks for it back, and gets nothing: silent incoherence.

These tests pin the fix:
  (a) read-your-writes: an approved-but-unflushed team write is returned by the very next
      recall / list / search — with the shared PG REACHABLE-but-unflushed AND with it UNREACHABLE
      (the degraded local floor). The overlay merges pending (journaled) entries over the backend.
  (b) post-flush dedupe: an entry present in the journal AND the backend yields EXACTLY ONE
      result — the newer (journaled) revision, never a duplicate.
  (c) a pending DELETE (a gated PRUNE) hides the row immediately.
  (d) negative — local mode is byte-identical (the overlay is never consulted / never wired);
      a fully-flushed team state is byte-identical to a bare backend read.
  (e) ReadThroughCache is gone from the source tree entirely.

All proven behind fakes/injected backends — no live Postgres.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_journal
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata.memory.backends import PostgresBackend, SQLiteBackend
from mokata.memory.overlay import JournalOverlay
from mokata.memory.store import build_backend
from mokata.team_journal import JournalEntry, TeamJournal


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
    """A minimal in-memory `mokata_memory` stand-in: enough to drive the real PostgresBackend SQL
    (id -> doc). Present + reachable — the point is that a REACHABLE PG that has NOT yet received
    the flushed row still yields the write on a read (via the overlay), never the backend."""

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


def _item(subject, value="v", **kw):
    return MemoryItem.create(subject=subject, value=value, **kw)


def _entry(item, op=team_journal.OP_PUT):
    """A pending journal entry for `item` — payload matches what the store's team-write path
    writes (`doc` = json of item.to_dict())."""
    payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
               "status": item.status, "doc": json.dumps(item.to_dict()), "project": None}
    return JournalEntry(id="j-" + item.id, op=op, table="mokata_memory", key=item.id,
                        payload=payload)


def _journal_with(root, *entries):
    """A TeamJournal seeded with pending `entries` (append order = newest last)."""
    j = TeamJournal(os.path.join(root, "team_journal.jsonl"))
    for e in entries:
        j.append(e)
    return j


# ===================================================================== (a) read-your-writes
class TestReadYourWritesOverlay(unittest.TestCase):
    def test_pending_write_visible_when_pg_reachable_but_unflushed(self):
        # PG present + reachable, but the row has NOT flushed to it. The journaled write must be
        # returned by list (all) AND get, sourced from the overlay — the backend has nothing.
        with tempfile.TemporaryDirectory() as d:
            pg = _FakePg()
            backend = PostgresBackend(conn=pg)
            it = _item("db.engine", "postgres")
            overlay = JournalOverlay(backend, _journal_with(d, _entry(it)))

            self.assertEqual(backend.all(), [], "precondition: nothing flushed to PG yet")
            got = overlay.all()
            self.assertEqual([(i.id, i.subject) for i in got], [(it.id, "db.engine")])
            self.assertIsNotNone(overlay.get(it.id))
            self.assertEqual(overlay.get(it.id).value, "postgres")

    def test_pending_write_visible_when_pg_unreachable_floor(self):
        # PG unreachable → the store is on the LOCAL SQLite floor. The journaled write must still
        # be returned immediately (the floor is empty; the overlay supplies it).
        with tempfile.TemporaryDirectory() as d:
            floor = SQLiteBackend(":memory:")
            it = _item("api.timeout", "30s")
            overlay = JournalOverlay(floor, _journal_with(d, _entry(it)))

            self.assertEqual(floor.all(), [], "precondition: floor is empty")
            self.assertEqual([i.subject for i in overlay.all()], ["api.timeout"])
            self.assertEqual(overlay.get(it.id).value, "30s")

    def test_overlay_honors_mtype_and_status_filters(self):
        # A pending entry whose type/status doesn't match the requested filter is NOT surfaced.
        with tempfile.TemporaryDirectory() as d:
            floor = SQLiteBackend(":memory:")
            active = _item("live", mtype="persistent")
            proposed = _item("draft", mtype="persistent")
            proposed.status = "proposed"
            overlay = JournalOverlay(floor, _journal_with(d, _entry(active), _entry(proposed)))

            active_only = overlay.all(statuses=("active",))
            self.assertEqual([i.subject for i in active_only], ["live"])
            decisions = overlay.all(mtype="decision")
            self.assertEqual(decisions, [], "no pending decision-typed item → nothing surfaced")


# ===================================================================== (b) post-flush dedupe
class TestDedupeNewestWins(unittest.TestCase):
    def test_entry_in_journal_and_backend_yields_one_newer_result(self):
        # The backend holds the OLD value; a pending journal UPDATE holds the NEW value. Exactly
        # one result for the id, and it's the newer (journaled) revision — never a duplicate.
        with tempfile.TemporaryDirectory() as d:
            pg = _FakePg()
            backend = PostgresBackend(conn=pg)
            old = _item("cache.ttl", "60")
            backend.put(old)                                  # already flushed: revision in PG
            new = MemoryItem.create(subject="cache.ttl", value="120", id=old.id)
            overlay = JournalOverlay(backend, _journal_with(d, _entry(new, team_journal.OP_UPDATE)))

            got = overlay.all()
            self.assertEqual(len(got), 1, "exactly one result for the id — no duplicate")
            self.assertEqual(got[0].value, "120", "the newer (journaled) revision wins")
            self.assertEqual(overlay.get(old.id).value, "120")

    def test_once_flushed_no_duplicate(self):
        # After the entry flushes (no longer pending) the backend is the sole source → still one.
        with tempfile.TemporaryDirectory() as d:
            pg = _FakePg()
            backend = PostgresBackend(conn=pg)
            it = _item("only", "once")
            backend.put(it)
            overlay = JournalOverlay(backend, _journal_with(d))   # empty journal (all flushed)
            got = overlay.all()
            self.assertEqual([i.id for i in got], [it.id])
            self.assertEqual(len(got), 1)


# ===================================================================== (c) pending delete hides
class TestPendingDeleteHides(unittest.TestCase):
    def test_pending_delete_removes_the_row_from_reads(self):
        with tempfile.TemporaryDirectory() as d:
            floor = SQLiteBackend(":memory:")
            it = _item("gone", "soon")
            floor.put(it)
            self.assertEqual(len(floor.all()), 1)
            overlay = JournalOverlay(floor, _journal_with(d, _entry(it, team_journal.OP_DELETE)))
            self.assertEqual(overlay.all(), [], "a pending gated PRUNE hides the row immediately")
            self.assertIsNone(overlay.get(it.id))


# ===================================================================== (d) negatives / byte-identity
class TestByteIdenticalPaths(unittest.TestCase):
    def test_local_build_backend_is_not_wrapped(self):
        # LOCAL mode (routing None) → the backend is returned UNWRAPPED. Zero overlay layer.
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("sqlite", d)
            self.assertIsInstance(be, SQLiteBackend)
            self.assertNotIsInstance(be, JournalOverlay)

    def test_postgres_unavailable_floors_unwrapped_in_local(self):
        # No dsn_env, no routing → SQLite floor, never wrapped (byte-identical to today).
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("postgres", d, config={})
            self.assertIsInstance(be, SQLiteBackend)
            self.assertNotIsInstance(be, JournalOverlay)

    def test_local_read_write_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("sqlite", d)
            it = _item("local")
            be.put(it)
            self.assertEqual([i.subject for i in be.all()], ["local"])
            self.assertEqual(be.get(it.id).id, it.id)

    def test_fully_flushed_team_state_is_backend_identical(self):
        # Team overlay with an EMPTY (fully-flushed) journal returns exactly the backend result.
        with tempfile.TemporaryDirectory() as d:
            floor = SQLiteBackend(":memory:")
            for s in ("a", "b", "c"):
                floor.put(_item(s))
            overlay = JournalOverlay(floor, _journal_with(d))
            self.assertEqual([i.subject for i in overlay.all()],
                             [i.subject for i in floor.all()])
            for i in floor.all():
                self.assertEqual(overlay.get(i.id).id, i.id)

    def test_transparent_delegation(self):
        # name + backend-specific methods + close delegate through the wrapper (drop-in).
        with tempfile.TemporaryDirectory() as d:
            pg = _FakePg()
            overlay = JournalOverlay(PostgresBackend(conn=pg), _journal_with(d))
            self.assertEqual(overlay.name, "postgres")
            self.assertTrue(hasattr(overlay, "list_projects"))
            overlay.close()   # must not raise


# ===================================================================== end-to-end via from_surface
def _team_repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = "team"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(d)


class TestReadYourWritesEndToEnd(unittest.TestCase):
    """The whole story on a real team-mode surface (offline / degraded floor): remember → the very
    next recall/list/search returns it. This is the coherence the bug denied — via the SAME store
    both the MCP `recall` tool and the CLI memory command build."""

    def setUp(self):
        self._saved = os.environ.pop("MOKATA_PG_DSN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["MOKATA_PG_DSN"] = self._saved

    def test_approved_write_is_readable_before_flush(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)                       # team mode, no DSN → offline (unflushed)
            MemoryStore.from_surface(surface).remember(
                _item("db.engine", "postgres"), confirm=lambda _t: True)

            # it is journaled + pending (not yet flushed) …
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            self.assertEqual(len(pend), 1)

            # … yet a FRESH store reads it back immediately: list, recall-by-subject, and search.
            store = MemoryStore.from_surface(surface)
            self.assertEqual([i.value for i in store.all_active()], ["postgres"])
            self.assertEqual([i.value for i in store.recall("db.engine")], ["postgres"])
            hits = store.recall_relevant("db.engine")
            self.assertIn("db.engine", [h.item.subject for h in hits])

    def test_mcp_recall_returns_the_unflushed_write(self):
        from mokata.mcp.tools_read import recall
        with tempfile.TemporaryDirectory() as d:
            surface = _team_repo(d)
            MemoryStore.from_surface(surface).remember(
                _item("api.timeout", "30s"), confirm=lambda _t: True)
            resp = recall(path=d, subject="api.timeout")
            self.assertTrue(resp["enabled"])
            self.assertIn("api.timeout", [i["subject"] for i in resp["items"]])


# ===================================================================== (e) ReadThroughCache is gone
class TestReadThroughCacheRetired(unittest.TestCase):
    def test_readthroughcache_referenced_nowhere_in_src(self):
        src = os.path.join(os.path.dirname(__file__), "..", "src")
        hits = []
        for base, _dirs, files in os.walk(src):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(base, fn)
                with open(p, encoding="utf-8") as fh:
                    if "ReadThroughCache" in fh.read():
                        hits.append(os.path.relpath(p, src))
        self.assertEqual(hits, [], f"ReadThroughCache must be gone from src/; found in {hits}")

    def test_cache_module_deleted(self):
        with self.assertRaises(ImportError):
            import mokata.memory.cache  # noqa: F401


if __name__ == "__main__":
    unittest.main()
