"""Stage 22 — RUN-REG-ENFORCED-BY-PROSE-ALONE (OSS #28): the step-1 registration instruction
cannot silently vanish from the shipped brainstorm surfaces.

THE DEFECT, IN ONE SENTENCE. `brainstorm`'s opening step is prose — "before you ask the first
question, call `session_save` with `register` = true" — and `gate_hook.py`'s phase gate binds on
the checkpoint that call writes. If the instruction is not followed there is no checkpoint, the
gate answers `"no active mokata run — not policed"` (gate_hook.py:483) and the write proceeds. That
is the RIGHT answer to the question the gate was asked, which is why a vanished instruction is
today indistinguishable from a followed one, and why nothing reds when the sentence goes.

SCOPE — THE CHEAP HALF ONLY. This pins the instruction. It does NOT make registration structural,
and gate_hook.py is untouched: firing registration from a non-prose surface collides with the
gate's positive-trigger contract (a run registered at SessionStart would police ordinary
hand-editing — the house arrest the `not policed` floor exists to prevent). That was triaged and
ruled out before this stage; the ruling is not re-litigated here.

TWO SURFACES, ONE SOURCE. The prose lives in `templates/commands/brainstorm.md` (the
`/mokata:brainstorm` command) and is regenerated into `skills/brainstorm/SKILL.md` (the agent
skill) — `test_handoff_g1.test_shipped_skill_md_mirrors_are_regenerated` pins the two EQUAL, but
equality says nothing about CONTENT: two identically-degraded copies pass it. Both are graded here,
independently, because both are read by an agent and either could be edited alone if the
generation chain is ever changed.

§7g — REQUIREMENTS, NOT A STRING. `_skill_prose` grades four requirements separately and names the
one that failed. A pin keyed on the exact sentence would red on a legitimate copy-edit, and a pin
that reds on copy-edits gets weakened rather than obeyed.

§7i — THE OFFENDERS ARE WHAT GRADE THIS. The shipped prose is correct today, so every real-tree
assertion below is green from the moment it is written and measures nothing on its own.
`TestSyntheticOffenders` DERIVES offenders from the real text — the section deleted, `register` =
true downgraded to a mention, the tool name dropped, the section moved below the first question,
the ordering anchor removed — and asserts each reds for ITS OWN requirement and no other.
"""

import os
import re
import unittest

import _support  # noqa: F401  (path-fix side-effect)

import _skill_prose
from _skill_prose import (REQ_ORDER, REQ_REGISTER_TRUE, REQ_SECTION, REQ_TOOL,
                          check_registration_step, find_first_question_anchor,
                          find_registration_section, parse_sections)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src", "mokata")

# Every SHIPPED surface that carries the instruction. Both live under `src/mokata/` and both go
# into the wheel via `[tool.setuptools.package-data]` (templates/commands/*.md, skills/<n>/SKILL.md).
SURFACES = {
    "skills/brainstorm/SKILL.md": os.path.join(_SRC, "skills", "brainstorm", "SKILL.md"),
    "templates/commands/brainstorm.md": os.path.join(_SRC, "templates", "commands",
                                                     "brainstorm.md"),
}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestTheShippedSurfacesCarryTheStep(unittest.TestCase):
    """The real tree. GREEN by construction — see the offenders below for what grades it."""

    def test_every_shipped_brainstorm_surface_meets_every_requirement(self):
        for name, path in sorted(SURFACES.items()):
            with self.subTest(surface=name):
                self.assertTrue(os.path.isfile(path), f"{name} is missing from src/")
                report = check_registration_step(_read(path))
                self.assertTrue(report.ok,
                                f"{name} no longer carries the run-registration step as "
                                f"specified:\n{report.describe()}")

    def test_the_step_is_found_by_its_own_heading_not_by_a_body_fallback(self):
        """The `basis` is part of the claim. A step still detectable only by a body scan has lost
        its heading, which is a real weakening even though the four requirements still pass — so
        the basis is asserted rather than left as an implementation detail."""
        for name, path in sorted(SURFACES.items()):
            with self.subTest(surface=name):
                _section, basis = find_registration_section(parse_sections(_read(path)))
                self.assertEqual(basis, "heading",
                                 f"{name}: the registration step no longer has a heading of its "
                                 f"own announcing it (found via: {basis})")

    def test_both_surfaces_are_graded_and_neither_is_silently_absent(self):
        """The sweep's own denominator. A future refactor that moves or renames a surface must
        change THIS list deliberately, not quietly shrink what the pin covers."""
        self.assertEqual(sorted(SURFACES), ["skills/brainstorm/SKILL.md",
                                            "templates/commands/brainstorm.md"])


