"""WT-LIST (FR-WT-1) — the read-only worktree × session JOIN, with a staleness verdict.

WT.S1 taught mokata to OFFER a worktree; WT.S4 taught it to BIND a run to one. Neither taught it
to LOOK: `git worktree list` was called nowhere, so a worktree whose window has long since exited
— or whose branch is already merged — was invisible. You could create worktrees and never see
them. This module is the eye, and it is ONLY an eye:

  * ENUMERATE — every git worktree of this repo, via `worktree.list_worktrees` (the same
    `_default_git` runner `worktree add`/`status`/`remove` already use; no second way to spawn git).
  * JOIN      — each worktree to its owning session, via `session_worktree.run_for_branch` over ONE
    `session_registry.list_sessions` read. Not a second store and not a second identity scheme: the
    registry is anchored at `canonical_repo_root` (session_registry._registry_store), which is the
    SAME `repo_identity` key WT.S1/WT.S4 bind on, so a window in a worktree and a window in the
    main checkout already share one registry file. The join inherits that for free.
  * VERDICT   — a small CLOSED set, each pinned to grounded evidence and never to a guess:
      main        the main checkout. LABELLED, never given a stale verdict.
      active      a LIVE session (alive pid) is bound to this worktree's branch.
      merged      the branch is already merged into the repo's default branch (a git query).
      idle        a session row is bound but its pid is dead — a window that exited.
      no-session  the worktree is on disk and no registry row binds to it.
      unknown     the EVIDENCE could not be gathered (an unreadable registry). Not a fifth kind of
                  staleness — the honest floor, so a read failure is never reported as "no-session"
                  (P16: mokata does not fabricate a verdict it could not reach).
    PRECEDENCE (linked worktrees only) is `active > merged > idle > no-session > unknown`, and each
    step earns its place: `active` FIRST so live work is never labelled stale, whatever git says
    about the branch; `merged` next because "the work is already in the default branch" is a fact
    about the WORK and dominates the two facts about WINDOWS beneath it — a dead window on merged
    work and a dead window on unmerged work are not the same situation; `idle` before `no-session`
    because a bound-then-exited session is strictly more specific than no binding at all.

READ-ONLY, END TO END. No `git worktree add`, no `remove`, and deliberately no `prune` — pruning is
FR-WT-2/3 (0.0.17) and a read must never be the thing that mutates what it read. Nothing is
registered either: unlike `session_windows`, this surface does NOT `touch()` to self-register,
because a lister has no business adding itself to the list it is showing. (The one nuance, stated
rather than papered over: `list_sessions(prune=False)` — the read WT.S4's binding already uses —
rides the registry's shared locked read-modify-write, so it rewrites the key with byte-identical
content and creates the file if it was absent. It invents no row and reaps none, which is the
property that matters; bypassing it would mean a SECOND registry read path, which is exactly what
this stage must not build.) DEGRADE-CLEAN throughout: not a git repo, git absent, or
an unreadable registry each yield an honest one-line reason and a clean exit, never a traceback
into a read-only surface (the WT.S4 discipline).

Stdlib + the git CLI only; no new deps, no daemon. Clean-room. Copyright 2026 MoStack. Licensed
under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .repo_identity import _common_dir, canonical_repo_root, worktree_label

# --------------------------------------------------------------------------------- THE VERDICTS
VERDICT_MAIN = "main"
VERDICT_ACTIVE = "active"
VERDICT_MERGED = "merged"
VERDICT_IDLE = "idle"
VERDICT_NO_SESSION = "no-session"
VERDICT_UNKNOWN = "unknown"

# The CLOSED set, in PRECEDENCE order for a linked worktree (`main` is a label, not a verdict, and
# is never contended). Exported on the report so a client never has to guess the vocabulary.
VERDICT_PRECEDENCE = (VERDICT_ACTIVE, VERDICT_MERGED, VERDICT_IDLE, VERDICT_NO_SESSION,
                      VERDICT_UNKNOWN)
VERDICTS = (VERDICT_MAIN,) + VERDICT_PRECEDENCE

NOT_A_GIT_REPO = ("not a git repository — `mokata worktree list` reads git's own worktree list, "
                  "and there is none here. Nothing was changed.")
GIT_UNAVAILABLE = ("git could not list this repo's worktrees (is git installed, and is this repo "
                   "healthy?). Nothing was changed.")
NO_WORKTREES = ("no worktrees — this repo has only its main checkout. Create one with "
                "`mokata worktree create \"<what you're working on>\"` (human-gated).")
MERGED_SKIPPED = ("merged-check skipped: this repo's default branch could not be determined "
                  "(no `origin/HEAD`, and no local `main` or `master`), so no worktree is "
                  "claimed to be merged")


@dataclass
class WorktreeRow:
    """One worktree, joined to its session (naming per doc 85 §3: a `*Report`'s row)."""

    path: str
    label: str                       # `worktree_label`: "main" | the path relative to the checkout
    branch: str = ""                 # "" when detached
    head: str = ""
    is_main: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False
    verdict: str = VERDICT_NO_SESSION
    reason: str = ""                 # the EVIDENCE behind the verdict, in one line
    session_id: str = ""
    short_id: str = ""
    session_alive: Optional[bool] = None    # None = no session row bound (or none readable)
    scope: str = ""
    phase: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "label": self.label, "branch": self.branch, "head": self.head,
            "is_main": self.is_main, "detached": self.detached, "locked": self.locked,
            "prunable": self.prunable, "verdict": self.verdict, "reason": self.reason,
            "session_id": self.session_id, "short_id": self.short_id,
            "session_alive": self.session_alive, "scope": self.scope, "phase": self.phase,
        }


