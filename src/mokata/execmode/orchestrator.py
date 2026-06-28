"""The run orchestrator: ties the selector, estimate, isolation, fan-out, two-stage
review, audit logging, and degradation together.

Sequential is the default/floor and needs no subagent runner. Parallel is opt-in: it
surfaces a cost estimate (F1), runs under the audit ledger (I3) and token governance,
logs every subagent decision, and degrades to sequential when subagents are unavailable
— never hard-failing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..bootstrap import estimate_tokens
from ..govern import TokenTracker
from .estimate import RunEstimate, estimate_run
from .review import two_stage_review
from .selector import ExecutionChoice
from .tasks import SubagentUnavailable, Task, TaskResult


@dataclass
class RunResult:
    choice: ExecutionChoice
    estimate: RunEstimate
    results: List[TaskResult] = field(default_factory=list)
    degraded: bool = False
    actual_input: int = 0
    actual_output: int = 0
    actual_cost: float = 0.0
    budget: Optional[int] = None

    @property
    def actual_total(self) -> int:
        return self.actual_input + self.actual_output

    @property
    def within_budget(self) -> bool:
        return self.budget is None or self.actual_total <= self.budget

    @property
    def all_reviews_passed(self) -> bool:
        reviewed = [r for r in self.results if r.review is not None]
        return all(r.review.passed for r in reviewed)


def _run_sequential(tasks: List[Task], ledger: Any,
                    tracker: TokenTracker) -> List[TaskResult]:
    """The default gated flow — mokata processes tasks in-loop, no subagent needed.
    Context is shared (not isolated); this is the lowest-cost path."""
    results: List[TaskResult] = []
    shared: List[str] = []
    for t in tasks:
        ctx = ("\n".join(shared) + ("\n" if shared else "")) + t.context
        out = f"processed:{t.id}"
        res = TaskResult(
            task_id=t.id, ok=True, summary=f"sequential {t.id}", output=out,
            input_tokens=estimate_tokens(ctx + t.description),
            output_tokens=estimate_tokens(out), isolated=False, seen_context=ctx,
        )
        tracker.add(f"seq:{t.id}", input_tokens=res.input_tokens,
                    output_tokens=res.output_tokens)
        if ledger is not None:
            ledger.record("sequential", task=t.id, ok=True)
        shared.append(out)
        results.append(res)
    return results


def _invoke_runner(runner, task: Task, model_name: str):
    """Run the task, passing the routed model name to a model-AWARE runner (E4) while
    staying compatible with the existing model-agnostic `run(task)` contract."""
    import inspect
    try:
        if "model" in inspect.signature(runner.run).parameters:
            return runner.run(task, model=model_name)
    except (TypeError, ValueError):
        pass
    return runner.run(task)


def _run_routed(task: Task, iso_task: Task, runner, router, ledger) -> TaskResult:
    """E4 — route the task to the cheapest capable model and escalate on BLOCKED. mokata
    governs the routing PLAN (which model, when to escalate) + audits it; the harness/runner
    executes. Returns the resolved attempt's TaskResult."""
    from .routing import BLOCKED
    box: dict = {}

    def attempt(model) -> str:
        res = _invoke_runner(runner, iso_task, model.name)
        box["res"] = res
        return BLOCKED if not res.ok else (res.summary or "ok")

    outcome = router.run_with_escalation(attempt, task_id=task.id)
    if ledger is not None:
        ledger.record("model_route", task=task.id, final_model=outcome.final_model,
                      escalated=any(d.escalated for d in outcome.decisions),
                      resolved=outcome.resolved, attempts=len(outcome.decisions))
    return box["res"]


def _run_one(task: Task, choice: ExecutionChoice, runner, reviewer, ledger,
             tracker: TokenTracker, handback_cap=None, density=None,
             router=None) -> TaskResult:
    # Fresh-subagent isolation (E2): the subagent receives ONLY this task's context.
    iso_task = Task(id=task.id, description=task.description, context=task.context)
    if router is None:
        res = runner.run(iso_task)                   # today's path — no routing
    else:
        res = _run_routed(task, iso_task, runner, router, ledger)   # E4 (opt-in)
    res.isolated = True
    if handback_cap is not None:
        # F3: the parent receives a capped SUMMARY, not the sub-agent's raw context.
        from ..govern import cap_summary
        res.summary = cap_summary(res.output or res.summary, handback_cap).summary
    if density is not None:
        # F4: density-compress the handback the parent absorbs — collapses blank-line
        # runs / duplicate lines / trailing whitespace, never content. OFF by default;
        # an OutputDensity(False) (or None) is a pass-through no-op (degrade-clean).
        res.summary = density.compress(res.summary)
    if choice.isolation:
        res.review = two_stage_review(task, res, reviewer=reviewer)   # E3
    tracker.add(f"task:{task.id}", input_tokens=res.input_tokens,
                output_tokens=res.output_tokens)
    if ledger is not None:
        ledger.record("subagent", task=task.id, ok=res.ok, isolated=res.isolated,
                      review_passed=(res.review.passed if res.review else None))
    return res


def _run_parallel(tasks: List[Task], choice: ExecutionChoice, runner, reviewer,
                  ledger, tracker: TokenTracker, handback_cap=None,
                  density=None, router=None) -> List[TaskResult]:
    if choice.fanout and len(tasks) > 1:
        out: dict = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futures = {ex.submit(_run_one, t, choice, runner, reviewer, ledger,
                                 tracker, handback_cap, density, router): t
                       for t in tasks}
            for fut in as_completed(futures):
                out[futures[fut].id] = fut.result()   # re-raises SubagentUnavailable
        return [out[t.id] for t in tasks]
    return [_run_one(t, choice, runner, reviewer, ledger, tracker, handback_cap,
                     density, router) for t in tasks]


def run_tasks(tasks: List[Task], choice: ExecutionChoice, runner=None,
              reviewer=None, ledger: Any = None, tracker: Optional[TokenTracker] = None,
              budget: Optional[int] = None, handback_cap: Optional[int] = None,
              density: Any = None, router: Any = None) -> RunResult:
    tracker = tracker or TokenTracker()
    estimate = estimate_run(tasks, choice, tracker)
    if ledger is not None:
        ledger.record("exec_estimate", mode=choice.mode, tasks=len(tasks),
                      est_in=estimate.est_input_tokens,
                      est_out=estimate.est_output_tokens,
                      est_cost=round(estimate.est_cost, 6))

    degraded = False
    if choice.is_parallel and runner is not None:
        try:
            results = _run_parallel(tasks, choice, runner, reviewer, ledger, tracker,
                                    handback_cap, density, router)
        except SubagentUnavailable as exc:
            degraded = True
            tracker.entries.clear()                  # discard partial accounting
            if ledger is not None:
                ledger.record("exec_degrade", reason=str(exc))
            results = _run_sequential(tasks, ledger, tracker)
    else:
        if choice.is_parallel and runner is None:
            degraded = True
            if ledger is not None:
                ledger.record("exec_degrade",
                              reason="no subagent runner available")
        results = _run_sequential(tasks, ledger, tracker)

    return RunResult(
        choice=choice, estimate=estimate, results=results, degraded=degraded,
        actual_input=tracker.total_input, actual_output=tracker.total_output,
        actual_cost=tracker.cost(), budget=budget,
    )
