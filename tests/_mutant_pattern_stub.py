#!/usr/bin/env python3
"""A STAND-IN MUTATOR THAT MUTATES NOTHING — the instrument behind the batch sweep (0.0.20 s09).

WHY THIS EXISTS
---------------
`MUTANT-BATCHES-RUN-BY-NOBODY`: measured on this tree, **45 batch drivers declare 817 mutants,
`.github/workflows/` references none of them, `scripts/` references none of them, and exactly ONE
is executed by anything** — `_run_mutants.sh`, by `test_mutant_batch_driver`, against a stub. So
every *"9/9 killed"* in this project's record rests on a batch nothing re-runs.

`MUTATION-FIXTURE-QUOTES-SOURCE-IT-DOES-NOT-OWN` says how that costs something concrete. A mutant
whose `old` string is hand-typed out of a file somebody else maintains — a dependabot-managed action
pin, a vendored block — stops matching the day that file changes. `mutate.sh` then exits **3**, the
driver aborts, and every mutant after it in the list is never graded. Four mutants died that way in
one batch and it took five days to notice.

⭐ **THE KEY OBSERVATION, AND IT IS WHAT MAKES A CHEAP SWEEP POSSIBLE: those four died at exit 3,
and exit 3 needs NO TEST EXECUTION AT ALL.** "does this `old` still occur exactly once in this
file" is a string count. So the expensive half of mutation testing (apply, run the suite, restore —
measured at roughly 1.5-2 hours for all 817 on this box, un-shardable because `mutate.sh` holds one
global lock) can stay a nightly job, while the half that actually rots gets graded on every push
for a few seconds.

WHAT THIS STUB DOES, AND THE ONE PLACE IT DELIBERATELY DIVERGES FROM `mutate.sh`
--------------------------------------------------------------------------------
Same argv contract, the single source for which is `scripts/mutate.sh`'s header:

    <label> <file> <old> <new> <test-pattern>

It counts occurrences of `old` in `file` and **writes one JSON line per mutant** to
`$MUTANT_SWEEP_LOG`. It never opens the file for writing, never runs a test, never takes the lock.

⛔ **THE DIVERGENCE: on a pattern that does not occur exactly once, this stub still exits 0.**
`mutate.sh` exits 3 there and the driver correctly aborts the batch — that is the right behaviour
for a real grading run, where everything after an unapplied mutation is untrustworthy. **It is the
wrong behaviour for a SWEEP**, whose entire job is to find EVERY stale pattern in one pass. A sweep
that stopped at the first offender would report one of four and read as if it had checked
everything, which is the silent-truncation shape doc 85 warns about — and, more sharply, it is the
same defect the sweep exists to close, committed by the sweep. The divergence is therefore
deliberate, is stated here, and is asserted by `test_the_stub_never_aborts_a_batch`.

⚠ **The verdict this stub prints is `RED` and it means NOTHING.** No mutation was applied and no
test ran. It is emitted only so a driver's accounting (`ran`/`red`/`green` counters, `TOTAL`
reconciliation) proceeds to the end of its list. **Nothing may read a sweep run as a mutation
score.** `test_the_sweep_is_not_a_mutation_score` pins that this file cannot produce a GREEN.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sys

# ⛔ THE DRIVER↔STUB CHANNEL IS UTF-8, DECLARED HERE AND DECODED AS UTF-8 BY `_mutant_sweep`.
# `scripts/mutate.sh` — the thing this file stands in for — is bash `printf`: it hands the label's
# bytes to the pipe untouched and cannot fail on any console codepage. Python ENCODES instead, and
# on a GitHub Windows runner `sys.stdout` comes up **cp1252**, where a label carrying `★` (U+2605)
# or `≥` (U+2265) raises `UnicodeEncodeError` — AFTER the record has been written — so the driver
# reads a nonzero exit, aborts its batch, and every mutant after that one is never graded. The
# sweep then reports a corpus that reached almost nothing, which says "no stale patterns" for the
# same reason a working one does (§7f).
#
# MEASURED at the 0.0.20 cut: `_a11 swept 1`, `_pg_floor swept 2`, `_stage28 swept 5` on the
# Windows legs of run 35484977365 — and the SAME THREE NUMBERS on Linux with
# `PYTHONIOENCODING=cp1252`, which is what named the cause rather than reproducing a symptom.
#
# `src/mokata/__init__._force_utf8_io` already does exactly this for the product; this stub never
# imports mokata, which is precisely why it was the one thing left speaking the console codepage.
# UNCONDITIONAL, unlike the product's `os.name != "nt"` early return: the guard that keeps this
# fixed runs on Linux, and a repair that is a no-op there would be graded by nobody (§7i).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        # A caller that handed us a replaced, detached or closed stream owns that decision; the
        # verdict line is cosmetic to the driver, which reads only the leading RED/GREEN word.
        pass

#: The one status a real `mutate.sh` returns when the pattern occurred zero times or twice. Named
#: rather than typed at the comparison, because this file's whole subject is that number.
MUTATE_EXIT_PATTERN_NOT_APPLIED = 3


def main(argv):
    if len(argv) < 6:
        # bash's own status for a missing `${n:?...}` expansion — mutate.sh exit 1, "usage or
        # environment, before anything was touched". Matched so a driver invoked wrongly fails the
        # same way under the stub as under the real mutator.
        sys.stderr.write("stub: usage: <label> <file> <old> <new> <test-pattern>\n")
        return 1
    label, target, old, new, pattern = argv[1:6]

    try:
        with open(target, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        occurrences = body.count(old)
        unreadable = ""
    except OSError as exc:
        occurrences = -1
        unreadable = f"{type(exc).__name__}: {exc}"

    record = {
        "label": label, "target": target, "pattern": pattern,
        "occurrences": occurrences, "unreadable": unreadable,
        "applies": occurrences == 1,
        # The mutated text is recorded but never written. A batch whose `new` is IDENTICAL to its
        # `old` grades nothing at all, and no existing check looks for it.
        "is_a_no_op_mutation": old == new,
        # The pattern itself, so the sweep can COUNT IT AGAIN in its own process. A sweep that
        # trusted `applies` would be trusting the only component that can green 45 batches by
        # being wrong -- see `_mutant_sweep.verify_records`.
        "old_text": old,
        # The replacement too, so a sweep record is a COMPLETE mutant: enough to replay this exact
        # mutation through the real `mutate.sh` without re-parsing the driver that produced it.
        # That is what makes a re-aimed pattern checkable -- a pattern that applies again is not
        # yet a pattern that still KILLS, and only a real run can say which.
        "new_text": new,
    }
    log = os.environ.get("MUTANT_SWEEP_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    if occurrences == 1:
        print(f"RED   {label}  [SWEEP — nothing was mutated, nothing was run]")
    else:
        print(f"RED   {label}  [SWEEP — PATTERN DOES NOT APPLY: {occurrences} occurrence(s) "
              f"in {target}; a real run would exit {MUTATE_EXIT_PATTERN_NOT_APPLIED} here and "
              f"abort the batch]")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
