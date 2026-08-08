"""SK.S3 — develop's no-assumptions → ask + amend-spec loop, and brainstorm's design pre-mortem.

Behavioral guards (all clean-room / own-words additions of stage SK.S3). This stage turns
develop's prose "no silent assumptions" into an ENFORCED loop and shifts review-class issue
resolution left into brainstorm's plan:

  * develop STOPS on a NON-TRIVIAL ambiguity (a branching decision, a boundary crossing, an
    unverifiable invariant, or an irreversible blast-radius), asks exactly ONE question, amends
    the spec through the HUMAN gate, and re-maps the ACs/tests — never "assumed and continued";
  * a TRIVIAL detail does NOT over-fire the loop (no asking on cosmetics);
  * the loop CAPS at ~2 amendments on the SAME ambiguity, then ESCALATES (no infinite loop);
  * it COMPOSES with the existing deviation + spec-persisted gates — it adds NO new gate and
    weakens neither; the gate registry + develop's Contract are UNCHANGED;
  * brainstorm's design pre-mortem lands a planted review-class edge-case into the PLAN + an AC
    (before code), not at review;
  * a change-sizing advisory (blast-radius-informed) is present and clearly ADVISORY, not a gate.

The single sources render into the shipped surfaces (skills.py prompt → command template →
SKILL.md), so the drift guards (SK.S1/S2, stage 33/54g) stay green.
"""

import os
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata.agent_skills import CURATED_SKILLS, skill_markdown
from mokata.skills import command_markdown, get_skill
from mokata import skill_contracts as sc

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")
_TEMPLATES = Path(os.path.join(_REPO, "src", "mokata", "templates", "commands"))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _skill(name):
    return _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))


def _template(name):
    return _read(str(_TEMPLATES / f"{name}.md"))


# ============================================================ develop: the enforced loop
class TestDevelopAskAmendLoop(unittest.TestCase):
    """A NON-TRIVIAL ambiguity → STOP + ask ONE + amend the spec (human-gated) + re-map ACs —
    NOT 'assumed and continued'."""

    def setUp(self):
        self.skill = _skill("develop")
        self.prompt = get_skill("develop").prompt

    def test_loop_is_a_named_section_on_the_shipped_skill(self):
        # the enforced loop is legible content on the develop SKILL.md, not buried prose
        self.assertIn("## No silent assumptions", self.skill,
                      "develop must carry the enforced ask→amend loop as its own section")

    def test_the_four_steps_stop_ask_amend_remap(self):
        low = self.prompt.lower()
        self.assertIn("stop", low)
        self.assertIn("ask exactly one question", low,
                      "the loop must ask exactly ONE question, not a wall")
        self.assertIn("amend the spec", low)
        self.assertIn("re-map", low)
        # the amend is HUMAN-GATED and the re-map keeps every AC mapped to a test
        self.assertIn("human-gated", low)
        for token in ("acceptance criterion", "maps to a test"):
            self.assertIn(token, low, f"the re-map step must keep {token!r}")

    def test_names_the_four_nontrivial_ambiguity_classes(self):
        low = self.prompt.lower()
        for cls in ("branching", "boundary", "unverifiable invariant",
                    "irreversible blast-radius"):
            self.assertIn(cls, low,
                          f"the non-trivial definition must name the {cls!r} class")

    def test_there_is_no_assumed_and_continued_path(self):
        self.assertIn('no "assumed and continued" path', self.prompt.lower())


