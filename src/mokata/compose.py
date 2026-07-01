"""L5 + L6 — manual composition and context-aware suggestions.

L5: chain subtasks yourself (e.g. spec -> test, debug -> test) without the full pipeline;
each step still carries its own gate (the chain never bypasses a step's gate). Reuses the
skills registry — no parallel command system.

L6: suggest the relevant command/skill for the current context. SUGGEST only — the
functions return data; they never run anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .skills import get_skill


# --------------------------------------------------------------- L5: chaining
@dataclass
class ChainStep:
    skill: str
    gate: str


def plan_chain(skill_names: List[str]) -> List[ChainStep]:
    """Resolve a chain to its steps, each carrying its skill's gate. Raises
    SkillNotFound for an unknown skill (no silent skip).

    Plan-only: `mokata chain` ships the resolved steps + their gates for the human to run;
    there is intentionally no in-process `run_chain` executor (removed pre-0.0.5 as
    production-orphaned — each step runs through its own gated skill, not a bypass loop)."""
    return [ChainStep(name, get_skill(name).gate.id) for name in skill_names]


# ------------------------------------------------------------ L6: suggestions
@dataclass
class SuggestionContext:
    starting_fresh: bool = False
    has_spec: bool = False
    has_failing_test: bool = False
    has_implementation: bool = False
    has_diff: bool = False
    has_bug_report: bool = False
    has_stacktrace: bool = False
    has_perf_issue: bool = False


@dataclass
class Suggestion:
    skill: str
    reason: str


def suggest(ctx: SuggestionContext) -> List[Suggestion]:
    """Return relevant command/skill suggestions for the context. Never runs them."""
    out: List[Suggestion] = []
    if ctx.has_bug_report:
        out.append(Suggestion("bug", "a bug report is present — reproduce it first"))
    if ctx.has_stacktrace:
        out.append(Suggestion("debug", "a stack trace is present — find the root cause"))
    if ctx.has_perf_issue:
        out.append(Suggestion("optimize", "a performance concern — measure first"))
    if ctx.starting_fresh and not ctx.has_spec:
        out.append(Suggestion("brainstorm", "no spec yet — explore approaches first"))
    if ctx.has_spec and not ctx.has_failing_test:
        out.append(Suggestion("test", "a spec with no failing test — write tests (RED)"))
    if ctx.has_failing_test and not ctx.has_implementation:
        out.append(Suggestion("develop", "a failing test exists — implement to green"))
    if ctx.has_diff:
        out.append(Suggestion("review", "a diff is present — review against the spec"))
    return out
