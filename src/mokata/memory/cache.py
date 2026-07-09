"""B3 (0.0.12) — E1 read-through cache for the team-Postgres hot read path.

In TEAM mode, memory retrieval and the gates read from the shared Postgres backend on the hot
path. A slow or briefly-unavailable network makes every repeated read pay a fresh round trip,
and a read can BLOCK on the DB. `ReadThroughCache` is a per-process decorator that wraps ONLY
the shared Postgres backend and serves repeated reads from an in-process cache:

  * a cache HIT is served WITHOUT touching the live connection — the hot path never blocks on
    the network for a read it has already made this run;
  * a cache MISS reads through to the backend and populates the cache;
  * every WRITE (put/update/delete) INVALIDATES the cache, so a durable write never leaves a
    stale cached read behind — consistent with mokata's write path (all writes route through
    the backend, and every one clears the cache);
  * it is TRANSPARENT — `name` and any backend-specific method (`list_projects`, …) delegate to
    the wrapped backend, so consumers can't tell the cache is there.

This is NOT wired for LOCAL mode: `build_backend` wraps only a live `PostgresBackend`, so the
SQLite/Obsidian floor is never decorated and stays byte-identical (no cache layer, no change).

It composes with the TM.S5 work-locally fallback rather than fighting it: the cache is the fast
path; the local/journal fallback stays the outage path. A read MISS that raises (a real outage)
PROPAGATES unchanged so the store still degrades to the floor — the cache never swallows it, and
never caches a failure. The cache itself uses NO external dependency (a plain dict) and degrades
clean: any cache-layer hiccup falls back to a direct read, never a crash.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .backends import MemoryBackend
from .item import MemoryItem


class ReadThroughCache(MemoryBackend):
    """A per-process read-through cache in front of a `MemoryBackend`'s hot read path.

    Caches `get(id)` and `all(mtype, statuses)` results; invalidates the whole cache on any
    write. Whole-cache invalidation is deliberate: an `all()` result depends on the entire
    table, so a targeted eviction would risk a stale list — clearing is cheap and always
    correct. Lifetime is one process (one mokata run); across processes, autocommit visibility
    on the shared DB is unchanged because the first read of each run always reads through."""

    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend
        self.name = getattr(backend, "name", "")
        self._cache: dict = {}

    # --- reads (cached) -----------------------------------------------------
    def get(self, item_id: str) -> Optional[MemoryItem]:
        key = ("get", item_id)
        try:
            if key in self._cache:
                return self._cache[key]
        except Exception:  # pragma: no cover - a cache hiccup degrades to a direct read
            return self._backend.get(item_id)
        result = self._backend.get(item_id)   # MISS: an outage raises here and PROPAGATES
        self._store(key, result)
        return result

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        key = ("all", mtype, statuses)
        try:
            if key in self._cache:
                return self._cache[key]
        except Exception:  # pragma: no cover - a cache hiccup degrades to a direct read
            return self._backend.all(mtype=mtype, statuses=statuses)
        result = self._backend.all(mtype=mtype, statuses=statuses)
        self._store(key, result)
        return result

    def _store(self, key, value) -> None:
        try:
            self._cache[key] = value
        except Exception:  # pragma: no cover - never let a cache write crash a read
            pass

    # --- writes (invalidate, then delegate) ---------------------------------
    def put(self, item: MemoryItem) -> None:
        self._invalidate()
        self._backend.put(item)

    def update(self, item: MemoryItem) -> None:
        self._invalidate()
        self._backend.update(item)

    def delete(self, item_id: str) -> bool:
        self._invalidate()
        return self._backend.delete(item_id)

    def _invalidate(self) -> None:
        self._cache.clear()

    # --- transparency -------------------------------------------------------
    def close(self) -> None:
        self._backend.close()

    def __getattr__(self, attr):
        # Delegate anything not defined here (e.g. list_projects, project, _conn) to the wrapped
        # backend so the cache is a drop-in. Guarded so a missing attr raises AttributeError,
        # not infinite recursion (self._backend is set in __init__ before any lookup).
        if attr == "_backend":
            raise AttributeError(attr)
        return getattr(self._backend, attr)
