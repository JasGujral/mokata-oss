"""E3 — two-stage review for the subagent path.

Stage 1 checks spec-compliance (did the task do exactly what was asked?); stage 2 checks
code-quality. The reviewer is injectable (the harness/LLM fulfils it); the default is a
deterministic structural check so the gate is real without a model. Clean-room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

STAGES = ("spec-compliance", "code-quality")


@dataclass
class ReviewStage:
    name: str
    passed: bool
    notes: str = ""


@dataclass
class ReviewResult:
    stages: List[ReviewStage] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.stages) and all(s.passed for s in self.stages)

    def summary(self) -> str:
        return "; ".join(f"{s.name}: {'pass' if s.passed else 'FAIL'}"
                         for s in self.stages)


def _default_reviewer(stage: str, task: Any, result: Any) -> Tuple[bool, str]:
    if stage == "spec-compliance":
        return (bool(result.ok and result.output),
                "produced output for the task" if result.output else "no output")
    return (bool(result.output), "non-empty result")


def two_stage_review(task: Any, result: Any, spec: Any = None,
                     reviewer: Optional[Callable[[str, Any, Any], Tuple[bool, str]]] = None,
                     *, layer: Any = None, change: Any = None,
                     decisions: Any = None, budget: Any = None
                     ) -> ReviewResult:
    """Two-stage review. GR.S2(m): when a real code graph + a change's touch-set are supplied,
    review becomes a `uses_graph` CONSUMER — a THIN verification slice adds bounded findings
    (callers-of-changed-symbols/guards to pass 2; the DORMANT declared-reach check to pass 1).
    With no graph (the default), the result is byte-identical to today's review."""
    review = reviewer or _default_reviewer
    stages: List[ReviewStage] = []
    for name in STAGES:
        passed, notes = review(name, task, result)
        stages.append(ReviewStage(name=name, passed=passed, notes=notes))

    if layer is not None and change is not None:
        from .review_graph import graph_verify
        gv = graph_verify(layer, change, decisions=decisions, budget=budget)
        if not gv.degraded and gv.findings:
            by_stage = {1: stages[0], 2: stages[1]}   # pass 1 -> spec-compliance, 2 -> quality
            for f in gv.findings:
                st = by_stage.get(f.pass_no, stages[-1])
                st.notes = (st.notes + " | " if st.notes else "") + f"[graph] {f.message}"
    return ReviewResult(stages=stages)
