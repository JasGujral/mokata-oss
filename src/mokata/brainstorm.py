"""D6/D7 — Brainstorm phase: Socratic pre-spec exploration.

The brainstorm phase is the FRONT of the pipeline. Before any spec is drafted it
explores the problem *with* the user: one question at a time, then two or three real
approaches with honest tradeoffs, a digestible design write-up, and an explicit human
approval. The approved approach is persisted as a downstream constraint the later
phases (strawman / pre-mortem / probes / completeness gate) are checked against.

This module is the framework machinery: the session state-machine, the HARD-GATE that
blocks any handoff before approval, grounding detection (graph/memory present?), and the
persisted approved-approach record. The Socratic *conversation* itself is conducted by
the agent reading `BRAINSTORM_PROTOCOL` (and the shipped `/brainstorm` command), whose
clean-room prompt devices mirror — but copy none of — the strongest existing practice.

Clean-room: no dependency on or import of any external methodology framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .manifest import ManifestError

# The 7 pipeline phases, in order. Brainstorm is first; its handoff feeds the strawman.
PIPELINE_PHASES = (
    "brainstorm",
    "analysis",
    "strawman",
    "pre_mortem",
    "probes",
    "completeness_gate",
    "emit",
)

# Where the approved approach is stored (StateStore key under .mokata/state/).
APPROACH_STATE_KEY = "approved_approach"

# A real divergent exploration offers a small set of genuine alternatives — not one
# strawman flanked by foils, and not a wall of options.
MIN_APPROACHES = 2
MAX_APPROACHES = 3


class BrainstormError(Exception):
    """A brainstorm-flow rule was violated (bad question order, bad approach set…)."""


class BrainstormGateError(BrainstormError):
    """The HARD-GATE was crossed: an attempt to hand off / persist a spec before the
    approach was explicitly approved."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- domain
@dataclass
class Question:
    text: str
    rationale: str = ""
    answer: Optional[str] = None

    @property
    def answered(self) -> bool:
        return self.answer is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "rationale": self.rationale, "answer": self.answer}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Question":
        return cls(text=d["text"], rationale=d.get("rationale", ""),
                   answer=d.get("answer"))


@dataclass
class Approach:
    name: str
    summary: str
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)

    @property
    def has_tradeoff(self) -> bool:
        return bool(self.pros) and bool(self.cons)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "summary": self.summary,
                "pros": list(self.pros), "cons": list(self.cons)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Approach":
        return cls(name=d["name"], summary=d.get("summary", ""),
                   pros=list(d.get("pros", [])), cons=list(d.get("cons", [])))


