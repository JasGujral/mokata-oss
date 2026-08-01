"""DB.S7c2 — STALE-REF, the memory-handle half: the `index_epoch` a citation is stamped with, and
the PURE comparison that decides whether it has gone stale.

WHY AN EPOCH AND NOT A PER-ITEM REVISION. A stamp only earns its keep for a citation that OUTLIVES
the store that minted it (doc 84:180). Validating such a citation per-item would mean re-reading
every cited row — a query per citation, on a gate. An `index_epoch` inverts that: ONE cheap read
mints/reads the epoch, and every outstanding citation is then validated by comparing two strings.
That is why `is_stale` takes no store and `govern.stale_ref_gate` opens no read surface.

WHY `index_epoch` AND NOT `generation`. `generation` is already taken, in this same package, for
the cumulative SCHEMA/migration generation in `PRAGMA user_version` (`backends.py:39-63`). Two
"generations" in one package is the second-vocabulary-for-one-concept failure `edges.py` decision
#1 already refused. GR.S4 was checked first for a term to reuse and has none — it carries per-FILE
fingerprints (`file_fingerprint`, `KnowledgeIndex`) and a session-scoped freshness state, but no
whole-index counter. Settled at the DB.S7c2 grounding, promoted into doc 85 §3 when it was built.

TEAM-ONLY IS STRUCTURAL, NOT A POLICY CHOICE. `_revision` — the only per-row change counter mokata
has — is stamped exclusively on the Postgres read paths (`backends.py:1239/:1296/:1341`). The
SQLite floor tracks nothing of the kind, so a local epoch would have to be INVENTED, and an
invented one that never moves is the always-equal-and-always-passes stamp doc 84:180 forbids twice.
So the floor gets an honest OFF, declared HERE and nowhere else: `INDEX_EPOCH_OFF` is the ABSENCE
of a stamp, and `is_stale` reads an absent stamp on either side as "no opinion", never as staleness.
The cost is stated rather than hidden: on a local install STALE-REF does nothing at all.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any

# The floor's honest OFF — the ABSENCE of a stamp, never a constant that would compare equal to
# itself forever. This is the ONE place in the package that decides what OFF means; a second
# module deciding it is how the floor's behaviour stops being auditable from a single read.
INDEX_EPOCH_OFF = ""


def read_index_epoch(backend: Any) -> str:
    """The whole-index epoch of `backend`'s store, or `INDEX_EPOCH_OFF` when it cannot mint one.

    Capability-probed with `hasattr`, deliberately: the SQLite floor does not implement
    `index_epoch` and must not be taught to fake one. That absence is the EXPECTED path and is
    silent — a local install has no epoch and never did.

    A backend that DOES claim the capability and then fails to answer is a different story, and the
    handler is broad but LOUD (D5). Broad because every way the read can fail says one thing to the
    caller — we could not establish an epoch — and none of them are nameable at module scope
    (psycopg is an optional extra). Loud because the consequence is invisible otherwise: citations
    go out un-stamped, the approve-path check silently never fires again, and a team that switched
    STALE-REF on by moving to Postgres would go on believing it is protecting them. Degrading to
    OFF rather than raising is still right — a citation stamp is evidence, and evidence that cannot
    be gathered is absent evidence, not a broken recall.
    """
    reader = getattr(backend, "index_epoch", None)
    if reader is None:
        return INDEX_EPOCH_OFF          # the floor: STALE-REF is OFF here, and always was
    try:
        return str(reader() or INDEX_EPOCH_OFF)
    except Exception as exc:
        from ..degrade import FAILURE_SCHEMA, note_degraded
        note_degraded("stale-ref", FAILURE_SCHEMA, detail=str(exc),
                      fallback="citations go out un-stamped — STALE-REF cannot fire",
                      fix="run `mokata team init` to (re-)provision the shared schema, then "
                          "re-run the prior-art pass so its citations are stamped again")
        return INDEX_EPOCH_OFF


def is_stale(stamped: str, current: str) -> bool:
    """Has the index moved since `stamped` was minted? A PURE comparison — no store, no query, no
    id resolution, no clock. This is the whole validation.

    Both sides must be present for there to be an opinion: an un-stamped citation (a floor mint) or
    an un-readable current epoch (STALE-REF OFF) yields False. That is the fail-OPEN direction on
    purpose — this gate refuses a human's approval, and refusing on "we don't know" would make an
    unconfigurable local install unusable while proving nothing about the citation.
    """
    if not stamped or not current:
        return False
    return stamped != current
