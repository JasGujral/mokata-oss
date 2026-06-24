"""A6 + H5 — typed adapter contract.

A6: an `AdapterContract` declares which capabilities a tool provides, so `negotiate`
can report coverage and the unmet gaps across a set of needs. H5: `validate_adapter`
checks a third party's adapter dict against the contract before it is wired in. Reuses
the spine's capability vocabulary (schema kinds/detect types) — one capability model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..schema import KNOWN_DETECT_TYPES, KNOWN_TOOL_KINDS


@dataclass
class AdapterContract:
    name: str
    provides: List[str]
    kind: str = "external"
    version: Optional[str] = None
    detect: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "provides": list(self.provides),
                             "kind": self.kind, "version": self.version}
        if self.detect is not None:
            d["detect"] = dict(self.detect)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AdapterContract":
        return cls(name=data["name"], provides=list(data.get("provides", [])),
                   kind=data.get("kind", "external"), version=data.get("version"),
                   detect=data.get("detect"))


def validate_adapter(data: Any) -> List[str]:
    """Return contract-violation errors for a candidate adapter (empty == valid)."""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["adapter must be an object"]
    if not isinstance(data.get("name"), str) or not data.get("name"):
        errors.append("adapter.name must be a non-empty string")
    provides = data.get("provides")
    if not isinstance(provides, list) or not provides:
        errors.append("adapter.provides must be a non-empty array")
    else:
        for p in provides:
            if not isinstance(p, str) or not p:
                errors.append("adapter.provides entries must be non-empty strings")
                break
    kind = data.get("kind", "external")
    if kind not in KNOWN_TOOL_KINDS:
        errors.append(f"adapter.kind '{kind}' invalid (one of {KNOWN_TOOL_KINDS})")
    detect = data.get("detect")
    if detect is not None:
        if not isinstance(detect, dict):
            errors.append("adapter.detect must be an object")
        else:
            dt = detect.get("type")
            if dt not in KNOWN_DETECT_TYPES:
                errors.append(f"adapter.detect.type '{dt}' invalid")
            elif dt in ("command", "python_module", "path") and not detect.get("name"):
                errors.append(f"adapter.detect.name is required for type '{dt}'")
    return errors


@dataclass
class CoverageReport:
    needs: List[str]
    covered: Dict[str, List[str]] = field(default_factory=dict)
    gaps: List[str] = field(default_factory=list)

    @property
    def fully_covered(self) -> bool:
        return not self.gaps

    def render(self) -> str:
        lines = ["capability coverage:"]
        for need in self.needs:
            who = self.covered.get(need) or []
            mark = ", ".join(who) if who else "— UNMET"
            lines.append(f"  {need}: {mark}")
        if self.gaps:
            lines.append(f"gaps: {', '.join(self.gaps)}")
        else:
            lines.append("gaps: none — full coverage")
        return "\n".join(lines)


def negotiate(needs: List[str], adapters: List[AdapterContract]) -> CoverageReport:
    """Report which needs each adapter set covers, and which remain unmet (A6)."""
    covered = {need: [a.name for a in adapters if need in a.provides] for need in needs}
    gaps = [need for need in needs if not covered[need]]
    return CoverageReport(needs=list(needs), covered=covered, gaps=gaps)
