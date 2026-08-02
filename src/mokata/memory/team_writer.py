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

import json
import os
from typing import Any, List, Optional

from .healing import ConflictRecord
from .item import MemoryItem

_OP_PUT = "memory_put"

# DB.S6 — the two resolutions the ONE resolver can commit. Named here (not spelled inline at the
# call site) because they are the journal's replay vocabulary: `kept-local` re-queues the local
# write at the current remote revision so a re-flush CASes over it; `kept-remote` drops the local
# write. Anything else would replay as an unknown record and silently do nothing.
KEEP_LOCAL = "kept-local"
KEEP_REMOTE = "kept-remote"


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

    # --- DB.S6: cross-writer conflicts ---------------------------------------
    def conflicts(self, surface: Any) -> List[ConflictRecord]:
        """The surfaced CAS conflicts, PROJECTED into plain memory-layer records (R3).

        This is the boundary the projection belongs on: `team_writer` is already the one place the
        store touches the collab layer, so `ConflictView` — and the journal type it wraps — stops
        here and `memory/healing.py` never learns they exist.

        PROPOSE-ONLY (I6): the journal is only CONSTRUCTED when its file already exists, because
        `TeamJournal.__init__` creates the parent directory. So the detection arm reads or reads
        nothing; it never writes, and a project that has never made a team write is untouched."""
        path = self._journal_path(surface)
        if not path or not os.path.exists(path):
            return []
        from ..team_journal import TeamJournal
        return [self._project(c) for c in TeamJournal(path).conflicts()]

    @staticmethod
    def _journal_path(surface: Any) -> Optional[str]:
        from ..team_journal import TeamJournal
        try:
            return TeamJournal.path_for(surface)
        except AttributeError:
            # A surface without `mokata_dir` (an injected double in a unit test) has no journal —
            # "no conflicts" is the honest answer, and it is the same answer as an empty journal.
            return None

    @classmethod
    def _project(cls, view: Any) -> ConflictRecord:
        """One `ConflictView` → one `ConflictRecord`. Degrade-clean by construction: an
        unparseable local doc falls back to the entry's own plain columns (which the flush already
        relies on), and an unparseable REMOTE doc yields `remote=None` — rendered as
        "unreadable" rather than as an invented value the human might resolve against."""
        entry = view.entry
        local = (cls._item(entry.payload.get("doc"))
                 or MemoryItem(id=entry.key,
                               subject=str(entry.payload.get("subject") or entry.key),
                               value="", mtype=str(entry.payload.get("mtype") or "fact"),
                               status=str(entry.payload.get("status") or "active")))
        remote_doc = (view.remote or {}).get("doc")
        return ConflictRecord(
            conflict_id=view.id, key=view.key, detail=view.detail, local=local,
            remote=cls._item(remote_doc),
            remote_revision=(view.remote or {}).get("revision"))

    @staticmethod
    def _item(doc: Any) -> Optional[MemoryItem]:
        if not doc:
            return None
        try:
            return MemoryItem.from_dict(json.loads(doc) if isinstance(doc, str) else doc)
        except Exception:
            # D5 — deliberately BROAD: `doc` is a JSON blob written by ANOTHER machine, possibly by
            # another mokata version. Every way it can be wrong (torn JSON, a non-dict, a missing
            # key, a type the model rejects) lands here, and the answer to all of them is the same:
            # this side of the conflict cannot be shown, which the caller renders honestly. Raising
            # would break a READ path — `detect_issues` — on a teammate's malformed row.
            return None

    def group_refusal(self, surface: Any, conflict_id: str, resolution: str) -> Optional[str]:
        """DB.S6/I1b — the refusal text if committing this resolution would retire a fact whose
        replacement, approved in the SAME group, is not going to land. None = allow.

        Sits beside `resolve_conflict` on purpose: the group linkage is the JOURNAL's (the approval
        id every entry already carries), so the question is answered where that lives and only the
        answer — a sentence — crosses back into the memory layer. `MemoryStore` never learns what a
        group is, exactly as it never learns what a `ConflictView` is.

        FAIL-CLOSED on an unreadable journal: it returns a refusal rather than None. Answering
        "allow" from a journal we could not read would be a guess in the one direction that loses a
        fact, and a resolution we cannot verify is a resolution we cannot safely commit."""
        path = self._journal_path(surface)
        if not path or not os.path.exists(path):
            return None                        # no journal → no group → nothing to protect
        from ..team_journal import (TeamJournal, duplicate_both_active_refusal,
                                    retire_without_replace_refusal)
        try:
            journal = TeamJournal(path)
            # DB.S7d — BOTH directions of the half-decided approval, asked in the order they cost:
            # losing a fact first, duplicating one second. Two calls rather than one merged
            # predicate, because they refuse different things and their messages have to say so.
            for guard in (retire_without_replace_refusal, duplicate_both_active_refusal):
                refusal = guard(journal, conflict_id, resolution)
                if refusal is not None:
                    return refusal
            return None
        except OSError as exc:
            return (f"refused: the local journal could not be read, so mokata cannot check whether "
                    f"this resolution would retire a fact whose replacement was approved alongside "
                    f"it ({type(exc).__name__}: {exc}). Nothing was changed — check permissions on "
                    f"`.mokata/temp_local/`, then resolve again.")

    def group_members(self, surface: Any, conflict_id: str) -> List[str]:
        """DB.S7d — the still-conflicted members of this conflict's approval (see
        `approval_group_conflicts`). Empty when there is no journal to read: the store renders that
        as "nothing to decide", which is the same answer an empty journal gives."""
        path = self._journal_path(surface)
        if not path or not os.path.exists(path):
            return []
        from ..team_journal import TeamJournal, approval_group_conflicts
        try:
            return approval_group_conflicts(TeamJournal(path), conflict_id)
        except OSError:
            return []

    def group_decision_refusal(self, surface: Any,
                               decisions: List[Any]) -> Optional[str]:
        """DB.S7d — the refusal text if this whole-approval verdict must not commit, else None.

        FAIL-CLOSED on an unreadable journal, for the same reason `group_refusal` is: a verdict we
        cannot check is a verdict we cannot safely commit, and here it covers N writes rather than
        one."""
        path = self._journal_path(surface)
        if not path or not os.path.exists(path):
            return None
        from ..team_journal import TeamJournal, group_decision_refusal
        try:
            return group_decision_refusal(TeamJournal(path), decisions)
        except OSError as exc:
            return (f"refused: the local journal could not be read, so mokata cannot check whether "
                    f"this whole-approval decision is safe to commit ({type(exc).__name__}: {exc})."
                    f" Nothing was changed — check permissions on `.mokata/temp_local/`, then "
                    f"resolve again.")

    def resolve_group(self, surface: Any, decisions: List[Any]) -> None:
        """DB.S7d — commit a whole-approval verdict as ONE durable act. The atomicity lives in
        `TeamJournal.resolve_group` (one buffer, one write, one lock hold); this is the boundary
        crossing, exactly as `resolve_conflict` is for the single-member case."""
        from ..team_journal import TeamJournal
        TeamJournal.for_surface(surface).resolve_group(decisions)

    def resolve_conflict(self, surface: Any, conflict_id: str, resolution: str, *,
                         remote_revision: Optional[int] = None) -> None:
        """Commit ONE human decision on ONE conflict (R4). The single place cross-writer state
        changes: `MemoryStore.apply_proposal` calls it from inside the WriteGate, and `mokata sync`
        reaches it through that same store method rather than resolving on its own."""
        from ..team_journal import TeamJournal
        TeamJournal.for_surface(surface).resolve(conflict_id, resolution,
                                                 remote_revision=remote_revision)

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
