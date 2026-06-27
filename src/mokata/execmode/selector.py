"""E8 — execution-mode selector.

At the start of EVERY pipeline run, ask which execution mode to use: the sequential
gated flow (DEFAULT, lowest-cost) or parallel subagents. If parallel, the user further
chooses fresh-subagent isolation (E2/E3) and/or concurrent fan-out — both selectable.
With no choice, default to sequential. The choice is recorded in the audit ledger.

`ask(question, default)` is injected so the harness/CLI/tests supply answers; when no
asker is given (non-interactive), the default is used — sequential.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

SEQUENTIAL = "sequential"
PARALLEL = "parallel"
ASK = "ask"

# settings.execution.default = sequential | parallel | ask  (default "ask").
EXECUTION_SETTINGS_KEY = "execution"
EXECUTION_DEFAULTS = (ASK, SEQUENTIAL, PARALLEL)


@dataclass
class ExecutionChoice:
    mode: str = SEQUENTIAL
    isolation: bool = False
    fanout: bool = False

    @property
    def is_parallel(self) -> bool:
        return self.mode == PARALLEL

    def label(self) -> str:
        if not self.is_parallel:
            return "sequential gated flow (default)"
        return (f"parallel subagents (isolation={self.isolation}, "
                f"fan-out={self.fanout})")


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes", "true", "1")


def select_execution_mode(ask: Optional[Callable[[str, str], str]] = None,
                          default_mode: str = SEQUENTIAL,
                          ledger: Any = None) -> ExecutionChoice:
    if ask is None:
        choice = ExecutionChoice(mode=default_mode)
    else:
        answer = (ask("Execution mode — sequential or parallel?", default_mode)
                  or default_mode).strip().lower()
        if answer.startswith("p"):
            isolation = _yes(ask("Fresh-subagent isolation (clean context + two-stage "
                                 "review)? [y/n]", "y"))
            fanout = _yes(ask("Concurrent fan-out (run tasks at once)? [y/n]", "n"))
            if not isolation and not fanout:
                isolation = True   # parallel implies at least isolation
            choice = ExecutionChoice(PARALLEL, isolation=isolation, fanout=fanout)
        else:
            choice = ExecutionChoice(SEQUENTIAL)

    return _record(choice, ledger)


def _record(choice: ExecutionChoice, ledger: Any) -> ExecutionChoice:
    if ledger is not None:
        ledger.record("exec_mode", mode=choice.mode, isolation=choice.isolation,
                      fanout=choice.fanout)
    return choice


def saved_execution_default(manifest: Any) -> str:
    """The saved per-run preference (Stage 25): settings.execution.default. Defaults to
    'ask' — never re-prompt friction, but never assume parallel either."""
    if manifest is None:
        return ASK
    try:
        ex = manifest.setting(EXECUTION_SETTINGS_KEY, {}) or {}
    except AttributeError:
        return ASK
    val = ex.get("default", ASK) if isinstance(ex, dict) else ASK
    return val if val in EXECUTION_DEFAULTS else ASK


def resolve_execution_choice(
    manifest: Any = None,
    ask: Optional[Callable[[str, str], str]] = None,
    tasks: Optional[list] = None,
    tracker: Any = None,
    ledger: Any = None,
    out: Optional[Callable[[str], None]] = None,
    subagents_available: bool = True,
) -> ExecutionChoice:
    """Decide a run's execution mode at the START of an implementation (Stage 25 Part A).

    The standing rule: every implementation asks parallel-vs-sequential FIRST and never
    fans out without the user picking — but kept light (P14): asked ONCE per run, with a
    sensible default (sequential = lowest cost), and a saved preference
    (settings.execution.default = sequential|parallel|ask, default 'ask') so power users
    aren't re-prompted. When 'ask', the one-line choice is presented with the parallel
    cost estimate (cost-aware). When the harness lacks subagents, force sequential with a
    clear note (degrade-safe — already the engine behavior downstream)."""
    emit = out or (lambda *_a: None)

    if not subagents_available:
        emit("execution: this harness has no subagents — running the sequential flow.")
        return _record(ExecutionChoice(SEQUENTIAL), ledger)

    default = saved_execution_default(manifest)
    if default == SEQUENTIAL:
        emit("execution: saved preference 'sequential' (no prompt).")
        return _record(ExecutionChoice(SEQUENTIAL), ledger)
    if default == PARALLEL:
        emit("execution: saved preference 'parallel' — fresh-subagent isolation.")
        return _record(ExecutionChoice(PARALLEL, isolation=True), ledger)

    # 'ask': surface the parallel cost estimate first, then ask once (default sequential).
    if tasks:
        from .estimate import estimate_run
        est = estimate_run(tasks, ExecutionChoice(PARALLEL), tracker)
        emit("if you choose parallel — " + est.report())
    return select_execution_mode(ask=ask, default_mode=SEQUENTIAL, ledger=ledger)
