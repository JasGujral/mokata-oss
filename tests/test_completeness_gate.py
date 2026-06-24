"""D2 — completeness gate: provable-completeness blocker. Emit is refused until every
acceptance criterion maps to a test. Reads the approved approach/handoff from the
brainstorm phase, and is wired as the `completeness_gate` phase's gate."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import Approach, BrainstormSession, persist_approach
from mokata.engine import (
    PHASE_GATE_CHECKS,
    AcceptanceCriterion,
    Spec,
    TestRef,
    run_completeness_gate,
)
from mokata.pipeline import PHASE_GATES
from mokata.state import StateStore


def spec2():
    return Spec(title="login", criteria=[
        AcceptanceCriterion("AC-1", "log in"),
        AcceptanceCriterion("AC-2", "log out"),
    ])


def approved_handoff():
    s = BrainstormSession("auth")
    s.propose_approaches([
        Approach("sessions", "server sessions", pros=["simple"], cons=["server state"]),
        Approach("jwt", "stateless tokens", pros=["stateless"], cons=["revocation"]),
    ])
    s.approve("jas", "jwt")
    return s.handoff()


class TestGateBlocksAndPasses(unittest.TestCase):
    def test_blocks_emit_when_an_ac_is_unmapped(self):
        res = run_completeness_gate(spec2(), [TestRef("t1", ["AC-1"])])
        self.assertFalse(res.passed)
        self.assertIn("AC-2", res.unmapped_ids)
        self.assertIn("unmapped", res.reason.lower())

    def test_passes_only_when_every_ac_maps(self):
        res = run_completeness_gate(
            spec2(), [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])])
        self.assertTrue(res.passed)
        self.assertEqual(res.unmapped_ids, [])

    def test_empty_spec_does_not_vacuously_pass(self):
        res = run_completeness_gate(Spec("empty", []), [])
        self.assertFalse(res.passed)


class TestGateReadsApprovedApproach(unittest.TestCase):
    def test_reads_handoff_passed_directly(self):
        res = run_completeness_gate(
            spec2(), [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])],
            handoff=approved_handoff())
        self.assertTrue(res.approach_present)
        self.assertEqual(res.approach, "jwt")

    def test_reads_handoff_from_the_brainstorm_state_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            # the brainstorm phase persisted its approved approach here
            session = BrainstormSession("auth")
            session.propose_approaches([
                Approach("sessions", "x", pros=["a"], cons=["b"]),
                Approach("jwt", "y", pros=["c"], cons=["d"]),
            ])
            session.approve("jas", "jwt")
            persist_approach(session, store)

            res = run_completeness_gate(
                spec2(), [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])],
                store=store)
            self.assertTrue(res.approach_present)
            self.assertEqual(res.approach, "jwt")


class TestWiredAsPhaseGate(unittest.TestCase):
    def test_completeness_gate_is_the_completeness_gate_phase_gate(self):
        # metadata lives in the existing pipeline; the executable check is wired to it
        self.assertEqual(PHASE_GATES["completeness_gate"].id, "completeness")
        self.assertIs(PHASE_GATE_CHECKS["completeness_gate"], run_completeness_gate)


if __name__ == "__main__":
    unittest.main()
