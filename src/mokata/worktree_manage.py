"""WT.S3 (0.0.23 E3) — the worktree MANAGEMENT surface: clean · remove · new-worktree notice · doctor.

WT-LIST (FR-WT-1) gave mokata an eye; this gives the human the hand, and keeps it a human's hand:

  * FR-WT-2 `clean`  — `plan_worktree_clean` builds a `WorktreePlan` over the SAME join `worktree
    list` renders (`worktree_list.build_worktree_report` — no second staleness rule), and
    `apply_worktree_clean` removes ONLY the items the plan marked `remove` (merged + clean + no live
    session) and prunes the `prune` items (directory already gone). Everything else is listed as
    KEPT with the reason and the exact per-item command. Never automatic: the CLI previews and asks
    (fail-closed `read_yes_no`), the MCP tool proposes and a human mints the approval.
  * FR-WT-3 `remove` — `remove_worktree` takes ONE target. It refuses the main checkout, a
    worktree with a LIVE session (no flag overrides that — close the session first), a dirty
    worktree and an unmerged branch; `force` overrides dirty/unmerged ONLY.
  * FR-WT-4 — `new_worktree_notice` is the SessionStart line for worktrees that appeared since the
    last session. ZERO subprocess (git's own `<common>/worktrees/*/gitdir` files are read), data
    only, degrade-clean; the first sighting records a baseline and says nothing.
  * FR-WT-6 — `worktree_findings` is doctor's worktree check (warning-level; it never fails doctor).

ONE REMOVE PATH: every removal goes through `worktree.WorktreeManager.remove` (which probes dirty,
fail-closed, runs `git worktree remove` + `prune`, and ledgers). Nothing here deletes a directory
itself. Naming per doc 85: `WorktreePlan` (proposal) · `WorktreeOutcome` (what happened).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .worktree_list import (VERDICT_ACTIVE, VERDICT_MERGED, VERDICT_UNKNOWN, WorktreeReport,
                            WorktreeRow, build_worktree_report)

ACTION_REMOVE = "remove"
ACTION_PRUNE = "prune"
ACTION_KEEP = "keep"

NOTICE_STATE_NAME = "worktrees_seen.json"


@dataclass
class WorktreePlanItem:
    path: str
    label: str
    branch: str
    verdict: str
    action: str                     # remove | prune | keep
    reason: str
    dirty: Optional[bool] = None    # None = not probed (kept for another reason first)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "label": self.label, "branch": self.branch,
                "verdict": self.verdict, "action": self.action, "reason": self.reason,
                "dirty": self.dirty}


@dataclass
class WorktreePlan:
    """What `worktree clean` WOULD do. Read-only to build; nothing is removed by building it."""

    ok: bool = True
    reason: str = ""
    repo_root: str = ""
    items: List[WorktreePlanItem] = field(default_factory=list)

    @property
    def actionable(self) -> List[WorktreePlanItem]:
        return [i for i in self.items if i.action in (ACTION_REMOVE, ACTION_PRUNE)]

    @property
    def kept(self) -> List[WorktreePlanItem]:
        return [i for i in self.items if i.action == ACTION_KEEP]

    def render(self) -> str:
        if not self.ok:
            return f"mokata worktree clean: {self.reason}"
        if not self.items:
            return ("mokata worktree clean: nothing to clean — this repo has only its main "
                    "checkout.")
        lines = [f"mokata worktree clean — {len(self.actionable)} to clean, "
                 f"{len(self.kept)} kept (of {len(self.items)} worktree(s)):"]
        for i in self.items:
            lines.append(f"  {i.action:<6}  {i.label}  ({i.branch or 'detached'})  {i.reason}")
        if not self.actionable:
            lines.append("nothing to clean — every worktree is in use, unmerged or dirty "
                         "(see the reasons above).")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "repo_root": self.repo_root,
                "total_count": len(self.items), "actionable_count": len(self.actionable),
                "kept_count": len(self.kept),
                "items": [i.to_dict() for i in self.items], "message": self.render()}


@dataclass
class WorktreeOutcome:
    """What a clean/remove actually did, per item. `ok` = nothing that was attempted failed."""

    ok: bool = True
    reason: str = ""
    removed: List[str] = field(default_factory=list)
    pruned: List[str] = field(default_factory=list)
    refused: List[Dict[str, str]] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)

    def render(self, verb: str = "clean") -> str:
        if self.reason and not (self.removed or self.pruned or self.refused or self.failed):
            return f"mokata worktree {verb}: {self.reason}"
        lines = [f"mokata worktree {verb}: {len(self.removed)} removed, {len(self.pruned)} "
                 f"pruned, {len(self.refused)} refused, {len(self.failed)} failed."]
        lines += [f"  removed  {p}" for p in self.removed]
        lines += [f"  pruned   {p}" for p in self.pruned]
        lines += [f"  refused  {r['path']} — {r['reason']}" for r in self.refused]
        lines += [f"  FAILED   {r['path']} — {r['reason']}" for r in self.failed]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "removed": list(self.removed),
                "pruned": list(self.pruned), "refused": list(self.refused),
                "failed": list(self.failed)}


# ----------------------------------------------------------------------------------- helpers
def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.abspath(path))
    except OSError:
        return os.path.abspath(path)


def _ledger(surface: Any) -> Any:
    try:
        from .govern.ledger import AuditLedger
        return AuditLedger.from_mokata_dir(surface.mokata_dir)
    except (AttributeError, OSError, ValueError):
        return None


def _manager(report: WorktreeReport, surface: Any, git: Optional[Callable[..., Any]]):
    from .worktree import WorktreeManager
    return WorktreeManager(report.repo_root, ledger=_ledger(surface), git=git)


def _is_dirty(mgr: Any, row: WorktreeRow) -> bool:
    from .worktree import Worktree
    return mgr.is_changed(Worktree(label=row.label, path=row.path))     # fail-closed: unknown=dirty


def _keep_reason(row: WorktreeRow) -> Optional[str]:
    """The reason a row can NEVER be removed by clean or by a forced remove, or None."""
    if row.is_main:
        return "the main checkout — never removed"
    if row.verdict == VERDICT_ACTIVE:
        return (f"session {row.short_id or row.session_id} is live here — close that window "
                f"first (no flag overrides a live session)")
    if row.locked:
        return "locked by git (`git worktree unlock` first)"
    return None


# ------------------------------------------------------------------------- FR-WT-2 · clean
def plan_worktree_clean(surface: Any, git: Optional[Callable[..., Any]] = None,
                        report: Optional[WorktreeReport] = None) -> WorktreePlan:
    """Propose removals. Only `merged` + clean + not-live worktrees are marked `remove`; a worktree
    whose directory is already gone is `prune`; everything else is KEPT with its reason."""
    report = report or build_worktree_report(surface, git=git)
    if not report.ok:
        return WorktreePlan(ok=False, reason=report.reason, repo_root=report.repo_root)
    mgr = _manager(report, surface, git)
    plan = WorktreePlan(repo_root=report.repo_root)
    for row in report.linked:
        item = WorktreePlanItem(path=row.path, label=row.label, branch=row.branch,
                                verdict=row.verdict, action=ACTION_KEEP, reason="")
        hard = _keep_reason(row)
        if hard:
            item.reason = hard
        elif row.prunable or not os.path.isdir(row.path):
            item.action, item.reason = ACTION_PRUNE, "directory is gone — git metadata only"
        elif row.verdict == VERDICT_UNKNOWN:
            item.reason = "session evidence unreadable — not judged (`mokata worktree list`)"
        else:
            item.dirty = _is_dirty(mgr, row)
            if item.dirty:
                item.reason = (f"uncommitted changes — review, then "
                               f"`mokata worktree remove {row.path} --force`")
            elif row.verdict == VERDICT_MERGED:
                item.action, item.reason = ACTION_REMOVE, row.reason
            else:
                item.reason = (f"{row.reason}; branch not merged — "
                               f"`mokata worktree remove {row.path} --force` to discard it")
        plan.items.append(item)
    return plan


def apply_worktree_clean(surface: Any, plan: WorktreePlan,
                         git: Optional[Callable[..., Any]] = None) -> WorktreeOutcome:
    """Carry out ONLY the plan's actionable items, each RE-CHECKED at apply time (a worktree that
    became dirty or live since the preview is refused, not removed)."""
    if not plan.ok:
        return WorktreeOutcome(ok=False, reason=plan.reason)
    out = WorktreeOutcome()
    if not plan.actionable:
        out.reason = "nothing to clean."
        return out
    prunes = [i for i in plan.actionable if i.action == ACTION_PRUNE]
    for item in plan.actionable:
        if item.action != ACTION_REMOVE:
            continue
        one = remove_worktree(surface, item.path, force=False, git=git)
        out.removed += one.removed
        out.refused += one.refused
        out.failed += one.failed
    if prunes:
        report = build_worktree_report(surface, git=git)
        mgr = _manager(report, surface, git)
        r = mgr._git(["worktree", "prune"], cwd=report.repo_root or plan.repo_root)
        if getattr(r, "ok", False):
            out.pruned = [i.path for i in prunes]
            mgr._log("worktree_prune", ok=True, paths=out.pruned)
        else:
            reason = (getattr(r, "stderr", "") or "git worktree prune failed").strip()[:160]
            out.failed += [{"path": i.path, "reason": reason} for i in prunes]
            mgr._log("worktree_prune", ok=False, reason=reason)
    out.ok = not out.failed
    return out


# ------------------------------------------------------------------------- FR-WT-3 · remove
def find_worktree(report: WorktreeReport, target: str) -> Optional[WorktreeRow]:
    """Resolve `target` (a path, the `list` label, or the branch) to ONE row, or None."""
    want = _real(target)
    for row in report.worktrees:
        if _real(row.path) == want:
            return row
    for row in report.worktrees:
        if target and (row.label == target or row.branch == target):
            return row
    return None


def check_remove(surface: Any, target: str, *, force: bool = False,
                 git: Optional[Callable[..., Any]] = None,
                 report: Optional[WorktreeReport] = None) -> "tuple":
    """(row, refusal) — the remove PRE-CHECK, shared by the CLI preview, the MCP proposal and the
    removal itself. `refusal` is "" when the removal may proceed."""
    report = report or build_worktree_report(surface, git=git)
    if not report.ok:
        return None, report.reason
    row = find_worktree(report, target)
    if row is None:
        return None, (f"no worktree of this repo matches '{target}' — "
                      f"`mokata worktree list` shows them")
    hard = _keep_reason(row)
    if hard:
        return row, hard
    if row.verdict == VERDICT_UNKNOWN and not force:
        return row, "session evidence unreadable — pass --force only if you know none is live"
    if os.path.isdir(row.path) and not force:
        if _is_dirty(_manager(report, surface, git), row):
            return row, "uncommitted changes — review them, then pass --force to discard"
        if row.verdict != VERDICT_MERGED:
            return row, (f"branch '{row.branch or '(detached)'}' is not merged into the default "
                         f"branch — pass --force to discard it")
    return row, ""


def remove_worktree(surface: Any, target: str, *, force: bool = False,
                    git: Optional[Callable[..., Any]] = None) -> WorktreeOutcome:
    """Remove ONE worktree through `WorktreeManager.remove` (the one remove path). The CALLER owns
    the human gate (CLI confirm / MCP approval); this re-checks every refusal at the moment of
    removal. The branch itself is never deleted."""
    report = build_worktree_report(surface, git=git)
    row, refusal = check_remove(surface, target, force=force, git=git, report=report)
    if row is None:
        return WorktreeOutcome(ok=False, reason=refusal)
    if refusal:
        return WorktreeOutcome(ok=False, refused=[{"path": row.path, "reason": refusal}])
    mgr = _manager(report, surface, git)
    if row.prunable or not os.path.isdir(row.path):
        # The directory is already gone: there is nothing to remove, only git's admin entry.
        r = mgr._git(["worktree", "prune"], cwd=report.repo_root)
        mgr._log("worktree_prune", ok=bool(getattr(r, "ok", False)), paths=[row.path])
        if getattr(r, "ok", False):
            return WorktreeOutcome(pruned=[row.path])
        return WorktreeOutcome(ok=False, failed=[{"path": row.path,
                                                  "reason": "git worktree prune failed"}])
    from .worktree import Worktree
    res = mgr.remove(Worktree(label=row.label, path=row.path), force=force)
    if res.removed:
        return WorktreeOutcome(removed=[row.path])
    reason = ("uncommitted changes appeared — kept" if res.changed
              else "git worktree remove failed — the worktree is still there")
    return WorktreeOutcome(ok=False, failed=[{"path": row.path, "reason": reason}])


# ----------------------------------------------------------------- FR-WT-4 · new-worktree notice
def _gitdir_worktrees(root: str) -> Optional[List[str]]:
    """Linked worktree paths from git's own admin files — `<common>/worktrees/<name>/gitdir` holds
    `<worktree>/.git`. Pure file reads (no subprocess). None when this is not a git repo."""
    from .repo_identity import _common_dir
    common = _common_dir(root)
    if common is None:
        return None
    admin = os.path.join(common, "worktrees")
    out: List[str] = []
    try:
        names = sorted(os.listdir(admin))
    except OSError:
        return []
    for name in names:
        try:
            with open(os.path.join(admin, name, "gitdir"), encoding="utf-8") as fh:
                gitfile = fh.read().strip()
        except OSError:
            continue
        if gitfile:
            out.append(os.path.normpath(os.path.dirname(gitfile)))
    return out


def _notice_state_path(surface: Any) -> str:
    from . import TEMP_LOCAL_DIRNAME
    return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, NOTICE_STATE_NAME)


def _write_seen(path: str, seen: List[str]) -> None:
    """The last-seen worktree set (transient run-state under temp_local/; ungated)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(seen), fh)
    os.replace(tmp, path)


