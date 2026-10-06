"""0.0.21 stage 06 — the whole-tree READING of the tty-prompt sweep. One test, ~48 seconds.

`SUITE-HANGS-AT-A-TTY-ON-THREE-TESTS`, the CLASS guard, running in the suite.

⚠ SEPARATE FROM `test_a34_a_suite_that_cannot_hang_or_silently_widen` ON COST GROUNDS, and the
split is worth stating. That module grades the MECHANISM — the instrument fires, a correct
interactive test is not flagged, the derivation is non-empty, the guard excludes itself — on
planted modules, in about a second. This one is the MEASUREMENT over the real tree, and it is a
second run of the 32 prompt-reaching modules.

⛔ THE COST IS THE REASON FOR THE SPLIT: every mutant in `_stage06_no_hang_no_widen_mutants.sh`
pays whatever its target module costs, and ten mutants against a slow module is minutes — which
also blows `test_a9`'s per-driver budget. **No mutant points at this file, and that is correct
rather than a gap: there is nothing here to mutate. It is a reading, not a rule.**

⚠ AND THE CORPUS WAS NARROWED ON MEASURED COST, which is a coverage trade and is stated as one.
The first version swept every module mentioning a stdin double or a bare `input(` — **32 modules,
48 s** — and `test_b1_internal_tests_meet_their_subject` runs the WHOLE suite twice, so that was
~144 s per full run and `test_b*` went from 148 s to over 175 s the day it landed.

⭐ Narrowing to the modules that name a prompt ENTRY POINT (`read_yes_no`, `_cli_ask`,
`read_yes_no_prompt`) is **17 modules, 17 s**. A module that installs a stdin double but never
reaches an entry point cannot reach `input()` on the ambient stream, and the "correct pattern is not
flagged" property is graded on a planted subject in `test_a35` rather than by sweeping the real
ones. ⛔ **WHAT THE NARROWING GIVES UP, SAID PLAINLY:** a test that reaches a prompt through a CLI
function without naming any entry point matches no static rule — but it never did, which is why the
weekly whole-suite leg exists and why `test_a35` pins that it still discovers everything. Skipping
in the nested runs was tried and is NOT available: `test_b1` asserts the dev run skips NOTHING, and
that premise is what makes the mirror's skips mean anything.

⭐ WHY IT RUNS AT ALL, GIVEN IT IS SLOW. The row came in PARTIAL: the three instances were closed at
0.0.20 and verified by hand, and the CLASS GUARD it also asks for was not built — leaving the
instrument's only caller a weekly opt-in workflow on a repo with **no Actions minutes since
2026-08-17** (`§5.5`, stage 08's blocker). *A guard whose only runner is disabled is a guard nobody
has.*

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import test_a35_the_tty_sweep_instrument_works as SWEEP_TESTS


# ⛔ NO `skipUnless` GUARD, AND THE ABSENCE IS DELIBERATE RATHER THAN AN OVERSIGHT. The first
# version guarded on `_tty_prompt_sweep.py` and `_tty_sweep/` existing — and **both SHIP**: they
# live under `tests/` and no control in `sync-public.sh` excludes them. So the guard could never
# fire, and `test_b1_internal_tests_meet_their_subject` said so three ways at once: the class read
# as UNDECIDABLE in the dev census, the mirror's skip count disagreed with the derived one by 6,
# and the guarded class named no internal subject. ⭐ A guard over a file that ships is not a
# weaker guard, it is a dead one (§7f/§7d) — so it is deleted, and the class is simply shippable.
class NoTestInThisTreeReachesAPromptOnTheAmbientStdin(unittest.TestCase):
    """⚠ IMPORTS THE HARNESS AS A FUNCTION, AND DOES NOT SUBCLASS. My first version subclassed
    the sweep class to inherit the fixtures, and the cost went from 48 s to **152 s**:
    `unittest`'s loader collects any `TestCase` subclass bound in a module's namespace, so the
    imported parent ran a second time under its alias AND every inherited test ran again under the
    subclass. ⭐ *Inheriting a TestCase to reuse a helper re-runs the parent's whole suite* — the
    helper is a function now, which is what it always was."""

    def test_no_module_reaches_a_prompt_on_the_ambient_stream(self):
        """⭐ THE ROW'S CLASS GUARD.

        ⛔ WHY CI CANNOT CATCH THIS BY RUNNING THE SUITE: CI is never a TTY, so it takes the SAME
        fail-closed arm the defective tests take, and reports green. Every consent gate in mokata
        is fail-closed off a TTY — correct at runtime, and a trap in a suite, because **the machine
        decides which arm a test grades.** At a real terminal the same test reaches `input()`, and
        if it also redirected stdout the prompt is swallowed and the run **hangs with nothing on
        screen** until a human types into the void. That happened on 2026-08-26: a stray `yes`
        typed into a blank screen was consumed, mokata approved, and the failure read like a
        regression.

        ⚠ IT COSTS ~48 SECONDS, measured. That is a second run of the prompt-reaching subset (32
        modules), not of the suite; a full second pass is ~10 minutes and is what the weekly
        workflow is still for — the two runners cover different corpora and are not redundant
        (§7f)."""
        modules = SWEEP_TESTS._prompt_reaching_modules()
        self.assertGreaterEqual(len(modules), 10,
                                "the derivation collapsed, which would make this reading vacuous "
                                "— see test_a35 for the mechanism's own guards: %r" % (modules,))
        proc, records = SWEEP_TESTS.run_sweep(modules)
        self.assertEqual(
            [], records,
            "%d test(s) reach a consent prompt with the AMBIENT stdin installed. At a real "
            "terminal each of these HANGS the suite with nothing on screen — and the answer then "
            "comes from whoever is watching. Install an explicit stdin double "
            "(`_support.stdin_is(_support.NoTtyStdin())`) and let the real reader read it, which "
            "is the correct pattern `test_a3_decline_strands_at_spec` models:\n%s"
            % (len(records), "\n".join("  %s\n      %s" % (r["test"], r["stack"][-1])
                                       for r in records)))
        # ⚠ The EXIT CODE IS NOT ASSERTED, and `test_a35`'s footprint test pins why: the forced
        # `isatty()` is inherited by subprocesses, and `hook_cli.py` correctly uses it to decide
        # whether a JSON payload was piped — so tests that drive a hook child fail under the sweep
        # while being correct. The journal is the measurement. What IS asserted is that a verdict
        # was produced at all, because a runner that died before the first test leaves the same
        # empty journal as a clean tree (§7g).
        self.assertRegex(proc.stdout + proc.stderr, r"Ran \d+ tests",
                         "the sweep produced NO verdict line, so the empty journal says nothing:\n"
                         f"--- stdout ---\n{proc.stdout[-1500:]}\n"
                         f"--- stderr ---\n{proc.stderr[-1500:]}")


if __name__ == "__main__":
    unittest.main()
