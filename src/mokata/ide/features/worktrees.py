"""`/api/worktrees` — the IDE's Worktrees panel (0.0.26 P31, SESSION-WORKTREES).

Every Claude Code session works in its own git worktree (Claude Code's native `.claude/worktrees/`).
This feature shows them and lets the human take them home — every durable action through mokata's
approval channel (P2): the page PROPOSES, the human approves in their own terminal with
`mokata approve <id>` (the act the page cannot perform), the page re-calls with the proposal id and
the write happens once. The proposal is bound to the worktree's exact heads: a worktree that moved
after the approval is refused, not merged.

  GET  /api/worktrees                 every linked worktree: session/run, branch, base, ahead/behind,
                                      dirty count, last activity, merged?, locked?, resume command
  GET  /api/worktree/diff?session=    what ONE session changed — EXACT for a worktree session
                                      (`git diff <base>` + untracked: Bash-made changes included);
                                      the Bash-window attribution for one that is not. DiffSet shape:
                                      the review pane renders it.
  GET  /api/worktree/size?path=       disk size of one worktree — computed on request only
  POST /api/worktree/sync             {path, proposal_id?} — rebase onto the main checkout's branch
                                      (gated; required before merge when the branch is behind)
  POST /api/worktree/merge            {path, proposal_id?} — merge back into the main checkout's
                                      branch (conflicts shown first), then remove worktree + branch
  POST /api/worktree/delete           {path, discard?, proposal_id?} — refuses a worktree with work
                                      unless the approved proposal says `discard`
  POST /api/worktree/clean            {proposal_id?} — remove every merged, clean, unlocked worktree

`--demo`: sample rows, and every POST answers `stage: "sample"` with nothing written.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from ... import approval
from ... import worktree_ops as OPS

SAMPLE_ROWS = [
    {"path": "/sample/repo/.claude/worktrees/mk-r1-s1", "name": "mk-r1-s1",
     "branch": "worktree-mk-r1-s1", "head": "b" * 40, "base": "a" * 40, "behind": 0, "ahead": 3,
     "dirty": 1, "merged": False, "fresh": False, "locked": False, "prunable": False,
     "missing": False, "session": "S-1", "sessions": ["S-1"], "run": "R-1",
     "last_activity": 0.0, "claude_managed": True, "resume": "claude --resume S-1"},
    {"path": "/sample/repo/.claude/worktrees/mk-s2", "name": "mk-s2", "branch": "worktree-mk-s2",
     "head": "c" * 40, "base": "a" * 40, "behind": 2, "ahead": 0, "dirty": 0, "merged": True,
     "fresh": False, "locked": False, "prunable": False, "missing": False, "session": "S-2",
     "sessions": ["S-2"], "run": None, "last_activity": 0.0, "claude_managed": True,
     "resume": "claude --resume S-2"},
]


def session_diff(root: str, session: str) -> Dict[str, Any]:
    """The DiffSet (review-pane shape) of one session's changes, plus how they were attributed —
    `ide.diffs.session_diff`, the ONE session diff source every view uses (P33)."""
    from ..diffs import session_diff as one_source
    return one_source(root, session).facts()


def routes(reg, ctx) -> None:
    import asyncio
    from starlette.responses import JSONResponse

    state = ctx.state

    def _ledger():
        import os
        from ...govern.ledger import AuditLedger
        from ... import MOKATA_DIR
        return AuditLedger.from_mokata_dir(os.path.join(state.root, MOKATA_DIR))

    def read(_request) -> Dict[str, Any]:
        if state.demo:
            return {"ok": True, "sample": True, "main": "/sample/repo", "into": "main",
                    "into_head": "a" * 40, "note": "", "worktrees": SAMPLE_ROWS}
        return {**OPS.rows(state.root), "sample": False}

    def diff(request) -> Dict[str, Any]:
        sid = ctx.q(request, "session")
        if not sid:
            raise ctx.bad_request("session is required")
        if state.demo:
            return {"title": f"Session {sid} (sample)", "note": "", "added": 0, "removed": 0,
                    "files": [], "mode": "worktree", "session": sid, "worktree": "", "branch": "",
                    "base": "", "shared": [], "sample": True}
        return {**session_diff(state.root, sid), "sample": False}

    def size(request) -> Dict[str, Any]:
        path = ctx.q(request, "path")
        if not path:
            raise ctx.bad_request("path is required")
        if state.demo:
            return {"ok": True, "path": path, "bytes": 0, "files": 0, "complete": True,
                    "sample": True}
        return {**OPS.size(state.root, path), "sample": False}

    def _propose(tool: str, args: Dict[str, Any], target: str, summary: str, preview: str):
        p = approval.propose(state.root, tool=tool, args=args, run_id=OPS.APPROVAL_RUN,
                             target=target, summary=summary, preview=preview)
        from ...awaiting import APPROVE_CMD
        return {"proposal_id": p.proposal_id, "command": APPROVE_CMD.format(proposal_id=p.proposal_id),
                "expires_in": p.expires_in(time.time())}

    def _redeem(pid: str, tool: str, args: Dict[str, Any]):
        c = approval.redeem(state.root, pid, tool=tool, args=args, run_id=OPS.APPROVAL_RUN)
        if c.granted:
            try:
                approval.record_redemption(_ledger(), c.proposal)
            except (OSError, ValueError):
                pass                             # the write is licensed; a lost ledger line is said
        return c

    def _merge(body: Dict[str, Any]) -> Dict[str, Any]:
        plan = OPS.plan_merge(state.root, str(body.get("path") or ""))
        if not plan.get("ok"):
            return {"stage": "blocked", "plan": plan,
                    "reason": "; ".join(plan.get("blocked") or [plan.get("reason") or "refused"])}
        args = {k: plan[k] for k in ("path", "branch", "head", "into", "into_head")}
        pid = str(body.get("proposal_id") or "")
        if not pid:
            return {"stage": "proposed", "plan": plan, **_propose(
                OPS.TOOL_MERGE, args, plan["path"],
                f"merge {plan['branch']} ({plan['ahead']} commit(s)) into {plan['into']}, then "
                f"remove the worktree and its branch",
                f"worktree : {plan['path']}\nbranch   : {plan['branch']} @ {plan['head'][:12]}\n"
                f"into     : {plan['into']} @ {plan['into_head'][:12]}\nconflicts: none found")}
        c = _redeem(pid, OPS.TOOL_MERGE, args)
        if not c.granted:
            return {"stage": "refused", "code": c.code, "reason": c.reason, "plan": plan}
        return {"stage": "done", "result": OPS.merge(state.root, args), "plan": plan}

    def _sync(body: Dict[str, Any]) -> Dict[str, Any]:
        """SYNC-BEFORE-SHIP — rebase the worktree onto the main checkout's branch, gated like merge."""
        plan = OPS.plan_sync(state.root, str(body.get("path") or ""))
        if not plan.get("ok"):
            return {"stage": "blocked", "plan": plan,
                    "reason": "; ".join(plan.get("blocked") or [plan.get("reason") or "refused"])}
        args = {k: plan[k] for k in ("path", "branch", "head", "onto", "onto_head")}
        pid = str(body.get("proposal_id") or "")
        if not pid:
            return {"stage": "proposed", "plan": plan, **_propose(
                OPS.TOOL_SYNC, args, plan["path"],
                f"rebase {plan['branch']} onto {plan['onto']} ({plan['behind']} new commit(s) there)",
                f"worktree : {plan['path']}\nbranch   : {plan['branch']} @ {plan['head'][:12]}\n"
                f"onto     : {plan['onto']} @ {plan['onto_head'][:12]}\n"
                f"rewrites : the branch's {plan['ahead']} commit(s) on top of the new base; a "
                f"conflict aborts and leaves it unchanged")}
        c = _redeem(pid, OPS.TOOL_SYNC, args)
        if not c.granted:
            return {"stage": "refused", "code": c.code, "reason": c.reason, "plan": plan}
        return {"stage": "done", "result": OPS.sync(state.root, args), "plan": plan}

    def _delete(body: Dict[str, Any]) -> Dict[str, Any]:
        plan = OPS.plan_delete(state.root, str(body.get("path") or ""))
        if not plan.get("ok"):
            return {"stage": "blocked", "plan": plan, "reason": plan.get("reason") or "refused"}
        discard = bool(body.get("discard"))
        if plan["has_work"] and not discard:
            return {"stage": "blocked", "plan": plan,
                    "reason": "this worktree has work (" + "; ".join(plan["work"]) + ") — resume "
                              "needs it. Delete only with an explicit discard."}
        args = {"path": plan["path"], "branch": plan["branch"], "head": plan["head"],
                "discard": discard and plan["has_work"]}
        pid = str(body.get("proposal_id") or "")
        if not pid:
            what = ("DISCARDS " + "; ".join(plan["work"])) if args["discard"] else "no unmerged work"
            return {"stage": "proposed", "plan": plan, **_propose(
                OPS.TOOL_DELETE, args, plan["path"],
                f"delete worktree {plan['path']} and branch {plan['branch']} — {what}",
                f"worktree : {plan['path']}\nbranch   : {plan['branch']} @ {plan['head'][:12]}\n"
                f"session  : {plan['session'] or '-'} (its resume will no longer find this code)")}
        c = _redeem(pid, OPS.TOOL_DELETE, args)
        if not c.granted:
            return {"stage": "refused", "code": c.code, "reason": c.reason, "plan": plan}
        return {"stage": "done", "result": OPS.delete(state.root, args), "plan": plan}

    def _clean(body: Dict[str, Any]) -> Dict[str, Any]:
        plan = OPS.plan_clean(state.root)
        if not plan["ok"]:
            return {"stage": "blocked", "plan": plan,
                    "reason": "no worktree is merged, clean and unlocked"}
        args = {"items": plan["items"]}
        pid = str(body.get("proposal_id") or "")
        if not pid:
            return {"stage": "proposed", "plan": plan, **_propose(
                OPS.TOOL_CLEAN, args, f"{len(plan['paths'])} worktree(s)",
                f"remove {len(plan['paths'])} merged worktree(s) and their branches",
                "\n".join(f"{it['branch']} @ {it['head'][:12]}  {it['path']}"
                          for it in plan["items"]))}
        c = _redeem(pid, OPS.TOOL_CLEAN, args)
        if not c.granted:
            return {"stage": "refused", "code": c.code, "reason": c.reason, "plan": plan}
        return {"stage": "done", "result": OPS.clean(state.root, plan["paths"]), "plan": plan}

    def _action(fn, needs_path: bool):
        async def handler(request):
            body = await ctx.body(request)
            if not isinstance(body, dict):
                body = {}
            if needs_path and not str(body.get("path") or ""):
                return JSONResponse({"error": "BadRequest", "note": "path is required"},
                                    status_code=400)
            if state.demo:
                return JSONResponse({"stage": "sample", "sample": True,
                                     "reason": "SAMPLE data — nothing is merged or deleted"})
            out = await asyncio.to_thread(fn, body)
            return JSONResponse({**out, "sample": False})
        return handler

    reg.register("/api/worktrees", read, kind="json", feature="worktrees")
    reg.register("/api/worktree/diff", diff, kind="json", feature="worktrees")
    reg.register("/api/worktree/size", size, kind="json", feature="worktrees")
    reg.register("/api/worktree/merge", _action(_merge, True), kind="action", feature="worktrees")
    reg.register("/api/worktree/sync", _action(_sync, True), kind="action", feature="worktrees")
    reg.register("/api/worktree/delete", _action(_delete, True), kind="action", feature="worktrees")
    reg.register("/api/worktree/clean", _action(_clean, False), kind="action", feature="worktrees")
