"""`INDEX.md`'s next-free doc number is DERIVED, and the collisions it already caused are MEASURED.

0.0.20, `INDEX-NEXT-FREE-IS-MANUAL`. `docs/build/INDEX.md` rule 4 says *"Never renumber. Next free
number: **N**"*. N is typed. Its own footnote records **four** occasions when it was behind the
tree, and on 2026-08-27 it read `106` while `107` existed — **the fifth**, with the footnote's own
prescription (*"the fix is to derive it, not to remind harder"*) written down and unbuilt for four
lapses running.

⭐ THIS FILE IS NOT A REMINDER. It is the derivation the footnote asked for, plus the thing the
footnote never had: a MEASUREMENT of what the stale field costs.

WHY THE COLLISION CENSUS REPORTS INSTEAD OF ASSERTING
------------------------------------------------------
Rule 4 exists to stop two documents claiming one number. That has already happened — the number
space is measured, not assumed, and the count is allowed to move as the tree grows. ⛔ A pinned
count would red on every legitimate archive, and a guard that reds for the wrong reason gets
silenced. So `TheCollisionsAreMEASURED` derives them, separates the sanctioned brief/audit pairs
from the rest by a mechanism rather than by a list, and pins only the property that must hold:
**the count does not GROW.**

§7i — A GUARD WHOSE OFFENDER YOU JUST FIXED GRADES NOTHING
-----------------------------------------------------------
`INDEX.md` is corrected in the same change that adds this file, so on a healthy tree `grade()`
returns `CURRENT` and every assertion about staleness would be vacuous. **`TheStalenessIsCAUGHT`
plants the defect** — a synthetic tree whose declared field is behind its filenames — and requires
`STALE`. That control is what grades the mechanism; the live assertion only grades the repo.

§7g — THREE FIELD STATES, AND THE ONE THAT MUST NEVER READ AS A PASS
---------------------------------------------------------------------
"the field is missing" (`FIELD_ABSENT`), "the field says `six`" (`UNPARSEABLE`) and "the field says
106" (`DECLARED`) are three facts. A reader collapsing the first two to `None` makes an index with
no rule-4 field at all look identical to one that is correct, which is exactly the shape that let
five lapses through: nothing separated *checked and fine* from *nobody checked*.

§7c — THE MIRROR HAS NO `docs/build/`
--------------------------------------
The subject is internal-only. On the public mirror it is legitimately absent, and on a dev checkout
that has LOST it, it is not. `_internal_subject.subject_state` gives those two different colours, so
this file never reports `OK` for a run that graded nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _docs_index as dix
import _internal_subject as isub


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "docs", "build")
INDEX = os.path.join(ROOT, "docs", "build", "INDEX.md")

#: The subject, repo-relative POSIX, for the mirror-aware guard.
SUBJECTS = ("docs/build/INDEX.md",)

#: The collisions standing when this guard was built, 2026-08-27, DERIVED not typed:
#: 8 numbers shared, of which 2 are sanctioned brief/audit pairs. The pin below is an
#: upper bound on the UNPAIRED ones — it may fall, it may not rise.
UNPAIRED_CEILING = 6


# ⛔ THE TWO CLASSES BELOW THAT TOUCH `docs/build` CARRY A CLASS DECORATOR, NOT A `setUp` SKIP,
# and it is spelled out INLINE on each rather than aliased to a name here — `_shipped_reads.guard_of`
# reads the decorator as an AST Call, so `@_MIRROR` would be invisible to it and the file would grade
# as unguarded while LOOKING guarded. A skip raised inside `setUp`/`setUpClass` collapses the class
# into ONE skip and drops its tests out of unittest's `Ran N`, so the mirror's count diverges from
# this repo's — and a read in `setUpClass` beats its own skip to the file anyway.


def _state():
    return isub.subject_state(ROOT, SUBJECTS)


def _index_text():
    with open(INDEX, "r", encoding="utf-8") as fh:
        return fh.read()


class TheSubjectIsReachableOrTheTreeSaysWhy(unittest.TestCase):
    """§7c. The one test that must run in EVERY tree, because it is the one that names the tree."""

    def test_the_state_is_never_UNDECIDABLE(self):
        state = _state()
        self.assertIn(state, (isub.GRADED, isub.ABSENT_BY_DESIGN),
                      "docs/build/INDEX.md is absent from a tree that is not a coherent mirror. "
                      "This is a dev checkout that LOST the file, not a public mirror, and the "
                      "difference is the whole point of the three-state model.")

    def test_a_DEV_tree_actually_grades(self):
        """⛔ The anti-vacuity half: on a dev tree ABSENT_BY_DESIGN would be a lie."""
        if isub.tree_state(ROOT).kind != isub.TREE_DEV:
            self.skipTest("not a dev checkout")
        self.assertEqual(_state(), isub.GRADED)


@unittest.skipUnless(os.path.exists(INDEX),
                     "docs/build/INDEX.md is dev-only, excluded from the public mirror")
class TheDeclaredNumberIsCURRENT(unittest.TestCase):
    """The live assertion. ⚠ It grades the REPO; the mechanism is graded by the planted controls."""

    def test_the_index_declares_a_next_free_number_AT_ALL(self):
        field = dix.declared_next_free(_index_text())
        self.assertEqual(field.state, dix.DECLARED,
                         "INDEX.md rule 4's next-free field is %s (raw %r). An index that does not "
                         "declare one is a different fact from an index that declares a stale one, "
                         "and neither is a pass." % (field.state, field.raw))

    def test_the_declared_number_is_NOT_ALREADY_CLAIMED(self):
        verdict = dix.grade(BUILD, _index_text())
        self.assertEqual(verdict.state, dix.CURRENT, verdict.render())

    def test_the_derivation_spans_the_ARCHIVE_and_not_only_the_live_tree(self):
        """⛔ Rule 4's "never renumber" means an archived doc still OWNS its number."""
        live = frozenset(
            n for n, rel in dix.numbered_docs(BUILD) if not rel.startswith("archive/"))
        every = dix.claimed_numbers(BUILD)
        self.assertTrue(every - live,
                        "the archive contributed no numbers, so this test is not grading the "
                        "property it names")
        self.assertGreater(max(every), 0)


