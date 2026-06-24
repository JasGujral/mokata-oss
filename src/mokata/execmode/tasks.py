"""E2 — task & subagent-runner contracts.

mokata governs the *plan* — what each task is, its isolated context, and the handback —
while the harness actually runs the subagent. `SubagentRunner` is that boundary; it
raises `SubagentUnavailable` when no harness can run subagents, which the orchestrator
turns into a graceful degrade to the sequential flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


class SubagentUnavailable(Exception):
    """No subagent execution is available — degrade to sequential flow."""


@dataclass
class Task:
    id: str
    description: str
    context: str = ""


@dataclass
class TaskResult:
    task_id: str
    ok: bool
    summary: str                       # the handback — a summary, not raw context
    output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    isolated: bool = False
    seen_context: str = ""             # what the runner actually received (isolation proof)
    review: Optional[Any] = None       # a ReviewResult, when E3 ran


class SubagentRunner(Protocol):
    def run(self, task: Task) -> TaskResult: ...
