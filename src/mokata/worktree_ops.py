"""P31 SESSION-WORKTREES — the worktree facts the IDE's Worktrees panel shows, and the three durable
actions it offers (merge back, delete, clean merged). Claude Code creates the worktrees; this module
reads them and, on a HUMAN-MINTED approval only, merges / removes them (rulings by Jas, 2026-10-22:
merging back is approved by the user in the IDE; the worktree is cleaned after a merge).

  * `rows`   — every linked worktree: session/run (from the P31 bindings), branch, base, ahead/behind
               the main checkout's HEAD, dirty count, last activity, merged?, locked?, the resume
               command. Bounded (MAX_ROWS, a deadline on the per-worktree `git status`), on request
               only — nothing runs at idle.
  * `size`   — disk size of ONE worktree, on request (bounded walk).
  * `plan_*` — read-only previews: what a merge / delete / clean would do, and why it may not
               (dirty worktree, unmerged work, conflicts via `git merge-tree`, a lock held by a
               running Claude Code session). The IDE turns a plan into an approval proposal.
  * `merge` / `delete` / `clean` — the writes. Called ONLY after `approval.redeem` granted the exact
               proposal (the IDE feature does that); each re-checks its plan first, so a worktree
               that changed since the human approved is refused, not merged.

Never deletes a worktree with work in it unless the delete was approved WITH `discard` — resume
depends on the worktree being there. Copyright 2026 MoStack. Apache License 2.0.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

from .repo_identity import canonical_repo_root, worktree_toplevel
from .session_worktrees import (_git, base_commit, bindings, branch_of, head_of, is_git, _gitdir_of,
                                _session_run, NOT_GIT)

MAX_ROWS = 50
DIRTY_DEADLINE_SECS = 8.0
SIZE_MAX_ENTRIES = 300_000
SIZE_DEADLINE_SECS = 10.0
MERGE_TIMEOUT = 120.0

APPROVAL_RUN = "ide-worktrees"
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}$")
TOOL_MERGE = "worktree_merge"
TOOL_DELETE = "worktree_delete"
TOOL_CLEAN = "worktree_clean_merged"
TOOL_SYNC = "worktree_sync"
REBASE_TIMEOUT = 300.0


def _real(p: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(p))


def _list(main: str) -> Optional[List[Any]]:
    from .worktree import _parse_worktree_porcelain
    code, out = _git(main, "worktree", "list", "--porcelain")
    return _parse_worktree_porcelain(out) if code == 0 else None


def _count(main: str, a: str, b: str) -> Optional[tuple]:
    """(behind, ahead) of `b` relative to `a`."""
    code, out = _git(main, "rev-list", "--left-right", "--count", f"{a}...{b}", "--")
    if code != 0:
        return None
    try:
        left, right = out.split()
        return int(left), int(right)
    except ValueError:
        return None


def _dirty(path: str) -> Optional[int]:
    code, out = _git(path, "--no-optional-locks", "status", "--porcelain=v1", "-z",
                     "--untracked-files=normal")
    if code != 0:
        return None
    return len([e for e in out.split("\0") if e and e[:2] != "  "])


def _last_activity(path: str, b: Optional[Dict[str, Any]]) -> float:
    best = float((b or {}).get("last_seen") or 0)
    gd = _gitdir_of(path)
    for name in ("index", "HEAD", "logs/HEAD"):
        if not gd:
            break
        try:
            best = max(best, os.path.getmtime(os.path.join(gd, *name.split("/"))))
        except OSError:
            continue
    return best


def _bound(root: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sid, b in bindings(root).items():
        out.setdefault(_real(str(b.get("path", ""))), []).append(b)
    for rows in out.values():
        rows.sort(key=lambda b: float(b.get("last_seen") or 0), reverse=True)
    return out


def rows(root: str) -> Dict[str, Any]:
    main = canonical_repo_root(root)
    if not is_git(main):
        return {"ok": False, "note": NOT_GIT, "main": main, "worktrees": []}
    wts = _list(main)
    if wts is None:
        return {"ok": False, "note": "git could not list this repo's worktrees", "main": main,
                "worktrees": []}
    into = branch_of(main)
    into_head = head_of(main)
    by_path = _bound(root)
    deadline = time.monotonic() + DIRTY_DEADLINE_SECS
    out: List[Dict[str, Any]] = []
    linked = [w for w in wts if _real(w.path) != _real(main)]
    for w in linked[:MAX_ROWS]:
        bs = by_path.get(_real(w.path), [])
        b = bs[0] if bs else None
        base = str((b or {}).get("base") or "")
        if not base and w.head and os.path.isdir(w.path):
            base = base_commit(w.path, w.branch)   # the branch's creation point (its reflog)
        ba = _count(main, into_head, w.head) if (into_head and w.head) else None
        dirty = _dirty(w.path) if (os.path.isdir(w.path) and time.monotonic() < deadline) else None
        ahead = ba[1] if ba else None
        merged = bool(ahead == 0 and w.head and base and w.head != base)
        sid = str((b or {}).get("session") or "")
        out.append({
            "path": w.path, "name": os.path.basename(w.path.rstrip("/\\")), "branch": w.branch,
            "head": w.head, "base": base, "behind": ba[0] if ba else None, "ahead": ahead,
            "dirty": dirty, "merged": merged, "fresh": bool(w.head and w.head == base and not dirty),
            "locked": bool(w.locked), "prunable": bool(w.prunable),
            "missing": not os.path.isdir(w.path),
            "session": sid, "sessions": [str(x.get("session")) for x in bs],
            "run": _session_run(root, sid) if sid else None,
            "last_activity": _last_activity(w.path, b) if os.path.isdir(w.path) else None,
            "claude_managed": _real(os.path.dirname(w.path.rstrip("/\\"))) ==
                              _real(os.path.join(main, ".claude", "worktrees")),
            "resume": f"claude --resume {sid}" if sid else "",
        })
    note = ""
    if len(linked) > MAX_ROWS:
        note = f"showing {MAX_ROWS} of {len(linked)} worktrees"
    if time.monotonic() >= deadline:
        note = (note + "; " if note else "") + "dirty counts stopped at the time budget (shown as ?)"
    return {"ok": True, "main": main, "into": into, "into_head": into_head, "note": note,
            "worktrees": out}


def find(root: str, path: str) -> Optional[Dict[str, Any]]:
    """The listed linked worktree at `path` (never the main checkout), or None."""
    if not path:
        return None
    want = _real(path)
    for r in rows(root).get("worktrees", []):
        if _real(r["path"]) == want:
            return r
    return None


def size(root: str, path: str) -> Dict[str, Any]:
    """Disk size of ONE listed worktree — computed on request, bounded by entries and time."""
    row = find(root, path)
    if row is None:
        return {"ok": False, "note": "not a linked worktree of this repo"}
    total, files, complete = 0, 0, True
    deadline = time.monotonic() + SIZE_DEADLINE_SECS
    stack = [row["path"]]
    seen = 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > SIZE_MAX_ENTRIES or time.monotonic() > deadline:
                        complete = False
                        stack.clear()
                        break
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return {"ok": True, "path": row["path"], "bytes": total, "files": files, "complete": complete}


# ================================================================================ plans
def _conflicts(main: str, into_head: str, head: str) -> Dict[str, Any]:
    """`git merge-tree --write-tree` (git ≥ 2.38): conflicts WITHOUT touching any tree."""
    code, out = _git(main, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                     into_head, head)
    if code == 0:
        return {"checked": True, "files": []}
    if code == 1:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return {"checked": True, "files": lines[1:]}
    return {"checked": False, "files": [],
            "note": "conflicts not pre-checked (needs git 2.38+ `merge-tree --write-tree`)"}


def plan_merge(root: str, path: str) -> Dict[str, Any]:
    row = find(root, path)
    if row is None:
        return {"ok": False, "reason": "not a linked worktree of this repo"}
    main = canonical_repo_root(root)
    plan = {"action": "merge", "path": row["path"], "branch": row["branch"], "head": row["head"],
            "into": branch_of(main), "into_head": head_of(main), "ahead": row["ahead"],
            "dirty": row["dirty"], "locked": row["locked"]}
    why = []
    if row["branch"] and not _SAFE_BRANCH.match(row["branch"]):
        why.append(f"refusing an unusual branch name {row['branch']!r}")
    if not row["branch"]:
        why.append("the worktree is on a detached HEAD — there is no branch to merge")
    if not plan["into"]:
        why.append("the main checkout is on a detached HEAD — check out the branch to merge into")
    if row["dirty"]:
        why.append(f"{row['dirty']} uncommitted change(s) in the worktree — commit them in the "
                   f"session (or discard) first; a merge takes commits only")
    if row["dirty"] is None:
        why.append("the worktree's status could not be read")
    if row["ahead"] == 0:
        why.append("nothing to merge — the branch has no commits the main checkout lacks")
    if not why and plan["into_head"] and row["head"]:
        c = _conflicts(main, plan["into_head"], row["head"])
        plan["conflicts"] = c
        if c["files"]:
            why.append(f"{len(c['files'])} file(s) conflict: " + ", ".join(c["files"][:10]))
    # SYNC-BEFORE-SHIP (Jas 2026-10-27): "always first rebase the worktree from develop, so we
    # don't lose changes between worktree creation and the PR". A branch BEHIND its base was tested
    # against a base that no longer exists; merging it lands code nobody ran with what landed since.
    behind = row.get("behind") or 0
    if behind > 0:
        why.append(f"{behind} commit(s) landed on {plan['into'] or 'the base'} since this worktree "
                   f"branched — Sync it first (rebase onto {plan['into'] or 'the base'}), re-run its "
                   f"tests, then merge")
        plan["behind"] = behind
    plan["blocked"] = why
    plan["ok"] = not why
    return plan


def plan_sync(root: str, path: str) -> Dict[str, Any]:
    """Rebase a session worktree's branch onto the main checkout's current branch — the step every
    merge and every PR now starts with (SYNC-BEFORE-SHIP). Local only: the IDE never goes online, so
    the base is the main checkout's branch as it is; the ship skill fetches first in the session."""
    row = find(root, path)
    if row is None:
        return {"ok": False, "reason": "not a linked worktree of this repo"}
    main = canonical_repo_root(root)
    plan = {"action": "sync", "path": row["path"], "branch": row["branch"], "head": row["head"],
            "onto": branch_of(main), "onto_head": head_of(main),
            "behind": row.get("behind") or 0, "ahead": row["ahead"]}
    why = []
    if not row["branch"] or not _SAFE_BRANCH.match(row["branch"] or ""):
        why.append("the worktree is not on a plain branch — nothing to rebase")
    if not plan["onto"]:
        why.append("the main checkout is on a detached HEAD — check out the base branch first")
    if row["dirty"]:
        why.append(f"{row['dirty']} uncommitted change(s) in the worktree — commit them in the "
                   f"session first; a rebase moves commits only")
    if row["dirty"] is None:
        why.append("the worktree's status could not be read")
    if row["locked"]:
        why.append("a running Claude Code session holds this worktree — rebasing under it would "
                   "change its files mid-turn; sync when the session is idle or ended")
    if not why and plan["behind"] == 0:
        why.append(f"already up to date with {plan['onto']}")
    plan["blocked"] = why
    plan["ok"] = not why
    return plan


