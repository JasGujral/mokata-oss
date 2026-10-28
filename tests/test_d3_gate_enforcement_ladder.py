"""0.0.23 D3 — GATE-HAS-NO-ENFORCEMENT-LADDER (doc 84).

`Gate` gains the advisory | soft | hard binding a rule has had since TM.S7; a gate's verdict is
decided by the EXISTING rule Sentinel (`govern/enforce.py`), not a parallel evaluator; an advisory
gate ledgers what it would have blocked; promotion is proposal-only, human-gated and ledgered through
`govern/learning.py`. And — the negative half — every gate that blocked before still blocks.
"""

import inspect
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata import pipeline, skill_contracts, skills
from mokata.engine import run_completeness_gate
from mokata.engine.phases import run_pipeline
from mokata.engine.spec import AcceptanceCriterion, Spec, TestRef
from mokata.govern import enforce, learning
from mokata.govern.karpathy import KARPATHY_GATES, run_karpathy_gate, KarpathyContext
from mokata.govern.ledger import AuditLedger, why_timeline
from mokata.memory.item import ENFORCEMENT_LEVELS
from mokata.skills import Gate


class _LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mokata-d3-")
        self.ledger = AuditLedger(os.path.join(self.tmp, "audit.jsonl"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def kinds(self):
        return [e["kind"] for e in self.ledger.entries()]


def _spec(n=1):
    return Spec("t", [AcceptanceCriterion(f"AC{i}", "x") for i in range(1, n + 1)])


# ------------------------------------------------------------------------------------- the field
class TestD3GateCarriesEnforcement(unittest.TestCase):
    def test_d3_levels_are_the_rule_levels(self):
        # one ladder, two carriers — the literal in skills.py is pinned to memory/item.py's.
        self.assertEqual(tuple(ENFORCEMENT_LEVELS), skills.GATE_ENFORCEMENT_LEVELS)

    def test_d3_default_is_hard(self):
        self.assertEqual("hard", Gate("x", "y").enforcement)

    def test_d3_invalid_level_refused_at_construction(self):
        with self.assertRaises(ValueError):
            Gate("x", "y", "check", enforcement="sorta")

    def test_d3_promotion_kind_literal_matches(self):
        src = inspect.getsource(learning.apply_gate_promotion)
        self.assertIn('"%s"' % enforce.ENFORCEMENT_CHANGE_KIND, src)

    def test_d3_advisory_kind_registered_in_event_projection(self):
        from mokata.events import projection
        self.assertIn(enforce.GATE_ADVISORY_KIND, projection.registered_kinds())

    def test_d3_advisory_declarable(self):
        self.assertEqual("advisory", Gate("x", "y", "check", enforcement="advisory").enforcement)


# ------------------------------------------------- today's gates ship at their present level
class TestD3TodaysGatesKeepTheirLevel(unittest.TestCase):
    def _backed(self):
        return {k for k, g in skill_contracts.GATES.items() if g.backed}

    def test_d3_every_phase_gate_is_hard_and_explicit(self):
        for phase, gate in pipeline.PHASE_GATES.items():
            self.assertEqual("hard", gate.enforcement, phase)
        # explicit in source, not inherited by default — a reader sees the level.
        src = inspect.getsource(pipeline)
        self.assertEqual(len(pipeline.PHASE_GATES), src.count('enforcement="hard"'))

    def test_d3_every_backed_gate_object_is_hard(self):
        # every `Gate` object whose id is a BACKED gate (derived, not typed: doc 85 §4 membership)
        # is HARD — so nothing backed was loosened by the ladder.
        backed = self._backed()
        self.assertGreaterEqual(len(backed), 8)
        objs = list(pipeline.PHASE_GATES.values()) + [s.gate for s in skills.SKILLS.values()]
        seen = set()
        for g in objs:
            if g.id in backed:
                seen.add(g.id)
                self.assertEqual("hard", g.enforcement, g.id)
        # approach-approval, completeness, no-code-without-failing-test are Gate objects today.
        self.assertTrue({"approach-approval", "completeness",
                         "no-code-without-failing-test"} <= seen, seen)

    def test_d3_karpathy_gates_declare_what_they_do(self):
        # they never blocked (ledger pass/fail, no consumer) — so they say advisory, not hard.
        for g in KARPATHY_GATES.values():
            self.assertEqual("advisory", g.gate.enforcement)

    def test_d3_hook_gates_are_hard_while_hook_ignores_level(self):
        # gate_hook enforces approach-approval unconditionally; the declared level must agree, or
        # a demotion in pipeline.py would be a claim the hook does not honour.
        from mokata import gate_hook
        self.assertEqual(pipeline.PHASE_GATES["brainstorm"].id, gate_hook.GATE_PHASE)
        self.assertEqual("hard", pipeline.PHASE_GATES["brainstorm"].enforcement)


# ------------------------------------------------- the naming collision, resolved
class TestD3NamingCollision(unittest.TestCase):
    def test_d3_pipeline_no_longer_says_advisory_for_ungated(self):
        src = inspect.getsource(pipeline)
        self.assertNotIn("advisory/no-gate", src)
        self.assertNotIn("(no gate)", src)
        self.assertIn("(ungated)", src)

    def test_d3_render_names_level_and_ungated(self):
        out = pipeline.render_entry(pipeline.plan_entry("brainstorm", "emit"))
        self.assertIn("[gate: completeness (check, hard)]", out)
        self.assertIn("(ungated)", out)
        self.assertNotIn("advisory", out)


# ------------------------------------------------- routed through the existing Sentinel
class TestD3RoutedThroughEnforce(_LedgerCase):
    def test_d3_decide_gate_uses_enforcement_gate(self):
        # the verdict IS an EnforcementOutcome from the rule Sentinel, not a parallel evaluator.
        d = enforce.decide_gate(Gate("g", "d", "check"), False, reason="r", ledger=self.ledger)
        self.assertIsInstance(d.outcome, enforce.EnforcementOutcome)
        self.assertEqual(["rule_block"], self.kinds())
        e = self.ledger.entries()[0]
        self.assertEqual(("g", "hard", "gate", "blocked"),
                         (e["item_id"], e["enforcement"], e["scope_level"], e["decision"]))

    def test_d3_phase_gate_routes_through_decide_gate(self):
        d = pipeline.decide_phase_gate("completeness_gate", False, reason="r")
        self.assertIsInstance(d, enforce.GateDecision)
        self.assertTrue(d.blocked)

    def test_d3_ungated_phase_has_no_verdict(self):
        with self.assertRaises(pipeline.PhaseError):
            pipeline.decide_phase_gate("analysis", True)

    def test_d3_hard_refuses_even_with_override(self):
        d = enforce.decide_gate(Gate("g", "d"), False, ledger=self.ledger,
                                override=lambda _v: True)
        self.assertTrue(d.blocked)
        self.assertFalse(d.overridden)
        self.assertNotIn("rule_override", self.kinds())

    def test_d3_soft_blocks_without_override(self):
        d = enforce.decide_gate(Gate("g", "d", enforcement="soft"), False, ledger=self.ledger)
        self.assertTrue(d.blocked)
        self.assertIn("override", d.message)
        self.assertEqual(["rule_block"], self.kinds())

    def test_d3_soft_override_is_ledgered(self):
        d = enforce.decide_gate(Gate("g", "d", enforcement="soft"), False, ledger=self.ledger,
                                override=lambda _v: True, actor="jas", reason="known flake")
        self.assertTrue(d.allowed)
        self.assertTrue(d.overridden)
        [e] = self.ledger.entries()
        self.assertEqual(("rule_override", "override", "jas", "gate"),
                         (e["kind"], e["decision"], e["actor"], e["scope_level"]))

    def test_d3_advisory_ledgers_would_block_and_does_not_block(self):
        d = enforce.decide_gate(Gate("g", "d", enforcement="advisory"), False,
                                reason="AC2 unmapped", ledger=self.ledger)
        self.assertTrue(d.allowed)
        self.assertTrue(d.would_block)
        self.assertFalse(d.check_passed)
        [e] = self.ledger.entries()
        self.assertEqual(("gate_advisory", "would-block", True),
                         (e["kind"], e["decision"], e["would_block"]))
        self.assertIn("would have blocked", d.message)
        self.assertTrue(any("advisory gate" in line for line in why_timeline(self.ledger.entries())))

    def test_d3_advisory_clean_pass_is_ledgered(self):
        d = enforce.decide_gate(Gate("g", "d", enforcement="advisory"), True, ledger=self.ledger)
        self.assertTrue(d.allowed)
        self.assertFalse(d.would_block)
        [e] = self.ledger.entries()
        self.assertEqual(("gate_advisory", "pass"), (e["kind"], e["decision"]))

    def test_d3_passing_hard_gate_ledgers_nothing(self):
        d = enforce.decide_gate(Gate("g", "d"), True, ledger=self.ledger)
        self.assertTrue(d.allowed)
        self.assertEqual([], self.kinds())

    def test_d3_undeterminable_level_fails_closed(self):
        class Duck:   # a duck-typed gate whose level is unreadable — not constructible as a Gate
            id, description, enforcement = "duck", "d", "sorta"
        d = enforce.decide_gate(Duck(), False, ledger=self.ledger, override=lambda _v: True)
        self.assertTrue(d.blocked)
        self.assertFalse(d.overridden)
        self.assertTrue(d.outcome.verdict.fail_closed)
        self.assertEqual(["rule_block"], self.kinds())


# ------------------------------------------------- negative: nothing that blocked stops blocking
class TestD3NothingStopsBlocking(_LedgerCase):
    def test_d3_completeness_still_blocks_unmapped(self):
        r = run_completeness_gate(_spec(2), [TestRef("t", ["AC1"])])
        self.assertFalse(r.passed)
        self.assertEqual("hard", r.enforcement)
        self.assertFalse(r.would_block)

    def test_d3_completeness_still_blocks_empty_spec(self):
        self.assertFalse(run_completeness_gate(Spec("t", []), []).passed)

    def test_d3_completeness_override_cannot_clear_hard(self):
        r = run_completeness_gate(_spec(), [], ledger=self.ledger, override=lambda _v: True)
        self.assertFalse(r.passed)
        self.assertFalse(r.overridden)

    def test_d3_completeness_block_is_ledgered(self):
        run_completeness_gate(_spec(), [], ledger=self.ledger)
        self.assertEqual(["rule_block"], self.kinds())

    def test_d3_completeness_passes_when_mapped(self):
        r = run_completeness_gate(_spec(), [TestRef("t", ["AC1"])], ledger=self.ledger)
        self.assertTrue(r.passed)
        self.assertEqual([], self.kinds())

    def test_d3_pipeline_still_refuses_emit_on_unmapped(self):
        from mokata.brainstorm import Approach, Grounding, Handoff
        handoff = Handoff(topic="t", approach=Approach("a", "b"), answered_questions=[],
                          grounding=Grounding(False, None, False, None), approver="jas", approved_at="now")
        run = run_pipeline(handoff, with_tasks(_spec()), [], ledger=self.ledger)
        self.assertFalse(run.ok)
        rec = {r.name: r for r in run.phases}
        self.assertFalse(rec["completeness_gate"].gate_passed)
        self.assertFalse(rec["emit"].ok)
        self.assertIn("rule_block", self.kinds())

    def test_d3_ledger_promotion_cannot_demote_a_hard_gate(self):
        # a forged/mistaken `enforcement_change` to advisory on the ledger does NOT loosen a gate
        # declared hard — the declared level is a floor (most-restrictive-wins).
        self.ledger.record("enforcement_change", subject="completeness", item_id="completeness",
                           gkind="gate", old="hard", new="advisory", direction="demote")
        r = run_completeness_gate(_spec(), [], ledger=self.ledger)
        self.assertFalse(r.passed)
        self.assertEqual("hard", r.enforcement)

    def test_d3_karpathy_fires_unchanged(self):
        # still record-only: a failing fire is ledgered as karpathy_gate, nothing else.
        fire = run_karpathy_gate("think-first", KarpathyContext(has_plan=False), ledger=self.ledger)
        self.assertFalse(fire.passed)
        self.assertEqual(["karpathy_gate"], self.kinds())


# ------------------------------------------------- promotion: proposal-only, human-gated, ledgered
class TestD3Promotion(_LedgerCase):
    def setUp(self):
        super().setUp()
        self.gate = Gate("new-wall", "a 0.0.24-style gate", "check", enforcement="advisory")

    def _runs(self, *results):
        for ok in results:
            enforce.decide_gate(self.gate, ok, ledger=self.ledger)

    def test_d3_no_proposal_below_threshold(self):
        self._runs(True, True)
        self.assertEqual([], learning.gate_promotion_proposals(self.ledger, [self.gate],
                                                               threshold=3))

    def test_d3_proposal_at_threshold_is_proposal_only(self):
        self._runs(True, True, True)
        before = len(self.ledger.entries())
        [p] = learning.gate_promotion_proposals(self.ledger, [self.gate], threshold=3)
        self.assertEqual("gate:new-wall", p.pattern_key)
        self.assertIn("advisory → soft", p.proposed_rule)
        # pure read: nothing written, level unchanged
        self.assertEqual(before, len(self.ledger.entries()))
        self.assertEqual("advisory", enforce.effective_gate_enforcement(self.gate, self.ledger))

    def test_d3_would_block_resets_the_streak(self):
        self._runs(True, True, False, True)
        self.assertEqual([], learning.gate_promotion_proposals(self.ledger, [self.gate],
                                                               threshold=3))
        [row] = learning.advisory_gate_report(self.ledger, [self.gate])
        self.assertEqual((4, 1, 1), (row.evaluations, row.would_block, row.clean_streak))

    def test_d3_proposal_recorded_when_learner_has_ledger(self):
        self._runs(True, True, True)
        learner = learning.RulesLearner(threshold=3, ledger=self.ledger)
        learning.gate_promotion_proposals(self.ledger, [self.gate], learner=learner)
        self.assertIn("rule_promotion_proposed", self.kinds())

    def test_d3_default_declines(self):
        self._runs(True, True, True)
        [p] = learning.gate_promotion_proposals(self.ledger, [self.gate], threshold=3)
        self.assertFalse(learning.apply_gate_promotion(p, self.gate, self.ledger, "approve"))
        self.assertNotIn("enforcement_change", self.kinds())
        self.assertEqual("advisory", enforce.effective_gate_enforcement(self.gate, self.ledger))

    def test_d3_reject_adds_nothing(self):
        self._runs(True, True, True)
        [p] = learning.gate_promotion_proposals(self.ledger, [self.gate], threshold=3)
        self.assertFalse(learning.apply_gate_promotion(p, self.gate, self.ledger, "reject",
                                                       assume_yes=True))
        self.assertNotIn("enforcement_change", self.kinds())

    def test_d3_no_ledger_no_promotion(self):
        p = learning.RulePromotion("gate:new-wall", "x", 3, "y")
        self.assertFalse(learning.apply_gate_promotion(p, self.gate, None, "approve",
                                                       assume_yes=True))

    def test_d3_approved_promotion_makes_gate_soft_blocking(self):
        self._runs(True, True, True)
        [p] = learning.gate_promotion_proposals(self.ledger, [self.gate], threshold=3)
        self.assertTrue(learning.apply_gate_promotion(p, self.gate, self.ledger, "approve",
                                                      confirm=lambda _t: True, actor="jas"))
        change = [e for e in self.ledger.entries() if e["kind"] == "enforcement_change"][-1]
        self.assertEqual(("gate", "advisory", "soft", "jas"),
                         (change["gkind"], change["old"], change["new"], change["actor"]))
        self.assertIsNotNone(change["approval_seq"])
        # Ruled 2026-10-16: a promoted gate BLOCKS, but a human override gets through, ledgered.
        self.assertEqual("soft", enforce.effective_gate_enforcement(self.gate, self.ledger))
        d = enforce.decide_gate(self.gate, False, ledger=self.ledger, override=lambda _v: False)
        self.assertTrue(d.blocked)
        self.assertFalse(d.would_block)
        through = enforce.decide_gate(self.gate, False, ledger=self.ledger, override=lambda _v: True)
        self.assertFalse(through.blocked)
        self.assertIn("rule_override", self.kinds())
        # promotion lands on soft and stops: a second approval cannot ratchet it to hard
        self.assertFalse(learning.apply_gate_promotion(p, self.gate, self.ledger, "approve",
                                                       confirm=lambda _t: True, actor="jas"))
        # and a promoted gate is no longer reported as long-lived advisory
        self.assertEqual([], learning.advisory_gate_report(self.ledger, [self.gate]))

    def test_d3_promotion_for_other_gate_refused(self):
        p = learning.RulePromotion("gate:other", "x", 3, "y")
        with self.assertRaises(ValueError):
            learning.apply_gate_promotion(p, self.gate, self.ledger, "approve", assume_yes=True)


if __name__ == "__main__":
    unittest.main()
