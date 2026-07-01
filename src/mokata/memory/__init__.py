"""mokata memory layer (Part C) — persistent, self-healing, default-on, human-gated.

Memory is a native part of the framework, on by default, with per-type toggles. It is
durable and pluggable (SQLite default / Obsidian / native-memory — storage only; the
logic is mokata's own), heals by SURFACING contradictions and staleness for human
approval (never silent rewrite), and gates every durable write. The memory triad —
persistent (C1), decision (C2), episodic (C3) — is individually toggleable. Consolidation
(C7) is proposal-only and human-gated; it never auto-applies.

Public API:
  - MemoryItem + types/statuses                                   (C1/C5 model)
  - SQLiteBackend / ObsidianBackend / NativeMemoryBackend         (C4 storage)
  - MemoryStore / enabled_memory_types / select_memory_backend    (C1/C2/C6/C8/C9 logic)
  - HealingProposal / detect_issues / render_proposal             (C5 surfacing)
  - EpisodicMemory / lexical_score                                (C3 episodic)
  - ConsolidationProposal / propose_consolidations                (C7 consolidation)
"""

from .backends import (
    MemoryBackend,
    MemoryClient,
    NativeMemoryBackend,
    ObsidianBackend,
    PostgresBackend,
    PostgresUnavailable,
    SQLiteBackend,
    build_postgres_backend,
)
from .share import (
    MEMORY_SHARE_FILENAME,
    ImportResult,
    MemoryShareError,
    export_memory,
    import_memory,
    load_memory_share,
)
from .migrate import (
    SUPPORTED as MIGRATE_BACKENDS,
    MigrateError,
    MigrateResult,
    build_named_backend,
    migrate_memory,
)
from .embed import EMBED_DIM, HashingEmbedder, cosine, make_embedder
from .tiered import RetrievalHit, tiered_recall
from .vector import (
    PgVectorBackend,
    VectorUnavailable,
    build_pgvector_backend,
)
from .consolidation import (
    MERGE,
    PRUNE,
    SUMMARIZE,
    ConsolidationProposal,
    propose_consolidations,
    render_consolidation,
)
from .episodic import EpisodicMemory, lexical_score
from .healing import (
    CONTRADICTION,
    STALE,
    HealingProposal,
    detect_issues,
    render_proposal,
)
from .item import (
    ACTIVE,
    ALWAYS_ON_KINDS,
    DEFAULT_TOP_K,
    BEST_PRACTICE,
    CONTEXT,
    DECISION,
    EPISODIC,
    GUARDRAIL,
    JIT_KINDS,
    MEMORY_KINDS,
    MEMORY_TYPES,
    PART_KINDS,
    PERSISTENT,
    REFERENCE,
    RULE,
    SUPERSEDED,
    MemoryItem,
)
from .brain import (
    always_on_lines,
    group_by_kind,
    jit_recall,
    normalize_kind,
)
from .intelligence import (
    MemoryHealth,
    RecallExplanation,
    assess_health,
    explain_recall,
    memory_health,
    why_surfaced,
)
from .store import (
    MEMORY_SETTINGS_KEY,
    HealingResult,
    MemoryDisabledError,
    MemoryError,
    MemoryStats,
    MemoryStore,
    WriteResult,
    build_backend,
    enabled_memory_types,
    select_memory_backend,
)

__all__ = [
    "MemoryItem",
    "PERSISTENT",
    "DECISION",
    "EPISODIC",
    "MEMORY_TYPES",
    "DEFAULT_TOP_K",
    "ACTIVE",
    "SUPERSEDED",
    "STALE",
    # Stage 36 — typed-memory parts (the project brain)
    "RULE",
    "GUARDRAIL",
    "BEST_PRACTICE",
    "CONTEXT",
    "REFERENCE",
    "MEMORY_KINDS",
    "PART_KINDS",
    "ALWAYS_ON_KINDS",
    "JIT_KINDS",
    "group_by_kind",
    "always_on_lines",
    "jit_recall",
    "normalize_kind",
    "MemoryBackend",
    "SQLiteBackend",
    "ObsidianBackend",
    "NativeMemoryBackend",
    "PostgresBackend",
    "PostgresUnavailable",
    "build_postgres_backend",
    "MemoryClient",
    "MemoryStore",
    "MemoryStats",
    "WriteResult",
    "HealingResult",
    "MemoryError",
    "MemoryDisabledError",
    "enabled_memory_types",
    "select_memory_backend",
    "build_backend",
    "MEMORY_SETTINGS_KEY",
    "HealingProposal",
    "detect_issues",
    "render_proposal",
    "CONTRADICTION",
    # C3 — episodic
    "EpisodicMemory",
    "lexical_score",
    # C7 — consolidation (proposal-only)
    "ConsolidationProposal",
    "propose_consolidations",
    "render_consolidation",
    "MERGE",
    "SUMMARIZE",
    "PRUNE",
    # Stage 35b — memory export/import (file share)
    "export_memory",
    "import_memory",
    "load_memory_share",
    "ImportResult",
    "MemoryShareError",
    "MEMORY_SHARE_FILENAME",
    # Stage 35c — migrate between backends
    "migrate_memory",
    "build_named_backend",
    "MigrateResult",
    "MigrateError",
    "MIGRATE_BACKENDS",
    # Stage 35e — vector backend + tiered semantic retrieval
    "HashingEmbedder",
    "make_embedder",
    "cosine",
    "EMBED_DIM",
    "tiered_recall",
    "RetrievalHit",
    # Stage 59 — memory intelligence (explainable retrieval + health nudge)
    "why_surfaced",
    "explain_recall",
    "RecallExplanation",
    "MemoryHealth",
    "memory_health",
    "assess_health",
    "PgVectorBackend",
    "build_pgvector_backend",
    "VectorUnavailable",
]
