"""G3-EVIDENCE (0.0.27) — `evidence_commit`, the in-harness twin of `mokata evidence commit`.

Commits a run's ledger rows and gate evidence to `.mokata/runs/<run>/` for the PR check. A durable
write: the first call PROPOSES and carries the EXACT bytes as the preview (nothing written); a human
approves out of band (`mokata approve <id>`); a re-call with that `proposal_id` writes them once
through the WriteGate. The approval is bound to the bytes (their hash is part of the call's args),
so a record that changed after approval is re-proposed, never committed. A secret in the projected
record BLOCKS before anything is proposed, and the result names the row, the field and the way out.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List

from .tool_annotations import Field

_PATH = "Repo root the tool operates on (default: the server's working directory)."


# Provenance (developers): G3-EVIDENCE (0.0.27), the in-harness twin of `mokata evidence commit`. The
# approval is bound to the exact bytes (their hash is in the call's args), so a record that changed
# after approval is re-proposed, never committed; on redeem the two re-derived values (`exported_at`,
# `head_sha`) come from the approved preview. A secret in the projected record BLOCKS before anything
# is proposed. The unreadable-ledger return stays `status: "error"` (a mokata-side fault). No required parameter:
# `run` defaults to the resolved run (an ambiguous repo refuses and lists candidates).
def evidence_commit(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        run: Annotated[str, Field(
            description="Run id to commit; default is the run resolved for this repo.")] = "",
        proposal_id: Annotated[str, Field(
            description="Id of a proposal a human approved with `mokata approve <id>`; leave "
                        "empty on the first call.")] = "") -> Dict[str, Any]:
    """Save a run's ledger rows and gate evidence into `.mokata/runs/<run>/` so the pull-request check can show them.

    The first call proposes and previews every byte; a human approves with `mokata approve <id>`, then re-call with `proposal_id` to write it once. A secret in the record blocks it and names the row and field. If the run has changed since the approved preview, it is re-proposed.

    Returns: `proposed` with the exact preview, then `committed` with `record`; `noop`, `blocked` or `refused` say why nothing was written.
    Errors: error when the run's local ledger or evidence cannot be read whole.
    """
    from .. import approval
    from ..govern.ledger import AuditLedger
    from ..run_record import commit_run_record, content_hash, frozen_meta, plan_run_record
    from ..run_resolver import resolve_run
    from .consent import _consent, _policy, _propose, _refused, _require
    from .registry import _surface
    surface = _surface(path)
    res = resolve_run(path, run_id=run or None)
    if not res.run_id:
        return {"status": "refused", "committed": False,
                "message": "no run could be resolved here — name it with `run`",
                "candidates": list(getattr(res, "candidates", ()) or ())}
    # M1 — on REDEEM the approval binds the exact bytes it previewed: the two values a re-plan would
    # re-derive (`exported_at`, `head_sha`) are taken from the approved preview, and every byte is
    # hashed into the call's args — so a source that changed since the preview refuses, and a
    # matching one writes byte-for-byte what the human saw. Never a silent re-render.
    approved = approval.load(path, proposal_id) if proposal_id else None
    try:
        plan = plan_run_record(path, surface.mokata_dir, res.run_id)
        if approved is not None and approved.tool == "evidence_commit":
            frozen = frozen_meta(approved.preview, plan)
            if frozen:
                plan = plan_run_record(path, surface.mokata_dir, res.run_id, **frozen)
    except (OSError, ValueError) as exc:
        return {"status": "error", "committed": False,
                "message": f"the run's machine-local ledger or evidence cannot be read whole "
                           f"({exc}) — nothing was proposed"}
    if plan.blocked:
        return {"status": "blocked", "committed": False, "message": plan.recovery(),
                "findings": [h.kind for h in plan.secrets]}
    if not plan.rows_exported and not plan.evidence_exported:
        return {"status": "noop", "committed": False,
                "message": f"run {res.run_id} has no ledger rows carrying its run id and no gate "
                           f"evidence on this machine — nothing to commit"}
    digest = content_hash(plan)
    if approved is not None and approved.tool == "evidence_commit" \
            and approved.preview != plan.preview():
        return {"status": "refused", "committed": False,
                "message": "refused: the run record's source changed since the preview that was "
                           "approved — nothing was written; re-propose it (call again without "
                           "`proposal_id`) and approve the new preview"}
    args = {"path": path, "run": res.run_id, "record_sha256": digest}
    gate = _consent(path, "evidence_commit", args, proposal_id, surface=surface)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        preview = plan.preview()
        return _propose(path, "evidence_commit", args,
                        {"preview": preview, "rows_exported": plan.rows_exported,
                         "evidence_exported": plan.evidence_exported,
                         "rows_unattributed": plan.rows_unattributed},
                        target=plan.rel_dir + "/",
                        summary=f"commit run record {plan.rel_dir}/ "
                                f"({plan.rows_exported} ledger row(s), "
                                f"{plan.evidence_exported} evidence record(s))",
                        preview=preview)
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    msgs: List[str] = []
    result = commit_run_record(path, res.run_id, plan=plan, expected=digest, ledger=ledger,
                               policy=_policy(path, "evidence_commit", human_approved=True,
                                              surface=surface),
                               out=msgs.append)
    approval.record_redemption(ledger, _require(gate), committed=result.committed)
    return {"status": "committed" if result.committed else "blocked",
            "committed": result.committed, "record": plan.rel_dir + "/",
            "reason": result.message}


__all__ = ["evidence_commit"]
