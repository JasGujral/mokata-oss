"""D1 — the full 7-phase pipeline runs end-to-end, each phase consuming the prior
handoff: brainstorm -> analysis -> strawman -> pre_mortem -> probes ->
completeness_gate -> emit."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import PIPELINE_PHASES, Approach, BrainstormSession
from mokata.engine import (
    AcceptanceCriterion,
    Spec,
    TestRef,
    run_pipeline,
)
from mokata.govern import AuditLedger
from mokata.state import StateStore


def handoff():
    s = BrainstormSession("slugify")
    s.ask("unicode or ascii-only?")
    s.answer("ascii-only")
    s.propose_approaches([
        Approach("regex", "strip via regex", pros=["tiny"], cons=["unicode edge cases"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["dependency"]),
    ])
    s.approve("jas", "regex")
    return s.handoff()


def spec2():
    return Spec("slugify", [AcceptanceCriterion("AC-1", "lowercase + hyphenate"),
                            AcceptanceCriterion("AC-2", "strip punctuation")])


FULL_TESTS = [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])]
PARTIAL_TESTS = [TestRef("t1", ["AC-1"])]


class TestSevenPhaseFlow(unittest.TestCase):
    def test_story_flows_through_all_seven_phases(self):
        with tempfile.TemporaryDirectory() as d:
            run = run_pipeline(handoff(), spec2(), FULL_TESTS,
                               store=StateStore(os.path.join(d, "state")))
            self.assertEqual(run.sequence, list(PIPELINE_PHASES))
            self.assertTrue(all(r.ok for r in run.phases))
            self.assertTrue(run.ok)
            self.assertIsNotNone(run.emitted)

    def test_each_phase_consumes_the_prior_output(self):
        with tempfile.TemporaryDirectory() as d:
            run = run_pipeline(handoff(), spec2(), FULL_TESTS,
                               store=StateStore(os.path.join(d, "state")))
            ctx = run.context
            # analysis fed strawman; strawman covers the spec's ACs
            self.assertIsNotNone(ctx.analysis)
            self.assertEqual(set(ctx.strawman.coverage), {"AC-1", "AC-2"})
            # pre_mortem produced probes; completeness read the brainstorm handoff
            self.assertTrue(ctx.probes)
            self.assertTrue(ctx.gate_result.approach_present)

    def test_completeness_blocks_emit_when_an_ac_is_unmapped(self):
        with tempfile.TemporaryDirectory() as d:
            run = run_pipeline(handoff(), spec2(), PARTIAL_TESTS,
                               store=StateStore(os.path.join(d, "state")))
            # all 7 phases still ran...
            self.assertEqual(len(run.phases), 7)
            # ...but the gate failed and emit was refused
            gate = next(r for r in run.phases if r.name == "completeness_gate")
            emit = next(r for r in run.phases if r.name == "emit")
            self.assertFalse(gate.ok)
            self.assertFalse(emit.ok)
            self.assertIsNone(run.emitted)

    def test_emit_is_human_gated(self):
        with tempfile.TemporaryDirectory() as d:
            run = run_pipeline(handoff(), spec2(), FULL_TESTS,
                               store=StateStore(os.path.join(d, "state")),
                               approve=False)              # human declines the emit
            emit = next(r for r in run.phases if r.name == "emit")
            self.assertFalse(emit.ok)
            self.assertIsNone(run.emitted)

    def test_phases_are_logged_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run_pipeline(handoff(), spec2(), FULL_TESTS,
                         store=StateStore(os.path.join(d, "state")), ledger=led)
            phases = [e["phase"] for e in led.entries() if e["kind"] == "phase"]
            self.assertEqual(phases, list(PIPELINE_PHASES))


if __name__ == "__main__":
    unittest.main()
