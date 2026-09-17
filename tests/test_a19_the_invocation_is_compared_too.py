"""The preflight and CI are compared on HOW they invoke the suite, not only on what they install.

0.0.20 stage 10b, `PREFLIGHT-VS-CI-INVOCATION-PARITY`. `test_s27_preflight_parity` derives the
dependency set `run_test_preflight` installs from `ci.yml` and reds when they part — and it derives
**nothing about the INVOCATION**. The row: *"a parity check that compares one axis of two commands
and is silent about the other."*

⛔ THE ROW ALSO FORBIDS THE OBVIOUS FIX. *"DO NOT close this by adding `< /dev/null` to `ci.yml`'s
`run:` line — that is the exact edit PR #54 made, and it produced 28 failures."* So the property
here is deliberately **not** "the two commands are identical":

    SELECTION     -s / -t / -p / -k   decide WHICH TESTS RUN.  Must MATCH — no reason excuses it.
    ENVIRONMENT   stdin               decides the CONDITIONS.  May differ, must be DECLARED.

⭐ AND THE DECLARATION IS HELD IN BOTH DIRECTIONS. An undeclared divergence reds, and a declaration
with no divergence behind it reds too — a register that outlives its entry is a lie that makes the
next reader trust the whole list less (the SI.6 rule), and it is how the *install*-set half of this
pin nearly went stale.

§7i — THE REAL TREE IS GREEN, SO THE PLANTS DO THE GRADING. `TheOffenderIsSupplied` hands `resolve`
a preflight text whose `-p` differs, one whose `-k` differs, one that runs a suite CI never runs,
and one whose stdin diverges on an axis nobody declared — and requires RED from each.

§7c — `scripts/release.sh` IS INTERNAL AND THIS FILE SHIPS. The real-tree assertions sit behind a
class DECORATOR (never a `setUpClass` skip), and a companion holds the private tree to having the
file at all, so a guard cannot hide a deletion.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _invocation_parity as ip
import _preflight_parity as pp


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")

_UNIT = "python -m unittest discover -s tests -t tests"


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class TheAxesAreSplitDeliberately(unittest.TestCase):
    """The split IS the design, so it is asserted rather than left to the reader."""

    def test_selection_and_environment_axes_do_not_overlap(self):
        self.assertFalse(set(ip.SELECTION_AXES) & set(ip.ENVIRONMENT_AXES))

    def test_every_declared_divergence_names_an_ENVIRONMENT_axis(self):
        """⛔ A selection axis must never be declarable. `-p` deciding which tests run is not a
        difference anybody gets to justify."""
        for axis in ip.DECLARED_DIVERGENCES:
            self.assertIn(axis, ip.ENVIRONMENT_AXES)

    def test_every_declaration_carries_a_REASON_and_not_a_note(self):
        for axis, reason in ip.DECLARED_DIVERGENCES.items():
            self.assertGreater(len(reason), 120,
                               "%r's declaration is a note, not a reason" % (axis,))

    def test_the_forbidden_fix_is_named_in_the_declaration(self):
        """⭐ The row's own warning lives where someone about to make the edit will read it."""
        self.assertIn("PR #54", ip.DECLARED_DIVERGENCES["stdin"])

    def test_the_UNCOMPARED_axes_are_NAMED_rather_than_omitted(self):
        """§7h. A gap that is written down is a decision; a gap that is not is a defect."""
        self.assertIn("env", ip.UNCOMPARED_AXES)
        self.assertNotIn("env", ip.SELECTION_AXES + ip.ENVIRONMENT_AXES)


