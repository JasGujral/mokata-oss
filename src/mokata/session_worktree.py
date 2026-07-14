"""WT.S1 — same-repo collision DETECT + human-gated worktree OFFER + gated CREATE.

MS.S2 stopped two Claude Code windows from clobbering each other's pipeline STATE, but the two
users still collide on the WORKING TREE itself. A git worktree isolates a window's working tree —
but bare Claude Code neither notices the collision nor offers the escape hatch. This module does:

  * DETECT — `live_siblings` finds the OTHER live windows on the SAME repo identity (WT.S1), across
    worktrees, reading the shared session registry (`session_registry`).
  * OFFER  — `emit_offer_once` / `offer_text_once` surface a ONE-TIME (per session) notice naming a
    sibling when one is live, mirroring CM.S2's once-per-subsystem pattern. The offer NEVER creates
    anything — it is text, not an action (P14: an offer, not an act).
  * CREATE — `create_worktree` is the ONLY durable action here, and it is explicitly HUMAN-GATED
    (P2): it asks/accepts the scope ("what is this session working on?"), recommends a branch/dir
    name derived from the topic + the sibling sessions' phases/scopes (a simple registry-data
    heuristic — NO graph work), confirms fail-closed via the standard `read_yes_no` path, then runs
    `git worktree add`. It records the scope on this session's registry entry and refuses politely
    when `root` is not a git repo.

Worktree creation is the only place a subprocess (`git`) is spawned — reused from the Stage-51
`worktree` manager's runner. Detection/identity are pure file reads (`repo_identity`). Stdlib + the
git CLI only; no new deps, no daemon. Clean-room. Copyright 2026 MoStack. Apache License 2.0.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Set

from .repo_identity import canonical_repo_root, repo_identity


@dataclass
class WorktreeCreateResult:
    """The produced value of a `worktree create` attempt (naming per doc 85 §3)."""

    created: bool
    path: str = ""
    branch: str = ""
    recommendation: str = ""
    scope: str = ""
    reason: str = ""


# --------------------------------------------------------------------------- DETECT (WT.S1)
def live_siblings(surface: Any, *, rows: Optional[List[Any]] = None) -> List[Any]:
    """The OTHER live windows on the SAME repo identity as `surface` (this session excluded). Reads
    the shared registry — which is anchored at the canonical repo root, so a window in a worktree
    and a window in the main checkout SEE each other. Degrade-clean: any registry problem → []."""
    from . import session_registry as SR
    from .session import current_session_id
    try:
        me = current_session_id()
        my_repo = repo_identity(getattr(surface, "root", "."))
        entries = rows if rows is not None else SR.list_sessions(surface)
    except (OSError, ImportError, ValueError):
        # D5 — the real raisers: an unreadable `.git`/registry path (OSError), a half-installed
        # package (ImportError), a torn JSON registry (ValueError). [] means "no sibling", which is
        # the same thing a healthy single-window repo returns — and it must be, because the OFFER it
        # feeds is an offer, not a guarantee.
        return []
    out: List[Any] = []
    for r in entries:
        if not getattr(r, "alive", False):
            continue
        if getattr(r, "session_id", None) == me:
            continue
        try:
            if repo_identity(getattr(r, "repo_root", "")) != my_repo:
                continue
        except (OSError, ValueError):
            # D5 — a sibling row whose recorded repo_root is gone/unreadable (OSError) or malformed
            # (ValueError) can't be matched to this repo, so it isn't a sibling. Skip it.
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- OFFER (WT.S1)
# Process-lifetime set of repo identities whose worktree offer has already been surfaced, so the
# offer fires ONCE per session (not once per `windows`/bootstrap call) — the CM.S2 notice pattern.
_OFFERED: Set[str] = set()


def build_offer(siblings: List[Any], *, ascii_only: bool = False) -> Optional[str]:
    """The one offer string, or None when there is no sibling. Names the first sibling (short id +
    phase) and points at the human-gated create command. Carries no DSN value or memory content."""
    if not siblings:
        return None
    s = siblings[0]
    short = getattr(s, "short_id", "") or ""
    phase = getattr(s, "phase", None) or "—"
    more = f" (+{len(siblings) - 1} more)" if len(siblings) > 1 else ""
    glyph = "[i]" if ascii_only else "↳"
    return (f"{glyph} another mokata session is active on this repo — {short} (phase: {phase})"
            f"{more}. A git worktree isolates your working tree so the two windows don't collide. "
            f"Create one (human-gated, never automatic): "
            f"mokata worktree create \"<what you're working on>\".")


def offer_text_once(surface: Any, *, rows: Optional[List[Any]] = None,
                    seen: Optional[Set[str]] = None, ascii_only: bool = False) -> Optional[str]:
    """The offer string the FIRST time a live sibling is seen for this repo in this process, else
    None (no sibling, or already offered). `seen` overrides the process set (tests). Degrade-clean:
    never raises into the read-only surfaces that call it."""
    store = _OFFERED if seen is None else seen
    try:
        sibs = live_siblings(surface, rows=rows)
    except (OSError, ImportError):
        # D5 — `live_siblings` is already degrade-clean ([] on any registry problem); what can still
        # reach here is an unreadable path (OSError) or a half-installed package (ImportError). No
        # offer is the safe, silent-by-design outcome: an offer's absence costs the user nothing.
        return None
    if not sibs:
        return None
    key = repo_identity(getattr(surface, "root", "."))
    if key in store:
        return None
    store.add(key)
    return build_offer(sibs, ascii_only=ascii_only)


def emit_offer_once(surface: Any, out: Callable[[str], None] = print, *,
                    rows: Optional[List[Any]] = None, seen: Optional[Set[str]] = None,
                    ascii_only: bool = False) -> bool:
    """Print the worktree offer once per session (True when it printed, False when suppressed as a
    repeat or when there is no live sibling). The offer NEVER creates a worktree."""
    msg = offer_text_once(surface, rows=rows, seen=seen, ascii_only=ascii_only)
    if msg is None:
        return False
    out(msg)
    return True


def reset_offers(seen: Optional[Set[str]] = None) -> None:
    """Clear the once-per-session memory (test hook; a fresh process starts empty anyway)."""
    (seen if seen is not None else _OFFERED).clear()


# --------------------------------------------------------------------------- CREATE (WT.S1, gated)
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")


def recommend_name(topic: str, siblings: List[Any] = ()) -> str:
    """A branch/dir name derived from the stated topic, disambiguated against the sibling sessions'
    recorded scopes/phases (a simple registry-data heuristic — NO graph work; that is 0.0.14). The
    returned name always contains the topic slug so the recommendation names what you're doing."""
    base = _slug(topic) or "session"
    taken = set()
    for s in siblings:
        for field in ("scope", "phase"):
            v = getattr(s, field, None)
            if v:
                taken.add(_slug(v))
    name = base
    i = 2
    while name in taken:
        name = f"{base}-{i}"
        i += 1
    return name


