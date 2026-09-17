"""The shipped `ux.notify` table is held to what the notifier ACTUALLY DOES, per platform.

0.0.20, `MANIFEST-UX-NOTIFY-ROW-OVERPROMISES-ON-WINDOWS` (filed at 0.0.19 stage 09a). The row:
`docs/reference/manifest.md` documented `ux.notify` as defaulting `true` with **no Windows
caveat**, while Windows ships **no visual arm at all** — a toast needs a PowerShell script string,
which `notify.py`'s rule 1 forbids. The row closes with the reason this file exists:

> ⛔ *"The gate must not let this ship uncaveated: it is the exact defect B5 grades, in a document
> rather than a constant."*

⭐ **THE CAVEAT IS PRESENT TODAY AND NOTHING GRADED IT.** `test_f13_windows_audio_arm` grades the
code and the shipped refusal notice; **no test read `docs/reference/manifest.md` at all**, so the
row was closeable on evidence and the evidence could rot silently — the same shape as the constant
B5 grades, one artefact over.

WHAT IS DERIVED, AND FROM WHERE
--------------------------------
The coverage set is obtained by **CALLING `notify._notification_argv` for each probed platform**,
never by reading its `if platform ==` chain: a source read still agrees with itself after the
dispatch changes shape, and prose agreeing with a stale reading of the code is the entire failure
mode. The doc's own `only` clause is then read POSITIVELY and the two sets compared, so both
directions come out of one derivation — a platform claimed without an arm is an OVERPROMISE, one
with an arm left out is UNDERSTATED.

⚠ §7j — THE PROBE SET IS DECLARED. `sys.platform` is open-ended; this probes the three the project
ships for and says so. `unnamed_platforms` reports any probed name the rows never mention, so the
vocabulary cannot go stale into vacuity.

🔴 THIS FILE'S OWN INSTRUMENT WENT VACUOUSLY GREEN THREE TIMES WHILE BEING WRITTEN
-----------------------------------------------------------------------------------
Each draft reported **GREEN with an EMPTY derived set**, and each for a different reason:

  1. it probed with a body of its own choosing, and `notify._require_fixed` refuses any body
     outside the frozen `BODIES` set — so every platform reported *no arm*;
  2. it read the doc for DENIAL phrases, and *"macOS and Linux only"* contains both a denial-shaped
     phrase and the name it does not deny — so macOS read as excluded;
  3. it probed a function that does not exist (`_visual_argv`; the real name is
     `_notification_argv`) inside `except Exception: continue`, so an **AttributeError became
     "this platform has no arm."**

⛔ **All three failed in the SAME DIRECTION: green.** `TheInstrumentCannotGoVACUOUSLYGREEN` is the
class that exists because of them, and it asserts the derived set is non-empty, that the dispatcher
resolves, and that a missing dispatcher RAISES rather than reporting an empty world.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _notify_doc_truth as nd

from mokata import notify


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_DOC = os.path.join(ROOT, "docs", "reference", "manifest.md")

#: A row shaped like the one the defect describes: a promise with no coverage claim at all.
_ROW_NO_CLAIM = ("| `ux.notify` | bool | `true` | notify you when it is YOUR move — opt-out |\n"
                 "| `ux.notify_audio` | bool | `true` | play a sound; a successful call means it "
                 "was queued with the OS |\n")


def _doc():
    with open(MANIFEST_DOC, "r", encoding="utf-8") as fh:
        return fh.read()


class TheInstrumentCannotGoVACUOUSLYGREEN(unittest.TestCase):
    """🔴 The class three drafts of this file needed. Every one of them reported GREEN on nothing."""

    def test_the_dispatcher_this_probe_calls_actually_EXISTS(self):
        self.assertTrue(hasattr(notify, nd.VISUAL_DISPATCHER),
                        "notify.%s is gone — every platform would probe as 'no arm' and the whole "
                        "file would pass having asked nothing" % nd.VISUAL_DISPATCHER)

    def test_a_MISSING_dispatcher_RAISES_rather_than_reporting_an_empty_world(self):
        class _Gone(object):
            BODIES = notify.BODIES
        with self.assertRaises(AttributeError):
            nd.platforms_with_visual_arm(_Gone())

    def test_the_derived_coverage_set_is_NOT_EMPTY(self):
        self.assertTrue(nd.platforms_with_visual_arm(notify),
                        "no probed platform has a visual arm, which is either false or means the "
                        "probe is broken — and both make every comparison below vacuous")

    def test_the_probe_body_is_one_the_notifier_ACCEPTS(self):
        """Draft 1's bug: `notify._require_fixed` refuses any body outside the frozen set."""
        body = nd.probe_body(notify)
        self.assertIn(body, notify.BODIES)
        self.assertTrue(notify._require_fixed(body))

    def test_a_body_the_notifier_REFUSES_would_have_emptied_the_set(self):
        """The bug, reproduced — so the guard above is graded rather than asserted."""
        self.assertEqual(nd.platforms_with_visual_arm(notify, body="not a declared body"), ())

    def test_every_probed_platform_NAME_appears_in_the_shipped_rows(self):
        """§7j. A doc rename would make the vocabulary unmatchable and everything trivially true."""
        self.assertEqual(nd.unnamed_platforms(_doc()), (),
                         "a probed platform's name appears nowhere in the rows, so no claim about "
                         "it can ever match")


