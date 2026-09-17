"""A green suite proves the tests agree with the code. It never proves either matches the AC.

0.0.20, from **OSS #65** — *"Test and reviewer are missing bugs occasionally"*, reported with the
diagnosis already done:

    "Every test validated the transformer functions in isolation — my AC4 test asserted
     errors['ABC123456'] is defined, which passed, because the transformer really populates it.
     Nothing crossed the transformer→pipeline-core seam where outputFormatter[0].length is the real
     contract ... No test fed adversarial keys or a 0 value. The reviewer re-ran my tests + read the
     diff, so it inherited the same blind spots."

⛔ **TWO DEFECTS, AND ONE OF THEM WAS MOKATA TELLING THE AGENT TO DO IT.**

  1. **THE SEAM.** The `test` skill said *"test ONLY the approved acceptance criteria"* and its
     rationalization table answered *"I'll add a couple of extra cases I think matter"* with
     *"unapproved coverage is scope creep."* An agent obeying that writes ONE comfortable case per
     AC, at the producer, and stops. But an AC is a promise to whoever READS the value: proving the
     producer populated a field, while the consumer reads a different field of it, is a green test
     about nothing. ⭐ And a boundary value — empty, **zero**, absent-vs-present-and-empty, a
     hostile key — does not change what is REQUIRED. It is the approved AC evaluated over its own
     input domain rather than at one point, and calling that scope creep is the rule that produced
     the bug.

  2. **THE REVIEWER INHERITED THE GAP.** `review` already forbade builder CLAIMS and ran the
     reviewer as a fresh-context subagent — and then told it to re-derive correctness from *"your
     own test run"*. The suite IS the builder's claim, in executable form. Running it answers a
     question about the tests; it cannot answer the two questions the tests are wrong about. This
     repo has its own name for the shape: **a property defended twice is a property no mutant can
     grade** — here the second defence reads its evidence off the first.

WHAT THIS FILE GRADES
---------------------
The subject is PROMPT TEXT, so the honest assertions are about the CONCEPTS being required and
about them REACHING EVERY SURFACE — never about wording, which no one could ship past:

  * the `test` skill requires a consumer-side assertion AND boundary inputs, naming the falsy case;
  * the `review` skill says the suite is a claim to be judged, not evidence to be inherited;
  * the rationalization that forbade this now DISTINGUISHES new behaviour from the same AC at its
    boundary — the failure was the ANSWER, so the answer is what is pinned;
  * every one of those reaches BOTH mirrors. ⚠ `skills.py` is the source and the agent never reads
    it: a change that stops at the registry hands the running agent the OLD instruction, which is
    the drift `test_handoff_g1` grades structurally and this grades by CONTENT.

Pure/offline; dependency-free; deterministic.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.skill_anatomy import ANATOMY
from mokata.skills import get_skill

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "src", "mokata")


def _surfaces(name):
    """The THREE places one skill's instruction lives, as `{label: text}`.

    ⚠ The registry is the source and the agent never sees it. The command template is what
    `/mokata:<name>` expands; the SKILL.md is what the model auto-engages. A property present in
    one and absent from another is not a formatting difference — it is two different instructions
    shipping under one name."""
    out = {"registry": get_skill(name).prompt}
    for label, path in (
        ("command-template", os.path.join(PKG, "templates", "commands", "%s.md" % name)),
        ("agent-skill", os.path.join(PKG, "skills", name, "SKILL.md")),
    ):
        with open(path, "r", encoding="utf-8") as fh:
            out[label] = fh.read()
    return out


#: The CONCEPTS, not the sentences. Each is a short anchor that cannot be satisfied by accident and
#: that a rewrite of the surrounding prose would keep — which is the line between a gate on meaning
#: and a gate on wording.
_TEST_REQUIREMENTS = {
    "the consumer is named as the place to assert": "AS THE CONSUMER READS IT",
    "the producer-only test is called out as proving nothing": "passes and proves nothing",
    "the falsy-but-present value is named": "FALSY-BUT-PRESENT",
    "a hostile key is named": "hostile key",
    "boundary work is declared NOT to be scope creep": "not extra scope",
}

_REVIEW_REQUIREMENTS = {
    "the suite is named as the builder's artefact": "THE SUITE IS THE BUILDER'S ARTEFACT TOO",
    "a green run is denied as evidence": "never that either matches the AC",
    "the reviewer must open a consumer": "TRACE THE CONSUMER",
    "the reviewer must name an unfed input": "NAME THE UNFED INPUT",
    "the brief hands the suite over as a claim": "in executable form",
}


class TheTestSkillRequiresTheSeamAndTheBoundary(unittest.TestCase):

    def test_every_requirement_reaches_EVERY_surface(self):
        surfaces = _surfaces("test")
        self.assertEqual(len(surfaces), 3, "a surface disappeared — this grades a subset")
        for why, anchor in _TEST_REQUIREMENTS.items():
            for label, text in surfaces.items():
                with self.subTest(requirement=why, surface=label):
                    self.assertIn(anchor, text,
                                  "%s does not say it on the %s surface, so an agent reading that "
                                  "one is told the pre-#65 instruction" % (why, label))

    def test_the_ONLY_the_approved_ACs_rule_is_STILL_THERE(self):
        """⛔ The control, and it is the whole reason this fix is narrow. Scope discipline is not
        being relaxed: inventing an AC is still forbidden. What changed is that the APPROVED AC's
        own input domain stopped counting as an invention. A version of this fix that deleted the
        scope rule would pass every assertion above and break the thing that makes specs provable."""
        prompt = get_skill("test").prompt
        self.assertIn("Test ONLY the approved acceptance criteria", prompt)
        self.assertIn("do not invent", prompt)

    def test_a_case_that_changes_what_is_REQUIRED_still_needs_an_amendment(self):
        """The line between the two, stated in the prompt rather than left to judgement."""
        self.assertIn("is a new AC and still needs an amendment", get_skill("test").prompt)


class TheRationalizationNoLongerForbidsTheFix(unittest.TestCase):
    """⭐ THE ANSWER WAS THE DEFECT. The row is meant to stop invented behaviour; as written it
    read as 'one case per AC and stop', and that is the sentence an agent obeyed into #65."""

    def _answers(self, skill):
        return {r.excuse: r.reality for r in ANATOMY[skill].rationalizations}

    def test_the_extra_cases_answer_now_distinguishes_BEHAVIOUR_from_the_BOUNDARY(self):
        answers = self._answers("test")
        row = next(v for k, v in answers.items() if "extra cases" in k)
        self.assertIn("New BEHAVIOUR is scope creep", row, "the scope rule was lost, not narrowed")
        self.assertIn("not extra scope", row,
                      "the answer still reads as 'one case per AC and stop' — the #65 instruction")

    def test_the_PRODUCER_excuse_is_named_as_an_excuse(self):
        """#65's exact reasoning — *the transformer really populates it* — filed where the agent
        meets it, because a rationalization it can recognise is worth more than a rule it forgets."""
        answers = self._answers("test")
        row = next((v for k, v in answers.items() if "transformer populates" in k), None)
        self.assertIsNotNone(row, "the producer-is-enough excuse is not in the table")
        self.assertIn("PRODUCER", row)

    def test_the_reviewer_has_a_row_for_TRUSTING_THE_SUITE(self):
        answers = self._answers("review")
        row = next((v for k, v in answers.items() if "tests pass" in k), None)
        self.assertIsNotNone(row, "the reviewer has no excuse row for inheriting the suite")
        self.assertIn("builder's claim", row)