def sync(root: str, approved: Dict[str, Any]) -> Dict[str, Any]:
    """Rebase the approved worktree branch onto the approved base head. Re-plans first (anything
    that moved since the approval refuses); a conflicting rebase is ABORTED, so the branch is left
    exactly as it was and the conflicting files are named."""
    plan = plan_sync(root, str(approved.get("path", "")))
    if not plan.get("ok"):
        return {"synced": False, "reason": "; ".join(plan.get("blocked") or [plan.get("reason", "")])}
    for k in ("head", "onto", "onto_head", "branch"):
        if plan.get(k) != approved.get(k):
            return {"synced": False, "reason": f"the worktree or its base changed since you approved "
                                               f"({k}) — review and approve again"}
    wt = plan["path"]
    code, out = _git(wt, "rebase", plan["onto_head"], timeout=REBASE_TIMEOUT)
    if code != 0:
        _, files = _git(wt, "diff", "--name-only", "--diff-filter=U")
        _git(wt, "rebase", "--abort")
        conflicts = [ln for ln in (files or "").splitlines() if ln.strip()]
        return {"synced": False, "conflicts": conflicts,
                "reason": ("rebase stopped on conflicts (aborted — the branch is unchanged): "
                           + ", ".join(conflicts[:10])) if conflicts else
                          f"git rebase failed (aborted, nothing changed): {out.strip()[:400]}"}
    return {"synced": True, "onto": plan["onto"], "head": head_of(wt),
            "next": "re-run this worktree's tests before you merge or raise the PR"}


