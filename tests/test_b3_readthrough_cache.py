"""Stage B3 (0.0.12) — E1 read-through cache for the team-Postgres hot read path.

In TEAM mode, memory retrieval and the gates read from Postgres on the hot path. A slow or
briefly-unavailable network makes repeated retrieval/gate reads pay a fresh round trip. This
stage puts a per-process READ-THROUGH cache in front of the shared Postgres backend so:

  * a cache HIT is served WITHOUT touching the live DB connection (the hot path never blocks
    on the network for repeated reads);
  * a durable WRITE invalidates the cache, so no stale cached read survives a write
    (consistent with mokata's write path — every backend write invalidates);
  * LOCAL mode is byte-identical — the SQLite/Obsidian floor is NEVER wrapped, gains no cache
    layer, and behaves exactly as before;
  * it composes with the TM.S5 work-locally fallback — the cache is the fast path, the local
    fallback stays the outage path (a read miss that raises still propagates so the store
    degrades to the floor);
  * it degrades clean — a cache-layer failure falls back to a direct read, never crashes.

All proven behind a fake/injected connection — no live DB.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.backends import PostgresBackend, SQLiteBackend
from mokata.memory.cache import ReadThroughCache
from mokata.memory.item import MemoryItem
from mokata.memory.store import build_backend


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakePg:
    """A minimal in-memory stand-in for the mokata_memory table — enough to drive the real
    PostgresBackend SQL. `rows`: id -> doc. `executes` counts every SQL statement so a test can
    prove a cache hit issued NO further statements. `dead` makes it raise like a dropped
    connection (models a lost network)."""

    def __init__(self):
        self.rows = {}
        self.executes = 0
        self.dead = False

    def execute(self, sql, params=None):
        if self.dead:
            raise RuntimeError("connection is closed")
        self.executes += 1
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


def _item(subject, value="v"):
    return MemoryItem.create(subject=subject, value=value)


def _pg_cache():
    """A ReadThroughCache wrapping a real PostgresBackend over a fake connection."""
    conn = _FakePg()
    backend = PostgresBackend(conn=conn)
    return ReadThroughCache(backend), conn


class TestHitServedWithoutDb(unittest.TestCase):
    def test_cache_hit_touches_no_live_connection(self):
        cache, conn = _pg_cache()
        cache.put(_item("alpha"))
        first = cache.all()               # MISS: reads through, populates
        self.assertEqual(len(first), 1)
        # Now sever the network. A HIT must be served WITHOUT any DB statement.
        conn.dead = True
        again = cache.all()               # HIT: no execute() → no RuntimeError
        self.assertEqual([i.subject for i in again], [i.subject for i in first])

    def test_get_hit_served_from_cache(self):
        cache, conn = _pg_cache()
        it = _item("beta")
        cache.put(it)
        got = cache.get(it.id)
        self.assertIsNotNone(got)
        conn.dead = True
        again = cache.get(it.id)          # HIT, no DB
        self.assertEqual(again.id, it.id)

    def test_hit_issues_no_further_statements(self):
        cache, conn = _pg_cache()
        cache.put(_item("gamma"))
        cache.all()                       # miss populates
        before = conn.executes
        cache.all()                       # hit
        cache.all()                       # hit
        self.assertEqual(conn.executes, before)


class TestWriteInvalidates(unittest.TestCase):
    def test_put_invalidates_all(self):
        cache, conn = _pg_cache()
        cache.put(_item("one"))
        self.assertEqual(len(cache.all()), 1)   # cached
        cache.put(_item("two"))                 # write → invalidate
        self.assertEqual(len(cache.all()), 2)   # reflects the write, not stale

    def test_delete_invalidates_get(self):
        cache, conn = _pg_cache()
        it = _item("gone")
        cache.put(it)
        self.assertIsNotNone(cache.get(it.id))  # cached present
        cache.delete(it.id)                     # write → invalidate
        self.assertIsNone(cache.get(it.id))     # reflects the delete

    def test_update_invalidates(self):
        cache, conn = _pg_cache()
        it = _item("subj", value="old")
        cache.put(it)
        cache.all()                             # cache the read
        it2 = MemoryItem.create(subject="subj", value="new", id=it.id)
        cache.update(it2)                       # write → invalidate
        docs = [json.loads(conn.rows[it.id])["value"]]
        self.assertEqual(docs, ["new"])
        # next read reflects the new value (not a stale cached "old")
        fresh = cache.get(it.id)
        self.assertEqual(fresh.value, "new")


class TestLocalUntouched(unittest.TestCase):
    def test_build_backend_sqlite_not_wrapped(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("sqlite", d, config={"path": os.path.join(d, "m.db")})
            self.assertIsInstance(be, SQLiteBackend)
            self.assertNotIsInstance(be, ReadThroughCache)

    def test_build_backend_postgres_unavailable_floors_uncached(self):
        # No dsn_env → build_postgres_backend returns None → SQLite floor, NEVER wrapped.
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("postgres", d, config={})
            self.assertNotIsInstance(be, ReadThroughCache)
            self.assertIsInstance(be, SQLiteBackend)

    def test_local_read_write_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            be = build_backend("sqlite", d)
            it = _item("local")
            be.put(it)
            self.assertEqual([i.subject for i in be.all()], ["local"])
            self.assertEqual(be.get(it.id).id, it.id)


class TestComposesAndDegrades(unittest.TestCase):
    def test_read_miss_propagates_for_outage_fallback(self):
        # A read MISS that raises must propagate unchanged so the store's work-locally / floor
        # degrade path (TM.S5) still triggers — the cache never swallows an outage.
        cache, conn = _pg_cache()
        conn.dead = True
        with self.assertRaises(RuntimeError):
            cache.all()

    def test_transparent_attribute_delegation(self):
        # The wrapper is transparent: name + backend-specific methods (list_projects) delegate.
        cache, conn = _pg_cache()
        self.assertEqual(cache.name, "postgres")
        self.assertTrue(hasattr(cache, "list_projects"))

    def test_close_delegates(self):
        cache, conn = _pg_cache()
        cache.close()  # must not raise


if __name__ == "__main__":
    unittest.main()
