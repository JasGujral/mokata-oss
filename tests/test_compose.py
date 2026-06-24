"""L5 — manual chaining (each step's gate applies); L6 — context-aware suggestions
(suggest only, never auto-run)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.compose import (
    SuggestionContext,
    plan_chain,
    run_chain,
    suggest,
)
from mokata.skills import SkillNotFound


class TestChaining(unittest.TestCase):
    def test_plan_chain_carries_each_steps_gate(self):
        steps = plan_chain(["spec", "test"])
        self.assertEqual([s.skill for s in steps], ["spec", "test"])
        self.assertEqual(steps[0].gate, "completeness")
        self.assertEqual(steps[1].gate, "red-before-green")

    def test_run_chain_runs_steps_in_order_with_gates(self):
        ran = []
        result = run_chain(["debug", "test"], runner=lambda name: ran.append(name))
        self.assertEqual(ran, ["debug", "test"])             # ran in order
        self.assertTrue(all(s.gate for s in result.steps))   # each step has its gate

    def test_unknown_skill_in_chain_raises(self):
        with self.assertRaises(SkillNotFound):
            plan_chain(["spec", "nonsense"])


class TestSuggestions(unittest.TestCase):
    def test_suggests_review_for_a_diff_without_running(self):
        suggestions = suggest(SuggestionContext(has_diff=True))
        skills = [s.skill for s in suggestions]
        self.assertIn("review", skills)
        # suggest returns data only — there is no runner / side effect
        self.assertTrue(all(hasattr(s, "reason") for s in suggestions))

    def test_suggests_bug_for_a_bug_report(self):
        self.assertIn("bug",
                      [s.skill for s in suggest(SuggestionContext(has_bug_report=True))])

    def test_suggests_brainstorm_when_starting_fresh(self):
        self.assertIn("brainstorm",
                      [s.skill for s in suggest(SuggestionContext(starting_fresh=True))])

    def test_suggests_develop_after_a_failing_test(self):
        ctx = SuggestionContext(has_failing_test=True, has_implementation=False)
        self.assertIn("develop", [s.skill for s in suggest(ctx)])

    def test_empty_context_suggests_nothing_runnable(self):
        # no signals -> no suggestions; nothing is ever auto-run
        self.assertEqual(suggest(SuggestionContext()), [])


if __name__ == "__main__":
    unittest.main()