class TheStalenessIsCAUGHT(unittest.TestCase):
    """§7i. The offender was fixed in this same change, so the mechanism is graded on a PLANT."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mok-docidx-")
        self.build = os.path.join(self.tmp, "build")
        os.makedirs(os.path.join(self.build, "archive"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _doc(self, name, sub=""):
        d = os.path.join(self.build, sub) if sub else self.build
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write("# planted\n")

    def test_a_field_BEHIND_the_tree_is_STALE(self):
        self._doc("40-alpha.md")
        self._doc("41-beta.md")
        verdict = dix.grade(self.build, "4. Never renumber. Next free number: **41** — note.")
        self.assertEqual(verdict.state, dix.STALE, verdict.render())
        self.assertEqual(verdict.derived, 42)
        self.assertIn("COLLIDES", verdict.render())

    def test_the_EXACT_lapse_of_2026_08_27_reds(self):
        """The fifth lapse, reproduced: the field read 106 while 107 existed."""
        self._doc("105-plan.md")
        self._doc("107-verification.md")
        verdict = dix.grade(self.build, "Next free number: **106**")
        self.assertEqual(verdict.state, dix.STALE)
        self.assertEqual(verdict.derived, 108)

    def test_an_ARCHIVED_number_still_makes_a_field_STALE(self):
        """⛔ The defect one directory deeper: deriving over the live tree hands out a taken number."""
        self._doc("30-live.md")
        self._doc("77-retired.md", sub="archive")
        verdict = dix.grade(self.build, "Next free number: **31**")
        self.assertEqual(verdict.state, dix.STALE,
                         "the archive was not consulted, so the next author would be handed 31 "
                         "while 77 is already owned")
        self.assertEqual(verdict.derived, 78)

    def test_a_CURRENT_field_passes(self):
        """The other half of the control — a mechanism that only ever reds grades nothing either."""
        self._doc("40-alpha.md")
        verdict = dix.grade(self.build, "Next free number: **41**")
        self.assertEqual(verdict.state, dix.CURRENT, verdict.render())

    def test_a_number_INSIDE_A_TITLE_cannot_claim_a_slot(self):
        self._doc("40-mokata-0.0.99-plan.md")
        self.assertEqual(dix.derive_next_free(self.build), 41)

    def test_an_UNNUMBERED_file_cannot_claim_a_slot_from_a_number_MID_NAME(self):
        """⛔ `INDEX.md` and `mokata-feature-list-v2.md` sit in this directory unnumbered.

        A reader that SEARCHES rather than anchors would let `notes-on-99-things.md` claim 99 and
        push the next-free number past a hundred slots nobody owns — the stale field in the other
        direction, which rule 4 is equally unable to survive.
        """
        self._doc("40-alpha.md")
        self._doc("notes-on-99-things.md")
        self.assertEqual(dix.derive_next_free(self.build), 41)


class TheUnreadableFieldIsNeverAPass(unittest.TestCase):
    """§7g + §7f. Three states, and UNGRADABLE is not green."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mok-docidx-f-")
        os.makedirs(self.tmp, exist_ok=True)
        with open(os.path.join(self.tmp, "10-a.md"), "w", encoding="utf-8") as fh:
            fh.write("x")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_MISSING_field_is_FIELD_ABSENT_and_UNGRADABLE(self):
        field = dix.declared_next_free("4. Never renumber. Ask someone.")
        self.assertEqual(field.state, dix.FIELD_ABSENT)
        self.assertIsNone(field.value)
        self.assertEqual(dix.grade(self.tmp, "no field here").state, dix.UNGRADABLE)

    def test_a_NON_NUMERIC_field_is_UNPARSEABLE_and_keeps_its_RAW_text(self):
        field = dix.declared_next_free("Next free number: **one hundred and six**")
        self.assertEqual(field.state, dix.UNPARSEABLE)
        self.assertIsNone(field.value)
        self.assertEqual(field.raw, "one hundred and six")

    def test_ABSENT_and_UNPARSEABLE_do_NOT_share_a_representation(self):
        absent = dix.declared_next_free("nothing")
        garbled = dix.declared_next_free("Next free number: ****")
        self.assertNotEqual(absent.state, garbled.state,
                            "an index with no rule-4 field and an index with an unreadable one are "
                            "different facts about the repo (§7g)")

    def test_UNGRADABLE_renders_as_a_REFUSAL_and_not_as_a_result(self):
        text = dix.grade(self.tmp, "no field").render()
        self.assertIn("UNGRADABLE", text)
        self.assertIn("NOT a pass", text)

    def test_the_FIRST_match_wins_so_COMMENTARY_is_not_graded(self):
        """The index quotes older values in its own drift note. Grading the note would be wrong."""
        text = ("4. Never renumber. Next free number: **50** — *(prior note:)* it once read "
                "Next free number: **89** while 89-96 existed.")
        self.assertEqual(dix.declared_next_free(text).value, 50)


