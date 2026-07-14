"""D2/D3 — the spec model: acceptance criteria and the tests that trace to them.

A spec is a title plus concrete, testable acceptance criteria (ACs). The AC-mapper and
the completeness gate are built on this; a `TestRef` records which ACs a test covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..spec_scope import SCOPE_KEY, SpecScope, scope_from_dict


@dataclass
class AcceptanceCriterion:
    id: str
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AcceptanceCriterion":
        return cls(id=d["id"], text=d.get("text", ""))


@dataclass
class TestRef:
    name: str
    ac_ids: List[str] = field(default_factory=list)
    path: str = ""
    line: int = 0


@dataclass
class Spec:
    title: str
    criteria: List[AcceptanceCriterion] = field(default_factory=list)
    approach: Optional[str] = None       # the approved-approach name, if known
    source: str = ""
    # DK.S0 — the domains-in-play, classified at brainstorm from the approach's graph surface and
    # persisted here as a FIRST-CLASS constraint beside the ACs + approach (human-approved +
    # legible). develop/review engage EXACTLY these; a domain reached only later amends the spec.
    # A JSON field only — the store SCHEMA is untouched; a legacy spec with no key reads as [].
    domains: List[str] = field(default_factory=list)
    # SI-DEV — the MACHINE-CHECKABLE scope: the paths this spec authorized, and the items it
    # explicitly DEFERRED (each carrying the paths/markers a hook can recognise it by). The gate
    # hook enforces exactly this and nothing more; a spec with no scope (every spec written before
    # SI-DEV) reads as None and is NOT policed. Additive, like `domains` above: a JSON field only,
    # serialized ONLY when set, so a scope-less spec's persisted bytes are unchanged.
    scope: Optional["SpecScope"] = None

    @property
    def ac_ids(self) -> List[str]:
        return [c.id for c in self.criteria]

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "title": self.title,
            "criteria": [c.to_dict() for c in self.criteria],
            "approach": self.approach,
            "domains": list(self.domains),
        }
        if self.scope is not None:
            out[SCOPE_KEY] = self.scope.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Spec":
        return cls(
            title=d.get("title", ""),
            criteria=[AcceptanceCriterion.from_dict(c) for c in d.get("criteria", [])],
            approach=d.get("approach"),
            source=d.get("source", ""),
            domains=list(d.get("domains", [])),
            scope=scope_from_dict(d.get(SCOPE_KEY)),
        )
