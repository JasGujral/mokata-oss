"""Token/cost estimate surfaced BEFORE a parallel run (via F1 TokenTracker).

A conservative upper-bound: input is estimated from each task's context+description, and
output is scaled up (output_factor) so the estimate bounds the real spend. The orchestrator
shows estimate vs actual and stays under the token budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..bootstrap import estimate_tokens
from ..govern import TokenTracker

DEFAULT_OUTPUT_FACTOR = 2


@dataclass
class RunEstimate:
    tasks: int
    est_input_tokens: int
    est_output_tokens: int
    est_cost: float
    mode: str

    def report(self) -> str:
        return (f"estimate [{self.mode}]: {self.tasks} task(s), "
                f"~{self.est_input_tokens} in / ~{self.est_output_tokens} out, "
                f"~${self.est_cost:.4f}")


def estimate_run(tasks: List, choice, tracker: Optional[TokenTracker] = None,
                 output_factor: int = DEFAULT_OUTPUT_FACTOR) -> RunEstimate:
    tracker = tracker or TokenTracker()
    est_in = sum(estimate_tokens(f"{t.context}\n{t.description}") for t in tasks)
    est_out = est_in * output_factor
    cost = (est_in / 1000 * tracker.input_cost_per_1k
            + est_out / 1000 * tracker.output_cost_per_1k)
    return RunEstimate(tasks=len(tasks), est_input_tokens=est_in,
                       est_output_tokens=est_out, est_cost=cost, mode=choice.mode)