@unittest.skipUnless(os.path.exists(INDEX),
                     "docs/build/INDEX.md is dev-only, excluded from the public mirror")
class TheCollisionsAreMEASURED(unittest.TestCase):
    """What the stale field ACTUALLY COST — derived from the tree, with the pin as a ceiling."""

    def test_the_sanctioned_brief_audit_pairs_are_RECOGNISED(self):
        paired = [c for c in dix.collisions(BUILD) if c.paired]
        self.assertTrue(paired,
                        "no brief/audit pair was recognised, so the pairing mechanism is not being "
                        "exercised and every real collision could be hiding behind it")
        for c in paired:
            self.assertEqual(len(c.paths), 2)

    def test_the_unpaired_collisions_do_NOT_GROW(self):
        found = dix.unpaired(BUILD)
        self.assertLessEqual(
            len(found), UNPAIRED_CEILING,
            "the number space gained a collision — rule 4's whole purpose:\n"
            + "\n".join(c.render() for c in found))

    def test_a_THREE_WAY_share_is_never_called_a_pair(self):
        self.assertFalse(dix._is_paired(("6-a.md", "6-audit-brief-b.md", "6-c.md")))

    def test_a_LONE_brief_is_not_a_pair(self):
        self.assertFalse(dix._is_paired(("6-audit-brief-a.md", "6-audit-brief-b.md")))

    def test_the_pairing_keys_on_the_PREFIX_and_not_on_the_word(self):
        """A doc merely mentioning an audit brief in its title must not earn the excuse."""
        self.assertFalse(
            dix._is_paired(("6-notes-on-the-audit-brief-process.md", "6-something-else.md")))


if __name__ == "__main__":
    unittest.main()
