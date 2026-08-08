"""The ONE rule for walking a repo's own source: where does this checkout STOP?

A walker rooted at a repo will happily descend into a SECOND checkout placed inside it and
count its files as this repo's. That is not hypothetical — a git worktree at
`.claude/worktrees/…`, created by the harness for a parallel session, duplicated every pinned
file and turned a repo-wide sweep red at a clean HEAD (0.0.17 stage 20). The product walkers
escaped that incident for one reason only: `.claude` happens to start with a dot, and every
walker prunes dot directories. The defence was a coincidence, not a rule.

So the rule here is keyed on STRUCTURE, not on a name:

    a directory carrying a `.git` ENTRY is the root of a DIFFERENT checkout.

Both on-disk shapes count, and a caller must not have to know which it is looking at — `.git`
as a DIRECTORY (a clone or a submodule) and `.git` as a FILE (the `gitdir:` pointer that
`git worktree add` writes, and the shape that caused the incident). A name-based skip-list
(`.claude`, `worktrees`, `vendor`, …) encodes whichever tool bit last: it passes for the
directory somebody already lost a day to and fails for the next one.

Two functions, deliberately small:

  * `is_checkout_boundary(path)` — the predicate, and the ONE definition of it in the tree.
    `tests/_support.py` imports it rather than keeping a second copy; a rule this repo has
    already been burned by name-drift on does not get to exist twice.
  * `prune_source_dirs(dirpath, dirnames, …)` — the in-place `os.walk` prune every product
    walker shares: hidden directories AND nested checkouts, with the skipped checkouts
    RECORDED so a caller can declare them rather than swallowing them.

The two rules COMPOSE; the boundary rule is additional, never a replacement. `.venv`,
`.mypy_cache` and `.mokata` carry no `.git` entry, so dropping the dot rule to "unify" the two
would widen indexing enormously. A checkout inside a dot directory is skipped as HIDDEN before
the boundary rule is ever consulted, and is therefore NOT recorded — the record answers "what
did the boundary rule cost you", and a dot directory was never going to be walked anyway.

Leaf module: stdlib only, imports nothing from mokata, so the walkers in `knowledge/`,
`engine/` and `detect.py` can all share it without an import edge between them.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import List, Optional

# The entry whose PRESENCE marks a checkout root, whichever shape it takes on disk.
CHECKOUT_MARKER = ".git"


def is_checkout_boundary(path: str) -> bool:
    """True when `path` is the root of a checkout — i.e. it carries a `.git` entry of either
    shape (a directory for a clone/submodule, a file for a linked worktree).

    Callers apply this to SUBDIRECTORIES of the walk root. The root carries `.git` itself, and
    pruning it would yield an empty tree that every sweep reads as green."""
    return os.path.exists(os.path.join(path, CHECKOUT_MARKER))


def prune_source_dirs(dirpath: str, dirnames: List[str], *,
                      skipped: Optional[List[str]] = None) -> None:
    """Prune `dirnames` IN PLACE (the `os.walk` contract) to this repo's own source.

    Dropped: hidden directories (`.git`, `.venv`, `.mokata`, `.mypy_cache` — config, state and
    caches, never source) and nested checkouts (someone else's source, wherever it sits).

    When `skipped` is given, the ABSOLUTE path of each pruned checkout is appended to it, so
    the caller can say how many it skipped and where instead of silently narrowing its own
    answer. Hidden directories are not recorded: they are the long-standing rule, and a walker
    reporting `.venv` every run would bury the one line that matters."""
    kept = []
    for name in dirnames:
        if name.startswith("."):
            continue
        full = os.path.join(dirpath, name)
        if is_checkout_boundary(full):
            if skipped is not None:
                skipped.append(full)
            continue
        kept.append(name)
    dirnames[:] = kept


__all__ = ["CHECKOUT_MARKER", "is_checkout_boundary", "prune_source_dirs"]
