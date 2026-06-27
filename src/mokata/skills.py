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

from .brainstorm import BRAINSTORM_AUTO_TRIGGER, BRAINSTORM_PROTOCOL
from .onboard import ONBOARD_PROTOCOL
from .refine import REFINE_PROTOCOL


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
    argument_hint: Optional[str] = None   # optional `/` autocomplete hint (frontmatter)
    show_progress: bool = False           # Stage 27 — print the run-progress block + banner
    when_to_use: Optional[str] = None     # Stage 29 — model-invocation trigger (frontmatter)
    requires_spec: bool = False           # Stage 32 — spec-persisted precondition (impl entry)
    ground: bool = True                   # Stage 33 — append the anti-assumption discipline


# Stage 32 — the precondition surfaced on implementation skills: a persisted, complete spec
# must exist before code/tests. Fired ahead of the skill's own gate.
SPEC_PERSISTED_PRECONDITION = (
    "Precondition (spec-persisted): a saved spec with at least one acceptance criterion must "
    "exist (`emitted_spec.json`, written by the human-gated `emit` after the completeness "
    "gate passes). If it's absent, STOP and produce + emit the spec first (`/mokata:spec`) — "
    "do not write code or tests against an unsaved spec."
)


# Stage 33 — the shared anti-assumption / ground-in-code clause, appended UNIFORMLY to every
# critical skill (single source so it can't drift). Covers both the up-front "verify or ask"
# and the continuous "discovered an assumption -> STOP, confirm, re-plan" rule, routed through
# the deviation gate. Clean-room: mokata's own words.
GROUNDING_DISCIPLINE = (
    "Decide from the code, not from assumption. Before you assert anything about types, "
    "signatures, behaviour, control flow, conventions, dependencies, error handling, or file "
    "layout, VERIFY it against the actual code: read the relevant source, run structural "
    "queries (`mokata query callers|callees|implementers|imports|blast_radius <symbol>`), and "
    "check memory for prior decisions and conventions. Consult the project brain: honour the "
    "captured rules and guardrails, and pull in only the context, references, and best-practices "
    "RELEVANT to the symbols/topic in play (just-in-time — never the whole corpus). The graph + "
    "memory are the source of "
    "truth; where they're absent, read or grep the code and state what you read. If a fact "
    "CANNOT be determined from the code, state the assumption explicitly and ASK — never "
    "silently assume. Cite what you verified. And continuously: if at any point you find a "
    "decision rested on an assumption, or the code contradicts something you assumed, STOP — "
    "surface it (what you assumed vs. what the code shows), CONFIRM with the user, and re-plan "
    "(route it through the deviation gate and amend the spec/ACs so they stay grounded and "
    "provable). There is no \"assumed and continued\" path."
)


# Stage 37 — the spec-awareness / regression-guard clause, appended to the change-making skills
# (spec/refine/develop). Clean-room: mokata's own words. Single source so it can't drift.
SPEC_AWARENESS_CLAUSE = (
    " Spec-awareness (regression guard): before making the change, check it against the SAVED "
    "specs and recorded decisions — run `mokata spec-check --symbols <touched> --files "
    "<touched>` (or the `spec_check` tool) over the symbols/files in play. If it reports the "
    "change affects a saved spec or a recorded decision, STOP and route it through the deviation "
    "gate: the human confirms (amend/supersede the affected spec/decision) or you re-plan — never "
    "break a previously-approved spec silently. Degrade-clean: no saved specs yet ⇒ it's a no-op "
    "(no false alarm); no code graph ⇒ it falls back to a lexical/file-overlap check and says so."
)


# Stage 27 — the instruction appended to pipeline-flow skills so the user always sees where
# they are. The block is READ-ONLY over the run-state (via `mokata progress` / the MCP
# `progress` tool); show it, never fabricate it.
PROGRESS_INSTRUCTION = (
    "At the START and END of this phase, show where the run is: print the mokata "
    "run-progress block (the ordered phases marked done/current/pending with the "
    "[done/total] count and what's next) and a one-line banner naming what's running now "
    "— e.g. `mokata · {name} (running)` then `mokata · {name} (done)`. This is read-only "
    "over the persisted run-state (`mokata progress` / the `progress` MCP tool) — surface "
    "it, don't invent it. So the user never wonders whether mokata is running or which part."
)


