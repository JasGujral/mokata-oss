"""Invoking `unittest` with a COMPUTED test list, without being able to grade nothing.

NOT A TEST. A small, pure helper plus one argv builder, for
`UNITTEST-BARE-INVOCATION-GRADES-EVERYTHING`.

WHERE THIS CAME FROM, AND IT WAS FOUND BY A MUTANT INSIDE THE STAGE ABOUT THAT EXACT COLLAPSE
---------------------------------------------------------------------------------------------
`python -m unittest` **with no test names falls back to discovery**. So a harness whose derivation
resolved to an EMPTY name list ran **all 7,357 tests and reported success**.

⭐ *"I graded the guarded modules"* and *"I graded nothing, so I graded everything"* **shared one
representation — §7g, in the test runner's own argument handling.**

⚠ **IT WAS CAUGHT ONLY BECAUSE THE MUTANT TOOK ELEVEN MINUTES INSTEAD OF THIRTY SECONDS.** The wall
clock was the tell; no assertion saw it. **A false green whose only symptom is duration is one
nothing in this repo watches.**

⛔ WHAT THIS IS NOT. It is not a wrapper everything must adopt. The repo's 46 existing batch drivers
all pass an explicit `-p` pattern and none is vulnerable today (checked at the row's review, not
assumed). This exists for the harnesses that COMPUTE a list — the shape the hazard needs — and the
guard in `test_a34_a_suite_that_cannot_hang_or_silently_widen` holds the tree to using it there.

THE ONE RULE: a computed list that came back empty is a BROKEN DERIVATION, never an empty job.
`names_or_refuse` raises; it does not return `[]` for a caller to pass on, because the whole defect
is that `[]` and "everything" are the same argv.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from typing import Iterable, List, Sequence


class EmptyGradingSet(AssertionError):
    """A derivation that resolved to nothing. An `AssertionError` on purpose, so a test that
    forgets to catch it FAILS rather than erroring into an unrelated bucket."""


def names_or_refuse(names: Iterable[str], what: str) -> List[str]:
    """The derived names, or raise. NEVER returns an empty list.

    `what` NAMES the derivation and is phrased as a noun the message can read after "the" — e.g.
    `"tty-sweep module derivation"`. The useful half of this failure is *which* derivation came
    back empty: a message saying "the list was empty" sends a reader to the runner instead of to
    the rule that produced nothing."""
    out = [n for n in names if n]
    if not out:
        raise EmptyGradingSet(
            "the %s resolved to NOTHING, so there is nothing to grade.\n"
            "  ⛔ This is refused rather than run, because `python -m unittest` with no names "
            "falls back to DISCOVERY: an empty computed list would have graded the WHOLE suite "
            "and reported success, which is how a blinded derivation once ran 7,357 tests and "
            "passed. 'I graded the set' and 'I graded nothing, so I graded everything' must not "
            "share an argv (§7g).\n"
            "  Fix the derivation, or state explicitly that the set is empty and skip." % what)
    return out


def unittest_argv(python: str, names: Sequence[str], what: str) -> List[str]:
    """`[python, '-m', 'unittest', <names...>]`, with the empty case unreachable.

    ⚠ MODULE NAMES AND NOT A `-p` GLOB, deliberately. A glob that matches nothing makes `unittest`
    print `Ran 0 tests ... OK` — a different false green, and the one `mutate.sh`'s exit-6 contract
    exists for. Explicit names make a missing module an ERROR with the module's name in it."""
    return [python, "-m", "unittest", *names_or_refuse(names, what)]
