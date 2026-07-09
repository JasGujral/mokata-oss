"""Stage 33 — anti-assumption / ground-in-code discipline across every critical skill.

Both jsonschema states. The shared GROUNDING_DISCIPLINE clause is present on every critical
skill (prompt-rendered surface AND its shipped template), via a single source so it can't
drift; `spec` requires inspecting the code before ACs and surfaces a "Verified from code:"
section; the per-skill grounding requirements are present; and `develop` carries the
mid-flight "discovered assumption → STOP/confirm/re-plan" rule referencing the deviation path.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.skills import (
    GROUNDING_DISCIPLINE,
    command_markdown,
    get_skill,
    render_skill,
    skill_names,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# every critical skill (the whole registry)
CRITICAL = ("brainstorm", "refine", "spec", "test", "develop", "review",
            "debug", "optimize", "bug")
# generated from command_markdown (brainstorm is hand-authored, kept its own format)
GENERATED = ("refine", "spec", "test", "develop", "review", "debug", "optimize", "bug")


def _template(name):
    with open(os.path.join(ROOT, "src", "mokata", "templates", "commands", f"{name}.md"),
              encoding="utf-8") as fh:
        return fh.read()


class TestGroundingClausePresent(unittest.TestCase):
    def test_every_critical_skill_has_the_clause(self):
        for name in CRITICAL:
            skill = get_skill(name)
            self.assertTrue(skill.ground, f"{name} should carry the grounding discipline")
            # the rendered launch surface carries the exact shared clause
            self.assertIn(GROUNDING_DISCIPLINE, render_skill(skill),
                          f"{name} render missing the grounding clause")
            # SK.S2 single-source: the template no longer embeds a literal copy — it carries the
            # grounding MARKER, expanded to the one canonical block at materialization time.
            from mokata.skills import GROUNDING_MARKER
            self.assertIn(GROUNDING_MARKER, _template(name),
                          f"{name} template must carry the grounding marker (not a copy)")
            self.assertNotIn("## Grounding discipline", _template(name),
                             f"{name} template must NOT embed a literal grounding block")

    def test_clause_is_single_source_for_generated(self):
        # template == command_markdown(skill) — the clause can't drift on generated skills
        for name in GENERATED:
            self.assertEqual(_template(name), command_markdown(get_skill(name)))

    def test_brainstorm_template_derives_from_the_single_source(self):
        # brainstorm.md is hand-authored, but SK.S2 removed its hand-maintained grounding COPY:
        # it now carries the single-source MARKER, which expands to the canonical block (so the
        # invariant phrases live in the ONE source, not in a template copy that can drift).
        from mokata.skills import GROUNDING_MARKER, expand_grounding
        tpl = _template("brainstorm")
        self.assertIn(GROUNDING_MARKER, tpl,
                      "brainstorm.md must carry the grounding marker, not a copy")
        self.assertNotIn("Decide from the code, not from assumption", tpl,
                         "brainstorm.md must not embed a hand-maintained grounding copy")
        # the expanded form (what the reader/agent actually sees) carries the invariant phrases
        expanded = expand_grounding(tpl)
        for phrase in ("Decide from the code, not from assumption",
                       "never silently assume", 'no "assumed and continued" path'):
            self.assertIn(phrase, expanded)

    def test_clause_states_the_core_principle(self):
        low = GROUNDING_DISCIPLINE.lower()
        for token in ("verify", "structural queries", "memory", "never silently assume",
                      "cite what you verified", "ask", "deviation gate"):
            self.assertIn(token, low)


class TestSpecGrounding(unittest.TestCase):
    def test_spec_inspects_code_before_acs_and_surfaces_verified_from_code(self):
        prompt = get_skill("spec").prompt
        low = prompt.lower()
        self.assertIn("before drafting or emitting any acceptance criterion", low)
        self.assertIn("inspect the real code", low)
        self.assertIn("Verified from code:", prompt)        # the auditable surface
        self.assertIn("ask before emitting", low)
        self.assertIn("small, ordered, verifiable tasks", low)  # adopted clean-room strength


class TestPerSkillGrounding(unittest.TestCase):
    def test_test_references_real_signatures(self):
        low = get_skill("test").prompt.lower()
        self.assertIn("real names, signatures", low)
        self.assertIn("never invent an interface", low)

    def test_develop_checks_call_sites_and_midflight_replan(self):
        prompt = get_skill("develop").prompt
        low = prompt.lower()
        # per-skill: check call sites before changing a shared symbol
        self.assertIn("call sites", low)
        self.assertIn("blast_radius", low)
        # mid-flight: discovered assumption -> STOP/confirm/re-plan via the deviation path
        # (carried by the shared clause appended to the rendered surface)
        rendered = render_skill(get_skill("develop"))
        rlow = rendered.lower()
        self.assertIn("stop", rlow)
        self.assertIn("confirm with the user", rlow)
        self.assertIn("re-plan", rlow)
        self.assertIn("deviation gate", rlow)

    def test_review_checks_against_actual_code(self):
        low = get_skill("review").prompt.lower()
        self.assertIn("actual code", low)
        self.assertIn("assumed rather than verified", low)

    def test_debug_and_bug_root_cause_from_real_code(self):
        for name in ("debug", "bug"):
            low = get_skill(name).prompt.lower()
            self.assertIn("real code", low)

    def test_optimize_measures_real_code(self):
        low = get_skill("optimize").prompt.lower()
        self.assertIn("real code", low)
        self.assertIn("don't assume the hot path", low)


if __name__ == "__main__":
    unittest.main()
