"""TM.S6 — the scope hierarchy + union read (doc 62 §2 axis A).

Every shared-memory item is written at exactly ONE level of a strict, broad→narrow chain:

    global  →  team  →  project  →  in-project category  →  personal

The chain is a **linear strict order** (each level has exactly ONE parent) plus single-parent
in-project categories (a category sits under exactly one project). There is NO diamond
inheritance, so **cycles are impossible by construction** — `parent_level` always steps to a
strictly-broader level and terminates at `global` (asserted in `assert_acyclic`).

On READ, an agent working in a context `(team T, project P, category C, user U)` sees the
**UNION** of every level on its path (the additive-accumulation rule from IAM/RBAC): broader
context is never silently dropped. `scope_path` builds the ordered path; `union_read` filters a
flat item list down to the items whose home scope lies on that path, preserving input order.

LOCAL mode is the **personal-only path** (`personal_context`): its union is exactly the set of
personal items, so recall on a personal-only repo is byte-identical to pre-TM.S6 behaviour —
every legacy item defaults to the personal level (`DEFAULT_SCOPE_LEVEL`), so all of them match.

This module is PURE (no I/O, no package imports) and duck-typed over items: it reads only
`item.scope_level` / `item.scope_id`, so both `MemoryItem` and lightweight test structs work.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

# ---------------------------------------------------------------- the scope levels (broad→narrow)
GLOBAL = "global"        # org-wide, everyone
TEAM = "team"            # one team
PROJECT = "project"      # one project (maps the Stage-71a project key)
CATEGORY = "category"    # an in-project sub-grouping (single parent under one project)
PERSONAL = "personal"    # the individual / private — today's default local memory

# The authoritative broad→narrow chain. Index == depth: broader = smaller depth, narrower =
# larger. This tuple is the SINGLE SOURCE of the ordering used everywhere (path + precedence).
SCOPE_LEVELS = (GLOBAL, TEAM, PROJECT, CATEGORY, PERSONAL)

# Every legacy / zero-config item lands here — so the personal-only path returns all of them
# (byte-identical local recall).
DEFAULT_SCOPE_LEVEL = PERSONAL

# In-project categories are USER-DEFINED free tags (not a fixed taxonomy) — like git branches.
# A few conventional defaults are offered for convenience only; any string is valid.
CONVENTIONAL_CATEGORIES = ("backend", "frontend", "infra", "docs", "tests", "data")

_DEPTH = {level: i for i, level in enumerate(SCOPE_LEVELS)}


def is_valid_scope(level: Any) -> bool:
    """True only for a recognised scope level."""
    return level in _DEPTH


def scope_depth(level: str) -> int:
    """Position in the broad→narrow chain: `global`==0 (broadest) … `personal`==4 (narrowest).
    An unrecognised level reads as the narrowest+1 (never crashes ordering) — fail-safe, but
    callers should pass a valid level."""
    return _DEPTH.get(level, len(SCOPE_LEVELS))


def parent_level(level: str) -> Optional[str]:
    """The single, strictly-broader parent of `level` (`None` for `global`, which is the root).
    The chain is linear + single-parent, so this is total and terminating — the guarantee that
    makes the hierarchy cycle-proof."""
    d = _DEPTH.get(level)
    if d is None or d == 0:
        return None
    return SCOPE_LEVELS[d - 1]


def assert_acyclic() -> None:
    """Prove (at import/test time) that the scope hierarchy is a strict linear chain with no
    diamond inheritance: walking `parent_level` from the narrowest level reaches `global` in
    exactly len-1 strictly-decreasing steps, visiting each level once. Raises AssertionError
    on any cycle / repeated node / non-decreasing depth."""
    seen = set()
    level: Optional[str] = SCOPE_LEVELS[-1]
    steps = 0
    while level is not None:
        assert level not in seen, f"cycle detected in scope chain at {level!r}"
        seen.add(level)
        parent = parent_level(level)
        if parent is not None:
            assert scope_depth(parent) < scope_depth(level), "parent must be strictly broader"
            steps += 1
        level = parent
    assert seen == set(SCOPE_LEVELS), "chain must visit every level exactly once"
    assert steps == len(SCOPE_LEVELS) - 1, "linear chain has exactly len-1 edges"


@dataclass(frozen=True)
class ScopeRef:
    """One element on a read path: a level + the id selected at that level. `id is None` means
    'match ANY id at this level' (used for `global`, which has no id, and for the personal level
    until per-user identity lands in TM.S10)."""

    level: str
    id: Optional[str] = None


@dataclass(frozen=True)
class ScopeContext:
    """The working context a read resolves against. `team`/`project`/`category` name the ids in
    scope (None = that level is absent from the path); `user` names the personal id (None =
    match any personal item — the pre-identity default). `include_global` forces the global
    element on/off; None = auto (present whenever the context is shared, i.e. a team or project
    is set — a purely-personal/local context omits it)."""

    team: Optional[str] = None
    project: Optional[str] = None
    category: Optional[str] = None
    user: Optional[str] = None
    include_global: Optional[bool] = None

    @property
    def shared(self) -> bool:
        return self.team is not None or self.project is not None or self.category is not None


def personal_context(user: Optional[str] = None) -> ScopeContext:
    """The LOCAL-mode context — a personal-only path (no global/team/project/category). Its
    union is exactly the personal items, giving byte-identical local recall."""
    return ScopeContext(user=user, include_global=False)


def scope_path(context: ScopeContext) -> List[ScopeRef]:
    """The ordered broad→narrow read path for `context`. `global` (when shared) first, then each
    named level, and ALWAYS the personal element last (the reader's own private memory). Absent
    levels are omitted; the result is deterministic."""
    include_global = (context.include_global
                      if context.include_global is not None else context.shared)
    refs: List[ScopeRef] = []
    if include_global:
        refs.append(ScopeRef(GLOBAL))
    if context.team is not None:
        refs.append(ScopeRef(TEAM, context.team))
    if context.project is not None:
        refs.append(ScopeRef(PROJECT, context.project))
    if context.category is not None:
        refs.append(ScopeRef(CATEGORY, context.category))
    refs.append(ScopeRef(PERSONAL, context.user))
    return refs


def _item_scope(item: Any) -> "tuple[str, str]":
    level = getattr(item, "scope_level", "") or DEFAULT_SCOPE_LEVEL
    sid = getattr(item, "scope_id", "") or ""
    return level, sid


def on_path(item: Any, path: Sequence[ScopeRef]) -> bool:
    """True when `item`'s home scope lies on `path`. An item matches a ref when the levels agree
    AND (the ref is global | the ref matches any id | the ids are equal | the item is a
    legacy/default personal item with an empty id, which matches any personal element)."""
    level, sid = _item_scope(item)
    for ref in path:
        if ref.level != level:
            continue
        if level == GLOBAL:
            return True
        if ref.id is None or ref.id == sid:
            return True
        if level == PERSONAL and sid == "":
            return True          # legacy / local-default personal → matches any personal reader
    return False


def union_read(items: Sequence[Any], context: ScopeContext) -> List[Any]:
    """The UNION read: the subset of `items` whose home scope is on `context`'s path, preserving
    the input order (stable). Broader context is additively included, never dropped."""
    path = scope_path(context)
    return [it for it in items if on_path(it, path)]
