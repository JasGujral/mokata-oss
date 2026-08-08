"""TM.S11a — brainstorm decision lenses (doc 62/63 §4, doc 42 R1.S4d, doc 55 K3, doc 04 P21).

Two PURE, comparable decision inputs computed per candidate approach in the SAME pre-spec pass —
so a mis-designed approach is rejected at brainstorm, not discovered after code (P21):

  * LENS 1 — BLAST RADIUS: the code impact of the approach's target symbols — the transitive
    caller/dependent surface from `KnowledgeLayer.blast_radius()` (the grep floor answers when the
    real graph is absent) UNIONED with the memory items whose `about_code` link intersects that
    surface → "affected team decisions" (doc 55 K3). Structured + comparable across approaches
    (impacted files bucketed callers/tests/docs/configs + affected-decision count).
  * LENS 2 — ARCHITECTURAL FIT: a named design-fit VERDICT (fits | risk | misfit) with the
    boundary / layering / ownership risks named. It is PROMPT-DRIVEN — the model produces it,
    grounded in the knowledge layer (module structure, import direction) + memory (ownership,
    prior decisions). This module only HOLDS + VALIDATES the verdict; there is NO hard-coded
    boundary engine (deriving one is out of scope — the full typed-edge arch model is 0.1.3, and
    the deep whole-codebase review stays user-invoked R1.S4e).

Both DEGRADE CLEAN: no graph → grep/heuristic still scores; no memory → no affected decisions,
never a crash. This module is PURE (no I/O): the caller injects a `layer` (duck-typed
`.blast_radius(sym, depth)`) + the memory items; this module computes the comparable report.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------- file classification (buckets)
# The impact surface is bucketed so two approaches compare on WHAT they touch, not just how much
# (callers/tests/docs/configs — the AC's "callers/tests/docs"). Heuristic + deterministic.
_TEST_MARKERS = ("test_", "_test.", "/tests/", "\\tests\\", ".test.", "spec_", "_spec.")
_DOC_EXTS = (".md", ".rst", ".txt", ".adoc")
_CONFIG_EXTS = (".yml", ".yaml", ".toml", ".ini", ".cfg", ".json")
CODE, TEST, DOC, CONFIG = "code", "test", "doc", "config"
BUCKETS = (CODE, TEST, DOC, CONFIG)


def classify_path(path: str) -> str:
    """Bucket a file path (deterministic, lexical): a test, a doc, a config, or code. A path with
    a test marker wins first (a test file may end in a code ext); then doc/config by extension."""
    p = (path or "").lower()
    if any(m in p for m in _TEST_MARKERS):
        return TEST
    if p.endswith(_DOC_EXTS) or "/docs/" in p or "\\docs\\" in p:
        return DOC
    if p.endswith(_CONFIG_EXTS):
        return CONFIG
    return CODE


# ---------------------------------------------------------------- Lens 1 — the impact report
@dataclass
class AffectedDecision:
    """A team DECISION/rule an approach touches — a memory item whose `about_code` link intersects
    the approach's code surface (doc 55 K3). `matched` names the symbols/files that intersected."""

    id: str
    subject: str
    kind: str
    matched: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "subject": self.subject, "kind": self.kind,
                "matched": list(self.matched)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AffectedDecision":
        return cls(id=d.get("id", ""), subject=d.get("subject", ""), kind=d.get("kind", ""),
                   matched=list(d.get("matched", [])))


