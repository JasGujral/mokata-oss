"""DK.S1 — the build/review-core domain skills: API & Security.

DK.S0 built the framework (the derived trigger chain, engage/amend wiring, memory+ledger, the
clean-room authoring standard). DK.S1 authors the first TWO domain skills on it — `api` and
`security` — clean-room from primary sources, wired to their EXISTING gate/instrument, and made
indistinguishable from a native skill:

  * api      — native to brainstorm + develop + review's Architecture axis; feeds blast-radius on
               contract changes (callers) → the contract decision is recorded to memory + ledger.
               Sources: the RFCs (9110/2119/8259), Hyrum's Law, semantic versioning, contract-first.
  * security — native to develop + review's Security axis; feeds secret-guard + WriteGate + the G-D
               untrusted-data posture, and its items are HARD rules that ride the EXISTING hard-rule
               enforcement path (govern/enforce.py) — not advisory. Sources: OWASP Top 10/ASVS/Cheat
               Sheets.

These tests are the DK.S1 validation block: each auto-engages on its trigger with the ⛭ banner;
an API contract change walks the blast-radius + records the decision; a security finding activates
the Security axis + rides the hard-rule enforce path as HARD; the SK.S4 citation lint passes on
both; the full skill anatomy is present; and NO new gate is added.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import domains as dk
from mokata import skill_lints as sl
from mokata.agent_skills import (SKILL_MARKER, install_domain_skills, installed_skill_names,
                                 parse_frontmatter, prune_orphan_skills)
from mokata.brainstorm_impact import ApproachImpact
from mokata.govern.enforce import EnforcementGate, PendingAction
from mokata.govern.ledger import AuditLedger
from mokata.memory import MemoryStore, SQLiteBackend
from mokata.memory.item import DECISION, GUARDRAIL, HARD, effective_enforcement
from mokata.progress import SKILL_GLYPH
from mokata.skill_contracts import GATES

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")

DOMAIN_SKILLS = ("api", "security")


def _skill_text(name):
    with open(os.path.join(_SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


# ============================================================ the skills ship + carry the anatomy
class TestBothDomainSkillsShip(unittest.TestCase):
    """api + security are the shipped domain-skill set, each a real file with the full anatomy."""

    def test_domain_skills_set_is_api_and_security(self):
        # DK.S1's pair still ships and LEADS the attachment-map order; later batches (DK.S2+)
        # append the rest, so assert membership + the leading order rather than the full set.
        for name in DOMAIN_SKILLS:
            self.assertIn(name, dk.DOMAIN_SKILLS)
        self.assertEqual(dk.shipped_domain_skills()[:2], ("api", "security"))  # attachment-map order

    def test_each_domain_skill_has_a_shipped_file_with_the_marker(self):
        for name in DOMAIN_SKILLS:
            path = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"missing shipped domain skill {name}")
            self.assertIn(SKILL_MARKER, _skill_text(name), "must carry the mokata skill marker")

    def test_each_carries_a_references_dir_progressive_disclosure(self):
        for name in DOMAIN_SKILLS:
            refs = os.path.join(_SKILLS_DIR, name, "references")
            self.assertTrue(os.path.isdir(refs), f"{name} must ship a references/ tree (SK.S2)")
            self.assertTrue(any(f.endswith(".md") for f in os.listdir(refs)))

    def test_full_anatomy_is_present_indistinguishable_from_native(self):
        # run the SK.S1/S2/S4 lints DIRECTLY on the domain texts: Contract + ⛭ activation +
        # ≥3 triggers + a negative + Rationalizations + Verification + (domain) citation — empty
        # findings means a domain skill is shaped exactly like a native pipeline skill.
        texts = {name: _skill_text(name) for name in DOMAIN_SKILLS}
        findings = sl.run_lints(texts=texts)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))


# ============================================================ 1. AUTO-ENGAGE on the trigger (⛭)
class TestAutoEngageOnTrigger(unittest.TestCase):
    """Each skill auto-engages when the DK.S0 classifier puts its id in the spec's domains — api
    on a routes/handler surface, security on an auth/input surface — with the ⛭ banner."""

    def test_api_engages_on_a_routes_handler_surface(self):
        surface = dk.DomainSurface(
            touched_files=["src/app/routes/orders.py", "src/app/handlers/create.py"],
            touched_symbols=["create_order"], roles=["route", "handler"])
        self.assertIn("api", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("Architecture", eng.axes)                 # review activates the API axis
        self.assertIn("api", eng.knowledge)                     # develop JIT-pulls the API skill

    def test_security_engages_on_an_auth_input_surface(self):
        surface = dk.DomainSurface(
            touched_files=["src/auth/login.py"], touched_symbols=["authenticate"],
            roles=["auth", "input-validation"])
        self.assertIn("security", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("Security", eng.axes)                     # review activates the Security axis
        self.assertIn("security", eng.knowledge)

    def test_the_activation_line_is_single_sourced_not_hand_typed(self):
        # exactly ONE ⛭ line per shipped file, and it EQUALS the single-source generator, so the
        # banner can never drift from the registry (SK.S1 discipline, extended to domain skills).
        for name in DOMAIN_SKILLS:
            lines = [ln.strip() for ln in _skill_text(name).splitlines()
                     if ln.strip().startswith(SKILL_GLYPH)]
            self.assertEqual(len(lines), 1, f"{name} must carry exactly one ⛭ line")
            self.assertEqual(lines[0], dk.domain_active_skill_line(name))

    def test_the_activation_line_announces_the_domains_governed_edge(self):
        # api announces blast-radius (an instrument); security announces the secret-guard hard
        # block (a backed gate, single-sourced from skill_contracts).
        self.assertIn("callers", dk.domain_headline_gate_line("api"))
        self.assertEqual(dk.domain_headline_gate_line("security"),
                         GATES["secret-guard"].one_line)


# ============================================================ 2. API — blast-radius + memory/ledger
class TestApiContractChangeWalksCallersAndRecords(unittest.TestCase):
    """An API contract change classifies as `api` from its blast-radius surface (callers), and the
    contract decision is RECORDED to typed memory + the audit ledger (human-gated)."""

    def test_a_contract_change_classifies_from_its_caller_blast_radius(self):
        # the change touches a route + the callers the blast-radius walked — derived, not guessed.
        impact = ApproachImpact(
            approach="freeze the orders response shape",
            targets=["create_order"],
            impacted_files=["src/routes/orders.py", "src/clients/orders_sdk.py"],
            impacted_symbols=["create_order", "OrdersClient.create"])
        self.assertIn("api", dk.classify_from_impact(impact, roles=["route", "api-contract"]))

    def test_the_contract_decision_is_recorded_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "api", "orders response contract",
                "POST /orders response shape is frozen — callers depend on it (Hyrum's Law)",
                store=store, ledger=ledger, about_code=["create_order"],
                confirm=lambda _p: True)                        # the human gate approves
            self.assertTrue(res.committed)
            # a typed API decision is on record (an api decision is a `decision`)
            stored = list(store.all_active())
            self.assertTrue(any(i.kind == DECISION for i in stored))
            # and it is audited under the `domain` ledger kind, tagged to the api domain
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "api")

    def test_the_api_skill_encodes_its_named_principles_and_cites_them(self):
        text = _skill_text("api").lower()
        self.assertIn("hyrum", text)                            # Hyrum's Law is encoded
        self.assertIn("contract-first", text)                   # contract-first is encoded
        self.assertIn("rfc-editor.org", text)                   # cited to the RFC primary source


# ============================================================ 3. SECURITY — rides the HARD path
class TestSecurityRidesTheHardRuleEnforcePath(unittest.TestCase):
    """A security constraint is a `guardrail` (born HARD) and rides the EXISTING hard-rule
    enforcement path — a violating action is a NON-overridable block, not an advisory flag. No
    gate is forked: it is govern/enforce.py's EnforcementGate, unchanged."""

    def test_a_security_guardrail_is_born_hard_not_advisory(self):
        item = dk.domain_memory_item("security", "no plaintext secrets",
                                     "credentials are hashed at rest, never logged")
        self.assertEqual(item.kind, GUARDRAIL)
        self.assertEqual(effective_enforcement(item), HARD)     # hard, not advisory

    def test_a_violating_change_is_a_non_overridable_block_on_the_existing_gate(self):
        rule = dk.domain_memory_item("security", "no plaintext secrets",
                                     "credentials are hashed at rest, never logged")
        action = PendingAction(kind="code", target="src/auth/store.py",
                               summary="log the raw token", violates=frozenset({rule.subject}))
        gate = EnforcementGate()                                # the EXISTING enforce gate
        outcome = gate.check(action, [rule],
                             override=lambda _v: True)          # even an explicit override…
        self.assertFalse(outcome.allowed, "a hard security rule must block the change")
        self.assertFalse(outcome.overridden, "a hard rule cannot be overridden at runtime")
        self.assertEqual(outcome.verdict.blocking.enforcement, HARD)

    def test_the_security_skill_activates_the_security_axis_in_review(self):
        eng = dk.engage_for_spec(["security"])
        self.assertEqual(list(eng.axes), ["Security"])

    def test_the_security_skill_encodes_owasp_as_hard_and_cites_it(self):
        text = _skill_text("security")
        low = text.lower()
        self.assertIn("owasp.org", low)                         # cited to the OWASP primary source
        self.assertIn("hard", low)                              # items are hard, not advisory
        self.assertIn("untrusted", low)                         # the G-D posture is carried