class TheParserReadsTheAxes(unittest.TestCase):

    def test_an_absent_top_level_defaults_to_the_source(self):
        """⛔ 'not passed' and 'passed the same value' must not read as a divergence."""
        inv = ip.invocations("python -m unittest discover -s tests", "x")[0]
        self.assertEqual(inv.top_level, "tests")

    def test_an_absent_pattern_defaults_to_unittests_own(self):
        inv = ip.invocations(_UNIT, "x")[0]
        self.assertEqual(inv.pattern, ip.DEFAULT_PATTERN)
        explicit = ip.invocations(_UNIT + ' -p "test*.py"', "x")[0]
        self.assertEqual(inv.selection, explicit.selection)

    def test_a_quoted_pattern_loses_its_quotes(self):
        self.assertEqual(ip.invocations(_UNIT + ' -p "test_a*.py"', "x")[0].pattern, "test_a*.py")
        self.assertEqual(ip.invocations(_UNIT + " -p 'test_a*.py'", "x")[0].pattern, "test_a*.py")

    def test_the_stdin_redirect_is_read(self):
        self.assertEqual(ip.invocations(_UNIT, "x")[0].stdin, "inherited")
        self.assertEqual(ip.invocations(_UNIT + " < /dev/null", "x")[0].stdin, "/dev/null")

    def test_a_COMMENTED_command_is_not_an_invocation(self):
        """⚠ Load-bearing, not hygiene, and the first version of this test did not prove it.

        A comment on its OWN line cannot leak into a later command — the tail regex stops at the
        newline — so a comment merely MENTIONING the redirect changes nothing, and the first cut of
        this test asserted exactly that and let `A04` survive. What the stripping is actually for
        is a comment that QUOTES A WHOLE COMMAND: `release.sh` carries several, including one at
        line 382 naming *"every release.sh function that runs `unittest discover`"*. A reader that
        kept them would count a command no shell ever runs, and its axes would be compared against
        CI as though they were real.
        """
        commented = "#   %s -p \"test_old*.py\" < /dev/null\n%s" % (_UNIT, _UNIT)
        found = ip.invocations(commented, "x")
        self.assertEqual(len(found), 1,
                         "a command inside a comment was parsed as an invocation: %r" % (found,))
        self.assertEqual(found[0].pattern, ip.DEFAULT_PATTERN)
        self.assertEqual(found[0].stdin, "inherited")

    def test_an_INDENTED_comment_is_still_a_comment(self):
        """Shell comments inside a function body are indented, which is where they all are."""
        self.assertEqual(ip.invocations("    # %s < /dev/null\n" % _UNIT, "x"), ())

    def test_a_line_that_runs_no_discover_yields_nothing(self):
        self.assertEqual(ip.invocations("python -m pytest tests", "x"), ())


class TheOffenderIsSupplied(unittest.TestCase):
    """§7i. The real tree is GREEN, so every claim about catching drift rests on these."""

    CI = ("jobs:\n  test:\n    steps:\n      - run: %s\n" % _UNIT)

    def test_a_matching_pair_with_a_DECLARED_divergence_is_GREEN(self):
        res = ip.resolve(_UNIT + " < /dev/null", self.CI)
        self.assertEqual(res.verdict, ip.GREEN, res.render())

    def test_an_UNDECLARED_environment_divergence_is_RED(self):
        res = ip.resolve(_UNIT + " < /dev/null", self.CI, declared={})
        self.assertEqual(res.verdict, ip.RED, res.render())
        self.assertEqual([d.axis for d in res.undeclared], ["stdin"])
        self.assertIn("UNDECLARED DIVERGENCE", res.render())

    def test_a_PATTERN_divergence_is_RED_and_no_declaration_can_excuse_it(self):
        res = ip.resolve(_UNIT + ' -p "test_a*.py"', self.CI,
                         declared={"pattern": "someone tried to justify this"})
        self.assertEqual(res.verdict, ip.RED, res.render())
        self.assertIn("NO COUNTERPART", res.render())

    def test_a_K_FILTER_divergence_is_RED(self):
        res = ip.resolve(_UNIT + " -k test_something", self.CI)
        self.assertEqual(res.verdict, ip.RED, res.render())

    def test_a_suite_CI_NEVER_RUNS_is_RED(self):
        res = ip.resolve("python -m unittest discover -s tests/extra -t tests/extra", self.CI)
        self.assertEqual(res.verdict, ip.RED, res.render())
        self.assertIn("different suite than CI ever runs", res.render())

    def test_and_removing_the_offender_makes_it_GREEN(self):
        """The other direction — a checker that only ever reds grades nothing either."""
        self.assertEqual(ip.resolve(_UNIT, self.CI).verdict, ip.GREEN)

    def test_a_STALE_declaration_is_reported(self):
        stale = ip.stale_declarations(_UNIT, self.CI, declared={"stdin": "no longer true"})
        self.assertEqual(stale, ("stdin",))

    def test_a_LIVE_declaration_is_not_reported_as_stale(self):
        self.assertEqual(
            ip.stale_declarations(_UNIT + " < /dev/null", self.CI, declared={"stdin": "x"}), ())


