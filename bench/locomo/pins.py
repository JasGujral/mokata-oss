"""BM (LOCOMO) — the 8 PINS, agreed before any code (doc 84 BM row), and where each is ENFORCED.

A pin that is only prose is a wish. Each pin below names the code that enforces it, and
`tests/test_bm_locomo.py` reds when that enforcement is removed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

PINS: Tuple[Tuple[int, str, str], ...] = (
    (1, "Gated on the embeddings leg — no entry ships measured on HashingEmbedder.",
        "harness.run_benchmark records each arm's embedder id; report.publish refuses a hashing arm."),
    (2, "ONE benchmark for v1: LOCOMO. LongMemEval and BEAM are follow-ons.",
        "dataset.py knows exactly one dataset."),
    (3, "The TITLE declares the embedder, the gate posture (benchmark auto-approve, ledgered) and "
        "that mokata performs NO LLM fact extraction.",
        "report.title() — fixed template; no flag edits it."),
    (4, "Judge and answerer DECLARED and ARCHIVED per G2: raw streams beside verdicts, "
        "{arm}/{conversation}/run{N}/, >= 5 repeats per cell.",
        "harness layout + report.publish refuses undeclared/fake judge or answerer, or < 5 repeats."),
    (5, "Tokens-per-retrieval is a first-class metric (substitutes G2's per-turn context growth).",
        "harness records tokens per retrieval for every question; report tabulates it per arm."),
    (6, "Arms are the DB.S8d ladder (A jaccard / B fts / C +vector / D +expansion) plus a "
        "FULL-CONTEXT control. The ablation is the contribution.",
        "retrieval.ARMS; report.publish refuses a run missing any arm."),
    (7, "No number is published that cannot be REPRODUCED from a committed artifact.",
        "report.publish refuses unless the dataset sha matches its lock and every run file exists; "
        "the report prints the archive digest and the reproduce command."),
    (8, "STOP CONDITION: if the honest headline is materially below the published field, it is "
        "published AS-IS or NOT AT ALL — never reframed. And (doc 99 § 0.0.23 row 13) no benchmark "
        "is published on a ranker disclosed as regressed.",
        "report.publish refuses while RANKER_DISCLOSURES has an open entry, and refuses a "
        "below-field headline unless acknowledge_below_field=True — which changes nothing in the "
        "title or headline, only adds the gap line."),
)

#: The H-4 ranker regression DISCLOSURES, as recorded in doc 84 at 2026-10-15 (re-derived by the
#: 0.0.21 / 0.0.22 stage-00 ledgers). An OPEN entry blocks publication (pin 8). Closing one is a
#: reviewed edit here citing the row that closed it — never a flag.
RANKER_DISCLOSURES: Dict[str, Dict[str, str]] = {
    "ARM-D-CONFOUND-BACK": {
        "state": "open",
        "detail": "PARTIAL: recall identity restored 2026-08-26; MRR divergence D vs B+expansion "
                  "-2.75pp at 100k OPEN (attributed to the embedder's drag).",
        "arms": "C,D"},
    "VECTOR-TIER-NOISE": {
        "state": "open",
        "detail": "RETIRED 2026-10-07 by no-placement (0.0.22 stage 00), NOT by a fix: the MRR half "
                  "(-0.81pp vs the Jaccard floor at 100k) was still open when retired.",
        "arms": "C,D"},
    "FTS-NORMALIZE-FLATTENS": {
        "state": "closed",
        "detail": "CLOSED 2026-09-20 (0.0.21 stage 00): the claim is no longer true in the tree.",
        "arms": "B,C,D"},
}


def open_disclosures() -> List[str]:
    return [f"{k}: {v['detail']} (arms {v['arms']})" for k, v in RANKER_DISCLOSURES.items()
            if v["state"] != "closed"]
