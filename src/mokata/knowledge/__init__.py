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

LAZY BY DESIGN (0.0.25 Lane HB, HOOK-BUDGET). Every name below is importable from
``mokata.knowledge`` exactly as before; the submodule that defines it is imported on FIRST USE
(PEP 562 ``__getattr__``) instead of when the package loads, so the PostToolUse ``dirty-track`` hook,
which needs only ``freshness.mark_dirty``, no longer loads the graph clients and backends with it.
The eager import list stays VERBATIM under ``TYPE_CHECKING`` for type checkers and the repo's AST
walkers; ``tests/test_hb_hook_budget.py`` pins that list, ``_EXPORTS`` and ``__all__`` together.
"""

from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:  # the eager list, for static readers only — see the module docstring
    from .anchors import (
        Anchor,
        DriftFinding,
        LatReport,
        lat_check,
        load_concepts,
        scan_anchors,
    )
    from .ast_backend import AstBackend, FileEdges, extract_edges, parse_source
    from .crg_client import (
        CRG_COMMAND,
        CodeReviewGraphClient,
        CrgTimeout,
        CrgUnavailable,
        CrgVersionSkew,
        parse_crg_version,
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
        graph_guidance,
        load_story_analysis,
        make_graph_scorer,
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

#: public name -> (submodule, attribute). Resolved on first access by `__getattr__` below.
_EXPORTS: Dict[str, Tuple[str, str]] = {
    "Anchor": ("anchors", "Anchor"),
    "DriftFinding": ("anchors", "DriftFinding"),
    "LatReport": ("anchors", "LatReport"),
    "lat_check": ("anchors", "lat_check"),
    "load_concepts": ("anchors", "load_concepts"),
    "scan_anchors": ("anchors", "scan_anchors"),
    "AstBackend": ("ast_backend", "AstBackend"),
    "FileEdges": ("ast_backend", "FileEdges"),
    "extract_edges": ("ast_backend", "extract_edges"),
    "parse_source": ("ast_backend", "parse_source"),
    "CRG_COMMAND": ("crg_client", "CRG_COMMAND"),
    "CodeReviewGraphClient": ("crg_client", "CodeReviewGraphClient"),
    "CrgTimeout": ("crg_client", "CrgTimeout"),
    "CrgUnavailable": ("crg_client", "CrgUnavailable"),
    "CrgVersionSkew": ("crg_client", "CrgVersionSkew"),
    "parse_crg_version": ("crg_client", "parse_crg_version"),
    "CodeReviewGraphBackend": ("graph_backend", "CodeReviewGraphBackend"),
    "GraphQueryClient": ("graph_backend", "GraphQueryClient"),
    "SubprocessGraphClient": ("graph_backend", "SubprocessGraphClient"),
    "GrepBackend": ("grep_backend", "GrepBackend"),
    "IndexEntry": ("index", "IndexEntry"),
    "KnowledgeIndex": ("index", "KnowledgeIndex"),
    "file_fingerprint": ("index", "file_fingerprint"),
    "surface_staleness": ("index", "surface_staleness"),
    "GRAPH_TOOLS": ("layer", "GRAPH_TOOLS"),
    "KnowledgeLayer": ("layer", "KnowledgeLayer"),
    "StoryAnalysis": ("layer", "StoryAnalysis"),
    "build_story_analysis": ("layer", "build_story_analysis"),
    "graph_guidance": ("layer", "graph_guidance"),
    "load_story_analysis": ("layer", "load_story_analysis"),
    "make_graph_scorer": ("layer", "make_graph_scorer"),
    "persist_story_analysis": ("layer", "persist_story_analysis"),
    "select_backends": ("layer", "select_backends"),
    "QUERY_KINDS": ("query", "QUERY_KINDS"),
    "BackendError": ("query", "BackendError"),
    "GraphBackend": ("query", "GraphBackend"),
    "QueryResult": ("query", "QueryResult"),
    "Reference": ("query", "Reference"),
}


def __getattr__(name: str) -> Any:
    """PEP 562: import the defining submodule on first use and cache the name on the package."""
    from importlib import import_module
    if name not in _EXPORTS and not name.startswith("__"):
        # a SUBMODULE reached as an attribute (`pkg.sub` after `import pkg`): the eager form
        # imported it as a side effect, so it is imported here — anything else is a real miss
        try:
            return import_module(f".{name}", __name__)
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    sub, attr = _EXPORTS[name]
    value = getattr(import_module(f".{sub}", __name__), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = [
    "QUERY_KINDS",
    "BackendError",
    "GraphBackend",
    "QueryResult",
    "Reference",
    "GrepBackend",
    "AstBackend",
    "FileEdges",
    "extract_edges",
    "parse_source",
    "CodeReviewGraphBackend",
    "GraphQueryClient",
    "SubprocessGraphClient",
    "CodeReviewGraphClient",
    "CrgTimeout",
    "CrgUnavailable",
    "CrgVersionSkew",
    "parse_crg_version",
    "CRG_COMMAND",
    "KnowledgeLayer",
    "select_backends",
    "graph_guidance",
    "make_graph_scorer",
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
