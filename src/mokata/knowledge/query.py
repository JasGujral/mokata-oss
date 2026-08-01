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
from typing import Any, Dict, List, Optional
from ..errors import DegradedCapability

# The structural questions the API answers. Stable across backends.
#
# CRG-NAV (b): `defs` and `refs` are the two NAVIGATION intents the graph-first prose names that
# previously had no op behind them — "where is this defined / does this symbol exist" and
# "everywhere this is referenced". They are ADDITIVE kinds on the existing typed API (the MCP
# `query` tool derives its enum from this tuple, so it accepts them for free); no new tool.
QUERY_KINDS = ("callers", "callees", "implementers", "imports", "blast_radius",
               "defs", "refs")

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


class BackendError(DegradedCapability):
    """A backend failed to answer a query (e.g. the graph tool errored). The layer
    catches this to degrade to the grep floor rather than hard-fail (A3)."""


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
    kind: str                                   # one of QUERY_KINDS
    target: str                                 # the symbol/module asked about
    references: List[Reference] = field(default_factory=list)
    backend: str = ""                           # which provider answered
    degraded: bool = False                      # True when the grep floor answered
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.references)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "backend": self.backend,
            "degraded": self.degraded,
            "note": self.note,
            "references": [r.to_dict() for r in self.references],
        }


class GraphBackend(ABC):
    """A provider that answers structural queries. Implementations: the adopted
    code-review-graph adapter, and the grep lexical floor."""

    name: str = ""
    is_graph: bool = False

    @abstractmethod
    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        ...

    def supports_kind(self, kind: str) -> bool:
        """CRG-NAV (b/d): whether THIS backend has an op for `kind`.

        The floors answer every kind, so the default is True. An adopted graph whose real
        interface exposes no mapping for a kind (code-review-graph has no definition-site
        pattern) says so HERE, and the layer routes that one kind straight to the floor with an
        honest note — instead of the graph raising, burning a recovery attempt, and reporting a
        mapping gap as if the tool had failed."""
        return kind in QUERY_KINDS