# ============================================================ 4. clean-room authoring passes SK.S4
class TestCleanRoomCitationLintPassesOnBoth(unittest.TestCase):
    """The SK.S4 (DK.S0) citation lint passes on both: each cites a primary-source URL for its
    external claims and carries the UNVERIFIED discipline — own words, no repo text."""

    def test_citation_lint_is_clean_on_both_domain_skills(self):
        for name in DOMAIN_SKILLS:
            self.assertEqual(sl.domain_citation_findings(name, _skill_text(name)), [],
                             f"{name} must pass the clean-room citation lint")

    def test_each_carries_the_unverified_discipline(self):
        for name in DOMAIN_SKILLS:
            self.assertIn("unverified", _skill_text(name).lower(),
                          f"{name} must flag unverified claims (clean-room standard)")

    def test_own_words_mokata_framing_not_a_lifted_advisory(self):
        # own-words check: the content is framed in mokata's voice (attached to the flow, the
        # gates it feeds) — not a lifted, advice-only workflow. Each references the mokata
        # mechanism its domain feeds, which a copied third-party skill would not.
        api = _skill_text("api").lower()
        self.assertIn("blast-radius", api)
        self.assertIn("memory", api)
        sec = _skill_text("security").lower()
        self.assertIn("secret-guard", sec)
        self.assertIn("hard-rule", sec)

    def test_a_domain_skill_stripped_of_citations_would_fail_the_lint(self):
        # proves the lint is load-bearing, not vacuous: strip the URLs + UNVERIFIED and it flags.
        stripped = "---\nname: api\n---\nAPIs are contracts. Version breaking changes.\n"
        self.assertTrue(sl.domain_citation_findings("api", stripped))


