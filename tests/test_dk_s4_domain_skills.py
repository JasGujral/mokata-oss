"""DK.S4 — the ship/govern domain skills: Deprecation + Docs/ADR + Shipping (the LAST batch).

DK.S1 authored the build/review core (`api`, `security`); DK.S2 the verify/UI batch (`performance`,
`frontend-a11y`, `browser-testing`); DK.S3 the pipeline/landing batch (`ci-cd`, `git`). DK.S4
authors the final THREE domain skills on the SAME DK.S0 framework — clean-room from primary sources,
wired to their EXISTING gate/instrument, indistinguishable from a native skill — completing Phase E's
domain knowledge (DOMAIN_SKILLS 7 → 10):

  * deprecation — native to refine + develop; walks the EXISTING blast-radius instrument over the
                  removed symbol → memory + ledger, and rides the EXISTING deviation gate (a removal
                  from an approved plan is a plan change). Adds NO new gate. Sources: Fowler's
                  Strangler Fig + Refactoring (code-as-liability), Chesterton's Fence, Semantic
                  Versioning.
  * docs-adr    — native to ship + govern; an ADR's essence is PERSISTED to memory + the ledger
                  through the EXISTING WriteGate (the governed edge). Sources: Michael Nygard's ADRs,
                  adr.github.io.
  * shipping    — native to ship; a pre-launch checklist + staged rollout + rollback thresholds FEED
                  the EXISTING ship-readiness gate (mokata's release gate) — no fork. Sources: Google
                  SRE launches/canarying/release-engineering, Fowler on canary/blue-green/feature
                  toggles.

These tests are the DK.S4 validation block: each auto-engages on its trigger with the ⛭ banner; a
removal walks blast-radius on the removed symbol → ledger; an ADR persists to memory + ledger;
shipping's rollback thresholds feed the EXISTING ship-readiness gate (no new gate); the SK.S4 citation
lint passes on all three; the full anatomy is present; and NO new gate is added.
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
from mokata.govern.ledger import AuditLedger
from mokata.memory import MemoryStore, SQLiteBackend
from mokata.memory.item import CONTEXT, DECISION
from mokata.progress import SKILL_GLYPH
from mokata.skill_contracts import GATES

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")

DK_S4_SKILLS = ("deprecation", "docs-adr", "shipping")


def _skill_text(name):
    with open(os.path.join(_SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


# ============================================================ the skills ship + carry the anatomy
class TestShipGovernDomainSkillsShip(unittest.TestCase):
    """The three DK.S4 skills join the shipped domain-skill set, each a real file with the full
    anatomy, indistinguishable from a native pipeline skill — and they COMPLETE the 10-domain map."""

    def test_domain_skills_set_completed_to_ten(self):
        for name in DK_S4_SKILLS:
            self.assertIn(name, dk.DOMAIN_SKILLS)
        self.assertEqual(len(dk.DOMAIN_SKILLS), 10)             # the full attachment map now ships
        # attachment-map (registry) order: the S1/S2/S3 batches, then the DK.S4 ship/govern batch
        self.assertEqual(dk.shipped_domain_skills(),
                         ("api", "security", "performance", "frontend-a11y", "browser-testing",
                          "ci-cd", "git", "deprecation", "docs-adr", "shipping"))

    def test_each_domain_skill_has_a_shipped_file_with_the_marker(self):
        for name in DK_S4_SKILLS:
            path = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"missing shipped domain skill {name}")
            self.assertIn(SKILL_MARKER, _skill_text(name), "must carry the mokata skill marker")

    def test_each_carries_a_references_dir_progressive_disclosure(self):
        for name in DK_S4_SKILLS:
            refs = os.path.join(_SKILLS_DIR, name, "references")
            self.assertTrue(os.path.isdir(refs), f"{name} must ship a references/ tree (SK.S2)")
            self.assertTrue(any(f.endswith(".md") for f in os.listdir(refs)))

    def test_full_anatomy_is_present_indistinguishable_from_native(self):
        # run the SK.S1/S2/S4 lints DIRECTLY on the three domain texts: Contract + ⛭ activation +
        # ≥3 triggers + a negative + Rationalizations + Verification + (domain) citation — empty
        # findings means each domain skill is shaped exactly like a native pipeline skill.
        texts = {name: _skill_text(name) for name in DK_S4_SKILLS}
        findings = sl.run_lints(texts=texts)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))


# ============================================================ 1. AUTO-ENGAGE on the trigger (⛭)
class TestAutoEngageOnTrigger(unittest.TestCase):
    """Each skill auto-engages when the DK.S0 classifier puts its id in the spec's domains —
    deprecation on a removal/migration surface, docs-adr on a decision surface, shipping on a
    ship/release surface — with the ⛭ banner."""

    def test_deprecation_engages_on_a_removal_migration_surface(self):
        surface = dk.DomainSurface(
            touched_files=["src/migrations/0009_drop_legacy.py", "src/legacy/shim.py"],
            touched_symbols=["drop_legacy_users"], roles=["migration", "removal", "legacy-shim"])
        self.assertIn("deprecation", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("deprecation", eng.knowledge)             # develop JIT-pulls the skill
        self.assertEqual(list(eng.axes), [])                    # no review axis — an instrument path

    def test_docs_adr_engages_on_a_decision_surface(self):
        surface = dk.DomainSurface(
            touched_files=["docs/adr/0004-use-sqlite.md"],
            touched_symbols=["ADR-0004"], roles=["adr", "doc"])
        self.assertIn("docs-adr", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("docs-adr", eng.knowledge)                # ship/govern pull the skill
        self.assertEqual(list(eng.axes), [])                    # no review axis — rides write-gate

    def test_shipping_engages_on_a_ship_release_surface(self):
        surface = dk.DomainSurface(
            touched_files=["deploy/rollout.yaml", "CHANGELOG.md"],
            touched_symbols=["canary_release"], roles=["release", "rollout", "rollback"])
        self.assertIn("shipping", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("shipping", eng.knowledge)                # ship JIT-pulls the skill
        self.assertEqual(list(eng.axes), [])                    # no review axis — feeds ship-readiness

    def test_the_activation_line_is_single_sourced_not_hand_typed(self):
        # exactly ONE ⛭ line per shipped file, and it EQUALS the single-source generator, so the
        # banner can never drift from the registry (SK.S1 discipline, extended to domain skills).
        for name in DK_S4_SKILLS:
            lines = [ln.strip() for ln in _skill_text(name).splitlines()
                     if ln.strip().startswith(SKILL_GLYPH)]
            self.assertEqual(len(lines), 1, f"{name} must carry exactly one ⛭ line")
            self.assertEqual(lines[0], dk.domain_active_skill_line(name))

    def test_each_activation_line_announces_its_domains_governed_edge(self):
        # deprecation announces the deviation BACKED-gate one-liner (single-sourced from
        # skill_contracts); docs-adr the write-gate BACKED-gate one-liner; shipping the release-gate
        # INSTRUMENT one-liner (single-sourced from DOMAIN_INSTRUMENT_ONELINE).
        self.assertEqual(dk.domain_headline_gate_line("deprecation"), GATES["deviation"].one_line)
        self.assertEqual(dk.domain_headline_gate_line("docs-adr"), GATES["write-gate"].one_line)
        self.assertEqual(dk.domain_headline_gate_line("shipping"),
                         dk.DOMAIN_INSTRUMENT_ONELINE["release-gate"])


# ============================================================ 2. DEPRECATION — blast-radius → ledger
class TestDeprecationWalksBlastRadiusAndRecords(unittest.TestCase):
    """A removal classifies as `deprecation` from its blast-radius surface (the removed symbol +
    the callers walked), rides the EXISTING deviation gate, and the removal decision is RECORDED to
    typed memory + the audit ledger (human-gated). Blast-radius is the EXISTING instrument."""

    def test_a_removal_classifies_from_its_removed_symbol_blast_radius(self):
        impact = ApproachImpact(
            approach="remove the legacy users table and its accessor",
            targets=["drop_legacy_users"],
            impacted_files=["src/migrations/0009_drop_legacy.py", "src/store/legacy_users.py"],
            impacted_symbols=["drop_legacy_users", "LegacyUsers.fetch"])
        self.assertIn("deprecation",
                      dk.classify_from_impact(impact, roles=["removal", "migration"]))

    def test_a_removal_walks_blast_radius_on_the_removed_symbol_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            # the removal decision is recorded ABOUT the removed symbol (its walked blast-radius)
            res = dk.record_domain_decision(
                "deprecation", "remove LegacyUsers.fetch",
                "blast-radius on LegacyUsers.fetch walked — 2 callers migrated to Users.get first; "
                "old path deleted only when its blast-radius was empty (Strangler Fig)",
                store=store, ledger=ledger, about_code=["LegacyUsers.fetch"],
                confirm=lambda _p: True)                        # the human gate approves
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == DECISION for i in store.all_active()))  # typed decision
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "deprecation")
            self.assertEqual(dom.get("about_code"), ["LegacyUsers.fetch"])  # the removed symbol

    def test_deprecation_rides_the_backed_deviation_gate_not_a_new_one(self):
        # a removal from an approved plan is a plan change → the EXISTING deviation gate.
        self.assertEqual(dk.DOMAIN_REGISTRY["deprecation"].gate, "deviation")
        self.assertTrue(GATES["deviation"].backed)

    def test_the_deprecation_skill_encodes_its_named_principles_and_cites_them(self):
        low = _skill_text("deprecation").lower()
        self.assertIn("strangler", low)                         # the Strangler Fig pattern
        self.assertIn("chesterton", low)                        # Chesterton's Fence
        self.assertIn("blast-radius", low)                      # walks blast-radius on removal
        self.assertIn("martinfowler.com", low)                  # cited to Fowler primary sources
        self.assertIn("semver.org", low)                        # removals are breaking (SemVer)


# ============================================================ 3. DOCS/ADR — persists to memory + ledger
class TestDocsAdrPersistsToMemoryAndLedger(unittest.TestCase):
    """An ADR's essence is PERSISTED to typed memory + the audit ledger through the EXISTING
    human-gated WriteGate — that persistence is the governed edge — and declined without approval."""

    def test_an_adr_persists_to_memory_and_ledger_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "docs-adr", "ADR-0004: use SQLite for local memory",
                "Context: single-file portability required. Decision: SQLite backend. "
                "Consequence: no server dependency; team mode adds Postgres later.",
                store=store, ledger=ledger, about_code=["SQLiteBackend"],
                confirm=lambda _p: True)                        # the human gate approves
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == DECISION for i in store.all_active()))  # a typed decision
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "docs-adr")

    def test_an_unapproved_adr_does_not_persist(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "docs-adr", "ADR-0005: switch to event sourcing",
                "Decision: adopt an event store.", store=store, ledger=ledger,
                confirm=lambda _p: False)                       # the human declines
            self.assertFalse(res.committed, "an un-approved ADR must not persist")
            self.assertEqual(list(store.all_active()), [])
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("decision"), "declined")   # audited as declined

    def test_docs_adr_rides_the_backed_write_gate_not_a_new_one(self):
        self.assertEqual(dk.DOMAIN_REGISTRY["docs-adr"].gate, "write-gate")
        self.assertTrue(GATES["write-gate"].backed)

    def test_the_docs_adr_skill_encodes_the_adr_structure_and_cites_it(self):
        low = _skill_text("docs-adr").lower()
        self.assertIn("context", low)                           # ADR Context…
        self.assertIn("consequence", low)                       # …and Consequences
        self.assertIn("superseded", low)                        # immutable, superseded not edited
        self.assertIn("cognitect.com", low)                     # cited to Nygard's ADR article
        self.assertIn("adr.github.io", low)                     # + the ADR community source


# ============================================================ 4. SHIPPING — feeds ship-readiness
class TestShippingFeedsTheExistingShipReadinessGate(unittest.TestCase):
    """Shipping's pre-launch checklist + rollback thresholds FEED the EXISTING ship-readiness gate
    (mokata's release gate) — it forks NO new gate. The launch decision records to memory + ledger."""

    def test_shipping_feeds_the_release_gate_instrument_not_a_new_backed_gate(self):
        # its ⛭ governed edge is the release-gate INSTRUMENT one-liner — never a NEW backed gate.
        self.assertEqual(dk.DOMAIN_REGISTRY["shipping"].gate, "release-gate")
        self.assertIn("release-gate", dk.DOMAIN_INSTRUMENT_ONELINE)
        self.assertNotIn("release-gate", GATES)                 # not a backed gate at all — no fork

    def test_the_existing_ship_readiness_gate_is_the_backed_release_gate(self):
        # the BACKED gate shipping feeds is the EXISTING ship-readiness gate (unchanged, not forked).
        self.assertTrue(GATES["ship-readiness"].backed)
        low = _skill_text("shipping").lower()
        self.assertIn("ship-readiness", low)                    # names the EXISTING gate it feeds
        self.assertIn("no new gate", low)                       # forks none
        self.assertIn("rollback threshold", low)                # rollback thresholds feed it
        self.assertIn("pre-launch checklist", low)              # + the pre-launch checklist

    def test_a_shipping_decision_records_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "shipping", "0.0.12 rollout plan",
                "canary at 5% → 50% → 100%; rollback if error-rate > 1% or p99 latency > 2x baseline",
                store=store, ledger=ledger, confirm=lambda _p: True)
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == CONTEXT for i in store.all_active()))  # typed context
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "shipping")

    def test_the_shipping_skill_encodes_staged_rollout_and_cites_it(self):
        low = _skill_text("shipping").lower()
        self.assertIn("canary", low)                            # staged rollout — canary
        self.assertIn("blue-green", low)                        # + blue-green
        self.assertIn("feature", low)                           # + feature flags (deploy≠release)
        self.assertIn("sre.google", low)                        # cited to Google SRE
        self.assertIn("martinfowler.com", low)                  # + Fowler primary sources


