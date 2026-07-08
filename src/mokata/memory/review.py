"""TM.S11 — the PM review workflow: state machine + roles + separation of duties (doc 62 §6).

A proposed memory change moves through a small state machine, each transition human-gated +
ledgered (doc 62 §6, doc 63 §4):

    Draft  →  In-Review  →  Approved  →  Published        (+ Rejected from any non-terminal state)

Proposals feeding it — editor edits, S7 enforcement promotions, S9 retain-on-success formula
proposals — all ENTER as Drafts and ride the same rails.

This module is the PURE core: the legal transitions, the HARD separation-of-duties check
(proposer ≠ approver), and the required-approver (CODEOWNERS-style) resolution over the S10
`AccessPolicy` grant map — plus the rendered "what changed" diff (reusing the memory old→new
form). It does NO I/O and NO gating: the store wraps it with the WriteGate + audit ledger + the
supersede lineage (exactly like `promote` / `promote_scope`), so there is NO parallel RBAC and NO
parallel diff.

Everything is FAIL-CLOSED (deny on doubt): an unknown/illegal transition, a self-approval, a
missing/empty approver, or any undeterminable grant → the transition is REFUSED and nothing changes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, List, Optional, Set

from .access import APPROVER, PROMOTION_APPROVER

# ---------------------------------------------------------------- the review states (doc 62 §6)
DRAFT = "draft"
IN_REVIEW = "in-review"
APPROVED = "approved"
PUBLISHED = "published"
REJECTED = "rejected"
REVIEW_STATES = (DRAFT, IN_REVIEW, APPROVED, PUBLISHED, REJECTED)

# Terminal states — no onward transition (a published or rejected proposal is done).
TERMINAL_STATES = (PUBLISHED, REJECTED)

# The legal FORWARD transitions. A proposal advances Draft → In-Review → Approved → Published; a
# reject is legal from any non-terminal state. Anything NOT listed is refused (fail-closed) — you
# can never skip Approved to reach Published, so a change can't publish without review.
_TRANSITIONS = {
    DRAFT: (IN_REVIEW, REJECTED),
    IN_REVIEW: (APPROVED, REJECTED),
    APPROVED: (PUBLISHED, REJECTED),
    PUBLISHED: (),
    REJECTED: (),
}


def is_valid_state(state: Any) -> bool:
    return state in REVIEW_STATES


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(frm: str, to: str) -> bool:
    """True ONLY for a legal forward transition (fail-closed on anything else — an unknown source
    state, a terminal source, or a skipped step all return False)."""
    return to in _TRANSITIONS.get(frm, ())


# ---------------------------------------------------------------- separation of duties (HARD)
def separation_ok(proposer: Optional[str], approver: Optional[str]) -> bool:
    """The HARD separation of duties (doc 62 §6): the approver must be a real identity DIFFERENT
    from the proposer. Fail-closed — a missing/empty approver, a missing proposer (distinctness
    can't be proven), or a self-approval is REFUSED."""
    if not approver or not proposer:
        return False
    return approver != proposer


# ---------------------------------------------------------------- required approvers (CODEOWNERS)
def _scope_keys(scope: str, category: Optional[str]) -> List[str]:
    """The grant-map lookup keys for a scope (+category), matching `AccessPolicy._scope_keys`: the
    bare scope (applies to every category) then the `scope:category` narrowing."""
    keys = [scope]
    if category:
        keys.append(f"{scope}:{category}")
    return keys


def required_approvers(access: Any, scope: str, category: Optional[str] = None) -> Set[str]:
    """The identities NAMED as the approver(s) for `scope` (+category) in the S10 grant map
    (CODEOWNERS-style) — the PM(s) who must sign off a change to that domain (doc 62 §6). A
    promotion-approver implies approver (the RBAC ladder), so they count too. Empty when there is
    no policy or no named approver. Reads the grant map only; never widens it."""
    if access is None:
        return set()
    grants = getattr(access, "grants", {}) or {}
    out: Set[str] = set()
    for key in _scope_keys(scope, category):
        block = grants.get(key) or {}
        for role in (APPROVER, PROMOTION_APPROVER):     # ladder: promotion-approver ⊇ approver
            ids = block.get(role)
            if ids:
                out.update(ids)
    return out


# ---------------------------------------------------------------- the rendered "what changed" diff
def diff_line(base: Any, proposal: Any) -> str:
    """The short rendered "what changed" for a proposal, reusing the memory old→new form (the same
    `value!r -> value!r` shape the self-healing diff uses). `base` is the currently-published item
    the proposal changes (None for a brand-new item). An enforcement-only change (binding moved,
    value unchanged) surfaces the binding delta instead of an empty `x -> x`."""
    new = getattr(proposal, "value", "")
    if base is None:
        return f"(new) -> {new!r}"
    old = getattr(base, "value", "")
    if old == new:
        ob = getattr(base, "enforcement", "") or ""
        nb = getattr(proposal, "enforcement", "") or ""
        if ob != nb and (ob or nb):
            return f"enforcement {ob or 'advisory'} -> {nb or 'advisory'}"
        return f"{old!r} (unchanged)"
    return f"{old!r} -> {new!r}"


def render_transition(proposal: Any, base: Any, frm: str, to: str, actor: str) -> str:
    """The legible human-gate surface for a state transition — the rendered diff + who + from→to,
    honest that nothing changes without approval. The store shows this at the WriteGate."""
    subject = getattr(proposal, "subject", "")
    return (
        f"mokata · review [{frm or 'new'} → {to}]  {subject}\n"
        f"  what changed: {diff_line(base, proposal)}\n"
        f"  by: {actor}\n"
        "Nothing changes unless you approve — every transition is ledgered (P2)."
    )


def render_rollback(current: Any, prior: Any) -> str:
    """The human-gate surface for a rollback (restore the prior via supersede lineage)."""
    subject = getattr(current, "subject", "")
    cv = getattr(current, "value", "")
    pv = getattr(prior, "value", "")
    return (
        f"mokata · ROLLBACK {subject}\n"
        f"  restore: {cv!r} -> {pv!r} (the superseded prior)\n"
        "Nothing changes unless you approve — the rollback is ledgered (P2)."
    )
