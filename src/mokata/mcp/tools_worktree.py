"""WORKTREE write tools — WT.S3 FR-WT-2/3/6 (0.0.23 E3): `worktree_clean` and `worktree_remove`.

The destructive half of the worktree surface, in-harness and HUMAN-GATED like every other MCP write
(SI.3, the `reset` shape): the first call PROPOSES — it renders the exact plan/target and removes
nothing; a human mints the approval out-of-band (`mokata approve <id>`); the re-call with that
`proposal_id` commits once. The approval is CONTENT-BOUND: the proposal args carry the plan's
actionable paths (clean) or the resolved target + force (remove), so a plan that changed between
the preview and the re-call no longer matches what the human approved and is re-proposed, never
applied. The removal itself is re-checked at apply time (`worktree_manage`) and goes through
`WorktreeManager.remove` — the one remove path.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Literal

from .consent import _consent, _gated_write, _policy, _propose, _refused
from .registry import _mokata_dir, _surface
from .tool_annotations import Field
from .validation import ValidationError

_PATH = "Repo root the tool operates on (default: the server's working directory)."
_PROPOSAL = ("Id of a proposal a human approved with `mokata approve <id>`; leave empty on the "
             "first call.")


# Provenance (developers): WT.S3 FR-WT-2 (0.0.23 E3). Human-gated (SI.3): the proposal args carry the
# plan's actionable paths, so a plan that changed between preview and re-call no longer matches the
# approval and is re-proposed, never applied. The removal is re-checked at apply time. A plan that
# cannot be built (git unreadable) is a genuine mokata-side fault, so it stays `status: "error"`.
# No required parameter: it cleans the whole repo's stale worktrees.
def worktree_clean(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Remove this repo's stale git worktrees: merged, clean ones with no live session, and ones whose folder is gone.

    Everything else is kept and listed with its reason. Branches are never deleted. Nothing is removed until a human approves the exact plan.

    Returns: `proposed` with the plan and a `proposal_id`, then the removal outcome; `noop` when nothing is stale.
    Errors: error when git cannot list the worktrees.
    """
    from .. import worktree_manage as WM
    surface = _surface(path)
    plan = WM.plan_worktree_clean(surface)
    if not plan.ok:
        return {"status": "error", "message": plan.render(), "plan": plan.to_dict()}
    if not plan.actionable:
        return {"status": "noop", "message": plan.render(), "plan": plan.to_dict()}
    args = {"path": path,
            "items": sorted(f"{i.action}:{i.path}" for i in plan.actionable)}
    gate = _consent(path, "worktree_clean", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        return _propose(path, "worktree_clean", args, {"plan": plan.to_dict()},
                        target=plan.repo_root,
                        summary=f"clean {len(plan.actionable)} git worktree(s)",
                        preview=plan.render())
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        box["outcome"] = WM.apply_worktree_clean(surface, plan)
        return box["outcome"].to_dict()

    out = _gated_write(_mokata_dir(path), "code", plan.repo_root, "", _apply, gate,
                       _policy(path, "worktree_clean", human_approved=True))
    if "outcome" in box:
        out["message"] = box["outcome"].render("clean")
    return out


# Provenance (developers): WT.S3 FR-WT-3. Human-gated (SI.3). Refused for the main checkout and for
# a worktree with a live session (nothing overrides that), and for a dirty/unmerged one unless
# `force`. The branch is kept. A refusal carries `remedyCommand` so a client can offer the next step.
def worktree_remove(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        target: Annotated[str, Field(
            description="Worktree to remove: its path, `worktree_list` label, or branch.")],
        force: Annotated[bool, Field(
            description="Also remove a dirty or unmerged worktree, discarding its work.")] = False,
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Remove ONE git worktree by path, label or branch; the branch itself is kept.

    Refused for the main checkout and for any worktree with a live session. A dirty or unmerged worktree needs `force`, which discards its uncommitted work. Nothing is removed until a human approves.

    Returns: `proposed` with a `proposal_id`, then the removal outcome; `refused` with a reason when the worktree cannot be removed.
    Errors: refused (invalid_input) when `target` is empty.
    """
    from .. import worktree_manage as WM
    surface = _surface(path)
    if not target:
        raise ValidationError("target", "is required: a path, label or branch from `worktree_list`")
    row, refusal = WM.check_remove(surface, target, force=force)
    if row is None or refusal:
        return {"status": "refused", "committed": False,
                "message": f"mokata worktree remove: refused — {refusal}" if row is not None
                           else f"mokata worktree remove: {refusal}",
                "remedyCommand": "mokata worktree list"}
    args = {"path": path, "target": row.path, "force": bool(force)}
    gate = _consent(path, "worktree_remove", args, proposal_id)
    if gate.refused:
        return _refused(gate)
    if not gate.granted:
        forced = " — FORCED: uncommitted / unmerged work is discarded" if force else ""
        return _propose(path, "worktree_remove", args,
                        {"worktree": row.to_dict()}, target=row.path,
                        summary=f"remove worktree {row.label}{forced}",
                        preview=f"remove {row.path} (branch '{row.branch or '(detached)'}' kept)"
                                f"{forced}")
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        box["outcome"] = WM.remove_worktree(surface, row.path, force=force)
        return box["outcome"].to_dict()

    out = _gated_write(_mokata_dir(path), "code", row.path, "", _apply, gate,
                       _policy(path, "worktree_remove", human_approved=True))
    if "outcome" in box:
        out["message"] = box["outcome"].render("remove")
    return out


# Provenance (developers): 0.0.27 P21 (ID-MERGE-GATED). Merges a session worktree's branch into the
# main checkout's branch. Without `proposal_id` it returns the plan (evidence at the branch head: full
# suite passed there, every AC has a passing test, review passed at that head) and, when nothing
# blocks, a proposal a HUMAN approves in their own terminal: an in-chat approval does not license a
# merge. Re-call with the id: the branch moves by compare-and-swap, then the main checkout is brought
# to the merge. No fetch, no push; the worktree is kept. `target` is NOT required: the redeem call
# reloads the worktree from the proposal, so a re-call needs only the id (hence no required param).
def worktree_merge(
        path: Annotated[str, Field(description=_PATH)] = ".", *,
        target: Annotated[str, Field(
            description="Session worktree to merge: its path or branch. Not needed with `proposal_id`.")] = "",
        strategy: Annotated[Literal["merge-commit", "squash", "ff"], Field(
            description="How the branch joins the main one.")] = "merge-commit",
        message: Annotated[str, Field(
            description="Commit message for the merge; default is generated.")] = "",
        proposal_id: Annotated[str, Field(description=_PROPOSAL)] = "") -> Dict[str, Any]:
    """Merge a session worktree's branch into the main checkout's branch, once its evidence passes and a human approves.

    Without `proposal_id` it returns the plan: the suite, criteria and review evidence at the branch head and every blocker by name. A human must approve in their own terminal; an in-chat approval does not license a merge. Re-call with that `proposal_id` to merge. The worktree is kept; nothing is fetched or pushed.

    Returns: `proposed` with the plan, `blocked` listing blockers, or `committed` once merged.
    Errors: refused (invalid_input) when `target` is empty or unknown; refused when the merge is read-only or the approval is unusable.
    """
    from .. import merge_gate as MG
    from ..awaiting import awaiting_block
    from ..govern.trust import MCP_SURFACE
    from .. import approval
    from .consent import _trust
    if not _trust(path).can_write(MG.TOOL, MCP_SURFACE):
        return {"status": "refused", "committed": False, "reason_code": "read-only",
                "reason": f"'{MG.TOOL}' is read-only on the mcp surface (settings.trust)"}
    if proposal_id:
        ok, code, reason = MG.ready_to_run(path, proposal_id)
        if not ok:
            return {"status": "refused", "committed": False, "reason_code": code,
                    "reason": reason}

        def through_consent(args: Dict[str, Any]) -> approval.Consent:
            # THE MCP consent check (trust + a human-minted record for this exact merge), redeemed
            # at the merge's commit point — never earlier, so a refusal before it burns nothing.
            return _consent(path, MG.TOOL, args, proposal_id, run_id=MG.APPROVAL_RUN)
        out = MG.run_merge(path, proposal_id, redeem=through_consent)
        return {"status": "committed" if out.merged else out.state, "committed": out.merged,
                **out.to_dict()}
    if not target:
        raise ValidationError("target", "is required without `proposal_id`: a session worktree's "
                              "path or branch from `worktree_list`")
    try:
        plan = MG.plan_merge(path, target, strategy, message or None)
    except MG.MergeGateError as exc:
        # An unknown target (or other unanswerable request) is the caller's to fix; the message
        # says what to do.
        raise ValidationError("target", str(exc)) from exc
    if not plan.ok:
        return {"status": "blocked", "committed": False, "state": plan.state,
                "blocked": plan.blocked, "message": plan.render(), "plan": plan.to_dict()}
    prop = MG.propose(path, plan)
    out: Dict[str, Any] = awaiting_block(prop["proposal_id"], MG.TOOL)
    out.update({"status": "proposed", "action": MG.TOOL, "committed": False,
                "proposal_id": prop["proposal_id"], "expires_in": prop["expires_in"],
                "preview": plan.render(), "plan": plan.to_dict(),
                "hint": (f"NOTHING was merged. Ask the human to run:  {prop['command']}  in their "
                         f"own terminal — then re-call worktree_merge with "
                         f"proposal_id=\"{prop['proposal_id']}\".")})
    return out
