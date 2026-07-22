"""DK.S0 — the domain-knowledge integration framework + the DERIVED trigger chain.

The behavioral guards for the framework the domain skills (DK.S1–S4) will populate. The crux is
a trigger chain that is DERIVED from the graph (approach × structure), never keyword-guessed:

  1. PRIMARY — brainstorm classifies the domains-in-play from the SURFACE an approach touches
     (its graph-derived files/symbols/roles), NOT from the free-text topic, and PERSISTS that
     domain set into emitted_spec.json as a FIRST-CLASS constraint beside the ACs + approach.
  2. develop/review engage EXACTLY the spec's domains — no more (no extra axis fires), no less
     (none silently missed).
  3. LIVE refinement — a domain reached only at develop-time (a graph query lands somewhere the
     spec didn't capture) is a NON-TRIVIAL discovery: it routes through SK.S3's ask→amend-spec
     loop (human-gated), never a silent apply and never a silent miss.
  4. the shared framework the domain skills inherit — the gate/instrument each domain feeds, the
     typed-memory + ledger wiring (human-gated + audited), the clean-room primary-source
     authoring standard (own words + cited URLs, flag UNVERIFIED, never the repo's text), and the
     shared anatomy (Contract + anti-rationalization + verification + references/).

Reuses SK.S1 (Contract/gates), SK.S2 (review axes + anatomy), SK.S3 (ask→amend loop) single
sources and adds NO new gate: the emitted_spec.json domain set is a constraint the existing gates
already carry. No store schema change (emitted_spec.json gains a `domains` field only).
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import domains as dk
from mokata.brainstorm import Approach, BrainstormSession
from mokata.brainstorm_impact import ApproachImpact, DesignFitVerdict
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_gate import SPEC_STATE_KEY, load_emitted_spec
from mokata.govern.deviation import DeviationRequest
from mokata.govern.ledger import AuditLedger
from mokata.memory import MemoryItem, MemoryStore, SQLiteBackend
from mokata.memory.item import MEMORY_KINDS
from mokata.skill_contracts import GATES
from mokata.state import StateStore


# ============================================================ the registry (the framework)
class TestDomainRegistry(unittest.TestCase):
    """The 10 domain areas of the attachment map, each attached to a phase + a governed edge."""

    def test_registry_covers_the_attachment_map_domains(self):
        for did in ("api", "security", "performance", "frontend-a11y", "browser-testing",
                    "ci-cd", "git", "deprecation", "docs-adr", "shipping"):
            self.assertIn(did, dk.DOMAIN_REGISTRY, f"domain {did!r} missing from the registry")

    def test_every_domain_attaches_to_a_phase_and_a_governed_edge(self):
        for did, dom in dk.DOMAIN_REGISTRY.items():
            with self.subTest(domain=did):
                self.assertTrue(dom.phases, f"{did} must name ≥1 native pipeline phase")
                self.assertTrue(dom.gate, f"{did} must name the gate/instrument it feeds")
                self.assertIn(dom.memory_kind, MEMORY_KINDS,
                              f"{did} must type its decisions with a real memory kind")

    def test_only_api_security_performance_activate_a_review_axis(self):
        # SK.S2's activatable instrument-axes are Security / Architecture / Performance.
        by_axis = {did: d.review_axis for did, d in dk.DOMAIN_REGISTRY.items() if d.review_axis}
        self.assertEqual(by_axis,
                         {"api": "Architecture", "security": "Security",
                          "performance": "Performance"})

    def test_the_framework_adds_no_new_gate(self):
        # every gate/instrument a domain cites is an EXISTING SK.S1 gate id or a named
        # SK.S2 instrument — never a newly-invented gate.
        instruments = {"blast-radius", "measure-first", "a11y-checklist", "test-gate",
                       "scorecard", "release-gate"}
        for did, dom in dk.DOMAIN_REGISTRY.items():
            with self.subTest(domain=did):
                self.assertTrue(dom.gate in GATES or dom.gate in instruments,
                                f"{did} feeds {dom.gate!r} — not an existing gate/instrument")


# ============================================================ 1. PRIMARY trigger — classify
class TestClassificationIsDerivedNotKeywordGuessed(unittest.TestCase):
    """The domain set is DERIVED from the graph-derived surface (files/symbols/roles), NEVER
    from the free-text topic — so a domain is never guessed from a prompt keyword."""

    def test_classify_takes_no_topic_text_only_the_surface(self):
        # structural guarantee: the classifier's input carries no free-text topic/summary field,
        # so it CANNOT keyword-match the prompt.
        surface = dk.DomainSurface(touched_files=[], touched_symbols=[], roles=[])
        for field in ("topic", "summary", "prompt", "text"):
            self.assertFalse(hasattr(surface, field),
                             f"the surface must not carry a {field!r} — classification is "
                             "derived from structure, not keywords")

    def test_roles_derive_the_domains(self):
        surface = dk.DomainSurface(
            touched_files=["src/app/handlers/orders.py"], touched_symbols=["create_order"],
            roles=["route", "auth"])
        got = dk.classify_domains(surface)
        self.assertIn("api", got)
        self.assertIn("security", got)

    def test_same_topic_different_surface_yields_different_domains(self):
        # PROOF it's the surface, not the words: identical (empty) role hint, different files.
        ui = dk.DomainSurface(touched_files=["web/components/Login.tsx"],
                              touched_symbols=["LoginForm"], roles=[])
        api = dk.DomainSurface(touched_files=["src/routes/session.py"],
                               touched_symbols=["login_route"], roles=[])
        self.assertIn("frontend-a11y", dk.classify_domains(ui))
        self.assertNotIn("frontend-a11y", dk.classify_domains(api))
        self.assertIn("api", dk.classify_domains(api))

    def test_no_keyword_leak_ui_only_surface_is_not_security(self):
        # a surface that touches ONLY UI files must NOT pick up security just because a role
        # word could appear elsewhere — only the touched structure counts.
        ui = dk.DomainSurface(touched_files=["web/views/profile.tsx"],
                              touched_symbols=["Profile"], roles=["ui-component"])
        self.assertNotIn("security", dk.classify_domains(ui))

    def test_path_floor_classifies_when_no_role_tags_supplied(self):
        # degrade-clean: with no model-supplied roles the path-marker FLOOR still scores (mirrors
        # blast-radius's grep floor) so a domain is never missed for lack of a role hint.
        surface = dk.DomainSurface(
            touched_files=["src/auth/login.py", "src/routes/orders.py"],
            touched_symbols=[], roles=[])
        got = dk.classify_domains(surface)
        self.assertIn("security", got)
        self.assertIn("api", got)

    def test_classify_from_impact_reads_the_blast_radius_surface(self):
        impact = ApproachImpact(
            approach="a", targets=["migrate_users"],
            impacted_files=["src/migrations/0007_drop_col.py"], impacted_symbols=["migrate_users"])
        got = dk.classify_from_impact(impact, roles=["migration"])
        self.assertIn("deprecation", got)


# ============================================================ 1b. PERSIST into the spec
class TestDomainsPersistIntoEmittedSpec(unittest.TestCase):
    """The classified domain set is written into emitted_spec.json as a FIRST-CLASS constraint,
    beside the ACs + approach — human-approved + legible. No store SCHEMA change."""

    def test_spec_carries_a_domains_field(self):
        spec = Spec(title="t", criteria=[AcceptanceCriterion("AC1", "x")],
                    approach="cache-aside", domains=["api", "security"])
        self.assertEqual(spec.domains, ["api", "security"])

    def test_domains_round_trip_through_to_from_dict(self):
        spec = Spec(title="t", criteria=[], approach=None, domains=["performance"])
        self.assertEqual(spec.to_dict()["domains"], ["performance"])
        self.assertEqual(Spec.from_dict(spec.to_dict()).domains, ["performance"])

    def test_legacy_spec_without_domains_reads_as_empty(self):
        # a pre-DK.S0 emitted_spec.json (no `domains` key) loads clean as [] — never crashes.
        legacy = {"title": "t", "criteria": [], "approach": None}
        self.assertEqual(Spec.from_dict(legacy).domains, [])

    def test_domains_land_in_persisted_emitted_spec_json(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            spec = Spec(title="orders", criteria=[AcceptanceCriterion("AC1", "x")],
                        approach="direct", domains=["api", "security"])
            store.write(SPEC_STATE_KEY, spec.to_dict())
            reloaded = load_emitted_spec(store)
            self.assertEqual(reloaded.domains, ["api", "security"])


class TestBrainstormClassifiesAndCarriesDomains(unittest.TestCase):
    """End-to-end PRIMARY trigger: a brainstorm approach → classify from its graph surface →
    the approved hand-off carries the domain set → it lands in the emitted spec."""

    def _approved_session(self):
        s = BrainstormSession("add an orders endpoint")
        s.propose_approaches([
            Approach("direct", "handler writes straight through",
                     pros=["simple"], cons=["coupled"],
                     targets=["src/routes/orders.py", "src/auth/require_login.py"]),
            Approach("queued", "enqueue then process", pros=["decoupled"], cons=["latency"],
                     targets=["src/routes/orders.py"]),
        ])
        s.assess_impacts()
        s.record_design_fit("direct", DesignFitVerdict("direct", "fits"))
        s.approve("jas", "direct")
        return s

    def test_session_classifies_the_chosen_approachs_domains(self):
        s = self._approved_session()
        got = dk.classify_session_domains(s, roles=["route", "auth"])
        self.assertIn("api", got)
        self.assertIn("security", got)

    def test_handoff_carries_the_domain_set(self):
        s = self._approved_session()
        domains = dk.classify_session_domains(s, roles=["route", "auth"])
        h = dk.handoff_with_domains(s, domains)
        self.assertEqual(sorted(h.domains), ["api", "security"])
        # and it round-trips (persisted approved approach keeps its domains)
        from mokata.brainstorm import Handoff
        self.assertEqual(sorted(Handoff.from_dict(h.to_dict()).domains), ["api", "security"])


# ============================================================ 2. engage EXACTLY the domains
class TestDevelopReviewEngageExactly(unittest.TestCase):
    """develop JIT-pulls the spec's domains for the symbols in play; review activates the
    matching axes — EXACTLY those domains, no more, no less."""

    def test_review_activates_exactly_the_spec_domain_axes(self):
        eng = dk.engage_for_spec(["api", "security"])
        self.assertEqual(sorted(eng.axes), ["Architecture", "Security"])
        self.assertNotIn("Performance", eng.axes)   # not in the spec → never fires

    def test_develop_pulls_exactly_the_spec_domain_knowledge(self):
        eng = dk.engage_for_spec(["api", "security"])
        self.assertEqual(sorted(eng.knowledge), ["api", "security"])

    def test_a_domain_not_in_the_spec_is_never_engaged(self):
        eng = dk.engage_for_spec(["performance"])
        self.assertEqual(sorted(eng.axes), ["Performance"])
        self.assertNotIn("security", eng.knowledge)

    def test_empty_spec_domains_engage_nothing(self):
        eng = dk.engage_for_spec([])
        self.assertEqual(list(eng.axes), [])
        self.assertEqual(list(eng.knowledge), [])

    def test_unknown_domain_id_is_rejected_not_silently_dropped(self):
        with self.assertRaises(KeyError):
            dk.engage_for_spec(["not-a-domain"])


# ============================================================ 3. LIVE refinement — amend on discovery
class TestLateDiscoveryRoutesThroughAmendLoop(unittest.TestCase):
    """A domain reached only at develop-time (not captured in the spec) is a NON-TRIVIAL
    discovery → routes through SK.S3's ask→amend-spec loop (human-gated), NOT a silent apply."""

    def test_reached_domain_absent_from_spec_is_flagged(self):
        late = dk.late_discoveries(spec_domains=["api"], reached_domains=["api", "security"])
        self.assertEqual(late, ["security"])

    def test_all_reached_in_spec_means_no_amend_needed(self):
        self.assertEqual(
            dk.late_discoveries(spec_domains=["api", "security"],
                                reached_domains=["api"]), [])

    def test_a_late_domain_produces_an_amend_spec_request_not_a_silent_apply(self):
        req = dk.domain_amend_request("security", phase="develop")
        self.assertIsInstance(req, DeviationRequest)
        # it names the domain, states WHY it must not silently apply, and offers the amend option
        self.assertIn("security", req.what.lower())
        self.assertTrue(req.why)
        self.assertTrue(any("amend" in o.lower() for o in req.options),
                        "the amend-the-spec option must be offered (human-gated)")

    def test_a_domain_is_never_silently_applied_or_silently_missed(self):
        # the guard names both failure modes it exists to prevent
        low = dk.DOMAIN_TRIGGER_DISCIPLINE.lower()
        self.assertIn("never silently applied", low)
        self.assertIn("never silently missed", low)


