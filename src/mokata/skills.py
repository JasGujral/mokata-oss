"""L1/L3/L4 — composable commands & skills.

Every capability mokata exposes is a standalone, directly-invocable skill with its own
gate — you never have to run the whole pipeline to use one. This module is the single
source of truth: the registry (catalog), the gate each skill applies on its own, the
clean-room prompt, and the renderer that produces both the CLI launch text and the
shipped `/<name>` slash-command templates (so the two can't drift).

Clean-room: prompt devices are mokata's own words; no external framework is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .brainstorm import BRAINSTORM_PROTOCOL


class SkillNotFound(KeyError):
    pass


@dataclass(frozen=True)
class Gate:
    id: str
    description: str
    kind: str = "human"          # "human" (needs approval) | "check" (verifiable)


@dataclass(frozen=True)
class Skill:
    name: str
    summary: str                 # one line — the cheap catalog view (L4)
    prompt: str                  # the full clean-room protocol — revealed on demand
    gate: Gate
    phase: Optional[str] = None  # pipeline phase it corresponds to, if any
    scaffold: bool = False       # True when the deep engine arrives in a later stage
    standalone: bool = True


_SKILLS: List[Skill] = [
    Skill(
        name="brainstorm",
        summary="Explore approaches with the user; HARD-GATE the spec behind approval.",
        prompt=BRAINSTORM_PROTOCOL,
        gate=Gate("approach-approval",
                  "HARD-GATE: no spec until exactly one approach is explicitly approved.",
                  "human"),
        phase="brainstorm",
    ),
    Skill(
        name="spec",
        summary="Turn the problem into testable acceptance criteria; map each to a test.",
        prompt=(
            "Turn the agreed problem into a spec: concrete, testable acceptance "
            "criteria. Map every criterion to a test before any code is written. If an "
            "approved brainstorm approach exists, the spec must honour it; if not, work "
            "from what the user states and mark assumptions."
        ),
        gate=Gate("completeness",
                  "No spec is complete until every acceptance criterion maps to a test "
                  "(RED before GREEN) and any approved approach is satisfied; "
                  "human-approve before emit.",
                  "human"),
        phase="emit",
    ),
    Skill(
        name="test",
        summary="Write failing tests first (RED); no implementation.",
        prompt=(
            "Write tests that express the desired behaviour and watch them FAIL first "
            "(RED). Do NOT write implementation here. One behaviour per test, clear "
            "names, real code over mocks."
        ),
        gate=Gate("red-before-green",
                  "Tests must be shown to FAIL before any implementation exists. "
                  "Writing implementation in this step is a gate violation.",
                  "check"),
    ),
    Skill(
        name="develop",
        summary="Implement the minimum to turn a failing test green.",
        prompt=(
            "Implement the minimum needed to turn a failing test GREEN. No new "
            "behaviour without a failing test that demands it; keep the change surgical "
            "and stop when the test passes."
        ),
        gate=Gate("no-code-without-failing-test",
                  "Implementation is allowed only against an existing failing test; the "
                  "change stays minimal.",
                  "check"),
    ),
    Skill(
        name="review",
        summary="Two-pass review: against the spec, then quality.",
        prompt=(
            "Review a diff in two passes. (1) Against the spec: does it do exactly what "
            "was specified — nothing more? (2) Quality: correctness, clarity, "
            "simplicity. Surface findings clearly; any fix is human-gated."
        ),
        gate=Gate("spec-then-quality",
                  "Review checks the diff against the spec (no extra features) first, "
                  "then quality. Findings are surfaced for human-gated fixes.",
                  "human"),
    ),
    Skill(
        name="debug",
        summary="Reproduce first, capture in a failing test, then fix.",
        prompt=(
            "Reproduce the failure before changing anything, then find the smallest "
            "change that fixes it. Form hypotheses and rule them out; after N strikes "
            "without a root cause, escalate to a stronger model. Root-cause before fix."
        ),
        gate=Gate("repro-first",
                  "No fix before the bug is reproduced and the root cause is identified.",
                  "check"),
    ),
    Skill(
        name="optimize",
        summary="Measure first; keep only proven, behaviour-preserving wins.",
        prompt=(
            "Measure before you change anything. Apply a change only after a baseline "
            "is recorded, and keep it only when a before/after measurement shows it is "
            "faster with behaviour unchanged; otherwise revert."
        ),
        gate=Gate("measure-first",
                  "No optimisation without a before/after measurement proving the win "
                  "and preserved behaviour.",
                  "check"),
    ),
    Skill(
        name="bug",
        summary="Start from a reproducer and a failing test, then fix.",
        prompt=(
            "Start from a reproducer. Write a failing test that captures the bug, then "
            "fix to green and leave the test as a regression guard. Labels progress "
            "reported -> reproduced -> fixing -> verified; the fix is gated behind a "
            "reproducer."
        ),
        gate=Gate("reproducer-required",
                  "A bug fix requires a reproducer and a failing test before the fix.",
                  "check"),
    ),
]

SKILLS = {s.name: s for s in _SKILLS}
SKILL_NAMES = tuple(s.name for s in _SKILLS)


def skill_names() -> List[str]:
    return list(SKILL_NAMES)


def list_skills() -> List[Tuple[str, str]]:
    """The catalog: (name, one-line summary) only — cheap, progressive disclosure."""
    return [(s.name, s.summary) for s in _SKILLS]


def get_skill(name: str) -> Skill:
    try:
        return SKILLS[name]
    except KeyError:
        raise SkillNotFound(
            f"no skill '{name}'; available: {', '.join(SKILL_NAMES)}"
        )


def render_skill(skill: Skill, grounding=None) -> str:
    """Standalone launch text for a skill (CLI `mokata run`)."""
    lines = [
        f"# mokata · /{skill.name} (standalone)",
        "",
        skill.prompt,
        "",
        f"## Gate ({skill.gate.kind})",
        skill.gate.description,
        "",
        "## Standalone",
        "Runs on its own — no upstream pipeline phase is required. Only this gate "
        "applies; a gate of a phase you did run is never silently skipped.",
    ]
    if skill.scaffold:
        lines += ["", "_(Scaffold: the deeper engine lands in a later stage.)_"]
    if grounding is not None:
        lines += ["", "## Grounding (resolved now)", grounding.summary_line()]
    return "\n".join(lines) + "\n"


def command_markdown(skill: Skill) -> str:
    """Render the shipped `/<name>` slash-command template from the skill source."""
    scaffold_note = (
        "\n_(Scaffold: the deeper engine lands in a later stage.)_\n"
        if skill.scaffold else ""
    )
    return (
        f"---\n"
        f"name: {skill.name}\n"
        f"description: {skill.summary}\n"
        f"---\n\n"
        f"# mokata · /{skill.name}\n\n"
        f"{skill.prompt}\n"
        f"{scaffold_note}\n"
        f"## Gate ({skill.gate.kind})\n"
        f"{skill.gate.description}\n\n"
        f"## Standalone\n"
        f"This command runs on its own — no upstream pipeline phase is required. It "
        f"applies only its own gate above, and never silently skips a gate of a phase "
        f"you did run.\n"
    )
