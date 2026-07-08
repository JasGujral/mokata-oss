"""TM.S8 — hard-rule enforcement IN-RUN (doc 62 §4, the Sentinel model + P14/P2).

S6 gathers the in-scope UNION across the scope path; S7 binds each rule's enforcement level
(advisory | soft | hard) SEPARATELY from its condition. This is where enforcement actually
FIRES: for a pending code-writing action, the in-scope governance rules it trips are evaluated
and the Sentinel semantics applied —

  * advisory  → surfaced / FLAGGED in the briefing; the run PROCEEDS (no block);
  * soft      → BLOCKS, but an explicit override is allowed and LEDGERED (who / which rule /
                approval / reason — privilege separation + non-repudiation, never a silent warn);
  * hard      → BLOCKS with NO runtime override (a hard rule leaves only by removal / supersede,
                itself a gated write — P2);
  * fail-closed on doubt — if a rule's enforcement can't be determined, it is treated as blocking.

MOST-RESTRICTIVE-WINS at any depth (S6 safety class): a hard violation ANYWHERE on the path is
authoritative — a narrower scope can never loosen it (deny-overrides). The block reads as a
LEGIBLE verdict via `legibility.gate_verdict` — the same legible-gate format mokata already uses.

Enforcement of EXISTING rules only — no new rule authoring / kinds / DDL. This module is duck-typed
over items (reads `subject` / `id` / `kind` / `enforcement` / `scope_level` / `scope_id` / `value`),
so both `MemoryItem` and lightweight structs work. Pure evaluation; the gate does the ledgering.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from ..legibility import gate_verdict
from ..memory.item import (
    ADVISORY,
    ENFORCEMENT_LEVELS,
    HARD,
    RULE,
    SOFT,
    effective_enforcement,
    governance_kind,
)

# Audit-ledger kinds (visible in `mokata audit`): a soft OVERRIDE (non-repudiation) and a
# recorded BLOCK. Labelled for the why-timeline in `govern.ledger._WHY_WHAT`.
RULE_OVERRIDE_KIND = "rule_override"
RULE_BLOCK_KIND = "rule_block"

# The condition text stamped on a fail-closed (undeterminable) violation — treated as blocking.
_UNDETERMINABLE = "enforcement undeterminable — fail-closed (treated as blocking)"


# ---------------------------------------------------------------- the pending action
@dataclass(frozen=True)
class PendingAction:
    """The code-writing action under evaluation. `violates` is the set of in-scope rule subjects
    (or ids) this action is KNOWN to trip — determined upstream (content analysis / the caller).
    TM.S8 is the enforcement of that decision, not the violation-detection itself: it applies the
    Sentinel semantics to whichever in-scope rules the action names."""

    kind: str = "code"
    target: str = ""
    summary: str = ""
    violates: frozenset = field(default_factory=frozenset)


# ---------------------------------------------------------------- one violated rule
@dataclass
class RuleViolation:
    rule_id: str
    subject: str
    enforcement: str          # advisory | soft | hard  (hard for a fail-closed violation)
    scope_level: str
    scope_id: str
    condition: str            # the rule's value / "why it fired"
    determinable: bool = True  # False → enforcement couldn't be read → fail-closed → blocking


# ---------------------------------------------------------------- the verdict
@dataclass
class EnforcementVerdict:
    """The buckets an action's violated in-scope rules fall into. `hard`/`fail_closed` are
    non-overridable blocks; `soft` is a block an explicit override can clear; `advisory` only
    flags. Most-restrictive-wins: a hard/fail-closed violation makes the whole verdict a
    non-overridable block regardless of any soft/advisory sibling."""

    action: PendingAction
    hard: List[RuleViolation] = field(default_factory=list)
    soft: List[RuleViolation] = field(default_factory=list)
    advisory: List[RuleViolation] = field(default_factory=list)
    fail_closed: List[RuleViolation] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.hard or self.soft or self.fail_closed)

    @property
    def overridable(self) -> bool:
        """A block is overridable ONLY when it is purely soft — a hard or fail-closed violation
        anywhere denies the runtime override (deny-overrides / most-restrictive-wins)."""
        return bool(self.soft) and not self.hard and not self.fail_closed

    @property
    def flags(self) -> List[RuleViolation]:
        """Advisory violations — surfaced in the briefing, never blocking."""
        return list(self.advisory)

    @property
    def blocking(self) -> Optional[RuleViolation]:
        """The AUTHORITATIVE violation to name in the verdict: the most-restrictive that fired
        (fail-closed, then hard, then soft), or None on a clean pass."""
        for bucket in (self.fail_closed, self.hard, self.soft):
            if bucket:
                return bucket[0]
        return None


# ---------------------------------------------------------------- gather: union (S6) × kind (S7)
def in_scope_rules(items: Sequence[Any], context: Any = None) -> List[Any]:
    """The in-scope governance RULES for an action: the S6 UNION across `context`'s scope path
    (None = local/personal — no scope filter, every item is in scope), then filtered to
    governance_kind == rule (S7 — facts/formulae never enforce). Order preserved."""
    if context is None:
        scoped = list(items)
    else:
        from ..memory.scope import union_read
        scoped = union_read(items, context)
    return [it for it in scoped if governance_kind(getattr(it, "kind", "")) == RULE]


def _default_matcher(rule: Any, action: PendingAction) -> bool:
    """A rule is violated by the action when the action names its subject (or id) in `violates`."""
    return (getattr(rule, "subject", "") in action.violates
            or getattr(rule, "id", "") in action.violates)


def _violation(rule: Any, level: str, determinable: bool) -> RuleViolation:
    return RuleViolation(
        rule_id=getattr(rule, "id", "") or "",
        subject=getattr(rule, "subject", "") or "",
        enforcement=level,
        scope_level=getattr(rule, "scope_level", "") or "",
        scope_id=getattr(rule, "scope_id", "") or "",
        condition=(getattr(rule, "value", "") or ""),
        determinable=determinable,
    )


# ---------------------------------------------------------------- the enforcement decision (pure)
def evaluate(action: PendingAction, rules: Sequence[Any], *, context: Any = None,
             matcher: Optional[Callable[[Any, PendingAction], bool]] = None) -> EnforcementVerdict:
    """Evaluate `action` against the in-scope governance rules → an EnforcementVerdict. Pure and
    deterministic: no I/O, no ledgering (the gate does that). Fail-closed — a rule whose
    enforcement can't be read is bucketed as fail-closed (a non-overridable block), never ignored."""
    match = matcher or _default_matcher
    verdict = EnforcementVerdict(action=action)
    for rule in in_scope_rules(rules, context):
        try:
            if not match(rule, action):
                continue
            level = effective_enforcement(rule)
            if level not in ENFORCEMENT_LEVELS:
                raise ValueError(f"unrecognized enforcement {level!r}")
        except Exception:
            # DOUBT → fail-closed: treat as a hard, non-overridable block (P2/P14).
            v = _violation(rule, HARD, determinable=False)
            v.condition = _UNDETERMINABLE
            verdict.fail_closed.append(v)
            continue
        if level == HARD:
            verdict.hard.append(_violation(rule, HARD, True))
        elif level == SOFT:
            verdict.soft.append(_violation(rule, SOFT, True))
        else:  # ADVISORY
            verdict.advisory.append(_violation(rule, ADVISORY, True))
    return verdict