# ============================================================ 4a. memory + ledger wiring
class TestDomainDecisionPersistsToMemoryAndLedger(unittest.TestCase):
    """A domain decision → a TYPED memory entry (human-gated) + an audited ledger record. The
    write routes through the SAME universal WriteGate; it adds no new gate."""

    def _store_and_ledger(self, d):
        store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
        ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
        return store, ledger

    def test_domain_memory_item_is_typed_by_the_domain(self):
        item = dk.domain_memory_item("api", "orders contract", "POST /orders is stable")
        self.assertIsInstance(item, MemoryItem)
        self.assertEqual(item.kind, dk.DOMAIN_REGISTRY["api"].memory_kind)

    def test_record_domain_decision_writes_typed_memory_and_audits(self):
        with tempfile.TemporaryDirectory() as d:
            store, ledger = self._store_and_ledger(d)
            res = dk.record_domain_decision(
                "api", "orders contract", "POST /orders response shape is frozen (Hyrum's Law)",
                store=store, ledger=ledger, about_code=["create_order"],
                confirm=lambda _p: True)      # the human gate: approve
            self.assertTrue(res.committed, "the approved domain decision must be stored")
            # a typed memory item is now on record
            stored = list(store.all_active())
            self.assertTrue(any(i.kind == dk.DOMAIN_REGISTRY["api"].memory_kind for i in stored))
            # and the decision is audited under the `domain` ledger kind
            kinds = [e.get("kind") for e in ledger.entries()]
            self.assertIn("domain", kinds)
            dom_entry = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom_entry.get("domain"), "api")

    def test_declined_at_the_gate_stores_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store, ledger = self._store_and_ledger(d)
            res = dk.record_domain_decision(
                "security", "no plaintext secrets", "hash tokens at rest",
                store=store, ledger=ledger, confirm=lambda _p: False)   # human declines
            self.assertFalse(res.committed)
            self.assertEqual(list(store.all_active()), [])


