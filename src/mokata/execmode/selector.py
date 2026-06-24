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

    if ledger is not None:
        ledger.record("exec_mode", mode=choice.mode, isolation=choice.isolation,
                      fanout=choice.fanout)
    return choice
