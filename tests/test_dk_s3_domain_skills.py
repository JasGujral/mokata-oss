"""DK.S3 — the pipeline/landing domain skills: CI/CD + Git-workflow.

DK.S1 authored the build/review core (`api`, `security`); DK.S2 the verify/UI batch (`performance`,
`frontend-a11y`, `browser-testing`). DK.S3 authors the next TWO domain skills on the SAME DK.S0
framework — clean-room from primary sources, wired to their EXISTING gate/instrument, and
indistinguishable from a native skill:

  * ci-cd — native to ship + hardening; feeds the quality-gate pipeline + Scorecard posture (ties to
            SC.S1's hash-pinned deps + Scorecard hardening), and records its decisions to memory +
            ledger. Adds NO new gate. Sources: Fowler on continuous integration, Google SRE release
            engineering, OpenSSF Scorecard, DORA.
  * git   — native to ship (landing); rides the EXISTING WriteGate (a BACKED gate) on git actions,
            encodes atomic-commit + trunk-based discipline as named principles, and carries the
            change-sizing ADVISORY (no hard change-sizing gate this release). Sources: Pro Git /
            git docs, trunk-based development, Conventional Commits, Semantic Versioning.

These tests are the DK.S3 validation block: each auto-engages on its trigger with the ⛭ banner;
CI/CD references the quality-gate + Scorecard posture (ties to SC.S1, adds no new gate); a git action
rides the EXISTING WriteGate + the atomic-commit/change-sizing discipline is present; the SK.S4
citation lint passes on both; the full skill anatomy is present; and NO new gate is added.
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
from mokata.memory.item import CONTEXT
from mokata.progress import SKILL_GLYPH
from mokata.skill_contracts import GATES

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")

DK_S3_SKILLS = ("ci-cd", "git")


def _skill_text(name):
    with open(os.path.join(_SKILLS_DIR, name, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


# ============================================================ the skills ship + carry the anatomy
class TestPipelineLandingDomainSkillsShip(unittest.TestCase):
    """The two DK.S3 skills join the shipped domain-skill set, each a real file with the full
    anatomy, indistinguishable from a native pipeline skill."""

    def test_domain_skills_set_grew_to_include_the_pipeline_landing_batch(self):
        for name in DK_S3_SKILLS:
            self.assertIn(name, dk.DOMAIN_SKILLS)
        # attachment-map (registry) order: DK.S1 pair, DK.S2 verify/UI, then the DK.S3 batch.
        # Prefix-sliced (like DK.S2's [:5]) so a later batch (DK.S4) extends the set without
        # breaking this — the pipeline/landing batch stays the first seven, in order.
        self.assertEqual(dk.shipped_domain_skills()[:7],
                         ("api", "security", "performance", "frontend-a11y", "browser-testing",
                          "ci-cd", "git"))

    def test_each_domain_skill_has_a_shipped_file_with_the_marker(self):
        for name in DK_S3_SKILLS:
            path = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"missing shipped domain skill {name}")
            self.assertIn(SKILL_MARKER, _skill_text(name), "must carry the mokata skill marker")

    def test_each_carries_a_references_dir_progressive_disclosure(self):
        for name in DK_S3_SKILLS:
            refs = os.path.join(_SKILLS_DIR, name, "references")
            self.assertTrue(os.path.isdir(refs), f"{name} must ship a references/ tree (SK.S2)")
            self.assertTrue(any(f.endswith(".md") for f in os.listdir(refs)))

    def test_full_anatomy_is_present_indistinguishable_from_native(self):
        # run the SK.S1/S2/S4 lints DIRECTLY on the two domain texts: Contract + ⛭ activation +
        # ≥3 triggers + a negative + Rationalizations + Verification + (domain) citation — empty
        # findings means each domain skill is shaped exactly like a native pipeline skill.
        texts = {name: _skill_text(name) for name in DK_S3_SKILLS}
        findings = sl.run_lints(texts=texts)
        self.assertEqual(findings, [], "\n".join(f.render() for f in findings))


# ============================================================ 1. AUTO-ENGAGE on the trigger (⛭)
class TestAutoEngageOnTrigger(unittest.TestCase):
    """Each skill auto-engages when the DK.S0 classifier puts its id in the spec's domains —
    ci-cd on a workflow/pipeline surface, git on a landing/git surface — with the ⛭ banner."""

    def test_ci_cd_engages_on_a_workflow_pipeline_surface(self):
        surface = dk.DomainSurface(
            touched_files=[".github/workflows/release.yml", "scripts/release.sh"],
            touched_symbols=["release_job"], roles=["ci-config", "pipeline"])
        self.assertIn("ci-cd", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("ci-cd", eng.knowledge)                   # ship JIT-pulls the CI/CD skill
        self.assertEqual(list(eng.axes), [])                    # no review axis — an instrument

    def test_git_engages_on_a_landing_git_surface(self):
        surface = dk.DomainSurface(
            touched_files=[".gitignore", ".gitattributes"],
            touched_symbols=["branch_policy"], roles=["vcs", "commit-flow", "branch-policy"])
        self.assertIn("git", dk.classify_domains(surface))
        eng = dk.engage_for_spec(dk.classify_domains(surface))
        self.assertIn("git", eng.knowledge)                     # ship JIT-pulls the git skill
        self.assertEqual(list(eng.axes), [])                    # no review axis — rides write-gate

    def test_the_activation_line_is_single_sourced_not_hand_typed(self):
        # exactly ONE ⛭ line per shipped file, and it EQUALS the single-source generator, so the
        # banner can never drift from the registry (SK.S1 discipline, extended to domain skills).
        for name in DK_S3_SKILLS:
            lines = [ln.strip() for ln in _skill_text(name).splitlines()
                     if ln.strip().startswith(SKILL_GLYPH)]
            self.assertEqual(len(lines), 1, f"{name} must carry exactly one ⛭ line")
            self.assertEqual(lines[0], dk.domain_active_skill_line(name))

    def test_each_activation_line_announces_its_domains_governed_edge(self):
        # ci-cd announces the scorecard INSTRUMENT one-liner (single-sourced from
        # DOMAIN_INSTRUMENT_ONELINE); git announces the write-gate BACKED-gate one-liner
        # (single-sourced from skill_contracts, no fork).
        self.assertEqual(dk.domain_headline_gate_line("ci-cd"),
                         dk.DOMAIN_INSTRUMENT_ONELINE["scorecard"])
        self.assertEqual(dk.domain_headline_gate_line("git"),
                         GATES["write-gate"].one_line)


# ============================================================ 2. CI/CD — quality-gate + Scorecard (SC.S1)
class TestCiCdReferencesQualityGateAndScorecard(unittest.TestCase):
    """CI/CD classifies from a pipeline surface, references the quality-gate pipeline + Scorecard
    posture (ties to SC.S1), adds NO new gate, and records its decision to memory + the ledger."""

    def test_a_pipeline_change_classifies_from_its_workflow_surface(self):
        impact = ApproachImpact(
            approach="hash-pin the pip installs in release CI",
            targets=["release_job"],
            impacted_files=[".github/workflows/ci.yml", ".github/workflows/release.yml"],
            impacted_symbols=["release_job"])
        self.assertIn("ci-cd", dk.classify_from_impact(impact, roles=["ci-config", "quality-gate"]))

    def test_the_ci_cd_skill_references_the_quality_gate_and_scorecard_posture(self):
        low = _skill_text("ci-cd").lower()
        self.assertIn("quality-gate", low)                      # the pipeline it feeds
        self.assertIn("scorecard", low)                         # + the Scorecard posture
        self.assertIn("sc.s1", low)                             # ties explicitly to SC.S1
        self.assertIn("pinned", low)                            # the pinned-deps posture (SC.S1)
        self.assertIn("no new gate", low)                       # references, does not rebuild

    def test_ci_cd_feeds_an_instrument_not_a_backed_gate(self):
        # its governed edge is the scorecard INSTRUMENT one-liner — never a BACKED hard gate.
        self.assertEqual(dk.DOMAIN_REGISTRY["ci-cd"].gate, "scorecard")
        self.assertIn("scorecard", dk.DOMAIN_INSTRUMENT_ONELINE)
        self.assertNotIn("scorecard", GATES)                    # not a backed gate at all

    def test_a_ci_cd_decision_is_recorded_to_memory_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "ci-cd", "release pip installs hash-pinned",
                "release.yml uses --require-hashes from uv.lock (SC.S1) — Scorecard pinned-deps green",
                store=store, ledger=ledger, about_code=["release_job"],
                confirm=lambda _p: True)                        # the human gate approves
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == CONTEXT for i in store.all_active()))  # typed context
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "ci-cd")


# ============================================================ 3. GIT — rides the EXISTING WriteGate
class TestGitRidesTheExistingWriteGate(unittest.TestCase):
    """A git action that persists a durable record rides the EXISTING WriteGate (secret-scan → human
    approval → audit) — no new gate. The atomic-commit + trunk-based + change-sizing-advisory
    discipline is encoded, and change-sizing stays ADVISORY this release."""

    def test_a_git_decision_rides_the_write_gate_declined_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            # the human gate DECLINES → the durable write does not land (WriteGate is real)
            res = dk.record_domain_decision(
                "git", "branching model", "trunk-based with short-lived branches",
                store=store, ledger=ledger, confirm=lambda _p: False)
            self.assertFalse(res.committed, "an un-approved git decision must not persist")
            self.assertEqual(list(store.all_active()), [])
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("decision"), "declined")   # audited as declined

    def test_a_git_decision_persists_only_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
            ledger = AuditLedger(os.path.join(d, "ledger.jsonl"))
            res = dk.record_domain_decision(
                "git", "commit convention",
                "atomic single-concern commits; imperative summary + motivation body",
                store=store, ledger=ledger, confirm=lambda _p: True)
            self.assertTrue(res.committed)
            self.assertTrue(any(i.kind == CONTEXT for i in store.all_active()))
            dom = next(e for e in ledger.entries() if e.get("kind") == "domain")
            self.assertEqual(dom.get("domain"), "git")

    def test_git_gate_is_the_backed_write_gate_not_a_new_one(self):
        # git's governed edge is a BACKED mokata gate — the EXISTING WriteGate, not a fork.
        self.assertEqual(dk.DOMAIN_REGISTRY["git"].gate, "write-gate")
        self.assertTrue(GATES["write-gate"].backed)

    def test_the_git_skill_encodes_atomic_and_trunk_based_and_change_sizing(self):
        low = _skill_text("git").lower()
        self.assertIn("atomic", low)                            # atomic-commit principle
        self.assertIn("trunk-based", low)                       # trunk-based principle
        self.assertIn("change-sizing", low)                     # the sizing advisory
        self.assertIn("advisory", low)                          # sizing stays advisory this release
        self.assertIn("write-gate", low)                        # rides the existing WriteGate
        self.assertIn("git-scm.com", low)                       # cited to the git primary source
        self.assertIn("trunkbaseddevelopment.com", low)         # + trunk-based primary source


# ============================================================ 4. clean-room authoring passes SK.S4
class TestCleanRoomCitationLintPassesOnBoth(unittest.TestCase):
    """The SK.S4 (DK.S0) citation lint passes on both: each cites a primary-source URL for its
    external claims and carries the UNVERIFIED discipline — own words, no repo text."""

    def test_citation_lint_is_clean_on_both_domain_skills(self):
        for name in DK_S3_SKILLS:
            self.assertEqual(sl.domain_citation_findings(name, _skill_text(name)), [],
                             f"{name} must pass the clean-room citation lint")

    def test_each_carries_the_unverified_discipline(self):
        for name in DK_S3_SKILLS:
            self.assertIn("unverified", _skill_text(name).lower(),
                          f"{name} must flag unverified claims (clean-room standard)")

    def test_own_words_mokata_framing_not_a_lifted_advisory(self):
        # own-words check: each references the mokata MECHANISM its domain feeds — which a copied
        # third-party advice-only skill would not name.
        cicd = _skill_text("ci-cd").lower()
        self.assertIn("quality-gate", cicd)
        self.assertIn("scorecard", cicd)
        git = _skill_text("git").lower()
        self.assertIn("writegate", git)
        self.assertIn("ledger", git)

    def test_a_domain_skill_stripped_of_citations_would_fail_the_lint(self):
        # proves the lint is load-bearing on these ids too: strip the URLs + UNVERIFIED and it flags.
        stripped = "---\nname: ci-cd\n---\nMerge often. Keep the build green.\n"
        self.assertTrue(sl.domain_citation_findings("ci-cd", stripped))


# ============================================================ 5. packaging — shipped, never pruned
class TestDomainSkillsShipAndSurvivePrune(unittest.TestCase):
    """The two DK.S3 skills are in the keep-set (curated + domain), so a setup/regen delivers them
    and NEVER prunes them as orphans — while a genuinely-dropped mokata skill still prunes."""

    def test_installed_keep_set_includes_the_pipeline_landing_domain_skills(self):
        keep = installed_skill_names()
        for name in DK_S3_SKILLS:
            self.assertIn(name, keep)

    def test_install_copies_skill_and_references_to_a_target(self):
        with tempfile.TemporaryDirectory() as d:
            install_domain_skills(_SKILLS_DIR, os.path.join(d, "skills"))
            for name in DK_S3_SKILLS:
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
            for name in DK_S3_SKILLS:
                self.assertTrue((sk / name / "SKILL.md").is_file(),
                                f"{name} must survive the sync")


# ============================================================ 6. NO new gate added
class TestNoNewGate(unittest.TestCase):
    """DK.S3 attaches to EXISTING gates/instruments — the backed enforcement set is unchanged."""

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

    def test_ci_cd_feeds_an_instrument_and_git_rides_an_existing_backed_gate(self):
        # ci-cd → the scorecard instrument (never a backed gate); git → the EXISTING write-gate
        # (a backed gate mokata already ships) — neither adds a NEW backed enforcement point.
        self.assertIn(dk.DOMAIN_REGISTRY["ci-cd"].gate, dk.DOMAIN_INSTRUMENT_ONELINE)
        self.assertFalse(dk.DOMAIN_REGISTRY["ci-cd"].gate in GATES)
        self.assertEqual(dk.DOMAIN_REGISTRY["git"].gate, "write-gate")
        self.assertTrue(GATES["write-gate"].backed)             # pre-existing, not new

    def test_frontmatter_names_match_the_domain_ids(self):
        for name in DK_S3_SKILLS:
            fm = parse_frontmatter(_skill_text(name))
            self.assertEqual(fm.get("name"), name)
            self.assertTrue(fm.get("description", "").startswith("mokata ·"))


if __name__ == "__main__":
    unittest.main()
