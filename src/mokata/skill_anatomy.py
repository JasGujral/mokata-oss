"""SK.S2 — per-skill anatomy: anti-rationalization tables + Verification checkbox gates.

Every mokata skill grows two legibility surfaces beyond its ``## Contract`` (SK.S1):

* an **anti-rationalization table** (``excuse → reality``) that names the exact thoughts an
  agent uses to talk itself out of the skill's discipline, and answers each; and
* a **Verification checkbox** — the evidence the skill must confirm before it may claim it is
  done ("seems right" is banned; the boxes demand a real check).

Like :mod:`skill_contracts`, this is the SINGLE SOURCE both surfaces render from. The blocks
are NOT hand-written into 15 SKILL.md files — they are pulled from :data:`ANATOMY` here and
injected by ``agent_skills.render_skill_md`` (which also injects the Contract + activation),
so all 15 render from one place and a drift guard re-renders and compares.

The Verification checkbox is **output-only legibility** — a list the agent confirms, NOT a new
runtime gate. The real gates stay exactly the eight backed enforcement points in the gate-integrity
map; nothing here blocks execution.

A skill may also declare progressive-disclosure ``references/`` — heavier detail kept OUT of
the always-loaded SKILL.md and pulled in just-in-time (P11 token frugality). Only skills that
genuinely warrant the extra depth carry them.

Clean-room: mokata's own words, examples, and structure; no external framework imported.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Rationalization:
    """One row of the anti-rationalization table: the tempting ``excuse`` and the ``reality``
    that answers it. The excuse is phrased as the thought the agent actually has."""

    excuse: str
    reality: str


@dataclass(frozen=True)
class Reference:
    """A progressive-disclosure reference file: ``path`` (relative to the skill dir) and a
    one-line ``about`` describing what heavier detail it holds (pulled in just-in-time)."""

    path: str
    about: str


@dataclass(frozen=True)
class Anatomy:
    """The SK.S2 anatomy for one skill: its rationalization rows, its verification checklist,
    and any progressive-disclosure references."""

    skill: str
    rationalizations: Tuple[Rationalization, ...]
    verification: Tuple[str, ...]
    references: Tuple[Reference, ...] = field(default_factory=tuple)


def _r(excuse: str, reality: str) -> Rationalization:
    return Rationalization(excuse=excuse, reality=reality)


# The 16 anatomies. Terse (P11); own words. Each excuse is a real self-talk pattern that
# defeats the skill's discipline; each verification item demands EVIDENCE, never a vibe.
ANATOMY: Dict[str, Anatomy] = {
    "brainstorm": Anatomy(
        skill="brainstorm",
        rationalizations=(
            _r("The direction is obvious — I'll just start the spec.",
               "No approach is approved until the human says so; the spec is gated on an "
               "explicit approval, not on your confidence."),
            _r("I'll offer one strong option to save time.",
               "A single option is a decision in disguise — present 2–3 grounded approaches "
               "and let the human choose."),
            _r("I can batch these questions into one message.",
               "One question per turn keeps the human steering; batching buries the choice "
               "that actually matters."),
            _r("I'll tighten the problem statement while I'm here.",
               "The problem statement is the human's; restating it as your own silently "
               "drifts the brief."),
        ),
        verification=(
            "exactly one approach is EXPLICITLY approved before any spec work begins",
            "each approach is grounded in the real code/graph, not an assumed design",
            "the approved approach was persisted through the human gate",
            "the human's problem statement is unchanged (not silently rewritten)",
        )),
    "spec": Anatomy(
        skill="spec",
        rationalizations=(
            _r("This AC is clearly testable — I'll map the test later.",
               "Every acceptance criterion maps to a test BEFORE emit; \"later\" is exactly "
               "where the coverage gap hides."),
            _r("I know the signature — I don't need to read the code.",
               "Ground every AC in the real symbols; a guessed interface ships a wrong spec."),
            _r("Close enough to complete — emit it.",
               "The completeness gate decides done, not your judgement — an unmapped AC blocks "
               "emit."),
        ),
        verification=(
            "every acceptance criterion maps to a named test (completeness gate passed)",
            "a \"Verified from code:\" list names the symbols/signatures you actually checked",
            "any approved brainstorm approach / refinement set is honoured",
            "the spec is emitted ONLY through the human gate",
        )),
    "test": Anatomy(
        skill="test",
        rationalizations=(
            _r("I'll write the implementation while the test is fresh.",
               "No implementation in this phase — the RED must be on record first."),
            _r("This test passes already — good enough.",
               "A test that never failed proves nothing; watch it FAIL (RED) before any code "
               "exists."),
            _r("I'll add a couple of extra cases I think matter.",
               "Test only the approved ACs; unapproved coverage is scope creep — amend the "
               "spec instead."),
        ),
        verification=(
            "every test maps 1:1 to an approved acceptance criterion",
            "each test was shown FAILING (RED) before any implementation",
            "real names/signatures are used — no invented interface",
            "NO implementation was written in this phase",
        )),
    "develop": Anatomy(
        skill="develop",
        rationalizations=(
            # REQUIRED by the SK.S2 spec — the signature develop excuse.
            _r("I'll clean this up while I'm here.",
               "Unasked cleanup is unapproved scope — leave it, or route it through the "
               "deviation gate; a green test never licenses a drive-by refactor."),
            _r("It's a tiny change — no test needed.",
               "Every new behaviour needs a failing test that demands it; \"tiny\" is how "
               "untested behaviour ships."),
            _r("I can assume this caller's shape.",
               "Check the call sites (`callers` / `blast_radius`) before touching a shared "
               "symbol — assume nothing you didn't read."),
            _r("The plan's slightly off but I'll adapt as I go.",
               "A plan change STOPS and re-enters approval (deviation gate); adapting silently "
               "is exactly the banned path."),
        ),
        verification=(
            "a persisted spec exists for this run (`spec_show` returns it)",
            "a failing test demanded every behaviour you added",
            "the call sites of every changed shared symbol were checked",
            "scope/approach/ACs/design are unchanged — or the deviation gate was used",
            "the suite is green against the pre-change baseline",
        ),
        references=(
            Reference("references/grounding-and-deviation.md",
                      "the full grounding + deviation drill: verify-or-ask, discovered-"
                      "assumption handling, and what re-enters approval"),
        )),
    "review": Anatomy(
        skill="review",
        rationalizations=(
            _r("The build looks right — I'll agree with it.",
               "Agreement isn't review; re-derive the verdict from the code and your OWN test "
               "run (doubt theater is confirming, not checking)."),
            _r("I'll skim the builder's notes to save time.",
               "The reviewer sees the artifact + contract ONLY — a builder's claims bias the "
               "verdict toward ratifying it."),
            _r("Minor divergence — I'll let it pass.",
               "An unapproved divergence is a finding, never a silent pass."),
            _r("One quick pass covers it.",
               "Both passes run: against the approved spec, then quality across the five named "
               "axes."),
        ),
        verification=(
            "pass 1 checked the diff against the approved spec/approach (divergence flagged)",
            "pass 2 covered all five axes: correctness · readability · architecture · "
            "security · performance",
            "each finding carries a severity label (Blocking/Minor/Suggestion/Info) and "
            "names file:line",
            "NO builder claims were used — the verdict was re-derived independently",
            "the verdict was persisted (`record-review`) so ship can read it",
        ),
        references=(
            Reference("references/review-axes.md",
                      "the five review axes, each hooked to its mokata instrument, the "
                      "severity taxonomy, and the improves-code-health bar in full"),
        )),
    "refine": Anatomy(
        skill="refine",
        rationalizations=(
            _r("I'll just fix what I find as I review.",
               "Refine PROPOSES; it never implements — any write is gated, and the fix belongs "
               "to develop after a spec."),
            _r("The scope obviously includes this too.",
               "Expanding the approved set silently is a plan change — route it through the "
               "deviation gate."),
            _r("I'll pick the refinements for them.",
               "HARD-GATE a scoped set the HUMAN approves, not one you assumed for them."),
        ),
        verification=(
            "findings are prioritized and grounded in the real code",
            "a scoped refinement set was EXPLICITLY approved before hand-off",
            "nothing was implemented in this phase",
            "the approved set hands off to spec unchanged",
        )),
    "debug": Anatomy(
        skill="debug",
        rationalizations=(
            _r("I see the bug — I'll just fix it.",
               "Reproduce the failure and capture it in a failing test BEFORE any fix."),
            _r("I'll widen the fix to be safe.",
               "Fix only the reproduced failure; a wider change is unapproved scope."),
            _r("I can reason about this path without reading it.",
               "Root-cause from the real code you actually read and traced, not a theory."),
            _r("The test is annoying — I'll relax it.",
               "Weakening or deleting the captured test hides the very bug it proves."),
        ),
        verification=(
            "the failure is reproduced and captured as a failing test",
            "the root cause was traced in real code (callers/callees), not theorised",
            "the fix is minimal — scoped to the reproduced failure",
            "the captured test remains as a regression guard",
        )),
    "bug": Anatomy(
        skill="bug",
        rationalizations=(
            _r("I understand the report — I'll fix from the description.",
               "Start from a real reproducer and a failing test, not a description."),
            _r("I'll make it pass by adjusting the test.",
               "\"Fixing\" the test hides the bug — fix the code and keep the test."),
            _r("The root cause is obvious.",
               "Trace it in the real failing path before you change anything."),
        ),
        verification=(
            "a reproducer exists and a failing test captures the bug",
            "the root cause was found in the real failing code",
            "the fix greens the test WITHOUT weakening it",
            "the regression test remains in place",
        )),
    "optimize": Anatomy(
        skill="optimize",
        rationalizations=(
            _r("This is obviously the hot path.",
               "Measure first — the real hot path is rarely the assumed one."),
            _r("It feels faster — keep it.",
               "Keep a change ONLY when a before/after measurement proves the win; feel is "
               "not evidence."),
            _r("A small behaviour change is fine for the speed.",
               "Behaviour must be preserved; a behaviour change routes through the deviation "
               "gate."),
        ),
        verification=(
            "a baseline measurement was recorded BEFORE any change",
            "an after-measurement proves the win in numbers, not feel",
            "behaviour is unchanged (suite green); any behaviour change went through deviation",
            "unmeasured or unproven changes were reverted",
        ),
        references=(
            Reference("references/measure-first.md",
                      "what to measure, how to record a baseline, and when a win is real "
                      "enough to keep"),
        )),
    "ship": Anatomy(
        skill="ship",
        rationalizations=(
            _r("Everything looks done — I'll land it.",
               "Verify from EVIDENCE: green suite vs baseline, ACs met, review recorded — not "
               "a glance."),
            _r("Review basically passed — close enough.",
               "Read the persisted verdict; no record on file means STOP, not proceed."),
            _r("I'll just merge / open the PR / push to finish.",
               "mokata never merges, PRs, or deletes — the human chooses how to land it."),
        ),
        verification=(
            "the full suite is green against the confirmed baseline",
            "every acceptance criterion is met (completeness)",
            "a review verdict is on record (independent-vs-inline noted honestly)",
            "NO git action ran without the human's explicit, specific choice",
            "the finish + the why-recap are recorded in the audit ledger",
        )),
    "onboard": Anatomy(
        skill="onboard",
        rationalizations=(
            _r("I'll store what they said verbatim.",
               "Distil each entry into a TYPED memory item — raw input is not a memory."),
            _r("This is clearly a rule — I'll just save it.",
               "Every entry is human-gated before storage; clear-to-you is not approved."),
            _r("A conflict? I'll overwrite the old one.",
               "A conflict routes through self-healing (old→new), never a silent overwrite."),
        ),
        verification=(
            "each entry is distilled and TYPED (rule / guardrail / convention / context / doc)",
            "every write was previewed and human-approved",
            "conflicts routed through the healing path, not a silent overwrite",
            "nothing raw or unapproved was stored",
        )),
    "govern": Anatomy(
        skill="govern",
        rationalizations=(
            _r("I'll fix this memory item straight from the view.",
               "The view is READ-ONLY; edits go through the gated memory-edit path it surfaces."),
            _r("I'll trigger a heal to tidy things up.",
               "Self-healing writes are not driven from this view."),
            _r("No MCP tool available — I'll skip it.",
               "Degrade to the CLI; the read-only view still renders."),
        ),
        verification=(
            "the view was rendered READ-ONLY (no write performed from it)",
            "any edit was routed through the gated memory-edit path",
            "pending self-healing proposals are surfaced for a human decision",
            "it degraded cleanly when the MCP tool was absent",
        )),
    "session": Anatomy(
        skill="session",
        rationalizations=(
            _r("I'll push the bundle to sync quickly.",
               "Every transport — push/pull/commit — is human-gated on each hop."),
            _r("It's just my own state — secrets are fine in it.",
               "Bundles are secret-scanned; a secret hard-blocks egress and human approval "
               "can't override it."),
            _r("The other machine's state is stale — overwrite it.",
               "Surface the content hash and CONFIRM before overwriting another machine."),
        ),
        verification=(
            "the bundle is path-free and content-hashed",
            "the secret scan passed (no secret in the bundle)",
            "each push/pull was human-gated",
            "an overwrite was confirmed against the content hash",
        )),
    "playbook": Anatomy(
        skill="playbook",
        rationalizations=(
            _r("This seems like a good moment to run the whole flow.",
               "Run only on an EXPLICIT request — never implicitly, it's a long multi-write "
               "flow."),
            _r("The tree's a bit dirty but it'll be fine.",
               "Confirm on a dirty tree before running — uncommitted work is at risk."),
            _r("It's just an integration check — writes don't need gating.",
               "Every durable write it performs is still human-gated."),
        ),
        verification=(
            "the run was EXPLICITLY requested by the human",
            "a dirty working tree was confirmed before starting",
            "every durable write went through its gate",
            "it ran on a repo the user accepts test writes in",
        )),
    "docsync": Anatomy(
        skill="docsync",
        rationalizations=(
            _r("The doc reads fine — it's probably still accurate.",
               "\"Reads fine\" is not checked — cross-reference every claim against the code "
               "(counts, command names, install path, symbols); drift hides in plausible prose."),
            _r("This claim is obviously stale — I'll just edit it.",
               "A reconcile edit is a durable write — PREVIEW the diff and get explicit approval; "
               "docsync never writes a doc silently."),
            _r("The number's a bit off — close enough to leave.",
               "A wrong count/command/path is a Blocking discrepancy, not a rounding error — "
               "flag it with its severity, never a silent pass."),
            _r("No code graph wired, so I can't really check.",
               "Degrade to the lexical + registry facts (counts, command names, install path "
               "still check); say what you could not verify — don't skip the audit."),
        ),
        verification=(
            "every claim was cross-referenced against the code (not read for plausibility)",
            "each discrepancy carries a severity (Blocking/Minor/Info) and names its stale section",
            "the audit wrote NOTHING (read-only) unless a reconcile was explicitly requested",
            "any reconcile edit was PREVIEWED as a diff and written only through the human gate",
        ),
        references=(
            Reference("references/docsync-checks.md",
                      "the full cross-reference checklist — what each check proves, its "
                      "severity, and how the graph/memory sharpen the symbol + config checks"),
        )),
    "mcp-repair": Anatomy(
        skill="mcp-repair",
        rationalizations=(
            _r("I'll set it up fresh from scratch.",
               "This is REPAIR, not first-time setup — use /mokata:setup to install."),
            _r("I'll rewrite the config file directly.",
               "The registration rewrite is a gated config write, not a silent edit."),
            _r("It's fixed — no need to restart.",
               "Name the one step left: the user restarts Claude Code so it re-reads the "
               "registration."),
        ),
        verification=(
            "an existing mokata install was detected (repair, not install)",
            "the MCP registration was re-checked and rewritten through the gated write",
            "the restart step is named for the user",
            "first-time setup was NOT attempted here",
        )),
}


# --------------------------------------------------------------- rendering
def render_anatomy_md(skill: str) -> str:
    """Render the SK.S2 anatomy block for `skill` from :data:`ANATOMY` — the anti-rationalization
    table, the Verification checkbox, and any progressive-disclosure references. Deterministic:
    the drift guard re-renders and compares byte-for-byte to the shipped SKILL.md."""
    a = ANATOMY[skill]
    lines: List[str] = []

    lines.append("## Rationalizations — stop if you catch yourself thinking any of these")
    lines.append("")
    lines.append("| Excuse | Reality |")
    lines.append("|---|---|")
    for row in a.rationalizations:
        lines.append(f"| \"{row.excuse}\" | {row.reality} |")
    lines.append("")

    lines.append("## Verification — confirm each before you claim this skill is done")
    lines.append("")
    lines.append("Evidence, not \"seems right\" — check every box or say which is unmet and why:")
    lines.append("")
    for item in a.verification:
        lines.append(f"- [ ] {item}")

    if a.references:
        lines.append("")
        lines.append("## References — pulled in just-in-time (not loaded inline)")
        lines.append("")
        for ref in a.references:
            lines.append(f"- `{ref.path}` — {ref.about}")

    return "\n".join(lines)


def skills_with_references() -> List[str]:
    """The skills that carry progressive-disclosure references (sorted). Used by setup to copy
    the reference files and by the anatomy lint to assert the pointed-to files exist."""
    return sorted(s for s, a in ANATOMY.items() if a.references)


def reference_relpaths(skill: str) -> List[str]:
    """Every reference file path (relative to the skill dir) declared by `skill`."""
    return [r.path for r in ANATOMY[skill].references]
