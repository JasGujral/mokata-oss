"""F1 — token / cost tracker (mokata's own, in-loop).

A conservative, dependency-free estimator (reuses the bootstrap chars/4 rule). Costs are
illustrative per-1k-token rates the caller can override; this is for in-loop governance
("are we spending more than the work is worth"), not billing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..bootstrap import estimate_tokens

# Illustrative default rates (USD per 1k tokens); override for your model.
DEFAULT_INPUT_COST = 0.003
DEFAULT_OUTPUT_COST = 0.015


@dataclass
class UsageEntry:
    label: str
    input_tokens: int
    output_tokens: int


@dataclass
class TokenTracker:
    input_cost_per_1k: float = DEFAULT_INPUT_COST
    output_cost_per_1k: float = DEFAULT_OUTPUT_COST
    entries: List[UsageEntry] = field(default_factory=list)

    def add(self, label: str, input_text: str = "", output_text: str = "",
            input_tokens: Optional[int] = None,
            output_tokens: Optional[int] = None) -> UsageEntry:
        it = input_tokens if input_tokens is not None else estimate_tokens(input_text)
        ot = output_tokens if output_tokens is not None else estimate_tokens(output_text)
        entry = UsageEntry(label=label, input_tokens=it, output_tokens=ot)
        self.entries.append(entry)
        return entry

    @property
    def total_input(self) -> int:
        return sum(e.input_tokens for e in self.entries)

    @property
    def total_output(self) -> int:
        return sum(e.output_tokens for e in self.entries)

    def cost(self) -> float:
        return (self.total_input / 1000 * self.input_cost_per_1k
                + self.total_output / 1000 * self.output_cost_per_1k)

    def report(self) -> str:
        return (f"tokens: {self.total_input} in / {self.total_output} out "
                f"across {len(self.entries)} call(s) — est. cost ${self.cost():.4f}")