# ---------------------------------------------------------------- the legible verdict
def _reason(v: RuleViolation) -> str:
    scope = f"[{v.scope_level}]" if v.scope_level else "[personal]"
    cond = f" — {v.condition}" if v.condition else ""
    return f"{v.enforcement} rule '{v.subject}' {scope}{cond}"


def _unblock(v: RuleViolation) -> str:
    if v.enforcement == SOFT and v.determinable:
        return ("approve an explicit override (recorded to the audit ledger) or comply with "
                "the rule")
    return ("remove or supersede the rule (a gated write) — a hard rule cannot be overridden "
            "at runtime")


def render_verdict(verdict: EnforcementVerdict, *, ascii_only: bool = False,
                   color: bool = False) -> str:
    """One LEGIBLE line for the enforcement decision, reusing `legibility.gate_verdict` (the same
    format every other mokata gate uses). A block names the authoritative rule, its scope, and why
    it fired, plus the single unblock action. A clean pass reads as a legible pass; advisory flags
    are appended so the briefing surfaces them."""
    fired = verdict.blocking
    if fired is None:
        reason = "no in-scope rule blocks this action"
        if verdict.flags:
            reason += " (advisory: " + ", ".join(f"'{f.subject}'" for f in verdict.flags) + ")"
        return gate_verdict("governance", True, reason, ascii_only=ascii_only, color=color)
    name = f"{fired.enforcement}-rule"
    return gate_verdict(name, False, _reason(fired), action=_unblock(fired),
                        ascii_only=ascii_only, color=color)


# ---------------------------------------------------------------- the gate (block / override / ledger)
@dataclass
class EnforcementOutcome:
    allowed: bool                     # may the action proceed?
    overridden: bool                  # a soft block cleared by an explicit, ledgered override
    verdict: EnforcementVerdict
    message: str                      # the legible verdict line


class EnforcementGate:
    """Applies the Sentinel decision at a code-writing entry point (P14). A hard / fail-closed
    block cannot be overridden at runtime; a soft block is cleared ONLY by an explicit override,
    which is recorded to the audit ledger (non-repudiation). Advisory flags proceed and ledger
    nothing. Every block is itself recorded, so `mokata audit` shows what fired and why."""

    def __init__(self, ledger: Any = None) -> None:
        self.ledger = ledger

    def _record(self, kind: str, v: RuleViolation, *, actor: str, decision: str,
                reason: str = "") -> None:
        if self.ledger is not None:
            self.ledger.record(kind, item_id=v.rule_id, subject=v.subject,
                               enforcement=v.enforcement, scope_level=v.scope_level,
                               actor=actor, decision=decision, reason=reason)

    def check(self, action: PendingAction, rules: Sequence[Any], *, context: Any = None,
              matcher: Optional[Callable[[Any, PendingAction], bool]] = None,
              override: Optional[Callable[[EnforcementVerdict], bool]] = None,
              actor: str = "agent", reason: str = "") -> EnforcementOutcome:
        verdict = evaluate(action, rules, context=context, matcher=matcher)
        message = render_verdict(verdict)

        if not verdict.blocked:
            # advisory-only / clean pass → proceed (flags are carried on the verdict).
            return EnforcementOutcome(True, False, verdict, message)

        if verdict.overridable:
            # SOFT block — an explicit override is allowed and LEDGERED; without one, it blocks.
            fired = verdict.blocking
            if override is not None and override(verdict):
                self._record(RULE_OVERRIDE_KIND, fired, actor=actor, decision="override",
                             reason=reason)
                return EnforcementOutcome(True, True, verdict, message)
            self._record(RULE_BLOCK_KIND, fired, actor=actor, decision="blocked", reason=reason)
            return EnforcementOutcome(False, False, verdict, message)

        # HARD / fail-closed — NO runtime override (P2). Record the block; never an override.
        self._record(RULE_BLOCK_KIND, verdict.blocking, actor=actor, decision="blocked",
                     reason=reason)
        return EnforcementOutcome(False, False, verdict, message)
