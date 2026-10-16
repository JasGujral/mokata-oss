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


# ---------------------------------------------- G-3: free-text intent -> skill
# "flaky bug" -> /debug. DETERMINISTIC: a fixed keyword/phrase table, word-boundary matching, a
# stable tie-break on pipeline order. No model call, no embedding, no network — the same words
# always give the same answer, and every recommendation names the words that produced it.
#
# OVERLAP, stated rather than hidden: Claude Code already activates an Agent Skill when the
# conversation matches its SKILL.md `when_to_use`. That is model-judged and fires INSIDE a
# session; this is a deterministic lookup any surface can call (the CLI before a session starts,
# MCP on a non-Claude harness that has no skill activation at all). It RECOMMENDS only — it never
# runs a skill, and it never stands in for a phase gate.

#: skill -> ((term, weight), ...). A term with a space is a phrase. Weights: 3 = names the skill's
#: job outright, 2 = strong signal, 1 = weak signal that only tips a tie.
INTENT_TERMS = {
    "debug": (("flaky", 3), ("intermittent", 3), ("intermittently", 3), ("root cause", 3),
              ("stack trace", 3), ("stacktrace", 3), ("traceback", 3), ("segfault", 3),
              ("crash", 2), ("crashes", 2), ("crashing", 2), ("exception", 2), ("error", 2),
              ("errors", 2), ("hang", 2), ("hangs", 2), ("broken", 2), ("not working", 2),
              ("why", 1), ("debug", 3), ("investigate", 2), ("failing", 1), ("fails", 1),
              ("bug", 1)),
    "bug": (("bug", 2), ("bugs", 2), ("reproducer", 3), ("repro", 3), ("bug report", 3),
            ("steps to reproduce", 3), ("defect", 2), ("regression", 2), ("issue", 1),
            ("ticket", 1), ("fix", 1)),
    "optimize": (("slow", 3), ("slower", 3), ("performance", 3), ("perf", 3), ("latency", 3),
                 ("speed up", 3), ("faster", 3), ("throughput", 3), ("bottleneck", 3),
                 ("optimize", 3), ("optimise", 3), ("benchmark", 2), ("profile", 2),
                 ("memory usage", 2), ("cpu", 1)),
    "brainstorm": (("brainstorm", 3), ("new feature", 3), ("feature", 2), ("idea", 2),
                   ("design", 2), ("approach", 2), ("approaches", 2), ("explore", 2),
                   ("architecture", 2), ("prototype", 2), ("from scratch", 2),
                   ("how should", 2), ("options", 1), ("build", 1), ("add", 1)),
    "refine": (("refactor", 3), ("refactoring", 3), ("clean up", 3), ("cleanup", 3),
               ("tech debt", 3), ("code smell", 3), ("existing code", 2), ("legacy", 2),
               ("simplify", 2), ("tidy", 2), ("restructure", 2), ("improve", 1)),
    "onboard": (("onboard", 3), ("onboarding", 3), ("project rules", 3), ("conventions", 2),
                ("guardrails", 2), ("coding standards", 2), ("house style", 2),
                ("capture", 1)),
    "spec": (("spec", 3), ("specification", 3), ("acceptance criteria", 3),
             ("requirements", 2), ("user story", 2), ("define", 1)),
    "test": (("write tests", 3), ("unit test", 3), ("unit tests", 3), ("tdd", 3),
             ("test", 2), ("tests", 2), ("coverage", 2)),
    "develop": (("implement", 3), ("make the test pass", 3), ("make it pass", 3),
                ("failing test", 3), ("green", 2), ("implementation", 2), ("code it", 2)),
    "review": (("review", 3), ("code review", 3), ("pull request", 3), ("pr", 2), ("diff", 2),
               ("before merge", 2), ("check my", 1)),
    "ship": (("ship", 3), ("release", 2), ("deploy", 2), ("merge", 2), ("land", 2),
             ("publish", 2), ("done", 1)),
    "version": (("which version", 3), ("update mokata", 3), ("upgrade mokata", 3),
                ("version", 2)),
}

# Pipeline order — the tie-break, so equal scores always come back the same way round.
_INTENT_ORDER = ("brainstorm", "refine", "onboard", "spec", "test", "develop", "review",
                 "debug", "optimize", "bug", "ship", "version")


@dataclass
class Recommendation:
    skill: str
    reason: str
    score: int
    matched: List[str]


def _intent_text(intent: str) -> str:
    import re
    words = re.findall(r"[a-z0-9]+", intent.lower().replace("_", " "))
    return " " + " ".join(words) + " "


def recommend(intent: str, ctx: "SuggestionContext | None" = None,
              limit: int = 3) -> List[Recommendation]:
    """Free-text intent -> ranked skill recommendations. RECOMMEND ONLY — never runs anything.

    Scores each skill by the weighted terms of `INTENT_TERMS` found in the intent (whole words /
    whole phrases only, so "pr" never matches inside "print"). When `ctx` is given, the flag-based
    `suggest(ctx)` adds +2 to each skill it names, so the two signals combine instead of competing.
    No term matched and no flag set -> an EMPTY list: an unmatched intent is not guessed at."""
    text = _intent_text(intent or "")
    scores: dict = {}
    matched: dict = {}
    for skill, terms in INTENT_TERMS.items():
        for term, weight in terms:
            if f" {term} " in text:
                scores[skill] = scores.get(skill, 0) + weight
                matched.setdefault(skill, []).append(term)
    flag_reasons = {}
    if ctx is not None:
        for s in suggest(ctx):
            scores[s.skill] = scores.get(s.skill, 0) + 2
            flag_reasons.setdefault(s.skill, s.reason)
    order = {name: i for i, name in enumerate(_INTENT_ORDER)}
    ranked = sorted((k for k, v in scores.items() if v > 0),
                    key=lambda k: (-scores[k], order.get(k, len(order)), k))
    out: List[Recommendation] = []
    for skill in ranked[:max(0, limit)]:
        parts = []
        if matched.get(skill):
            parts.append("your words: " + ", ".join(f"'{t}'" for t in matched[skill]))
        if skill in flag_reasons:
            parts.append(flag_reasons[skill])
        out.append(Recommendation(skill, "; ".join(parts), scores[skill],
                                  list(matched.get(skill, []))))
    return out
