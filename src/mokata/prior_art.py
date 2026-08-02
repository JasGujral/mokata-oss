"""GR-PA — the prior-art step (doc 02/84 GR-PA, doc 04 P22 + anti-repetition grounding).

Before an approach is approved, mokata asks the question bare Claude Code never does: *does this
already exist HERE?* This module is the PURE computation behind that question — for each candidate
approach it runs a bounded prior-art pass over the injected knowledge layer + memory recall and
normalizes the result to a small typed `PriorArtResult`:

  * EXISTING IMPLEMENTATIONS — the symbols/files that already do something like the planned work,
    found via the layer's best available tier: CRG semantic search when a semantic-capable graph is
    adopted; structural name-resolution (`implementers`/`callers`) on the AST floor or a real graph;
    the lexical grep floor otherwise. The tier is named HONESTLY (`no prior art found via <tier>`).
  * RELATED DECISIONS — the prior team decisions the planned surface touches, from the SAME memory
    recall channel the rest of brainstorm uses (no second retrieval channel; shares the budget).

It is EVIDENCE-GATHERING, never a refusal: a degraded/absent graph still runs the step (the tier is
marked degraded, the findings still score). The step-RAN gate lives in `govern/prior_art_gate.py`;
GR.S3 owns the degraded-radius refusal and this stage duplicates none of it.

Bounded (top-N) + deterministic. PURE (no I/O of its own): the caller injects a `layer`
(duck-typed `.implementers`/`.callers`/`.semantic`) and a `recall` callable — this module opens no
retrieval surface, so it cannot become a second budget channel.

Named hook (GR.S2(m) precedent): `decisions` activates decisions[]-aware recall enrichment ONLY
when AP-SD's structured `decisions[]` is supplied; until AP-SD ships it stays dormant (recall-only).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# The retrieval tiers, named honestly — "no prior art found via <tier>" tells the human exactly how
# hard mokata looked. `semantic` = a CRG-adopted semantic graph; `graph` = a real adopted structural
# graph; `name-resolution` = the embedded AST floor (name resolution, not type inference); `lexical`
# = the grep floor; `absent` = no layer wired at all (the step still runs — recall only).
TIER_SEMANTIC = "semantic"
TIER_GRAPH = "graph"
TIER_NAME_RESOLUTION = "name-resolution"
TIER_LEXICAL = "lexical"
TIER_ABSENT = "absent"

# A human phrase per tier for the tradeoff-table row.
_TIER_DISPLAY = {
    TIER_SEMANTIC: "semantic search",
    TIER_GRAPH: "graph (structural)",
    TIER_NAME_RESOLUTION: "name-resolution (AST)",
    TIER_LEXICAL: "lexical (grep floor)",
    TIER_ABSENT: "no graph (recall only)",
}

# Bounded top-N: prior-art shares the existing brainstorm budget — a small, comparable pass, never
# an unbounded fan-out (doc 85 §7 frugality; no second channel).
DEFAULT_TOP_N = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup(seq: Sequence[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ---------------------------------------------------------------- typed results
@dataclass
class PriorArtFinding:
    """One existing implementation the planned work would otherwise re-implement — a symbol/file the
    layer surfaced. `via` names the tier that found it, so the row is honest about its confidence."""

    symbol: str = ""
    path: str = ""
    line: int = 0
    kind: str = ""            # the query kind that found it (implementers/callers/semantic)
    via: str = ""             # the tier

    @property
    def location(self) -> str:
        return self.path + (f":{self.line}" if self.line else "")

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "path": self.path, "line": self.line,
                "kind": self.kind, "via": self.via}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PriorArtFinding":
        return cls(symbol=d.get("symbol", ""), path=d.get("path", ""),
                   line=int(d.get("line", 0) or 0), kind=d.get("kind", ""), via=d.get("via", ""))


@dataclass
class RelatedDecision:
    """A prior team decision the planned surface touches — recalled from memory, or (when AP-SD has
    shipped) matched from the structured `decisions[]` about_code anchors. `source` distinguishes the
    two: `recall` from the memory channel, `decisions[]` from the AP-SD enrichment hook."""

    id: str = ""
    subject: str = ""
    kind: str = ""
    matched: List[str] = field(default_factory=list)
    source: str = "recall"
    # DB.S7c2 STALE-REF — the `index_epoch` the minting store was at when this citation was made.
    # It rides `to_dict`/`from_dict` because that is the WHOLE point: this `id` is persisted into
    # brainstorm run state and read back after the store that minted it is gone, and the stamp is
    # the only thing that survives with it. Empty = un-stamped (a `decisions[]` match, which never
    # crossed a store boundary, or a floor mint where STALE-REF is OFF) — see `memory.staleness`.
    index_epoch: str = ""
    # H-6 S4 STALE-REF (code-anchor half) — the `about_code` anchors this cited decision CONCERNS.
    # It rides `to_dict`/`from_dict` for the same reason `index_epoch` does, and it is the same
    # reason: this citation is persisted into brainstorm run state and read back after the pass
    # that made it is gone, so an anchor that did not survive the round trip could never be checked
    # at approve time. Distinct from `matched`, which is the intersection with THIS approach's
    # surface (and empty for a `recall`-sourced citation); this is the decision's own anchor list.
    # Empty for a legacy citation and for a decision that names no code — no anchors, no opinion.
    about_code: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "subject": self.subject, "kind": self.kind,
                "matched": list(self.matched), "source": self.source,
                "index_epoch": self.index_epoch, "about_code": list(self.about_code)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RelatedDecision":
        return cls(id=d.get("id", ""), subject=d.get("subject", ""), kind=d.get("kind", ""),
                   matched=list(d.get("matched", [])), source=d.get("source", "recall"),
                   index_epoch=d.get("index_epoch", ""),
                   about_code=list(d.get("about_code", [])))

    @classmethod
    def from_item(cls, item: Any, index_epoch: str = "") -> "RelatedDecision":
        return cls(
            id=getattr(item, "id", "") or "",
            subject=getattr(item, "subject", "") or "",
            kind=(getattr(item, "effective_kind", "") or getattr(item, "kind", "") or ""),
            source="recall", index_epoch=index_epoch,
            about_code=list(getattr(item, "about_code", None) or []))


@dataclass
class PriorArtResult:
    """The prior-art evidence for ONE approach — what the bound step records in run state. `ran` is
    the whole point of the gate (approval requires the step RAN, never that it FOUND something);
    `tier` + the finding counts + `at` are the recorded evidence; `degraded` marks a lexical/absent
    tier (honest, but NEVER a refusal here — GR.S3 owns the degraded-radius gate)."""

    approach: str
    ran: bool = False
    at: str = ""
    tier: str = TIER_ABSENT
    implementations: List[PriorArtFinding] = field(default_factory=list)
    decisions: List[RelatedDecision] = field(default_factory=list)
    degraded: bool = False
    note: str = ""

    @property
    def finding_count(self) -> int:
        return len(self.implementations) + len(self.decisions)

    @property
    def found_something(self) -> bool:
        return bool(self.implementations or self.decisions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approach": self.approach,
            "ran": self.ran,
            "at": self.at,
            "tier": self.tier,
            "implementations": [f.to_dict() for f in self.implementations],
            "decisions": [d.to_dict() for d in self.decisions],
            "degraded": self.degraded,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PriorArtResult":
        return cls(
            approach=d.get("approach", ""),
            ran=bool(d.get("ran", False)),
            at=d.get("at", ""),
            tier=d.get("tier", TIER_ABSENT),
            implementations=[PriorArtFinding.from_dict(f) for f in d.get("implementations", [])],
            decisions=[RelatedDecision.from_dict(x) for x in d.get("decisions", [])],
            degraded=bool(d.get("degraded", False)),
            note=d.get("note", ""),
        )


# ---------------------------------------------------------------- the tier
def _tier(layer: Any) -> str:
    """Name the best retrieval tier the layer can answer with — honestly. A semantic-capable adopted
    graph answers by meaning; a real structural graph by edges; the AST floor by name resolution; the
    grep floor lexically; no layer at all leaves only recall."""
    if layer is None:
        return TIER_ABSENT
    if getattr(layer, "supports_semantic", False):
        return TIER_SEMANTIC
    if getattr(layer, "uses_graph", False):
        return TIER_GRAPH
    if (getattr(layer, "backend_name", "") or "").lower() == "ast":
        return TIER_NAME_RESOLUTION
    return TIER_LEXICAL


def tier_display(tier: str) -> str:
    return _TIER_DISPLAY.get(tier, tier)


# ---------------------------------------------------------------- the step
def run_prior_art(approach: str, targets: Sequence[str], *, layer: Any = None,
                  recall: Optional[Callable[[str], Sequence[Any]]] = None, query: str = "",
                  top_n: int = DEFAULT_TOP_N, decisions: Optional[Sequence[Any]] = None,
                  at: Optional[str] = None, index_epoch: str = "") -> PriorArtResult:
    """Run the prior-art pass for one approach over its `targets` (the symbols/terms it would touch).

    Bounded top-N over the injected `layer` (semantic tier when CRG-adopted, structural/lexical
    otherwise — the tier is named honestly) UNIONED with the memory `recall` channel for related
    decisions. Evidence-gathering — it ALWAYS runs and records `ran=True`; a degraded/absent tier is
    marked but never refused. Pure + deterministic; opens no retrieval surface of its own."""
    tier = _tier(layer)
    tgts = _dedup([str(t).strip() for t in (targets or []) if str(t).strip()])[: max(0, top_n)]
    q = query.strip() or " ".join(tgts)

    findings: List[PriorArtFinding] = []
    if layer is not None:
        if tier == TIER_SEMANTIC:
            findings.extend(_collect(_safe(layer, "semantic", q), tier))
        else:
            for t in tgts:
                if len(findings) >= top_n:
                    break
                findings.extend(_collect(_safe(layer, "implementers", t), tier))
    # bound the surfaced implementations (shares the budget — no unbounded fan-out).
    findings = _dedup_findings(findings)[:top_n]

    # RELATED DECISIONS — the SAME memory recall channel brainstorm already uses (one shared query,
    # not per-item), bounded top-N. `recall` is injected, so this module never opens a second channel.
    related: List[RelatedDecision] = []
    if recall is not None and q:
        for item in list(recall(q) or [])[:top_n]:
            # DB.S7c2 — stamp the MINTING store's `index_epoch` onto the citation. Only the recall
            # arm is stamped: these ids come FROM the store and get persisted into run state, so
            # they are the ones that outlive it. `index_epoch=""` (the floor, or no store) leaves
            # the citation un-stamped, which `memory.staleness.is_stale` reads as "no opinion".
            related.append(RelatedDecision.from_item(item, index_epoch=index_epoch))

    # NAMED HOOK (GR.S2(m) precedent) — dormant until AP-SD's structured `decisions[]` is supplied.
    # When it is, enrich the related set from decisions whose about_code anchors intersect the surface.
    if decisions is not None:
        related.extend(_enrich_from_decisions(decisions, tgts, findings, seen=related))

    degraded = tier in (TIER_LEXICAL, TIER_ABSENT)
    note = "" if (findings or related) else f"no prior art found via {tier}"
    return PriorArtResult(
        approach=approach, ran=True, at=at or _now_iso(), tier=tier,
        implementations=findings, decisions=related, degraded=degraded, note=note)


def _safe(layer: Any, method: str, arg: str) -> Any:
    """Run one layer query, degrade-clean: any query fault yields no references (never a crash)."""
    fn = getattr(layer, method, None)
    if not callable(fn):
        return None
    try:
        return fn(arg)
    except Exception:                                     # noqa: BLE001 — a failed query is no evidence
        return None


def _collect(qr: Any, tier: str) -> List[PriorArtFinding]:
    out: List[PriorArtFinding] = []
    if qr is None:
        return out
    kind = getattr(qr, "kind", "") or ""
    for r in getattr(qr, "references", []) or []:
        path = getattr(r, "path", "") or ""
        sym = getattr(r, "symbol", None) or ""
        if not (path or sym):
            continue
        out.append(PriorArtFinding(symbol=sym, path=path, line=int(getattr(r, "line", 0) or 0),
                                   kind=kind, via=tier))
    return out


def _dedup_findings(findings: Sequence[PriorArtFinding]) -> List[PriorArtFinding]:
    seen, out = set(), []
    for f in findings:
        key = (f.symbol, f.path, f.line)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _enrich_from_decisions(decisions: Sequence[Any], targets: Sequence[str],
                           findings: Sequence[PriorArtFinding],
                           *, seen: Sequence[RelatedDecision]) -> List[RelatedDecision]:
    """AP-SD hook — match structured decisions' `about_code` anchors against the approach's surface
    (its targets ∪ the symbols/files prior art surfaced). A match is a related decision the plan must
    not silently contradict. Duck-typed over dict or object, exactly as review_graph reads AP-SD."""
    universe = set(targets)
    for f in findings:
        if f.symbol:
            universe.add(f.symbol)
        if f.path:
            universe.add(f.path)
    have = {d.id for d in seen}
    out: List[RelatedDecision] = []
    for d in decisions or []:
        anchors = (d.get("about_code") if isinstance(d, dict) else getattr(d, "about_code", None)) or []
        matched = sorted(a for a in anchors if a in universe)
        if not matched:
            continue
        did = (d.get("id") if isinstance(d, dict) else getattr(d, "id", "")) or ""
        subject = ((d.get("statement") if isinstance(d, dict) else getattr(d, "statement", ""))
                   or (d.get("subject") if isinstance(d, dict) else getattr(d, "subject", "")) or "")
        if did in have:
            continue
        have.add(did)
        out.append(RelatedDecision(id=did, subject=subject, kind="decision",
                                   matched=matched, source="decisions[]",
                                   about_code=[str(a) for a in anchors]))
    return out


# ---------------------------------------------------------------- render (the tradeoff-table row)
def render_prior_art(results: Sequence[PriorArtResult]) -> str:
    """The compact per-approach prior-art block for the design write-up / plan file — mokata-rendered
    (never model-paraphrased-only): each candidate carries an "existing <symbol> in <file> — extend?"
    row, or the honest "no prior art found via <tier>" when the pass came back empty."""
    if not results:
        return "· prior art: (no approaches assessed)"
    lines = ["· Prior art (extend, don't re-implement):"]
    for r in results:
        if not r.found_something:
            lines.append(f"  - {r.approach} [{tier_display(r.tier)}]: {r.note}")
            continue
        parts: List[str] = []
        for f in r.implementations:
            label = f.symbol or f.location
            parts.append(f"existing {label} in {f.location or '?'} — extend?")
        for d in r.decisions:
            parts.append(f"related decision [{d.kind}] {d.subject}".rstrip())
        lines.append(f"  - {r.approach} [{tier_display(r.tier)}]: " + " · ".join(parts))
    return "\n".join(lines)
