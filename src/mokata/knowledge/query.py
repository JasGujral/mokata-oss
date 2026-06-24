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

# The structural questions the API answers. Stable across backends.
QUERY_KINDS = ("callers", "callees", "implementers", "imports", "blast_radius")


class BackendError(Exception):
    """A backend failed to answer a query (e.g. the graph tool errored). The layer
    catches this to degrade to the grep floor rather than hard-fail (A3)."""


@dataclass
class Reference:
    """One structural hit: a location and the symbol it relates to."""

    path: str
    line: int
    snippet: str = ""
    symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "line": self.line,
                "snippet": self.snippet, "symbol": self.symbol}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reference":
        return cls(path=d["path"], line=int(d.get("line", 0)),
                   snippet=d.get("snippet", ""), symbol=d.get("symbol"))


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
