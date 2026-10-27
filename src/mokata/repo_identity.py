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


def find_project_root(start: str = ".") -> str:
    """Resolve the real project root from any directory inside it (Stage 23).

    Walks up from `start` and returns the nearest ancestor that already holds an
    initialized `.mokata/manifest.json`; failing that, the nearest VCS root (`.git`);
    failing that, `start` itself. This is what lets `init`/`status`/the MCP tools and the
    SessionStart offer all agree on whether the project is set up — so an already-init
    repo is recognized from a subdirectory and never re-offered init (the "asks every
    time" bug).

    WT-ROOT — both tiers were blind to git worktrees, in the two different ways a path-walk can be.
    The manifest tier is now `repo_identity.resolve_mokata_root` (shared with
    `gate_hook.find_mokata_root`, so the two cannot disagree). The VCS tier tested
    `os.path.isdir(".git")`, and a linked worktree's `.git` is a FILE — so it walked straight past
    the worktree it was standing in and answered `start`, which is how `mokata init` came to be
    offered INSIDE a worktree. It now resolves through `canonical_repo_root`, the same primitive
    `repo_identity` already uses for the registry.

    It lives HERE, beside the resolver it wraps, and `config` re-exports it: the PostToolUse
    dirty-track hook needs the root on every write and must not import `config` (detect, manifest,
    router, schema) to get it (HOOK-BUDGET, 0.0.25 Lane HB)."""
    cur = os.path.abspath(start)

    resolved = resolve_mokata_root(cur).root
    if resolved is not None:
        return resolved

    if _common_dir(cur) is not None:
        return canonical_repo_root(cur)

    return cur


def is_initialized(root: str = ".") -> bool:
    """Does `root` itself hold an initialized `.mokata/manifest.json`? (`Surface.is_initialized`.)"""
    from . import MANIFEST_FILENAME, MOKATA_DIR
    return os.path.exists(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME))


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


# --------------------------------------------------------------------------- P31: ONE state, per-tree facts
# 0.0.26 P31 (SESSION-WORKTREES, Jas 2026-10-22) — every Claude Code session runs in its own git
# worktree (`.claude/worktrees/<name>/`), and `.mokata/temp_local/` is gitignored, so a fresh
# worktree has none. Runs, sessions, approvals, snapshots, the ledger and the IDE's own state are ONE
# store shared by every tree of the repo — the main checkout's — or the IDE (which runs from the
# main checkout) never sees a worktree session's run, and a merged-then-removed worktree would take
# its run history with it. What is genuinely a fact about ONE tree's files (the AST parse cache, the
# freshness dirty-set, anchor fingerprints) still lives in that same store, keyed by the tree.
# This supersedes WT-ROOT's "pipeline state stays local to the tree" (2026-08-04) by ruling.

#: Per-tree facts of a LINKED worktree live under `<main>/.mokata/temp_local/trees/<key>/`.
TREES_DIRNAME = "trees"


def worktree_toplevel(path: str) -> Optional[str]:
    """The top of the LINKED worktree `path` is in (the directory whose `.git` is a FILE pointing
    into another repo's `worktrees/`), or None — for a main checkout, a submodule, a non-git dir.
    Pure file reads; never raises."""
    try:
        cur = os.path.abspath(path)
    except (OSError, ValueError):
        return None
    while True:
        gp = os.path.join(cur, _GIT)
        try:
            if os.path.isdir(gp):
                return None
            if os.path.isfile(gp):
                common = _common_from_gitfile(gp)
                if common and os.path.basename(common) == _GIT and \
                        os.path.realpath(os.path.dirname(common)) != os.path.realpath(cur):
                    return cur
                return None
        except OSError:
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def state_root(root: str) -> str:
    """The directory whose `.mokata/` holds the ONE shared state for `root`'s repo: the main
    checkout when `root` is in a linked worktree of a repo initialised there, else `root` itself
    (byte-identical for a main checkout or a non-git dir). Never conjures a store: a main checkout
    with no `.mokata/` leaves `root` as it was (`canonical_mokata_dir`'s rule)."""
    from . import MOKATA_DIR
    if not root:
        return root                       # the bare layout tail (`state_dir("")`): no repo above it
    return os.path.dirname(canonical_mokata_dir(os.path.join(root, MOKATA_DIR)))


def temp_local_dir(root: str) -> str:
    """`<state root>/.mokata/temp_local` — every path builder of shared run-state goes through this."""
    from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
    return os.path.join(state_root(root), MOKATA_DIR, TEMP_LOCAL_DIRNAME)


def tree_key(tree: str) -> str:
    """A filesystem-safe key for a linked worktree: its basename + a short hash of its real path
    (two `.claude/worktrees/a` in two repos, or a re-created one at a new path, never collide)."""
    import hashlib
    real = os.path.normcase(os.path.realpath(os.path.abspath(tree)))
    base = "".join(c if c.isalnum() or c in "-_." else "-" for c in os.path.basename(real))[:40]
    return f"{base or 'tree'}-{hashlib.sha256(real.encode('utf-8')).hexdigest()[:8]}"


def tree_temp_local_dir(root: str) -> str:
    """Where a PER-TREE fact about `root`'s files lives: the shared `temp_local` for the main
    checkout (unchanged), `<main>/.mokata/temp_local/trees/<key>/` for a linked worktree — so a
    worktree's AST cache or dirty-set never mixes with the main checkout's, and nothing is ever
    written inside the worktree itself."""
    shared = temp_local_dir(root)
    tree = worktree_toplevel(root)
    if tree is None or os.path.realpath(state_root(root)) == os.path.realpath(tree):
        return shared
    return os.path.join(shared, TREES_DIRNAME, tree_key(tree))
