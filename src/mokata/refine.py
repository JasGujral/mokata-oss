"""Stage 26 — the `refine` front-end: review EXISTING code, propose changes, approve a
scoped set, then hand off to the existing `spec` skill.

`refine` is the counterpart to `brainstorm` (which is for *new* problems). It grounds in the
real code via the graph + memory, runs a deep, user-steerable review across all quality
dimensions, proposes a prioritized set of refinements, and HARD-GATES the spec behind the
user's explicit approval of a scoped set. The approved set is persisted as
`approved_refinements` (mirroring brainstorm's `approved_approach`) and read by the
completeness gate — then the UNCHANGED pipeline (`spec → test → develop → review`) does the
work. `refine` never writes the spec itself.

Clean-room: the review protocol is mokata's own words; no external framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .brainstorm import Grounding, ground  # reuse the grounding (graph+memory) primitive

# StateStore key (under .mokata/temp_local/state/) — the approved scoped refinement set.
REFINE_STATE_KEY = "approved_refinements"

# The review dimensions a full (unscoped) pass covers. The user can include/exclude/focus.
REVIEW_DIMENSIONS = (
    "architecture & boundaries",
    "design patterns & anti-patterns",
    "CS best practices",
    "code quality & readability",
    "testability",
    "coupling & cohesion",
    "error handling",
    "security",
    "performance",
)

BEHAVIOR_PRESERVING = "preserving"
BEHAVIOR_CHANGING = "changing"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RefineError(Exception):
    """A refine-flow rule was violated."""


class RefineGateError(RefineError):
    """The HARD-GATE was crossed: a hand-off/spec attempt before a scoped set of
    refinements was explicitly approved."""


@dataclass
class Refinement:
    """One proposed change — prioritized, with the principle it serves, the tradeoff, and
    whether it preserves or changes behavior (default: behavior-preserving)."""

    title: str
    rationale: str = ""
    principle: str = ""
    tradeoff: str = ""
    behavior_impact: str = BEHAVIOR_PRESERVING   # preserving | changing
    priority: int = 1                            # 1 = highest
    dimension: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "rationale": self.rationale,
            "principle": self.principle, "tradeoff": self.tradeoff,
            "behavior_impact": self.behavior_impact, "priority": self.priority,
            "dimension": self.dimension,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Refinement":
        return cls(
            title=d["title"], rationale=d.get("rationale", ""),
            principle=d.get("principle", ""), tradeoff=d.get("tradeoff", ""),
            behavior_impact=d.get("behavior_impact", BEHAVIOR_PRESERVING),
            priority=int(d.get("priority", 1)), dimension=d.get("dimension", ""),
        )


@dataclass
class RefinementPlan:
    """The approved, scoped refinement set — what `spec` consumes and the completeness gate
    checks against. Mirrors brainstorm's `Handoff`."""

    target: str
    refinements: List[Refinement]
    scope_in: List[str] = field(default_factory=list)
    scope_out: List[str] = field(default_factory=list)
    grounding: Optional[Grounding] = None
    approver: str = "unknown"
    approved_at: str = ""
    schema_version: int = 1

    @property
    def label(self) -> str:
        return f"{len(self.refinements)} refinement(s) on {self.target}"

    @property
    def behavior_changing(self) -> List[Refinement]:
        return [r for r in self.refinements if r.behavior_impact == BEHAVIOR_CHANGING]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "phase": "refine",
            "target": self.target,
            "scope_in": list(self.scope_in),
            "scope_out": list(self.scope_out),
            "refinements": [r.to_dict() for r in self.refinements],
            "grounding": self.grounding.to_dict() if self.grounding else {},
            "approver": self.approver,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RefinementPlan":
        return cls(
            target=d["target"],
            refinements=[Refinement.from_dict(r) for r in d.get("refinements", [])],
            scope_in=list(d.get("scope_in", [])),
            scope_out=list(d.get("scope_out", [])),
            grounding=Grounding.from_dict(d["grounding"]) if d.get("grounding") else None,
            approver=d.get("approver", "unknown"),
            approved_at=d.get("approved_at", ""),
        )


class RefineSession:
    """A refine run: propose refinements → user approves a scoped subset → HARD-GATE the
    hand-off to `spec`. No approval → no spec (mirrors BrainstormSession)."""

    def __init__(self, target: str, grounding: Optional[Grounding] = None) -> None:
        self.target = target
        self.grounding = grounding
        self.proposed: List[Refinement] = []
        self.scope_in: List[str] = []
        self.scope_out: List[str] = []
        self.approved: bool = False
        self.approved_set: List[Refinement] = []
        self.approver: Optional[str] = None
        self.approved_at: Optional[str] = None

    def propose(self, refinements: List[Refinement],
                scope_in: Optional[List[str]] = None,
                scope_out: Optional[List[str]] = None) -> None:
        if not refinements:
            raise RefineError("a refine pass must propose at least one refinement")
        # Prioritized: keep the caller's order but expose it sorted by priority too.
        self.proposed = list(refinements)
        self.scope_in = list(scope_in or [])
        self.scope_out = list(scope_out or [])

    def approve(self, selected_titles: List[str], approver: str = "user") -> None:
        """Approve a SCOPED subset of the proposed refinements (the user chooses scope,
        not just yes/no). Must select at least one proposed refinement."""
        if not self.proposed:
            raise RefineError("nothing has been proposed yet to approve")
        chosen = [r for r in self.proposed if r.title in set(selected_titles)]
        if not chosen:
            raise RefineError(
                "approval must select at least one proposed refinement by title")
        self.approved_set = chosen
        self.approved = True
        self.approver = approver
        self.approved_at = _now_iso()

    @property
    def can_emit_spec(self) -> bool:
        return self.approved

    def handoff(self) -> RefinementPlan:
        """Produce the approved plan for `spec`. HARD-GATE: refuses until approved."""
        if not self.approved or not self.approved_set:
            raise RefineGateError(
                "HARD-GATE: no spec, no hand-off until the user explicitly approves a "
                "scoped set of refinements. If you are unsure whether approval was given, "
                "it was not."
            )
        return RefinementPlan(
            target=self.target,
            refinements=self.approved_set,
            scope_in=self.scope_in,
            scope_out=self.scope_out,
            grounding=self.grounding,
            approver=self.approver or "unknown",
            approved_at=self.approved_at or _now_iso(),
        )


