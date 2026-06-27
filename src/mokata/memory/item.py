"""C1/C5 — the memory item.

A single durable memory fact, decision, or convention. Carries the metadata the
self-healing layer needs: provenance (where/who/when), a TTL via `expires_at`/`valid_for`
(staleness), and `supersedes`/`depends_on` edges (contradiction resolution + lineage).

This is mokata's own data model; storage backends only serialize it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

# The memory triad — each individually toggleable (C9). This is the STORAGE type that gates
# enablement; the finer `kind` (below) is the institutional-knowledge taxonomy on top of it.
PERSISTENT = "persistent"   # project facts / conventions (C1)
DECISION = "decision"       # project decisions (C2)
EPISODIC = "episodic"       # past conversation turns (C3)
MEMORY_TYPES = (PERSISTENT, DECISION, EPISODIC)

# Stage 36 — the typed-memory "parts": a first-class `kind` on each item (D1), so the project
# brain is surfaced + retrieved by category, not as a flat dump. The new parts are all stored
# as PERSISTENT (so they inherit the persistent toggle) and distinguished by `kind`.
RULE = "rule"                   # hard project rule (always-on; counts to the rules budget)
GUARDRAIL = "guardrail"         # safety/quality constraint (always-on)
BEST_PRACTICE = "best-practice" # recommended pattern/convention (JIT)
CONTEXT = "context"             # domain fact/formula/constraint (JIT)
REFERENCE = "reference"         # distilled key points from a document, + a source pointer (JIT)

# The full taxonomy (parts + the existing decision/episodic), in display order.
MEMORY_KINDS = (RULE, GUARDRAIL, BEST_PRACTICE, CONTEXT, REFERENCE, DECISION, EPISODIC)

# The "parts" captured by /mokata:onboard — all persisted as PERSISTENT, keyed by `kind`.
PART_KINDS = (RULE, GUARDRAIL, BEST_PRACTICE, CONTEXT, REFERENCE)

# Always-on: injected into the SessionStart briefing / rules surface every run (capped, P11).
ALWAYS_ON_KINDS = (RULE, GUARDRAIL)
# JIT: pulled into a skill ONLY when relevant to the task at hand — never a corpus dump (P11).
JIT_KINDS = (BEST_PRACTICE, CONTEXT, REFERENCE)

# Item lifecycle statuses.
ACTIVE = "active"
SUPERSEDED = "superseded"
STALE = "stale"

# Default top-k for by-relevance retrieval (recall_relevant / jit_recall / semantic_search /
# episodic search) — frugal (P11): retrieval returns a small ranked set, never the corpus.
DEFAULT_TOP_K = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_seconds(iso: str, seconds: int) -> str:
    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(seconds=seconds)).isoformat()


@dataclass
class MemoryItem:
    subject: str
    value: str
    mtype: str = PERSISTENT
    id: str = ""
    status: str = ACTIVE
    kind: str = ""               # Stage 36 — the typed-memory part (see MEMORY_KINDS)
    provenance: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[str] = None
    supersedes: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid4().hex

    @classmethod
    def create(
        cls,
        subject: str,
        value: str,
        mtype: str = PERSISTENT,
        author: str = "user",
        source: str = "manual",
        created_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        valid_for: Optional[int] = None,
        id: Optional[str] = None,
        kind: str = "",
        supersedes: Optional[List[str]] = None,
        depends_on: Optional[List[str]] = None,
    ) -> "MemoryItem":
        created = created_at or now_iso()
        if expires_at is None and valid_for is not None:
            expires_at = add_seconds(created, valid_for)
        return cls(
            subject=subject,
            value=value,
            mtype=mtype,
            id=id or uuid4().hex,
            kind=kind,
            provenance={"source": source, "author": author, "created_at": created},
            expires_at=expires_at,
            supersedes=list(supersedes or []),
            depends_on=list(depends_on or []),
        )

    @property
    def created_at(self) -> str:
        return self.provenance.get("created_at", "")

    @property
    def effective_kind(self) -> str:
        """The taxonomy bucket for surfacing/grouping: the explicit `kind`, or the storage
        `mtype` when none was set (so legacy decision/episodic items still group sensibly)."""
        return self.kind or self.mtype

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "value": self.value,
            "mtype": self.mtype,
            "status": self.status,
            "kind": self.kind,
            "provenance": dict(self.provenance),
            "expires_at": self.expires_at,
            "supersedes": list(self.supersedes),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryItem":
        return cls(
            subject=d["subject"],
            value=d["value"],
            mtype=d.get("mtype", PERSISTENT),
            id=d.get("id", ""),
            status=d.get("status", ACTIVE),
            kind=d.get("kind", ""),
            provenance=dict(d.get("provenance", {})),
            expires_at=d.get("expires_at"),
            supersedes=list(d.get("supersedes", [])),
            depends_on=list(d.get("depends_on", [])),
        )
