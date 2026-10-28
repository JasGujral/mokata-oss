"""What SHIPS, for guards that grade the copy a reader meets.

A dev tree carries records the public mirror never gets — `docs/build/` (the build log),
`docs/talks/`, `docs/launch/`, `docs/marketing/`, `CLAUDE.md`. Those quote superseded numbers,
old minimum versions and retired command spellings ON PURPOSE: they are a history, kept rather
than edited. A product guard that walks them reds on history, which is what Jas's dev-tree run
of the 0.0.26 checkpoint hit (O16, 14 failures) while the mirror clone on the bed stayed green.

⭐ The boundary is DERIVED, never remembered here: `_public_counts._mirror_excluded_prefixes`
reads `scripts/sync-public.sh`, the thing that actually copies the bytes. On the public mirror
that script is absent — there everything present ships, so `is_shipped` answers True and a
guard's scope is the whole tree, which is exactly right for the tree users clone.
"""
from __future__ import annotations

import os

import _public_counts
import _support


def internal_prefixes(root: str) -> frozenset:
    """The prefixes the mirror never gets, or an empty set when this tree IS the published one."""
    try:
        return _public_counts._mirror_excluded_prefixes(root)
    except _public_counts.MirrorScriptAbsent:
        return frozenset()


def is_shipped(root: str, path: str) -> bool:
    """True when `path` (absolute, inside `root`) is part of the published tree."""
    # `_support.posix_rel`, never a bare relpath: a repo-relative path used as an IDENTITY goes
    # through the one converter (`test_repo_paths_invariant`), or two spellings of the same file
    # compare unequal on Windows.
    rel = _support.posix_rel(path, root)
    return not any(rel == p or rel.startswith(p + "/") for p in internal_prefixes(root))