class TheVerificationChecklistsAskForBoth(unittest.TestCase):
    """A rule with no closing check is a rule the phase can finish without meeting."""

    def test_the_test_phase_must_confirm_the_consumer_and_the_boundary(self):
        checks = " | ".join(ANATOMY["test"].verification)
        self.assertIn("CONSUMER", checks)
        self.assertIn("boundary of its input domain", checks)

    def test_the_review_phase_must_confirm_it_JUDGED_the_suite(self):
        checks = " | ".join(ANATOMY["review"].verification)
        self.assertIn("JUDGED rather than trusted", checks)
        self.assertIn("real CONSUMER was opened", checks)
        self.assertIn("input class the tests never feed", checks)

    def test_the_old_checks_SURVIVED(self):
        """⛔ Anti-regression in the other direction: a checklist that swapped its items for new
        ones would satisfy every assertion above while dropping RED-before-GREEN."""
        checks = " | ".join(ANATOMY["test"].verification)
        self.assertIn("shown FAILING (RED)", checks)
        self.assertIn("maps 1:1 to an approved acceptance criterion", checks)


class TheReviewerStopsInheritingTheSuite(unittest.TestCase):

    def test_every_requirement_reaches_EVERY_surface(self):
        surfaces = _surfaces("review")
        for why, anchor in _REVIEW_REQUIREMENTS.items():
            for label, text in surfaces.items():
                with self.subTest(requirement=why, surface=label):
                    self.assertIn(anchor, text,
                                  "%s is missing from the %s surface" % (why, label))

    def test_INDEPENDENCE_ITSELF_IS_UNTOUCHED(self):
        """⛔ The control. The fresh-context subagent and the no-builder-claims rule were already
        right and are the half #65 did NOT fault — a fix that traded them for the new clauses would
        pass this file and lose more than it gained."""
        prompt = get_skill("review").prompt
        # ⚠ "by DEFAULT", not just the words "fresh-context subagent". Mutant C02 rewrote the
        # opening sentence to *"run this review inline, reusing your own context"* and SURVIVED,
        # because the next sentence still contained the noun phrase this pin was looking for. The
        # property is that independence is the DEFAULT; a pin on the vocabulary is not that pin.
        self.assertIn("INDEPENDENTLY by default", prompt)
        self.assertIn("FRESH-CONTEXT subagent", prompt)
        self.assertIn("NO builder conclusions or claims", prompt)
        self.assertIn("not ratify yours", prompt)
        self.assertNotIn("reusing your own context", prompt)


if __name__ == "__main__":
    unittest.main()
