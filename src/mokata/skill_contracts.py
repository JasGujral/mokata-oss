"""SK.S1 — per-skill Contracts, grounded in the boundary→gate integrity map.

Every mokata skill announces a `## Contract` (CAN / MUST NOT / DEPENDS ON) so its boundaries
are legible instead of buried in prose. The load-bearing rule of this module: **a Contract
clause may cite a gate ONLY if that gate is a real, executable enforcement point** (a `GateRef`
with ``backed=True`` in :data:`GATES`). A boundary that no code enforces is stated as *advisory*,
never as a gate — so a Contract can never claim an enforcement that doesn't exist.

This module is the SINGLE SOURCE the shipped SKILL.md Contract blocks render from (via
:func:`render_contract_md`, injected by ``agent_skills.render_skill_md``) AND the source the
``⛭`` activation line reads its per-skill gate one-liner from (``progress.active_skill_line``).
The mapping is authored to match the gate-integrity map exactly; the SK.S1 parity guard re-derives
and compares so the two can't drift.

Clean-room: mokata's own words; no external framework imported.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------- the gate registry
@dataclass(frozen=True)
class GateRef:
    """One enforcement point. ``backed=True`` means it is an executable gate (a Contract clause
    may cite it); ``backed=False`` is a protocol boundary (advisory — never cited as a gate)."""

    id: str
    one_line: str                    # what it enforces, one line
    enforcement_point: str = ""      # the module that implements it (empty for advisory)
    backed: bool = True


# The REAL, executable gates (Part 1 of the SK.S1 map). Enforcement-point paths are asserted to
# exist by the parity guard, so this table can't cite a module that was moved/removed.
GATES: Dict[str, GateRef] = {
    "write-gate": GateRef(
        "write-gate",
        # "human-approved" is now LITERALLY true, not aspirational. Until SI.3 the MCP path's
        # approval was `approve=true` — a parameter the MODEL typed — so this line overstated what
        # the code enforced. The approval is now minted out-of-band by a human (`mokata approve
        # <id>`, see approval.py) and the model can only REFERENCE it. Wording unchanged on purpose:
        # the claim was always the right one; SI.3 is what made the code keep it.
        "every durable write is secret-scanned, human-approved, and audited",
        "src/mokata/govern/gate.py"),
    "secret-guard": GateRef(
        "secret-guard",
        "secrets hard-block a write or egress; human approval cannot override",
        "src/mokata/govern/secrets.py"),
    "spec-persisted": GateRef(
        "spec-persisted",
        "no code or tests until a saved spec with ≥1 acceptance criterion exists",
        "src/mokata/engine/spec_gate.py"),
    "completeness": GateRef(
        "completeness",
        "no emit until every acceptance criterion maps to a test",
        "src/mokata/engine/completeness.py"),
    "no-code-without-failing-test": GateRef(
        "no-code-without-failing-test",
        "implementation is allowed only against a recorded FAILING (RED) test",
        "src/mokata/govern/tdd.py"),
    "deviation": GateRef(
        "deviation",
        "a plan change is surfaced, human-gated, and audited — never silent",
        "src/mokata/govern/deviation.py"),
    "hard-rule": GateRef(
        "hard-rule",
        "in-scope hard governance rules block with no runtime override (fail-closed)",
        "src/mokata/govern/enforce.py"),
    "ship-readiness": GateRef(
        "ship-readiness",
        "landing blocks until tests are green, ACs met, and a review verdict is recorded",
        "src/mokata/engine/ship.py"),
    # `approach-approval` was advisory until PH-GATE.S0 (doc 76 FU-1): the SI.1 gate hook now
    # ENFORCES the brainstorm boundary — a native implementation write with a run registered but no
    # approach approved exits 2 (`gate_hook.GATE_PHASE`). It is a backed gate now, so the brainstorm
    # Contract may cite it.
    "approach-approval": GateRef(
        "approach-approval",
        "no spec until exactly one approach is explicitly approved",
        "src/mokata/gate_hook.py"),
    # --- advisory protocol boundaries (backed=False): headline-gate copy only, never cited ---
    "refinement-approval": GateRef(
        "refinement-approval",
        "no spec until a scoped set of refinements is explicitly approved",
        "", backed=False),
    "red-before-green": GateRef(
        "red-before-green",
        "tests are shown FAILING (RED) before any implementation exists",
        "", backed=False),
    "spec-then-quality": GateRef(
        "spec-then-quality",
        "check the diff against the approved spec first, then quality; fixes are human-gated",
        "", backed=False),
    "repro-first": GateRef(
        "repro-first",
        "reproduce the failure and capture it in a failing test before any fix",
        "", backed=False),
    "reproducer-required": GateRef(
        "reproducer-required",
        "a reproducer and a failing test come before the fix",
        "", backed=False),
    "measure-first": GateRef(
        "measure-first",
        "keep no change without a before/after measurement proving the win",
        "", backed=False),
    "finish-is-human-landed": GateRef(
        "finish-is-human-landed",
        "verify done, then the human chooses how to land it — mokata never merges/PRs/deletes",
        "", backed=False),
    "typed-capture-human-gated": GateRef(
        "typed-capture-human-gated",
        "every captured entry is typed and human-gated before it is stored",
        "", backed=False),
    "read-only-view": GateRef(
        "read-only-view",
        "the governed state is read-only; edits go through the human write gate",
        "", backed=False),
    "portable-human-gated": GateRef(
        "portable-human-gated",
        "bundles are secret-scanned; push/pull is human-gated on every transport",
        "", backed=False),
    "explicit-request-only": GateRef(
        "explicit-request-only",
        "a long multi-write flow — run on explicit request only; confirm on a dirty tree",
        "", backed=False),
    "repair-then-restart": GateRef(
        "repair-then-restart",
        "re-check and rewrite the MCP registration, then you restart Claude Code",
        "", backed=False),
    "docs-truth": GateRef(
        "docs-truth",
        "docs are cross-checked against the code; the audit is read-only and a fix is "
        "previewed then human-gated",
        "", backed=False),
}


# --------------------------------------------------------------- the contract model
@dataclass(frozen=True)
class Clause:
    """One MUST-NOT / DEPENDS-ON boundary. ``gate`` names a BACKED gate id when the boundary is
    code-enforced; ``None`` means advisory (prose-only). ``hard`` marks a hard dependency."""

    text: str
    gate: Optional[str] = None       # a backed GATES id, or None (advisory)
    hard: bool = False               # DEPENDS-ON hardness (unused for MUST-NOT)


@dataclass(frozen=True)
class Contract:
    skill: str
    headline_gate: str               # a GATES id (backed or advisory) — the ⛭ line announces it
    can: Tuple[str, ...]
    must_not: Tuple[Clause, ...]
    depends_on: Tuple[Clause, ...]


def _c(text: str, gate: Optional[str] = None, hard: bool = False) -> Clause:
    return Clause(text=text, gate=gate, hard=hard)


# The 16 Contracts — authored from doc 45 + the doc-76 map. Terse (P11); every gate cited is a
# backed GATES id (the parity guard asserts it), and every prose-only boundary is advisory.
CONTRACTS: Dict[str, Contract] = {
    "brainstorm": Contract(
        skill="brainstorm", headline_gate="approach-approval",
        can=(
            "converse Socratically, one question per turn",
            "query the graph, memory, and code read-only",
            "present 2–3 grounded approaches and write the design write-up",
            "persist the approved approach (human-gated)",
            "checkpoint each answered turn (cheap, atomic) + each coarse milestone + the approval "
            "+ a passed gate so a crash can't lose them (ungated local save via the `session_save` "
            "tool; add `turn` for a per-turn autosave)",
        ),
        must_not=(
            _c("write or edit code", "write-gate"),
            _c("draft a spec or write implementation code before an approach is explicitly approved",
               "approach-approval"),
            _c("batch questions, or rewrite the immutable problem statement"),
        ),
        depends_on=(
            _c("nothing hard — graph/memory are optional (degrade to read/grep, "
               "assumptions stated)"),
        )),
    "spec": Contract(
        skill="spec", headline_gate="completeness",
        can=(
            "turn the problem into concrete, testable acceptance criteria",
            "run the completeness gate and map each AC to a test",
            "emit the spec — `spec_emit` / `mokata spec emit` (human-gated): persists "
            "emitted_spec.json AND the shared spec corpus in one commit",
        ),
        must_not=(
            _c("emit a spec that fails completeness", "completeness"),
            _c("emit durable spec output without the write gate", "write-gate"),
            _c("write code or tests"),
        ),
        depends_on=(
            _c("an approved approach when a brainstorm/refine ran (read as a constraint, "
               "not required)"),
        )),
    "test": Contract(
        skill="test", headline_gate="red-before-green",
        can=(
            "write failing tests (RED) mapped 1:1 to approved ACs, against real symbols",
        ),
        must_not=(
            _c("write tests before a spec is persisted", "spec-persisted"),
            _c("write implementation, or fake RED (the RED must be on record)",
               "no-code-without-failing-test"),
            _c("invent ACs or cover behaviour the approved spec doesn't state"),
        ),
        depends_on=(
            _c("emitted_spec.json — stop and route to spec if absent", "spec-persisted",
               hard=True),
        )),
    "develop": Contract(
        skill="develop", headline_gate="no-code-without-failing-test",
        can=(
            "implement the minimum to green an existing failing test",
            "run baseline / spec-check / blast-radius queries first",
            "stage small, ordered, grounded tasks",
        ),
        must_not=(
            _c("write code without a persisted spec", "spec-persisted"),
            _c("write code without a failing test that demands it",
               "no-code-without-failing-test"),
            _c("change scope, approach, ACs, or design beyond approval", "deviation"),
            _c("fan out to subagents without an explicit per-run choice, or skip the "
               "green baseline"),
        ),
        depends_on=(
            _c("emitted_spec.json", "spec-persisted", hard=True),
            _c("a failing test", "no-code-without-failing-test", hard=True),
        )),
    "review": Contract(
        skill="review", headline_gate="spec-then-quality",
        can=(
            "read the diff and code",
            "run pass 1 against the approved spec/approach (flag unapproved divergence) "
            "then pass 2 for quality",
            "surface prioritized findings",
        ),
        must_not=(
            _c("apply fixes itself — a fix is a gated write", "write-gate"),
            _c("pass silently on an unapproved divergence, or approve its own work "
               "(review gates ship)"),
        ),
        depends_on=(
            _c("a diff to review; a saved spec is optional (quality-only without one, "
               "and it says so)"),
        )),
    "refine": Contract(
        skill="refine", headline_gate="refinement-approval",
        can=(
            "deep-review existing code (user-steerable)",
            "propose prioritized refinements and HARD-GATE a scoped set",
            "hand off the approved set to spec",
        ),
        must_not=(
            _c("implement anything — its output is an approved set, not code; any write "
               "is gated", "write-gate"),
            _c("expand the approved set silently (a scope change is a plan change)",
               "deviation"),
        ),
        depends_on=(
            _c("nothing hard — graph/memory optional with a stated degrade"),
        )),
    "debug": Contract(
        skill="debug", headline_gate="repro-first",
        can=(
            "reproduce the failure, capture it in a failing test, then fix minimally",
            "root-cause from the real code via structural queries",
        ),
        must_not=(
            _c("fix before the failure is captured as a failing test",
               "no-code-without-failing-test"),
            _c("widen the fix beyond the reproduced failure, or weaken/delete the test"),
        ),
        depends_on=(
            _c("a reproducer — debug builds it first"),
        )),
    "bug": Contract(
        skill="bug", headline_gate="reproducer-required",
        can=(
            "start from the reproducer, capture it in a failing test, fix to green, "
            "leave the test as a regression guard",
        ),
        must_not=(
            _c("fix before a failing test captures the bug",
               "no-code-without-failing-test"),
            _c("widen the fix, or \"fix\" by weakening the test"),
        ),
        depends_on=(
            _c("a user-provided reproducer (hard input, not machine-checked)", hard=True),
        )),
    "optimize": Contract(
        skill="optimize", headline_gate="measure-first",
        can=(
            "measure a baseline, propose, and apply behaviour-preserving wins proven by "
            "measurement",
            "keep the suite green throughout",
        ),
        must_not=(
            _c("optimize without a before-measurement, or keep an unmeasured change"),
            _c("alter behaviour — a test change routes through the deviation gate",
               "deviation"),
        ),
        depends_on=(
            _c("a green suite and a measurable target"),
        )),
    "ship": Contract(
        skill="ship", headline_gate="finish-is-human-landed",
        can=(
            "verify done-ness (green suite vs baseline, ACs met, review recorded)",
            "summarize and PREPARE landing (stage a commit, draft PR text)",
            "record the finish and show the audit --why recap",
        ),
        must_not=(
            _c("present landing options while anything is red or unmet", "ship-readiness"),
            _c("merge, PR, push, delete, or discard without the human's explicit choice",
               "write-gate"),
        ),
        depends_on=(
            _c("completed develop+review artifacts", "ship-readiness", hard=True),
            _c("git (soft — report if absent)"),
        )),
    "onboard": Contract(
        skill="onboard", headline_gate="typed-capture-human-gated",
        can=(
            "guide capture of rules, guardrails, conventions, domain, and docs",
            "distil each into a TYPED memory entry proposed for approval",
        ),
        must_not=(
            _c("store raw input verbatim, or write memory without approval", "write-gate"),
            _c("write code"),
        ),
        depends_on=(
            _c("the memory store (the product of this skill; degrades to a note)", hard=True),
        )),
    "govern": Contract(
        skill="govern", headline_gate="read-only-view",
        can=(
            "render the read-only governed-state view (rules, memory by kind, read/write "
            "ratio, pending proposals)",
            "surface the gated memory edit path",
        ),
        must_not=(
            _c("write anything from the view — the edit path it surfaces is the write gate",
               "write-gate"),
            _c("trigger self-healing writes"),
        ),
        depends_on=(
            _c("the govern MCP tool (degrades to the CLI)"),
        )),
    "session": Contract(
        skill="session", headline_gate="portable-human-gated",
        can=(
            "package session state into a path-free, hashed bundle",
            "push/pull via local/vault/postgres transports (human-gated), and resume",
        ),
        must_not=(
            _c("push or commit anywhere without approval", "write-gate"),
            _c("include a secret in a bundle", "secret-guard"),
            _c("overwrite another machine's state unconfirmed"),
        ),
        depends_on=(
            _c(".mokata/temp_local/ state", hard=True),
            _c("transport availability (postgres DSN) — degrade to local with a note"),
        )),
    "playbook": Contract(
        skill="playbook", headline_gate="explicit-request-only",
        can=(
            "run the full v1 story end-to-end as an integration check",
        ),
        must_not=(
            _c("run implicitly — explicit user request only; or run on a dirty tree "
               "without confirmation"),
            _c("skip the human gate on any durable write it performs", "write-gate"),
        ),
        depends_on=(
            _c("the full toolchain and a repo the user accepts test writes in"),
        )),
    "docsync": Contract(
        skill="docsync", headline_gate="docs-truth",
        can=(
            "audit a doc (or sweep + drift-detect the docs) against the code — read-only",
            "cross-reference claims: skill counts, command names, install/getting-started "
            "path, version examples, symbols/config keys (via graph + memory)",
            "propose reconcile edits and PREVIEW the diff",
            "write reconcile edits (human-gated)",
        ),
        must_not=(
            _c("apply a doc edit without the previewed, explicit approval", "write-gate"),
            _c("edit code, or invent a fix it cannot ground in the code"),
            _c("silently pass a stale claim — a discrepancy is a finding, never a quiet skip"),
        ),
        depends_on=(
            _c("a doc to audit (a path) or the doc tree to sweep"),
            _c("the code facts — curated-skills registry, CLI parser, version (read-only; "
               "the graph/memory sharpen the symbol + config checks, never required)"),
        )),
    "mcp-repair": Contract(
        skill="mcp-repair", headline_gate="repair-then-restart",
        can=(
            "re-check the MCP server registration, rewrite it, and name the one step left "
            "(restart Claude Code)",
        ),
        must_not=(
            _c("do first-time setup or initial installation (use /mokata:setup)"),
            _c("rewrite the Claude Code registration outside the gated config write",
               "write-gate"),
        ),
        depends_on=(
            _c("an already-installed mokata (this is repair, not install)"),
        )),
}


# --------------------------------------------------------------- rendering + validation
def headline_gate_line(skill: str) -> str:
    """The one-line gate the `⛭` activation line announces for `skill`. Single source for the
    activation line's `gate:` copy (read by ``progress.active_skill_line``)."""
    return GATES[CONTRACTS[skill].headline_gate].one_line


