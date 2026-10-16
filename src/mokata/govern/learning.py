"""G5 — rules-learning + reinforcement.

When a pattern recurs past a threshold, mokata AUTO-PROPOSES a rule promotion — it
proposes, it never auto-adds. Adding the rule is a separate, human-gated step (default
declines). Proposals and decisions are logged to the audit ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

# Ledger entry decisions that mark a write/action a human pushed back on.
_DECLINED = ("declined", "blocked", "rejected", "deny", "denied", "abort", "aborted")


@dataclass
class RulePromotion:
    pattern_key: str
    proposed_rule: str
    occurrences: int
    rationale: str


class RulesLearner:
    def __init__(self, threshold: int = 3, ledger: Any = None) -> None:
        self.threshold = threshold
        self._counts: Dict[str, int] = {}
        self._proposed: Set[str] = set()
        self.promotions: List[RulePromotion] = []
        self._ledger = ledger

    def observe(self, pattern_key: str,
                rule_text: Optional[str] = None) -> Optional[RulePromotion]:
        """Record one occurrence of a pattern. Returns a promotion proposal the first
        time the pattern reaches the threshold (proposes only — never adds a rule)."""
        self._counts[pattern_key] = self._counts.get(pattern_key, 0) + 1
        n = self._counts[pattern_key]
        if n >= self.threshold and pattern_key not in self._proposed:
            self._proposed.add(pattern_key)
            promo = RulePromotion(
                pattern_key=pattern_key,
                proposed_rule=rule_text or f"Recurring pattern '{pattern_key}' — "
                                           f"consider promoting it to a rule.",
                occurrences=n,
                rationale=f"observed {n} times (>= threshold {self.threshold})",
            )
            self.promotions.append(promo)
            if self._ledger is not None:
                self._ledger.record("rule_promotion_proposed", pattern=pattern_key,
                                    occurrences=n, rule=promo.proposed_rule)
            return promo
        return None

    def apply_promotion(self, promotion: RulePromotion, decision: str,
                        confirm: Optional[Callable[[str], bool]] = None,
                        assume_yes: bool = False,
                        sink: Optional[Callable[[str], None]] = None) -> bool:
        """Human-gated application of a proposal. Returns True only if the rule was
        actually added. Default (no approval) adds nothing."""
        led = self._ledger
        if decision in ("reject", "defer"):
            if led is not None:
                led.record("rule_promotion_decision", pattern=promotion.pattern_key,
                           decision=decision, added=False)
            return False
        if not assume_yes:
            gate = confirm or (lambda _text: False)   # default: decline (never auto-add)
            if not gate(promotion.proposed_rule):
                if led is not None:
                    led.record("rule_promotion_decision",
                               pattern=promotion.pattern_key, decision="declined",
                               added=False)
                return False
        if sink is not None:
            sink(promotion.proposed_rule)
        if led is not None:
            led.record("rule_promotion_decision", pattern=promotion.pattern_key,
                       decision="approve", added=True)
        return True


def _correction_key(entry: Dict[str, Any]) -> Optional[str]:
    """The recurring-correction pattern key for one ledger entry, or None if it isn't a
    correction worth learning from. Keyed on a stable field so repeats coalesce."""
    kind = entry.get("kind")
    if kind == "write_gate":
        if str(entry.get("decision", "")).lower() in _DECLINED:
            return f"write_gate:{entry.get('target', '')}"
        return None
    if kind == "revert":
        return f"revert:{entry.get('target', '')}"
    if kind == "spec_conflict":
        return f"spec_conflict:{entry.get('phase', '')}"
    return None


def learn_from_ledger(ledger: Any, *, threshold: int = 3,
                      learner: Optional["RulesLearner"] = None) -> List[RulePromotion]:
    """Replay the audit ledger's recurring CORRECTIONS (declined writes, reverts, spec
    conflicts) through a `RulesLearner` and return the promotion PROPOSALS — proposal-only,
    never writing a rule. A pure read over the ledger (the default learner carries no
    ledger, so nothing is appended); promoting a proposal stays a separate, human-gated
    step (`apply_promotion`)."""
    learner = learner or RulesLearner(threshold=threshold)
    for entry in ledger.entries():
        key = _correction_key(entry)
        if key is None:
            continue
        learner.observe(key, rule_text=f"Recurring correction '{key}' — consider "
                                       f"promoting a guardrail rule.")
    return list(learner.promotions)


# =====================================================================================================
# 0.0.23 D3 — gate promotion on evidence: the SAME threshold → proposal → human gate as a rule.
#
# An ADVISORY gate (`Gate.enforcement == "advisory"`, decided by `enforce.decide_gate`) ledgers every
# evaluation as `gate_advisory` (decision "pass" or "would-block"). A CLEAN advisory run is one that
# would NOT have blocked. Once a gate's trailing streak of clean runs since its last enforcement change
# reaches the threshold, the `RulesLearner` threshold machinery PROPOSES promoting it — proposal-only.
# Promotion itself is `apply_gate_promotion`: `RulesLearner.apply_promotion`'s human gate (default
# declines), then an `enforcement_change` (gkind "gate") on the ledger — the record
# `enforce.effective_gate_enforcement` raises the gate's level from. A would-block resets the streak:
# a gate that would have walled legitimate work is not evidence it is ready to be a wall.
#
# PROMOTION TARGET — ruled 2026-10-16 (doc 84 GATE-HAS-NO-ENFORCEMENT-LADDER): an earned promotion
# goes advisory → SOFT. A promoted gate blocks, but a human can override and the override is ledgered
# (`rule_override`), so a gate that misfires after promotion never walls work with no way through.
# HARD stays a level a gate is DECLARED at (every backed pipeline gate is); it is never the automatic
# landing of a learned promotion.
# =====================================================================================================

#: Clean advisory runs a gate must string together before a promotion is proposed.
DEFAULT_GATE_PROMOTION_THRESHOLD = 5
#: Where an earned promotion lands (ruled 2026-10-16, see the block above).
GATE_PROMOTION_TARGET = "soft"
#: The learner's pattern-key prefix for a gate, so a gate never coalesces with a correction key.
GATE_PATTERN_PREFIX = "gate:"


@dataclass
class GateAdvisoryStats:
    """One gate's advisory record, for `mokata govern`-style surfacing (the doc-84 risk: advisory
    gates that are never promoted accumulate, and one that would-block constantly is a FALSE-POSITIVE
    problem to look at, not a gate to keep)."""

    gate_id: str
    level: str
    evaluations: int
    would_block: int
    clean_streak: int


def _gate_entries_since_change(entries: List[Dict[str, Any]], gate_id: str) -> List[Dict[str, Any]]:
    from .enforce import ENFORCEMENT_CHANGE_KIND, GATE_ADVISORY_KIND, GATE_GKIND
    out: List[Dict[str, Any]] = []
    for e in entries:
        if (e.get("kind") == ENFORCEMENT_CHANGE_KIND and e.get("gkind") == GATE_GKIND
                and e.get("item_id") == gate_id):
            out = []                       # a level change starts the evidence over
        elif e.get("kind") == GATE_ADVISORY_KIND and e.get("gate") == gate_id:
            out.append(e)
    return out


def _clean_streak(evals: List[Dict[str, Any]]) -> int:
    n = 0
    for e in reversed(evals):
        if e.get("would_block"):
            break
        n += 1
    return n


def advisory_gate_report(ledger: Any, gates: Any) -> List[GateAdvisoryStats]:
    """Per-gate advisory evidence for every gate whose level IN FORCE is advisory. Read-only."""
    from .enforce import effective_gate_enforcement
    entries = ledger.entries() if ledger is not None else []
    rows: List[GateAdvisoryStats] = []
    for gate in gates:
        level = effective_gate_enforcement(gate, ledger)
        if level != "advisory":
            continue
        evals = _gate_entries_since_change(entries, gate.id)
        rows.append(GateAdvisoryStats(
            gate_id=gate.id, level=level, evaluations=len(evals),
            would_block=sum(1 for e in evals if e.get("would_block")),
            clean_streak=_clean_streak(evals)))
    return rows


def gate_promotion_proposals(ledger: Any, gates: Any, *,
                             threshold: int = DEFAULT_GATE_PROMOTION_THRESHOLD,
                             learner: Optional[RulesLearner] = None) -> List[RulePromotion]:
    """PROPOSE promoting each advisory gate whose clean streak has reached `threshold` — through the
    `RulesLearner` threshold machinery, proposal-only. A pure read by default (the default learner
    carries no ledger, mirroring `learn_from_ledger`); pass a learner WITH a ledger to record the
    proposals (`rule_promotion_proposed`). Never changes a gate's level."""
    learner = learner or RulesLearner(threshold=threshold)
    for row in advisory_gate_report(ledger, gates):
        key = GATE_PATTERN_PREFIX + row.gate_id
        text = (f"Promote gate '{row.gate_id}' advisory → {GATE_PROMOTION_TARGET}: "
                f"{row.clean_streak} consecutive clean advisory run(s) would not have blocked.")
        for _ in range(row.clean_streak):
            if learner.observe(key, rule_text=text) is not None:
                break
    return [p for p in learner.promotions if p.pattern_key.startswith(GATE_PATTERN_PREFIX)]