def create_worktree(surface: Any, topic: Optional[str] = None, *, assume_yes: bool = False,
                    confirm: Optional[Callable[[str], bool]] = None,
                    out: Optional[Callable[[str], None]] = None,
                    git: Optional[Callable[..., Any]] = None) -> WorktreeCreateResult:
    """Create a git worktree to isolate THIS session's working tree — the only durable action in
    WT, and it is explicitly HUMAN-GATED (P2/P14). Asks/accepts the scope, recommends a topic-aware
    branch/dir name (from registry data), confirms fail-closed via the standard `read_yes_no` path
    (`assume_yes`/`confirm` are the sanctioned non-interactive approve seams), then runs
    `git worktree add`. Records the scope on this session's registry entry. Refuses politely when
    `root` is not a git repo. Degrade-clean: a git failure returns `created=False`, never raises."""
    emit = out or (lambda *_a: None)
    root = getattr(surface, "root", ".")

    # 1) must be a git repo (else there is no worktree to add).
    from .repo_identity import _common_dir
    canon = canonical_repo_root(root)
    if _common_dir(root) is None:
        msg = ("not a git repository — `mokata worktree create` needs git to isolate your working "
               "tree (nothing created).")
        emit(msg)
        return WorktreeCreateResult(False, reason=msg)

    # 2) resolve the scope (what this session is working on).
    topic = (topic or "").strip()
    if not topic:
        from .prompt import _stdin_is_tty
        if not _stdin_is_tty():
            msg = ("no scope given and stdin is not interactive — pass the topic "
                   "(`mokata worktree create \"<what you're working on>\"`); nothing created.")
            emit(msg)
            return WorktreeCreateResult(False, reason=msg)
        try:
            topic = input("what is this session working on? ").strip()
        except (EOFError, OSError):
            topic = ""
        if not topic:
            msg = "no scope given — nothing created."
            emit(msg)
            return WorktreeCreateResult(False, reason=msg)

    # 3) recommend a branch/dir name (topic + sibling phases/scopes).
    siblings = live_siblings(surface)
    name = recommend_name(topic, siblings)
    parent = os.path.dirname(canon)
    wt_path = os.path.join(parent, f"{os.path.basename(canon)}-{name}")
    recommendation = f"branch '{name}' → worktree at {wt_path}"

    # 4) confirm — fail-closed (non-TTY/EOF → No), unless explicitly approved.
    prompt = (f"mokata · create a git worktree to isolate this session's working tree?\n"
              f"  scope: {topic}\n  recommended: {recommendation}\n"
              f"  (runs `git worktree add` — a durable change; nothing else is touched)")
    if assume_yes:
        approved = True
    elif confirm is not None:
        approved = bool(confirm(prompt))
    else:
        from .prompt import read_yes_no
        approved = read_yes_no(prompt, "create the worktree?")
    if not approved:
        msg = f"worktree creation declined — nothing created (recommended {recommendation})."
        emit(msg)
        return WorktreeCreateResult(False, recommendation=recommendation, reason=msg)

    # 5) the durable action: `git worktree add -b <name> <path>` from the main checkout.
    from .worktree import _default_git
    runner = git or _default_git
    try:
        r = runner(["worktree", "add", "-b", name, wt_path], cwd=canon)
    except Exception as exc:  # pragma: no cover - runner contract is never-raise, belt-and-braces
        msg = f"git worktree add failed ({exc}) — nothing created."
        emit(msg)
        return WorktreeCreateResult(False, recommendation=recommendation, reason=msg)
    if not getattr(r, "ok", False):
        detail = (getattr(r, "stderr", "") or "git worktree add failed").strip()[:200]
        msg = f"git worktree add failed: {detail} — nothing created."
        emit(msg)
        return WorktreeCreateResult(False, recommendation=recommendation, reason=msg)

    # 6) record the scope on this session's registry entry (transient upkeep, degrade-clean).
    try:
        from . import session_registry as SR
        SR.touch(surface, scope=topic)
    except Exception:
        pass

    emit(f"created worktree at {wt_path} (branch '{name}') for “{topic}”. "
         f"Open a new mokata session there to work isolated.")
    return WorktreeCreateResult(True, path=wt_path, branch=name,
                                recommendation=recommendation, scope=topic)
