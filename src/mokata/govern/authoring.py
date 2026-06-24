"""G6 — self-authoring skills via RED-GREEN-REFACTOR-for-docs.

Author a skill test-first: declare doc/spec requirements, watch them FAIL (RED) before
any content exists, write the content until they pass (GREEN), then refine (REFACTOR).
Only a GREEN draft can be promoted to a registry Skill. Clean-room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


class AuthoringError(Exception):
    pass


@dataclass
class DocRequirement:
    name: str
    must_contain: str


@dataclass
class DocCheckResult:
    passed: bool
    failures: List[str] = field(default_factory=list)


class SkillDraft:
    def __init__(self, name: str) -> None:
        self.name = name
        self.requirements: List[DocRequirement] = []
        self.content: Optional[str] = None

    def require(self, name: str, must_contain: str) -> "SkillDraft":
        self.requirements.append(DocRequirement(name, must_contain))
        return self

    def write(self, content: str) -> "SkillDraft":
        self.content = content
        return self

    def check(self) -> DocCheckResult:
        if not self.requirements:
            return DocCheckResult(False, ["no doc requirements declared"])
        if self.content is None:
            return DocCheckResult(
                False, [r.name for r in self.requirements])     # RED — no content yet
        failures = [r.name for r in self.requirements
                    if r.must_contain not in self.content]
        return DocCheckResult(passed=not failures, failures=failures)

    @property
    def status(self) -> str:
        return "green" if self.check().passed else "red"

    def to_skill(self, summary: str, gate: Any) -> Any:
        """Promote a GREEN draft into a registry Skill. Raises if still RED."""
        if not self.check().passed:
            raise AuthoringError(
                f"skill '{self.name}' is not GREEN yet — doc tests still failing")
        from ..skills import Skill
        return Skill(name=self.name, summary=summary, prompt=self.content or "",
                     gate=gate)
