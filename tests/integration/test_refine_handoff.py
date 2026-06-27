"""Stage 26 — the refine front-end threads into the EXISTING pipeline end-to-end.

Proves a real flow: ground → propose → approve a scoped set → persist (via the Surface
state store) → `spec` turns it into acceptance criteria (incl. a "behavior preserved"
characterization criterion) → the completeness gate reads the approved refinements and
passes only when every AC maps to a (characterization) test. `refine` writes no spec
itself; the unchanged spec→test→develop→review flow does the work.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.config import Surface
from mokata.engine import Spec, TestRef, run_completeness_gate
from mokata.engine.spec import AcceptanceCriterion
from mokata.init import init_repo
from mokata.refine import (
    Refinement,
    RefineSession,
    ground_refine,
    load_approved_refinements,
    persist_refinements,
)


def _silent(_):
    pass


class TestRefineHandoffEndToEnd(unittest.TestCase):
    def test_refine_to_spec_to_completeness(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            surface = Surface.load(d)

            # 1. refine — ground (graph/memory, degrades to grep/read), propose, approve.
            grounding = ground_refine(surface.router)
            session = RefineSession("mod_a.py", grounding=grounding)
            session.propose(
                [
                    Refinement(title="extract compute() helper boundary",
                               principle="separation of concerns",
                               behavior_impact="preserving", priority=1,
                               dimension="architecture"),
                    Refinement(title="guard helper() against bad input",
                               principle="defense in depth",
                               behavior_impact="changing", priority=2,
                               dimension="security"),
                ],
                scope_in=["architecture", "security"], scope_out=["performance"])
            session.approve(["extract compute() helper boundary"], approver="jas")

            # persist through the SAME state surface the completeness gate reads
            persist_refinements(session, surface.state)

            # 2. spec (the existing skill's job, modeled here): approved changes -> ACs,
            # including a behavior-preserved (characterization) criterion.
            spec = Spec(title="refine mod_a", criteria=[
                AcceptanceCriterion("AC-1", "compute() boundary extracted"),
                AcceptanceCriterion("AC-2", "behavior preserved by characterization test"),
            ])

            # 3. test (RED) — characterization tests pin current behavior before the change.
            tests = [TestRef("test_boundary", ["AC-1"]),
                     TestRef("test_characterization", ["AC-2"])]

            # 4. completeness gate (existing) reads the approved refinements + AC↔test map.
            reloaded = Surface.load(d)              # a fresh session reads from disk
            res = run_completeness_gate(spec, tests, store=reloaded.state)

            self.assertTrue(res.passed, res.render())
            self.assertTrue(res.refinements_present)
            self.assertFalse(res.approach_present)   # the direction came from refine
            plan = load_approved_refinements(reloaded.state)
            self.assertEqual(plan.target, "mod_a.py")
            self.assertEqual([r.title for r in plan.refinements],
                             ["extract compute() helper boundary"])

    def test_gate_blocks_when_a_characterization_test_is_missing(self):
        # behavior-preserving refinement with no characterization test -> the gate blocks,
        # so behavior can't silently drift (RED-before-GREEN still rules).
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            surface = Surface.load(d)
            session = RefineSession("mod_a.py")
            session.propose([Refinement(title="restructure", behavior_impact="preserving")])
            session.approve(["restructure"], approver="jas")
            persist_refinements(session, surface.state)

            spec = Spec(title="r", criteria=[
                AcceptanceCriterion("AC-1", "restructured"),
                AcceptanceCriterion("AC-2", "behavior preserved"),
            ])
            res = run_completeness_gate(spec, [TestRef("t1", ["AC-1"])],
                                        store=surface.state)
            self.assertFalse(res.passed)
            self.assertIn("AC-2", res.unmapped_ids)
            self.assertTrue(res.refinements_present)   # still read the approved set


if __name__ == "__main__":
    unittest.main()
