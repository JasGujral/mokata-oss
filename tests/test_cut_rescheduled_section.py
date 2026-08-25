"""0.0.19's cut criterion: the notes carry a `Re-scheduled` section, complete and correct.

The criterion has TWO halves and they fail in opposite directions:

    * an item that SLIPPED is missing from the section  — the release renumbered a published
      promise in silence, which is `DISCLOSURE-PRESENCE-IS-NOT-DISCLOSURE-TRUTH` in the notes;
    * an item that HELD is listed in it                 — the release disclosed a slip that did
      not happen, and the PostgreSQL floor is the live example: its date is PostgreSQL 14's
      upstream end-of-life and a slot is ours to move where an external clock is not.

⛔ THE RULE IS A PURE FUNCTION OVER SUPPLIED TEXT (`tests/_rescheduled.py`), because a gate that
both discovers its corpus and judges it cannot be graded — the tree it walks has no offender in it.
`TestTheRuleCanActuallyFire` feeds it planted sections; `TestTheLiveNotes` applies it to the two
files being cut.
"""

import os
import unittest

import _support  # noqa: F401  (path fix: puts src/ on sys.path)

from _rescheduled import rescheduled_faults, section_items, section_lines

from mokata import __version__
from mokata.disclosure import PUBLISHED_COMMITMENTS, PublishedCommitment

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOTES = ("CHANGELOG.md", "RELEASE_NOTES.md")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _commitment(subject, promised_for, now_lands, status="DISCLOSED"):
    return PublishedCommitment(fragment="f", promised_for=promised_for, now_lands=now_lands,
                               status=status, on="2026-08-23", venue="v", subject=subject)


_SECTION = """## Re-scheduled

- **The FTS/BM25 ranking repair.** Promised for 0.0.19.
  It is re-scheduled to 0.0.20.
- **The sub-10-minute PR gate.** Promised for 0.0.18, re-homed to 0.0.19, re-scheduled to 0.0.20.

⛔ The PostgreSQL floor is deliberately not in this section — it shipped.

## Known limitations
"""


class TestTheRuleCanActuallyFire(unittest.TestCase):
    """Planted corpora, so the rule is graded rather than trusted."""

    def setUp(self):
        self.published = (_commitment("FTS", "0.0.19", "0.0.20"),
                          _commitment("PR gate", "0.0.19", "0.0.20"),
                          _commitment("PostgreSQL", "0.0.19", "0.0.19", status="HELD"))

    def test_the_complete_section_is_clean(self):
        # The control: without it, every assertion below would also hold of a rule that convicts
        # everything.
        self.assertEqual(rescheduled_faults(_SECTION, self.published, "0.0.19"), [])

    def test_an_item_that_slipped_and_is_not_listed_is_a_fault(self):
        without_fts = "\n".join(ln for ln in _SECTION.splitlines()
                                if "FTS" not in ln and "re-scheduled to 0.0.20." not in ln)
        faults = rescheduled_faults(without_fts, self.published, "0.0.19")
        self.assertTrue(any("FTS" in f for f in faults), faults)

    def test_the_held_item_appearing_in_the_list_is_a_fault(self):
        listed = _SECTION.replace(
            "- **The sub-10-minute PR gate.**",
            "- **The PostgreSQL floor** is re-scheduled to 0.0.20.\n- **The sub-10-minute PR gate.**")
        faults = rescheduled_faults(listed, self.published, "0.0.19")
        self.assertTrue(any("PostgreSQL" in f and "did NOT move" in f for f in faults), faults)

    def test_prose_naming_the_held_item_is_NOT_a_fault(self):
        """The sentence that states the criterion is being met must not be what breaks it."""
        self.assertIn("PostgreSQL floor is deliberately not in this section", _SECTION)
        self.assertEqual(rescheduled_faults(_SECTION, self.published, "0.0.19"), [])

    def test_a_missing_section_and_an_empty_one_are_different_faults(self):
        missing = rescheduled_faults("## Known limitations\n\n- nothing\n", self.published, "0.0.19")
        empty = rescheduled_faults("## Re-scheduled\n\nnothing yet.\n\n## Known limitations\n",
                                   self.published, "0.0.19")
        self.assertTrue(any("no `Re-scheduled` section at all" in f for f in missing), missing)
        self.assertTrue(any("lists no items" in f for f in empty), empty)
        self.assertNotEqual(missing, empty)

    def test_a_commitment_published_for_a_LATER_release_is_not_accounted_here(self):
        """The promises this cut MAKES name 0.0.20 and have not moved. Reading them as `held`
        commitments of 0.0.19 would convict the section for listing the items it re-schedules."""
        future = self.published + (_commitment("FTS", "0.0.20", "0.0.20", status="HELD"),
                                   _commitment("PR gate", "0.0.20", "0.0.20", status="HELD"))
        self.assertEqual(rescheduled_faults(_SECTION, future, "0.0.19"), [])

    def test_a_slip_with_no_subject_reds_rather_than_passing_silently(self):
        blind = (_commitment("", "0.0.19", "0.0.20"),)
        faults = rescheduled_faults(_SECTION, blind, "0.0.19")
        self.assertTrue(any("carries no subject" in f for f in faults), faults)


class TestTheLiveNotes(unittest.TestCase):
    """The two files this cut publishes."""

    def test_the_published_population_is_not_empty(self):
        """⚠ Assert the corpus before asserting over it: a table with no entry for the release
        being cut grades nothing and looks exactly like a table with nothing wrong in it."""
        published = [c for c in PUBLISHED_COMMITMENTS if c.promised_for == __version__]
        self.assertTrue(published,
                        "no commitment is declared for %s, so every assertion below would pass "
                        "having graded nothing" % __version__)
        self.assertTrue(any(c.moved for c in published), "no slip to look for")
        self.assertTrue(any(not c.moved for c in published), "no held item to look for")

    def test_every_entry_carries_a_subject(self):
        for entry in PUBLISHED_COMMITMENTS:
            self.assertTrue(entry.subject.strip(),
                            "%r carries no subject, so its omission from a `Re-scheduled` section "
                            "could never be detected" % entry.fragment)

    def test_both_files_carry_a_complete_rescheduled_section(self):
        for rel in NOTES:
            with self.subTest(rel):
                self.assertEqual(rescheduled_faults(read(rel), PUBLISHED_COMMITMENTS, __version__),
                                 [], rel)

    def test_the_section_lists_one_item_per_slip(self):
        moved = {c.subject for c in PUBLISHED_COMMITMENTS
                 if c.promised_for == __version__ and c.moved}
        for rel in NOTES:
            with self.subTest(rel):
                items = section_items(section_lines(read(rel), "Re-scheduled"))
                self.assertEqual(len(items), len(moved),
                                 "%s: %d item(s) for %d slip(s): %s"
                                 % (rel, len(items), len(moved), items))


if __name__ == "__main__":
    unittest.main()
