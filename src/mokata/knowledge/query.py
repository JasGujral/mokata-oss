"""B2 — the typed structural query API.

A stable, backend-independent shape for structural code questions. The same
`QueryResult` of `Reference`s is returned whether a real codebase graph answered or the
grep floor did — callers never branch on the backend.

mokata persists and queries; it never builds a parser. The grep backend does lexical
search (the documented floor); the graph backend delegates entirely to the adopted tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from ..errors import DegradedCapability

# The structural questions the API answers. Stable across backends.
#
# CRG-NAV (b): `defs` and `refs` are the two NAVIGATION intents the graph-first prose names that
# previously had no op behind them — "where is this defined / does this symbol exist" and
# "everywhere this is referenced". They are ADDITIVE kinds on the existing typed API (the MCP
# `query` tool derives its enum from this tuple, so it accepts them for free); no new tool.
QUERY_KINDS = ("callers", "callees", "implementers", "imports", "blast_radius",
               "defs", "refs")

# 0.0.26 O7 (BLAST-FULL-ON-REQUEST) — how much a blast radius may spend. `default` is the bounded
# walk every automatic caller gets (the 0.0.25 K cap; ruling U10 keeps it). `full` is asked for by
# a HUMAN on a result the default cut (`mokata query blast_radius X --full`, the IDE's "calculate
# the full blast radius" action): a much longer, still hard-bounded and cancellable walk. Only the
# blast radius has a budget; every other kind refuses `full` rather than silently ignoring it.
BUDGET_DEFAULT = "default"
BUDGET_FULL = "full"
BUDGETS = (BUDGET_DEFAULT, BUDGET_FULL)

# CRG-NAV — the NAVIGATION subset: "where does this symbol live / who touches it". `blast_radius`
# is deliberately NOT here: that is the IMPACT question (GRAPH-FIRST-IMPACT owns it), and the
# navigation floor note below must not attach itself to impact answers.
NAVIGATION_KINDS = ("defs", "refs", "callers", "callees", "implementers", "imports")

# CRG-NAV (d) — the chain's most-preferred REAL structural graph. The honest floor note binds to
# THE CHAIN through this one symbol (`graph_adopt.ADOPTABLE_GRAPH_TOOLS` heads with it too), so a
# later re-ordering of the backend chain (backlog GRAPH-SWEEP) moves the note with it instead of
# leaving prose that names a product mokata no longer prefers.
PREFERRED_GRAPH_TOOL = "code-review-graph"

# The one honest sentence a NAVIGATION answer carries when the LEXICAL floor produced it: what
# answered, and the single step that buys the full chain. Attached by the grep floor itself, so
# it rides every route to that floor (direct, via the AST floor's fallthrough, or via the layer's
# degrade) without a second mechanism.
GREP_FLOOR_NAV_NOTE = f"grep floor — install {PREFERRED_GRAPH_TOOL} for full navigation"

# GRAPH-FIRST-IMPACT (d) (0.0.23 D2) — the IMPACT twin of the navigation note above: a blast radius
# the lexical floor produced is DEGRADED and says so, with the one step that buys a structural one.
# Attached at the floor itself for the same reason the nav note is (it rides every route there).
# The layer-aware REMEDY — adopt vs repair vs check-the-target — is `layer.impact_degrade`, because
# only the layer knows whether a graph is adopted (fix D's lesson: "install a graph" is the wrong
# sentence to a user whose graph is adopted and broken).
IMPACT_KINDS = ("blast_radius",)
GREP_FLOOR_IMPACT_NOTE = f"degraded — install {PREFERRED_GRAPH_TOOL} for full impact"

# ---------------------------------------------------------------------- the BASIS of an answer
# D2 (BLAST-RADIUS-LEAF-DEGRADE) — WHICH RUNG ANSWERED, and the reason this is a named vocabulary
# rather than a bool.
#
# `references == []` used to mean two irreconcilable things: "this symbol genuinely has no
# callers" and "I have no structural evidence about this symbol". `degraded` could not separate
# them either, because the AST floor's zero-edge fallthrough reached the grep floor and inherited
# the floor's own verdict. So an approach naming ONE leaf — an entry point, a new function, a
# top-level component, exactly what a frontend approach names — had its whole blast radius marked
# degraded and `spec_emit` refused it, on mokata's own primary language.
#
# That is doc 85 §7g: an absent answer and a real answer must never share a representation. The
# worked model in this repo is `run_resolver.RunResolution`, and this mirrors its three moving
# parts deliberately — distinct OUTCOMES instead of a boolean with a comment, a `basis` naming
# which rung answered so an answer carries its own provenance, and one stated invariant:
#
#     an empty `references` list NEVER distinguishes an answer from an absence — only `basis`.
#
# The consistency argument, which is CRG-NAV's own and is why this is not special-casing zero:
# `refs` is refused from the AST index because calls+imports is "a PARTIAL set dressed as
# structural". A zero-caller `blast_radius` over a symbol the index HOLDS A DEFINITION FOR is the
# opposite shape — a COMPLETE set that is empty — and calling it absent is that same overclaim
# pointed the other way. A verified zero is exactly as trustworthy as a verified three: both are
# bounded by the floor's one documented limit (name resolution, not type inference), which is why
# a verified-empty answer carries the same note rather than a bare claim.
BASIS_STRUCTURAL = "structural"          # a structural backend answered
BASIS_VERIFIED_EMPTY = "verified_empty"  # ...and CERTIFIES that the answer is zero
BASIS_LEXICAL = "lexical"                # no structural answer — the lexical floor stands in

# VERIFIED_EMPTY is a STRICTLY STRONGER claim than STRUCTURAL, not merely "structural with an
# empty list": it says the backend can account for the symbol and vouch for the absence of edges.
# Only a backend that can actually check that (today: the AST floor, via its definition index)
# may use it, which is why `__post_init__` polices it and the adopted-graph sites do not reach
# for it — an adopted graph's empty result stays STRUCTURAL, byte-identical to before.

# The bases that mean "a structural backend answered this question". Membership is asked HERE, in
# one place, so no consumer can invent its own idea of what counts as structural — the drift that
# let three GR.S3 consumers each carry their own copy of the rule.
STRUCTURAL_BASES = (BASIS_STRUCTURAL, BASIS_VERIFIED_EMPTY)


class BackendError(DegradedCapability):
    """A backend failed to answer a query (e.g. the graph tool errored). The layer
    catches this to degrade to the grep floor rather than hard-fail (A3)."""


class GraphNotIndexed(BackendError):
    """0.0.26 O2 (`CRG-BLAST-EMPTY`) — the adopted graph is HEALTHY and holds no node for the
    target (code-review-graph's `status: "not_found"` / `"ambiguous"`). Neither "the tool failed"
    nor "zero dependents": the graph simply cannot speak for this symbol. The layer answers it from
    the floor with a note naming both backends — no recovery re-probe, no degrade alarm against a
    tool that is working."""


class GraphIndexUnbuilt(GraphNotIndexed):
    """0.0.26 O13 (`CRG-AUTO-BUILD`) — the graph is installed but its index for THIS repo was never
    built (or is being built, or its build failed). Not "this symbol is absent": NO symbol is there.
    The layer answers from the floor, and the backend has already started (or declined, with the
    reason) the one background build; the message is the note the reader sees."""


def floor_symbol(graph_symbol: Optional[str]) -> Optional[str]:
    """0.0.26 O13 (F3) — a graph's symbol id in the FLOOR's identity scheme.

    The AST floor names a symbol by its bare name (`refund`, a method `meth`), and the IDE builds
    its node ids from that (`sym:refund`). code-review-graph names it `path::name` /
    `path::Class.meth`. Left as-is, the same function had two node ids depending on which backend
    happened to answer, so a symbol picked while the floor answered vanished once the graph took
    over. The graph's own id is kept in `metadata.qualified_name`; this is only the display/identity
    form, applied once at the adapter boundary."""
    if not graph_symbol:
        return graph_symbol
    name = graph_symbol.rsplit("::", 1)[-1]
    return name.rsplit(".", 1)[-1] or graph_symbol


@dataclass
class Reference:
    """One structural hit: a location and the symbol it relates to.

    GR.S2: `edge_type` and `metadata` are ADDITIVE — they carry the richer fields an adopted
    graph (code-review-graph) offers (the relationship kind, e.g. CALLS/IMPORTS_FROM/
    TESTED_BY/INHERITS, and per-symbol metadata like qualified_name / kind / is_test). The
    lexical + AST floors leave them at their defaults, so every existing consumer is
    unchanged and richer consumers read them when a real graph answered."""

    path: str
    line: int
    snippet: str = ""
    symbol: Optional[str] = None
    edge_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "line": self.line,
                "snippet": self.snippet, "symbol": self.symbol,
                "edge_type": self.edge_type, "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reference":
        return cls(path=d["path"], line=int(d.get("line", 0)),
                   snippet=d.get("snippet", ""), symbol=d.get("symbol"),
                   edge_type=d.get("edge_type"),
                   metadata=dict(d.get("metadata") or {}))


@dataclass
class QueryResult:
    """One structural answer — and WHICH RUNG produced it (D2; see the basis vocabulary above).

    `basis` is the single stored representation; `degraded` is DERIVED from it. That asymmetry is
    the whole fix and it is deliberate: while both were fields, a leaf answer and an absent answer
    could agree on `degraded` while disagreeing about reality, and every consumer that read the
    bool inherited the confusion. There is now one place the truth lives, so a consumer cannot
    read a stale half of it.

    `basis` defaults to LEXICAL, which is the FAIL-HONEST direction: a backend that has not been
    taught this vocabulary makes no structural claim, so an un-migrated caller under-claims (the
    old, safe behaviour) rather than silently certifying a zero it never verified."""

    kind: str                                   # one of QUERY_KINDS
    target: str                                 # the symbol/module asked about
    references: List[Reference] = field(default_factory=list)
    backend: str = ""                           # which provider answered
    basis: str = BASIS_LEXICAL                  # which rung answered (the ONE stored signal)
    note: str = ""
    # 0.0.25 K — a BOUNDED answer says so. `truncated` is True when the backend stopped before the
    # full answer (the AST floor's blast-radius bound, the adopted graph's result cap); `total` is
    # the full count when the backend KNOWS it (code-review-graph reports `result_count`), None when
    # it does not. Set through `mark_truncated`, which also attaches the classed `truncated —` note,
    # so the flag and the reason cannot drift apart. A truncated answer is still the backend's own
    # basis (a structural prefix is structural) — but it is a PREFIX, never "the" answer: a reader
    # that concludes an absence from it ("no test reaches this") must read `truncated` first.
    truncated: bool = False
    total: Optional[int] = None
    # 0.0.26 P35 (G28/R37) — the target NAME matched more than one definition (kubernetes:
    # `HandleError` has 18 graph nodes). The answer is the union over all of them, and says so:
    # `definitions` is how many there were, `candidates` names them (repo-relative qualified name,
    # path, line) so a reader can ask again for exactly one. None / [] when the name was unique.
    definitions: Optional[int] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The one incoherent combination: a basis claiming "the answer is zero" while carrying
        # hits. Refused at construction rather than left for a reader to notice, because this is
        # exactly the shape whose two halves drifted apart before.
        if self.basis == BASIS_VERIFIED_EMPTY and self.references:
            raise ValueError(
                f"basis={BASIS_VERIFIED_EMPTY!r} claims a structurally verified ZERO but "
                f"{len(self.references)} reference(s) were supplied")

    @property
    def count(self) -> int:
        return len(self.references)

    @property
    def degraded(self) -> bool:
        """True when NO structural backend answered — the lexical floor's approximation stands in.

        Read-only ON PURPOSE. It used to be a settable field, and that is how "the AST found zero
        callers" and "the AST could not see this symbol" ended up sharing a value. Demote through
        `demote_to_floor` instead, which moves the basis and the note together."""
        return self.basis not in STRUCTURAL_BASES

    @property
    def structural(self) -> bool:
        """True when a structural backend answered — WHETHER OR NOT it found anything."""
        return self.basis in STRUCTURAL_BASES

    @property
    def verified_empty(self) -> bool:
        """True when the structural answer is zero AND that zero is the answer, not an absence."""
        return self.basis == BASIS_VERIFIED_EMPTY

    def demote_to_floor(self, why: str = "") -> None:
        """Demote an answer to the lexical floor — the ONE spelling of "this is no longer a
        structural claim", so a caller cannot move the verdict without moving the reason."""
        self.basis = BASIS_LEXICAL
        if why:
            self.note = f"{self.note}; {why}" if self.note else why

    def to_dict(self) -> Dict[str, Any]:
        # `degraded` is still emitted — it is a derived view, and every existing reader of the
        # wire shape keeps working — with `basis` beside it carrying what the bool cannot say.
        return {
            "kind": self.kind,
            "target": self.target,
            "backend": self.backend,
            "basis": self.basis,
            "degraded": self.degraded,
            "note": self.note,
            "truncated": self.truncated,
            "total": self.total,
            "references": [r.to_dict() for r in self.references],
            **({"definitions": self.definitions, "candidates": list(self.candidates)}
               if self.definitions else {}),
        }


#: The class word a bounded answer's note leads with (pipe-joined onto the existing note, the same
#: convention every other classed note on a QueryResult uses).
TRUNCATED_NOTE_PREFIX = "truncated —"


def mark_truncated(result: "QueryResult", total: Optional[int], why: str) -> "QueryResult":
    """Mark `result` as a PARTIAL answer (0.0.25 K) — the one spelling, so `truncated`, `total` and
    the note always move together. `total` is the full count when the backend knows it."""
    result.truncated = True
    if total is not None:
        result.total = int(total)
    shown = len(result.references)
    head = (f"{TRUNCATED_NOTE_PREFIX} {shown} of {total} shown" if total is not None
            else f"{TRUNCATED_NOTE_PREFIX} {shown} shown, more exist")
    add = f"{head} ({why})" if why else head
    result.note = f"{result.note} | {add}" if result.note else add
    return result


class GraphBackend(ABC):
    """A provider that answers structural queries. Implementations: the adopted
    code-review-graph adapter, and the grep lexical floor."""

    name: str = ""
    is_graph: bool = False

    @abstractmethod
    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        ...

    def blast_radius_full(self, target: str, depth: int = 1,
                          checkpoint: Optional[Callable[[], None]] = None) -> QueryResult:
        """0.0.26 O7 — the blast radius with the FULL budget, asked for on request only.

        A backend whose blast radius has no budget (the grep floor walks to the end; an adopted
        graph caps on its own side and says so) answers it exactly as `query` does. The AST floor,
        which cuts its default walk, overrides this with its long hard-bounded walk. `checkpoint`
        is called between steps of a long walk and RAISES to cancel it (the IDE passes its
        abandoned-request check)."""
        return self.query("blast_radius", target, depth=depth)

    def supports_kind(self, kind: str) -> bool:
        """CRG-NAV (b/d): whether THIS backend has an op for `kind`.

        The floors answer every kind, so the default is True. An adopted graph whose real
        interface exposes no mapping for a kind (code-review-graph has no definition-site
        pattern) says so HERE, and the layer routes that one kind straight to the floor with an
        honest note — instead of the graph raising, burning a recovery attempt, and reporting a
        mapping gap as if the tool had failed."""
        return kind in QUERY_KINDS
