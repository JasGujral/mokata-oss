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
