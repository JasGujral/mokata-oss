"""D4 — pre-mortem + probes.

An adversarial-hardening phase that runs before the completeness gate. It derives probes
(risks the spec must answer) from the APPROVED approach: each declared con of the chosen
approach becomes a risk probe, plus a small set of standard pre-mortem angles ("assume it
shipped and failed — why?"). Convergent and grounded in the brainstorm Handoff, distinct
from the divergent brainstorm itself.

Clean-room: the pre-mortem device is mokata's own framing; no external text is copied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

# Standard failure lenses applied to every approved approach.
_ANGLES = [
    ("failure", "Assume '{name}' shipped and failed within a quarter — what is the "
                "single most likely cause, and does the spec address it?"),
    ("scale", "Where does '{name}' break under 10x load or data, and which acceptance "
              "criterion proves it holds?"),
    ("rollback", "If '{name}' must be reverted after release, what is the rollback path "
                 "and is it specified?"),
]


@dataclass
class Probe:
    id: str
    question: str
    risk: str
    category: str
    source_con: Optional[str] = None


@dataclass
class PreMortemResult:
    approach: str
    probes: List[Probe] = field(default_factory=list)

    def summary(self) -> str:
        return (f"pre-mortem for approved approach '{self.approach}': "
                f"{len(self.probes)} probe(s) to harden the spec against.")


def derive_probes(handoff: Any) -> List[Probe]:
    """Derive risk probes from the approved approach in the brainstorm handoff."""
    approach = handoff.approach
    probes: List[Probe] = []
    n = 0

    # 1) every declared downside of the CHOSEN approach is a concrete risk to probe
    for con in approach.cons:
        n += 1
        probes.append(Probe(
            id=f"P{n}",
            question=f"How does the spec mitigate the known downside: {con}?",
            risk=con,
            category="approach-risk",
            source_con=con,
        ))

    # 2) standard adversarial pre-mortem angles, grounded in the approach name
    for category, template in _ANGLES:
        n += 1
        probes.append(Probe(
            id=f"P{n}",
            question=template.format(name=approach.name),
            risk=f"{category} risk for {approach.name}",
            category=category,
        ))
    return probes


def pre_mortem(handoff: Any) -> PreMortemResult:
    return PreMortemResult(approach=handoff.approach.name,
                           probes=derive_probes(handoff))
