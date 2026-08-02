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
# TM.S11a — the two pre-spec decision lenses (blast radius + architectural fit). Pure/injected —
# the engine holds + gates on them; the computation lives in brainstorm_impact.py (doc 63 §4, P21).
from .errors import MokataError
from .brainstorm_impact import (
    ApproachImpact,
    DesignFitVerdict,
    compute_impact,
    deep_review_offer,
    render_design_fits,
    render_impacts,
)
# GR-PA — the prior-art step (existing-implementations query + related-decision recall) the engine
# holds + gates on before approval; the pure computation + render live in prior_art.py (P22).
from .prior_art import PriorArtResult, render_prior_art, run_prior_art

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

# Stage 54g — anti-drift bounds. The running synthesis is a COMPACT state of the
# exploration, re-surfaced every turn — never a transcript dump. So it is hard-capped:
# at most this many constraints / approaches, each line clipped to this length.
MAX_SYNTHESIS_ITEMS = 6
MAX_SYNTHESIS_LINE = 200


def _clip_line(text: Any) -> str:
    """Collapse to a single bounded line — frugal, deterministic, transcript-proof."""
    s = " ".join(str(text or "").split())
    if len(s) > MAX_SYNTHESIS_LINE:
        s = s[: MAX_SYNTHESIS_LINE - 1].rstrip() + "…"
    return s


class BrainstormError(MokataError):
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


# --------------------------------------------------------------------------- AP-SD: decisions[]
@dataclass
class DecisionDeferral:
    """AP-SD — ONE thing a decision explicitly DEFERS, and how a hook recognises it landing anyway.

    This is the ONE-truth source of a spec-scope `DeferredItem`: `paths` catches the deferred work
    landing in its own module, `markers` catches it inside an otherwise-authorized one (the SI-DEV
    incident shape). PATH globs + LITERAL markers only — the tokens the human named — never a
    semantic judgement of what a diff means (that is the SI-DEV boundary; no hook can decide it)."""

    id: str
    item: str = ""
    paths: List[str] = field(default_factory=list)
    markers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "item": self.item,
                "paths": list(self.paths), "markers": list(self.markers)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionDeferral":
        return cls(id=str(d.get("id", "")), item=str(d.get("item", "")),
                   paths=list(d.get("paths", [])), markers=list(d.get("markers", [])))


@dataclass
class Decision:
    """AP-SD — a recorded, machine-readable design decision on the approved approach (doc 85 §3:
    a `*Decision` is a recorded human choice; P2 — what the human approved becomes machine-readable).

    The model PROPOSES a decision during brainstorm; it persists ONLY through the existing gated
    approval flow (no new write path). Once durable it is the CONTRACT the pipeline verifies against:
    `about_code` anchors are what review's pass-1 checks the diff's blast radius against (GR.S2(m))
    and what prior-art recall enriches from (GR-PA); `deferred` is what the spec's scope section
    derives at emit. The shape (`id`/`statement`/`about_code`) is exactly what both dormant hooks
    duck-type over, so a `Decision` (or its `to_dict`) drops straight into them."""

    id: str
    statement: str = ""
    rationale_ref: str = ""
    about_code: List[str] = field(default_factory=list)
    deferred: List[DecisionDeferral] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "statement": self.statement,
                "rationale_ref": self.rationale_ref,
                "about_code": list(self.about_code),
                "deferred": [d.to_dict() for d in self.deferred]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Decision":
        return cls(
            id=str(d.get("id", "")),
            statement=str(d.get("statement", "")),
            rationale_ref=str(d.get("rationale_ref", "")),
            about_code=list(d.get("about_code", [])),
            deferred=[DecisionDeferral.from_dict(x) for x in d.get("deferred", [])],
        )


@dataclass
class Approach:
    name: str
    summary: str
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    # TM.S11a — the code symbols/files this approach would TOUCH (the model names them). They seed
    # Lens 1 (blast radius): its impact is computed over these. Empty = the impact degrades to the
    # about_code intersection only (still scores). Not a tradeoff input — pure impact seed.
    targets: List[str] = field(default_factory=list)
    # AP-SD — the machine-readable decisions[] block (additive; empty on a pre-AP-SD approach, which
    # is then byte-identical to today). Recorded via `BrainstormSession.propose_decision` and
    # persisted through the existing approval flow. `schema_version` stamps the approach record for
    # future evolution (the additive-field convention: fields are added without bumping it).
    decisions: List[Decision] = field(default_factory=list)
    schema_version: int = 1

    @property
    def has_tradeoff(self) -> bool:
        return bool(self.pros) and bool(self.cons)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "summary": self.summary,
                "pros": list(self.pros), "cons": list(self.cons),
                "targets": list(self.targets),
                "decisions": [d.to_dict() for d in self.decisions],
                "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Approach":
        return cls(name=d["name"], summary=d.get("summary", ""),
                   pros=list(d.get("pros", [])), cons=list(d.get("cons", [])),
                   targets=list(d.get("targets", [])),
                   decisions=[Decision.from_dict(x) for x in d.get("decisions", [])],
                   schema_version=int(d.get("schema_version", 1)))


