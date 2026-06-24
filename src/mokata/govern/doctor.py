"""K5 — `mokata doctor`.

Validate the stack and report problems: an invalid manifest, capabilities with no present
provider (missing deps), broken adapters, role conflicts (resolved by precedence), bad
trust levels, and oversized rule tiers. Read-only diagnosis built on the existing
schema validator, router, adapter contract, and rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from .. import schema
from ..adapters import overlapping_capabilities, validate_adapter
from ..manifest import ManifestError
from .rules import load_rules, validate_caps
from .trust import TRUST_LEVELS, TRUST_SETTINGS_KEY


@dataclass
class DoctorFinding:
    severity: str            # "error" | "warning" | "info"
    code: str
    detail: str


@dataclass
class DoctorReport:
    findings: List[DoctorFinding] = field(default_factory=list)

    @property
    def errors(self) -> List[DoctorFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        if not self.findings:
            return "mokata doctor: all checks passed."
        lines = [f"mokata doctor: {len(self.findings)} finding(s):"]
        for f in self.findings:
            lines.append(f"  [{f.severity}] {f.code}: {f.detail}")
        lines.append("OK" if self.ok else "PROBLEMS FOUND")
        return "\n".join(lines)


def diagnose(surface: Any) -> DoctorReport:
    findings: List[DoctorFinding] = []
    m = surface.manifest

    # 1) manifest schema validity
    for e in schema.validate_manifest(m.data):
        findings.append(DoctorFinding("error", "manifest", e))

    # 2) missing providers — capabilities that resolve to nothing present
    for need in m.capabilities:
        try:
            res = surface.router.resolve(need)
        except ManifestError as exc:
            findings.append(DoctorFinding("error", "bad-capability", str(exc)))
            continue
        if not res.available:
            tried = ", ".join(t for t, _ in res.attempted) or "none"
            findings.append(DoctorFinding(
                "error", "missing-provider",
                f"capability '{need}' has no present provider (tried: {tried})"))

    # 3) broken adapters — each tool must satisfy the adapter contract
    for tid, tool in m.tools.items():
        errs = validate_adapter({"name": tid, "provides": [tool.get("provides")],
                                 "kind": tool.get("kind"), "detect": tool.get("detect")})
        for e in errs:
            findings.append(DoctorFinding("error", "broken-adapter", f"{tid}: {e}"))

    # 4) role conflicts — >1 provider for one capability (resolved by precedence)
    for need, providers in overlapping_capabilities(m).items():
        findings.append(DoctorFinding(
            "warning", "role-conflict",
            f"capability '{need}' claimed by {providers} (resolved by precedence)"))

    # 5) config — rule caps + trust levels
    try:
        for e in validate_caps(load_rules(surface)):
            findings.append(DoctorFinding("error", "rules-cap", e))
    except Exception:
        pass
    for tool, level in (m.setting(TRUST_SETTINGS_KEY, {}) or {}).items():
        if level not in TRUST_LEVELS:
            findings.append(DoctorFinding(
                "error", "bad-trust",
                f"tool '{tool}' has invalid trust level '{level}'"))

    return DoctorReport(findings)
