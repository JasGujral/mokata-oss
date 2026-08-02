"""MEMORY write tools — human-gated (SI.3) memory writes.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15). These are the memory writers
that STAY through SIMP — the CORE writers (`remember`, `apply_proposal`) AND the memory BACKUP
surface (`memory_export` = backup, `memory_import` = restore), re-homed here at 35b from
`tools_share.py`. The backup surface survives SIMP as the ONE gated backup-and-restore command; only
`vault_push` (a genuine deprecated sharing channel) stays in `tools_share.py` for the SIMP.S3
deletion. Registration order + tool names are preserved by the `tools_write.py` aggregator; every
tool routes through the one consent boundary in `mcp/consent.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import approval
from ..govern import AuditLedger
from ..memory import CROSS_WRITER, DECISION, MemoryItem, MemoryStore
from .consent import (_consent, _gated_write, _policy, _propose, _refused,
                      _require)
from .registry import _surface


def remember(path: str = ".", subject: str = "", value: str = "",
             memory_type: str = DECISION, kind: str = "", approve: bool = False,
             confirm: Optional[bool] = None, mtype: Optional[str] = None,
             proposal_id: str = "", about_code: Optional[list] = None) -> Dict[str, Any]:
    """Remember a fact/decision in memory. `memory_type` is the storage tier
    (persistent/decision/episodic); `kind` is the typed project part (rule/guardrail/
    best-practice/context/reference) captured by /mokata:onboard; `mtype` is a DEPRECATED alias
    for `memory_type`. HUMAN-GATED (SI.3): this is PROPOSE-ONLY. It returns a proposal_id and writes
    nothing. Only an approval a HUMAN minted with `mokata approve <id>` — then referenced back here
    as `proposal_id` — commits it, once. `approve=true` does NOT commit (it never really did: it was
    a flag the model typed). A secret in `subject` OR `value` is blocked even when approved. A part
    kind is stored as persistent project knowledge."""
    from ..memory import PART_KINDS, PERSISTENT, normalize_kind
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    mtype = mtype or memory_type      # `mtype` (deprecated) overrides only if explicitly passed
    norm = normalize_kind(kind)
    if norm in PART_KINDS:
        mtype = PERSISTENT          # the captured "parts" are persistent project knowledge
    item = MemoryItem.create(subject, value, mtype=mtype, kind=norm or kind,
                             about_code=about_code)

    args = {"path": path, "subject": subject, "value": value, "memory_type": mtype, "kind": norm}
    gate = _consent(path, "remember", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        # GR.S4 — validate any about_code anchors against the code graph. A PROPOSAL-level
        # warning rides on the proposal (never a block, never an auto-write — P2 untouched).
        payload = {"preview": store.render_write(item)}
        if about_code:
            try:
                from ..knowledge.about_code import check_about_code_anchors
                from ..knowledge.layer import KnowledgeLayer
                chk = check_about_code_anchors(about_code, KnowledgeLayer.from_surface(surface))
                if chk.warning:
                    payload["about_code_warning"] = chk.warning
            except Exception:
                pass                       # validation never blocks a proposal
        return _propose(path, "remember", args, payload,
                        target=f"memory:{subject}", summary=f"remember '{subject}' = {value[:60]}",
                        preview=store.render_write(item), approve=approve, confirm=confirm)
    # H4: scan subject AND value so a secret pasted into the subject can't slip the gate.
    return _gated_write(surface.mokata_dir, "memory", f"memory:{subject}",
                        f"{subject}\n{value}",
                        lambda: store.remember(item, assume_yes=True).committed, gate,
                        _policy(path, "remember", human_approved=True, surface=surface))


def apply_proposal(path: str = ".", subject: str = "", decision: str = "approve",
                   approve: bool = False, confirm: Optional[bool] = None,
                   proposal_id: str = "") -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction/staleness/cross-writer).
    HUMAN-GATED (SI.3): PROPOSE-ONLY — it shows the staged old->new change and writes nothing. A
    human mints the approval with `mokata approve <id>`; re-call with that `proposal_id` to apply
    your `decision` (approve/reject/defer, plus `discard` on a cross-writer conflict).

    DB.S6 — `discard` means "keep THEIRS": it drops your own approved-but-unlanded write when a
    teammate's concurrent change won the CAS. It has its own word rather than riding `reject`
    because `reject` is where every safe default lands (a dismissed consent, a non-interactive
    call), and a default that throws away an approved write is exactly the silent loss this stage
    removes. It is refused on any other kind of proposal."""
    if decision not in ("approve", "reject", "defer", "discard"):
        return {"status": "error",
                "message": "decision must be one of approve/reject/defer (or discard, on a "
                           "cross-writer conflict)"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    proposals = store.detect_issues()
    if decision == "discard":
        # `discard` is only meaningful for a conflict, and a subject can carry BOTH a conflict and
        # an ordinary contradiction — so select by kind rather than letting first-match decide
        # which proposal a destructive decision lands on.
        proposals = [p for p in proposals if p.kind == CROSS_WRITER]
    match = next((p for p in proposals if p.subject == subject), None)
    if match is None:
        return {"status": "error",
                "message": f"no pending proposal for subject '{subject}'"}

    args = {"path": path, "subject": subject, "decision": decision, "diff": match.diff()}
    gate = _consent(path, "apply_proposal", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "apply_proposal", args,
                        {"subject": subject, "kind": match.kind, "diff": match.diff(),
                         "decision": decision},
                        target=f"memory:{subject}",
                        summary=f"{decision} the self-healing proposal for '{subject}'",
                        preview=match.diff(), approve=approve, confirm=confirm)
    return _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(),
        lambda: store.apply_proposal(match, decision, assume_yes=True).message, gate,
        _policy(path, "apply_proposal", human_approved=True, surface=surface))


