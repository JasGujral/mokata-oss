"""MEMORY write tools — human-gated (SI.3) memory writes.

Domain split out of `mcp/tools_write.py` (PRE-SIMP, release 0.0.15). These are the memory writers
that STAY through SIMP — the CORE writers (`remember`, `apply_proposal`) AND the memory BACKUP
surface (`memory_export` = backup, `memory_import` = restore), re-homed here at 35b from
`tools_share.py`. The backup surface survives SIMP as the ONE gated backup-and-restore command; only
`vault_push` (the design-artifact vault's gated write — NOT deprecated; the removed `vault`
channel was the session-transport kind) stays in `tools_share.py`, formerly filed for the SIMP.S3
deletion. Registration order + tool names are preserved by the `tools_write.py` aggregator; every
tool routes through the one consent boundary in `mcp/consent.py`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from .. import approval
from ..govern import AuditLedger
from ..memory import CROSS_WRITER, DECISION, MemoryItem, MemoryStore
from .consent import (_consent, _gated_write, _policy, _propose, _refused,
                      _require)
from .registry import _surface
from .tool_annotations import Field
from .validation import ValidationError

_Path = Annotated[str, Field(
    description="Repo root the tool operates on (default: the server's working directory).")]
_ProposalId = Annotated[str, Field(
    description="Id of a proposal a human approved with `mokata approve <id>`; omit on the first call.")]


# remember — gated (SI.3): propose-only; only a human-minted `proposal_id` commits, once. A secret in
# `subject` OR `value` is blocked even when approved (H4). A part kind is stored as persistent
# project knowledge.
# PM.S1 — `kind="intention"` is a PROSPECTIVE memory ("remind me when X"): `due_at` (YYYY-MM-DD or ISO
# instant) and/or `when_keyword` / `when_path` (comma-separated) are its due condition; surfaced at
# SessionStart and by the healing sweep; marked done through the gated `apply_proposal`.
# R5 — GROUNDED: every citation the write carries (`about_code` anchors, paths / `path:line` / commit
# ids / links in the value, `retire_when` predicates) must resolve against the repo, or the write is
# REFUSED before any proposal is minted and the refusal names the citation. Symbols are checked
# through the code graph when one is adopted. `retire_when` predicates: `deleted:<path>`,
# `removed:<symbol>`, `after:<commit>`, `date:<YYYY-MM-DD>`; when one fires the learning is surfaced
# for a refresh verb (keep/update/consolidate/replace/delete via `apply_proposal`). Text inside
# `<private>…</private>` is never stored. 0.0.28 I2: `mtype` (deprecated alias) and `approve`/`confirm`
# deleted; subject/value required and non-blank (a blank value used to mint an empty proposal).
def remember(path: _Path = ".", *,
             subject: Annotated[str, Field(
                 description="Short title of the fact or decision; must not be blank.")],
             value: Annotated[str, Field(
                 description="The fact or decision text to store; must not be blank.")],
             memory_type: Annotated[Literal["persistent", "decision", "episodic"], Field(
                 description="Storage tier (default decision); a part or intention kind forces persistent.")] = DECISION,
             kind: Annotated[Literal["", "rule", "guardrail", "best-practice", "context",
                                     "reference", "decision", "episodic", "intention"], Field(
                 description="Typed part, or intention for a reminder; empty means no part kind.")] = "",
             proposal_id: _ProposalId = "",
             about_code: Annotated[Optional[List[str]], Field(
                 description="Code anchors (paths, `path:line`, symbols) the memory is about; must resolve.")] = None,
             due_at: Annotated[str, Field(
                 description="Intention only: due date, YYYY-MM-DD or ISO-8601 instant.")] = "",
             when_keyword: Annotated[str, Field(
                 description="Intention only: comma-separated keywords that make it due.")] = "",
             when_path: Annotated[str, Field(
                 description="Intention only: comma-separated paths that make it due when touched.")] = "",
             retire_when: Annotated[Optional[List[str]], Field(
                 description="Expiry predicates: deleted:<path>, removed:<symbol>, after:<commit>, date:YYYY-MM-DD.")] = None,
             ) -> Dict[str, Any]:
    """Remember a fact, decision, rule or reminder in project memory; call it to record what should outlive this chat.

    Pass `subject` and `value`. `kind` files it as a typed part (rule, guardrail, best-practice,
    context, reference) or `intention` (a reminder with `due_at`, `when_keyword` or `when_path`).
    Every citation (`about_code`, paths and commit ids in the value, `retire_when`) must resolve in
    the repo, or the write is refused before a proposal exists. A secret in either field is blocked.

    Returns: status "proposed" (preview, grounding, overlap), then "committed" once redeemed;
    "refused" with `unresolved` citations; "unavailable" when memory is off for the profile.
    Errors: refused (invalid_input) when `subject` or `value` is blank or the due fields are misused."""
    from ..memory import INTENTION, PART_KINDS, PERSISTENT, normalize_kind
    from ..memory.intentions import IntentionError, make_envelope
    if not subject.strip():
        raise ValidationError("subject", "must not be blank")
    if not value.strip():
        raise ValidationError("value", "must not be blank")
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}
    mtype = memory_type
    norm = normalize_kind(kind)
    if norm in PART_KINDS:
        mtype = PERSISTENT          # the captured "parts" are persistent project knowledge
    applicability = None
    due_given = any((v or "").strip() for v in (due_at, when_keyword, when_path))
    if norm == INTENTION:
        try:
            applicability = make_envelope(due_at, when_keyword, when_path)
        except IntentionError:
            raise ValidationError(
                "due_at", "an intention needs a due condition: due_at as YYYY-MM-DD or an "
                          "ISO-8601 instant, and/or when_keyword / when_path")
        mtype = PERSISTENT          # an intention rides the persistent toggle
    elif due_given:
        # Never silently drop a due condition the caller thought it was setting.
        raise ValidationError("due_at", "due_at / when_keyword / when_path apply only to "
                                        "kind='intention'")
    item = MemoryItem.create(subject, value, mtype=mtype, kind=norm or kind,
                             about_code=about_code, applicability=applicability,
                             retire_when=retire_when)

    # R5 — refuse an ungrounded write BEFORE a proposal is minted: a human must never be asked to
    # approve a learning whose citations point at nothing. `store.remember` re-checks at commit.
    from ..memory.private import UnbalancedPrivateTag, strip_private
    try:
        strip_private(subject)
        strip_private(value)
    except UnbalancedPrivateTag as exc:
        return {"status": "refused", "committed": False, "message": str(exc)}
    grounding = store.grounding_report(item)
    if grounding.refusal() is not None:
        return {"status": "refused", "committed": False, "message": grounding.refusal(),
                "unresolved": [v.citation.raw for v in grounding.unresolved]}

    args = {"path": path, "subject": subject, "value": value, "memory_type": mtype, "kind": norm}
    if applicability is not None:
        # The due condition is part of WHAT the human approves — so it is part of the hash.
        args["due"] = applicability["due"]
    if about_code:
        args["about_code"] = list(about_code)
    if item.retire_when:
        args["retire_when"] = list(item.retire_when)
    gate = _consent(path, "remember", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        overlap = store.overlap_for(item)
        preview = store.render_write(item, grounding, overlap)
        payload = {"preview": preview,
                   "grounding": [line.strip() for line in grounding.lines()]}
        if overlap is not None:
            payload["overlap"] = overlap.describe()
            payload["updates_existing"] = overlap.updates
        return _propose(path, "remember", args, payload,
                        target=f"memory:{subject}", summary=f"remember '{subject}' = {value[:60]}",
                        preview=preview)
    # H4: scan subject AND value so a secret pasted into the subject can't slip the gate.
    return _gated_write(surface.mokata_dir, "memory", f"memory:{subject}",
                        f"{subject}\n{value}",
                        lambda: store.remember(item, assume_yes=True).committed, gate,
                        _policy(path, "remember", human_approved=True, surface=surface))


# apply_proposal — gated (SI.3): propose-only, shows the staged old->new change.
# DB.S6 — `discard` means "keep THEIRS": it drops your own approved-but-unlanded write when a
# teammate's concurrent change won the CAS. It has its own word rather than riding `reject` because
# `reject` is where every safe default lands (a dismissed consent, a non-interactive call), and a
# default that throws away an approved write is the silent loss that stage removed. Refused on any
# other kind of proposal.
# R5 — a `retire_due` proposal (a learning whose `retire_when` fired) is resolved with a REFRESH verb:
# keep / update / consolidate / replace / delete (`value` for update/consolidate/replace, `into_id`
# for consolidate, optional `about_code` for update/replace); verb, value and target are in the hash.
def apply_proposal(path: _Path = ".", *,
                   subject: Annotated[str, Field(
                       description="Subject of the pending self-healing proposal to resolve.")],
                   decision: Annotated[Literal["approve", "reject", "defer", "discard", "keep",
                                               "update", "consolidate", "replace", "delete"], Field(
                       description="Your verdict (default approve); discard is for cross-writer conflicts, the rest for retire_due.")] = "approve",
                   proposal_id: _ProposalId = "",
                   value: Annotated[str, Field(
                       description="New text for update, consolidate or replace; empty otherwise.")] = "",
                   into_id: Annotated[str, Field(
                       description="Target memory id for consolidate; empty otherwise.")] = "",
                   about_code: Annotated[Optional[List[str]], Field(
                       description="Replacement code anchors for update or replace; omit to keep them.")] = None,
                   ) -> Dict[str, Any]:
    """Resolve a surfaced self-healing memory proposal (contradiction, staleness, conflict or expiry).

    `decision` approve, reject or defer settles a contradiction or staleness; `discard` keeps a
    teammate's version over your unlanded write; keep, update, consolidate, replace or delete
    refresh a learning whose `retire_when` fired. It shows the staged old-to-new change first.

    Returns: status "proposed" (subject, kind, diff, decision), then "committed" with the outcome.
    Errors: refused (invalid_input) when `subject` is blank or has no pending proposal, or
    `decision` is not allowed."""
    from ..memory.healing import REFRESH_VERBS, RETIRE_DUE
    if not subject.strip():
        raise ValidationError("subject", "must not be blank")
    allowed = ("approve", "reject", "defer", "discard") + tuple(REFRESH_VERBS)
    if decision not in allowed:
        raise ValidationError("decision", "is not an allowed decision", allowed=list(allowed))
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    proposals = store.detect_issues()
    if decision in REFRESH_VERBS:
        proposals = [p for p in proposals if p.kind == RETIRE_DUE]
    if decision == "discard":
        # `discard` is only meaningful for a conflict, and a subject can carry BOTH a conflict and
        # an ordinary contradiction — so select by kind rather than letting first-match decide
        # which proposal a destructive decision lands on.
        proposals = [p for p in proposals if p.kind == CROSS_WRITER]
    match = next((p for p in proposals if p.subject == subject), None)
    if match is None:
        raise ValidationError("subject", "has no pending proposal for this decision")

    args = {"path": path, "subject": subject, "decision": decision, "diff": match.diff()}
    if decision in REFRESH_VERBS:
        args.update({"value": value, "into_id": into_id,
                     "about_code": list(about_code) if about_code is not None else None})
    gate = _consent(path, "apply_proposal", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "apply_proposal", args,
                        {"subject": subject, "kind": match.kind, "diff": match.diff(),
                         "decision": decision},
                        target=f"memory:{subject}",
                        summary=f"{decision} the self-healing proposal for '{subject}'",
                        preview=match.diff())

    def _apply() -> str:
        if decision in REFRESH_VERBS:
            return store.refresh(match, decision, value=value or None, into_id=into_id,
                                 about_code=about_code, assume_yes=True).message
        return store.apply_proposal(match, decision, assume_yes=True).message
    return _gated_write(
        surface.mokata_dir, "memory", f"memory:{subject}", match.diff(), _apply, gate,
        _policy(path, "apply_proposal", human_approved=True, surface=surface))


# consolidate (M-4/R5 PHASE 2) — gated (SI.3): propose-only. Submits a summary the MODEL drafted for an
# episodic cluster (see the `consolidate_proposals` read tool); mokata calls no model and writes no
# summary of its own. No argument here reaches a write, so the model cannot approve its own draft.
def consolidate(path: _Path = ".", *,
                session: Annotated[str, Field(
                    description="Session id of the episodic cluster, from `consolidate_proposals`.")],
                value: Annotated[str, Field(
                    description="The summary text you drafted for that cluster.")],
                proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Submit a summary you drafted for an episodic cluster; call it after `consolidate_proposals`.

    mokata writes no summary of its own: `value` is your draft. A human approves before it is stored,
    and a secret in the draft is blocked even then.

    Returns: status "proposed" (session, kind, diff, turns), then "committed"; "unavailable" when
    memory is off for the profile.
    Errors: refused (invalid_input) when `session` or `value` is blank, or no summarize proposal
    exists for that session."""
    from ..memory.consolidation import constant_drafter, find_summarize
    if not session.strip():
        raise ValidationError("session", "a session id from consolidate_proposals is required")
    if not value.strip():
        raise ValidationError("value", "the summary you drafted is required")
    surface = _surface(path)
    store = MemoryStore.from_surface(surface)
    if not store.enabled_types:
        return {"status": "unavailable", "message": "memory is disabled for this profile"}

    # The drafted text rides the DRAFTER SEAM, not a hand-built item: same typing, same always-on
    # clamp, same secret-scan, same gate render as any drafted summary. See `constant_drafter`.
    match = find_summarize(store.propose_consolidations(drafter=constant_drafter(session, value)),
                           session)
    if match is None:
        raise ValidationError("session", "has no summarize proposal; call consolidate_proposals "
                                         "to see what wants drafting")

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
                        preview=store.render_consolidation(match))
    # H4 discipline — scan the SUBJECT and the drafted VALUE: a drafted summary is exactly where a
    # credential quoted out of a conversation turn would enter.
    return _gated_write(
        surface.mokata_dir, "memory", f"memory:summary:{session}",
        f"{match.new.subject}\n{match.new.value}",
        lambda: store.apply_consolidation(match, "approve", assume_yes=True).message, gate,
        _policy(path, "consolidate", human_approved=True, surface=surface))