@dataclass
class WorktreeReport:
    """The read-only worktree×session view (doc 85 §3: `*Report` = read-only diagnostic).

    ONE source of truth, TWO surfaces: `mokata worktree list` renders it and the `worktree_list`
    MCP tool serialises it, so the CLI and the harness can never disagree about what is stale."""

    ok: bool = True
    reason: str = ""                          # why NOT ok; "" when ok
    repo_root: str = ""                       # the canonical (main-checkout) root
    default_branch: Optional[str] = None      # None ⇒ no `merged` verdict is ever asserted
    merged_check: str = ""                    # what the merged-check compared against, or skipped
    registry_ok: bool = True                  # False ⇒ session evidence was unreachable
    worktrees: List[WorktreeRow] = field(default_factory=list)

    @property
    def linked(self) -> List[WorktreeRow]:
        """The worktrees that are NOT the main checkout — what "how many worktrees" means."""
        return [w for w in self.worktrees if not w.is_main]

    @property
    def empty(self) -> bool:
        """True when this repo has ONLY its main checkout. A definitive answer, not an empty
        table the reader has to interpret."""
        return self.ok and not self.linked

    def render(self, ascii_only: bool = False) -> str:
        """The exact text both surfaces show. A degraded read and an empty repo each get a
        DEFINITIVE sentence — never a blank listing."""
        if not self.ok:
            return f"mokata worktree list: {self.reason}"
        if self.empty:
            return f"mokata worktree list: {NO_WORKTREES}\n  main checkout: {self.repo_root}"
        head = (f"mokata worktree list — {len(self.linked)} worktree(s) + the main checkout "
                f"of {self.repo_root}:")
        lines = [head]
        wid = max(len(w.label) for w in self.worktrees)
        bid = max((len(w.branch or "(detached)") for w in self.worktrees), default=1)
        for w in self.worktrees:
            branch = w.branch or "(detached)"
            flags = "".join(f" [{f}]" for f, on in
                            (("locked", w.locked), ("prunable", w.prunable)) if on)
            lines.append(f"  {w.label:<{wid}}  {branch:<{bid}}  {w.verdict:<10}  {w.reason}{flags}")
            lines.append(f"  {'':<{wid}}  {w.path}")
        if self.merged_check:
            glyph = "[i]" if ascii_only else "↳"
            lines.append(f"{glyph} {self.merged_check}")
        if not self.registry_ok:
            glyph = "[i]" if ascii_only else "↳"
            lines.append(f"{glyph} the session registry could not be read — no worktree could be "
                         f"joined to a session, so none is claimed to have one.")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "reason": self.reason, "repo_root": self.repo_root,
            "default_branch": self.default_branch, "merged_check": self.merged_check,
            "registry_ok": self.registry_ok,
            "empty": self.empty, "count": len(self.worktrees), "linked_count": len(self.linked),
            "verdicts": list(VERDICTS),
            "worktrees": [w.to_dict() for w in self.worktrees],
            "message": self.render(),
        }


