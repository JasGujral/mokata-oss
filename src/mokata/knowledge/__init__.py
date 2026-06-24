"""mokata knowledge layer (Part B) — a codebase brain via orchestration.

mokata persists and queries through an adopted codebase-graph backend and degrades to a
grep floor; it never builds a parser or an in-house graph. The backend is chosen through
the spine's capability router (`code_graph`), so there is one detection path, not two.

Public API:
  - QueryResult / Reference / GraphBackend / QUERY_KINDS / BackendError  (B2 types)
  - GrepBackend                                                          (B3 floor)
  - CodeReviewGraphBackend / GraphQueryClient / SubprocessGraphClient    (B1 adapter)
  - KnowledgeLayer / select_backends / GRAPH_TOOLS                       (B3 policy)
  - StoryAnalysis / build_story_analysis / persist/load_story_analysis   (B6 bridge)
  - KnowledgeIndex / surface_staleness                                   (B4 freshness)
  - scan_anchors / lat_check / LatReport                                 (B5 drift anchors)
"""

from .anchors import (
    Anchor,
    DriftFinding,
    LatReport,
    lat_check,
    load_concepts,
    scan_anchors,
)
from .graph_backend import (
    CodeReviewGraphBackend,
    GraphQueryClient,
    SubprocessGraphClient,
)
from .grep_backend import GrepBackend
from .index import (
    IndexEntry,
    KnowledgeIndex,
    file_fingerprint,
    surface_staleness,
)
from .layer import (
    GRAPH_TOOLS,
    KnowledgeLayer,
    StoryAnalysis,
    build_story_analysis,
    load_story_analysis,
    persist_story_analysis,
    select_backends,
)
from .query import (
    QUERY_KINDS,
    BackendError,
    GraphBackend,
    QueryResult,
    Reference,
)

__all__ = [
    "QUERY_KINDS",
    "BackendError",
    "GraphBackend",
    "QueryResult",
    "Reference",
    "GrepBackend",
    "CodeReviewGraphBackend",
    "GraphQueryClient",
    "SubprocessGraphClient",
    "KnowledgeLayer",
    "select_backends",
    "GRAPH_TOOLS",
    "StoryAnalysis",
    "build_story_analysis",
    "persist_story_analysis",
    "load_story_analysis",
    # B4 — incremental index + staleness
    "KnowledgeIndex",
    "IndexEntry",
    "file_fingerprint",
    "surface_staleness",
    # B5 — drift anchors
    "Anchor",
    "DriftFinding",
    "LatReport",
    "scan_anchors",
    "load_concepts",
    "lat_check",
]
