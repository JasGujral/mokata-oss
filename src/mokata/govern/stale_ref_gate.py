"""DB.S7c2 — the STALE-REF refusal for the approve path (doc 85 §3 naming; its own id, distinct
from GR-PA's and GR.S3's).

A BOUND CHECK, not an eleventh backed gate: like GR-PA's step-ran check it is computed by the
caller and passed into `BrainstormSession.approve`, which refuses through the same
`BrainstormGateError`. Absent, it is a no-op and approval is byte-identical to before.

WHAT IT REFUSES, and only this: a prior-art citation stamped with one `index_epoch` being approved
against a store that is now at a different one. The `id` was minted by a store that has since moved
on, so acting on it means acting on memory that may no longer say what the human was shown.

WHAT IT NEVER REFUSES, and the reason each is somebody else's signal:
  * A WINDOW-CLOSED EDGE. Closing an edge's validity window is R3 history. The epoch is aggregated
    from the ITEM table alone (`PostgresBackend.index_epoch`), so this is structural here — there
    is no edge input to ignore.
  * A RETIRED-STATUS ITEM. Supersession is K2's signal and healing owns it. This module cannot see
    `status`: it is handed two strings and compares them.
  * A MISSING PRIOR-ART STEP. That is GR-PA's refusal (`prior_art_gate`), and duplicating it here
    would give one omission two different messages.

LOUD, NEVER SILENT-CORRECT. The verdict refuses and names its fix; it never re-stamps a stale
citation to the current epoch. Auto-refreshing would turn "you are looking at old memory" into
"you are looking at old memory, silently relabelled as current" — the exact failure STALE-REF
exists to stop.

A COMPARISON, NOT A QUERY. Nothing here opens a store, resolves an id, or issues SQL. The current
epoch is read ONCE by the caller (`memory.staleness.read_index_epoch`) and every citation is then
judged by string comparison — which is what keeps a gate on the approve path from acquiring a read
surface, and what keeps the cost at one read rather than one per citation.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence

from ..memory.staleness import is_stale

# The distinct id (doc 85 §3) — a citation-freshness check on the approve path.
GATE_ID = "stale-ref"
CONSUMER = "stale-ref (re-query, don't act on an aged citation)"


@dataclass
class StaleRefOutcome:
    """The STALE-REF verdict for the approve path (doc 85 §3: a `*Outcome`).

    `refused` is the check; `stale_ids` names the citations that aged, so the refusal points at
    something the human can look up rather than at a feeling."""

    consumer: str
    refused: bool
    stale_ids: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return not self.refused

    def render(self) -> str:
        return self.reason


def check_stale_refs(*, decisions: Sequence[Any], current_epoch: str) -> StaleRefOutcome:
    """The shared verdict. REFUSE iff some citation carries an `index_epoch` that differs from
    `current_epoch`.

    Pure: it reads two strings per citation and writes nothing — in particular it never repairs a
    stale stamp. An un-stamped citation, or an empty `current_epoch` (STALE-REF OFF on the SQLite
    floor), yields no opinion and therefore no refusal; `memory.staleness.is_stale` owns that
    direction and the reasoning for it.
    """
    stale = [str(getattr(d, "id", "") or "") for d in (decisions or [])
             if is_stale(str(getattr(d, "index_epoch", "") or ""), current_epoch)]
    reason = ""
    if stale:
        named = ", ".join(stale)
        reason = (
            "REFUSED: approving on prior-art citations minted against an older memory index. "
            f"These recalled decisions were cited at a different index_epoch than the store now "
            f"holds: {named}. The memory they point at may have been rewritten, retired or "
            "replaced since you were shown it, so approving now would decide on a view of team "
            "memory that no longer exists.\n"
            "Road out: re-run the prior-art pass for this approach, read the refreshed decisions, "
            "then approve. mokata will NOT silently re-stamp the old citations as current — that "
            "would hide exactly the change you need to see.")
    return StaleRefOutcome(consumer=CONSUMER, refused=bool(stale),
                           stale_ids=stale, reason=reason)


def brainstorm_stale_ref_gate(session: Any, approach_name: str, *,
                              current_epoch: str) -> StaleRefOutcome:
    """The IN-SESSION verdict: judge the named approach's RECORDED prior-art citations — the ones
    restored from run state — against the store's current epoch.

    An approach with no prior-art evidence yields no refusal here. "The step never ran" is GR-PA's
    verdict; this one has nothing to judge, and answering anyway would be a second opinion on a
    question that already has an owner."""
    evidence = getattr(session, "prior_art", {}).get(approach_name)
    return check_stale_refs(decisions=getattr(evidence, "decisions", []) or [],
                            current_epoch=current_epoch)
