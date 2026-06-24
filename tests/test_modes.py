"""E5/E6 — the bug, debug, and optimize engines (replacing the Stage-6 scaffolds).
Reproducer-before-fix, root-cause-before-fix (N-strikes), and measure-first are gated."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.execmode import ModelRouter
from mokata.modes import (
    FIXING,
    REPORTED,
    REPRODUCED,
    VERIFIED,
    Bug,
    BugFlow,
    DebugSession,
    MeasureFirstError,
    OptimizeSession,
    ReproRequiredError,
    RootCauseRequiredError,
)


# --- E5: bug-fix mode ----------------------------------------------------------
class TestBugFlow(unittest.TestCase):
    def test_fix_is_blocked_before_a_reproducer(self):
        flow = BugFlow(Bug("B1", "crash on save"))
        with self.assertRaises(ReproRequiredError):
            flow.start_fix()

    def test_label_progression(self):
        flow = BugFlow(Bug("B1", "crash on save"))
        self.assertEqual(flow.label, REPORTED)
        flow.reproduce("test_crash_on_save")
        self.assertEqual(flow.label, REPRODUCED)
        flow.start_fix()
        self.assertEqual(flow.label, FIXING)
        flow.verify()
        self.assertEqual(flow.label, VERIFIED)

    def test_reproducer_is_captured(self):
        flow = BugFlow(Bug("B1", "x"))
        flow.reproduce("test_repro")
        self.assertEqual(flow.bug.reproducer, "test_repro")


# --- E6: debug mode ------------------------------------------------------------
class TestDebugSession(unittest.TestCase):
    def test_fix_blocked_without_root_cause(self):
        s = DebugSession("flaky test", max_strikes=3)
        with self.assertRaises(RootCauseRequiredError):
            s.propose_fix()

    def test_n_strikes_escalates(self):
        s = DebugSession("flaky test", max_strikes=2)
        s.rule_out(s.hypothesize("a race condition"))
        self.assertFalse(s.escalated)
        s.rule_out(s.hypothesize("a stale cache"))
        self.assertTrue(s.escalated)             # N strikes reached

    def test_root_cause_allows_fix(self):
        s = DebugSession("flaky test")
        s.set_root_cause("uninitialised variable")
        self.assertIn("uninitialised variable", s.propose_fix())

    def test_escalation_bumps_the_model_when_a_router_is_present(self):
        s = DebugSession("flaky", max_strikes=1, router=ModelRouter())
        self.assertEqual(s.model.name, "fast")
        s.rule_out(s.hypothesize("guess"))
        self.assertTrue(s.escalated)
        self.assertEqual(s.model.name, "balanced")


# --- E6: optimize mode ---------------------------------------------------------
class TestOptimizeSession(unittest.TestCase):
    def test_change_blocked_before_a_baseline_measurement(self):
        o = OptimizeSession("hot loop")
        with self.assertRaises(MeasureFirstError):
            o.apply_change("memoize")

    def test_accept_requires_improvement_and_preserved_behavior(self):
        o = OptimizeSession("hot loop")
        o.measure_baseline(100.0)
        o.apply_change("memoize")
        o.measure_after(60.0, behavior_preserved=True)
        res = o.accept()
        self.assertTrue(res.kept)
        self.assertTrue(res.improved)

    def test_rejects_when_not_faster(self):
        o = OptimizeSession("hot loop")
        o.measure_baseline(100.0)
        o.apply_change("x")
        o.measure_after(120.0, behavior_preserved=True)
        self.assertFalse(o.accept().kept)

    def test_rejects_when_behavior_changed(self):
        o = OptimizeSession("hot loop")
        o.measure_baseline(100.0)
        o.apply_change("x")
        o.measure_after(50.0, behavior_preserved=False)
        self.assertFalse(o.accept().kept)


if __name__ == "__main__":
    unittest.main()
