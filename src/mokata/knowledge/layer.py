"""B3 + B6 — the knowledge layer.

`KnowledgeLayer` is the typed structural-query surface the engine uses. It selects its
backend THROUGH the existing capability router (`router.resolve("code_graph")`) — there
is deliberately no second tool-detection path. Policy: prefer the graph when the router
resolves to a real graph tool; otherwise use the grep floor. If the graph backend errors
at query time, the layer degrades to grep rather than failing (A3).

B6: a story's queries are recorded and can be persisted via the existing state surface,
so analysis enriches a durable layer instead of being recomputed each run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..manifest import ManifestError
from .graph_backend import CodeReviewGraphBackend, GraphQueryClient
from .grep_backend import GrepBackend
from .query import BackendError, GraphBackend, QueryResult

# Tools that ARE real structural graphs (everything else is the lexical floor).
GRAPH_TOOLS = ("code-review-graph", "serena")

STORY_ANALYSIS_PREFIX = "story_analysis__"


def select_backends(
    router: Any,
    root: str,
    client: Optional[GraphQueryClient] = None,
):
    """Pick (primary, fallback) backends from the router's code_graph resolution.

    Returns the grep floor as the fallback only when the primary is a real graph, so a
    graph failure degrades cleanly. When the floor is already primary, fallback is None.
    """
    try:
        res = router.resolve("code_graph")
    except (ManifestError, AttributeError):
        res = None

    if res is not None and res.available and res.tool in GRAPH_TOOLS:
        primary = CodeReviewGraphBackend(name=res.tool, root=root, client=client)
        return primary, GrepBackend(root=root)

    # Resolved to ripgrep/grep, or no code_graph capability at all -> grep floor.
    name = res.tool if (res is not None and res.available and res.tool) else "grep"
    return GrepBackend(root=root, name=name), None


class KnowledgeLayer:
    def __init__(self, primary: GraphBackend,
                 fallback: Optional[GraphBackend] = None,
                 index: Any = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.index = index            # optional KnowledgeIndex (B4) for staleness
        self.history: List[QueryResult] = []

    # --- construction (always via the router) -------------------------------
    @classmethod
    def from_router(cls, router: Any, root: str,
                    client: Optional[GraphQueryClient] = None,
                    index: Any = None) -> "KnowledgeLayer":
        primary, fallback = select_backends(router, root, client=client)
        return cls(primary, fallback, index=index)

    @classmethod
    def from_surface(cls, surface: Any,
                     client: Optional[GraphQueryClient] = None,
                     index: Any = None) -> "KnowledgeLayer":
        return cls.from_router(surface.router, surface.root, client=client,
                               index=index)

    # --- introspection ------------------------------------------------------
    @property
    def backend_name(self) -> str:
        return self.primary.name

    @property
    def uses_graph(self) -> bool:
        return self.primary.is_graph

    # --- typed queries ------------------------------------------------------
    def _run(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        try:
            result = self.primary.query(kind, target, depth=depth)
        except BackendError:
            if self.fallback is None:
                raise
            result = self.fallback.query(kind, target, depth=depth)
            result.degraded = True
            result.note = (
                f"primary backend '{self.primary.name}' failed; "
                f"fell back to '{self.fallback.name}'"
            )
        # B4: surface staleness for any referenced file changed since indexing.
        if self.index is not None:
            from .index import surface_staleness
            root = getattr(self.primary, "root", None)
            if root:
                surface_staleness(result, self.index, root)
        self.history.append(result)
        return result

    def callers(self, symbol: str) -> QueryResult:
        return self._run("callers", symbol)

    def callees(self, symbol: str) -> QueryResult:
        return self._run("callees", symbol)

    def implementers(self, name: str) -> QueryResult:
        return self._run("implementers", name)

    def imports(self, target: str) -> QueryResult:
        return self._run("imports", target)

    def blast_radius(self, symbol: str, depth: int = 2) -> QueryResult:
        return self._run("blast_radius", symbol, depth=depth)


# --------------------------------------------------------------- B6 story bridge
@dataclass
class StoryAnalysis:
    """The persistable record of a story's structural analysis."""

    story_id: str
    summary: str
    symbols: List[str] = field(default_factory=list)
    queries: List[Dict[str, Any]] = field(default_factory=list)
    backend: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "story_id": self.story_id,
            "summary": self.summary,
            "symbols": list(self.symbols),
            "queries": list(self.queries),
            "backend": self.backend,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StoryAnalysis":
        return cls(
            story_id=d["story_id"],
            summary=d.get("summary", ""),
            symbols=list(d.get("symbols", [])),
            queries=list(d.get("queries", [])),
            backend=d.get("backend", ""),
        )


def build_story_analysis(story_id: str, summary: str,
                         layer: KnowledgeLayer) -> StoryAnalysis:
    """Assemble a StoryAnalysis from the queries a layer has run this story."""
    symbols: List[str] = []
    queries: List[Dict[str, Any]] = []
    for r in layer.history:
        if r.target not in symbols:
            symbols.append(r.target)
        queries.append({"kind": r.kind, "target": r.target,
                        "count": r.count, "backend": r.backend})
    return StoryAnalysis(story_id=story_id, summary=summary, symbols=symbols,
                         queries=queries, backend=layer.backend_name)


def persist_story_analysis(store: Any, analysis: StoryAnalysis) -> str:
    """Persist a story's analysis via the state surface (explicit, never silent)."""
    return store.write(STORY_ANALYSIS_PREFIX + analysis.story_id, analysis.to_dict())


def load_story_analysis(store: Any, story_id: str) -> Optional[StoryAnalysis]:
    data = store.read(STORY_ANALYSIS_PREFIX + story_id)
    return StoryAnalysis.from_dict(data) if data else None