def consolidate(path: str = ".", session: str = "", value: str = "",
                approve: bool = False, confirm: Optional[bool] = None,
                proposal_id: str = "") -> Dict[str, Any]:
    """M-4/R5 PHASE 2 — submit a summary YOU drafted for an episodic cluster (see the
    `consolidate_proposals` read tool for what to draft and the turns to draft from).

    mokata calls no model and writes no summary of its own; the text in `value` is yours. HUMAN-GATED
    (SI.3): PROPOSE-ONLY — this returns a proposal_id and writes nothing. A human mints the approval
    with `mokata approve <id>`; re-call with that `proposal_id` to commit it, once. You cannot
    approve your own draft — there is no argument here that reaches a write. A secret in the drafted
    summary is blocked even when approved."""
    from ..memory.consolidation import constant_drafter, find_summarize
    if not session or not value:
        return {"status": "error",
                "message": "consolidate needs `session` (from consolidate_proposals) and the "
                           "`value` you drafted — mokata does not write the summary"}
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}

    # The drafted text rides the DRAFTER SEAM, not a hand-built item: same typing, same always-on
    # clamp, same secret-scan, same gate render as any drafted summary. See `constant_drafter`.
    match = find_summarize(store.propose_consolidations(drafter=constant_drafter(session, value)),
                           session)
    if match is None:
        return {"status": "error",
                "message": f"no summarize proposal for session '{session}' — call "
                           f"consolidate_proposals to see what wants drafting"}

    args = {"path": path, "session": session, "value": value}
    gate = _consent(path, "consolidate", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "consolidate", args,
                        {"session": session, "kind": match.kind, "diff": match.diff(),
                         "turns": len(match.olds)},
                        target=f"memory:summary:{session}",
                        summary=f"store the drafted summary for '{session}' "
                                f"({len(match.olds)} turns)",
                        preview=store.render_consolidation(match),
                        approve=approve, confirm=confirm)
    # H4 discipline — scan the SUBJECT and the drafted VALUE: a drafted summary is exactly where a
    # credential quoted out of a conversation turn would enter.
    return _gated_write(
        surface.mokata_dir, "memory", f"memory:summary:{session}",
        f"{match.new.subject}\n{match.new.value}",
        lambda: store.apply_consolidation(match, "approve", assume_yes=True).message, gate,
        _policy(path, "consolidate", human_approved=True, surface=surface))


# ======================================================================================
# the memory BACKUP surface (35b) — export = backup, import = restore. Re-homed from
# `tools_share.py` (it SURVIVES SIMP.S3; only `vault_push` dies there). Backup = store → an
# explicit human-owned FILE (gated egress); restore = file → store via the WriteGate, provenance-
# stamped. It is a BACKUP surface, NOT a sharing channel — team sharing is the Postgres DSN alone.
# ======================================================================================

