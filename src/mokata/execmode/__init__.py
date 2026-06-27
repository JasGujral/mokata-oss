"""mokata execution modes (E8/E2/E3).

At the start of every pipeline run, the selector asks which mode to use: the sequential
gated flow (default, lowest-cost) or parallel subagents — with fresh-subagent isolation
(E2 + two-stage review E3) and/or concurrent fan-out, both selectable. Parallel runs
surface a cost estimate (F1), stay under the audit ledger (I3) + token budget, log every
subagent decision, and degrade to sequential when subagents are unavailable.
"""

from .estimate import RunEstimate, estimate_run
from .orchestrator import RunResult, run_tasks
from .review import ReviewResult, ReviewStage, two_stage_review
from .routing import (
    BLOCKED,
    DEFAULT_MODELS,
    Model,
    ModelRouter,
    RoutingDecision,
    RoutingOutcome,
    model_cost,
)
from .selector import (
    ASK,
    EXECUTION_SETTINGS_KEY,
    PARALLEL,
    SEQUENTIAL,
    ExecutionChoice,
    resolve_execution_choice,
    saved_execution_default,
    select_execution_mode,
)
from .tasks import SubagentRunner, SubagentUnavailable, Task, TaskResult

__all__ = [
    "SEQUENTIAL",
    "PARALLEL",
    "ASK",
    "EXECUTION_SETTINGS_KEY",
    "ExecutionChoice",
    "select_execution_mode",
    "resolve_execution_choice",
    "saved_execution_default",
    "Task",
    "TaskResult",
    "SubagentRunner",
    "SubagentUnavailable",
    "ReviewStage",
    "ReviewResult",
    "two_stage_review",
    "RunEstimate",
    "estimate_run",
    "RunResult",
    "run_tasks",
    # E4 — model routing
    "Model",
    "ModelRouter",
    "RoutingDecision",
    "RoutingOutcome",
    "DEFAULT_MODELS",
    "BLOCKED",
    "model_cost",
]