class TheVacuousCasesAreUNDECIDABLE(unittest.TestCase):
    """§7g. Both empty sides make the comparison vacuously TRUE, and true is not the same as pass."""

    def test_a_preflight_that_runs_nothing_is_UNDECIDABLE(self):
        res = ip.resolve("echo hello", "jobs:\n  test:\n    steps:\n      - run: %s\n" % _UNIT)
        self.assertEqual(res.verdict, ip.UNDECIDABLE)
        self.assertIn("vacuously true", res.detail)
        self.assertIn("NOT a pass", res.render())

    def test_a_ci_that_runs_nothing_is_UNDECIDABLE(self):
        res = ip.resolve(_UNIT, "jobs: {}")
        self.assertEqual(res.verdict, ip.UNDECIDABLE)
        self.assertIn("NOT a pass", res.render())

    def test_UNDECIDABLE_is_not_GREEN_and_not_RED(self):
        self.assertNotIn(ip.UNDECIDABLE, (ip.GREEN, ip.RED))

    def test_stale_declarations_says_nothing_when_the_comparison_did_not_happen(self):
        self.assertEqual(ip.stale_declarations("echo hi", "jobs: {}", declared={"stdin": "x"}), ())


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "scripts/release.sh is dev-only, excluded from the public mirror")
class TheRealTreeAgrees(unittest.TestCase):
    """The live assertion. ⚠ It grades the REPO; the mechanism is graded by the plants above."""

    def setUp(self):
        self.body = pp.preflight_body(_read(RELEASE_SH))
        self.ci = _read(CI_YML)

    def test_the_preflight_body_was_actually_FOUND(self):
        """⛔ Anti-vacuity: an empty body makes everything below UNDECIDABLE-shaped."""
        self.assertIn("unittest discover", self.body)

    def test_the_real_invocations_AGREE(self):
        res = ip.resolve(self.body, self.ci)
        self.assertEqual(res.verdict, ip.GREEN, res.render())

    def test_the_stdin_divergence_is_REAL_and_is_the_declared_one(self):
        """⭐ The declaration must describe something that is actually happening, or it is a
        comment. This is the entry `test_a_STALE_declaration_is_reported` protects."""
        res = ip.resolve(self.body, self.ci)
        self.assertEqual([d.axis for d in res.divergences], ["stdin"] * len(res.divergences))
        self.assertTrue(res.divergences, "no divergence exists, so the declaration is stale")

    def test_no_declaration_is_STALE(self):
        self.assertEqual(ip.stale_declarations(self.body, self.ci), ())

    def test_both_of_the_preflights_suites_have_a_counterpart(self):
        res = ip.resolve(self.body, self.ci)
        sources = sorted({i.source for i, _c in res.pairs})
        self.assertEqual(sources, ["tests", "tests/integration"],
                         "the preflight runs a suite ci.yml does not, or one of them stopped "
                         "being compared")


class TheMirrorBoundaryIsWhyThatClassSkips(unittest.TestCase):
    """§7g wearing a skip: a guard that hides a DELETION is not a guard."""

    def test_release_sh_is_absent_ONLY_because_of_the_mirror_boundary(self):
        import _internal_subject as isub
        state = isub.subject_state(ROOT, ("scripts/release.sh",))
        self.assertIn(state, (isub.GRADED, isub.ABSENT_BY_DESIGN), state)

    def test_ci_yml_SHIPS_and_is_therefore_never_guarded(self):
        self.assertTrue(os.path.exists(CI_YML),
                        "ci.yml is public and present in every checkout; if this fails the file "
                        "was deleted, not excluded")


if __name__ == "__main__":
    unittest.main()
