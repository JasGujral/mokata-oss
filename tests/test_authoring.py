"""G6 — self-authoring skills via RED-GREEN-REFACTOR-for-docs: a failing doc/spec test
first, then the skill content, then refine into a registry Skill."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import AuthoringError, SkillDraft
from mokata.skills import Gate


class TestSelfAuthoring(unittest.TestCase):
    def test_red_before_content_exists(self):
        draft = (SkillDraft("clarify")
                 .require("gate section", "## Gate")
                 .require("trigger", "Use when"))
        result = draft.check()
        self.assertFalse(result.passed)        # RED — no content yet
        self.assertEqual(draft.status, "red")

    def test_partial_content_is_still_red(self):
        draft = (SkillDraft("clarify")
                 .require("gate section", "## Gate")
                 .require("trigger", "Use when"))
        draft.write("## Gate only, no trigger")
        result = draft.check()
        self.assertFalse(result.passed)
        self.assertIn("trigger", result.failures)

    def test_green_when_all_doc_tests_pass(self):
        draft = (SkillDraft("clarify")
                 .require("gate section", "## Gate")
                 .require("trigger", "Use when"))
        draft.write("## Gate\nUse when the spec is ambiguous.")
        self.assertTrue(draft.check().passed)
        self.assertEqual(draft.status, "green")

    def test_to_skill_requires_green(self):
        draft = SkillDraft("clarify").require("gate", "## Gate")
        with self.assertRaises(AuthoringError):
            draft.to_skill("clarify reqs", Gate("clarify-gate", "ask before assuming"))
        draft.write("## Gate\nask before assuming")
        skill = draft.to_skill("clarify reqs",
                               Gate("clarify-gate", "ask before assuming"))
        self.assertEqual(skill.name, "clarify")
        self.assertIn("## Gate", skill.prompt)


if __name__ == "__main__":
    unittest.main()
