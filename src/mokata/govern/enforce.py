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


# =====================================================================================================
# 0.0.23 D3 — GATE-HAS-NO-ENFORCEMENT-LADDER: a `Gate` decided by THIS Sentinel, not a parallel one.
#
# `Gate` (`skills.py`) now carries the same advisory | soft | hard binding a rule carries. A gate's
# verdict is decided here by adapting the gate into a rule-shaped item and running the EXISTING
# `evaluate` / `EnforcementGate.check` over it — so most-restrictive-wins, fail-closed-on-doubt, the
# soft override and the `rule_block` / `rule_override` ledger kinds are the rule path's own, byte for
# byte. Gate entries are told apart in the ledger by `scope_level == "gate"` (no rule has that scope).
#
#   advisory -> evaluated; ledgers `gate_advisory` (decision "would-block" or "pass"); never blocks
#   soft     -> a failed check blocks; an explicit override clears it and is ledgered
#   hard     -> a failed check refuses; no runtime override
#
# The level IN FORCE is the declared level, RAISED (never lowered) by an approved promotion on the
# ledger (`enforcement_change` with gkind "gate" — written only by `learning.apply_gate_promotion`,
# human-gated). The declared level is a floor: a ledger can promote a gate, never demote it, so a gate
# that is hard in code is hard no matter what the ledger says (most-restrictive-wins).
# =====================================================================================================

#: The scope stamped on a gate's ledger entries — no governance rule carries it, so `mokata audit`
#: and the learner can tell a gate's block from a rule's without a new ledger kind.
GATE_SCOPE = "gate"
#: An advisory gate's evaluation — what it WOULD have blocked, or that it would have passed. The
#: clean-run evidence a promotion proposal is earned from (`learning.gate_promotion_proposals`).
GATE_ADVISORY_KIND = "gate_advisory"
#: The governance kind stamped on a gate's `enforcement_change` (a rule's carries "rule").
GATE_GKIND = "gate"
#: The ledger kind a promotion is recorded under — the SAME kind rule promotion uses.
ENFORCEMENT_CHANGE_KIND = "enforcement_change"


@dataclass(frozen=True)
class _GateAsRule:
    """A `Gate` in the shape `evaluate` reads. `kind` is GUARDRAIL, not RULE: both normalize to the
    governance `rule` kind (so it is in scope), but a guardrail's kind-default enforcement is HARD —
    so if the level were ever unreadable, `effective_enforcement` itself leans to the wall."""

    id: str
    subject: str
    enforcement: str
    value: str
    kind: str = "guardrail"
    scope_level: str = GATE_SCOPE
    scope_id: str = ""


@dataclass
class GateDecision:
    """One gate's verdict. `allowed` is what the caller acts on; `check_passed` is the raw check.
    `would_block` is the advisory case: the check failed, the gate is advisory, nothing was blocked —
    and the ledger says so."""

    gate_id: str
    enforcement: str             # the level IN FORCE (declared, raised by approved promotions)
    declared: str                # the level the code declares
    check_passed: bool
    allowed: bool
    overridden: bool
    would_block: bool
    message: str
    outcome: Optional[EnforcementOutcome] = None
    evidence: Any = None         # 0.0.23 D8 — `govern.evidence.GateEvidence` for this outcome

    @property
    def blocked(self) -> bool:
        return not self.allowed


def promoted_gate_level(gate_id: str, ledger: Any) -> Optional[str]:
    """The most recent APPROVED enforcement level the ledger records for `gate_id` (an
    `enforcement_change` with gkind "gate"), or None. Read-only; an unreadable ledger reads None."""
    if ledger is None:
        return None
    try:
        entries = ledger.entries()
    except Exception:
        return None
    level: Optional[str] = None
    for e in entries:
        if (e.get("kind") == ENFORCEMENT_CHANGE_KIND and e.get("gkind") == GATE_GKIND
                and e.get("item_id") == gate_id):
            level = e.get("new")
    return level


def effective_gate_enforcement(gate: Any, ledger: Any = None) -> str:
    """The level in force for `gate`: its declared `enforcement`, raised to an approved promoted
    level when the ledger holds one. Never lowered. A declared HARD gate never reads the ledger (it
    cannot be raised further) — so today's gates pay nothing for the ladder. An unrecognized
    promoted value is ignored here and the declared level stands; an unrecognized DECLARED value is
    returned as-is so `decide_gate` fails closed on it."""
    declared = getattr(gate, "enforcement", "") or ""
    if declared == HARD or declared not in ENFORCEMENT_LEVELS:
        return declared
    from ..memory.item import enforcement_rank
    promoted = promoted_gate_level(getattr(gate, "id", ""), ledger)
    if promoted in ENFORCEMENT_LEVELS and enforcement_rank(promoted) > enforcement_rank(declared):
        return promoted
    return declared


