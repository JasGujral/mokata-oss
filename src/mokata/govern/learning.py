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