# ============================================================ 4b. clean-room authoring standard
class TestCleanRoomAuthoringStandard(unittest.TestCase):
    """The clean-room primary-source authoring standard is EXPRESSED so the SK.S4 lint enforces
    it on DK.S1–S4 content: own words + cited URLs, flag UNVERIFIED, never the repo's text."""

    def test_authoring_standard_states_the_clean_room_rules(self):
        low = dk.DOMAIN_AUTHORING_STANDARD.lower()
        self.assertIn("primary source", low)
        self.assertIn("cite", low)
        self.assertIn("unverified", low)
        self.assertIn("own words", low)

    def test_shared_anatomy_requires_the_full_skill_shape(self):
        req = " ".join(dk.DOMAIN_ANATOMY_REQUIREMENTS).lower()
        for part in ("contract", "rationaliz", "verification", "references/"):
            self.assertIn(part, req)

    def test_domain_skills_grow_as_content_lands(self):
        # DK.S0 shipped the framework only (DOMAIN_SKILLS empty); DK.S1 landed the build/review
        # core — `api` + `security`; DK.S2 added the verify/UI batch — `performance`,
        # `frontend-a11y`, `browser-testing`; DK.S3 adds the pipeline/landing batch — `ci-cd` +
        # `git`. Later batches (DK.S4) append the rest. Assert the SHIPPED set contains each
        # landed batch and that every shipped id is a real registry domain (not a hardcoded full
        # set, so the next batch extends it for free).
        for did in ("api", "security", "performance", "frontend-a11y", "browser-testing",
                    "ci-cd", "git"):
            self.assertIn(did, dk.DOMAIN_SKILLS)
        for did in dk.DOMAIN_SKILLS:
            self.assertIn(did, dk.DOMAIN_REGISTRY)

    def test_citation_lint_fires_on_a_domain_skill_missing_citations(self):
        from mokata import skill_lints as sl
        # a synthetic domain skill with no cited URL + no UNVERIFIED discipline → flagged
        text = ("---\nname: api\n---\nAPIs should be versioned. Contracts are forever.\n")
        findings = sl.domain_citation_findings("api", text)
        self.assertTrue(findings, "a domain skill without a cited primary source must be flagged")

    def test_citation_lint_passes_a_properly_cited_domain_skill(self):
        from mokata import skill_lints as sl
        text = ("---\nname: api\n---\nContract-first design (own words), grounded in the "
                "primary source https://www.rfc-editor.org/rfc/rfc9110 . Anything unread is "
                "flagged UNVERIFIED.\n")
        self.assertEqual(sl.domain_citation_findings("api", text), [])

    def test_citation_lint_is_a_noop_on_non_domain_skills(self):
        from mokata import skill_lints as sl
        # a normal pipeline skill (not a domain skill) is untouched by the citation lint
        self.assertEqual(sl.domain_citation_findings("develop", "no urls here at all"), [])


# ============================================================ gates unchanged
class TestNoNewGate(unittest.TestCase):
    """DK.S0 adds NO gate — the backed enforcement set is exactly the SK.S1 eight."""

    def test_backed_gate_set_is_unchanged(self):
        # PH-GATE.S0 (0.0.14) backs `approach-approval` — the SI.1 gate hook now enforces the
        # brainstorm boundary (doc 76 FU-1), so it joins the SK.S1 eight. DK.S0 itself still adds
        # no gate; this set is the post-PH-GATE.S0 backed enforcement set.
        self.assertEqual(
            sorted(g for g, r in GATES.items() if r.backed),
            ["approach-approval", "completeness", "deviation", "hard-rule",
             "no-code-without-failing-test", "secret-guard", "ship-readiness", "spec-persisted",
             "write-gate"])


if __name__ == "__main__":
    unittest.main()
