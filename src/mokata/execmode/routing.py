"""E4 — per-task model routing.

Pick the cheapest capable model for a task and escalate to a stronger one on a BLOCKED
signal. The model set is a PLUGGABLE policy — no hard dependency on any specific model
list (the defaults are generic capability tiers you override). Cost is computed through
the F1 TokenTracker so there's one cost view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from ..govern import TokenTracker

BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Model:
    name: str
    tier: int                       # capability rank (higher = stronger)
    input_cost_per_1k: float
    output_cost_per_1k: float


# Generic, illustrative tiers — override with your own model set.
DEFAULT_MODELS: List[Model] = [
    Model("fast", 1, 0.001, 0.004),
    Model("balanced", 2, 0.003, 0.015),
    Model("deep", 3, 0.015, 0.075),
]


def model_cost(model: Model, input_tokens: int, output_tokens: int) -> float:
    """Cost via the F1 TokenTracker (one cost view, not a parallel calc)."""
    tracker = TokenTracker(input_cost_per_1k=model.input_cost_per_1k,
                           output_cost_per_1k=model.output_cost_per_1k)
    tracker.add("route", input_tokens=input_tokens, output_tokens=output_tokens)
    return tracker.cost()


@dataclass
class RoutingDecision:
    task_id: str
    model: str
    tier: int
    escalated: bool
    reason: str


@dataclass
class RoutingOutcome:
    decisions: List[RoutingDecision]
    final_model: str
    resolved: bool


class ModelRouter:
    def __init__(self, models: Optional[List[Model]] = None) -> None:
        self.models = sorted(models or DEFAULT_MODELS, key=lambda m: m.tier)

    def cheapest(self, min_tier: int = 1) -> Model:
        candidates = [m for m in self.models if m.tier >= min_tier]
        if not candidates:
            raise ValueError(f"no model with tier >= {min_tier}")
        return min(candidates, key=lambda m: (m.tier, m.input_cost_per_1k))

    def escalate(self, current: Model) -> Optional[Model]:
        higher = [m for m in self.models if m.tier > current.tier]
        return min(higher, key=lambda m: m.tier) if higher else None

    def route(self, task_id: str = "task", min_tier: int = 1) -> RoutingDecision:
        m = self.cheapest(min_tier)
        return RoutingDecision(task_id, m.name, m.tier, escalated=False,
                               reason="cheapest capable model")

    def run_with_escalation(self, attempt: Callable[[Model], str],
                            task_id: str = "task", min_tier: int = 1) -> RoutingOutcome:
        """Try the cheapest model; on a BLOCKED verdict, escalate to the next stronger
        model, until resolved or no stronger model remains."""
        current = self.cheapest(min_tier)
        decisions: List[RoutingDecision] = []
        step = 0
        resolved = False
        while True:
            verdict = attempt(current)
            decisions.append(RoutingDecision(task_id, current.name, current.tier,
                                             escalated=step > 0, reason=verdict))
            if verdict != BLOCKED:
                resolved = True
                break
            nxt = self.escalate(current)
            if nxt is None:
                break
            current = nxt
            step += 1
        return RoutingOutcome(decisions=decisions, final_model=current.name,
                              resolved=resolved)
