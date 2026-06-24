"""G1/G2 — 4-tier rules + constitution under the unified config (always-on rules ≤60
lines), and the rules-vs-gates-vs-hooks taxonomy."""

import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.config import Surface
from mokata.govern import (
    ADVISORY,
    BLOCKING,
    EVENT,
    RULE_TIERS,
    always_on_rules,
    classify,
    load_rules,
    mechanism_for,
    validate_caps,
)
from mokata.init import init_repo


def silent(_):
    pass


class TestFourTierRules(unittest.TestCase):
    def test_the_four_tiers_exist(self):
        self.assertEqual(
            RULE_TIERS, ("always_on", "agent_memory", "steering", "articles"))

    def test_always_on_rules_within_60_line_cap(self):
        rs = always_on_rules()
        self.assertLessEqual(rs.line_count, 60)
        self.assertTrue(rs.within_cap)

    def test_load_rules_pulls_articles_from_the_constitution(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rules = load_rules(Surface.load(d))
            self.assertIn("articles", rules)
            self.assertGreater(rules["articles"].line_count, 0)   # constitution loaded
            self.assertTrue(rules["always_on"].within_cap)

    def test_validate_caps_flags_an_oversized_tier(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rules = load_rules(Surface.load(d))
            self.assertEqual(validate_caps(rules), [])           # defaults are fine
            # blow the always-on cap and confirm it's flagged
            rules["always_on"].lines = ["x"] * 61
            self.assertTrue(any("always_on" in e for e in validate_caps(rules)))


class TestTaxonomy(unittest.TestCase):
    def test_advisory_stays_prose(self):
        self.assertEqual(classify(blocking=False, on_event=False), ADVISORY)
        self.assertEqual(mechanism_for(ADVISORY), "rule")

    def test_blocking_becomes_a_gate(self):
        self.assertEqual(classify(blocking=True), BLOCKING)
        self.assertEqual(mechanism_for(BLOCKING), "gate")

    def test_event_becomes_a_hook(self):
        self.assertEqual(classify(on_event=True), EVENT)
        self.assertEqual(mechanism_for(EVENT), "hook")


if __name__ == "__main__":
    unittest.main()