def apply_gate_promotion(promotion: RulePromotion, gate: Any, ledger: Any, decision: str, *,
                         confirm: Optional[Callable[[str], bool]] = None,
                         assume_yes: bool = False, actor: str = "user") -> bool:
    """Human-gated application of a gate promotion proposal — `RulesLearner.apply_promotion`'s own
    gate (default DECLINES; reject/defer add nothing), then, only on approval, an
    `enforcement_change` (gkind "gate", old → GATE_PROMOTION_TARGET, the approving entry's seq) on the
    ledger. Returns True only if the gate was promoted. A ledger is required: the ledger IS where a
    promotion lives, so a promotion with nowhere to be recorded is refused rather than lost."""
    from .enforce import GATE_GKIND, effective_gate_enforcement
    if ledger is None:
        return False
    if promotion.pattern_key != GATE_PATTERN_PREFIX + gate.id:
        raise ValueError(f"promotion {promotion.pattern_key!r} is not for gate {gate.id!r}")
    old = effective_gate_enforcement(gate, ledger)
    if old == GATE_PROMOTION_TARGET:
        return False
    if not RulesLearner(ledger=ledger).apply_promotion(promotion, decision, confirm=confirm,
                                                      assume_yes=assume_yes):
        return False
    approval_seq = None
    for e in reversed(ledger.entries()):
        if e.get("kind") == "rule_promotion_decision" and e.get("pattern") == promotion.pattern_key:
            approval_seq = e.get("seq")
            break
    # The kind is spelled as a literal (== enforce.ENFORCEMENT_CHANGE_KIND, pinned by test_d3_*) so
    # the ledger-kind sweep (`tests/_ledger_kinds.py`) can resolve it — the same literal the rule
    # path writes in `memory/store.py`.
    ledger.record("enforcement_change", subject=gate.id, item_id=gate.id, gkind=GATE_GKIND,
                  direction="promote", old=old, new=GATE_PROMOTION_TARGET, actor=actor,
                  approval_seq=approval_seq)
    return True
