"""BATCHES THAT WERE RETIRED ON PURPOSE — so "retired" and "quietly vanished" are different facts.

Doc 85 §7g, applied to the mutant corpus itself.  A batch driver can leave the tree two ways: a
deliberate retirement, because the thing it graded no longer exists; and an accident — a rename, a
bad merge, a `git rm` in the wrong lane.  With nothing written down the two are the same absence,
and the second one is silent forever.  `MUTANT-BATCHES-RUN-BY-NOBODY` is the shape one level up:
nobody notices what stopped happening.

So a retirement is a RECORD, and the record is graded (`test_a9_mutant_batches_are_swept.py`):

  * every driver named here must be **absent** from `tests/` — a "retired" batch still sitting in
    the tree is a batch somebody will run;
  * no driver still present may be named here;
  * and where the retirement was caused by a deleted subject, that subject must still be **gone**.
    ⭐ That last clause is the useful one: if `migrate_channels.py` is ever reintroduced, this reds
    and says the batch that graded it should come back too.  A register that only recorded the past
    would go stale the moment the past changed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Retirement:
    subject: str            #: the repo-relative path the batch mutated, or "" if not a file
    subject_deleted_by: str #: which release/stage deleted it
    retired_by: str         #: which release/stage retired the batch
    why: str


RETIRED = {
    "_stage17_mutants.sh": Retirement(
        subject="src/mokata/migrate_channels.py",
        subject_deleted_by="0.0.18 lane D slice 4",
        retired_by="0.0.20 stage 09",
        why=(
            "All 22 mutants targeted `migrate_channels.py`, the vault channel migrator, which "
            "slice 4 deleted — every one of them made `mutate.sh` exit 3, so the batch had been "
            "aborting at mutant 1 for two releases while the record still carried its 22/22. "
            "Its graders were gone as well: the driver ran `-p 'test_stage17*.py'` against a tree "
            "with no such file, and `unittest` answers that with `Ran 0 tests ... OK` — a GREEN. "
            "So had the target survived, this batch would have reported 21 survivors and sent "
            "somebody to look for a weakened pin that was never there. Nothing here is "
            "repairable: the subject, the graders and the behaviour all left together."
        ),
    ),
}