def new_worktree_notice(surface: Any, *, ascii_only: bool = False) -> Optional[str]:
    """The ONE SessionStart line naming worktrees created since the last session, or None. The
    first sighting records a baseline silently (an existing repo is not told about old worktrees).
    Writes only when the set changed. Never raises."""
    try:
        current = _gitdir_worktrees(getattr(surface, "root", "."))
        if current is None:
            return None
        state = _notice_state_path(surface)
        try:
            with open(state, encoding="utf-8") as fh:
                seen = json.load(fh)
            seen = [s for s in seen if isinstance(s, str)] if isinstance(seen, list) else None
        except (OSError, ValueError):
            seen = None
        if seen is not None and sorted(seen) == sorted(current):
            return None
        _write_seen(state, current)
        if seen is None:
            return None                                          # baseline, said nothing
        new = [p for p in current if p not in set(seen)]
        if not new:
            return None
        from .repo_identity import worktree_label
        names = ", ".join(worktree_label(p) for p in new[:3])
        more = f" (+{len(new) - 3} more)" if len(new) > 3 else ""
        glyph = "[i]" if ascii_only else "↳"
        return (f"{glyph} new git worktree(s) since the last session: {names}{more} — "
                f"`mokata worktree list` shows them; `mokata worktree clean` tidies merged ones "
                f"(human-gated).")
    except Exception:  # noqa: BLE001 — a notice is data; its absence costs nothing
        return None


