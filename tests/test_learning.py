"""G5 — rules-learning + reinforcement: a recurring pattern PROPOSES a rule promotion
(human-gated, never auto-added); proposals and decisions are logged."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import AuditLedger, RulesLearner


class TestRulesLearning(unittest.TestCase):
    def test_proposes_at_threshold_and_does_not_auto_add(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            sink = []
            learner = RulesLearner(threshold=3, ledger=led)
            self.assertIsNone(learner.observe("retry-without-backoff"))
            self.assertIsNone(learner.observe("retry-without-backoff"))
            promo = learner.observe("retry-without-backoff")
            self.assertIsNotNone(promo)                 # proposed at the 3rd recurrence
            self.assertEqual(sink, [])                  # NEVER auto-added
            self.assertIn("rule_promotion_proposed",
                          [e["kind"] for e in led.entries()])

    def test_apply_is_human_gated(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            sink = []
            learner = RulesLearner(threshold=1, ledger=led)
            promo = learner.observe("always-validate-input", "Always validate input.")
            # reject -> nothing added
            self.assertFalse(learner.apply_promotion(promo, "reject", sink=sink.append))
            self.assertEqual(sink, [])
            # approve -> added (human-gated)
            self.assertTrue(learner.apply_promotion(promo, "approve", assume_yes=True,
                                                    sink=sink.append))
            self.assertEqual(sink, ["Always validate input."])
            self.assertIn("rule_promotion_decision",
                          [e["kind"] for e in led.entries()])

    def test_default_confirm_declines_so_nothing_is_auto_added(self):
        learner = RulesLearner(threshold=1)
        promo = learner.observe("p")
        sink = []
        # approve intent but no confirmation callback -> declines (no auto-add)
        self.assertFalse(learner.apply_promotion(promo, "approve", sink=sink.append))
        self.assertEqual(sink, [])

    def test_proposes_each_pattern_once(self):
        learner = RulesLearner(threshold=2)
        learner.observe("p")
        first = learner.observe("p")
        again = learner.observe("p")
        self.assertIsNotNone(first)
        self.assertIsNone(again)        # not re-proposed every recurrence


if __name__ == "__main__":
    unittest.main()
