"""L1/L3/L4 — the skill/command registry: standalone, directly invocable, with a
progressive-disclosure catalog."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.skills import (
    SKILL_NAMES,
    SkillNotFound,
    get_skill,
    list_skills,
    render_skill,
    skill_names,
)


class TestRegistry(unittest.TestCase):
    def test_all_expected_skills_present(self):
        names = set(skill_names())
        self.assertEqual(
            names,
            {"brainstorm", "refine", "onboard", "spec", "test", "develop", "review",
             "debug", "optimize", "bug", "ship"},
        )
        self.assertEqual(tuple(skill_names()), SKILL_NAMES)

    def test_every_skill_is_standalone(self):
        for name in skill_names():
            self.assertTrue(get_skill(name).standalone)

    def test_each_skill_declares_its_own_gate(self):
        self.assertEqual(get_skill("test").gate.id, "red-before-green")
        self.assertEqual(get_skill("brainstorm").gate.id, "approach-approval")
        self.assertEqual(get_skill("bug").gate.id, "reproducer-required")

    def test_unknown_skill_raises(self):
        with self.assertRaises(SkillNotFound):
            get_skill("teleport")


class TestProgressiveDisclosure(unittest.TestCase):
    def test_catalog_is_cheap_names_and_summaries_only(self):
        cat = list_skills()
        names = [name for name, _summary in cat]
        self.assertIn("test", names)
        # the catalog must NOT carry the full prompt body (progressive disclosure)
        blob = "\n".join(f"{n} {s}" for n, s in cat)
        self.assertNotIn("watch them FAIL first", blob)

    def test_detail_reveals_the_full_prompt(self):
        skill = get_skill("test")
        self.assertIn("watch them FAIL first", skill.prompt)


class TestStandaloneInvocation(unittest.TestCase):
    def test_render_skill_is_self_contained(self):
        text = render_skill(get_skill("test"))
        self.assertIn("standalone", text.lower())
        self.assertIn("RED", text)
        self.assertIn("Gate", text)
        # a downstream skill does not drag in brainstorm's approval gate
        self.assertNotIn("approach-approval", text)


if __name__ == "__main__":
    unittest.main()
