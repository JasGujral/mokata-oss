"""Stage 31 — plan-adherence + deviation gate (never silently deviate).

Both jsonschema states. The develop/test/refine prompts carry the ask-before-deviation
clause (and review makes the backstop explicit); a deviation that changes an AC without
amending the spec is caught by the completeness gate / spec-compliance review; and a
deviation request + decision are recorded in the audit ledger (a plan change is a gate
decision).
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.engine import Spec, TestRef, run_completeness_gate, spec_compliance_review
from mokata.engine.spec import AcceptanceCriterion
from mokata.govern import AuditLedger, DeviationGate, DeviationRequest
from mokata.govern.deviation import (
    ACCEPTANCE_CRITERIA,
    APPROVED,
    DECLINED,
    DEVIATION_KIND,
    PROPOSED,
)
from mokata.skills import command_markdown, get_skill

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------- the prompt clause (forward guardrail)

class TestPlanAdherenceClause(unittest.TestCase):
    def _both(self, name):
        """Return (skill prompt, shipped template) for `name`."""
        prompt = get_skill(name).prompt
        with open(os.path.join(ROOT, "templates", "commands", f"{name}.md"),
                  encoding="utf-8") as fh:
            template = fh.read()
        return prompt, template

    def test_develop_has_ask_before_deviation(self):
        for text in self._both("develop"):
            low = text.lower()
            self.assertIn("approved plan", low)
            self.assertIn("stop", low)
            self.assertIn("explicit human approval", low)
            self.assertIn("never silently deviate", low)
            self.assertIn("audit ledger", low)

    def test_test_skill_guards_the_acs(self):
        for text in self._both("test"):
            low = text.lower()
            self.assertIn("only the approved acceptance criteria", low)
            self.assertIn("amend the spec", low)
            self.assertIn("stop", low)

    def test_refine_does_not_broaden_post_approval(self):
        for text in self._both("refine"):
            low = text.lower()
            self.assertIn("approved set", low)
            self.assertIn("broaden", low)
            self.assertIn("explicit approval", low)

    def test_review_backstop_is_explicit(self):
        prompt = get_skill("review").prompt.lower()
        self.assertIn("approved plan", prompt)
        self.assertIn("unapproved divergence", prompt)
        self.assertIn("never a silent pass", prompt)

    def test_clause_is_single_source(self):
        # generated template == command_markdown(skill) — the clause can't drift
        for name in ("develop", "test", "review", "refine"):
            with open(os.path.join(ROOT, "templates", "commands", f"{name}.md"),
                      encoding="utf-8") as fh:
                self.assertEqual(fh.read(), command_markdown(get_skill(name)))


# ------------------------------------------------------- the backstop (caught before/after)

def _approved_spec():
    return Spec(title="login", criteria=[
        AcceptanceCriterion("AC-1", "log in"),
        AcceptanceCriterion("AC-2", "log out"),
    ])


class TestDeviationBackstop(unittest.TestCase):
    def test_dropping_an_ac_test_blocks_the_completeness_gate(self):
        # a silent deviation drops AC-2's test (changes the plan without amending the spec)
        spec = _approved_spec()
        res = run_completeness_gate(spec, [TestRef("t1", ["AC-1"])])  # AC-2 unmapped
        self.assertFalse(res.passed)
        self.assertIn("AC-2", res.unmapped_ids)

    def test_unapproved_extra_feature_flagged_by_compliance(self):
        # the build adds a feature traceable to no approved AC (scope creep)
        spec = _approved_spec()
        features = [TestRef("login_impl", ["AC-1"]),
                    TestRef("logout_impl", ["AC-2"]),
                    TestRef("password_reset_impl", ["AC-3"])]   # AC-3 not in the spec
        result = spec_compliance_review(spec, features)
        self.assertTrue(result.has_unspecified)
        self.assertIn("password_reset_impl", result.render())

    def test_amending_the_spec_keeps_it_provable(self):
        # the proper route: amend the spec so the new AC maps to a test -> gate passes
        spec = Spec(title="login", criteria=[
            AcceptanceCriterion("AC-1", "log in"),
            AcceptanceCriterion("AC-2", "log out"),
            AcceptanceCriterion("AC-3", "reset password"),   # amended in
        ])
        tests = [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"]), TestRef("t3", ["AC-3"])]
        self.assertTrue(run_completeness_gate(spec, tests).passed)


# ------------------------------------------------------- the deviation is audited

class TestDeviationAudited(unittest.TestCase):
    def _ledger(self, d):
        return AuditLedger(os.path.join(d, "ledger.jsonl"))

    def _req(self):
        return DeviationRequest(
            what="drop AC-2 (log out) — out of scope for this story",
            why="logout lives in a separate auth service we don't own here",
            options=["keep AC-2 and stub it", "amend the spec to remove AC-2"],
            target=ACCEPTANCE_CRITERIA, phase="develop")

    def test_request_and_approval_are_logged(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._ledger(d)
            gate = DeviationGate(ledger=led)
            outcome = gate.submit(self._req(), assume_yes=True)   # explicit approval
            self.assertTrue(outcome.approved)
            kinds = [e for e in led.entries() if e["kind"] == DEVIATION_KIND]
            decisions = [e["decision"] for e in kinds]
            self.assertIn(PROPOSED, decisions)     # the request was surfaced
            self.assertIn(APPROVED, decisions)     # the decision was recorded
            self.assertEqual(kinds[-1]["target"], ACCEPTANCE_CRITERIA)

    def test_declined_deviation_is_logged_and_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._ledger(d)
            gate = DeviationGate(ledger=led)
            outcome = gate.submit(self._req(), confirm=lambda _t: False)
            self.assertFalse(outcome.approved)
            self.assertTrue(outcome.aborted)
            decisions = [e["decision"] for e in led.entries()
                         if e["kind"] == DEVIATION_KIND]
            self.assertEqual(decisions, [PROPOSED, DECLINED])

    def test_no_ledger_is_safe(self):
        # the gate works without a ledger wired (just no audit trail)
        outcome = DeviationGate().submit(self._req(), assume_yes=True)
        self.assertTrue(outcome.approved)


if __name__ == "__main__":
    unittest.main()
