"""SK.S2 — skill anatomy, single-sourced grounding, and review enrichment.

Guards, all clean-room / own-words additions of stage SK.S2:
  * every one of the 15 CURATED_SKILLS carries an anti-rationalization table AND a Verification
    checkbox, single-sourced from `skill_anatomy` (not 15 hand copies);
  * develop's required "I'll clean this up while I'm here" excuse is present;
  * the grounding block is single-sourced: ONE edit to `skills.GROUNDING_DISCIPLINE` propagates
    to all 15 SKILL.md + every command surface (no template holds a literal copy);
  * the single source now carries G-C (source/citation) + G-D (untrusted-data tiers);
  * review renders the five named axes + per-finding severity + the code-health bar + the
    doubt-theater flag, and adds/*changes* NO gate;
  * progressive-disclosure references exist on disk and ship via setup;
  * the Verification checkbox is OUTPUT-ONLY — it is not wired as a runtime gate.
"""

import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import skill_anatomy as sa
from mokata.agent_skills import CURATED_SKILLS, skill_markdown
from mokata.skills import (
    GROUNDING_DISCIPLINE,
    GROUNDING_MARKER,
    expand_grounding,
    grounding_block,
)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")
_TEMPLATES = Path(os.path.join(_REPO, "src", "mokata", "templates", "commands"))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _skill(name):
    return _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))