# ============================================================ 5. packaging — shipped, never pruned
class TestDomainSkillsShipAndSurvivePrune(unittest.TestCase):
    """The domain skills are in the keep-set (curated + domain), so a setup/regen delivers them
    and NEVER prunes them as orphans — while a genuinely-dropped mokata skill still prunes."""

    def test_installed_keep_set_includes_the_domain_skills(self):
        keep = installed_skill_names()
        self.assertIn("api", keep)
        self.assertIn("security", keep)

    def test_install_copies_skill_and_references_to_a_target(self):
        with tempfile.TemporaryDirectory() as d:
            written = install_domain_skills(_SKILLS_DIR, os.path.join(d, "skills"))
            self.assertTrue(any(p.name == "SKILL.md" and p.parent.name == "api"
                                for p in written))
            self.assertTrue(any("references" in p.parts for p in written))
            self.assertTrue(os.path.isfile(os.path.join(d, "skills", "security", "SKILL.md")))

    def test_prune_keeps_domain_skills_but_drops_a_real_orphan(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            sk = Path(d) / "skills"
            install_domain_skills(_SKILLS_DIR, sk)             # api + security land
            (sk / "dropped").mkdir(parents=True)               # a genuinely removed mokata skill
            (sk / "dropped" / "SKILL.md").write_text(SKILL_MARKER + "\nx", encoding="utf-8")
            removed = prune_orphan_skills(sk, installed_skill_names())
            self.assertEqual([p.parent.name for p in removed], ["dropped"])
            self.assertTrue((sk / "api" / "SKILL.md").is_file(), "api must survive the sync")
            self.assertTrue((sk / "security" / "SKILL.md").is_file())


# ============================================================ 6. NO new gate added
class TestNoNewGate(unittest.TestCase):
    """DK.S1 attaches to EXISTING gates/instruments — the backed enforcement set is unchanged."""

    def test_backed_gate_set_is_unchanged(self):
        self.assertEqual(
            sorted(g for g, r in GATES.items() if r.backed),
            ["completeness", "deviation", "hard-rule", "no-code-without-failing-test",
             "secret-guard", "ship-readiness", "spec-persisted", "write-gate"])

    def test_frontmatter_names_match_the_domain_ids(self):
        for name in DOMAIN_SKILLS:
            fm = parse_frontmatter(_skill_text(name))
            self.assertEqual(fm.get("name"), name)
            self.assertTrue(fm.get("description", "").startswith("mokata ·"))


if __name__ == "__main__":
    unittest.main()