def build_worktree_report(surface: Any,
                          git: Optional[Callable[..., Any]] = None) -> WorktreeReport:
    """THE join: every git worktree of this repo, each with its owning session and a staleness
    verdict. Read-only and never-raising — every failure becomes an honest `ok=False` reason or a
    lower-confidence verdict that says why. `git` injects the runner (tests)."""
    from .worktree import default_branch, list_worktrees, merged_branches

    root = getattr(surface, "root", ".") or "."
    canon = canonical_repo_root(root)

    # 1) is there a git repo at all? Answered by the SAME zero-subprocess probe WT.S1 uses, so
    #    "not a git repo" reads identically here and in `create_worktree`.
    try:
        is_git = _common_dir(root) is not None
    except (OSError, ValueError):
        is_git = False
    if not is_git:
        return WorktreeReport(ok=False, reason=NOT_A_GIT_REPO, repo_root=canon)

    # 2) enumerate. None (never []) is git's "I could not answer" — the ambiguity FR-WT-1 removes.
    wts = list_worktrees(canon, git=git)
    if wts is None:
        return WorktreeReport(ok=False, reason=GIT_UNAVAILABLE, repo_root=canon)

    # 3) the session evidence — ONE registry read for every worktree, and `prune=False` because a
    #    READ must not reap the rows it read (the WT.S4 lesson: a binding that answers once and
    #    then silently stops is the worst possible shape). `list_sessions` contracts never to
    #    raise; the guard is belt-and-braces, and it DOWNGRADES the verdict rather than lying.
    rows: List[Any] = []
    registry_ok = True
    try:
        from . import session_registry as SR
        rows = SR.list_sessions(surface, prune=False)
    except Exception:
        # (iv) SUPPRESS-OK — the fallback is NOT silent and NOT a fabrication: `registry_ok=False`
        # is reported on the report, printed by `render`, and forces every unresolvable worktree to
        # `unknown` instead of the false "no-session". Broad because the registry spans PID probing
        # + transient-file IO across three OSes, and a read-only lister must never traceback.
        registry_ok = False
    by_session = {getattr(r, "session_id", None): r for r in rows}

    # 4) the default branch + the merged set. Both may legitimately be unavailable; when they are,
    #    NO worktree is claimed to be merged and the report says so once (P16).
    base = default_branch(canon, git=git)
    merged: Optional[set] = merged_branches(canon, base, git=git) if base else None
    merged_check = (f"merged-check: compared against the default branch '{base}'" if merged is not None
                    else MERGED_SKIPPED)

    # 5) join + verdict. `is_main` is re-grounded on the canonical root (the key the rest of WT
    #    uses) and falls back to git's own ordering when no path matches.
    main_real = _real(canon)
    matched_main = any(_real(w.path) == main_real for w in wts)
    out: List[WorktreeRow] = []
    for w in wts:
        is_main = (_real(w.path) == main_real) if matched_main else w.is_main
        out.append(_row(surface, w, is_main=is_main, rows=rows, by_session=by_session,
                        registry_ok=registry_ok, base=base, merged=merged))
    return WorktreeReport(ok=True, repo_root=canon, default_branch=base,
                          merged_check=merged_check, registry_ok=registry_ok, worktrees=out)


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.abspath(path))
    except OSError:
        return os.path.abspath(path)


