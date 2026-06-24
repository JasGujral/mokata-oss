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
    SQLiteBackend,
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
    DECISION,
    EPISODIC,
    MEMORY_TYPES,
    PERSISTENT,
    SUPERSEDED,
    MemoryItem,
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
    "ACTIVE",
    "SUPERSEDED",
    "STALE",
    "MemoryBackend",
    "SQLiteBackend",
    "ObsidianBackend",
    "NativeMemoryBackend",
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
]