@dataclass
class ApproachImpact:
    """The Lens-1 blast-radius report for ONE approach — structured + comparable. `degraded` marks
    a grep/heuristic score (no real graph). `magnitude` is the single comparable scalar."""

    approach: str
    targets: List[str] = field(default_factory=list)
    impacted_files: List[str] = field(default_factory=list)
    impacted_symbols: List[str] = field(default_factory=list)
    caller_count: int = 0                    # dedup'd caller/dependent references in the radius
    buckets: Dict[str, int] = field(default_factory=dict)     # callers/tests/docs/configs counts
    affected_decisions: List[AffectedDecision] = field(default_factory=list)
    degraded: bool = False
    note: str = ""
    # GR.S3 — the QUERY-LEVEL floor signal, distinct from the display `degraded` caveat above:
    # True only when a structural answer was ATTEMPTED and WITHHELD — no layer, a failed query, or
    # a query that reached the lexical floor (`qr.degraded`). The AST floor answering structurally
    # keeps `degraded=True` (uses_graph=False) for the display but `graph_degraded=False`, so it is
    # NOT refused. This is the signal the `graph.required` gate reads.
    #
    # D2 — "answering structurally" INCLUDES a structurally verified ZERO. This comment used to say
    # "the grep floor / empty-AST fallthrough", and that second clause was the defect written down:
    # a LEAF (a symbol the AST floor holds the definition of, that nothing calls) fell through to
    # grep and set this True, so one entry point among an approach's targets refused `spec_emit`
    # for the whole approach. The fix is at the BACKEND, where the distinction actually lives
    # (`ast_backend._holds_definition`), which is why this OR is unchanged and all three GR.S3
    # consumers inherit it — none of them carries its own copy of the rule.
    graph_degraded: bool = False

    @property
    def file_count(self) -> int:
        return len(self.impacted_files)

    @property
    def decision_count(self) -> int:
        return len(self.affected_decisions)

    @property
    def magnitude(self) -> int:
        """A comparable impact size: the caller/dependent surface + the files touched + the team
        decisions affected. Bigger = a wider blast radius (used to rank + to gate the deep-review
        OFFER). Deterministic — no graph needed (heuristic still scores)."""
        return self.caller_count + self.file_count + self.decision_count

    def bucket(self, name: str) -> int:
        return int(self.buckets.get(name, 0))

    def summary_line(self) -> str:
        g = "grep/heuristic" if self.degraded else "graph"
        b = self.buckets
        return (f"{self.approach}: {self.caller_count} dependents across {self.file_count} files "
                f"(code {b.get(CODE, 0)} · tests {b.get(TEST, 0)} · docs {b.get(DOC, 0)} · "
                f"configs {b.get(CONFIG, 0)}) · {self.decision_count} team decision(s) [{g}]")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approach": self.approach,
            "targets": list(self.targets),
            "impacted_files": list(self.impacted_files),
            "impacted_symbols": list(self.impacted_symbols),
            "caller_count": self.caller_count,
            "buckets": dict(self.buckets),
            "affected_decisions": [a.to_dict() for a in self.affected_decisions],
            "degraded": self.degraded,
            "note": self.note,
            "graph_degraded": self.graph_degraded,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ApproachImpact":
        return cls(
            approach=d.get("approach", ""),
            targets=list(d.get("targets", [])),
            impacted_files=list(d.get("impacted_files", [])),
            impacted_symbols=list(d.get("impacted_symbols", [])),
            caller_count=int(d.get("caller_count", 0)),
            buckets=dict(d.get("buckets", {})),
            affected_decisions=[AffectedDecision.from_dict(a)
                                for a in d.get("affected_decisions", [])],
            degraded=bool(d.get("degraded", False)),
            note=d.get("note", ""),
            graph_degraded=bool(d.get("graph_degraded", False)),
        )


