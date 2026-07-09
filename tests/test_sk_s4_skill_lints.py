"""SK.S4 — skill lints + proactive dispatch.

Guards:
  * all three lints (contract / trigger / anatomy) are GREEN on the 15 shipped curated skills;
  * each lint FAILS on a planted defect (missing Contract/activation, <3 triggers or no
    negative, a dropped anatomy section);
  * a planted self-exempt in a skill's OWN frontmatter is REJECTED — the underlying defect is
    still reported (exemptions are validator-owned, never settable from the file under test);
  * the expected set is `CURATED_SKILLS`, not a hardcoded number (adding a name extends it);
  * proactive dispatch: every curated skill carries an auto-fire `when_to_use` (≥3 triggers + a
    negative), and the nudge is present in the ≤60-line always-on rules + the ≤2k bootstrap.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.agent_skills import CURATED_SKILLS, parse_frontmatter
from mokata.skill_lints import (
    EXEMPTIONS,
    LINT_ANATOMY,
    LINT_CONTRACT,
    LINT_SELF_EXEMPT,
    LINT_TRIGGER,
    MIN_TRIGGERS,
    analyze_triggers,
    expected_skill_names,
    load_shipped_skill_texts,
    run_lints,
)


def _lint_ids(findings):
    return {f.lint for f in findings}


def _inject_frontmatter_key(text, line):
    """Insert `line` into the SKILL.md frontmatter (right after the `name:` line)."""
    lines = text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("name:"):
            lines.insert(i + 1, line + "\n")
            break
    return "".join(lines)


class TestLintsGreenOnShipped(unittest.TestCase):
    def test_all_three_lints_green_on_the_15_curated_skills(self):
        findings = run_lints()
        self.assertEqual(
            findings, [],
            "shipped skills must pass every lint; got:\n"
            + "\n".join(f.render() for f in findings))

    def test_expected_set_is_the_curated_registry_not_a_number(self):
        # The lint reads its expected set from CURATED_SKILLS — no hardcoded count.
        self.assertEqual(expected_skill_names(), tuple(CURATED_SKILLS))
        self.assertEqual(len(run_lints()), 0)
        self.assertEqual(set(load_shipped_skill_texts()), set(CURATED_SKILLS))


class TestPlantedDefectsFailEachLint(unittest.TestCase):
    def setUp(self):
        # A real, passing shipped skill as the baseline to mutate.
        self.good = load_shipped_skill_texts(("spec",))["spec"]
        self.assertEqual(run_lints({"spec": self.good}), [])

    def test_contract_lint_fails_on_missing_contract(self):
        bad = self.good.replace("## Contract", "## (contract removed)")
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_CONTRACT, _lint_ids(findings))

    def test_contract_lint_fails_on_missing_activation_line(self):
        # Drop the ⛭ activation line only (leave the Contract intact).
        bad = "\n".join(
            ln for ln in self.good.splitlines() if not ln.strip().startswith("⛭ mokata spec"))
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_CONTRACT, _lint_ids(findings))
        self.assertTrue(any("activation line" in f.message for f in findings))

    def test_trigger_lint_fails_on_too_few_triggers(self):
        bad = ("---\nname: spec\ndescription: x\n"
               "when_to_use: Engage when the user asks. Do NOT engage otherwise.\n---\n"
               + self.good.split("---", 2)[2])
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_TRIGGER, _lint_ids(findings))
        self.assertTrue(any("engage trigger" in f.message for f in findings))

    def test_trigger_lint_fails_on_missing_negative(self):
        bad = ("---\nname: spec\ndescription: x\n"
               "when_to_use: Engage when the user asks, when they request it, or when it fits.\n"
               "---\n" + self.good.split("---", 2)[2])
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_TRIGGER, _lint_ids(findings))
        self.assertTrue(any("negative trigger" in f.message for f in findings))

    def test_anatomy_lint_fails_on_missing_rationalizations(self):
        bad = self.good.replace("## Rationalizations", "## (rationalizations removed)")
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_ANATOMY, _lint_ids(findings))

    def test_anatomy_lint_fails_on_missing_verification(self):
        bad = self.good.replace("## Verification", "## (verification removed)")
        findings = run_lints({"spec": bad})
        self.assertIn(LINT_ANATOMY, _lint_ids(findings))


class TestValidatorOwnedAntiSelfExempt(unittest.TestCase):
    """A skill can NEVER exempt itself: a self-exempt in its frontmatter is ignored (the defect
    is still reported) AND flagged. Exemptions live only in the validator-owned EXEMPTIONS."""

    def setUp(self):
        self.good = load_shipped_skill_texts(("spec",))["spec"]

    def test_self_exempt_key_is_rejected_and_does_not_waive_the_defect(self):
        # A skill with a real defect (no Contract) that ALSO plants a self-exemption for it.
        broken = self.good.replace("## Contract", "## (contract removed)")
        planted = _inject_frontmatter_key(broken, "lint_exempt: contract-lint")
        findings = run_lints({"spec": planted})
        ids = _lint_ids(findings)
        # the exemption did NOT suppress the real defect ...
        self.assertIn(LINT_CONTRACT, ids)
        # ... and the self-exempt attempt is itself flagged.
        self.assertIn(LINT_SELF_EXEMPT, ids)

    def test_only_validator_owned_exemptions_waive_a_lint(self):
        broken = self.good.replace("## Contract", "## (contract removed)")
        # Without a code-level exemption -> the defect is reported.
        self.assertIn(LINT_CONTRACT, _lint_ids(run_lints({"spec": broken})))
        # WITH a validator-owned exemption passed in code -> waived (this is the only channel).
        waived = {LINT_CONTRACT: frozenset({"spec"}), LINT_TRIGGER: frozenset(),
                  LINT_ANATOMY: frozenset()}
        self.assertNotIn(LINT_CONTRACT, _lint_ids(run_lints({"spec": broken}, exemptions=waived)))

    def test_shipped_default_exemptions_are_empty(self):
        # Every curated skill is expected to satisfy every lint — no skill is exempted by default.
        for lint in (LINT_CONTRACT, LINT_TRIGGER, LINT_ANATOMY):
            self.assertEqual(EXEMPTIONS[lint], frozenset())


class TestAddingASkillExtendsTheExpectedSet(unittest.TestCase):
    """Simulate DK.S5's `docsync` (15->16): adding a name makes the lint EXPECT a SKILL.md for
    it. Because the expected set is derived from CURATED_SKILLS, this is a one-line change
    there — no count constant to bump — and a missing file then fails the lint."""

    def test_a_new_curated_name_without_a_file_fails_the_lint(self):
        # docsync is now shipped (DK.S5); use a name that is NOT shipped to exercise the loader's
        # "curated name, no SKILL.md yet → fail" path.
        new_name = "not-a-shipped-skill"
        extended = tuple(CURATED_SKILLS) + (new_name,)
        texts = load_shipped_skill_texts(extended)
        self.assertIn(new_name, texts)           # the loader picked up the new name
        self.assertEqual(texts[new_name], "")    # no shipped file yet
        findings = run_lints(texts)
        self.assertTrue(any(f.skill == new_name for f in findings),
                        "an added curated skill with no SKILL.md must fail the lint")


class TestProactiveDispatch(unittest.TestCase):
    def test_every_curated_skill_auto_fires_with_enough_triggers(self):
        for name, text in load_shipped_skill_texts().items():
            with self.subTest(skill=name):
                wtu = (parse_frontmatter(text).get("when_to_use") or "").strip()
                self.assertTrue(wtu, f"{name} must declare when_to_use (auto-fire trigger)")
                count, has_negative = analyze_triggers(wtu)
                self.assertGreaterEqual(count, MIN_TRIGGERS, f"{name}: {count} triggers")
                self.assertTrue(has_negative, f"{name}: missing a negative trigger")

    def test_nudge_present_in_always_on_rules_within_60_line_cap(self):
        from mokata.govern.rules import always_on_rules
        rs = always_on_rules()
        self.assertTrue(rs.within_cap, f"always-on rules exceed the 60-line cap ({rs.line_count})")
        self.assertLessEqual(rs.line_count, 60)
        body = "\n".join(rs.lines).lower()
        self.assertIn("proactive dispatch", body)
        self.assertIn("auto-engage the matching mokata skill", body)

    def test_nudge_present_in_bootstrap_within_2k_budget(self):
        from mokata.config import Surface
        from mokata.bootstrap import build_bootstrap, BOOTSTRAP_TOKEN_BUDGET
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            surface = Surface.load(d)
            result = build_bootstrap(surface)
        self.assertTrue(result.within_budget,
                        f"bootstrap {result.token_estimate} > {BOOTSTRAP_TOKEN_BUDGET}")
        self.assertLessEqual(result.token_estimate, BOOTSTRAP_TOKEN_BUDGET)
        self.assertIn("Skills: mokata", result.text)
        self.assertIn("⛭", result.text)


if __name__ == "__main__":
    unittest.main()
