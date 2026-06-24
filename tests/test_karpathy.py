"""G3 (hybrid) — Karpathy principles as gates: the engine implements each check; the
rules layer owns registration, per-config toggle, and audit (reuses the Gate type /
PHASE_GATES pattern / ledger). Each gate fires at its pipeline point."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import (
    KARPATHY_GATES,
    AuditLedger,
    KarpathyContext,
    run_karpathy_for_phase,
    run_karpathy_pipeline,
)
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data


def all_pass_ctx():
    return KarpathyContext(has_plan=True, complexity=2, max_complexity=5,
                           touched_files=3, max_scope=10,
                           has_success_criteria=True, verified=True)


class TestKarpathyRegistry(unittest.TestCase):
    def test_four_gates_registered_at_their_phases(self):
        self.assertEqual(set(KARPATHY_GATES),
                         {"think-first", "simplicity", "surgical-scope", "verify"})
        phases = {gid: g.phase for gid, g in KARPATHY_GATES.items()}
        self.assertEqual(phases["think-first"], "analysis")
        self.assertEqual(phases["simplicity"], "strawman")
        self.assertEqual(phases["surgical-scope"], "emit")
        self.assertEqual(phases["verify"], "completeness_gate")

    def test_gates_reuse_the_gate_type(self):
        from mokata.skills import Gate
        self.assertIsInstance(KARPATHY_GATES["think-first"].gate, Gate)


class TestKarpathyFiring(unittest.TestCase):
    def test_gate_fires_at_its_phase_and_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            fires = run_karpathy_for_phase("analysis", all_pass_ctx(), ledger=led)
            self.assertEqual([f.gate_id for f in fires], ["think-first"])
            self.assertTrue(fires[0].passed)
            self.assertIn("karpathy_gate", [e["kind"] for e in led.entries()])

    def test_pipeline_fires_all_four_in_order(self):
        fires = run_karpathy_pipeline(all_pass_ctx())
        # pipeline-phase order: completeness_gate (verify) precedes emit (surgical-scope)
        self.assertEqual([f.gate_id for f in fires],
                         ["think-first", "simplicity", "verify", "surgical-scope"])
        self.assertTrue(all(f.passed for f in fires))

    def test_a_check_can_fail(self):
        ctx = KarpathyContext(has_plan=True, touched_files=99, max_scope=10,
                              has_success_criteria=True, verified=True)
        fires = run_karpathy_for_phase("emit", ctx)
        self.assertEqual(fires[0].gate_id, "surgical-scope")
        self.assertFalse(fires[0].passed)


class TestKarpathyToggle(unittest.TestCase):
    def test_disabling_a_gate_stops_it_firing(self):
        data = build_manifest_data("full", "0.1.0")
        data["settings"]["governance"] = {"karpathy": {"think-first": False}}
        m = Manifest.from_dict(data)
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            fires = run_karpathy_for_phase("analysis", all_pass_ctx(),
                                           manifest=m, ledger=led)
            self.assertEqual(fires, [])           # disabled -> does not fire
            self.assertEqual(led.entries(), [])   # and is not audited (nothing fired)

    def test_other_gates_still_fire_when_one_disabled(self):
        data = build_manifest_data("full", "0.1.0")
        data["settings"]["governance"] = {"karpathy": {"think-first": False}}
        m = Manifest.from_dict(data)
        fires = run_karpathy_pipeline(all_pass_ctx(), manifest=m)
        ids = {f.gate_id for f in fires}
        self.assertNotIn("think-first", ids)
        self.assertIn("verify", ids)


if __name__ == "__main__":
    unittest.main()