def ground_refine(router: Optional[Any]) -> Grounding:
    """Ground a refine pass in what's available right now — the codebase graph
    (callers/callees/imports/blast_radius) and memory (prior decisions/conventions).
    Reuses the brainstorm grounding primitive; never raises, degrades cleanly."""
    return ground(router)


def persist_refinements(session: RefineSession, store: Any) -> str:
    """Persist the approved scoped set via a StateStore. Calls `handoff()`, so the
    HARD-GATE is enforced here too — an unapproved session cannot be written."""
    return store.write(REFINE_STATE_KEY, session.handoff().to_dict())


def load_approved_refinements(store: Any) -> Optional[RefinementPlan]:
    """Retrieve the persisted approved refinement plan (the downstream constraint)."""
    data = store.read(REFINE_STATE_KEY)
    return RefinementPlan.from_dict(data) if data else None


# --------------------------------------------------------------- clean-room prompt
# The agent-facing protocol. Clean-room: mokata's own words — a single hard gate, deep
# user-steerable review, prioritized proposals, and frugal graph/memory grounding.
REFINE_PROTOCOL = """\
You are running mokata's REFINE phase — a deep, comprehensive review of code the user
ALREADY has, to propose concrete improvements. This is for EXISTING code (brainstorm is for
new problems). You are NOT writing a spec or code yet; you produce an approved set of
refinements and hand off to the `spec` skill.

## 1. Ground in the real code (don't guess, don't file-dump)

Use the codebase graph for structure — callers, callees, imports, and blast_radius of the
target — and memory for prior decisions/conventions. Read ONLY the code the user points at;
pull related context through the graph + memory, not by pasting the repo. If the graph or
memory is absent, read/grep the target and state your structural assumptions. Depth comes
from better grounding, not from spending more tokens.

## 2. Deep, comprehensive review (the default is thorough)

Review across ALL dimensions unless the user narrows it: architecture & boundaries, design
patterns and anti-patterns, CS best practices, code quality/readability, testability,
coupling & cohesion, error handling, security, and performance.

## 2a. Honor user-steerable scope

The invocation may include free-form guidance (via $ARGUMENTS) to include, exclude, or focus
— e.g. "focus on the auth module and security", "exclude performance", "only the public API".
State up front, in one line, which dimensions/areas are IN and OUT of scope for this run.
With no guidance, do the full in-depth review.

## 3. Propose changes as a PRIORITIZED list

For each proposed refinement give: the change, its rationale, the principle it serves, the
tradeoff/cost, and a behavior-impact note (behavior-PRESERVING vs behavior-CHANGING). Order
by priority. Surface a prioritized summary first; expand a dimension on demand rather than
emitting an exhaustive wall.

## 4. Offer 2-3 coherent directions

Where refinement directions genuinely differ (e.g. "minimal cleanup" vs "restructure the
boundary"), present 2-3 coherent options — not one strawman flanked by foils — so the user
chooses SCOPE, not just yes/no.

## The one hard gate

HARD-GATE: do NOT draft a spec, write code, or hand off until the user EXPLICITLY approves a
SCOPED SET of refinements. No approval, no spec. This gate cannot be skipped, softened, or
assumed. If you are unsure whether approval was given, it was not.

## Hand off (reuse, don't reinvent)

Once a scoped set is approved, persist it and HAND OFF to the existing `spec` skill — refine
does NOT write the spec itself. `spec` turns the approved changes into acceptance criteria,
INCLUDING "behavior preserved" criteria for any behavior-preserving refinement, so the
completeness gate requires CHARACTERIZATION tests (written RED, before the change) that pin
current behavior. Then the unchanged flow runs: spec → completeness gate → test (RED) →
develop (GREEN) → review. Behavior-preserving by default; structural changes are pinned by
tests written before the change.

## Stick to the approved set

Once a scoped set is approved, implement ONLY that set — do not broaden it. If a needed change
falls outside the approved refinements (a new refinement appears, or one turns out wrong or
infeasible), STOP and get EXPLICIT approval — re-approve an expanded/amended set before
proceeding. Never silently broaden scope or change the approved direction; a plan change is a
durable change, so it is human-gated and audited.
"""