# ============================================================ 5. clean-room authoring passes SK.S4
class TestCleanRoomCitationLintPassesOnAllThree(unittest.TestCase):
    """The SK.S4 (DK.S0) citation lint passes on all three: each cites a primary-source URL for its
    external claims and carries the UNVERIFIED discipline — own words, no repo text."""

    def test_citation_lint_is_clean_on_all_three_domain_skills(self):
        for name in DK_S4_SKILLS:
            self.assertEqual(sl.domain_citation_findings(name, _skill_text(name)), [],
                             f"{name} must pass the clean-room citation lint")

    def test_each_carries_the_unverified_discipline(self):
        for name in DK_S4_SKILLS:
            self.assertIn("unverified", _skill_text(name).lower(),
                          f"{name} must flag unverified claims (clean-room standard)")

    def test_own_words_mokata_framing_not_a_lifted_advisory(self):
        # own-words check: each references the mokata MECHANISM its domain feeds — which a copied
        # third-party advice-only skill would not name.
        dep = _skill_text("deprecation").lower()
        self.assertIn("blast-radius", dep)
        self.assertIn("ledger", dep)
        adr = _skill_text("docs-adr").lower()
        self.assertIn("ledger", adr)
        self.assertIn("write-gate", adr)
        ship = _skill_text("shipping").lower()
        self.assertIn("ship-readiness", ship)
        self.assertIn("ledger", ship)

    def test_a_domain_skill_stripped_of_citations_would_fail_the_lint(self):
        # proves the lint is load-bearing on these ids too: strip the URLs + UNVERIFIED and it flags.
        stripped = "---\nname: shipping\n---\nRoll it out. Watch the graphs.\n"
        self.assertTrue(sl.domain_citation_findings("shipping", stripped))


