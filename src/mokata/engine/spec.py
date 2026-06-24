"""D2/D3 — the spec model: acceptance criteria and the tests that trace to them.

A spec is a title plus concrete, testable acceptance criteria (ACs). The AC-mapper and
the completeness gate are built on this; a `TestRef` records which ACs a test covers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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

    @property
    def ac_ids(self) -> List[str]:
        return [c.id for c in self.criteria]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "criteria": [c.to_dict() for c in self.criteria],
            "approach": self.approach,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Spec":
        return cls(
            title=d.get("title", ""),
            criteria=[AcceptanceCriterion.from_dict(c) for c in d.get("criteria", [])],
            approach=d.get("approach"),
            source=d.get("source", ""),
        )
