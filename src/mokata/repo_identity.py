"""WT.S1 — ONE canonical repo identity, the same for a checkout and every worktree of it.

Two Claude Code windows on one repo collide on the WORKING TREE; a git worktree is the escape
hatch (MS.S2 already split the STATE layer per session). But a worktree has a DIFFERENT path than
the main checkout, so anything that keys off the working path — sibling detection (WT.S1) and the
team `project` key (WT.S2) — would treat a worktree as a different repo, missing the collision and
silently forking team memory.

This module is the fix: a single identity derived from git's COMMON DIR (`…/main/.git`), which is
shared by the main checkout and every linked worktree. Detection is ZERO-SUBPROCESS — a worktree's
`.git` is a FILE (`gitdir: …/main/.git/worktrees/<n>`) whose `commondir` sibling points at the
common dir; the main checkout's `.git` is a DIR that IS the common dir. Reading those files beats
shelling out to `git rev-parse --git-common-dir`, and is fully degrade-clean: any non-git dir /
parse error falls back to the plain path, never a crash.

  * `repo_identity(root)`    — the canonical identity (realpath of the common dir), for grouping
                               sibling sessions; SAME across worktrees, distinct across repos.
  * `canonical_repo_root(root)` — the MAIN checkout's toplevel: SAME across worktrees, and equal to
                               `abspath(root)` for a non-worktree (so the existing project key is
                               byte-identical — the pinned-project-key regression guard holds).
  * `worktree_label(root)`   — "main" for the primary checkout, else the worktree's relative path
                               (for `mokata windows`).

Dependency-free (stdlib only), clean-room. Copyright 2026 MoStack. Licensed under the Apache
License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

_GIT = ".git"


def _common_from_gitfile(gitfile: str) -> Optional[str]:
    """The git COMMON DIR reached from a worktree's `.git` FILE, by pure file reads. Returns the
    realpath of the common dir, or None on any parse/IO error (degrade-clean). Handles both a
    linked worktree (`<gitdir>/commondir` → the shared `…/main/.git`) and a submodule (no
    `commondir`; the gitdir IS its own common dir)."""
    try:
        with open(gitfile, "r", encoding="utf-8", errors="replace") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    if not line.lower().startswith("gitdir:"):
        return None
    gitdir = line[len("gitdir:"):].strip()
    if not gitdir:
        return None
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(os.path.dirname(gitfile), gitdir)
    gitdir = os.path.realpath(gitdir)
    common = gitdir
    commondir_file = os.path.join(gitdir, "commondir")
    if os.path.isfile(commondir_file):
        try:
            with open(commondir_file, "r", encoding="utf-8", errors="replace") as fh:
                rel = fh.readline().strip()
        except OSError:
            rel = ""
        if rel:
            common = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
    return os.path.realpath(common)


def _common_dir(root: str) -> Optional[str]:
    """The git common dir for `root` (walking up to the first `.git`), or None for a non-git dir.
    A `.git` DIR is the common dir itself; a `.git` FILE is resolved via `_common_from_gitfile`.
    Never raises."""
    try:
        cur = os.path.abspath(root)
    except OSError:
        return None
    while True:
        gp = os.path.join(cur, _GIT)
        try:
            if os.path.isdir(gp):
                return os.path.realpath(gp)
            if os.path.isfile(gp):
                return _common_from_gitfile(gp)
        except OSError:
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def repo_identity(root: str) -> str:
    """The ONE canonical identity for `root`'s repo — the realpath of the git common dir, SAME for
    the main checkout and every linked worktree of it, distinct across repos. Degrade-clean: a
    non-git dir identifies as its own realpath (so two unrelated non-git dirs never collide)."""
    common = _common_dir(root)
    if common:
        return common
    try:
        return os.path.realpath(os.path.abspath(root))
    except OSError:
        return os.path.abspath(root)


def canonical_repo_root(root: str) -> str:
    """The MAIN checkout's toplevel for `root`'s repo — SAME across the main checkout and every
    worktree of it. For a non-worktree (main checkout, subdir, or non-git dir) this is exactly
    `abspath(root)` when there is no symlink, so the derived project key is unchanged (the
    pinned-project-key regression guard holds). For a linked worktree it resolves to the main
    checkout's toplevel, so the team `project` identity no longer splits (WT.S2)."""
    ar = os.path.abspath(root)
    common = _common_dir(ar)
    if common and os.path.basename(common) == _GIT:
        return os.path.dirname(common)
    return ar


# --------------------------------------------------------------------------- WT-ROOT: root resolution
@dataclass(frozen=True)
class RootResolution:
    """Where `.mokata/` actually is for a directory — and, when it is nowhere, WHY.

    `root` is the directory holding `.mokata/manifest.json`, or None. The two flags are what stop a
    worktree failure from being indistinguishable from a fresh directory:

      * `via_worktree` — resolution only succeeded by redirecting a linked worktree to its main
        checkout. Nothing downstream needs it, but it is the difference between "found it" and
        "found it somewhere the caller did not ask about", and surfaces say so.
      * `unresolved_worktree` — this IS a linked worktree of a repo that visibly HAS a `.mokata/`,
        and resolution still failed. That is a BROKEN mokata repo, not a non-mokata one, and it must
        never take the same silent instant-exit-0 path (stage 17's posture on `ledger=None`)."""

    root: Optional[str]
    via_worktree: bool = False
    unresolved_worktree: bool = False
    detail: str = ""


