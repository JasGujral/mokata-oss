"""GR-PA — the prior-art step-RAN gate (doc 85 §3 naming; distinct gate id from GR.S3).

A BOUND STEP, not a new hard gate: approval requires the prior-art step to have RUN for the chosen
approach — never that it FOUND something. This is the whole distinction from GR.S3's
`graph.required` gate: GR.S3 refuses a DEGRADED blast radius (a graph-QUALITY verdict); this refuses
only a MISSING step (a step-RAN verdict). A degraded/absent graph still runs the prior-art step
(evidence-gathering), so this gate never refuses on graph quality and duplicates none of GR.S3's
refusal semantics.

There is no override here because there is nothing to escape: the step ALWAYS can run — it degrades
to the lexical/recall tier but still records `ran=True`. The only way to be refused is to genuinely
skip looking; the fix is to look. The refusal reuses `BrainstormGateError`'s verdict class (raised by
`BrainstormSession.approve`), and the verdict shape follows doc 85 §3 (`*Outcome` = gate verdict).

The CLI skill loop and the MCP loop both compute this ONE verdict via `brainstorm_prior_art_gate`
(the MCP loop over the restored persisted session), so the refusal is identical across surfaces.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The distinct gate id (doc 85 §3) — a step-ran check on the approve path, NOT a graph-quality gate.
GATE_ID = "prior-art"
CONSUMER = "prior-art (extend, don't re-implement)"


@dataclass
class PriorArtGateOutcome:
    """The prior-art step-ran verdict for the approve path (doc 85 §3: a `*Outcome`).

    `refused` is the gate: approval must NOT proceed until the prior-art step has run for the chosen
    approach. `ran`/`tier` echo the recorded evidence for a legible refusal."""

    consumer: str
    ran: bool
    refused: bool
    tier: str = ""
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return not self.refused

    def render(self) -> str:
        return self.reason


def check_prior_art_ran(*, ran: bool, tier: str = "", consumer: str = CONSUMER) -> PriorArtGateOutcome:
    """The shared verdict. REFUSE iff the prior-art step has NOT run for the approach. The refusal is
    informative + actionable (never a stack trace): it names the ONE road out — run the pass — and is
    explicit that this is a step-RAN check, so empty findings ("no prior art found") is a PASS."""
    refused = not bool(ran)
    reason = ""
    if refused:
        reason = (
            "REFUSED: approach approval before the prior-art step has run. mokata binds a PRIOR-ART "
            "pass into brainstorm before approval — graph query for existing implementations related "
            "to the approach's symbols + recall of related team decisions — so \"extend, don't "
            "re-implement\" is on the table BEFORE an approach is approved.\n"
            "Road out: run the prior-art pass for this approach, then approve. This is a step-RAN "
            "check — it NEVER requires that prior art was found, only that you looked; an empty "
            "result (\"no prior art found via <tier>\") is a first-class pass.")
    return PriorArtGateOutcome(consumer=consumer, ran=bool(ran), refused=refused,
                               tier=tier, reason=reason)


def brainstorm_prior_art_gate(session: Any, approach_name: str) -> PriorArtGateOutcome:
    """The step-ran verdict the CLI skill AND the MCP loop both compute the same way: read whether the
    chosen approach has recorded prior-art evidence in the session's run state, and return the shared
    verdict. Pass the result to `BrainstormSession.approve(prior_art_gate=...)`."""
    res = (getattr(session, "prior_art", {}) or {}).get(approach_name)
    ran = bool(getattr(res, "ran", False))
    tier = getattr(res, "tier", "") if res is not None else ""
    return check_prior_art_ran(ran=ran, tier=tier)