def plan_delete(root: str, path: str) -> Dict[str, Any]:
    row = find(root, path)
    if row is None:
        return {"ok": False, "reason": "not a linked worktree of this repo"}
    unmerged = bool((row["ahead"] or 0) > 0 and not row["merged"])
    work = []
    if unmerged:
        work.append(f"{row['ahead']} commit(s) not in the main checkout")
    if row["dirty"]:
        work.append(f"{row['dirty']} uncommitted change(s)")
    if row["dirty"] is None and not row["missing"]:
        work.append("its status could not be read (treated as having work)")
    plan = {"action": "delete", "path": row["path"], "branch": row["branch"], "head": row["head"],
            "work": work, "has_work": bool(work), "locked": row["locked"],
            "session": row["session"], "ok": not row["locked"]}
    if row["locked"]:
        plan["reason"] = "locked — a running Claude Code session holds this worktree"
    return plan


def plan_clean(root: str) -> Dict[str, Any]:
    targets = [r for r in rows(root).get("worktrees", [])
               if r["merged"] and r["dirty"] == 0 and not r["locked"]]
    return {"action": "clean", "paths": [r["path"] for r in targets],
            "items": [{"path": r["path"], "branch": r["branch"], "head": r["head"]} for r in targets],
            "ok": bool(targets)}


