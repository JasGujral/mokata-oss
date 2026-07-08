"""TM.S10 — scoped-consent access control (doc 52 M-1/M-2, doc 62 §6, doc 63 §4).

Access grants are CONFIG-BASED (CODEOWNERS-style — NO new DDL table; SQL/scale is 0.1.3): a team
declares, per scope (+ optional in-project category), which identities hold which ROLE. The RBAC
ladder is scoped to team needs (NOT a full IAM):

    viewer  ⊂  editor  ⊂  approver  ⊂  promotion-approver

Higher roles IMPLY the lower ones (doc 62 §6). A `promotion-approver` is the top — the only role
that may broaden an item's scope (doc 52 M-2). `approver` exists for the S11 review workflow; here
it only implies edit rights (S10 is enforcement + the scope-promotion gate, NOT the review workflow).

Everything is FAIL-CLOSED (deny on doubt): no grant, missing identity, malformed/undeterminable
config, or any error → DENY. A non-enforcing policy (local mode) allows everything, so the local /
zero-config path is byte-identical. The OWNER of a personal scope always has full access to their
OWN personal items (single-user default) — but owning personal scope is NOT authority to broaden it.

Grant config shape (`settings.access.grants`) — a plain dict, per scope-key → role → [identities]:

    {
      "global":          {"promotion-approver": ["org-steward"]},
      "team":            {"promotion-approver": ["lead"], "editor": ["alice", "bob"]},
      "project":         {"viewer": ["contractor"]},
      "project:backend": {"editor": ["carol"]}          # scope + in-project category
    }

A scope-level grant (no category) applies to every category in that scope; a `scope:category` grant
narrows to that category. This module is PURE + duck-typed (no I/O, no manifest coupling) so it is
trivially testable and the store injects it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Set

from .scope import PERSONAL

# ---------------------------------------------------------------- the RBAC ladder (team-scoped)
VIEWER = "viewer"
EDITOR = "editor"
APPROVER = "approver"
PROMOTION_APPROVER = "promotion-approver"
ROLES = (VIEWER, EDITOR, APPROVER, PROMOTION_APPROVER)

# rank in the ladder — a held role implies EVERY role at or below its rank (higher ⊇ lower).
_RANK = {VIEWER: 0, EDITOR: 1, APPROVER: 2, PROMOTION_APPROVER: 3}

_ACCESS_SETTINGS_KEY = "access"      # settings.access.grants


def _expand(held: Set[str]) -> Set[str]:
    """Expand a set of directly-granted roles down the ladder: the highest held rank implies every
    role at or below it (viewer ⊂ editor ⊂ approver ⊂ promotion-approver, doc 62 §6). Unknown roles
    contribute nothing (fail-closed — an unrecognised role is never a grant)."""
    ranks = [_RANK[r] for r in held if r in _RANK]
    if not ranks:
        return set()
    top = max(ranks)
    return {role for role, rank in _RANK.items() if rank <= top}


@dataclass
class AccessPolicy:
    """A config-derived access policy. `grants` maps a scope-key (`"<scope>"` or
    `"<scope>:<category>"`) → role → the identities holding it. `enforce=False` (local mode) allows
    everything — the byte-identical local path. All checks are fail-closed."""

    grants: Dict[str, Dict[str, FrozenSet[str]]] = field(default_factory=dict)
    enforce: bool = True

    # -------------------------------------------------- construction
    @classmethod
    def from_grants(cls, grants: Any, enforce: bool = True) -> "AccessPolicy":
        """Build a policy from raw config (`settings.access.grants`). Tolerant + fail-closed: a
        malformed scope block (not a role→ids map) is dropped to an EMPTY block (so it grants
        nothing — deny on doubt), never raised. Role names are kept verbatim (unknown ones are
        ignored at check time by `_expand`)."""
        normalized: Dict[str, Dict[str, FrozenSet[str]]] = {}
        if isinstance(grants, dict):
            for scope_key, block in grants.items():
                roles: Dict[str, FrozenSet[str]] = {}
                if isinstance(block, dict):
                    for role, ids in block.items():
                        if isinstance(ids, str):
                            ids = [ids]
                        try:
                            roles[str(role)] = frozenset(str(i) for i in ids)
                        except TypeError:
                            roles[str(role)] = frozenset()     # malformed ids → grants nothing
                normalized[str(scope_key)] = roles             # non-dict block → {} (deny on doubt)
        return cls(grants=normalized, enforce=enforce)

    @classmethod
    def from_settings(cls, settings: Any, enforce: bool = True) -> "AccessPolicy":
        """Build from a manifest settings dict — reads `settings.access.grants`. Fail-closed: a
        missing/odd settings shape yields an empty (deny-by-default when enforcing) policy."""
        try:
            block = (settings or {}).get(_ACCESS_SETTINGS_KEY) or {}
            grants = block.get("grants") or {}
        except AttributeError:
            grants = {}
        return cls.from_grants(grants, enforce=enforce)

    # -------------------------------------------------- role resolution
    def roles_for(self, identity: Optional[str], scope: str,
                  category: Optional[str] = None, owner: Optional[str] = None) -> Set[str]:
        """The EXPANDED roles `identity` holds for an item at `scope` (+ optional in-project
        `category`; `owner` for a personal item). Fail-closed: a falsy identity, or any lookup
        error, yields no roles. The personal-scope OWNER always gets full manage rights
        (viewer+editor) over their OWN (or a legacy empty-owner) personal item — never
        promotion-approver (broadening needs authority at the target scope)."""
        if not identity:
            return set()
        try:
            if scope == PERSONAL:
                if owner in (None, "", identity):
                    return {VIEWER, EDITOR}
                # someone else's personal item: only an explicit config grant can reach it
            held: Set[str] = set()
            for key in self._scope_keys(scope, category):
                block = self.grants.get(key) or {}
                for role, ids in block.items():
                    if identity in ids:
                        held.add(role)
            return _expand(held)
        except Exception:
            return set()                # deny on doubt (fail-closed)

    @staticmethod
    def _scope_keys(scope: str, category: Optional[str]):
        """The lookup keys for a scope (+category), broad→narrow: the bare scope (applies to every
        category) then the `scope:category` (narrows to that category). Roles union across both."""
        keys = [scope]
        if category:
            keys.append(f"{scope}:{category}")
        return keys

    # -------------------------------------------------- the checks (all fail-closed)
    def can_read(self, identity: Optional[str], scope: str,
                 category: Optional[str] = None, owner: Optional[str] = None) -> bool:
        if not self.enforce:
            return True
        return VIEWER in self.roles_for(identity, scope, category, owner=owner)

    def can_edit(self, identity: Optional[str], scope: str,
                 category: Optional[str] = None, owner: Optional[str] = None) -> bool:
        if not self.enforce:
            return True
        return EDITOR in self.roles_for(identity, scope, category, owner=owner)

    def can_approve(self, identity: Optional[str], scope: str,
                    category: Optional[str] = None, owner: Optional[str] = None) -> bool:
        """May `identity` APPROVE a proposed change at `scope` (+category)? Requires the approver
        role there (the PM for that project/category — TM.S11 review workflow, doc 62 §6). Reuses
        the same ladder as every other check (promotion-approver ⊇ approver). Fail-closed; a
        non-enforcing policy (local mode) approves nothing-is-required so it returns True."""
        if not self.enforce:
            return True
        return APPROVER in self.roles_for(identity, scope, category, owner=owner)

    def can_promote_scope(self, identity: Optional[str], to_scope: str,
                          category: Optional[str] = None) -> bool:
        """May `identity` broaden an item TO `to_scope` (doc 52 M-2)? Requires the
        promotion-approver role AT the target (broader) scope — the audience-widening gate."""
        if not self.enforce:
            return True
        return PROMOTION_APPROVER in self.roles_for(identity, to_scope, category)
