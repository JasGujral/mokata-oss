"""0.0.23 D13 — G-3: free-text intent -> skill recommendation, on top of `compose.suggest`.

Business claims:
  * "flaky bug" -> /debug first (the row's own example); the obvious intents land on the obvious
    skill; every recommendation names the words that produced it;
  * deterministic — no model, same input same output; whole-word matching ("pr" never inside
    "print"); an unmatched intent returns NOTHING rather than a guess;
  * it composes with the flag-based suggest, which stays byte-identical when no intent is given;
  * reachable from the CLI (`mokata suggest "<intent>"`) and the MCP `suggest` tool (`intent=`);
  * recommend-only: every recommended name is a real skill, and nothing is run.
"""

import io
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.cli import main
from mokata.compose import (INTENT_TERMS, SuggestionContext, recommend, suggest)
from mokata.mcp import tools_read
from mokata.skills import list_skills


def _top(intent, ctx=None):
    recs = recommend(intent, ctx)
    return recs[0].skill if recs else None


class IntentLandsOnTheRightSkill(unittest.TestCase):
    CASES = (
        ("flaky bug", "debug"),
        ("the test is flaky on CI", "debug"),
        ("users hit a crash with this traceback", "debug"),
        ("here is a reproducer for the bug report", "bug"),
        ("the endpoint is slow, latency doubled", "optimize"),
        ("refactor this legacy module", "refine"),
        ("I want to add a new feature for CSV export", "brainstorm"),
        ("write acceptance criteria for the login change", "spec"),
        ("write tests for the parser", "test"),
        ("I have a failing test, make it pass", "develop"),
        ("please review my pull request", "review"),
        ("ready to ship and release", "ship"),
        ("which version of mokata is installed", "version"),
        ("capture our project rules and conventions", "onboard"),
    )

    def test_cases(self):
        for intent, want in self.CASES:
            with self.subTest(intent=intent):
                self.assertEqual(_top(intent), want, [(r.skill, r.score)
                                                      for r in recommend(intent)])

    def test_reason_names_the_matched_words(self):
        r = recommend("flaky bug")[0]
        self.assertIn("flaky", r.matched)
        self.assertIn("'flaky'", r.reason)


class DeterministicAndHonest(unittest.TestCase):
    def test_same_input_same_output(self):
        a = [(r.skill, r.score, r.reason) for r in recommend("slow flaky test in review")]
        for _ in range(5):
            self.assertEqual(a, [(r.skill, r.score, r.reason)
                                 for r in recommend("slow flaky test in review")])

    def test_unmatched_intent_is_not_guessed(self):
        self.assertEqual(recommend("hello world"), [])
        self.assertEqual(recommend(""), [])
        self.assertEqual(recommend("   !!! "), [])

    def test_whole_words_only(self):
        # "pr" must not fire inside "print", "test" not inside "latest", "ship" not in "relationship"
        self.assertEqual(recommend("print the latest relationship"), [])

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(_top("FLAKY-bug!!"), "debug")

    def test_limit(self):
        self.assertLessEqual(len(recommend("slow flaky test review ship spec")), 3)
        self.assertEqual(len(recommend("slow flaky test review", limit=1)), 1)
        self.assertEqual(recommend("flaky", limit=0), [])

    def test_every_term_table_entry_is_a_real_skill(self):
        names = {n for n, _ in list_skills()}
        self.assertTrue(set(INTENT_TERMS) <= names, set(INTENT_TERMS) - names)


class ComposesWithFlags(unittest.TestCase):
    def test_flags_add_to_the_intent(self):
        recs = recommend("I am stuck", SuggestionContext(has_diff=True))
        self.assertEqual(recs[0].skill, "review")
        self.assertIn("a diff is present", recs[0].reason)

    def test_flag_breaks_a_word_tie(self):
        # "bug" alone leans /bug; a stack trace flag tips it to /debug
        self.assertEqual(_top("bug"), "bug")
        self.assertEqual(_top("bug", SuggestionContext(has_stacktrace=True)), "debug")

    def test_plain_suggest_is_unchanged(self):
        self.assertEqual([s.skill for s in suggest(SuggestionContext(has_diff=True))], ["review"])


class Surfaces(unittest.TestCase):
    def _cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_cli_quoted_and_unquoted_intent(self):
        for argv in (["suggest", "flaky bug"], ["suggest", "flaky", "bug"]):
            rc, out = self._cli(argv)
            self.assertEqual(rc, 0)
            self.assertIn("/debug", out.splitlines()[1])
            self.assertIn("not run", out)

    def test_cli_unmatched_intent_says_so(self):
        rc, out = self._cli(["suggest", "hello world"])
        self.assertEqual(rc, 0)
        self.assertIn("no skill matched", out)

    def test_cli_flags_without_intent_unchanged(self):
        rc, out = self._cli(["suggest", "--diff"])
        self.assertEqual(rc, 0)
        self.assertIn("suggested (not run", out)
        self.assertIn("/review", out)

    def test_mcp_intent_param(self):
        res = tools_read.suggest(intent="flaky bug")
        self.assertEqual(res["suggestions"][0]["skill"], "debug")
        self.assertIn("flaky", res["suggestions"][0]["matched"])
        self.assertEqual(tools_read.suggest(intent="hello world")["suggestions"], [])

    def test_mcp_without_intent_byte_identical_shape(self):
        res = tools_read.suggest(diff=True)
        # E2 (AXI-OUT) adds a `count` to every list answer; the intent-less SHAPE is otherwise
        # exactly the pre-G-3 one.
        self.assertEqual(res, {"suggestions": [
            {"skill": "review", "reason": "a diff is present — review against the spec"}],
            "count": 1})


if __name__ == "__main__":
    unittest.main()