_SKILLS: List[Skill] = [
    Skill(
        name="brainstorm",
        summary="mokata · Explore approaches with the user; HARD-GATE the spec behind approval.",
        prompt=BRAINSTORM_PROTOCOL,
        gate=Gate("approach-approval",
                  "HARD-GATE: no spec until exactly one approach is explicitly approved.",
                  "human"),
        phase="brainstorm",
        show_progress=True,
        when_to_use=BRAINSTORM_AUTO_TRIGGER,   # Stage 29 — model-invocable when exploring
    ),
    Skill(
        name="refine",
        summary=("mokata · Deep, user-steerable review of EXISTING code → propose "
                 "prioritized refinements → HARD-GATE a scoped set, then hand off to spec."),
        prompt=REFINE_PROTOCOL + SPEC_AWARENESS_CLAUSE,
        gate=Gate("refinement-approval",
                  "HARD-GATE: no spec until the user explicitly approves a scoped set of "
                  "refinements; the approved set hands off to the existing spec skill.",
                  "human"),
        phase="refine",
        argument_hint="[scope]   # e.g. focus auth + security, or exclude performance",
        show_progress=True,
    ),
    Skill(
        name="onboard",
        summary=("mokata · Guided capture of the project's rules, guardrails, conventions, "
                 "domain context & docs into TYPED, human-gated memory the skills then reference."),
        prompt=ONBOARD_PROTOCOL,
        gate=Gate("typed-capture-human-gated",
                  "Every captured entry is distilled, typed, and HUMAN-GATED before it is "
                  "stored; a conflict routes through self-healing (old→new), never silent.",
                  "human"),
        argument_hint="[focus]   # e.g. rules, guardrails, conventions, context, or a doc to ingest",
        when_to_use=(
            "Engage when the user wants to teach mokata the project's rules, guardrails, "
            "conventions, domain facts/formulas, or a reference document — i.e. capturing "
            "institutional knowledge mokata should honour, during setup or any time later. "
            "Do NOT engage for one-off decisions mid-task (those are remembered inline)."
        ),
    ),
    Skill(
        name="spec",
        summary="mokata · Turn the problem into testable acceptance criteria; map each to a test.",
        prompt=(
            "BEFORE drafting or emitting ANY acceptance criterion, inspect the REAL code the "
            "change touches: the symbols involved, their callers/callees/implementers, the "
            "existing tests, and the conventions of nearby code (use the structural queries "
            "and memory). Every acceptance criterion must be grounded in actual code — real "
            "names, signatures, and behaviour — never a guessed interface. Emit a short "
            "\"Verified from code:\" list naming the symbols / signatures / edges you checked, "
            "so the grounding is auditable. If an AC rests on something you could NOT verify "
            "from the code, mark it as an assumption and ASK before emitting it. Then turn the "
            "agreed problem into a spec: concrete, testable acceptance criteria, and map every "
            "criterion to a test before any code is written. Decompose the work into SMALL, "
            "ordered, verifiable tasks — each naming the exact files/symbols it touches and a "
            "concrete check — so each task is grounded and provable. If an approved brainstorm "
            "approach or refinement set exists, the spec must honour it."
            + SPEC_AWARENESS_CLAUSE
        ),
        gate=Gate("completeness",
                  "No spec is complete until every acceptance criterion maps to a test "
                  "(RED before GREEN) and any approved approach is satisfied; "
                  "human-approve before emit.",
                  "human"),
        phase="emit",
        show_progress=True,
    ),
    Skill(
        name="test",
        summary="mokata · Write failing tests first (RED); no implementation.",
        prompt=(
            "Do NOT write tests until the spec is emitted and SAVED: if there is no "
            "`emitted_spec.json` (the persisted, completeness-gate-passed spec), STOP and "
            "produce + emit the spec first (`/mokata:spec`). Then write tests that express "
            "the desired behaviour and watch them FAIL first (RED). Do NOT write "
            "implementation here. One behaviour per test, clear names, real code over "
            "mocks. Reference the REAL names, signatures, and return types found in the code "
            "— never invent an interface; verify each symbol you call exists and has the "
            "shape you expect. Test ONLY the approved acceptance criteria — do not invent "
            "ACs or cover behaviour the approved spec doesn't state. If an AC is wrong, "
            "missing, or untestable, STOP and ask to amend the spec (so ACs and tests stay "
            "provable); never silently add or drop coverage."
        ),
        gate=Gate("red-before-green",
                  "Tests must be shown to FAIL before any implementation exists. "
                  "Writing implementation in this step is a gate violation.",
                  "check"),
        show_progress=True,
        requires_spec=True,
    ),
    Skill(
        name="develop",
        summary="mokata · Implement the minimum to turn a failing test green.",
        prompt=(
            "BEFORE writing any code, ask how to run this implementation: the sequential "
            "gated flow (default, lowest cost) or parallel subagents (fresh-subagent "
            "isolation and/or concurrent fan-out). Ask ONCE for the run, show the cost "
            "estimate when offering parallel, honor the saved `settings.execution.default` "
            "preference, and NEVER fan out without an explicit choice; if the harness has "
            "no subagents, say so and run sequentially. Do NOT write code until the spec "
            "is emitted and SAVED: if there is no `emitted_spec.json` (the persisted, "
            "completeness-gate-passed spec), STOP and produce + emit the spec first "
            "(`/mokata:spec`). Confirm a GREEN test baseline before you start "
            "(`mokata baseline`), so any new failure is attributable to your change. Then "
            "implement the minimum needed "
            "to turn a failing test GREEN. Implement against the REAL contracts found in the "
            "code, not assumed ones: before changing a shared symbol, check its call sites "
            "(`mokata query callers <symbol>` / `blast_radius`) so you don't break a caller "
            "you didn't read. Work in SMALL, ordered, grounded tasks — each naming the "
            "files/symbols it touches and a check. No new behaviour without a failing test "
            "that demands it; keep the change surgical and stop when the test passes. "
            "Implement STRICTLY against the approved plan — the approved spec and its "
            "acceptance criteria, the approved approach (brainstorm) or refinement set "
            "(refine), and the failing tests. You may NOT change scope, the chosen "
            "approach, the acceptance criteria, or the design beyond what was approved, "
            "and never expand scope unasked. If you discover the plan must change — an AC "
            "is wrong or infeasible, the approved approach doesn't work, a materially "
            "better design appears, or an unforeseen constraint blocks it — STOP and "
            "surface the deviation (what changes - why - the options), then get EXPLICIT "
            "human approval before proceeding. An approved change re-enters the approval "
            "surface (re-approve the approach/refinements, or amend the spec so every AC "
            "still maps to a test) and is logged to the audit ledger. Never silently "
            "deviate."
            + SPEC_AWARENESS_CLAUSE
        ),
        gate=Gate("no-code-without-failing-test",
                  "Implementation is allowed only against an existing failing test; the "
                  "change stays minimal.",
                  "check"),
        show_progress=True,
        requires_spec=True,
    ),
    Skill(
        name="review",
        summary="mokata · Two-pass review: against the spec, then quality.",
        prompt=(
            "Review a diff in two passes. (1) Against the approved plan: does it do "
            "EXACTLY what was specified and approved — the approved acceptance criteria "
            "and the approved approach/refinements, nothing more? Flag any UNAPPROVED "
            "divergence (added scope, a changed approach, a changed or dropped AC, a "
            "redesign) as a finding — never a silent pass. Check the diff against the "
            "ACTUAL code it touches — do the calls, signatures, contracts, and conventions "
            "match the real symbols (verify with the structural queries)? Flag anything that "
            "looks ASSUMED rather than verified. (2) Quality: correctness, "
            "clarity, simplicity. Surface findings clearly; any fix is human-gated."
        ),
        gate=Gate("spec-then-quality",
                  "Review checks the diff against the spec (no extra features) first, "
                  "then quality. Findings are surfaced for human-gated fixes.",
                  "human"),
        show_progress=True,
    ),
    Skill(
        name="debug",
        summary="mokata · Reproduce first, capture in a failing test, then fix.",
        prompt=(
            "Reproduce the failure before changing anything, then find the smallest "
            "change that fixes it. Root-cause from the REAL code — read the failing path "
            "and trace it with the structural queries (callers/callees); don't theorise "
            "about code you haven't read. Form hypotheses and rule them out against the "
            "actual source; after N strikes without a root cause, escalate to a stronger "
            "model. Root-cause before fix."
        ),
        gate=Gate("repro-first",
                  "No fix before the bug is reproduced and the root cause is identified.",
                  "check"),
    ),
    Skill(
        name="optimize",
        summary="mokata · Measure first; keep only proven, behaviour-preserving wins.",
        prompt=(
            "Measure before you change anything — measure the REAL code, don't assume the "
            "hot path; confirm where the time actually goes first. Apply a change only "
            "after a baseline is recorded, and keep it only when a before/after measurement "
            "shows it is faster with behaviour unchanged; otherwise revert."
        ),
        gate=Gate("measure-first",
                  "No optimisation without a before/after measurement proving the win "
                  "and preserved behaviour.",
                  "check"),
    ),
    Skill(
        name="bug",
        summary="mokata · Start from a reproducer and a failing test, then fix.",
        prompt=(
            "Start from a reproducer. Write a failing test that captures the bug, then "
            "fix to green and leave the test as a regression guard. Root-cause from the "
            "REAL code — read the failing path and trace it with the structural queries "
            "before fixing; don't guess at code you haven't read. Labels progress "
            "reported -> reproduced -> fixing -> verified; the fix is gated behind a "
            "reproducer."
        ),
        gate=Gate("reproducer-required",
                  "A bug fix requires a reproducer and a failing test before the fix.",
                  "check"),
    ),
    Skill(
        name="ship",
        summary="mokata · Verify it's truly done, then let YOU choose how to land it.",
        prompt=(
            "Close out the work — verify it's actually done, then help the human land it. "
            "mokata NEVER merges, opens a PR, or deletes work on its own.\n\n"
            "1. VERIFY (evidence over claims — do not take 'done' on faith): the full test "
            "suite is GREEN (re-run it; compare against the green baseline you confirmed "
            "before starting, so any new failure is attributable to this change), every "
            "acceptance criterion in the emitted spec is met (completeness), and `review` "
            "passed. If ANYTHING is red or unmet, STOP and report exactly what's missing — "
            "do not present landing options for unfinished work.\n"
            "2. SUMMARIZE what shipped: the spec and its acceptance-criteria-to-tests "
            "mapping, the diff surface (files/symbols changed), the decisions captured to "
            "memory, and the audit trail — so landing it is a reviewed decision.\n"
            "3. PRESENT the landing options and let the HUMAN choose: merge, open a PR, keep "
            "the branch, or discard. You may PREPARE (stage a commit/branch, draft a PR "
            "description), but run a git action ONLY after the human's explicit confirmation "
            "of a specific option. Never merge, force, or delete anything unasked; never "
            "discard work without explicit confirmation.\n"
            "4. RECORD the finish decision in the audit ledger."
        ),
        gate=Gate("finish-is-human-landed",
                  "Shipping verifies done (green tests + met ACs + passed review) and the "
                  "human chooses how to land it; mokata never merges/PRs/deletes without "
                  "explicit confirmation.",
                  "human"),
        phase="ship",
        show_progress=True,
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
    if skill.ground:
        lines += ["", "## Grounding discipline", GROUNDING_DISCIPLINE]
    if skill.requires_spec:
        lines += ["", "## Precondition", SPEC_PERSISTED_PRECONDITION]
    if skill.show_progress:
        lines += ["", "## Progress", PROGRESS_INSTRUCTION.format(name=skill.name)]
    if grounding is not None:
        lines += ["", "## Grounding (resolved now)", grounding.summary_line()]
    return "\n".join(lines) + "\n"


def command_markdown(skill: Skill) -> str:
    """Render the shipped `/<name>` slash-command template from the skill source."""
    scaffold_note = (
        "\n_(Scaffold: the deeper engine lands in a later stage.)_\n"
        if skill.scaffold else ""
    )
    hint_line = (f"argument-hint: \"{skill.argument_hint}\"\n"
                 if skill.argument_hint else "")
    # Stage 29 — a `when_to_use` makes Claude Code model-INVOKE the skill (auto-activate),
    # in addition to the /mokata:<name> slash command. Only set where we want that.
    trigger_line = (f"when_to_use: {skill.when_to_use}\n"
                    if skill.when_to_use else "")
    grounding_section = (
        f"\n## Grounding discipline\n{GROUNDING_DISCIPLINE}\n"
        if skill.ground else ""
    )
    precondition_section = (
        f"\n## Precondition\n{SPEC_PERSISTED_PRECONDITION}\n"
        if skill.requires_spec else ""
    )
    progress_section = (
        f"\n## Progress\n{PROGRESS_INSTRUCTION.format(name=skill.name)}\n"
        if skill.show_progress else ""
    )
    return (
        f"---\n"
        f"name: {skill.name}\n"
        f"description: {skill.summary}\n"
        f"{trigger_line}"
        f"{hint_line}"
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
        f"{grounding_section}"
        f"{precondition_section}"
        f"{progress_section}"
    )
