"""DK.S2 — the verify/UI domain skills: Performance + Frontend-a11y + Browser-testing.

DK.S1 authored the build/review core (`api`, `security`) on the DK.S0 framework. DK.S2 authors the
next THREE domain skills on the SAME framework — clean-room from primary sources, wired to their
EXISTING gate/instrument, and indistinguishable from a native skill:

  * performance     — native to optimize + review's Performance axis; feeds measure-first — a perf
                      change REQUIRES a before/after measurement → the result is recorded to memory
                      + ledger (perf budget stays ADVISORY this release). Sources: web.dev / Core
                      Web Vitals (LCP/INP/CLS), lab-vs-field measurement.
  * frontend-a11y   — native to develop + review; feeds the a11y checklist run in review at the
                      WCAG-AA bar → adopted design-system conventions are recorded to memory
                      (best-practice). Sources: WCAG (POUR/AA) + MDN + WAI-ARIA.
  * browser-testing — native to test + debug; feeds the test gate; captures the live runtime signal
                      (console / network / page state) through the EXISTING MCP surface — no new
                      server. Sources: Chrome DevTools / DevTools Protocol / W3C WebDriver / MDN.

These tests are the DK.S2 validation block: each auto-engages on its trigger with the ⛭ banner; a
perf change requires a before-measurement + records a ledger entry; an a11y check runs in review
(WCAG-AA) + a design-system convention records to memory; browser-testing captures runtime data via
the existing MCP surface (no new server); the SK.S4 citation lint passes on all three; the full
skill anatomy is present; and NO new gate is added.
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
from mokata.memory.item import BEST_PRACTICE, CONTEXT
from mokata.progress import SKILL_GLYPH
from mokata.skill_contracts import GATES

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")

DK_S2_SKILLS = ("performance", "frontend-a11y", "browser-testing")


def _skill_text(name):
    with open(os.path.join(_SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


# ============================================================ the skills ship + carry the anatomy
class TestVerifyUiDomainSkillsShip(unittest.TestCase):
    """The three DK.S2 skills join the shipped domain-skill set, each a real file with the full
    anatomy, indistinguishable from a native pipeline skill."""

    def test_domain_skills_set_grew_to_include_the_verify_ui_batch(self):
        for name in DK_S2_SKILLS:
            self.assertIn(name, dk.DOMAIN_SKILLS)
        # attachment-map (registry) order: DK.S1 pair first, then the DK.S2 verify/UI batch. Later
        # batches (DK.S3+) append the rest, so assert this batch's LEADING order (like DK.S1's
        # `[:2]`) rather than the full set.
        self.assertEqual(dk.shipped_domain_skills()[:5],
                         ("api", "security", "performance", "frontend-a11y", "browser-testing"))

    def test_each_domain_skill_has_a_shipped_file_with_the_marker(self):
        for name in DK_S2_SKILLS:
            path = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"missing shipped domain skill {name}")
            self.assertIn(SKILL_MARKER, _skill_text(name), "must carry the mokata skill marker")

    def test_each_carries_a_references_dir_progressive_disclosure(self):
        for name in DK_S2_SKILLS:
            refs = os.path.join(_SKILLS_DIR, name, "references")
            self.assertTrue(os.path.isdir(refs), f"{name} must ship a references/ tree (SK.S2)")
            self.assertTrue(any(f.endswith(".md") for f in os.listdir(refs)))

    def test_full_anatomy_is_present_indistinguishable_from_native(self):
        # run the SK.S1/S2/S4 lints DIRECTLY on the three domain texts: Contract + ⛭ activation +
        # ≥3 triggers + a negative + Rationalizations + Verification + (domain) citation — empty
        # findings means each domain skill is shaped exactly like a native pipeline skill.
        texts = {name: _skill_text(name) for name in DK_S2_SKILLS}
        findings = sl.run_lints(texts=texts)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))


# ============================================================ 1. AUTO-ENGAGE on the trigger (⛭)
class TestAutoEngageOnTrigger(unittest.TestCase):
    """Each skill auto-engages when the DK.S0 classifier puts its id in the spec's domains —
    performance on a hot-path/perf-AC surface, frontend-a11y on a components/views surface,
    browser-testing on a browser/runtime surface — with the ⛭ banner."""

    def test_performance_engages_on_a_hot_path_surface(self):
        surface = dk.DomainSurface(
            touched_files=["src/app/queries/report.py"],
            touched_symbols=["build_report"], roles=["hot-path", "perf-ac"])
        self.assertIn("performance", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("Performance", eng.axes)                  # review activates the Perf axis
        self.assertIn("performance", eng.knowledge)             # develop/optimize JIT-pulls it

    def test_frontend_a11y_engages_on_a_components_views_surface(self):
        surface = dk.DomainSurface(
            touched_files=["src/ui/components/Button.tsx", "src/ui/views/Cart.vue"],
            touched_symbols=["Button"], roles=["ui-component", "view"])
        self.assertIn("frontend-a11y", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("frontend-a11y", eng.knowledge)           # develop JIT-pulls the a11y skill

    def test_browser_testing_engages_on_a_browser_runtime_surface(self):
        surface = dk.DomainSurface(
            touched_files=["tests/e2e/checkout.spec.ts"],
            touched_symbols=["checkout_flow"], roles=["browser-test", "devtools"])
        self.assertIn("browser-testing", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("browser-testing", eng.knowledge)

    def test_the_activation_line_is_single_sourced_not_hand_typed(self):
        # exactly ONE ⛭ line per shipped file, and it EQUALS the single-source generator, so the
        # banner can never drift from the registry (SK.S1 discipline, extended to domain skills).
        for name in DK_S2_SKILLS:
            lines = [ln.strip() for ln in _skill_text(name).splitlines()
                     if ln.strip().startswith(SKILL_GLYPH)]
            self.assertEqual(len(lines), 1, f"{name} must carry exactly one ⛭ line")
            self.assertEqual(lines[0], dk.domain_active_skill_line(name))

    def test_each_activation_line_announces_its_domains_governed_edge(self):
        # all three feed SK.S2 INSTRUMENTS (measure-first / a11y-checklist / test-gate), whose
        # one-liner is single-sourced from DOMAIN_INSTRUMENT_ONELINE — never a hard gate.
        self.assertIn("before/after", dk.domain_headline_gate_line("performance"))
        self.assertIn("checklist", dk.domain_headline_gate_line("frontend-a11y"))
        self.assertIn("test phase", dk.domain_headline_gate_line("browser-testing"))


# ============================================================ 2. PERFORMANCE — measure-first + ledger
class TestPerformanceRequiresMeasurementAndRecords(unittest.TestCase):
    """A perf change classifies from its hot-path surface, REQUIRES a before/after measurement, and
    the result is RECORDED to typed `context` memory + the audit ledger (human-gated)."""

    def test_a_perf_change_classifies_from_its_hot_path_blast_radius(self):
        impact = ApproachImpact(
            approach="cache the report aggregation",
            targets=["build_report"],
            impacted_files=["src/cache/report_cache.py"],
            impacted_symbols=["build_report"])
        self.assertIn("performance", dk.classify_from_impact(impact, roles=["hot-path", "cache"]))

    def test_the_before_after_measurement_is_recorded_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "performance", "report aggregation latency",
                "BEFORE p95=820ms → AFTER p95=140ms (lab, N=200, same fixture) — cache-aside win",
                store=store, ledger=ledger, about_code=["build_report"],
                confirm=lambda _p: True)                        # the human gate approves
            self.assertTrue(res.committed)
            stored = list(store.all_active())
            # a perf decision is typed `context` (measurement, not a rule)
            self.assertTrue(any(i.kind == CONTEXT for i in stored))
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "performance")

    def test_the_perf_skill_requires_a_before_measurement_and_stays_advisory(self):
        text = _skill_text("performance").lower()
        self.assertIn("measure-first", text)                    # the instrument it feeds
        self.assertIn("before", text)                           # a BEFORE measurement is required
        self.assertIn("advisory", text)                         # the perf budget stays advisory
        self.assertIn("web.dev", text)                          # cited to the primary source

    def test_the_perf_skill_activates_the_performance_axis_in_review(self):
        eng = dk.engage_for_spec(["performance"])
        self.assertEqual(list(eng.axes), ["Performance"])


# ============================================================ 3. FRONTEND-A11Y — WCAG-AA + memory
class TestFrontendA11yChecksInReviewAndRecordsConventions(unittest.TestCase):
    """The a11y checklist runs in review at the WCAG-AA bar, and an adopted design-system convention
    is recorded as a typed `best-practice` memory item + the ledger (human-gated)."""

    def test_a_design_system_convention_is_a_best_practice_memory_item(self):
        item = dk.domain_memory_item(
            "frontend-a11y", "focus-visible convention",
            "all interactive controls use the 2px accent-token focus ring; never outline:none")
        self.assertEqual(item.kind, BEST_PRACTICE)

    def test_the_convention_is_recorded_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "frontend-a11y", "focus-visible convention",
                "interactive controls use the accent-token focus ring; never outline:none",
                store=store, ledger=ledger, confirm=lambda _p: True)
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == BEST_PRACTICE for i in store.all_active()))
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "frontend-a11y")

    def test_the_a11y_skill_checks_wcag_aa_in_review_and_cites_it(self):
        text = _skill_text("frontend-a11y")
        low = text.lower()
        self.assertIn("wcag", low)                              # the standard
        self.assertIn("aa", low)                                # at the AA bar
        self.assertIn("review", low)                            # the checks run in review
        self.assertIn("w3.org", low)                            # cited to the WCAG primary source
        self.assertIn("developer.mozilla.org", low)             # + MDN


# ============================================================ 4. BROWSER-TESTING — MCP, no new server
class TestBrowserTestingCapturesRuntimeViaExistingMcp(unittest.TestCase):
    """Browser-testing feeds the test gate and captures the live runtime signal through the EXISTING
    MCP surface — it adds no new server, and treats captured output as tier-3 UNTRUSTED (G-D)."""

    def test_a_browser_run_record_is_typed_context(self):
        item = dk.domain_memory_item(
            "browser-testing", "checkout console-error repro",
            "clicking Pay logged TypeError in cart.js:42 — the request never fired (captured)")
        self.assertEqual(item.kind, CONTEXT)

    def test_the_run_record_persists_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "browser-testing", "checkout console-error repro",
                "captured console TypeError + the missing POST /pay request via the MCP surface",
                store=store, ledger=ledger, confirm=lambda _p: True)
            self.assertTrue(res.committed)
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "browser-testing")

    def test_the_skill_uses_the_existing_mcp_surface_and_adds_no_new_server(self):
        text = _skill_text("browser-testing")
        low = text.lower()
        self.assertIn("mcp", low)                               # the capture surface
        self.assertIn("no new", low)                            # adds no new server
        self.assertIn("console", low)                           # captures the runtime signal
        self.assertIn("network", low)
        self.assertIn("untrusted", low)                         # tier-3 posture (G-D)

    def test_browser_testing_feeds_the_existing_test_gate_not_a_new_one(self):
        # its governed edge is the test-gate INSTRUMENT one-liner, single-sourced (no fork).
        self.assertEqual(dk.domain_headline_gate_line("browser-testing"),
                         dk.DOMAIN_INSTRUMENT_ONELINE["test-gate"])


# ============================================================ 5. clean-room authoring passes SK.S4
class TestCleanRoomCitationLintPassesOnAllThree(unittest.TestCase):
    """The SK.S4 (DK.S0) citation lint passes on all three: each cites a primary-source URL for its
    external claims and carries the UNVERIFIED discipline — own words, no repo text."""

    def test_citation_lint_is_clean_on_all_three_domain_skills(self):
        for name in DK_S2_SKILLS:
            self.assertEqual(sl.domain_citation_findings(name, _skill_text(name)), [],
                             f"{name} must pass the clean-room citation lint")

    def test_each_carries_the_unverified_discipline(self):
        for name in DK_S2_SKILLS:
            self.assertIn("unverified", _skill_text(name).lower(),
                          f"{name} must flag unverified claims (clean-room standard)")

    def test_own_words_mokata_framing_not_a_lifted_advisory(self):
        # own-words check: each references the mokata MECHANISM its domain feeds — which a copied
        # third-party advice-only skill would not name.
        perf = _skill_text("performance").lower()
        self.assertIn("measure-first", perf)
        self.assertIn("ledger", perf)
        a11y = _skill_text("frontend-a11y").lower()
        self.assertIn("checklist", a11y)
        self.assertIn("memory", a11y)
        bt = _skill_text("browser-testing").lower()
        self.assertIn("test gate", bt)
        self.assertIn("mcp surface", bt)

    def test_a_domain_skill_stripped_of_citations_would_fail_the_lint(self):
        # proves the lint is load-bearing on these ids too: strip the URLs + UNVERIFIED and it flags.
        stripped = "---\nname: performance\n---\nMake it fast. Cache everything.\n"
        self.assertTrue(sl.domain_citation_findings("performance", stripped))


# ============================================================ 6. packaging — shipped, never pruned
class TestDomainSkillsShipAndSurvivePrune(unittest.TestCase):
    """The three DK.S2 skills are in the keep-set (curated + domain), so a setup/regen delivers them
    and NEVER prunes them as orphans — while a genuinely-dropped mokata skill still prunes."""

    def test_installed_keep_set_includes_the_verify_ui_domain_skills(self):
        keep = installed_skill_names()
        for name in DK_S2_SKILLS:
            self.assertIn(name, keep)

    def test_install_copies_skill_and_references_to_a_target(self):
        with tempfile.TemporaryDirectory() as d:
            install_domain_skills(_SKILLS_DIR, os.path.join(d, "skills"))
            for name in DK_S2_SKILLS:
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
            for name in DK_S2_SKILLS:
                self.assertTrue((sk / name / "SKILL.md").is_file(),
                                f"{name} must survive the sync")


# ============================================================ 7. NO new gate added
class TestNoNewGate(unittest.TestCase):
    """DK.S2 attaches to EXISTING gates/instruments — the backed enforcement set is unchanged."""

    def test_backed_gate_set_is_unchanged(self):
        self.assertEqual(
            sorted(g for g, r in GATES.items() if r.backed),
            # PH-GATE.S0 (0.0.14) backed `approach-approval`; this stage still adds no gate.
            # 0.0.17 stage 5 moved the SET without changing its SIZE: `self-protect` was
            # added (it enforces at `WriteGate.submit` layer 0 and was missing from the
            # table doc 85 §4 has listed it in since 2026-07-26) and `ship-readiness` was
            # demoted to advisory (nothing executes it). Still no gate from THIS stage.
            ["approach-approval", "completeness", "deviation", "hard-rule",
             "no-code-without-failing-test", "secret-guard", "self-protect", "spec-persisted",
             "write-gate"])

    def test_the_verify_ui_domains_feed_instruments_not_backed_gates(self):
        # measure-first / a11y-checklist / test-gate are SK.S1/S2 instruments, not backed hard
        # gates — each has an instrument one-liner and is never a BACKED enforcement point.
        for name in DK_S2_SKILLS:
            gate = dk.DOMAIN_REGISTRY[name].gate
            self.assertIn(gate, dk.DOMAIN_INSTRUMENT_ONELINE)
            self.assertFalse(gate in GATES and GATES[gate].backed,
                             f"{name}'s instrument {gate} must not be a backed hard gate")

    def test_frontmatter_names_match_the_domain_ids(self):
        for name in DK_S2_SKILLS:
            fm = parse_frontmatter(_skill_text(name))
            self.assertEqual(fm.get("name"), name)
            self.assertTrue(fm.get("description", "").startswith("mokata ·"))


if __name__ == "__main__":
    unittest.main()