class TestTrivialDoesNotOverfire(unittest.TestCase):
    """A TRIVIAL detail must NOT trigger the loop — no over-firing on cosmetic choices."""

    def test_trivial_details_are_excluded_with_examples(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("trivial", low)
        self.assertIn("do not trigger", low,
                      "trivial details must be stated as NOT triggering the loop")
        # concrete cosmetic examples so the model doesn't over-ask
        self.assertTrue(
            any(ex in low for ex in ("variable name", "formatting", "log ")),
            "name concrete trivial/cosmetic examples that must not fire the loop")

    def test_over_asking_is_named_as_its_own_failure(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("over-ask", low,
                      "over-asking on cosmetics must be flagged as a failure mode")


class TestCapAndEscalate(unittest.TestCase):
    """The loop CAPS at ~2 amendments on the SAME ambiguity, then ESCALATES — never loops
    forever."""

    def test_cap_is_about_two_on_the_same_ambiguity(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("same ambiguity", low)
        self.assertIn("at most", low)
        self.assertTrue("twice" in low or "two" in low,
                        "the cap must be ~2 amendments on the same ambiguity")

    def test_escalates_and_never_loops_forever(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("escalate", low)
        self.assertTrue(
            "infinite" in low or "third time" in low or "loops forever" in low,
            "must state the loop never becomes an infinite question loop")


class TestComposesWithExistingGates(unittest.TestCase):
    """The loop COMPOSES with the deviation + spec-persisted gates — the amend IS the
    human-gated spec write. It adds NO new gate and weakens neither."""

    def test_loop_text_names_the_gates_it_composes_with(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("deviation gate", low)
        self.assertIn("spec-persisted", low)
        self.assertIn("adds no new gate", low,
                      "the loop must state explicitly that it adds no new gate")

    def test_develop_headline_gate_is_unchanged(self):
        # SK.S1's develop headline gate is untouched — the loop is a discipline, not a gate.
        self.assertEqual(sc.CONTRACTS["develop"].headline_gate,
                         "no-code-without-failing-test")

    def test_develop_contract_still_cites_deviation_and_spec_persisted(self):
        cited = {cl.gate for cl in sc.CONTRACTS["develop"].must_not
                 if cl.gate} | {cl.gate for cl in sc.CONTRACTS["develop"].depends_on if cl.gate}
        self.assertIn("deviation", cited)
        self.assertIn("spec-persisted", cited)

    def test_gate_registry_is_unchanged(self):
        # No gate was added or altered by this stage — the backed set is exactly the SK.S1 eight.
        self.assertEqual(
            sorted(g for g, r in sc.GATES.items() if r.backed),
            # PH-GATE.S0 (0.0.14) backed `approach-approval`; this stage still adds no gate.
            # 0.0.17 stage 5 moved the SET without changing its SIZE: `self-protect` was
            # added (it enforces at `WriteGate.submit` layer 0 and was missing from the
            # table doc 85 §4 has listed it in since 2026-07-26) and `ship-readiness` was
            # demoted to advisory (nothing executes it). Still no gate from THIS stage.
            ["approach-approval", "completeness", "deviation", "hard-rule",
             "no-code-without-failing-test", "secret-guard", "self-protect", "spec-persisted",
             "write-gate"])


class TestChangeSizingAdvisory(unittest.TestCase):
    """The optional change-sizing advisory is present, blast-radius-informed, and clearly
    ADVISORY — not a gate."""

    def test_change_sizing_is_advisory_and_blast_radius_informed(self):
        low = get_skill("develop").prompt.lower()
        self.assertIn("change-sizing", low)
        self.assertIn("blast-radius", low)
        # advisory, explicitly not a gate
        window = low[low.index("change-sizing"):low.index("change-sizing") + 400]
        self.assertIn("advisory", window)
        self.assertIn("not a gate", window,
                      "change-sizing must be stated as advisory, never a gate")


# ============================================================ brainstorm: design pre-mortem
class TestBrainstormPreMortem(unittest.TestCase):
    """SL.S1 — the design pre-mortem resolves review-class issue-classes IN THE PLAN: a planted
    edge-case lands in the plan + an AC BEFORE code, not at review."""

    def setUp(self):
        self.skill = _skill("brainstorm")

    def test_pre_mortem_is_a_named_section(self):
        self.assertIn("## Design pre-mortem", self.skill,
                      "brainstorm must carry the SL.S1 design pre-mortem section")

    def test_covers_the_review_class_issue_classes(self):
        low = self.skill.lower()
        for issue in ("edge case", "error handling", "integration",
                      "coverage", "scope creep", "blast-radius"):
            self.assertIn(issue, low,
                          f"the pre-mortem must anticipate the {issue!r} review-class issue")

    def test_each_surfaced_risk_lands_in_the_plan_with_an_ac_and_a_check(self):
        low = self.skill.lower()
        # resolved IN THE PLAN (before the spec), each risk carrying its AC + the check
        self.assertIn("in the plan", low)
        self.assertIn("acceptance criterion", low)
        # the shift-left claim: caught here, before code — not first found AT review later
        self.assertTrue("before the code" in low or "before code" in low)
        self.assertIn("at review", low)

    def test_planted_edge_case_is_recorded_not_deferred(self):
        # the instruction is explicit: a surfaced edge-case is written into the plan as an AC,
        # never left to be discovered by review later.
        low = self.skill.lower()
        self.assertIn("surface", low)
        self.assertIn("record", low)


# ============================================================ single-source / drift guards
class TestSingleSourcedAndDriftGuardsHold(unittest.TestCase):
    """The additions render from the single sources — the SK.S1/S2 + stage-33/54g drift guards
    stay green (shipped == generated), and the loop is NOT hand-copied into the template."""

    def test_develop_template_matches_command_markdown(self):
        # develop is a GENERATED skill: its template is exactly command_markdown(develop)
        self.assertEqual(_template("develop"), command_markdown(get_skill("develop")))

    def test_every_skill_md_matches_generated(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                self.assertEqual(_skill(name), skill_markdown(name, _TEMPLATES),
                                 f"{name}/SKILL.md drifted — regenerate, never hand-edit")

    def test_brainstorm_pre_mortem_lives_in_the_template(self):
        # brainstorm.md is hand-authored; the pre-mortem section is added there and flows into
        # the SKILL.md via the renderer (not hand-edited into SKILL.md).
        self.assertIn("## Design pre-mortem", _template("brainstorm"))


if __name__ == "__main__":
    unittest.main()