@dataclass
class Synthesis:
    """Stage 54g — a COMPACT running synthesis of the exploration so far: the goal, the
    constraints decided, the approaches on the table, and the current open question. Updated
    each turn and re-surfaced (with the anchor) so a long brainstorm never loses the thread.

    It is BOUNDED by construction — `__post_init__` clips every line and caps the lists — so
    no caller (or restored dict) can turn it into a transcript dump."""

    goal: str = ""
    constraints: List[str] = field(default_factory=list)
    approaches: List[str] = field(default_factory=list)
    open_question: str = ""

    def __post_init__(self) -> None:
        self.goal = _clip_line(self.goal)
        self.open_question = _clip_line(self.open_question)
        self.constraints = [_clip_line(c) for c in list(self.constraints)[:MAX_SYNTHESIS_ITEMS]
                            if str(c).strip()]
        self.approaches = [_clip_line(a) for a in list(self.approaches)[:MAX_SYNTHESIS_ITEMS]
                           if str(a).strip()]

    @property
    def is_empty(self) -> bool:
        return not (self.goal or self.constraints or self.approaches or self.open_question)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": list(self.constraints),
            "approaches": list(self.approaches),
            "open_question": self.open_question,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Synthesis":
        # Construction re-applies the bounds, so a hand-crafted/oversized dict is still clamped.
        return cls(
            goal=d.get("goal", ""),
            constraints=list(d.get("constraints", [])),
            approaches=list(d.get("approaches", [])),
            open_question=d.get("open_question", ""),
        )


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
    # TM.S11a — the chosen approach's two decision lenses, carried into the hand-off so they become
    # DOWNSTREAM SPEC CONSTRAINTS (the spec/pre-mortem/completeness gate inherit the impact +
    # design-fit the approach was approved under). None only on a legacy pre-S11a hand-off.
    impact: Optional[ApproachImpact] = None
    design_fit: Optional[DesignFitVerdict] = None
    # GR-PA — the chosen approach's prior-art evidence, carried into the hand-off so the "extend,
    # don't re-implement" finding + the step-ran record are durable + reviewable. None on a legacy
    # pre-GR-PA hand-off.
    prior_art: Optional[PriorArtResult] = None
    # DK.S0 — the domains-in-play, classified from the chosen approach's graph surface (blast
    # radius + roles), carried into the hand-off so they become a first-class SPEC CONSTRAINT
    # (persisted into emitted_spec.json). Empty on a legacy pre-DK.S0 hand-off.
    domains: List[str] = field(default_factory=list)
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
            "impact": self.impact.to_dict() if self.impact else None,
            "design_fit": self.design_fit.to_dict() if self.design_fit else None,
            "prior_art": self.prior_art.to_dict() if self.prior_art else None,
            "domains": list(self.domains),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Handoff":
        imp = d.get("impact")
        fit = d.get("design_fit")
        pa = d.get("prior_art")
        return cls(
            topic=d["topic"],
            approach=Approach.from_dict(d["approach"]),
            answered_questions=[Question.from_dict(q)
                                for q in d.get("answered_questions", [])],
            grounding=Grounding.from_dict(d.get("grounding", {})),
            approver=d.get("approver", "unknown"),
            approved_at=d.get("approved_at", ""),
            impact=ApproachImpact.from_dict(imp) if imp else None,
            design_fit=DesignFitVerdict.from_dict(fit) if fit else None,
            prior_art=PriorArtResult.from_dict(pa) if pa else None,
            domains=list(d.get("domains", [])),
            schema_version=int(d.get("schema_version", 1)),
        )