# ======================================================================================
# the memory BACKUP surface (35b) — export = backup, import = restore. Re-homed from
# `tools_share.py` (both survive SIMP.S3 — see that module's docstring for why the earlier claim
# that `vault_push` dies there was wrong). Backup = store → an
# explicit human-owned FILE (gated egress); restore = file → store via the WriteGate, provenance-
# stamped. It is a BACKUP surface, NOT a sharing channel — team sharing is the Postgres DSN alone.
# ======================================================================================

# memory_export — gated (SI.3): propose-only; READ-ONLY on the store, reports how many items WOULD be
# written. The default dest is a timestamped `.mokata/backups/memory-<UTC>.json`. Every value is
# secret-scanned at EGRESS strength (SI.6): a hit hard-blocks that item (named in `blocked`, left out).
# `file` defaults, so nothing is required (exempt).
def memory_export(path: _Path = ".",
                  file: Annotated[str, Field(
                      description="Backup file to write; empty writes a timestamped file under .mokata/backups/.")] = "",
                  proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Back up local memory (active items with provenance) to a JSON file; call it before risky changes.

    Reports how many items would be written and writes no file until a human approves. Every value is
    secret-scanned: a hit is named in `blocked` and left out of the backup.

    Returns: status "proposed" (items, dest, blocked, blocked_keys), then "committed" with `dest`.
    Errors: refused when `proposal_id` is not a live human approval of this exact call."""
    from ..memory import default_backup_path, export_memory, export_payload
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

    args = {"path": path, "dest": dest, "items": len(data["items"])}
    gate = _consent(path, "memory_export", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "memory_export", args,
                        {"items": len(data["items"]), "dest": dest,
                         "blocked": len(blocked), "blocked_keys": blocked},
                        target=dest, summary=f"back up {len(data['items'])} memory item(s) to {dest}")
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


# memory_import — gated (SI.3): propose-only. The backup content is UNTRUSTED, so each item is
# secret-scanned through the WriteGate and audit-logged (a secret is hard-blocked, not imported); every
# restored item is stamped with import provenance (source file · batch · imported-at) and original
# provenance is preserved. Conflicts route through the self-healing surface — never a silent overwrite.
def memory_import(path: _Path = ".", *,
                  file: Annotated[str, Field(
                      description="Memory backup JSON file to restore (from `memory_export`).")],
                  proposal_id: _ProposalId = "") -> Dict[str, Any]:
    """Restore a memory backup file into local memory; call it to recover or move memory.

    Previews counts and a keys-only sample, and writes nothing until a human approves. Items are
    de-duplicated and secret-scanned (a secret is blocked, not imported); conflicts go to the
    self-healing proposals instead of overwriting.

    Returns: status "proposed" (incoming, already, sample), then "committed" with added, skipped,
    resolved, declined, blocked and errors counts.
    Errors: refused (invalid_input) when `file` is empty, unreadable or not a memory backup."""
    from ..memory import import_memory, load_memory_share, plan_memory_import
    if not file.strip():
        raise ValidationError("file", "a memory backup path is required")
    surface = _surface(path)
    try:
        data = load_memory_share(file)
    except (OSError, ValueError):
        raise ValidationError("file", "cannot be read as a JSON memory backup")
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
                        summary=f"restore {n} memory item(s) from {file}")
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    res = import_memory(MemoryStore.from_surface(surface), data, assume_yes=True,
                        ledger=ledger, source=file)
    approval.record_redemption(ledger, _require(gate), committed=not res.aborted)
    return {"status": "aborted" if res.aborted else "committed",
            "committed": not res.aborted, "added": res.added, "skipped": res.skipped,
            "resolved": res.resolved, "declined": res.declined,
            "blocked": res.blocked, "errors": res.errors}