def _clause_tag(clause: Clause) -> str:
    """`(gate: <id>)` for a backed clause, `(advisory)` for a prose-only one. Fails loudly if a
    clause ever cites a non-backed/unknown gate id — the invariant this module guarantees."""
    if clause.gate is None:
        return "(advisory)"
    ref = GATES.get(clause.gate)
    if ref is None or not ref.backed:
        raise ValueError(
            f"contract clause cites gate '{clause.gate}' which is not a backed enforcement "
            f"point — a Contract may never claim an enforcement that doesn't exist")
    return f"(gate: {clause.gate})"


def render_contract_md(skill: str) -> str:
    """Render the `## Contract` block for `skill` from :data:`CONTRACTS`. Deterministic — the
    parity/drift guard re-renders and compares byte-for-byte to the shipped SKILL.md."""
    c = CONTRACTS[skill]
    lines: List[str] = ["## Contract", ""]
    lines.append("**CAN**")
    for item in c.can:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("**MUST NOT**")
    for cl in c.must_not:
        lines.append(f"- {cl.text} {_clause_tag(cl)}")
    lines.append("")
    lines.append("**DEPENDS ON**")
    for cl in c.depends_on:
        hard = " **(hard)**" if cl.hard else ""
        lines.append(f"- {cl.text}{hard} {_clause_tag(cl)}")
    lines.append("")
    lines.append(
        "> Grounding: `(gate: …)` boundaries are enforced by that gate in code; `(advisory)` "
        "ones are protocol discipline this skill follows, not a hard block.")
    return "\n".join(lines)


def cited_gate_ids() -> List[str]:
    """Every gate id cited by any Contract clause (deduped, sorted) — what the invariant checks."""
    ids = set()
    for c in CONTRACTS.values():
        for cl in list(c.must_not) + list(c.depends_on):
            if cl.gate is not None:
                ids.add(cl.gate)
    return sorted(ids)


def unbacked_citations() -> List[Tuple[str, str]]:
    """`(skill, gate_id)` for any clause that cites a gate that is missing or not backed. Empty
    list == the invariant holds (no Contract claims an enforcement the map didn't back)."""
    bad: List[Tuple[str, str]] = []
    for skill, c in CONTRACTS.items():
        for cl in list(c.must_not) + list(c.depends_on):
            if cl.gate is not None:
                ref = GATES.get(cl.gate)
                if ref is None or not ref.backed:
                    bad.append((skill, cl.gate))
    return bad


def prose_only_headline_skills() -> List[str]:
    """Skills whose HEADLINE gate is advisory (prose-only) — surfaced for the SK.S1 report so the
    flags in doc 76 stay honest (these announce a protocol HARD-GATE, not a code gate)."""
    return sorted(s for s, c in CONTRACTS.items() if not GATES[c.headline_gate].backed)