class TestSyntheticOffenders(unittest.TestCase):
    """§7i. Each offender is DERIVED from the real shipped text, degraded one way, and must red for
    exactly one requirement — so the pin can say WHAT vanished, not merely that something did."""

    def setUp(self):
        self.text = _read(SURFACES["skills/brainstorm/SKILL.md"])
        self.sections = parse_sections(self.text)
        self.section, basis = find_registration_section(self.sections)
        self.assertIsNotNone(self.section, "the real file has no registration step to degrade")
        self.assertEqual(basis, "heading")

    def _slice(self):
        return self.text[self.section.start:self.section.end]

    def assertRedsFor(self, offender, requirement, *, ungradable=()):
        """The offender reds, for EXACTLY the named requirement, and for nothing else."""
        report = check_registration_step(offender)
        self.assertFalse(report.ok, "the offender was accepted — this pin grades nothing")
        self.assertEqual(report.missing_ids, (requirement,),
                         f"expected exactly {requirement} to be missing, got "
                         f"{report.missing_ids} / ungradable {report.ungradable_ids}")
        self.assertEqual(report.ungradable_ids, tuple(ungradable))
        reason = dict(report.missing)[requirement]
        self.assertTrue(reason.strip(), f"{requirement} failed with no reason attached")

    # ---- offender 1: the section is deleted outright -----------------------------------------

    def test_a_deleted_registration_section_reds(self):
        offender = self.text[:self.section.start] + self.text[self.section.end:]
        self.assertNotIn("register the run", offender.lower())
        self.assertRedsFor(offender, REQ_SECTION,
                           ungradable=(REQ_TOOL, REQ_REGISTER_TRUE, REQ_ORDER))

    def test_a_deleted_section_is_reported_as_ABSENT_not_as_a_reworded_one(self):
        """§7g's whole point: absent and reworded must not share a representation."""
        offender = self.text[:self.section.start] + self.text[self.section.end:]
        report = check_registration_step(offender)
        self.assertEqual(report.basis, "absent")
        self.assertIn("no section registers the run", dict(report.missing)[REQ_SECTION])

    # ---- offender 2: `register` = true downgraded to a mention --------------------------------

    def test_register_true_downgraded_to_a_mention_reds(self):
        """The realistic weakening: the parameter is still NAMED, so a string search for
        `register` still finds it, but nothing instructs that it be SET."""
        phrase = "`register` = true"
        self.assertIn(phrase, self._slice(),
                      "the offender is built by rewriting this exact phrase; if it was "
                      "copy-edited, re-derive the offender rather than deleting this assertion")
        degraded = self._slice().replace(phrase, "the `register` parameter")
        offender = self.text[:self.section.start] + degraded + self.text[self.section.end:]
        self.assertIn("register", offender)
        self.assertRedsFor(offender, REQ_REGISTER_TRUE)

    def test_register_set_false_reds_just_as_hard_as_an_absent_argument(self):
        offender = self.text.replace("`register` = true", "`register` = false")
        self.assertRedsFor(offender, REQ_REGISTER_TRUE)

    # ---- offender 3: the tool name is dropped -------------------------------------------------

    def test_a_registration_step_that_stops_naming_the_tool_reds(self):
        degraded = self._slice().replace(_skill_prose.TOOL_NAME, "the session tool")
        self.assertNotIn(_skill_prose.TOOL_NAME, degraded)
        offender = self.text[:self.section.start] + degraded + self.text[self.section.end:]
        self.assertRedsFor(offender, REQ_TOOL)

    # ---- offender 4: the section moves below the first question -------------------------------

    def test_a_registration_step_moved_below_the_first_question_reds(self):
        anchor = find_first_question_anchor(self.sections, exclude=self.section)
        self.assertIsNotNone(anchor, "the real file has no question-asking instruction to move past")
        self.assertLess(self.section.start, anchor.start, "the real file is already out of order")
        cut = self._slice()
        rest = self.text[:self.section.start] + self.text[self.section.end:]
        # Re-insert after the anchor section, in the same document, unchanged byte for byte.
        moved_anchor = find_first_question_anchor(parse_sections(rest))
        self.assertIsNotNone(moved_anchor)
        offender = rest[:moved_anchor.end] + cut + rest[moved_anchor.end:]
        self.assertEqual(sorted(offender), sorted(self.text),
                         "the offender must be a REORDERING — nothing added, nothing removed")
        self.assertRedsFor(offender, REQ_ORDER)

    def test_the_order_failure_names_both_positions(self):
        """A red that cannot say what moved past what is the defect wearing a test."""
        anchor = find_first_question_anchor(self.sections, exclude=self.section)
        cut = self._slice()
        rest = self.text[:self.section.start] + self.text[self.section.end:]
        moved_anchor = find_first_question_anchor(parse_sections(rest))
        offender = rest[:moved_anchor.end] + cut + rest[moved_anchor.end:]
        reason = dict(check_registration_step(offender).missing)[REQ_ORDER]
        self.assertIn("AFTER", reason)
        self.assertIn(anchor.heading, reason)
        self.assertIn(self.section.heading, reason)

    # ---- offender 5: the ordering ANCHOR is removed — UNGRADABLE, never default-green ---------

    def test_removing_the_question_instruction_renders_the_order_UNGRADABLE(self):
        """"No anchor found" must not silently mean "the order is fine". The step is still
        present, still names the tool, still requires `register` = true — and the ordering
        requirement reports that it could not be answered."""
        offender = self.text
        for section in reversed(parse_sections(self.text)):
            if section.start == self.section.start:
                continue
            if _skill_prose._ASK_QUESTION.search(_skill_prose._normalise(section.body)):
                offender = offender[:section.start] + offender[section.end:]
        self.assertIsNone(find_first_question_anchor(parse_sections(offender),
                                                     exclude=self.section))
        report = check_registration_step(offender)
        self.assertEqual(report.missing_ids, ())
        self.assertEqual(report.ungradable_ids, (REQ_ORDER,))
        self.assertFalse(report.ok, "an UNGRADABLE requirement is not a pass")

    # ---- the tolerance the requirements exist to buy ------------------------------------------

    def test_a_legitimate_copy_edit_of_the_step_stays_GREEN(self):
        """The other half of §7g. If a faithful rewording reds, the pin teaches people to weaken
        it — so a re-headed, re-worded, differently-spelled-argument version must pass."""
        rewritten = (
            "## Step 0 — registering this run (so the brainstorm is tracked)\n"
            "\n"
            "Before your first question to the user, register the run: call `session_save`,\n"
            "setting `register` to True. Without it nothing downstream can see this brainstorm.\n"
            "\n")
        offender = self.text[:self.section.start] + rewritten + self.text[self.section.end:]
        report = check_registration_step(offender)
        self.assertTrue(report.ok, f"a faithful copy-edit reds:\n{report.describe()}")
        self.assertEqual(report.basis, "heading",
                         "the rewrite kept a heading that says it REGISTERS the run, so it must "
                         "still be found BY that heading — falling back to a body scan means the "
                         "stem match has tightened to one spelling of the word")


