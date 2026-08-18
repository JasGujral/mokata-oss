"""Stage 4 — grade the mirror graders against PLANTED offenders.

The real tree holds none: link 1 is 11 generated + 1 declared hand-authored, link 2 is 16/16 with
nothing absent. A guard written over the real tree would therefore pass whether or not it worked
(doc 85 §7i), and the two rows this stage closes are both of exactly that shape — a `continue`
that had never skipped anything, and a literal tuple that had never been widened.

So every disposition in `_skill_mirrors` is produced HERE from a corpus this file builds: a
template that drifted, a template that vanished, a mirror that was deleted, a hand-authored
template that got regenerated, a declaration with no reason. Each is restored in a `finally` where
it touches a real path; most never touch one at all.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

import _skill_mirrors as SM
from mokata.agent_skills import CURATED_SKILLS
from mokata.skills import SKILLS, SKILL_NAMES, command_markdown, get_skill

_SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"
_TEMPLATES = _SRC / "templates" / "commands"
_SKILLS_DIR = _SRC / "skills"


def _corpus(names):
    """A throwaway copy of the templates + skills trees, cut to `names`. The graders take their
    corpus as an argument precisely so this is possible without touching the repo."""
    d = Path(tempfile.mkdtemp())
    (d / "templates").mkdir()
    (d / "skills").mkdir()
    for n in names:
        src_t = _TEMPLATES / f"{n}.md"
        if src_t.is_file():
            shutil.copyfile(src_t, d / "templates" / f"{n}.md")
        src_s = _SKILLS_DIR / n / "SKILL.md"
        if src_s.is_file():
            (d / "skills" / n).mkdir()
            shutil.copyfile(src_s, d / "skills" / n / "SKILL.md")
    return d


class TestLink1Dispositions(unittest.TestCase):
    """registry -> templates/commands/<n>.md, one planted instance per verdict."""

    def test_a_generated_template_that_agrees_is_a_match(self):
        d = _corpus(["review"])
        try:
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["review"]),
                             {"review": SM.L1_GENERATED_MATCH})
        finally:
            shutil.rmtree(d)

    def test_a_generated_template_that_drifts_is_caught(self):
        """The defect link 1 exists to catch, on a skill the OLD four-name tuple did not grade."""
        d = _corpus(["optimize"])
        try:
            p = d / "templates" / "optimize.md"
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["optimize"])["optimize"],
                             SM.L1_GENERATED_MATCH)
            p.write_text(p.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["optimize"])["optimize"],
                             SM.L1_GENERATED_DRIFT)
        finally:
            shutil.rmtree(d)

    def test_a_missing_template_FAILS_and_is_never_skipped(self):
        d = _corpus(["review"])
        try:
            (d / "templates" / "review.md").unlink()
            graded = SM.grade_link1(SKILLS, d / "templates", ["review"])
            self.assertEqual(graded, {"review": SM.L1_TEMPLATE_MISSING})
            self.assertEqual(set(graded), {"review"}, "the absent name dropped out of the verdict")
            self.assertTrue(SM.offenders(graded, SM.L1_HEALTHY))
        finally:
            shutil.rmtree(d)

    def test_a_hand_authored_template_that_extends_is_healthy(self):
        d = _corpus(["brainstorm"])
        try:
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["brainstorm"]),
                             {"brainstorm": SM.L1_HAND_AUTHORED})
        finally:
            shutil.rmtree(d)

    def test_a_hand_authored_template_that_was_REGENERATED_is_an_offender(self):
        """⭐ The arm that matters most, and the one a naive widening gets backwards. Overwriting
        brainstorm.md with what the registry renders is what "fix the drift" would have done: the
        extra 85 lines are gone, and the pin must go RED on the loss rather than green on the
        agreement."""
        d = _corpus(["brainstorm"])
        try:
            p = d / "templates" / "brainstorm.md"
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["brainstorm"])["brainstorm"],
                             SM.L1_HAND_AUTHORED)
            p.write_text(command_markdown(get_skill("brainstorm")), encoding="utf-8")
            graded = SM.grade_link1(SKILLS, d / "templates", ["brainstorm"])
            self.assertEqual(graded["brainstorm"], SM.L1_HAND_AUTHORED_IDENTICAL)
            self.assertTrue(SM.offenders(graded, SM.L1_HEALTHY))
        finally:
            shutil.rmtree(d)

    def test_a_declaration_without_a_reason_is_an_offender(self):
        """A declaration nobody had to justify is an allow-list entry — the shape `SHIM-FALSE-GREEN`
        and the old four-name tuple both had."""
        d = _corpus(["optimize"])
        try:
            planted = dict(SKILLS)
            planted["optimize"] = dataclasses.replace(SKILLS["optimize"],
                                                      hand_authored_template="   ")
            graded = SM.grade_link1(planted, d / "templates", ["optimize"])
            self.assertEqual(graded, {"optimize": SM.L1_DECLARATION_UNREASONED})
            self.assertTrue(SM.offenders(graded, SM.L1_HEALTHY))
        finally:
            shutil.rmtree(d)

    def test_a_name_with_no_registry_entry_is_not_in_the_class(self):
        """The five curated standalones. `SkillNotFound` is not drift, and must not red the pin."""
        d = _corpus(["govern"])
        try:
            self.assertEqual(SM.grade_link1(SKILLS, d / "templates", ["govern"]),
                             {"govern": SM.L1_NOT_IN_REGISTRY})
            self.assertEqual(SM.offenders({"govern": SM.L1_NOT_IN_REGISTRY}, SM.L1_HEALTHY), {})
        finally:
            shutil.rmtree(d)


class TestLink2Dispositions(unittest.TestCase):
    """templates/commands/<n>.md -> skills/<n>/SKILL.md — MIRROR-ABSENCE-READS-AS-MATCH's own row."""

    def test_a_shipped_mirror_that_agrees_is_a_match(self):
        d = _corpus(["review"])
        try:
            self.assertEqual(
                SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["review"]),
                {"review": SM.L2_MATCH})
        finally:
            shutil.rmtree(d)

    def test_a_DELETED_mirror_FAILS_and_is_never_skipped(self):
        """⭐ MIRROR-ABSENCE-READS-AS-MATCH, planted. The old loop's `continue` gave this the same
        green as a match; it now has its own verdict, and that verdict is an offender."""
        d = _corpus(["brainstorm"])
        try:
            p = d / "skills" / "brainstorm" / "SKILL.md"
            self.assertEqual(
                SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["brainstorm"]),
                {"brainstorm": SM.L2_MATCH})
            p.unlink()
            graded = SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["brainstorm"])
            self.assertEqual(graded, {"brainstorm": SM.L2_MIRROR_MISSING})
            self.assertNotEqual(graded["brainstorm"], SM.L2_MATCH)
            self.assertTrue(SM.offenders(graded, SM.L2_HEALTHY),
                            "an absent mirror scored as healthy — the defect is back")
        finally:
            shutil.rmtree(d)

    def test_a_stale_mirror_is_drift_and_a_missing_one_is_not_the_same_fact(self):
        """§7g: the two must not share a representation, so the two must not share a verdict."""
        d = _corpus(["ship"])
        try:
            p = d / "skills" / "ship" / "SKILL.md"
            p.write_text(p.read_text(encoding="utf-8") + "\nstale\n", encoding="utf-8")
            stale = SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["ship"])["ship"]
            p.unlink()
            gone = SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["ship"])["ship"]
            self.assertEqual(stale, SM.L2_DRIFT)
            self.assertEqual(gone, SM.L2_MIRROR_MISSING)
            self.assertNotEqual(stale, gone)
        finally:
            shutil.rmtree(d)

    def test_an_unrenderable_template_is_its_own_verdict_not_a_missing_mirror(self):
        d = _corpus(["ship"])
        try:
            (d / "templates" / "ship.md").unlink()
            self.assertEqual(
                SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["ship"]),
                {"ship": SM.L2_TEMPLATE_MISSING})
        finally:
            shutil.rmtree(d)

    def test_an_uncurated_name_must_have_NO_mirror_and_a_planted_one_is_caught(self):
        """`version`. "No mirror expected" must not be reachable by failing to look, so a mirror
        that appears for an uncurated name is a finding, not a silent pass."""
        d = _corpus(["version"])
        try:
            self.assertEqual(
                SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["version"]),
                {"version": SM.L2_NOT_CURATED})
            (d / "skills" / "version").mkdir()
            (d / "skills" / "version" / "SKILL.md").write_text("planted\n", encoding="utf-8")
            self.assertEqual(
                SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", ["version"])["version"],
                SM.L2_DRIFT)
        finally:
            shutil.rmtree(d)


