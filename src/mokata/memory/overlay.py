"""CM.S3 — the read-your-writes journal overlay for team-mode reads (C-3).

In team mode a durable write is journaled FIRST and flushed to the shared Postgres in batches
(the ONE write path, doc 48 E3). That is correct for durability — but reads went straight to the
backend, so a just-approved write was INVISIBLE until the next flush landed. `JournalOverlay`
closes that gap: it wraps the resolved team-mode backend and MERGES the pending (journaled,
unflushed) team writes over every backend read, so an approved write is visible to the very next
recall / list / search — with zero extra infrastructure (no cache, no daemon, no schema change).

Coherence rules:
  * **Newest-wins by id, no post-flush duplicate.** A pending entry is BY CONSTRUCTION the newest
    un-applied revision for its key: `journal.pending()` returns only PENDING entries — once an
    entry flushes it is marked `_FLUSHED` and drops out of `pending()`, and a CAS lost-update is
    marked `_CONFLICT` (also not pending). So the overlaid item always supersedes the backend's
    copy for that id, and after the flush the backend is the sole source — exactly one result, the
    newer revision, never a duplicate.
  * **A pending DELETE (a gated PRUNE) hides the row** immediately, matching the eventual flush.
  * **Filter-honest.** The same mtype/status filter the backend applied is applied to the overlaid
    items, so a pending PROPOSED draft never leaks into an ACTIVE recall, and a pending update that
    moved a row OUT of the requested set correctly removes it.

Transparency: same result shape as a bare backend; `name`, backend-specific methods
(`list_projects`, …) and `close` delegate, so the store can't tell the overlay is there. Writes
delegate unchanged — team writes journal FIRST and never reach this layer, so the wrapper stays a
pure drop-in (it is wired ONLY in team mode; LOCAL/zero-config is never wrapped, so the local hot
path is byte-identical and consults no journal at all).

This retires the B3 per-process read-through cache (D7): stores are per-call and the overlay
supplies coherence, so there is no second cache layer to go stale on top of the journal.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..degrade import FAILURE_CONFLICTED_WRITE, FAILURE_LOCAL_IO, note_degraded
from .backends import MemoryBackend
from .item import MemoryItem


class JournalOverlay(MemoryBackend):
    """A read-your-writes decorator over a team-mode `MemoryBackend`: reads merge the pending
    (journaled, unflushed) team writes over the backend result; writes/transparency delegate."""

    def __init__(self, backend: MemoryBackend, journal: Any) -> None:
        self._backend = backend
        self._journal = journal
        self.name = getattr(backend, "name", "")

    # --- overlay reconstruction ---------------------------------------------
    def _pending(self) -> Tuple[Dict[str, MemoryItem], Set[str]]:
        """`(overrides, deletes)` from the CURRENT pending journal (re-read each call, so a write
        made mid-run is seen): `overrides` maps id → the reconstructed item for a pending put/
        update, `deletes` is the set of ids a pending DELETE hides. Append order is honored, so the
        newest entry for a key wins. Degrade-clean: a torn/unreadable entry is skipped, never
        raises (a read must not break because the journal has an odd line).

        D5 — an unreadable journal used to switch read-your-writes OFF in silence: every approved-
        but-unflushed team write vanished from every read, which is the exact CM.S3 bug this overlay
        exists to close, wearing the overlay's own clothes. The `{}, set()` fallback STAYS (a read
        must not break), but it now says so."""
        from ..team_journal import OP_DELETE
        overrides: Dict[str, MemoryItem] = {}
        deletes: Set[str] = set()
        try:
            self._announce_conflicts()
            pend = self._journal.pending()
        except OSError as exc:
            # The ONLY raisable: the replay opens the journal file. A torn JSON line is already
            # skipped inside `_records()`, so a broken/locked `temp_local/` is the real case.
            note_degraded("memory-journal", FAILURE_LOCAL_IO,
                          fallback="pending team writes are on disk but INVISIBLE to reads",
                          fix="check permissions on `.mokata/temp_local/`, then run `mokata sync`",
                          detail=f"{type(exc).__name__}: {exc}")
            return {}, set()
        for e in pend:
            try:
                item = MemoryItem.from_dict(json.loads(e.payload["doc"]))
            except Exception:  # pragma: no cover - skip an unreadable entry
                continue
            if e.op == OP_DELETE:
                deletes.add(e.key)
                overrides.pop(e.key, None)
            else:
                overrides[e.key] = item
                deletes.discard(e.key)
        return overrides, deletes

    def _announce_conflicts(self) -> None:
        """DB.S6/I2b — say, LOUDLY and once per process, that N approved writes are NOT in the
        memory this read is returning.

        The overlay merges only PENDING entries, and a write that loses its CAS is marked
        CONFLICT — so it silently falls out of every read. The reader then sees the teammate's
        value with no hint that their own approved fact never landed. That silence is the whole
        problem: an approved write that is neither in memory nor mentioned is indistinguishable
        from one that was never made.

        Announcing rather than OVERLAYING is the deliberate choice. Overlaying the conflicted item
        would put a value into recall that is NOT in the shared store, and — because the overlay
        overrides by id — would hide the teammate's actual value behind it. A read that lies about
        shared state to avoid an omission is a worse trade than one that names the omission.

        Costs one dict lookup: `conflicts()` and `pending()` come from the SAME cached replay, so
        this adds no parse and no file read to the hot path."""
        conflicts = self._journal.conflicts()
        if not conflicts:
            return
        n = len(conflicts)
        note_degraded("memory-conflicted-writes", FAILURE_CONFLICTED_WRITE,
                      fallback=(f"{n} approved write(s) are NOT in active memory — they "
                                f"conflicted with another writer's change"),
                      fix="run `mokata sync` (or `mokata govern`) and decide which version wins",
                      detail=", ".join(sorted(c.key for c in conflicts)[:5]))

    @staticmethod
    def _matches(item: MemoryItem, mtype: Optional[str],
                 statuses: Optional[Tuple[str, ...]]) -> bool:
        """The SAME filter the backends apply in `all()` — so an overlaid item honors the request."""
        return ((mtype is None or item.mtype == mtype)
                and (statuses is None or item.status in statuses))

    # --- reads (overlaid) ----------------------------------------------------
    def get(self, item_id: str) -> Optional[MemoryItem]:
        overrides, deletes = self._pending()
        if item_id in deletes:
            return None
        if item_id in overrides:
            return overrides[item_id]
        return self._backend.get(item_id)

    @property
    def supports_scope_pushdown(self) -> bool:
        """DB.S2b — the overlay can push scope IFF the store underneath it can. Forwarded rather
        than hard-coded: in team mode this wrapper IS the read path, so leaving it False would mean
        the Postgres pushdown never actually runs where the scoped corpus is largest."""
        return bool(getattr(self._backend, "supports_scope_pushdown", False))

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        # DB.S2a — mtype/statuses ARE pushed to the backend (it filters in SQL now). `limit` is
        # deliberately NOT: this layer removes rows a pending delete hid and appends rows a pending
        # write revealed, so a backend-side LIMIT would take its N before the merge and hand back
        # the wrong N. Apply it to the MERGED result, which is the set the caller asked to cap.
        #
        # DB.S2b — `scope_path` IS pushed, and the pending overrides are then held to the SAME
        # predicate below. Pushing it to the backend without also applying it here would let an
        # unflushed write at another scope appear in a scoped read — the journal overlay is a read
        # path like any other, and a filter that only covers the flushed half is not a filter.
        extra = {} if scope_path is None else {"scope_path": scope_path}
        base = self._backend.all(mtype=mtype, statuses=statuses, **extra)
        overrides, deletes = self._pending()
        if not overrides and not deletes:
            return base[:limit] if limit is not None else base   # fully-flushed → backend-identical
        # A pending entry is the authoritative CURRENT state for its key: drop the backend's copy,
        # then re-add the pending item IFF it still matches the requested filter.
        touched = set(overrides) | deletes
        result = [it for it in base if it.id not in touched]
        for it in overrides.values():
            if self._matches(it, mtype, statuses) and self._in_scope(it, scope_path):
                result.append(it)
        return result[:limit] if limit is not None else result

    @staticmethod
    def _in_scope(item: MemoryItem, scope_path: Optional[Sequence[Any]]) -> bool:
        """DB.S2b — is a PENDING item on the requested scope path? Delegates to `scope.on_path`, the
        same predicate the SQL mirrors, so a pending row and a flushed row are judged identically.
        No path requested → everything is in scope (the unscoped/local read)."""
        if scope_path is None:
            return True
        from .scope import on_path
        return on_path(item, scope_path)

    # --- writes (delegate — team writes journal first, never reach here) -----
    def put(self, item: MemoryItem) -> None:
        self._backend.put(item)

    def update(self, item: MemoryItem) -> None:
        self._backend.update(item)

    def delete(self, item_id: str) -> bool:
        return self._backend.delete(item_id)

    # --- transparency --------------------------------------------------------
    def close(self) -> None:
        self._backend.close()

    def __getattr__(self, attr: str) -> Any:
        # Delegate anything not defined here (list_projects, _conn, …) to the wrapped backend so
        # the overlay is a drop-in. Guarded so a missing attr raises AttributeError, not infinite
        # recursion (_backend/_journal are set in __init__ before any lookup).
        if attr in ("_backend", "_journal"):
            raise AttributeError(attr)
        return getattr(self._backend, attr)