# ------------------------------------------------------------------------- FR-WT-6 · doctor
def worktree_findings(surface: Any, git: Optional[Callable[..., Any]] = None) -> List[Any]:
    """Doctor's worktree check: WARNING-level findings naming worktree-state problems and the
    command that fixes each. Silent for a non-git repo, a repo with no worktrees, or a healthy set.
    Never raises (doctor must render)."""
    from .govern.doctor import DoctorFinding
    try:
        report = build_worktree_report(surface, git=git)
    except Exception:  # noqa: BLE001 — a lister fault must not take doctor down
        return []
    if not report.ok:
        return []
    out: List[Any] = []
    prunable = [w for w in report.linked if w.prunable]
    merged = [w for w in report.linked if w.verdict == VERDICT_MERGED and not w.prunable]
    idle = [w for w in report.linked if w.verdict == "idle" and not w.prunable]
    if prunable:
        out.append(DoctorFinding(
            "warning", "worktree-prunable",
            f"{len(prunable)} worktree(s) whose directory is gone "
            f"({', '.join(w.label for w in prunable[:3])}) — run `mokata worktree clean`"))
    if merged:
        out.append(DoctorFinding(
            "warning", "worktree-merged",
            f"{len(merged)} worktree(s) on a branch already merged "
            f"({', '.join(w.label for w in merged[:3])}) — `mokata worktree clean` previews "
            f"their removal"))
    if idle:
        out.append(DoctorFinding(
            "info", "worktree-idle",
            f"{len(idle)} worktree(s) whose session has exited "
            f"({', '.join(w.label for w in idle[:3])}) — `mokata worktree list`"))
    if not report.registry_ok:
        out.append(DoctorFinding(
            "warning", "worktree-registry",
            "the session registry could not be read — worktrees cannot be joined to sessions "
            "(`mokata windows` to re-register)"))
    return out