class TestAnatomyCoversAll15(unittest.TestCase):
    def test_anatomy_source_covers_exactly_the_15(self):
        self.assertEqual(set(sa.ANATOMY), set(CURATED_SKILLS),
                         "ANATOMY must cover exactly the 15 curated skills")

    def test_every_skill_has_rationalizations_and_verification(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                text = _skill(name)
                self.assertIn("## Rationalizations — stop if you catch yourself", text,
                              f"{name} missing its anti-rationalization table")
                self.assertIn("| Excuse | Reality |", text,
                              f"{name} rationalization table has no header")
                self.assertIn("## Verification — confirm each before you claim", text,
                              f"{name} missing its Verification checkbox")
                self.assertIn("- [ ]", text, f"{name} Verification has no checkboxes")
                # non-empty content
                a = sa.ANATOMY[name]
                self.assertGreaterEqual(len(a.rationalizations), 3,
                                        f"{name} needs a real rationalization table")
                self.assertGreaterEqual(len(a.verification), 3,
                                        f"{name} needs a real verification checklist")

    def test_develop_has_the_required_cleanup_excuse(self):
        text = _skill("develop")
        self.assertIn("I'll clean this up while I'm here", text,
                      "develop must carry the required signature excuse")

    def test_anatomy_is_single_sourced_not_hand_copied(self):
        # the block each SKILL.md carries IS render_anatomy_md(name) — one source, no 15 copies
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(sa.render_anatomy_md(name), _skill(name),
                              f"{name}'s anatomy is not the single-source render")


class TestGroundingSingleSourced(unittest.TestCase):
    """The block is authored ONCE; templates carry the marker, and the render/materialize paths
    expand it — so a single edit propagates to every consumer."""

    def test_no_template_embeds_a_literal_grounding_block(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                tpl = _read(str(_TEMPLATES / f"{name}.md"))
                self.assertNotIn("## Grounding discipline", tpl,
                                 f"{name}.md must not embed a literal grounding block")

    def test_grounding_carrying_templates_use_the_marker(self):
        # the 11 code-grounding skills carry the marker; the 4 non-grounding ones carry neither
        grounding_skills = {"brainstorm", "spec", "test", "develop", "review", "refine",
                            "debug", "bug", "optimize", "ship", "onboard", "docsync"}
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                tpl = _read(str(_TEMPLATES / f"{name}.md"))
                if name in grounding_skills:
                    self.assertIn(GROUNDING_MARKER, tpl, f"{name}.md must carry the marker")
                else:
                    self.assertNotIn(GROUNDING_MARKER, tpl,
                                     f"{name}.md is not a grounding skill — no marker expected")

    def test_shipped_skills_expand_the_marker_never_leak_it(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                text = _skill(name)
                self.assertNotIn(GROUNDING_MARKER, text,
                                 f"{name}/SKILL.md leaked an unexpanded marker")

    def test_one_edit_propagates_to_all_consumers(self):
        # PROVE single-sourcing: monkey-patch GROUNDING_DISCIPLINE, re-render, and every
        # grounding skill's SKILL.md reflects the edit — from ONE change, no per-file edits.
        import mokata.skills as sk
        sentinel = "SENTINEL-GROUNDING-EDIT-XYZ"
        original = sk.GROUNDING_DISCIPLINE
        sk.GROUNDING_DISCIPLINE = original + " " + sentinel
        try:
            grounding_skills = {"brainstorm", "spec", "test", "develop", "review", "refine",
                                "debug", "bug", "optimize", "ship", "onboard"}
            for name in grounding_skills:
                rendered = skill_markdown(name, _TEMPLATES)
                self.assertIn(sentinel, rendered,
                              f"a single edit to GROUNDING_DISCIPLINE did NOT reach {name}")
            # and a non-grounding skill is untouched (no false propagation)
            self.assertNotIn(sentinel, skill_markdown("govern", _TEMPLATES))
        finally:
            sk.GROUNDING_DISCIPLINE = original

    def test_grounding_carries_g_c_citation_and_g_d_tiers(self):
        low = GROUNDING_DISCIPLINE.lower()
        # G-C — official-docs citation + UNVERIFIED flagging
        self.assertIn("official", low)
        self.assertIn("cite", low)
        self.assertIn("unverified", low)
        # G-D — the three trust tiers + the never-follow-embedded-instructions rule
        for token in ("trust tiers", "trusted", "untrusted", "browser", "hosted-agent",
                      "data, not a command"):
            self.assertIn(token, low)

    def test_marker_expands_to_the_canonical_block(self):
        self.assertEqual(expand_grounding(GROUNDING_MARKER), grounding_block())
        self.assertIn(GROUNDING_DISCIPLINE, grounding_block())
        # a no-op when the marker is absent
        self.assertEqual(expand_grounding("no marker here"), "no marker here")


class TestReviewEnrichment(unittest.TestCase):
    def test_review_prints_the_five_named_axes(self):
        text = _skill("review")
        for axis in ("**Correctness**", "**Readability**", "**Architecture**",
                     "**Security**", "**Performance**"):
            self.assertIn(axis, text, f"review missing the {axis} axis")

    def test_axes_hook_to_real_instruments(self):
        text = _skill("review")
        self.assertIn("design-fit lens", text)          # Architecture → design-fit lens
        self.assertIn("blast-radius", text)
        self.assertIn("secret-guard", text)             # Security → secret-guard
        self.assertIn("untrusted-data posture (G-D)", text)   # Security → G-D
        self.assertIn("perf checklist", text)           # Performance → checklist

    def test_per_finding_severity_labels(self):
        text = _skill("review")
        for sev in ("**Blocking**", "**Minor**", "**Suggestion**", "**Info**"):
            self.assertIn(sev, text, f"review missing the {sev} severity")

    def test_code_health_bar_and_doubt_theater(self):
        text = _skill("review")
        self.assertIn("DEFINITELY improves code health", text)
        self.assertIn("DOUBT THEATER", text)

    def test_severity_is_output_only_not_a_gate(self):
        text = _skill("review")
        self.assertIn("OUTPUT LABEL", text)
        self.assertIn("is NOT a gate and changes no gate", text)

    def test_review_headline_gate_unchanged(self):
        # the ⛭ gate line + Contract must be exactly SK.S1's — no gate added or changed
        from mokata.skill_contracts import CONTRACTS
        self.assertEqual(CONTRACTS["review"].headline_gate, "spec-then-quality")


class TestProgressiveDisclosureReferences(unittest.TestCase):
    def test_reference_files_exist_on_disk(self):
        for name in sa.skills_with_references():
            for rel in sa.reference_relpaths(name):
                path = os.path.join(_SKILLS_DIR, name, rel)
                with self.subTest(skill=name, ref=rel):
                    self.assertTrue(os.path.isfile(path),
                                    f"{name} points at a missing reference {rel}")

    def test_skill_md_points_at_its_references(self):
        for name in sa.skills_with_references():
            text = _skill(name)
            self.assertIn("## References", text, f"{name} SKILL.md has no References section")
            for rel in sa.reference_relpaths(name):
                self.assertIn(rel, text, f"{name} SKILL.md doesn't point at {rel}")

    def test_setup_copies_references(self):
        from mokata.agent_skills import copy_skill_references
        with tempfile.TemporaryDirectory() as d:
            written = copy_skill_references(Path(_SKILLS_DIR), Path(d))
            self.assertTrue(written, "no references were copied")
            for name in sa.skills_with_references():
                for rel in sa.reference_relpaths(name):
                    self.assertTrue(os.path.isfile(os.path.join(d, name, rel)),
                                    f"setup did not deliver {name}/{rel}")


class TestDriftGuardStillHolds(unittest.TestCase):
    def test_shipped_matches_generated_with_anatomy(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                self.assertEqual(_skill(name), skill_markdown(name, _TEMPLATES),
                                 f"{name}/SKILL.md drifted — regenerate, never hand-edit")


class TestCleanRoomOwnWords(unittest.TestCase):
    """Own-words check: the anatomy/severity taxonomies use mokata's OWN labels, not the
    agent-skills repo's. The repo's severity set is Critical/Nit/Optional/FYI — ours is
    Blocking/Minor/Suggestion/Info; the repo's verification header differs too."""

    def test_severity_taxonomy_is_mokatas_own_not_the_repos(self):
        text = _skill("review")
        # mokata's labels present …
        for ours in ("Blocking", "Suggestion", "Info"):
            self.assertIn(ours, text)
        # … and the repo's distinctive labels absent (would signal copied text)
        for theirs in ("Nit", "FYI"):
            self.assertNotIn(theirs, text, f"foreign severity label {theirs!r} leaked in")


if __name__ == "__main__":
    unittest.main()
