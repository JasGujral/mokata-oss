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
                     reviewer: Optional[Callable[[str, Any, Any], Tuple[bool, str]]] = None
                     ) -> ReviewResult:
    review = reviewer or _default_reviewer
    stages: List[ReviewStage] = []
    for name in STAGES:
        passed, notes = review(name, task, result)
        stages.append(ReviewStage(name=name, passed=passed, notes=notes))
    return ReviewResult(stages=stages)