# ================================================================================ the writes
def _remove(main: str, path: str, branch: str, force: bool) -> List[str]:
    """Remove the worktree, then its branch. Returns problems ([] = both gone)."""
    problems = []
    args = ["worktree", "remove"] + (["--force"] if force else []) + [os.path.abspath(path)]
    code, out = _git(main, *args)
    if code != 0:
        problems.append(f"worktree not removed: {out.strip()[:300]}")
        return problems
    if branch and _SAFE_BRANCH.match(branch):
        code, out = _git(main, "branch", "-D" if force else "-d", branch)
        if code != 0:
            problems.append(f"branch {branch} kept: {out.strip()[:300]}")
    return problems


def merge(root: str, approved: Dict[str, Any]) -> Dict[str, Any]:
    """Merge the approved branch into the main checkout's branch, then remove the worktree + branch.
    Re-plans first: anything that moved since the approval (either head) refuses."""
    plan = plan_merge(root, str(approved.get("path", "")))
    if not plan.get("ok"):
        return {"merged": False, "reason": "; ".join(plan.get("blocked") or [plan.get("reason", "")])}
    for k in ("head", "into", "into_head", "branch"):
        if plan.get(k) != approved.get(k):
            return {"merged": False, "reason": f"the worktree changed since you approved ({k}) — "
                                               f"review and approve again"}
    main = canonical_repo_root(root)
    code, out = _git(main, "merge", "--no-ff", "--no-edit", plan["branch"],
                     timeout=MERGE_TIMEOUT)
    if code != 0:
        _git(main, "merge", "--abort")
        return {"merged": False, "reason": f"git merge failed (aborted, nothing changed): "
                                           f"{out.strip()[:400]}"}
    problems = _remove(main, plan["path"], plan["branch"], force=False)
    return {"merged": True, "into": plan["into"], "merge_head": head_of(main),
            "removed": not problems, "problems": problems}


def delete(root: str, approved: Dict[str, Any]) -> Dict[str, Any]:
    plan = plan_delete(root, str(approved.get("path", "")))
    if not plan.get("ok"):
        return {"deleted": False, "reason": plan.get("reason") or "not deletable"}
    if plan["head"] != approved.get("head"):
        return {"deleted": False, "reason": "the worktree changed since you approved — approve again"}
    if plan["has_work"] and not approved.get("discard"):
        return {"deleted": False, "reason": "refused — the worktree has work (" +
                "; ".join(plan["work"]) + "). Approve the delete WITH discard to throw it away."}
    problems = _remove(canonical_repo_root(root), plan["path"], plan["branch"],
                       force=bool(approved.get("discard")))
    return {"deleted": not problems, "problems": problems}


def clean(root: str, approved_paths: List[str]) -> Dict[str, Any]:
    plan = plan_clean(root)
    allowed = {_real(p): it for p, it in zip(plan["paths"], plan["items"])}
    done, skipped = [], []
    main = canonical_repo_root(root)
    for p in approved_paths:
        it = allowed.get(_real(p))
        if it is None:
            skipped.append({"path": p, "reason": "no longer merged-and-clean"})
            continue
        problems = _remove(main, it["path"], it["branch"], force=False)
        (skipped.append({"path": p, "reason": "; ".join(problems)}) if problems else done.append(p))
    return {"removed": done, "skipped": skipped}


__all__ = ["rows", "find", "size", "plan_merge", "plan_sync", "plan_delete", "plan_clean", "merge",
           "sync", "delete", "clean", "APPROVAL_RUN", "TOOL_MERGE", "TOOL_SYNC", "TOOL_DELETE",
           "TOOL_CLEAN"]
