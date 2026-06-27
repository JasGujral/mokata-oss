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

import re

from ..manifest import ManifestError
from .graph_backend import CodeReviewGraphBackend, GraphQueryClient
from .grep_backend import GrepBackend
from .query import BackendError, GraphBackend, QueryResult

# Tools that ARE real structural graphs (everything else is the lexical floor).
GRAPH_TOOLS = ("code-review-graph", "serena", "neo4j")

STORY_ANALYSIS_PREFIX = "story_analysis__"


def select_backends(
    router: Any,
    root: str,
    client: Optional[GraphQueryClient] = None,
):
    """Pick (primary, fallback) backends from the router's code_graph resolution.

    Returns the grep floor as the fallback only when the primary is a real graph, so a
    graph failure degrades cleanly. When the floor is already primary, fallback is None.

    Availability probing is UNIFORM across graph tools (Stage 39 / M5), it just happens at the
    layer each tool lives in: command-based tools (`code-review-graph`, `serena`, `ripgrep`) are
    probed by the detector — `router.resolve` only returns a tool whose command is present, so an
    absent tool never reaches here (it resolves to grep). The external `neo4j` needs an extra
    BUILD-TIME probe (below) because a present driver module ≠ a reachable DB. In all cases a
    tool that's present-but-broken at query time raises `BackendError`, which `_run` degrades to
    the grep floor — so the floor is the universal guarantee no matter where a tool fails.
    """
    try:
        res = router.resolve("code_graph")
    except (ManifestError, AttributeError):
        res = None

    if res is not None and res.available and res.tool in GRAPH_TOOLS:
        if res.tool == "neo4j":
            # External Neo4j graph (Stage 35f): build its client from env; if it can't be
            # built (no driver / no NEO4J_* env / DB down) degrade cleanly to the grep floor.
            from .neo4j_backend import build_neo4j_client
            cfg: Dict[str, Any] = {}
            try:
                cfg = router.manifest.tool_config("neo4j")
            except (AttributeError, ManifestError):
                cfg = {}
            gclient = client or build_neo4j_client(cfg)
            if gclient is None:
                return GrepBackend(root=root, name="grep"), None
            return (CodeReviewGraphBackend(name="neo4j", root=root, client=gclient),
                    GrepBackend(root=root))
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


# ----------------------------------------------------- Stage 25 Part B: graph guidance
def graph_guidance(surface: Any) -> str:
    """An ACTIONABLE one-line hint for doctor/status (Stage 25 Part B).

    When a real graph is wired, point at the structural queries it unlocks (reflecting any
    configured path/endpoint from the tool's `config` block, Stage 24A). When only the grep
    floor is active, give a concrete next step to wire one — not just a status line."""
    layer = KnowledgeLayer.from_surface(surface)
    if layer.uses_graph:
        tool = layer.backend_name
        cfg = ""
        try:
            c = surface.manifest.tool_config(tool)
            if c:
                cfg = " [config: " + ", ".join(f"{k}={v}" for k, v in c.items()) + "]"
        except Exception:
            cfg = ""
        return (f"code graph active ({tool}){cfg} — use `mokata query callers <sym>` / "
                f"`callees <sym>` / `blast_radius <sym>` for structural queries.")
    return (
        "no codebase graph wired — running on the grep floor (safe, but lexical). To enable "
        "richer structural queries, install a graph tool (code-review-graph or serena) and "
        "wire it: `mokata init --profile full`, or add it via `mokata config set "
        "tools.<graph>...` / the manifest."
    )


# ----------------------------------------------- Stage 35f: live graph-proximity scorer
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")   # identifier-like tokens (len >= 3)


def make_graph_scorer(layer: Any, query: str):
    """A live graph-keyed relevance scorer for memory's tiered retrieval (closes the 35e
    SHOULD). Returns `(query, item) -> 0..1` that boosts a memory item which references a
    symbol the CODE GRAPH confirms is real and related to the query — or None when no real
    graph is wired (so the graph tier stays silent and lexical+semantic hold).

    Frugal (P11): runs one cheap graph lookup per identifier token in the query (a small set),
    not per memory item; the resulting anchor set scores items by token membership."""
    if layer is None or not getattr(layer, "uses_graph", False):
        return None
    anchors = set()
    for tok in {t for t in _IDENT.findall(query or "")}:
        try:
            res = layer.callers(tok)          # graph confirms the symbol exists/relates
        except Exception:
            continue
        if res is not None and not res.degraded and res.references:
            anchors.add(tok)
    if not anchors:
        return None

    def _scorer(_query: str, item: Any) -> float:
        text = f"{item.subject} {item.value}"
        return 1.0 if any(a in text for a in anchors) else 0.0

    return _scorer


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
