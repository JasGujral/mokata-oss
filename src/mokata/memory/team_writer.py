"""The TEAM-mode write strategy — the collab-layer seam behind `MemoryStore`'s durable writes.

Extracted from `memory/store.py` (PRE-SIMP, release 0.0.15) so the store (an L2 domain module) no
longer reaches UP to the L3 collab layer (`team_journal`/`team_audit`/`teamdb`) at its own edge:
`MemoryStore` holds an INJECTED `TeamWriter` (resolved in `from_surface`, defaulted lazily) and
delegates its journal-first write + best-effort flush here. This is the layering seed the 0.1.1
LAYER-LINT locks in; the resolved writes are byte-identical to the pre-extraction store internals.

Only the TEAM-mode path uses this — LOCAL / zero-config stores never construct or call it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Optional

from .item import MemoryItem

_OP_PUT = "memory_put"


class TeamWriter:
    """The journal-first + flush operations `MemoryStore` performs in TEAM mode. Stateless; the
    surface and ledger are passed per call, so one instance serves any store. Splitting this out of
    the store keeps the team-journal wiring in ONE place SIMP.S3 can evolve without touching store
    internals — the bodies below are the pre-extraction `_journal_team_write` / `_best_effort_flush`
    verbatim, so behaviour is unchanged."""

    def journal_write(self, surface: Any, item: MemoryItem, ledger_id: Any, *,
                      op: str = _OP_PUT,
                      base_revision: Optional[int] = None) -> None:
        """Journal-first (doc 48 E3): buffer this durable write in the crash-safe local journal
        instead of writing direct-to-backend. `ledger_id` is the gate's approval id (C5/P2): the
        deferred flush re-records it, so deferred durability inherits the human decision. `op`
        (put/update/delete) + `base_revision` drive the flush's compare-and-set (TM.S5/S5c), so a
        concurrent change SURFACES as a conflict — never silently last-writer-wins."""
        import json as _json
        from .. import team_journal, teamdb
        from ..project import project_id
        # D5 — both handlers below are deliberately left BROAD, with no narrow class to name:
        # `project.derive_project_id` ("Never raises") and `team_audit.actor` (never-raise by
        # contract) both promise not to raise, so there is no honest class to enumerate for either.
        # Naming a made-up class here would be worse than the broad catch it replaced.
        try:
            project = project_id(surface)
        except Exception:
            project = None
        try:
            from ..team_audit import actor as _actor
            who = _actor()
        except Exception:
            who = "user"
        # D6 — `to_doc` (not `to_dict`): the DURABLE serializer, which refuses a doc newer than the
        # schema this build speaks. The journal is a durable write like any other — a stripped doc
        # journaled here would flush to the shared table and destroy a teammate's approved fields.
        payload = {"id": item.id, "mtype": item.mtype, "subject": item.subject,
                   "status": item.status, "doc": _json.dumps(item.to_doc()),
                   "project": project}
        # base_revision None → a believed-new row (INSERT ... ON CONFLICT DO NOTHING at flush; a
        # concurrent create SURFACES as a conflict); an int → the revision-guarded UPDATE/DELETE
        # base. Either way, never a silent overwrite.
        team_journal.record_team_write(
            surface, op=op, table=teamdb.MEMORY_TABLE, key=item.id,
            payload=payload, ledger_id=ledger_id, project=project, actor=who,
            base_revision=base_revision)

    def flush(self, surface: Any, ledger: Any) -> None:
        """After a healthy gated team write, flush the journal so the write reaches Postgres
        immediately (doc 48 E3: 'flush when healthy'). NEVER blocks and NEVER raises — offline
        returns skipped (the write stays journaled: work-locally, nothing lost; `mokata sync`
        reconciles later). The committed gate decision is never undone by a flush hiccup."""
        try:
            from .. import flush_liveness
            # CM.S4 — the liveness-aware flush: a failed flush is now retried with bounded backoff
            # on subsequent touchpoints (no daemon) and the pending backlog is counted/surfaced,
            # instead of silently waiting. Healthy writes drain immediately (byte-identical).
            flush_liveness.flush_with_liveness(surface, ledger=ledger)
        except Exception:  # pragma: no cover - flush is best-effort by construction
            pass