class TheShippedTableIsTRUE(unittest.TestCase):
    """The live assertion. It grades the DOC; the mechanism is graded by the plants below."""

    def test_the_rows_this_module_grades_EXIST(self):
        rows = nd.documented_rows(_doc())
        for key, row in rows.items():
            self.assertIsNotNone(row, "`%s` is documented nowhere — absent is not the same as "
                                      "wrong, and neither is a pass" % key)

    def test_the_visual_coverage_claim_matches_the_ARMS(self):
        self.assertTrue(nd.grade(_doc(), notify).ok, nd.grade(_doc(), notify).render())

    def test_WINDOWS_is_excluded_and_that_is_the_filed_row(self):
        """⭐ The row's subject, pinned by name: Windows must not be inside the coverage claim."""
        claimed, found = nd.covered_platforms(nd.documented_rows(_doc())[nd.VISUAL_KEY])
        self.assertTrue(found)
        self.assertNotIn("win32", claimed)
        self.assertNotIn("win32", nd.platforms_with_visual_arm(notify))

    def test_the_audio_row_does_not_promise_AUDIBILITY(self):
        row = nd.documented_rows(_doc())[nd.AUDIO_KEY].lower()
        self.assertTrue(any(q.lower() in row for q in nd.AUDIBILITY_QUALIFIERS),
                        "the audio row promises a sound without saying a successful call means it "
                        "was QUEUED, not heard — no platform's arm can report audibility")


class TheFiledDefectIsCAUGHT(unittest.TestCase):
    """§7i. The doc was corrected before this guard existed, so the plants do the grading."""

    def test_a_row_with_NO_COVERAGE_CLAIM_is_the_filed_defect_and_reds(self):
        verdict = nd.grade(_ROW_NO_CLAIM, notify)
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.no_coverage_claim)
        self.assertIn("This is the filed defect verbatim", verdict.render())

    def test_a_claim_that_INCLUDES_windows_is_an_OVERPROMISE(self):
        row = ("| `ux.notify` | bool | `true` | notify you — macOS, Linux and Windows only |\n"
               "| `ux.notify_audio` | bool | `true` | queued with the OS |\n")
        verdict = nd.grade(row, notify)
        self.assertFalse(verdict.ok)
        self.assertIn("win32", verdict.unnamed_exclusions)
        self.assertIn("OVERPROMISE", verdict.render())

    def test_a_claim_that_DROPS_a_platform_with_an_arm_is_UNDERSTATED(self):
        row = ("| `ux.notify` | bool | `true` | notify you — macOS only |\n"
               "| `ux.notify_audio` | bool | `true` | queued with the OS |\n")
        verdict = nd.grade(row, notify)
        self.assertFalse(verdict.ok)
        self.assertIn("linux", verdict.false_exclusions)
        self.assertIn("UNDERSTATED", verdict.render())

    def test_an_audio_row_promising_a_SOUND_reds(self):
        row = ("| `ux.notify` | bool | `true` | notify you — macOS and Linux only |\n"
               "| `ux.notify_audio` | bool | `true` | play a sound with the notification |\n")
        self.assertTrue(nd.grade(row, notify).audio_overpromises)

    def test_a_MISSING_row_is_its_own_answer(self):
        verdict = nd.grade("| `something.else` | bool | `true` | x |\n", notify)
        self.assertFalse(verdict.ok)
        self.assertEqual(sorted(verdict.row_missing), sorted([nd.AUDIO_KEY, nd.VISUAL_KEY]))
        self.assertIn("documented nowhere", verdict.render())

    def test_and_a_CORRECT_row_passes(self):
        """A checker that only ever reds grades nothing either."""
        row = ("| `ux.notify` | bool | `true` | notify you — the desktop notification is macOS and "
               "Linux only; Windows ships no visual arm |\n"
               "| `ux.notify_audio` | bool | `true` | a successful call means it was queued with "
               "the OS, not that anything was audible |\n")
        self.assertTrue(nd.grade(row, notify).ok, nd.grade(row, notify).render())


class TheAudibilityDimensionStaysOPEN(unittest.TestCase):
    """⛔ 09a: *"the call is graded; the sound is not"* — and no runner can change that."""

    def test_nothing_here_claims_the_sound_was_HEARD(self):
        """This file grades PROSE against ARGV. Neither is a human hearing anything, and the doc
        must go on saying so — which is what `test_the_audio_row_does_not_promise_AUDIBILITY`
        pins. This test exists to state the boundary in the file that could be mistaken for
        closing it."""
        row = nd.documented_rows(_doc())[nd.AUDIO_KEY].lower()
        self.assertTrue(any(q.lower() in row for q in nd.AUDIBILITY_QUALIFIERS))
        self.assertNotIn("verified audible", row)


if __name__ == "__main__":
    unittest.main()