class TestTheOtherSurfaceIsGradedTheSameWay(unittest.TestCase):
    """The command template is the SOURCE the skill is generated from; degrading it degrades both.
    One offender against it, so this file's coverage of that surface is not equality-by-proxy."""

    def test_deleting_the_step_from_the_command_template_reds(self):
        text = _read(SURFACES["templates/commands/brainstorm.md"])
        section, _basis = find_registration_section(parse_sections(text))
        offender = text[:section.start] + text[section.end:]
        report = check_registration_step(offender)
        self.assertEqual(report.missing_ids, (REQ_SECTION,))


class TestTheParserItself(unittest.TestCase):
    """The checker is only as honest as its section boundaries."""

    def test_headings_inside_a_fenced_block_are_not_sections(self):
        text = "# top\n\nbody\n\n```\n## not a heading\n```\n\n## real\n\nmore\n"
        headings = [s.heading for s in parse_sections(text)]
        self.assertEqual(headings, ["", "top", "real"])

    def test_content_before_the_first_heading_is_kept_as_a_section(self):
        sections = parse_sections("preamble text\n\n## one\n\nbody\n")
        self.assertEqual(sections[0].heading, "")
        self.assertIn("preamble text", sections[0].body)

    def test_section_bounds_partition_the_document_exactly(self):
        text = _read(SURFACES["skills/brainstorm/SKILL.md"])
        sections = parse_sections(text)
        self.assertEqual("".join(s.body for s in sections), text,
                         "the sections do not reassemble the document — an offender built by "
                         "slicing on these bounds would be silently corrupt")

    def test_a_red_flags_table_row_is_not_mistaken_for_a_question_instruction(self):
        """The anchor regex must not bind to prose ABOUT asking questions, only to the
        instruction. The brainstorm red-flags table is the live near-miss."""
        row = ('| "I\'ll ask everything up front to save time." | One question at a time. |\n')
        self.assertIsNone(_skill_prose._ASK_QUESTION.search(_skill_prose._normalise(row)))
        self.assertIsNotNone(_skill_prose._ASK_QUESTION.search(
            _skill_prose._normalise("Ask exactly one question at a time, and wait.")))


if __name__ == "__main__":
    unittest.main()
