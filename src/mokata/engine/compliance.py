"""D5 — spec-compliance review.

Verifies built code matches the spec: every implemented feature must be traceable to an
acceptance criterion, and every acceptance criterion should have an implementing feature.
A feature traceable to no AC is flagged as an unspecified (extra) feature. Reuses the
AC-mapper from 7A for the AC -> feature coverage side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from .acmapper import map_acceptance_criteria
from .spec import Spec, TestRef

# A built unit and the acceptance criteria it claims to satisfy. Structurally a TestRef
# (name + ac_ids), so the AC-mapper can consume it directly.
FeatureRef = TestRef


@dataclass
class ComplianceFinding:
    kind: str            # "unspecified-feature" | "unimplemented-ac"
    detail: str
    ref: str


@dataclass
class ComplianceResult:
    findings: List[ComplianceFinding] = field(default_factory=list)

    @property
    def has_unspecified(self) -> bool:
        return any(f.kind == "unspecified-feature" for f in self.findings)

    @property
    def compliant(self) -> bool:
        return not self.findings

    def render(self) -> str:
        if not self.findings:
            return "spec-compliance: OK — every feature maps to an AC and vice versa."
        lines = ["spec-compliance findings:"]
        for f in self.findings:
            lines.append(f"  [{f.kind}] {f.detail}")
        return "\n".join(lines)


def spec_compliance_review(spec: Spec, features: List[Any]) -> ComplianceResult:
    ac_ids = set(spec.ac_ids)
    findings: List[ComplianceFinding] = []

    # Extra features: not traceable to any acceptance criterion the spec declares.
    for f in features:
        if not any(a in ac_ids for a in f.ac_ids):
            findings.append(ComplianceFinding(
                "unspecified-feature",
                f"'{f.name}' is not traceable to any acceptance criterion", f.name))

    # Unimplemented ACs: reuse the AC-mapper (treating features as the mapped units).
    mapping = map_acceptance_criteria(spec, features)
    for ac_id in mapping.unmapped_ids:
        findings.append(ComplianceFinding(
            "unimplemented-ac", f"{ac_id} has no implementing feature", ac_id))

    return ComplianceResult(findings=findings)