def _walk_up_for(start: str, *parts: str) -> Optional[str]:
    """The nearest ancestor of `start` (inclusive) holding `parts`, else None. The plain ancestor
    walk both root-finders have always done — one `os.path.exists` per ancestor, unchanged."""
    try:
        cur = os.path.abspath(start)
    except OSError:
        return None
    while True:
        if os.path.exists(os.path.join(cur, *parts)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _is_linked_worktree(root: str) -> bool:
    """True when `root` (or an ancestor) is a linked worktree — its `.git` is a FILE, not a dir.
    `selfprotect.py` already knows this shape; this is the same test, named once."""
    try:
        cur = os.path.abspath(root)
    except OSError:
        return False
    while True:
        gp = os.path.join(cur, _GIT)
        try:
            if os.path.isdir(gp):
                return False
            if os.path.isfile(gp):
                return True
        except OSError:
            return False
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def resolve_mokata_root(start: str = ".") -> RootResolution:
    """THE canonical resolver for "where is this directory's mokata repo" — the one place that knows
    a git worktree is not a different repository, which `gate_hook.find_mokata_root` and
    `config.find_project_root` both inherit rather than each re-deriving.

    Order matters, and it is chosen so the common cases pay nothing:

      1. the plain ancestor walk for `.mokata/manifest.json`. A main checkout resolves here on the
         first probe, exactly as before.
      2. if that landed inside a LINKED WORKTREE, redirect to the main checkout. This is not
         paranoia — `.mokata/.gitignore` ignores only `temp_local/`, so `manifest.json` and
         `constitution.md` are COMMITTED and every worktree gets a checked-out copy. Without this
         step the worktree's own copy wins, and the repo quietly grows a second memory store and a
         second audit ledger beside it. Config in a tree is config; it is not a state root.
      3. if the walk found nothing, redirect from the canonical repo root and walk again — the case
         where the manifest was never committed, so the worktree has no `.mokata/` at all.
      4. still nothing: distinguish "never a mokata repo" (silent, the instant exit 0) from "a
         worktree of a repo that HAS `.mokata/`, unresolved" (which SPEAKS — see `RootResolution`).

    Never raises: every probe is an `os.path` test, and a non-git / unreadable tree resolves to
    None exactly as it always did."""
    from . import MANIFEST_FILENAME, MOKATA_DIR

    found = _walk_up_for(start, MOKATA_DIR, MANIFEST_FILENAME)
    canon = canonical_repo_root(found if found else start)

    if found is not None:
        if os.path.realpath(canon) == os.path.realpath(found):
            return RootResolution(found)                       # main checkout: byte-identical
        # (2) the manifest we found is a worktree's checked-out COPY of committed config.
        canon_found = _walk_up_for(canon, MOKATA_DIR, MANIFEST_FILENAME)
        if canon_found is not None:
            return RootResolution(canon_found, via_worktree=True)
        return RootResolution(found)     # the main checkout has none — the tree's copy is all there is

    # (3) nothing in this tree — the manifest was never committed, so a worktree has no `.mokata/`.
    if os.path.realpath(canon) != os.path.realpath(os.path.abspath(start)):
        canon_found = _walk_up_for(canon, MOKATA_DIR, MANIFEST_FILENAME)
        if canon_found is not None:
            return RootResolution(canon_found, via_worktree=True)

    # (4) genuinely unresolved. Say WHICH kind of unresolved this is.
    if _is_linked_worktree(start) and os.path.isdir(os.path.join(canon, MOKATA_DIR)):
        return RootResolution(
            None, unresolved_worktree=True,
            detail=f"{os.path.abspath(start)} is a linked git worktree of {canon}, which has a "
                   f"{MOKATA_DIR}/ but no readable {MANIFEST_FILENAME}")
    return RootResolution(None)


def canonical_mokata_dir(mokata_dir: str) -> str:
    """The MAIN checkout's `.mokata/` for a `.mokata/` path in ANY tree of the repo — the redirect
    the repo-scoped stores (audit ledger, memory store) apply so a worktree writes to the ONE store
    rather than growing an empty second one.

    Byte-identical for the common case: a main checkout / non-git dir returns the path it was given
    (identity), so nothing that was already correct moves. The redirect is also refused when the
    main checkout has no `.mokata/` of its own — a store is never conjured somewhere the user has
    never initialized. Degrade-clean: any resolution error returns the input unchanged."""
    try:
        given = os.path.abspath(mokata_dir)
        tree = os.path.dirname(given)
        canon = canonical_repo_root(tree)
        if os.path.realpath(canon) == os.path.realpath(tree):
            return mokata_dir                                  # main checkout / non-git: unchanged
        redirected = os.path.join(canon, os.path.basename(given))
        return redirected if os.path.isdir(redirected) else mokata_dir
    except OSError:
        return mokata_dir


def worktree_label(root: str) -> str:
    """A short, human-scannable label for `root`'s checkout in `mokata windows`: "main" for the
    primary checkout, else the worktree's path relative to the main checkout (falling back to the
    basename when a relative path can't be formed). Degrade-clean."""
    try:
        ar = os.path.realpath(os.path.abspath(root))
    except OSError:
        return "main"
    canon = os.path.realpath(canonical_repo_root(root))
    if ar == canon:
        return "main"
    try:
        return os.path.relpath(ar, canon)
    except ValueError:
        return os.path.basename(ar)
