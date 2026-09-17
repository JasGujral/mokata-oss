"""A7 — `RUN-RESOLVER-TDD-PREFIX-DEAD` (0.0.20 stage 07). The candidate set is DERIVED, not typed.

WHAT THE ROW SAID, AND WHAT THE DERIVATION FOUND
------------------------------------------------
The row called `run_resolver`'s `TDD_STATE_PREFIX = "tdd_state__"` a **dead branch**, and doc 105
made the reachability derivation the stage's first deliverable, with *"a derivation showing it is
unreachable is a PASS."*

⛔ **The literal is unreachable and that is the LEAST interesting half.** Nothing in `src/` writes
`tdd_state__`, so the branch in `run_ids()` never fired — but the same line ALSO kept the prefix
that IS written (`tdd_state.TDD_STATE_PREFIX == "tdd_phase__"`) out of the candidate set. The dead
branch and the missing branch were one statement, and only one of them had a name.

**MEASURED before the fix, not argued** — a repo holding run A's TDD phase and a bare checkpoint
for run B:

    tdd_phase__A.json                     run_ids -> set()      resolve -> basis='none'
    tdd_phase__A.json + pipeline_run__B   run_ids -> {'B'}      resolve -> ('B', basis='single')

The second line is the defect. `basis='single'` is a CONFIDENT answer naming the wrong run, and
`gate_hook._red_set` then reads B's (empty) red set for a write that A's red set forbids. ⭐ **That
is worse than ambiguity by this resolver's own design:** `BASIS_AMBIGUOUS` exists precisely so
mokata refuses to pick a window, and here it did not know it was picking. After the fix the same
repo answers `basis='ambiguous'` with both candidates named — a refusal, which is the contract.

WHY THIS FILE IS A STANDING GUARD AND NOT A REGRESSION TEST FOR ONE TYPO
------------------------------------------------------------------------
The comment above the offending line asserted that `gate_hook` imports these prefixes *"so
enforcement and resolution can never disagree about the candidate set."* **`gate_hook:111` does
import the real prefix from `tdd_state`; `run_resolver` declared its own.** The claim was true of
one module and false of the other, and the comment is what kept anyone from checking — doc 85 §7h:
a pin (or a comment) that encodes a false premise then protects the defect.

So this file grades the PROPERTY — every prefix in the candidate set is spelled by the module that
writes it — rather than the instance. A future prefix added as a fresh literal reds here on the day
it is added, whether or not it happens to be misspelled.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata import gate_hook
from mokata import run_resolver as R
from mokata import tdd_state as T
from mokata.govern import resume as _resume


class TheCandidateSetIsSpelledByItsOwners(unittest.TestCase):
    """Every prefix, traced to the module that WRITES that file. Nothing here retypes a string."""

    def test_the_tdd_prefix_is_the_one_tdd_state_actually_writes(self):
        """THE INSTANCE. `tdd_state` owns the file; the resolver must count what it writes."""
        self.assertIs(R.TDD_STATE_PREFIX, T.TDD_STATE_PREFIX,
                      "run_resolver has re-acquired its own spelling of the TDD prefix — that is "
                      "exactly the defect: the resolver counted a key nothing writes while "
                      "gate_hook read the key that is written")
        self.assertIn(T.TDD_STATE_PREFIX, R.RUN_STATE_PREFIXES)

    def test_the_resolver_and_the_gate_hook_agree_about_the_tdd_prefix(self):
        """The comment on the candidate set has always CLAIMED this. Now something checks it."""
        self.assertEqual(gate_hook.TDD_STATE_PREFIX, R.TDD_STATE_PREFIX,
                         "enforcement and resolution disagree about which key holds TDD phase — "
                         "the exact disagreement the candidate-set comment says cannot happen")

    def test_the_checkpoint_prefix_still_matches_its_owner_despite_being_a_literal(self):
        """`CHECKPOINT_PREFIX` stays a LITERAL for a stated latency reason (SI.2 pins the import
        surface). A literal for a reason is fine; a literal nobody compares is how this stage's
        defect happened. So it is compared here, at test time, where the import costs nothing."""
        self.assertEqual(_resume.CHECKPOINT_PREFIX, R.CHECKPOINT_PREFIX)

    def test_every_candidate_prefix_is_written_by_something_in_src(self):
        """THE GENERAL PROPERTY, and the one that catches the NEXT instance. A prefix that no
        module writes cannot make a run a candidate — it can only hide one, which is what
        `tdd_state__` did for four releases."""
        import pathlib
        src = pathlib.Path(R.__file__).resolve().parent
        bodies = {p: p.read_text(encoding="utf-8", errors="replace")
                  for p in src.rglob("*.py") if "__pycache__" not in str(p)}
        unwritten = []
        for prefix in R.RUN_STATE_PREFIXES:
            writers = [p.name for p, body in bodies.items()
                       if p.name != "run_resolver.py" and repr(prefix)[1:-1] in body]
            if not writers:
                unwritten.append(prefix)
        self.assertEqual([], unwritten,
                         f"{unwritten} appear in RUN_STATE_PREFIXES and nowhere else in src/. A "
                         "candidate prefix nothing writes cannot find a run; it can only mask one.")


class ARunWithOnlyTddStateIsFound(unittest.TestCase):
    """The behaviour, driven end to end. These are the two lines measured before the fix."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.sd = R.state_dir(self.root)
        os.makedirs(self.sd, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _put(self, name):
        with open(os.path.join(self.sd, name), "w", encoding="utf-8") as fh:
            fh.write("{}")

    def test_a_run_whose_only_state_is_tdd_phase_is_a_candidate(self):
        self._put(T.state_key("A") + ".json")
        self.assertEqual({"A"}, R.run_ids(self.sd),
                         "a run holding TDD phase state is invisible to the resolver every surface "
                         "reads — the write gate will bind some other run's red set to its writes")
        self.assertEqual("A", R.resolve_run(self.root).run_id)

    def test_the_two_resolvers_in_this_tree_agree_about_a_tdd_only_run(self):
        """`tdd_state._sole_run_id` is a SECOND resolver over the same directory. Before the fix it
        answered 'A' while `run_resolver` answered 'B'. Two resolvers, one directory, opposite
        answers, both confident — and nothing compared them."""
        self._put(T.state_key("A") + ".json")
        self.assertEqual(T._sole_run_id(self.sd), R.resolve_run(self.root).run_id)

    def test_a_tdd_run_beside_a_bare_checkpoint_is_AMBIGUOUS_not_confidently_wrong(self):
        """⭐ THE DEFECT, stated as the behaviour that replaced it. Before: ('B', 'single') — the
        wrong run, named without hedging. After: a refusal that lists both candidates, which is
        what this resolver promises to do when it cannot narrow."""
        self._put(T.state_key("A") + ".json")
        self._put(R.CHECKPOINT_PREFIX + "B.json")
        res = R.resolve_run(self.root)
        self.assertIsNone(res.run_id,
                          f"the resolver picked {res.run_id!r} at basis {res.basis!r} from two "
                          "genuine candidates — picking a window is the whole defect REVIEW-FIX.R1 "
                          "forbade")
        self.assertEqual(("A", "B"), res.candidates)

    def test_the_dead_spelling_is_gone_and_stays_gone(self):
        """The hygiene half. A file under the OLD literal must not make anything a candidate — if
        it did, the resolver would be counting a key that has never been written."""
        self._put("tdd_state__GHOST.json")
        self.assertEqual(set(), R.run_ids(self.sd),
                         "`tdd_state__` is a candidate prefix again — nothing in src/ writes it, so "
                         "every id it contributes is a phantom run")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