def _label(path: str) -> str:
    try:
        return worktree_label(path)
    except (OSError, ImportError, ValueError):
        return os.path.basename(path.rstrip(os.sep)) or path


def _row(surface: Any, w: Any, *, is_main: bool, rows: List[Any], by_session: Dict[Any, Any],
         registry_ok: bool, base: Optional[str], merged: Optional[set]) -> WorktreeRow:
    """One worktree's joined row. The verdict PRECEDENCE lives here and only here."""
    row = WorktreeRow(path=w.path, label=_label(w.path), branch=w.branch, head=w.head,
                      is_main=is_main, detached=bool(w.detached), locked=bool(w.locked),
                      prunable=bool(w.prunable))

    # The main checkout is LABELLED, never judged: it is not a worktree anyone could clean up, and
    # calling the repo you are standing in "stale" would be nonsense.
    if is_main:
        row.verdict = VERDICT_MAIN
        row.reason = "the main checkout — never given a stale verdict"
        return row

    # --- the session evidence, resolved through the ONE branch→run resolver (no second rule) ---
    run_id = None
    if registry_ok and w.branch:
        try:
            from .session_worktree import run_for_branch
            run_id = run_for_branch(surface, w.branch, rows=rows)
        except Exception:
            # (iv) SUPPRESS-OK — the resolver is degrade-clean by contract (returns None on any
            # registry/identity fault); this guard exists so an UNEXPECTED fault downgrades the
            # verdict honestly rather than tracebacking into a read-only lister. It cannot promote
            # anything: the only outcome is "no run resolved", handled below.
            run_id = None
    entry = by_session.get(run_id) if run_id else None
    if entry is not None:
        row.session_id = getattr(entry, "session_id", "") or ""
        row.short_id = _short(entry)
        row.session_alive = bool(getattr(entry, "alive", False))
        row.scope = getattr(entry, "scope", None) or ""
        row.phase = getattr(entry, "phase", None) or ""

    is_merged = bool(merged is not None and w.branch and w.branch != base
                     and w.branch in merged)

    # --- PRECEDENCE: active > merged > idle > no-session > unknown -----------------------------
    if row.session_alive is True:
        row.verdict = VERDICT_ACTIVE
        row.reason = (f"session {row.short_id} is live here (pid alive) on branch "
                      f"'{w.branch}'{_scope_tail(row)}")
    elif is_merged:
        row.verdict = VERDICT_MERGED
        row.reason = f"branch '{w.branch}' is already merged into '{base}'"
    elif row.session_alive is False:
        row.verdict = VERDICT_IDLE
        row.reason = (f"session {row.short_id} is bound to branch '{w.branch}' but its window has "
                      f"exited (dead pid){_scope_tail(row)}")
    elif not registry_ok:
        row.verdict = VERDICT_UNKNOWN
        row.reason = ("the session registry could not be read, so no binding could be checked "
                      "(no session is claimed either way)")
    elif not w.branch:
        row.verdict = VERDICT_NO_SESSION
        row.reason = "detached HEAD — there is no branch to bind a session to or to compare"
    else:
        row.verdict = VERDICT_NO_SESSION
        row.reason = f"no mokata session is bound to branch '{w.branch}'"
    return row


def _scope_tail(row: WorktreeRow) -> str:
    return f" · scope: {row.scope}" if row.scope else ""


def _short(entry: Any) -> str:
    try:
        return getattr(entry, "short_id", "") or ""
    except (ImportError, ValueError):
        # `short_id` is a property that imports `session.short_id` lazily; a half-installed package
        # must not break a listing. The full id is still on the row.
        return getattr(entry, "session_id", "") or ""