def memory_export(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Back up local memory (active items + provenance) to a human-owned JSON FILE. READ-ONLY on the
    store. HUMAN-GATED (SI.3): PROPOSE-ONLY — it reports how many items WOULD be written and writes
    no file. A human mints the approval with `mokata approve <id>`; re-call with that `proposal_id`
    to write the backup. The DEFAULT dest is a timestamped `.mokata/backups/memory-<UTC>.json` (35b —
    the deprecated `memory-share.json` channel is no longer a default; writing to it still works but
    warns). Every value is secret-scanned at EGRESS strength (SI.6): a hit HARD-BLOCKS that item — it
    is named in `blocked` and left out of the backup entirely."""
    from .. import deprecation
    from ..memory import (default_backup_path, export_memory, export_payload,
                          is_legacy_share_dest)
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    data = export_memory(store)             # read-only; scans + computes the items without writing
    # dest: an explicit --file wins; otherwise the timestamped backup default. The timestamp is
    # minted ONCE, at propose time, and RECOVERED from the approved proposal on redeem — so the two
    # calls hash the SAME dest (else the content-hash would never match and the approval couldn't be
    # redeemed) AND the human approves the exact file that is written (P16 honesty).
    dest = file
    if not dest and proposal_id:
        p = approval.load(path, proposal_id)
        if p is not None and p.target:
            dest = p.target
    if not dest:
        dest = default_backup_path(path)
    blocked = data["blocked"]
    # legacy channel → warn ONCE per repo (SIMP.S2 machinery, no new warn system); still writes.
    if is_legacy_share_dest(dest):
        deprecation.warn_deprecated("memory-share", surface.mokata_dir)

    args = {"path": path, "dest": dest, "items": len(data["items"])}
    gate = _consent(path, "memory_export", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "memory_export", args,
                        {"items": len(data["items"]), "dest": dest,
                         "blocked": len(blocked), "blocked_keys": blocked},
                        target=dest, summary=f"back up {len(data['items'])} memory item(s) to {dest}",
                        approve=approve, confirm=confirm)
    # SI.6 (74 C2): the write goes through the universal WriteGate as kind `send` — a backup is
    # EGRESS, so the gate's own scan runs at outbound strength over the exact bytes that leave, and
    # the ledger records the write with that content hashed. Per-item blocking already happened in
    # `export_memory`; this is the belt-and-suspenders on the assembled backup.
    res = _gated_write(surface.mokata_dir, "send", dest, export_payload(data),
                       lambda: export_memory(store, dest=dest), gate,
                       _policy(path, "memory_export", human_approved=True, surface=surface))
    return {"status": res["status"], "committed": res["committed"], "dest": dest,
            "items": len(data["items"]), "blocked": len(blocked), "blocked_keys": blocked,
            "reason": res["reason"]}


def memory_import(path: str = ".", file: str = "", approve: bool = False,
                  confirm: Optional[bool] = None, proposal_id: str = "") -> Dict[str, Any]:
    """Restore a memory backup FILE into local memory. HUMAN-GATED (SI.3): PROPOSE-ONLY — it previews
    (counts + a keys-only sample, an already-restored count for idempotence) and writes nothing. A
    human mints the approval with `mokata approve <id>`; re-call with that `proposal_id` to dedup,
    gate-add new items, and route conflicts through the self-healing surface (never a silent
    overwrite). The backup content is UNTRUSTED, so each item is secret-scanned through the WriteGate
    and audit-logged — a secret is HARD-BLOCKED, not imported. Every restored item is stamped with
    import provenance (source file · batch · imported-at); original provenance is preserved."""
    from ..memory import import_memory, load_memory_share, plan_memory_import
    surface = _surface(path)
    try:
        data = load_memory_share(file)
    except (OSError, ValueError) as exc:
        return {"status": "error", "message": f"cannot read {file}: {exc}"}
    plan = plan_memory_import(MemoryStore.from_surface(surface), data, source=file)
    n = plan.count

    args = {"path": path, "file": file, "incoming": n}
    gate = _consent(path, "memory_import", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "memory_import", args,
                        {"incoming": n, "already": plan.already, "sample": plan.sample},
                        target=f"memory:import:{file}",
                        summary=f"restore {n} memory item(s) from {file}",
                        approve=approve, confirm=confirm)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = import_memory(MemoryStore.from_surface(surface), data, assume_yes=True,
                        ledger=ledger, source=file)
    approval.record_redemption(ledger, _require(gate), committed=not res.aborted)
    return {"status": "aborted" if res.aborted else "committed",
            "committed": not res.aborted, "added": res.added, "skipped": res.skipped,
            "resolved": res.resolved, "declined": res.declined,
            "blocked": res.blocked, "errors": res.errors}
