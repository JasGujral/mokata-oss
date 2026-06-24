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

# The memory triad — each individually toggleable (C9).
PERSISTENT = "persistent"   # project facts / conventions (C1)
DECISION = "decision"       # project decisions (C2)
EPISODIC = "episodic"       # past conversation turns (C3)
MEMORY_TYPES = (PERSISTENT, DECISION, EPISODIC)

# Item lifecycle statuses.
ACTIVE = "active"
SUPERSEDED = "superseded"
STALE = "stale"


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
            provenance={"source": source, "author": author, "created_at": created},
            expires_at=expires_at,
            supersedes=list(supersedes or []),
            depends_on=list(depends_on or []),
        )

    @property
    def created_at(self) -> str:
        return self.provenance.get("created_at", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "value": self.value,
            "mtype": self.mtype,
            "status": self.status,
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
            provenance=dict(d.get("provenance", {})),
            expires_at=d.get("expires_at"),
            supersedes=list(d.get("supersedes", [])),
            depends_on=list(d.get("depends_on", [])),
        )