# --------------------------------------------------------------------------- session
class BrainstormSession:
    def __init__(self, topic: str, grounding: Optional[Grounding] = None,
                 anchor: Optional[str] = None) -> None:
        self.topic = topic
        self.grounding = grounding or Grounding(
            False, None, False, None,
            ["no grounding sources wired; explore from first principles and ask"],
        )
        self.questions: List[Question] = []
        self.approaches: List[Approach] = []
        # TM.S11a — the two pre-spec decision lenses, per approach name. `impacts` = Lens 1 (blast
        # radius, computed); `design_fit` = Lens 2 (architectural-fit verdict, model-supplied). The
        # HARD-GATE (`approve`) refuses until BOTH are on the table for the chosen approach (P21).
        self.impacts: Dict[str, ApproachImpact] = {}
        self.design_fit: Dict[str, DesignFitVerdict] = {}
        # GR-PA — the prior-art evidence per approach name (existing implementations + related
        # decisions). Recorded by `assess_prior_art`; the step-RAN gate reads `ran` off it.
        self.prior_art: Dict[str, PriorArtResult] = {}
        self.chosen: Optional[Approach] = None
        self.approved: bool = False
        self.approver: Optional[str] = None
        self.approved_at: Optional[str] = None
        self.events: List[str] = []
        # Stage 54g — the IMMUTABLE anchor (the original ask/goal) + the compact running
        # synthesis. The anchor is set ONCE and never mutated by later turns or by restore;
        # the synthesis is bounded and updated each turn. See `set_anchor` / `update_synthesis`.
        self._anchor: Optional[str] = _clip_line(anchor) if anchor else None
        self.synthesis: Optional[Synthesis] = None

    def _log(self, msg: str) -> None:
        self.events.append(msg)

    # --- Stage 54g: the immutable anchor + the bounded running synthesis ----
    @property
    def anchor(self) -> Optional[str]:
        """The original ask/goal, captured once at brainstorm start. Read-only — there is no
        setter, so later turns and restore can never mutate it."""
        return self._anchor

    def set_anchor(self, text: str) -> Optional[str]:
        """Record the original ask ONCE. Set-once: if an anchor already exists this is a no-op
        (the original wins) — a later turn cannot overwrite the thread the user came with."""
        if self._anchor is None and str(text or "").strip():
            self._anchor = _clip_line(text)
            self._log(f"anchor: {self._anchor}")
        return self._anchor

    def update_synthesis(self, goal: Optional[str] = None,
                         constraints: Optional[List[str]] = None,
                         approaches: Optional[List[str]] = None,
                         open_question: Optional[str] = None) -> Synthesis:
        """Update the compact running synthesis. Only the fields passed are replaced; the
        result is re-bounded on every update (never a transcript dump)."""
        cur = self.synthesis or Synthesis()
        self.synthesis = Synthesis(
            goal=cur.goal if goal is None else goal,
            constraints=cur.constraints if constraints is None else constraints,
            approaches=cur.approaches if approaches is None else approaches,
            open_question=cur.open_question if open_question is None else open_question,
        )
        self._log("synthesis updated")
        return self.synthesis

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

    # --- AP-SD: propose a structured decision onto an approach (the capture path) ------------
    def propose_decision(self, approach_name: str, decision: "Decision") -> "Decision":
        """Record a PROPOSED machine-readable decision on the named approach (P2 — the model
        proposes; nothing is written here). It becomes durable only when the approved approach is
        persisted through the existing approval flow (`persist_approach`) — no new write path."""
        a = next((a for a in self.approaches if a.name == approach_name), None)
        if a is None:
            raise BrainstormError(
                f"no approach named '{approach_name}'; propose decisions onto one of "
                f"{[a.name for a in self.approaches]}")
        a.decisions.append(decision)
        self._log(f"propose decision: {approach_name}/{decision.id}")
        return decision

    # --- TM.S11a: the two pre-spec decision lenses (blast radius + architectural fit) --------
    def assess_impacts(self, layer: Any = None, memory_items: Any = None,
                       depth: int = 2) -> Dict[str, "ApproachImpact"]:
        """LENS 1 — compute + record the blast-radius impact for EVERY proposed approach, over each
        approach's `targets`, unioned with the `about_code` memory decisions it touches. Pure over
        the injected `layer` (duck-typed `.blast_radius`, None → grep/heuristic degrade) + memory
        items. Idempotent — safe to re-run as targets firm up. Returns the per-approach map."""
        for a in self.approaches:
            self.impacts[a.name] = compute_impact(
                a.name, a.targets, layer=layer, memory_items=memory_items, depth=depth)
        self._log(f"assess impacts: {sorted(self.impacts)}")
        return self.impacts

    def record_impact(self, approach_name: str, impact: "ApproachImpact") -> None:
        """Record a pre-computed Lens-1 impact for one approach (when the caller computed it)."""
        self.impacts[approach_name] = impact
        self._log(f"impact recorded: {approach_name}")

    # --- GR-PA: the prior-art step (existing implementations + related decisions) --------------
    def assess_prior_art(self, *, layer: Any = None,
                         recall: Any = None, memory_store: Any = None,
                         top_n: int = 5, decisions: Any = None) -> Dict[str, "PriorArtResult"]:
        """Run the bounded prior-art pass for EVERY proposed approach, over each approach's `targets`,
        and record the evidence in run state. The graph query uses the injected `layer` (semantic tier
        when a CRG semantic graph is adopted, structural/lexical otherwise — the tier is named
        honestly); related decisions come from `recall` (a `query -> items` callable) or, when a
        `memory_store` is given instead, its EXISTING `recall_relevant` channel (no second channel —
        GR.S2 boundary). Evidence-gathering: it always runs; empty findings are a first-class outcome.

        `decisions` is the dormant AP-SD hook (GR.S2(m) precedent): None = recall-only; a structured
        `decisions[]` activates about_code enrichment. Returns the per-approach map."""
        rec = recall
        epoch = ""
        if memory_store is not None:
            # DB.S7c2 STALE-REF — read the minting store's `index_epoch` ONCE, here, and stamp it
            # onto every citation this pass records. One read, then N comparisons at approve time;
            # `read_index_epoch` answers OFF on the SQLite floor, which leaves citations un-stamped.
            from .memory.staleness import read_index_epoch
            epoch = read_index_epoch(getattr(memory_store, "backend", None))
        if rec is None and memory_store is not None:
            # the SAME tiered recall channel the rest of brainstorm uses — never a second surface.
            rec = lambda q: [getattr(h, "item", h)                       # noqa: E731
                             for h in memory_store.recall_relevant(q, top_k=top_n)]
        for a in self.approaches:
            # AP-SD wake — when the caller supplies no explicit `decisions`, each approach's OWN
            # decisions[] enrich its recall (source == "decisions[]"). An approach with no decisions
            # passes None, so the hook stays dormant and the pass is byte-identical to pre-AP-SD.
            dec = decisions
            if dec is None and a.decisions:
                dec = a.decisions
            self.prior_art[a.name] = run_prior_art(
                a.name, a.targets, layer=layer, recall=rec, query=a.summary,
                top_n=top_n, decisions=dec, index_epoch=epoch)
        self._log(f"assess prior art: {sorted(self.prior_art)}")
        return self.prior_art

    def record_prior_art(self, approach_name: str, result: "PriorArtResult") -> None:
        """Record a pre-computed prior-art result for one approach (when the caller ran the step)."""
        self.prior_art[approach_name] = result
        self._log(f"prior art recorded: {approach_name}")

    def prior_art_ran(self, approach_name: str) -> bool:
        """True only when the prior-art step has run for `approach_name` — the condition the bound
        step requires before an approach can be approved (never that it found anything)."""
        return bool(getattr(self.prior_art.get(approach_name), "ran", False))

    def assess_doc_freshness(self, approach_name: str, *, root: Any = ".",
                             facts: Any = None, resolve: Any = None) -> Any:
        """DK.S5 — Lens 1 doc-freshness: for the docs the approach's blast radius touches (its
        impacted files + touched symbols), audit each via docsync and mark fresh / stale /
        new-doc-needed. Advisory + human-gated (docsync's reconcile is previewed + approved) — a
        stale doc left unaddressed carries into the spec as an open item. Returns the per-doc list;
        empty when the approach has no computed impact. Degrade-clean: docsync audits lexically when
        no code graph is wired."""
        from .docsync import assess_doc_freshness as _assess
        impact = self.impacts.get(approach_name)
        if impact is None:
            return []
        touched_files = list(impact.impacted_files) + list(impact.targets)
        results = _assess(touched_files, root=root, touched_symbols=impact.impacted_symbols,
                          facts=facts, resolve=resolve)
        self._log(f"doc freshness: {approach_name} → "
                  f"{sum(1 for r in results if r.stale)} stale of {len(results)}")
        return results

    def record_design_fit(self, approach_name: str, verdict: "DesignFitVerdict") -> None:
        """LENS 2 — record the model's architectural-fit VERDICT for one approach. It must be VALID
        (a known verdict; a risk/misfit must name ≥1 concrete risk) — a hand-waved flag is refused
        so the gate can't be satisfied with an empty verdict (fail-closed)."""
        if not isinstance(verdict, DesignFitVerdict) or not verdict.valid:
            raise BrainstormError(
                f"design-fit verdict for '{approach_name}' is not on the table — it must be one of "
                "fits/risk/misfit, and a risk/misfit must name at least one boundary/layering/"
                "ownership risk")
        self.design_fit[approach_name] = verdict
        self._log(f"design-fit recorded: {approach_name} = {verdict.verdict}")

    def lenses_ready(self, approach_name: str) -> bool:
        """True only when BOTH lenses are on the table for `approach_name` — the condition the
        HARD-GATE requires before an approach can be approved (doc 63 §4 / P21)."""
        return approach_name in self.impacts and approach_name in self.design_fit

    def missing_lenses(self, approach_name: str) -> List[str]:
        """Which lens(es) are NOT yet on the table for `approach_name` (for a legible gate refusal)."""
        miss = []
        if approach_name not in self.impacts:
            miss.append("blast radius (Lens 1)")
        if approach_name not in self.design_fit:
            miss.append("architectural fit (Lens 2)")
        return miss

    def deep_review_offer(self) -> Optional[str]:
        """Over a complexity/impact threshold, the OFFER of the deep whole-codebase review (R1.S4e,
        user-invoked, 0.2.0) — never auto-run. None when under threshold. Read-only."""
        return deep_review_offer(list(self.impacts.values()), list(self.design_fit.values()))

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
        # TM.S11a — the two pre-spec decision lenses, RECORDED in the plan file so the approval's
        # impact + design-fit are durable + reviewable, and carry into the spec as constraints (P21).
        if self.impacts or self.design_fit:
            lines.append("## Decision inputs (pre-spec — blast radius + architectural fit)")
            lines.append(render_impacts([self.impacts[a.name] for a in self.approaches
                                         if a.name in self.impacts]))
            lines.append(render_design_fits([self.design_fit[a.name] for a in self.approaches
                                             if a.name in self.design_fit]))
            # GR-PA — the prior-art row beside blast radius + design fit: existing implementations
            # ("extend, don't re-implement") + related decisions, mokata-rendered, per approach.
            if self.prior_art:
                lines.append(render_prior_art([self.prior_art[a.name] for a in self.approaches
                                               if a.name in self.prior_art]))
            offer = self.deep_review_offer()
            if offer:
                lines.append(f"· Deep review: {offer}")
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
                at: Optional[str] = None, *, graph_gate: Any = None,
                prior_art_gate: Any = None, stale_ref_gate: Any = None,
                code_anchor_gate: Any = None) -> Approach:
        """Explicitly approve one approach. This is the human gate the whole phase
        turns on; nothing downstream proceeds without it.

        GR.S3 — `graph_gate` is the `graph.required` verdict for the chosen approach's Lens-1 blast
        radius (a `GraphRequiredOutcome`, computed by the caller via
        `govern.graph_required.brainstorm_impact_gate`). When it REFUSES — the radius is degraded,
        `graph.required` is on, and the session has no ledgered override — the approval is blocked
        with the informative refusal, exactly as the CLI and the MCP loop enforce it. Absent (the
        `graph.required=false` path, or a legacy caller) it is a no-op — byte-identical."""
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
        # TM.S11a HARD-GATE — no approach is approved until BOTH decision lenses are on the table
        # for it: the blast radius (Lens 1) AND the architectural-fit verdict (Lens 2). This is the
        # P21 gate — correctness is designed in, so the impact + design fit are weighed BEFORE the
        # spec, not discovered after code. Fail-closed: a missing lens refuses the approval.
        if not self.lenses_ready(approach_name):
            missing = " and ".join(self.missing_lenses(approach_name))
            raise BrainstormGateError(
                f"HARD-GATE: cannot approve '{approach_name}' — {missing} not yet on the table. "
                "Both pre-spec decision lenses (blast radius + architectural fit) must be shown "
                "for the chosen approach before it can be approved (P21)."
            )
        # GR.S3 HARD-GATE — a DEGRADED blast radius is not a decision input. When `graph.required`
        # is on (default) and the chosen approach's Lens-1 radius fell to the lexical floor, the
        # approval is REFUSED unless a human has ledgered an `--allow-degraded` override for this
        # session. The refusal is informative + actionable (never a stack trace); the escape keeps
        # the degraded marking (honesty over convenience, P22).
        if graph_gate is not None and getattr(graph_gate, "refused", False):
            raise BrainstormGateError(graph_gate.render())
        # GR-PA BOUND STEP — the prior-art pass must have RUN for this approach before it can be
        # approved (a step-RAN check, not a graph-quality gate; distinct gate id per doc 85 §3). When
        # a `prior_art_gate` is supplied and the step did not run, the approval is refused with the
        # informative message. Absent (a legacy caller / the graph.required=false path) it is a no-op
        # — byte-identical. A degraded/absent graph is NEVER refused here: the step still ran (GR.S3
        # owns the degraded-radius refusal; this stage adds no duplicate refusal semantics).
        if prior_art_gate is not None and getattr(prior_art_gate, "refused", False):
            raise BrainstormGateError(prior_art_gate.render())
        # DB.S7c2 STALE-REF — the chosen approach's prior-art CITATIONS must not have been minted
        # against an older memory index (a `StaleRefOutcome`, computed by the caller via
        # `govern.stale_ref_gate.brainstorm_stale_ref_gate`). Distinct from GR-PA above: that one
        # asks whether the step ran, this one whether what it found is still current. Absent — a
        # legacy caller, or the SQLite floor where STALE-REF is OFF — it is a no-op, byte-identical.
        if stale_ref_gate is not None and getattr(stale_ref_gate, "refused", False):
            raise BrainstormGateError(stale_ref_gate.render())
        # H-6 S4 STALE-REF, the CODE-ANCHOR half — the chosen approach's prior-art citations must
        # not be anchored to code that has MOVED since those decisions were recorded (a
        # `CodeAnchorOutcome`, computed by the caller via
        # `govern.code_anchor_gate.brainstorm_code_anchor_gate`). Its own id and its own message,
        # distinct from the memory-handle half above: that one asks whether the memory INDEX moved
        # under a citation, this one whether the CODE did. Absent — a legacy caller, or a repo with
        # no recorded anchors — it is a no-op, byte-identical.
        if code_anchor_gate is not None and getattr(code_anchor_gate, "refused", False):
            raise BrainstormGateError(code_anchor_gate.render())
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
            # TM.S11a — the chosen approach's lenses ride into the hand-off as spec constraints.
            impact=self.impacts.get(self.chosen.name),
            design_fit=self.design_fit.get(self.chosen.name),
            # GR-PA — the chosen approach's prior-art evidence rides along too.
            prior_art=self.prior_art.get(self.chosen.name),
        )

    # --- mid-brainstorm checkpoint (Stage 50): save/restore an IN-PROGRESS session --
    def to_dict(self) -> Dict[str, Any]:
        """Serialize the WHOLE session — answered questions + the candidate approaches being
        weighed, not just an approved one — so a brainstorm can be left mid-stream and resumed."""
        return {
            "topic": self.topic,
            "anchor": self._anchor,
            "synthesis": self.synthesis.to_dict() if self.synthesis else None,
            "grounding": self.grounding.to_dict(),
            "questions": [q.to_dict() for q in self.questions],
            "approaches": [a.to_dict() for a in self.approaches],
            # TM.S11a — persist both lenses so a resumed/persisted brainstorm keeps its decision
            # inputs (and the gate stays satisfied on restore).
            "impacts": {k: v.to_dict() for k, v in self.impacts.items()},
            "design_fit": {k: v.to_dict() for k, v in self.design_fit.items()},
            # GR-PA — persist prior-art evidence so a resumed/persisted brainstorm keeps its
            # step-ran record (the bound step stays satisfied on restore).
            "prior_art": {k: v.to_dict() for k, v in self.prior_art.items()},
            "chosen": self.chosen.name if self.chosen else None,
            "approved": self.approved,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "events": list(self.events),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BrainstormSession":
        # The anchor is restored straight into the immutable slot — it is NEVER re-derived or
        # mutated, so a resumed brainstorm keeps the exact original ask it started with.
        s = cls(d["topic"], grounding=Grounding.from_dict(d.get("grounding", {})),
                anchor=d.get("anchor"))
        syn = d.get("synthesis")
        s.synthesis = Synthesis.from_dict(syn) if syn else None
        s.questions = [Question.from_dict(q) for q in d.get("questions", [])]
        s.approaches = [Approach.from_dict(a) for a in d.get("approaches", [])]
        # TM.S11a — restore both lenses straight into their slots (no re-derivation).
        s.impacts = {k: ApproachImpact.from_dict(v)
                     for k, v in (d.get("impacts", {}) or {}).items()}
        s.design_fit = {k: DesignFitVerdict.from_dict(v)
                        for k, v in (d.get("design_fit", {}) or {}).items()}
        # GR-PA — restore prior-art evidence straight into its slot (no re-derivation).
        s.prior_art = {k: PriorArtResult.from_dict(v)
                       for k, v in (d.get("prior_art", {}) or {}).items()}
        name = d.get("chosen")
        s.chosen = next((a for a in s.approaches if a.name == name), None) if name else None
        s.approved = bool(d.get("approved", False))
        s.approver = d.get("approver")
        s.approved_at = d.get("approved_at")
        s.events = list(d.get("events", []))
        return s


# ------------------------------------------------------ Stage 54g: the anchor brief
def build_anchor_brief(session: BrainstormSession) -> str:
    """Render the compact anchor + running-synthesis block to re-surface every turn.

    Pure and deterministic; frugal/bounded (the synthesis is hard-capped, so this can't grow
    into a transcript). Degrade-clean: with no synthesis yet it is just the anchor line; with
    no explicit anchor it falls back to the topic. Always ends with the drift-check prompt so
    a straying turn is re-grounded against the original ask."""
    anchor = session.anchor or session.topic
    lines = ["mokata · brainstorm anchor (re-grounding)",
             f"▸ Original ask: {anchor}"]
    syn = session.synthesis
    if syn is not None and not syn.is_empty:
        if syn.goal:
            lines.append(f"· Goal: {syn.goal}")
        if syn.constraints:
            lines.append("· Decided so far: " + "; ".join(syn.constraints))
        if syn.approaches:
            lines.append("· Approaches on the table: " + "; ".join(syn.approaches))
        if syn.open_question:
            lines.append(f"· Open question: {syn.open_question}")
    lines.append(f"Drift-check: we're exploring “{anchor}” — does this turn still "
                 "serve that? If it strays, re-ground before continuing.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- persist
def save_approach_plan(session: BrainstormSession, plans_dir: str) -> Optional[str]:
    """Stage 6p — save the approved design as a durable, reviewable FILE at approval, BEFORE the
    spec. Content is the session's `design_writeup()` (the SINGLE source — topic, grounding, the
    Q&A, the approaches, and the approved one), written to `<plans_dir>/<slug>.md`.

    HARD-GATE: refuses on an unapproved session (nothing is written). DEGRADE-CLEAN: any write
    failure logs to the session and returns None — it must NEVER break the approval, whose
    run-state hand-off remains the source of truth."""
    if not session.approved or session.chosen is None:
        raise BrainstormGateError(
            "HARD-GATE: no plan file until an approach is explicitly approved."
        )
    from .plans import plan_slug, write_plan_file
    slug = plan_slug(session.topic)
    path = write_plan_file(plans_dir, slug, session.design_writeup())
    session._log(f"plan saved: {path}" if path else "plan save FAILED (continuing)")
    return path


def persist_approach(session: BrainstormSession, store: Any,
                     plans_dir: Optional[str] = None) -> str:
    """Persist the approved approach via a StateStore. Calls `handoff()`, so the
    HARD-GATE is enforced here too — an unapproved session cannot be written.

    Stage 6p — when `plans_dir` is given, ALSO save the plan as a durable file (see
    `save_approach_plan`) BEFORE the spec pipeline. That write is degrade-clean: a failure never
    breaks the approval (the run-state hand-off this returns is still authoritative)."""
    handoff = session.handoff()
    path = store.write(APPROACH_STATE_KEY, handoff.to_dict())
    if plans_dir is not None:
        save_approach_plan(session, plans_dir)
    return path


def load_approved_approach(store: Any) -> Optional[Handoff]:
    """Retrieve the persisted approved approach (the downstream constraint), or None."""
    data = store.read(APPROACH_STATE_KEY)
    return Handoff.from_dict(data) if data else None


def load_decisions(store: Any) -> List[Decision]:
    """AP-SD — the persisted approved approach's `decisions[]`, or [] when none (no approach, or a
    pre-AP-SD approach that carries no block). This is the NAMED BRIDGE the review-graph docstring
    anticipates: a review with a change passes `decisions=load_decisions(store)` and pass-1 lights
    up; prior-art recall reads the same source. Degrade-clean — never raises."""
    handoff = load_approved_approach(store)
    return list(handoff.approach.decisions) if handoff is not None else []


# --------------------------------------------------- mid-brainstorm checkpoint (Stage 50)
# An IN-PROGRESS session (answered questions + candidate approaches, NOT just the approved
# one) so a user can leave a brainstorm at any step and come back. This is resume state, NOT
# a durable approval — the HARD-GATE still holds: restoring never marks anything approved.
BRAINSTORM_PROGRESS_KEY = "brainstorm_progress"


def save_brainstorm_progress(session: BrainstormSession, store: Any) -> str:
    """Persist the in-progress session so it can be resumed. An explicit, intentional save
    (the approval HARD-GATE is unaffected — this stores progress, not a decision)."""
    return store.write(BRAINSTORM_PROGRESS_KEY, session.to_dict())


def restore_brainstorm_progress(store: Any) -> Optional[BrainstormSession]:
    """Re-hydrate a saved in-progress session, or None if there is none."""
    data = store.read(BRAINSTORM_PROGRESS_KEY)
    return BrainstormSession.from_dict(data) if data else None


def clear_brainstorm_progress(store: Any) -> bool:
    """Drop the saved in-progress session (e.g. once an approach is approved)."""
    return store.delete(BRAINSTORM_PROGRESS_KEY)


# ----------------------------------------------------- Stage 29: auto-engage (toggle)
# The autonomous-trigger description (clean-room, mokata's own words) that makes Claude Code
# model-INVOKE brainstorm when the user is exploring — not only on /mokata:brainstorm. Shipped
# in the command frontmatter's `when_to_use`. Phrased to fire on EXPLORATION, not on direct
# commands or mid-implementation, so it's proactive without hijacking.
BRAINSTORM_AUTO_TRIGGER = (
    "Engage when the user is exploring an approach, weighing options or trade-offs, or "
    "describing a NEW problem/feature before any implementation — i.e. thinking through "
    "*what* and *how* before code exists. Do NOT engage for direct commands, edits to "
    "existing code, or work already mid-implementation."
)

# settings.brainstorm.auto = on | off | ask  (default "on").
BRAINSTORM_SETTINGS_KEY = "brainstorm"
AUTO_ON, AUTO_OFF, AUTO_ASK = "on", "off", "ask"
AUTO_MODES = (AUTO_ON, AUTO_OFF, AUTO_ASK)


def brainstorm_auto_mode(manifest: Any) -> str:
    """The saved auto-engage preference (Stage 29). Default 'on', easily turned off."""
    if manifest is None:
        return AUTO_ON
    try:
        s = manifest.setting(BRAINSTORM_SETTINGS_KEY, {}) or {}
    except AttributeError:
        return AUTO_ON
    val = s.get("auto", AUTO_ON) if isinstance(s, dict) else AUTO_ON
    return val if val in AUTO_MODES else AUTO_ON


@dataclass
class AutoEngageDecision:
    engage: bool          # True -> start the brainstorm conversation now
    offer: bool           # True -> mode 'ask': offer it, don't auto-start
    mode: str
    reason: str
    banner: str = ""      # the Stage 27 active-skill banner when engaging


def brainstorm_engaged_banner() -> str:
    """The Stage 27 banner announcing mokata auto-engaged brainstorm."""
    from .progress import active_banner
    return active_banner("brainstorm", state="engaged")


def decide_auto_engage(manifest: Any, exploring: bool) -> AutoEngageDecision:
    """Decide whether to auto-engage brainstorm (Stage 29). Proactive but NOT intrusive:
    engage only when the user is genuinely *exploring* (the model supplies that signal via
    the SKILL trigger), and honor settings.brainstorm.auto — 'off' never engages, 'ask'
    offers, 'on' engages. Auto-engaging only STARTS the conversation; it never bypasses the
    HARD-GATE (no spec/code until an approach is explicitly approved — P2)."""
    mode = brainstorm_auto_mode(manifest)
    if not exploring:
        return AutoEngageDecision(False, False, mode,
                                  "not exploring — don't hijack a direct task")
    if mode == AUTO_OFF:
        return AutoEngageDecision(False, False, mode,
                                  "auto-engage disabled (settings.brainstorm.auto=off)")
    if mode == AUTO_ASK:
        return AutoEngageDecision(False, True, mode,
                                  "ask first (settings.brainstorm.auto=ask)")
    return AutoEngageDecision(True, False, mode, "exploring — engaging brainstorm",
                              banner=brainstorm_engaged_banner())


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

## Two decision lenses before you approve (blast radius + architectural fit)
Before ANY approach can be approved, put BOTH pre-spec decision lenses on the table for EACH
candidate — correctness is designed IN at brainstorm, not reviewed in after code (P21):
1. Lens 1 — BLAST RADIUS (code impact): name the symbols/files each approach touches and compute
   its impact — `mokata query blast_radius <symbol>` for the transitive callers/dependents (the
   grep floor still scores when no graph is wired) UNIONED with the team decisions it disturbs
   (memory items whose `about_code` names those symbols → affected team decisions). Show
   callers/tests/docs/configs touched + affected decisions per approach, and COMPARE them.
   DOC-FRESHNESS (part of Lens 1): for the docs that blast radius touches, run docsync's audit
   (`mokata docsync <doc>`, or `mokata docsync` to sweep + drift-detect) — per approach, list the
   docs the change touches or invalidates and mark each fresh / stale / new-doc-needed. HIGHLIGHT the
   stale ones and ASK the user to update them; a stale doc left unaddressed is written into the plan
   as an OPEN item and carries into the spec (advisory + human-gated — docsync's reconcile is
   previewed and approved, never silent).
2. Lens 2 — ARCHITECTURAL FIT (design-fit review): assess each approach's module boundaries,
   layering, import direction, and ownership — grounded in the knowledge layer + memory — and give
   a NAMED verdict (fits / risk / misfit) naming the boundary/layering/ownership risks. A
   mis-layered approach is flagged HERE, before the spec. Prompt-driven, not a boundary engine.
The HARD-GATE has TWO conditions: you cannot approve an approach until BOTH its blast radius AND
its design-fit verdict are on the table. No lenses, no approval. Record both in the plan file —
they become spec constraints; the develop/CI deviation guard stays only as the backstop. If the
change looks high-impact (wide blast radius, or a design MISFIT), OFFER — do not run — the deep
whole-codebase architectural review (user-invoked). mokata offers it; the user decides.

## Prior art — extend, don't re-implement (a BOUND step before approval)
Before you approve, run the PRIOR-ART pass for the chosen approach — a bound step, not a suggestion.
Graph-query the codebase for EXISTING implementations related to the approach's symbols/terms
(`mokata query implementers <name>` / `callers <name>`; CRG semantic search when adopted; the AST
name-resolution floor or grep otherwise — name the tier honestly) AND recall the RELATED team
decisions the surface touches. Surface each finding in the tradeoff table as an "existing
`<symbol>` in `<file>` — extend?" row beside blast radius, so the human weighs reuse before the
approach is chosen. A deviation from found prior art must be STATED, not silent. This is a step-RAN
check: an empty result ("no prior art found via <tier>") is a first-class PASS — the gate is that you
LOOKED, never that you found something — and a degraded/absent graph still runs the pass (GR.S3 owns
the degraded-radius refusal; nothing new is refused here). Approving before the pass has run is
refused.

## Design pre-mortem (resolve review-class issues IN THE PLAN)
Once an approach is chosen and its blast radius + design-fit are weighed, run a short DESIGN
PRE-MORTEM before the plan is approved: anticipate the issue-classes review would otherwise raise
AFTER the code — missing edge cases, error handling, integration points, test/AC coverage gaps,
scope creep, blast-radius crossings — and resolve each HERE, in the plan. For EACH risk you
SURFACE, RECORD it in the plan as a triple: the risk + the acceptance criterion (AC) that covers
it + the check it gets. The emitted spec then carries an AC + a test for each, so the edge-case is
caught before code, not at review; a risk you can't resolve now is written into the plan as an
OPEN item, never dropped silently.

## Domains in play (classify from the surface, persist into the spec)
Once the approach is chosen and its blast radius is on the table, classify the DOMAINS it
touches — DERIVED from the graph surface it reaches, never guessed from the words of the ask.
Read the symbols/files in its blast radius and name their structural role: routes/handlers → API,
auth/input/secrets/external calls → security, components/views → frontend + a11y, a migration or a
removal → deprecation, a hot path or a perf AC → performance (and so on). The domain set is what
the SURFACE structurally touches, not what the topic string says — an approach whose ask never
says "security" but whose blast radius crosses an auth boundary IS a security change. PERSIST that
set into the spec as a FIRST-CLASS constraint, beside the ACs and the approach, so it is
human-approved and legible — the user approves the domains along with the plan. Downstream, develop
engages EXACTLY these domains and review activates EXACTLY their axes; a domain reached only later
re-enters here as a spec amendment, so a domain is never silently applied and never silently missed.

## Stay anchored (so a long brainstorm never drifts off-thread)
A long exploration loses the plot if the original ask scrolls out of view. Hold it down:
1. RECORD THE ANCHOR at the start — the user's original ask/goal, in one line. It is
   IMMUTABLE: capture it once and never rewrite it. Everything is measured against it.
2. MAINTAIN A COMPACT SYNTHESIS and RESTATE it each turn — the goal, the constraints decided
   so far, the approaches on the table, and the current open question. Keep it tight (a few
   lines): it is a running state, NOT a transcript — never replay the whole conversation.
3. DRIFT-CHECK every turn against the anchor: "we're exploring <the anchor> — does this still
   serve that?" If the turn has strayed, say so and RE-GROUND to the anchor before continuing,
   rather than following the tangent. Surfacing the anchor + synthesis each turn is what keeps
   the original topic in context no matter how long the chat runs.

## Red flags — STOP if you catch yourself thinking:
| Thought | Why it's wrong |
|---|---|
| "I already know the approach, I'll jump to the spec." | The gate is approval, not your confidence. Stop. |
| "I'll ask everything up front to save time." | One question at a time. A wall is a failure. |
| "Two of these are weak, but I'll list them as options." | Foils aren't options. Offer real, defensible alternatives. |
| "They seemed happy — that's basically approval." | Seeming happy is not approval. Ask for it explicitly. |
| "I'll approve now and check the impact/architecture later." | Both lenses are a HARD-GATE. No blast radius + design-fit, no approval. |
| "No graph/memory, so I'll assume the structure." | Absence means read/grep and state assumptions, never guess silently. |
| "We've wandered, but I'll keep following this thread." | Drift-check against the anchor and re-ground — the original ask is the thread. |
| "I'll rewrite the original ask to match where we ended up." | The anchor is immutable. Update the synthesis, never the anchor. |

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
