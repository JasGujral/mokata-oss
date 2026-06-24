"""L2 — mid-pipeline entry/exit over the existing PIPELINE_PHASES: enter at any phase,
stop after; the gates of the phases you run apply; upstream phases are NOT forced and
their gates are NOT applied (but skipping is explicit, never silent)."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import PIPELINE_PHASES
from mokata.pipeline import PhaseError, plan_entry, render_entry


def gate_ids(plan):
    return [g.id for g in plan.gates_applied]


class TestEntry(unittest.TestCase):
    def test_enter_single_phase_by_default(self):
        plan = plan_entry("completeness_gate")
        self.assertEqual(plan.phases_run, ["completeness_gate"])

    def test_entering_late_does_not_force_upstream_gates(self):
        plan = plan_entry("completeness_gate")
        # the completeness gate applies...
        self.assertIn("completeness", gate_ids(plan))
        # ...but the brainstorm approval gate does NOT (it's upstream, not run)
        self.assertNotIn("approach-approval", gate_ids(plan))
        self.assertIn("brainstorm", plan.skipped_upstream)

    def test_skipping_is_explicit_not_silent(self):
        plan = plan_entry("probes")
        # every upstream phase is named as skipped
        self.assertEqual(
            plan.skipped_upstream,
            ["brainstorm", "analysis", "strawman", "pre_mortem"],
        )
        text = render_entry(plan)
        self.assertIn("skipped", text.lower())
        self.assertIn("brainstorm", text)

    def test_every_run_phase_gate_is_applied(self):
        # entering at brainstorm and running to emit applies all gated phases in range
        plan = plan_entry("brainstorm", stop="emit")
        self.assertIn("approach-approval", gate_ids(plan))
        self.assertIn("completeness", gate_ids(plan))
        self.assertIn("emit-approval", gate_ids(plan))

    def test_range_runs_a_slice(self):
        plan = plan_entry("strawman", stop="probes")
        self.assertEqual(plan.phases_run,
                         ["strawman", "pre_mortem", "probes"])

    def test_unknown_phase_raises(self):
        with self.assertRaises(PhaseError):
            plan_entry("nonsense")

    def test_stop_before_start_raises(self):
        with self.assertRaises(PhaseError):
            plan_entry("emit", stop="brainstorm")

    def test_uses_the_existing_pipeline_phases(self):
        # L2 is defined over the spine's PIPELINE_PHASES, not a parallel list
        plan = plan_entry(PIPELINE_PHASES[0])
        self.assertEqual(plan.phases_run, [PIPELINE_PHASES[0]])


if __name__ == "__main__":
    unittest.main()
