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

from typing import Any, Dict, Optional

from .consent import _consent, _gated_write, _policy, _propose, _refused
from .registry import _mokata_dir, _surface


def worktree_clean(path: str = ".", approve: bool = False, confirm: Optional[bool] = None,
                   proposal_id: str = "") -> Dict[str, Any]:
    """Clean this repo's stale git worktrees: remove those whose branch is MERGED, that are clean
    and have no live session, and prune those whose directory is gone. Everything else is KEPT and
    listed with its reason. HUMAN-GATED (SI.3): PROPOSE-ONLY — it previews the plan and removes
    nothing. A human mints the approval with `mokata approve <id>`; re-call with that `proposal_id`
    to apply it. Branches are never deleted."""
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
                        preview=plan.render(), approve=approve, confirm=confirm)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        box["outcome"] = WM.apply_worktree_clean(surface, plan)
        return box["outcome"].to_dict()

    out = _gated_write(_mokata_dir(path), "code", plan.repo_root, "", _apply, gate,
                       _policy(path, "worktree_clean", human_approved=True))
    if "outcome" in box:
        out["message"] = box["outcome"].render("clean")
    return out


def worktree_remove(path: str = ".", target: str = "", force: bool = False,
                    approve: bool = False, confirm: Optional[bool] = None,
                    proposal_id: str = "") -> Dict[str, Any]:
    """Remove ONE git worktree (`target` = its path, its `worktree_list` label, or its branch). It
    is REFUSED for the main checkout and for a worktree with a live session (nothing overrides
    that), and for a dirty or unmerged one unless `force=true`. HUMAN-GATED (SI.3): PROPOSE-ONLY —
    a human mints the approval with `mokata approve <id>`; re-call with that `proposal_id` to
    remove it. The branch is kept."""
    from .. import worktree_manage as WM
    surface = _surface(path)
    if not target:
        return {"status": "error",
                "message": "worktree_remove needs `target` — a path, label or branch from "
                           "`worktree_list`."}
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
                                f"{forced}",
                        approve=approve, confirm=confirm)
    box: Dict[str, Any] = {}

    def _apply() -> Any:
        box["outcome"] = WM.remove_worktree(surface, row.path, force=force)
        return box["outcome"].to_dict()

    out = _gated_write(_mokata_dir(path), "code", row.path, "", _apply, gate,
                       _policy(path, "worktree_remove", human_approved=True))
    if "outcome" in box:
        out["message"] = box["outcome"].render("remove")
    return out
