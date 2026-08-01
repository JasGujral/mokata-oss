"""H-6 S4 — STALE-REF, the CODE-ANCHOR half: the refusal for the approve path.

MOVED here from DB.S7 at that stage's plan of record (decision #3, 2026-07-30). The split was
clean: the MEMORY-HANDLE half is a generation stamp on the node handles DB.S7a introduced and
shipped at DB.S7c2 (`memory/staleness.py` + `stale_ref_gate.py`); THIS half needs the durable
anchor→fingerprint record H-6 S1 creates, which DB.S7 does not have.

A BOUND CHECK, not an eleventh backed gate — exactly its sibling's posture. It is computed by the
caller and passed into `BrainstormSession.approve`, refusing through the same `BrainstormGateError`.
Absent, it is a no-op and approval is byte-identical. doc 85 §4 still names ten backed gates, and a
test holds that number.

WHAT IT REFUSES, and only this: approving an approach whose prior-art citations are anchored to
code that has CHANGED since those decisions were recorded. The decision the human was shown is
about code that is no longer the code in front of them.

WHY IT IS A SEPARATE ID FROM THE MEMORY-HANDLE HALF. They answer different questions from different
evidence. `stale-ref` asks whether the memory INDEX moved under a citation (one `index_epoch`
string, team-mode only, blind to the repo). This asks whether the CODE moved under one (a content
hash per anchor, works on a local install, blind to the store). One message for both would be a
message that is wrong about half its cases.

THE PIN THAT GOVERNS THIS MODULE (H-6 plan of record, P4 — NON-NEGOTIABLE):

    THIS REFUSAL AND H-6's PROPOSAL ARM DECLINE UNDER THE SAME CONDITIONS.

Both consume the ONE `AnchorVerdict` and neither derives a predicate of its own — this module
computes no hash, opens no record and never touches `fingerprint_forces_refresh`. A refusal costs
more than a proposal (it stops a human's approval), so a bridge that failed LOUD where H-6 itself
declines would be strictly worse than no bridge. "Refuse whenever unsure" is the false claim
`knowledge/about_code.py:8-11` forbids, wearing a safety costume: a symbol anchor on the AST/grep
floor is not evidence of staleness, it is an absence of evidence, and refusing on it would make an
un-adopted install unusable while proving nothing. The equality is asserted across the FULL verdict
matrix, both shapes, rather than at whichever points are convenient.

LOUD, NEVER SILENT-CORRECT. The verdict refuses and names its fix; it never re-stamps the moved
anchor to the current fingerprint. Auto-refreshing would turn "you are looking at a decision about
older code" into "…silently relabelled as current" — the exact failure STALE-REF exists to stop,
and the reason `record_anchors(refresh=True)` is a human's word rather than this module's.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence

# The distinct id (doc 85 §3) — a CODE-freshness check on the approve path, beside `stale-ref`'s
# memory-freshness one.
GATE_ID = "code-anchor-ref"
CONSUMER = "code-anchor-ref (re-read the code, don't act on a decision about older code)"


@dataclass
class CodeAnchorOutcome:
    """The verdict for the approve path (doc 85 §3: a `*Outcome`).

    `moved_anchors` names the code, `stale_ids` names the citations — a human can look up both,
    which is the difference between a refusal and a feeling."""

    consumer: str
    refused: bool
    moved_anchors: List[str] = field(default_factory=list)
    stale_ids: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return not self.refused

    def render(self) -> str:
        return self.reason


def check_code_anchors(*, decisions: Sequence[Any], root: str,
                       layer: Any = None) -> CodeAnchorOutcome:
    """The shared verdict. REFUSE iff some cited decision has an `about_code` anchor whose verdict
    is MOVED.

    **`moved`, and nothing else** — `DECLINED` is not a refusal here and that is P4, not leniency.
    A declined anchor means H-6 could not establish that anything changed (no baseline, a file
    absent from this tree, a symbol with no authoritative graph to name its definition site), and
    the proposal arm raises nothing in exactly those states. This gate must not invent a stronger
    opinion from the same evidence.

    Writes nothing, in particular never repairs a stamp it found moved.
    """
    from ..knowledge.anchor_fingerprints import evaluate_anchors, read_record

    record = read_record(root)
    moved: List[str] = []
    stale_ids: List[str] = []
    paths: List[str] = []
    symbols: List[str] = []
    sites: dict = {}
    if record:
        # One record read for the whole pass, then N verdicts — the cost model DB.S7c2 set for the
        # sibling half ("one cheap read, N comparisons"), kept here so a gate on the approve path
        # does not acquire a per-citation read surface.
        for d in decisions or []:
            anchors = [str(a) for a in (getattr(d, "about_code", None) or []) if a]
            if not anchors:
                continue
            hit = [v for v in evaluate_anchors(anchors, root=root, layer=layer,
                                               record=record) if v.moved]
            if not hit:
                continue
            for v in hit:
                moved.append(v.anchor)
                (symbols if v.shape == "symbol" else paths).append(v.anchor)
                sites[v.anchor] = v.path
            stale_ids.append(str(getattr(d, "id", "") or ""))
    moved, stale_ids = sorted(set(moved)), sorted(set(stale_ids))
    paths, symbols = sorted(set(paths)), sorted(set(symbols))
    reason = ""
    if moved:
        # THE WORDING PIN (H-6 decision #2) ON THE REFUSAL SIDE. The proposal arm words the two
        # shapes differently because they rest on different evidence, and a refusal that collapsed
        # them into one sentence would quietly upgrade every path anchor's claim to the symbol
        # arm's — while COSTING more, since this stops a human rather than informing one. So the
        # two are listed separately, each saying only what its evidence supports. (An earlier draft
        # had one shared sentence; the surface-level pin is what caught it.)
        lines = ["REFUSED: approving on prior-art decisions whose CODE has moved since they were "
                 f"recorded (cited by: {', '.join(stale_ids)})."]
        if paths:
            lines.append(
                "  These FILES are no longer the files they were: " + ", ".join(paths) +
                ". The decisions themselves may still be right — what mokata can see is that the "
                "code underneath them changed.")
        if symbols:
            lines.append(
                "  These anchors' DEFINITION SITES changed: " +
                ", ".join(f"{s} (defined in {sites.get(s) or 'an unnamed file'})" for s in symbols) +
                ". The code graph named those sites, so this is the anchored code itself moving.")
        lines.append(
            "Road out: re-read the changed code, decide whether those decisions still hold, then "
            "approve. mokata will NOT silently re-stamp the anchors as current — that would hide "
            "exactly the change you need to see.")
        reason = "\n".join(lines)
    return CodeAnchorOutcome(consumer=CONSUMER, refused=bool(moved),
                             moved_anchors=moved, stale_ids=stale_ids, reason=reason)


def handoff_code_anchor_gate(handoff: Any, *, root: str,
                             layer: Any = None) -> CodeAnchorOutcome:
    """The EMIT-SEAM verdict — the ONE both production surfaces (CLI `mokata spec emit`, MCP
    `spec_emit`) compute, exactly as `prior_art_gate.handoff_prior_art_gate` is.

    Read the CHOSEN approach's prior-art citations from the DURABLE `approved_approach` Handoff
    (stamped at approval in `BrainstormSession.handoff`) and judge their `about_code` anchors
    against the code as it stands now. The Handoff is read rather than a live session for the reason
    its prior-art sibling documents: it is guaranteed present exactly when there is an approved
    approach to gate.

    **FAIL-OPEN, and the asymmetry with `handoff_prior_art_gate` is deliberate.** That gate
    fail-CLOSES on a legacy/absent record, because "the step never ran" is exactly what an absent
    record means. Here an absent record means the opposite — no citations, or no baseline for the
    anchors they carry — which by decision #6 is NO OPINION, not staleness. Fail-closing would
    refuse every approach a pre-H-6 brainstorm produced, on no evidence at all, which is the
    "refuse whenever unsure" false claim P4 forbids.
    """
    pa = getattr(handoff, "prior_art", None)
    return check_code_anchors(decisions=getattr(pa, "decisions", []) or [],
                              root=root, layer=layer)


def brainstorm_code_anchor_gate(session: Any, approach_name: str, *, root: str,
                                layer: Any = None) -> CodeAnchorOutcome:
    """The IN-SESSION verdict: judge the named approach's RECORDED prior-art citations — the ones
    restored from run state — against the code as it stands now.

    An approach with no prior-art evidence yields no refusal. "The step never ran" is GR-PA's
    verdict (`prior_art_gate`), and answering it here as well would give one omission two different
    messages."""
    evidence = getattr(session, "prior_art", {}).get(approach_name)
    return check_code_anchors(decisions=getattr(evidence, "decisions", []) or [],
                              root=root, layer=layer)