# ============================================================ 6. packaging — shipped, never pruned
class TestDomainSkillsShipAndSurvivePrune(unittest.TestCase):
    """The three DK.S4 skills are in the keep-set (curated + domain), so a setup/regen delivers them
    and NEVER prunes them as orphans — while a genuinely-dropped mokata skill still prunes."""

    def test_installed_keep_set_includes_the_ship_govern_domain_skills(self):
        keep = installed_skill_names()
        for name in DK_S4_SKILLS:
            self.assertIn(name, keep)

    def test_install_copies_skill_and_references_to_a_target(self):
        with tempfile.TemporaryDirectory() as d:
            install_domain_skills(_SKILLS_DIR, os.path.join(d, "skills"))
            for name in DK_S4_SKILLS:
                self.assertTrue(os.path.isfile(
                    os.path.join(d, "skills", name, "SKILL.md")), f"{name} not installed")
                refs = os.path.join(d, "skills", name, "references")
                self.assertTrue(os.path.isdir(refs), f"{name} references/ not installed")

    def test_prune_keeps_domain_skills_but_drops_a_real_orphan(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            sk = Path(d) / "skills"
            install_domain_skills(_SKILLS_DIR, sk)             # all shipped domain skills land
            (sk / "dropped").mkdir(parents=True)               # a genuinely removed mokata skill
            (sk / "dropped" / "SKILL.md").write_text(SKILL_MARKER + "\nx", encoding="utf-8")
            removed = prune_orphan_skills(sk, installed_skill_names())
            self.assertEqual([p.parent.name for p in removed], ["dropped"])
            for name in DK_S4_SKILLS:
                self.assertTrue((sk / name / "SKILL.md").is_file(),
                                f"{name} must survive the sync")


# ============================================================ 7. NO new gate added
class TestNoNewGate(unittest.TestCase):
    """DK.S4 attaches to EXISTING gates/instruments — the backed enforcement set is unchanged."""

    def test_backed_gate_set_is_unchanged(self):
        self.assertEqual(
            sorted(g for g, r in GATES.items() if r.backed),
            # PH-GATE.S0 (0.0.14) backed `approach-approval`; this stage still adds no gate.
            ["approach-approval", "completeness", "deviation", "hard-rule",
             "no-code-without-failing-test", "secret-guard", "ship-readiness", "spec-persisted",
             "write-gate"])

    def test_each_domain_feeds_an_existing_gate_or_instrument(self):
        # deprecation → the EXISTING deviation backed gate; docs-adr → the EXISTING write-gate backed
        # gate; shipping → the release-gate instrument (which the EXISTING ship-readiness gate backs).
        self.assertEqual(dk.DOMAIN_REGISTRY["deprecation"].gate, "deviation")
        self.assertTrue(GATES["deviation"].backed)
        self.assertEqual(dk.DOMAIN_REGISTRY["docs-adr"].gate, "write-gate")
        self.assertTrue(GATES["write-gate"].backed)
        self.assertIn(dk.DOMAIN_REGISTRY["shipping"].gate, dk.DOMAIN_INSTRUMENT_ONELINE)
        self.assertNotIn(dk.DOMAIN_REGISTRY["shipping"].gate, GATES)   # instrument, not a new gate

    def test_frontmatter_names_match_the_domain_ids(self):
        for name in DK_S4_SKILLS:
            fm = parse_frontmatter(_skill_text(name))
            self.assertEqual(fm.get("name"), name)
            self.assertTrue(fm.get("description", "").startswith("mokata ·"))


if __name__ == "__main__":
    unittest.main()
