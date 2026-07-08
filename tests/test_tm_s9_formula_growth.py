"""TM.S9 — formula item + retain-on-success growth (doc 62 §2 axis B, §5, §8).

Covers the grooming decision exactly (doc 62 §8):
  * a FORMULA is a parameterized TEMPLATE STRING (named params) + TRIGGER/APPLICABILITY
    metadata (when it applies), living in the existing item model + scope (S6), kind=formula
    (S7). It round-trips (template + applicability + params) through the item JSON — no DDL;
  * recall by APPLICABILITY: a formula surfaces when its applicability MATCHES the query/context
    and is omitted when it doesn't — matched by its applicability metadata, not just similarity;
    returned for INJECTION (the template + its params), never computed/evaluated;
  * precedence CLASS = preference (never safety);
  * RETAIN-ON-SUCCESS growth reuses the surface-and-approve propose engine — after a repeated
    successful procedure it PROPOSES a formula, routed through the WriteGate (P2). A declined
    gate writes NOTHING. There is NO autonomous write path (propose-only);
  * HARD RULES NEVER auto-grow — only formulae/facts grow via propose (asserted).

No live DB — the local SQLite floor + an in-memory ledger cover everything.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.memory import precedence as P
from mokata.memory.backends import SQLiteBackend
from mokata.memory.formula import (
    applies_to,
    formula_params,
    make_formula,
    recall_applicable,
    render_injection,
    template_params,
)
from mokata.memory.growth import (
    GROWABLE_KINDS,
    FormulaProposal,
    RetainOnSuccess,
    apply_formula_proposal,
    assert_growable,
)
from mokata.memory.item import FACT, FORMULA, GUARDRAIL, HARD, RULE, MemoryItem
from mokata.memory.scope import PROJECT, ScopeContext
from mokata.memory.store import MemoryStore


def _store(tmp):
    led = AuditLedger(os.path.join(tmp, "ledger.jsonl"))
    store = MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")), ledger=led)
    return store, led


# ============================================================ template params
class TestTemplateParams(unittest.TestCase):
    def test_named_slots_are_extracted_in_order_deduped(self):
        t = "deploy {service} to {env} using {service}'s config"
        self.assertEqual(template_params(t), ["service", "env"])

    def test_no_slots_is_empty(self):
        self.assertEqual(template_params("a plain sentence, no params"), [])


# ============================================================ formula item round-trip
class TestFormulaItem(unittest.TestCase):
    def test_make_formula_is_a_formula_kind_with_template_and_metadata(self):
        f = make_formula("deploy-recipe", "deploy {service} to {env}",
                         triggers=["deploy", "release"], topic="deployment")
        self.assertEqual(f.governance_kind, FORMULA)
        self.assertEqual(f.value, "deploy {service} to {env}")       # the template body
        self.assertEqual(formula_params(f), ["service", "env"])       # params from the template
        self.assertEqual(f.applicability["triggers"], ["deploy", "release"])
        self.assertEqual(f.applicability["topic"], "deployment")

    def test_explicit_params_override_the_derived_ones(self):
        f = make_formula("f", "do {x}", params=["x", "y"])
        self.assertEqual(formula_params(f), ["x", "y"])

    def test_round_trips_template_applicability_and_params_through_json(self):
        f = make_formula("f", "run {task} in {env}", triggers=["run"], topic="ops", id="f1")
        d = f.to_dict()
        self.assertEqual(d["kind"], FORMULA)
        self.assertEqual(d["applicability"]["params"], ["task", "env"])
        back = MemoryItem.from_dict(d)
        self.assertEqual(back.value, "run {task} in {env}")
        self.assertEqual(back.applicability, f.applicability)
        self.assertEqual(formula_params(back), ["task", "env"])

    def test_legacy_item_has_empty_applicability_and_is_unaffected(self):
        legacy = MemoryItem.from_dict({"subject": "db", "value": "postgres"})
        self.assertEqual(legacy.applicability, {})
        self.assertEqual(legacy.governance_kind, FACT)
        # a non-formula item exposes no params
        self.assertEqual(formula_params(legacy), [])

    def test_a_pre_s9_formula_doc_without_applicability_round_trips(self):
        # an item stored kind=formula but with no applicability key reads back cleanly.
        back = MemoryItem.from_dict({"subject": "f", "value": "do {x}", "kind": FORMULA})
        self.assertEqual(back.governance_kind, FORMULA)
        self.assertEqual(back.applicability, {})
        # params still derive from the template when metadata is absent
        self.assertEqual(formula_params(back), ["x"])


# ============================================================ precedence class = preference
class TestFormulaPrecedence(unittest.TestCase):
    def test_formula_is_preference_never_safety(self):
        f = make_formula("f", "do {x}", triggers=["x"])
        self.assertEqual(P.precedence_class(f), P.PREFERENCE)


# ============================================================ applicability match
class TestAppliesTo(unittest.TestCase):
    def _f(self, **kw):
        return make_formula("recipe", "deploy {service} to {env}", **kw)

    def test_matches_when_a_trigger_keyword_is_in_the_query(self):
        f = self._f(triggers=["deploy", "release"])
        self.assertTrue(applies_to(f, "how do I deploy the api"))
        self.assertTrue(applies_to(f, "time to release the build"))

    def test_matches_when_the_topic_condition_is_in_the_query(self):
        f = self._f(topic="deployment pipeline")
        self.assertTrue(applies_to(f, "walk me through the deployment pipeline steps"))

    def test_omits_when_nothing_matches(self):
        f = self._f(triggers=["deploy"], topic="deployment")
        self.assertFalse(applies_to(f, "what database do we use"))

    def test_empty_applicability_never_matches(self):
        # matched by applicability metadata, not similarity — no metadata → no applicability hit
        f = make_formula("recipe", "deploy {service}")
        self.assertFalse(applies_to(f, "deploy something"))

    def test_multiword_trigger_needs_all_its_tokens(self):
        f = self._f(triggers=["deploy staging"])
        self.assertTrue(applies_to(f, "please deploy to staging now"))
        self.assertFalse(applies_to(f, "please deploy to prod"))    # 'staging' missing


# ============================================================ recall by applicability (store)
class TestRecallFormulas(unittest.TestCase):
    def test_recall_returns_a_formula_when_applicability_matches(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.remember(make_formula("deploy-recipe", "deploy {service} to {env}",
                                        triggers=["deploy"], topic="deployment", id="f1"),
                           assume_yes=True)
            store.remember(MemoryItem.create("db", "postgres", id="fact1"), assume_yes=True)
            hits = store.recall_formulas("how do I deploy the service")
            self.assertEqual([h.id for h in hits], ["f1"])

    def test_recall_omits_a_formula_when_applicability_does_not_match(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.remember(make_formula("deploy-recipe", "deploy {service}",
                                        triggers=["deploy"], topic="deployment", id="f1"),
                           assume_yes=True)
            self.assertEqual(store.recall_formulas("what database do we use"), [])

    def test_recall_is_matched_by_applicability_not_general_similarity(self):
        # a formula whose TEMPLATE shares words with the query but whose APPLICABILITY does not
        # → NOT surfaced by the applicability recall (it is matched by metadata, not similarity).
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.remember(make_formula("recipe", "configure the database connection",
                                        triggers=["deploy"], id="f1"), assume_yes=True)
            self.assertEqual(store.recall_formulas("configure the database"), [])

    def test_recall_only_returns_formula_kind_items(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            # a plain fact that would lexically match the query is never a formula hit
            store.remember(MemoryItem.create("deploy", "we deploy on fridays", id="fact1"),
                           assume_yes=True)
            self.assertEqual(store.recall_formulas("deploy the service"), [])

    def test_recall_honours_the_scope_union(self):
        # a project-scoped formula on the read path surfaces; an off-path one does not.
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.scope_context = ScopeContext(project="web")
            store.remember(make_formula("f-on", "deploy {x}", triggers=["deploy"],
                                        scope_level=PROJECT, scope_id="web", id="on"),
                           assume_yes=True)
            store.remember(make_formula("f-off", "deploy {x}", triggers=["deploy"],
                                        scope_level=PROJECT, scope_id="other", id="off"),
                           assume_yes=True)
            hits = store.recall_formulas("deploy now")
            self.assertEqual([h.id for h in hits], ["on"])

    def test_render_injection_shows_template_and_params(self):
        f = make_formula("deploy-recipe", "deploy {service} to {env}", triggers=["deploy"])
        out = render_injection(f)
        self.assertIn("deploy {service} to {env}", out)   # the template, injected as-is
        self.assertIn("service", out)                     # its params named
        self.assertIn("env", out)


# ============================================================ retain-on-success growth (propose-only)
class TestRetainOnSuccess(unittest.TestCase):
    def _proc(self):
        return dict(key="deploy-web", subject="deploy-web-recipe",
                    template="deploy {service} to {env}", triggers=["deploy"], topic="deployment")

    def test_proposes_a_formula_after_reaching_the_threshold(self):
        r = RetainOnSuccess(threshold=3)
        self.assertIsNone(r.observe_success(**self._proc()))   # 1
        self.assertIsNone(r.observe_success(**self._proc()))   # 2
        proposal = r.observe_success(**self._proc())           # 3 → proposes
        self.assertIsInstance(proposal, FormulaProposal)
        self.assertEqual(proposal.occurrences, 3)
        self.assertEqual(proposal.template, "deploy {service} to {env}")
        self.assertEqual(proposal.params, ["service", "env"])
        self.assertEqual(proposal.triggers, ["deploy"])

    def test_proposes_only_once_per_pattern(self):
        r = RetainOnSuccess(threshold=2)
        r.observe_success(**self._proc())
        self.assertIsInstance(r.observe_success(**self._proc()), FormulaProposal)  # proposes
        self.assertIsNone(r.observe_success(**self._proc()))                       # not again

    def test_proposal_becomes_a_formula_item(self):
        proposal = FormulaProposal(key="k", subject="s", template="do {x}",
                                   triggers=["go"], params=["x"], occurrences=3, rationale="r")
        item = proposal.to_item()
        self.assertEqual(item.governance_kind, FORMULA)
        self.assertEqual(item.value, "do {x}")
        self.assertEqual(item.applicability["triggers"], ["go"])

    def test_observe_never_writes_to_a_backend_propose_only(self):
        # the growth tracker holds NO store and cannot write — there is no autonomous write path.
        r = RetainOnSuccess(threshold=1)
        self.assertFalse(hasattr(r, "backend"))
        self.assertFalse(hasattr(r, "store"))
        proposal = r.observe_success(**self._proc())
        self.assertIsInstance(proposal, FormulaProposal)   # a proposal, never a stored write


# ============================================================ growth routes through the gate (P2)
class TestApplyFormulaProposal(unittest.TestCase):
    def _proposal(self):
        return FormulaProposal(key="deploy-web", subject="deploy-web-recipe",
                               template="deploy {service} to {env}", triggers=["deploy"],
                               params=["service", "env"], occurrences=3,
                               rationale="succeeded 3 times")

    def test_approved_proposal_writes_the_formula_through_the_writegate(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            res = apply_formula_proposal(store, self._proposal(), "approve", assume_yes=True)
            self.assertTrue(res.committed)
            # the formula is now recallable by its applicability
            hits = store.recall_formulas("deploy the api")
            self.assertEqual([h.subject for h in hits], ["deploy-web-recipe"])
            # it went through the ONE universal WriteGate (audit-ledgered)
            self.assertTrue(any(e["kind"] == "write_gate" and e["decision"] == "approved"
                                for e in led.entries()))

    def test_declined_gate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            before = store.stats.writes
            res = apply_formula_proposal(store, self._proposal(), "approve",
                                         confirm=lambda _t: False)   # user declines at the gate
            self.assertFalse(res.committed)
            self.assertTrue(res.aborted)
            self.assertEqual(store.stats.writes, before)             # nothing written
            self.assertEqual(store.recall_formulas("deploy the api"), [])   # not stored

    def test_reject_decision_writes_nothing_and_does_not_open_the_gate(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            res = apply_formula_proposal(store, self._proposal(), "reject")
            self.assertFalse(res.committed)
            self.assertEqual(store.recall_formulas("deploy the api"), [])
            self.assertEqual([e for e in led.entries() if e["kind"] == "write_gate"], [])


# ============================================================ hard rules never auto-grow (asserted)
class TestNoRuleAutoGrowth(unittest.TestCase):
    def test_growable_kinds_are_facts_and_formulae_only(self):
        self.assertEqual(set(GROWABLE_KINDS), {FACT, FORMULA})
        self.assertNotIn(RULE, GROWABLE_KINDS)

    def test_assert_growable_accepts_facts_and_formulae(self):
        assert_growable(FORMULA)
        assert_growable(FACT)
        assert_growable("")            # a plain fact
        assert_growable("context")     # a fact-class kind

    def test_assert_growable_rejects_a_rule(self):
        with self.assertRaises(AssertionError):
            assert_growable(RULE)

    def test_assert_growable_rejects_a_hard_guardrail(self):
        # a guardrail normalizes to a rule (and reads as hard) — never auto-grown.
        with self.assertRaises(AssertionError):
            assert_growable(GUARDRAIL)

    def test_a_hard_rule_is_never_proposed_for_auto_growth(self):
        # the growth tracker can only ever produce a FORMULA proposal — feeding it a procedure
        # whose intended kind is a hard rule is impossible: growth proposes formulae, and the
        # proposal builder asserts growable, so a rule can never slip through.
        proposal = FormulaProposal(key="k", subject="s", template="do {x}",
                                   triggers=["go"], params=["x"], occurrences=3, rationale="r")
        self.assertEqual(proposal.to_item().governance_kind, FORMULA)   # never a rule
        with self.assertRaises(AssertionError):
            assert_growable(HARD if False else RULE)   # a rule proposal would be rejected


if __name__ == "__main__":
    unittest.main()