@dataclass
class Grounding:
    graph_available: bool
    graph_tool: Optional[str]
    memory_available: bool
    memory_tool: Optional[str]
    notes: List[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return self.graph_available or self.memory_available

    def summary_line(self) -> str:
        g = self.graph_tool if self.graph_available else "absent"
        m = self.memory_tool if self.memory_available else "absent"
        return f"graph: {g} · memory: {m}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_available": self.graph_available,
            "graph_tool": self.graph_tool,
            "memory_available": self.memory_available,
            "memory_tool": self.memory_tool,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Grounding":
        return cls(
            graph_available=bool(d.get("graph_available")),
            graph_tool=d.get("graph_tool"),
            memory_available=bool(d.get("memory_available")),
            memory_tool=d.get("memory_tool"),
            notes=list(d.get("notes", [])),
        )


def ground(router: Optional[Any]) -> Grounding:
    """Detect what the approaches can be grounded in *right now* (D6).

    Resolves the `code_graph` and `memory_store` capabilities through the router. When a
    capability is present, approaches should lean on it; when it is absent (no provider,
    or not declared at all — e.g. the minimal profile) grounding degrades to an explicit
    instruction rather than a silent guess. Never raises.
    """
    graph_avail, graph_tool = False, None
    mem_avail, mem_tool = False, None

    if router is not None:
        for need in ("code_graph", "memory_store"):
            try:
                res = router.resolve(need)
            except ManifestError:
                res = None  # capability not declared in this manifest
            if res is not None and res.available:
                if need == "code_graph":
                    graph_avail, graph_tool = True, res.tool
                else:
                    mem_avail, mem_tool = True, res.tool

    notes: List[str] = []
    if graph_avail:
        notes.append(
            f"ground structure in the codebase graph via '{graph_tool}' "
            "(callers/callees/imports) instead of guessing"
        )
    else:
        notes.append(
            "no codebase graph available — read or grep the relevant code and state "
            "your structural assumptions explicitly"
        )
    if mem_avail:
        notes.append(
            f"check prior decisions and conventions in memory via '{mem_tool}' before "
            "proposing anything that might contradict them"
        )
    else:
        notes.append(
            "no memory store available — ask the user about prior decisions instead of "
            "assuming them"
        )
    return Grounding(graph_avail, graph_tool, mem_avail, mem_tool, notes)


# --------------------------------------------------------------------------- handoff
@dataclass
class Handoff:
    """The approved approach + the answered questions — what later phases consume, and
    what the completeness gate checks the final spec against (D7)."""

    topic: str
    approach: Approach
    answered_questions: List[Question]
    grounding: Grounding
    approver: str
    approved_at: str
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "phase": "brainstorm",
            "topic": self.topic,
            "approach": self.approach.to_dict(),
            "answered_questions": [q.to_dict() for q in self.answered_questions],
            "grounding": self.grounding.to_dict(),
            "approver": self.approver,
            "approved_at": self.approved_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Handoff":
        return cls(
            topic=d["topic"],
            approach=Approach.from_dict(d["approach"]),
            answered_questions=[Question.from_dict(q)
                                for q in d.get("answered_questions", [])],
            grounding=Grounding.from_dict(d.get("grounding", {})),
            approver=d.get("approver", "unknown"),
            approved_at=d.get("approved_at", ""),
            schema_version=int(d.get("schema_version", 1)),
        )


# --------------------------------------------------------------------------- session
class BrainstormSession:
    def __init__(self, topic: str, grounding: Optional[Grounding] = None) -> None:
        self.topic = topic
        self.grounding = grounding or Grounding(
            False, None, False, None,
            ["no grounding sources wired; explore from first principles and ask"],
        )
        self.questions: List[Question] = []
        self.approaches: List[Approach] = []
        self.chosen: Optional[Approach] = None
        self.approved: bool = False
        self.approver: Optional[str] = None
        self.approved_at: Optional[str] = None
        self.events: List[str] = []

    def _log(self, msg: str) -> None:
        self.events.append(msg)

    # --- Socratic, one question at a time -----------------------------------
    def pending_question(self) -> Optional[Question]:
        return next((q for q in self.questions if not q.answered), None)

    def ask(self, text: str, rationale: str = "") -> Question:
        if self.pending_question() is not None:
            raise BrainstormError(
                "one question at a time — answer the open question before asking the "
                "next (a wall of questions is a failure)"
            )
        q = Question(text=text, rationale=rationale)
        self.questions.append(q)
        self._log(f"ask: {text}")
        return q

    def answer(self, text: str) -> Question:
        q = self.pending_question()
        if q is None:
            raise BrainstormError("no open question to answer")
        q.answer = text
        self._log(f"answer: {text}")
        return q

    @property
    def answered_questions(self) -> List[Question]:
        return [q for q in self.questions if q.answered]

    # --- divergent approaches with real tradeoffs ---------------------------
    def propose_approaches(self, approaches: List[Approach]) -> None:
        n = len(approaches)
        if not (MIN_APPROACHES <= n <= MAX_APPROACHES):
            raise BrainstormError(
                f"put {MIN_APPROACHES}–{MAX_APPROACHES} real approaches on the table; "
                f"got {n}"
            )
        for a in approaches:
            if not a.has_tradeoff:
                raise BrainstormError(
                    f"approach '{a.name}' must state at least one pro AND one con — a "
                    "foil with no downside is not a real option"
                )
        self.approaches = list(approaches)
        self._log(f"propose: {[a.name for a in approaches]}")

    def design_writeup(self) -> str:
        """A digestible, sectioned write-up of where the exploration landed."""
        lines: List[str] = []
        lines.append(f"# Brainstorm — {self.topic}")
        lines.append("")
        lines.append(f"Grounding: {self.grounding.summary_line()}")
        lines.append("")
        if self.answered_questions:
            lines.append("## What we learned")
            for q in self.answered_questions:
                lines.append(f"- {q.text} → {q.answer}")
            lines.append("")
        lines.append("## Approaches")
        for a in self.approaches:
            lines.append(f"### {a.name}")
            lines.append(a.summary)
            for p in a.pros:
                lines.append(f"- pro: {p}")
            for c in a.cons:
                lines.append(f"- con: {c}")
            lines.append("")
        lines.append("## Decision")
        if self.approved and self.chosen is not None:
            lines.append(f"Approved: **{self.chosen.name}** (by {self.approver}).")
        else:
            lines.append(
                "No approach is approved yet. Choose one and approve it explicitly — "
                "the spec is HARD-GATED behind this decision."
            )
        return "\n".join(lines) + "\n"

    # --- the HARD-GATE ------------------------------------------------------
    def approve(self, approver: str, approach_name: str,
                at: Optional[str] = None) -> Approach:
        """Explicitly approve one approach. This is the human gate the whole phase
        turns on; nothing downstream proceeds without it."""
        if not self.approaches:
            raise BrainstormGateError(
                "cannot approve before any approaches are on the table"
            )
        chosen = next((a for a in self.approaches if a.name == approach_name), None)
        if chosen is None:
            raise BrainstormError(
                f"no approach named '{approach_name}'; choose one of "
                f"{[a.name for a in self.approaches]}"
            )
        self.chosen = chosen
        self.approved = True
        self.approver = approver
        self.approved_at = at or _now_iso()
        self._log(f"approve: {approach_name} by {approver}")
        return chosen

    @property
    def can_emit_spec(self) -> bool:
        return self.approved

    def handoff(self) -> Handoff:
        """Produce the downstream constraint. HARD-GATE: refuses until approved."""
        if not self.approved or self.chosen is None:
            raise BrainstormGateError(
                "HARD-GATE: no spec, no handoff until an approach is explicitly "
                "approved. If you are unsure whether approval was given, it was not."
            )
        return Handoff(
            topic=self.topic,
            approach=self.chosen,
            answered_questions=self.answered_questions,
            grounding=self.grounding,
            approver=self.approver or "unknown",
            approved_at=self.approved_at or _now_iso(),
        )


# --------------------------------------------------------------------------- persist
def persist_approach(session: BrainstormSession, store: Any) -> str:
    """Persist the approved approach via a StateStore. Calls `handoff()`, so the
    HARD-GATE is enforced here too — an unapproved session cannot be written."""
    handoff = session.handoff()
    return store.write(APPROACH_STATE_KEY, handoff.to_dict())


def load_approved_approach(store: Any) -> Optional[Handoff]:
    """Retrieve the persisted approved approach (the downstream constraint), or None."""
    data = store.read(APPROACH_STATE_KEY)
    return Handoff.from_dict(data) if data else None


# --------------------------------------------------------------- clean-room prompt
# The agent-facing protocol. Clean-room: mirrors the *devices* that make models behave
# (a single hard gate, one-question discipline, an anti-rationalization red-flag table,
# real-alternatives discipline, explicit approval) in mokata's own words — no copied text.
BRAINSTORM_PROTOCOL = """\
# mokata · brainstorm (pre-spec exploration)

You are running mokata's brainstorm phase — the FIRST phase, before any spec exists.
Explore the problem WITH the user until one approach is chosen and explicitly approved.
You are not writing a spec yet. You are not writing code.

## The one hard gate
HARD-GATE: do not draft a spec, write code, or hand off to the next phase until the user
has explicitly approved exactly one approach. No approval, no spec. This gate cannot be
skipped, softened, or assumed. If you are unsure whether approval was given, it was not.

## How to run the conversation
1. Ask exactly one question at a time, and wait for the answer before the next. A wall of
   questions is a failure — it ends the conversation the user came to have.
2. Spend each question on the biggest remaining unknown — the answer that most changes
   the design.
3. Ground every assumption. If a codebase graph is available, navigate by structure
   (callers, callees, imports) instead of guessing; if it is absent, read or grep the
   code and say what you assumed. If a memory store is available, check prior decisions
   and conventions first; if it is absent, ask the user.
4. When the unknowns are closed, put two or three real approaches on the table, each with
   honest tradeoffs — what it costs, what it risks, what it gives up. Not one strawman
   flanked by foils. The user chooses the direction.
5. Write the design up in digestible sections (problem, what we learned, the approaches
   and their tradeoffs, your recommendation), then ask for explicit approval of one.

## Red flags — STOP if you catch yourself thinking:
| Thought | Why it's wrong |
|---|---|
| "I already know the approach, I'll jump to the spec." | The gate is approval, not your confidence. Stop. |
| "I'll ask everything up front to save time." | One question at a time. A wall is a failure. |
| "Two of these are weak, but I'll list them as options." | Foils aren't options. Offer real, defensible alternatives. |
| "They seemed happy — that's basically approval." | Seeming happy is not approval. Ask for it explicitly. |
| "No graph/memory, so I'll assume the structure." | Absence means read/grep and state assumptions, never guess silently. |

## When approval is given
Record the approved approach and the answered questions as mokata's downstream
constraint. Everything after this — strawman, pre-mortem, probes, the completeness gate —
is checked against the approach approved here. Then hand off; do not re-ask what was
settled.
"""


def render_launch(grounding: Grounding) -> str:
    """The standalone `/brainstorm` launch text: the protocol + live grounding status."""
    lines = [BRAINSTORM_PROTOCOL, "", "## Grounding (resolved now)",
             grounding.summary_line()]
    for note in grounding.notes:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"