def _dedup(seq: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def compute_impact(approach: str, targets: Sequence[str], *, layer: Any = None,
                   memory_items: Optional[Sequence[Any]] = None, depth: int = 2) -> ApproachImpact:
    """LENS 1 — compute the blast-radius impact of `approach` over its `targets` (the symbols/files
    it would touch). Unions the transitive dependents from `layer.blast_radius()` with the memory
    items whose `about_code` intersects that surface. Degrade-clean: `layer=None` (or a failing
    query) → no structural refs but the `about_code` intersection over the targets STILL scores,
    marked degraded. Pure + deterministic."""
    tgts = _dedup([str(t).strip() for t in (targets or []) if str(t).strip()])
    # Degraded = no layer at all, OR the layer is the grep floor (no real graph). Either way the
    # about_code intersection + any grep hits STILL score — degradation lowers confidence, not the
    # ability to compare (doc 63 §2). A layer without `uses_graph` is assumed a real graph.
    degraded = layer is None or (getattr(layer, "uses_graph", True) is False)
    # GR.S3 — the QUERY-LEVEL floor signal: True only when a structural answer was ATTEMPTED and
    # fell to the lexical grep floor (no layer, a failed query, or `qr.degraded`). The AST floor
    # answering WITH evidence does NOT set it (its query is `degraded=False`), so AST-with-evidence
    # is not refused. An approach that named NO targets has no blast radius to refuse (nothing was
    # queried) — it is not "degraded", just empty; so the signal starts False when `tgts` is empty.
    graph_degraded = bool(tgts) and layer is None
    touched_syms = set(tgts)
    touched_files: set = set()
    ref_keys: set = set()
    caller_count = 0

    if layer is not None:
        for t in tgts:
            try:
                qr = layer.blast_radius(t, depth=depth)
            except Exception:
                degraded = True                       # a failing query → degrade, keep scoring
                graph_degraded = True                 # the structural answer was withheld
                continue
            if getattr(qr, "degraded", False):
                degraded = True                       # the grep floor answered
                graph_degraded = True                 # ...from the lexical floor (a decision input)
            for r in getattr(qr, "references", []) or []:
                path = getattr(r, "path", "") or ""
                line = getattr(r, "line", 0) or 0
                key = (path, line)
                if key not in ref_keys:
                    ref_keys.add(key)
                    caller_count += 1
                if path:
                    touched_files.add(path)
                sym = getattr(r, "symbol", None)
                if sym:
                    touched_syms.add(sym)

    files = sorted(touched_files)
    buckets = {b: 0 for b in BUCKETS}
    for f in files:
        buckets[classify_path(f)] += 1

    # about_code intersection — the affected team decisions (doc 55 K3). Match an item when any of
    # its about_code entries names a symbol OR file the approach touches (targets ∪ radius).
    universe = touched_syms | touched_files
    affected: List[AffectedDecision] = []
    for it in (memory_items or []):
        ac = [str(s) for s in (getattr(it, "about_code", []) or [])]
        matched = sorted(s for s in ac if s in universe)
        if matched:
            affected.append(AffectedDecision(
                id=getattr(it, "id", "") or "",
                subject=getattr(it, "subject", "") or "",
                kind=(getattr(it, "effective_kind", "") or getattr(it, "kind", "") or ""),
                matched=matched))

    note = ("graph absent — grep/heuristic impact (still scored)" if degraded
            else "graph-grounded impact")
    return ApproachImpact(
        approach=approach, targets=tgts, impacted_files=files,
        impacted_symbols=sorted(touched_syms), caller_count=caller_count,
        buckets=buckets, affected_decisions=affected, degraded=degraded, note=note,
        graph_degraded=graph_degraded)


def compare_impacts(impacts: Sequence[ApproachImpact]) -> List[ApproachImpact]:
    """Rank the approaches by impact magnitude (smallest blast radius first — usually the safer
    default). Stable on ties by approach name. The COMPARABILITY the lens exists to provide."""
    return sorted(impacts, key=lambda i: (i.magnitude, i.approach))


def render_impacts(impacts: Sequence[ApproachImpact]) -> str:
    """A compact per-approach impact block for the design write-up / plan file (records Lens 1)."""
    if not impacts:
        return "· blast radius: (no approaches assessed)"
    lines = ["· Blast radius (Lens 1 — code impact + affected team decisions):"]
    for imp in impacts:
        lines.append(f"  - {imp.summary_line()}")
        for dec in imp.affected_decisions:
            lines.append(f"      ↳ decision [{dec.kind}] {dec.subject} "
                         f"(via {', '.join(dec.matched)})")
    return "\n".join(lines)


# ---------------------------------------------------------------- Lens 2 — architectural fit
FITS = "fits"
RISK = "risk"
MISFIT = "misfit"
DESIGN_FIT_VERDICTS = (FITS, RISK, MISFIT)


@dataclass
class DesignFitVerdict:
    """LENS 2 — the PROMPT-DRIVEN architectural-fit verdict for one approach (doc 63 §4). The model
    produces it, grounded in the knowledge layer + memory; this holds + validates it. A `risk`/
    `misfit` MUST name at least one boundary/layering/ownership risk — you cannot flag a misfit
    without saying why (and cannot silently pass one either)."""

    approach: str
    verdict: str                             # fits | risk | misfit
    risks: List[str] = field(default_factory=list)     # named boundary/layering/ownership risks
    rationale: str = ""

    @property
    def flagged(self) -> bool:
        """True when the approach is architecturally risky/mis-layered — surfaced at brainstorm,
        before the spec (so a mis-layered approach is caught by construction, P21)."""
        return self.verdict in (RISK, MISFIT)

    @property
    def valid(self) -> bool:
        """A verdict is on-the-table ONLY when it is a known verdict AND — if it flags a
        risk/misfit — names at least one concrete risk (fail-closed: no hand-waved flags)."""
        if self.verdict not in DESIGN_FIT_VERDICTS:
            return False
        if self.verdict in (RISK, MISFIT) and not [r for r in self.risks if str(r).strip()]:
            return False
        return True

    def summary_line(self) -> str:
        tag = {FITS: "fits", RISK: "RISK", MISFIT: "MISFIT"}.get(self.verdict, self.verdict)
        risks = f" — {'; '.join(self.risks)}" if self.risks else ""
        return f"{self.approach}: design-fit {tag}{risks}"

    def to_dict(self) -> Dict[str, Any]:
        return {"approach": self.approach, "verdict": self.verdict,
                "risks": list(self.risks), "rationale": self.rationale}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DesignFitVerdict":
        return cls(approach=d.get("approach", ""), verdict=d.get("verdict", ""),
                   risks=list(d.get("risks", [])), rationale=d.get("rationale", ""))


def render_design_fits(verdicts: Sequence[DesignFitVerdict]) -> str:
    """A compact per-approach design-fit block for the design write-up / plan file (records Lens 2)."""
    if not verdicts:
        return "· design fit: (no approaches assessed)"
    lines = ["· Architectural fit (Lens 2 — boundary/layering/ownership):"]
    for v in verdicts:
        lines.append(f"  - {v.summary_line()}")
    return "\n".join(lines)


# ---------------------------------------------------------------- deep-review OFFER (R1.S4e)
# Over a complexity/impact threshold brainstorm OFFERS the deep whole-codebase architectural review
# (R1.S4e, user-invoked, 0.2.0) — it NEVER auto-runs it. Magnitude, not a hard rule, so the grep
# floor still triggers the offer.
DEEP_REVIEW_MAGNITUDE_THRESHOLD = 20


def deep_review_offer(impacts: Sequence[ApproachImpact], design_fits: Sequence[Any] = (),
                      threshold: int = DEEP_REVIEW_MAGNITUDE_THRESHOLD) -> Optional[str]:
    """Return an OFFER string when this change looks high-impact (peak blast-radius magnitude ≥
    `threshold`, OR any approach is a design MISFIT) — otherwise None. mokata OFFERS the deep
    whole-codebase review (R1.S4e); it never runs it unasked. Pure — just returns text."""
    peak = max((i.magnitude for i in impacts), default=0)
    misfit = any(getattr(v, "verdict", "") == MISFIT for v in (design_fits or ()))
    if peak < threshold and not misfit:
        return None
    why = f"peak blast radius {peak}" + (" + a design MISFIT" if misfit else "")
    return (
        f"This looks high-impact ({why}). mokata can OFFER — not run — the DEEP whole-codebase "
        "architectural review (R1.S4e, user-invoked; 0.2.0) before the spec is committed. "
        "Say the word to launch it; otherwise the brainstorm lenses above stand."
    )