def decide_gate(gate: Any, check_passed: bool, *, reason: str = "", ledger: Any = None,
                override: Optional[Callable[[EnforcementVerdict], bool]] = None,
                actor: str = "agent", artifacts: Optional[Sequence[Any]] = None,
                tested: Optional[Sequence[Any]] = None) -> GateDecision:
    """Decide a gate's verdict from its raw check through the rule Sentinel (`EnforcementGate`).

    The check's failure is the "violation": the gate is adapted to a rule-shaped item and the action
    names it in `violates` only when the check FAILED. An undeterminable level makes the matcher
    raise, which `evaluate` buckets as FAIL-CLOSED — a non-overridable block — exactly the doubt
    path a rule takes. Advisory evaluations are ledgered as `gate_advisory`; soft/hard blocks and a
    soft override are ledgered by `EnforcementGate` itself (`rule_block` / `rule_override`, scoped
    "gate").

    0.0.23 D8 — every decision carries `evidence` (`govern.evidence.GateEvidence`): the verdict, the
    level, the typed `artifacts` and the `tested` check inventory the caller exercised. Pure — the
    caller persists it (`evidence.record_evidence`), because only the caller knows the run."""
    gate_id = getattr(gate, "id", "") or ""
    declared = getattr(gate, "enforcement", "") or ""
    level = effective_gate_enforcement(gate, ledger)
    item = _GateAsRule(id=gate_id, subject=gate_id, enforcement=level,
                       value=reason or (getattr(gate, "description", "") or ""))
    action = PendingAction(kind="gate", target=gate_id, summary=reason,
                           violates=frozenset() if check_passed else frozenset({gate_id}))

    def _match(rule: Any, act: PendingAction) -> bool:
        if getattr(rule, "enforcement", "") not in ENFORCEMENT_LEVELS:
            # Doubt about the level → `evaluate` buckets it fail-closed. Only a FAILED check needs
            # its level to be read: a passing check has nothing to treat, so it is not doubted.
            if act.violates:
                raise ValueError(f"gate {gate_id!r}: undeterminable enforcement")
            return False
        return _default_matcher(rule, act)

    outcome = EnforcementGate(ledger=ledger).check(action, [item], matcher=_match,
                                                   override=override, actor=actor, reason=reason)
    verdict = outcome.verdict
    would_block = bool(verdict.advisory) and not verdict.blocked
    if level == ADVISORY and ledger is not None and not verdict.fail_closed:
        ledger.record(GATE_ADVISORY_KIND, gate=gate_id, item_id=gate_id, subject=gate_id,
                      enforcement=ADVISORY, scope_level=GATE_SCOPE, would_block=would_block,
                      decision="would-block" if would_block else "pass", actor=actor,
                      reason=reason)
    if would_block:
        message = gate_verdict(gate_id, True,
                               f"advisory — would have blocked: {reason or 'check failed'}")
    elif not verdict.blocked:
        message = gate_verdict(gate_id, True, reason or "check passed")
    else:
        # The rule renderer's unblock line speaks of removing a RULE; a gate is cleared by
        # satisfying it, so the gate names its own unblock action (same legible format).
        fired = verdict.blocking
        if fired is not None and fired.enforcement == SOFT and fired.determinable:
            unblock = "approve an explicit override (recorded to the audit ledger) or satisfy the gate"
        else:
            unblock = "satisfy the gate — a hard gate has no runtime override"
        why = fired.condition if fired is not None else reason
        message = gate_verdict(gate_id, False, f"{fired.enforcement if fired else level} gate — "
                               f"{why or 'check failed'}", action=unblock)
    decision = GateDecision(gate_id=gate_id, enforcement=level, declared=declared,
                            check_passed=bool(check_passed), allowed=outcome.allowed,
                            overridden=outcome.overridden, would_block=would_block,
                            message=message, outcome=outcome)
    from .evidence import Artifact, GateEvidence, tested_from, verdict_of
    decision.evidence = GateEvidence(
        gate_id=gate_id, verdict=verdict_of(decision), enforcement=level,
        artifacts=[a if isinstance(a, Artifact) else Artifact.from_dict(a)
                   for a in (artifacts or ())],
        tested=tested_from(tested or ()))
    return decision