class TestTheDomainsAreDerivedNotTyped(unittest.TestCase):
    """doc 85 §7j — the corpus is an axis too. A grader that ranges over a shrunken set must be
    visibly a different claim from one that ranges over the registry."""

    def test_a_shrunken_domain_grades_fewer_names(self):
        d = _corpus(SKILL_NAMES)
        try:
            full = SM.grade_link1(SKILLS, d / "templates", SKILL_NAMES)
            narrow = SM.grade_link1(SKILLS, d / "templates", ("review", "develop", "test", "ship"))
            self.assertEqual(len(full), 12)
            self.assertEqual(len(narrow), 4)
            self.assertEqual(set(narrow) - set(full), set())
        finally:
            shutil.rmtree(d)

    def test_the_old_four_name_tuple_is_blind_to_a_drift_the_derivation_catches(self):
        """The regression this stage exists for, made concrete: `spec` drifts, the four-name pin
        sees nothing, the derived pin reds."""
        d = _corpus(SKILL_NAMES)
        try:
            p = d / "templates" / "spec.md"
            p.write_text(p.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
            narrow = SM.grade_link1(SKILLS, d / "templates", ("review", "develop", "test", "ship"))
            derived = SM.grade_link1(SKILLS, d / "templates", SKILL_NAMES)
            self.assertEqual(SM.offenders(narrow, SM.L1_HEALTHY), {},
                             "the four-name pin is expected to be blind here — that is the row")
            self.assertEqual(SM.offenders(derived, SM.L1_HEALTHY),
                             {"spec": SM.L1_GENERATED_DRIFT})
        finally:
            shutil.rmtree(d)

    def test_link2s_domain_is_curated_and_a_shrunken_one_misses_a_deleted_mirror(self):
        d = _corpus(CURATED_SKILLS)
        try:
            (d / "skills" / "docsync" / "SKILL.md").unlink()
            narrow = SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills",
                                    ("review", "develop", "test", "ship"))
            derived = SM.grade_link2(CURATED_SKILLS, d / "templates", d / "skills", CURATED_SKILLS)
            self.assertEqual(SM.offenders(narrow, SM.L2_HEALTHY), {})
            self.assertEqual(SM.offenders(derived, SM.L2_HEALTHY),
                             {"docsync": SM.L2_MIRROR_MISSING})
            self.assertEqual(len(derived), 16)
        finally:
            shutil.rmtree(d)

    def test_healthy_sets_do_not_contain_an_offender_disposition(self):
        """The split is data, so assert it is the split — a mutant that moves DRIFT or MISSING into
        the healthy set has something to move it in."""
        for bad in (SM.L1_GENERATED_DRIFT, SM.L1_HAND_AUTHORED_IDENTICAL,
                    SM.L1_TEMPLATE_MISSING, SM.L1_DECLARATION_UNREASONED):
            self.assertNotIn(bad, SM.L1_HEALTHY)
        for bad in (SM.L2_DRIFT, SM.L2_MIRROR_MISSING, SM.L2_TEMPLATE_MISSING):
            self.assertNotIn(bad, SM.L2_HEALTHY)
        self.assertEqual(len(SM.L1_HEALTHY), 3)
        self.assertEqual(len(SM.L2_HEALTHY), 2)


if __name__ == "__main__":
    unittest.main()
