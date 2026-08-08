"""Stage 23 — SAST-SCORE-REGRESSION: the per-check differ, and what it must refuse to say.

THE EVENT THIS EXISTS FOR. Between 2026-06-30 and 2026-08-04 mokata-oss's Scorecard aggregate
went 5.5 -> 6.2 -> 6.6 and three consecutive readings called that pure progress. Across the same
window `SAST` went 10 -> 7 -> 8 and NOBODY NOTICED, because every reading compared the headline
number. The row's own remedy is the whole point of this module: the post-cut step must diff
PER CHECK, not compare aggregates.

FOUR THINGS ARE PINNED HERE, and three of them are §7g:

  1. NO PREVIOUS READING and NO CHANGE SINCE THE PREVIOUS READING must not share a
     representation. A first run has no delta; it must SAY so and must not render zeros. They get
     different statuses, different exit codes, and different text — checked pairwise, because a
     representation is only "split" if nothing collapses it back.

  2. A check Scorecard could not score (`-1`) is an ABSENT answer, not a score of minus one.
     10 -> -1 is not a drop of eleven, and -1 -> 10 is not a rise of eleven. Both are changes of
     KIND, and only one of them is a regression (we stopped being able to measure).

  3. A reason we never wrote down is not a reason. The 2026-06-30 baseline lives in doc 42 as
     prose — it records SAST 10 and does NOT record why. Comparing that against a real reason
     string would manufacture a "reason changed" finding out of our own missing notes.

  4. §7i — A DIFFER OVER ONE STORED READING GRADES NOTHING. Every assertion below runs over a
     SUPPLIED pair. Two of the fixtures are real readings and their diff is the actual historical
     regression: the aggregate ROSE 5.5 -> 6.6 while SAST DROPPED 10 -> 8, and the differ must go
     red on it. The rest are planted corpora, because a clean tree proves nothing.
"""

import json
import os
import subprocess
import sys
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import scorecard_delta as SD  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_JSON = os.path.join(HERE, "_scorecard_reading_2026-06-30.json")
CURRENT_JSON = os.path.join(HERE, "_scorecard_reading_2026-08-04.json")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return SD.parse_reading(json.load(fh))


def reading(checks, aggregate=5.0, commit="aaaaaaa", date="2026-01-01T00:00:00Z"):
    """Build a reading from {name: (score, reason)} — the planted-corpus constructor (§7i)."""
    return SD.parse_reading(
        {
            "repo": {"name": "github.com/planted/corpus", "commit": commit},
            "date": date,
            "score": aggregate,
            "checks": [
                {"name": n, "score": s, "reason": r} for n, (s, r) in checks.items()
            ],
        }
    )


# =================================================================================================
# 1 · §7g — "no previous reading" and "no change" must not share a representation
# =================================================================================================


class TestAbsentDeltaIsNotAnEmptyDelta(unittest.TestCase):
    def setUp(self):
        self.one = reading({"SAST": (8, "detected but not run on all commits")})
        self.absent = SD.diff(None, self.one)
        self.unchanged = SD.diff(self.one, self.one)

    def test_a_first_run_reports_NO_PREVIOUS_not_no_change(self):
        self.assertEqual(SD.NO_PREVIOUS, self.absent.status)
        self.assertEqual(SD.NO_CHANGE, self.unchanged.status)

    def test_the_two_do_not_collapse_on_ANY_observable(self):
        # A split representation that any single accessor collapses is not split. Check every
        # channel a caller could dispatch on.
        self.assertNotEqual(self.absent.status, self.unchanged.status)
        self.assertNotEqual(self.absent.exit_code, self.unchanged.exit_code)
        self.assertNotEqual(SD.render(self.absent), SD.render(self.unchanged))

    def test_NO_PREVIOUS_gets_its_own_exit_code_distinct_from_pass_and_from_fail(self):
        # mutate.sh's 0-vs-6 split, applied here: "not compared" is not "compared and fine".
        self.assertEqual(SD.EXIT_NOT_COMPARED, self.absent.exit_code)
        self.assertNotEqual(SD.EXIT_OK, SD.EXIT_NOT_COMPARED)
        self.assertNotEqual(SD.EXIT_REGRESSION, SD.EXIT_NOT_COMPARED)

    def test_NO_PREVIOUS_says_so_in_words_and_never_prints_a_zero_delta(self):
        text = SD.render(self.absent)
        self.assertIn("NO PREVIOUS READING", text)
        self.assertIn("no delta", text.lower())
        # The failure this pin exists for: a first run printing a tidy "0 changed, 0 dropped"
        # table that reads exactly like a clean comparison.
        self.assertNotIn("0 dropped", text)
        self.assertNotIn("unchanged", text.lower())

    def test_NO_PREVIOUS_is_not_a_regression_but_is_not_a_pass_either(self):
        self.assertFalse(self.absent.is_regression)
        self.assertEqual((), self.absent.changes)
        self.assertFalse(self.absent.compared)
        self.assertTrue(self.unchanged.compared)

    def test_a_NO_PREVIOUS_delta_carrying_changes_is_unconstructible(self):
        # The status is DERIVED, never asserted beside the data it describes. Two fields that can
        # disagree eventually do — that is how a written status column drifts from its subject.
        with self.assertRaises(ValueError):
            SD.Delta(
                status=SD.NO_PREVIOUS,
                changes=(SD.CheckChange("SAST", SD.DROP, 10, 8, None, None),),
                previous=None,
                current=self.one,
            )


# =================================================================================================
# 2 · The whole feature: a per-check DROP is red no matter what the aggregate did
# =================================================================================================


class TestTheRealRegression(unittest.TestCase):
    """The two REAL fixtures. This diff is the event the row was filed for."""

    def setUp(self):
        self.baseline = load(BASELINE_JSON)   # 2026-06-30, aggregate 5.5, SAST 10
        self.current = load(CURRENT_JSON)     # 2026-08-04, aggregate 6.6, SAST 8
        self.delta = SD.diff(self.baseline, self.current)

    def test_the_fixtures_are_the_readings_they_claim_to_be(self):
        self.assertEqual(5.5, self.baseline.aggregate)
        self.assertEqual(6.6, self.current.aggregate)
        self.assertEqual(10, self.baseline.checks["SAST"].score)
        self.assertEqual(8, self.current.checks["SAST"].score)
        self.assertEqual("cea79a9", self.current.commit[:7])

    def test_the_aggregate_ROSE_across_the_window(self):
        # Stated as an assertion because it is the trap: every reading that missed this
        # regression missed it by looking here and stopping.
        self.assertGreater(self.current.aggregate, self.baseline.aggregate)

    def test_SAST_is_reported_as_a_DROP(self):
        drops = {c.name: c for c in self.delta.changes if c.kind == SD.DROP}
        self.assertIn("SAST", drops)
        self.assertEqual(10, drops["SAST"].before_score)
        self.assertEqual(8, drops["SAST"].after_score)

    def test_a_drop_is_a_regression_even_though_the_aggregate_rose(self):
        self.assertTrue(self.delta.is_regression)
        self.assertEqual(SD.EXIT_REGRESSION, self.delta.exit_code)
        self.assertEqual(["SAST"], [c.name for c in self.delta.regressions])

    def test_the_rendered_report_names_the_dropped_check_and_both_scores(self):
        text = SD.render(self.delta)
        self.assertIn("SAST", text)
        self.assertIn("10", text)
        self.assertIn("8", text)
        self.assertIn("REGRESSION", text)

    def test_the_drop_is_reported_FIRST_and_is_never_buried_under_the_noise(self):
        # Rendered alphabetically, SAST lands fourteenth of eighteen, between two reason-string
        # notes. A report that buries the one line it exists to surface is this row's own defect
        # in a new costume.
        self.assertEqual("SAST", self.delta.changes[0].name)
        text = SD.render(self.delta)
        self.assertLess(text.index("SAST"), text.index("Binary-Artifacts"))

    def test_every_change_kind_has_a_reporting_rank(self):
        # An unranked kind would sort as a KeyError at render time, on the one run that finally
        # had something new to say.
        self.assertEqual(set(SD._REGRESSION_OF), set(SD._KIND_RANK))

    def test_the_rises_are_reported_and_do_not_offset_the_drop(self):
        rises = {c.name for c in self.delta.changes if c.kind == SD.RISE}
        self.assertIn("Pinned-Dependencies", rises)   # 0 -> 6
        self.assertIn("Token-Permissions", rises)     # 0 -> 10
        self.assertTrue(self.delta.is_regression)


# =================================================================================================
# 3 · §7g in the instrument — Scorecard's -1 is an absent answer, not a score
# =================================================================================================


class TestUnscoredIsNotAScore(unittest.TestCase):
    def test_minus_one_parses_as_UNSCORED_with_no_number_at_all(self):
        r = reading({"Branch-Protection": (-1, "Resource not accessible by integration")})
        c = r.checks["Branch-Protection"]
        self.assertEqual(SD.UNSCORED, c.state)
        self.assertIsNone(c.score)

    def test_losing_the_ability_to_measure_is_a_regression_not_a_ten_point_drop(self):
        before = reading({"SAST": (10, "detected and run on all commits")})
        after = reading({"SAST": (-1, "internal error")})
        d = SD.diff(before, after)
        kinds = {c.name: c.kind for c in d.changes}
        self.assertEqual(SD.BECAME_UNSCORED, kinds["SAST"])
        self.assertNotIn(SD.DROP, kinds.values())   # NOT arithmetic on a sentinel
        self.assertTrue(d.is_regression)

    def test_becoming_measurable_is_reported_and_is_NOT_a_regression(self):
        # The real Branch-Protection/CI-Tests/Packaging/Signed-Releases transition across the
        # fixture window. A check that starts scoring is news, not a fault.
        before = reading({"CI-Tests": (-1, "no pull request found")})
        after = reading({"CI-Tests": (10, "16 out of 16 merged PRs checked by a CI test")})
        d = SD.diff(before, after)
        self.assertEqual([SD.BECAME_SCORED], [c.kind for c in d.changes])
        self.assertFalse(d.is_regression)

    def test_unscored_to_unscored_is_no_change_when_the_reason_holds(self):
        r = reading({"Packaging": (-1, "no published package detected")})
        self.assertEqual(SD.NO_CHANGE, SD.diff(r, r).status)

    def test_the_real_fixtures_carry_four_became_scored_checks(self):
        d = SD.diff(load(BASELINE_JSON), load(CURRENT_JSON))
        became = {c.name for c in d.changes if c.kind == SD.BECAME_SCORED}
        self.assertEqual(
            {"Branch-Protection", "CI-Tests", "Packaging", "Signed-Releases"}, became
        )


# =================================================================================================
# 4 · The reason string — what caught this row, and what we must not invent
# =================================================================================================


class TestReasonStrings(unittest.TestCase):
    def test_a_reason_only_change_is_surfaced_and_does_NOT_fail(self):
        before = reading({"SAST": (8, "SAST tool detected but not run on all commits")})
        after = reading({"SAST": (8, "SAST tool is not run on all commits")})
        d = SD.diff(before, after)
        self.assertEqual([SD.REASON_CHANGED], [c.kind for c in d.changes])
        self.assertFalse(d.is_regression)
        self.assertEqual(SD.EXIT_OK, d.exit_code)
        self.assertIn("SAST", SD.render(d))

    def test_a_score_that_moves_under_an_UNCHANGED_reason_is_still_reported(self):
        # THE ROW'S OWN SIGNATURE: 7 -> 8 with a byte-identical reason string. The reason not
        # moving is the evidence that the cause did not change.
        s = "SAST tool detected but not run on all commits"
        d = SD.diff(reading({"SAST": (7, s)}), reading({"SAST": (8, s)}))
        (change,) = d.changes
        self.assertEqual(SD.RISE, change.kind)
        self.assertEqual(change.before_reason, change.after_reason)
        self.assertIn("reason unchanged", SD.render(d).lower())

    def test_an_UNRECORDED_reason_never_manufactures_a_reason_changed_finding(self):
        # doc 42's baseline says "already 10/10" and does not say why. A null is our missing
        # note, not Scorecard's answer.
        before = reading({"License": (10, None)})
        after = reading({"License": (10, "license file detected")})
        d = SD.diff(before, after)
        self.assertEqual([SD.REASON_INCOMPARABLE], [c.kind for c in d.changes])
        self.assertFalse(d.is_regression)

    def test_an_unrecorded_reason_is_a_distinct_state_from_an_empty_one(self):
        self.assertEqual(SD.REASON_UNRECORDED, reading({"X": (1, None)}).checks["X"].reason_state)
        self.assertEqual(SD.REASON_RECORDED, reading({"X": (1, "")}).checks["X"].reason_state)

    def test_an_unrecorded_reason_does_not_suppress_a_real_score_drop(self):
        # The absent note must not eat the finding it sits next to.
        d = SD.diff(reading({"SAST": (10, None)}), reading({"SAST": (8, None)}))
        self.assertEqual([SD.DROP], [c.kind for c in d.changes])
        self.assertTrue(d.is_regression)


# =================================================================================================
# 5 · Checks that appear and disappear
# =================================================================================================


class TestCheckSetChanges(unittest.TestCase):
    def test_a_check_that_vanished_is_a_regression_we_can_no_longer_measure(self):
        before = reading({"SAST": (10, "ok"), "Fuzzing": (0, "not fuzzed")})
        after = reading({"SAST": (10, "ok")})
        d = SD.diff(before, after)
        self.assertEqual([("Fuzzing", SD.REMOVED)], [(c.name, c.kind) for c in d.changes])
        self.assertTrue(d.is_regression)

    def test_a_newly_reported_check_is_news_not_a_fault(self):
        before = reading({"SAST": (10, "ok")})
        after = reading({"SAST": (10, "ok"), "Fuzzing": (0, "not fuzzed")})
        d = SD.diff(before, after)
        self.assertEqual([("Fuzzing", SD.ADDED)], [(c.name, c.kind) for c in d.changes])
        self.assertFalse(d.is_regression)


# =================================================================================================
# 6 · The parser refuses malformed input rather than defaulting it
# =================================================================================================


class TestParserRefuses(unittest.TestCase):
    def test_a_checkless_reading_raises_however_the_absence_is_spelled(self):
        # An empty reading would diff against a real one as "every check was REMOVED" -- i.e.
        # eighteen regressions -- or against nothing as "no change". Both spellings are pinned:
        # the first draft of this test only covered the MISSING key, and the mutant that allowed
        # `"checks": []` through survived because of it (stage 23 mutant P01).
        base = {"repo": {"name": "x", "commit": "y"}, "date": "d", "score": 1.0}
        for absence in ({}, {"checks": []}, {"checks": None}):
            payload = dict(base, **absence)
            with self.subTest(absence=absence):
                with self.assertRaises(ValueError):
                    SD.parse_reading(payload)

    def test_a_non_integer_score_raises(self):
        with self.assertRaises(ValueError):
            SD.parse_reading(
                {
                    "repo": {"name": "x", "commit": "y"},
                    "date": "d",
                    "score": 1.0,
                    "checks": [{"name": "SAST", "score": "eight", "reason": "r"}],
                }
            )

    def test_a_duplicate_check_name_raises(self):
        with self.assertRaises(ValueError):
            SD.parse_reading(
                {
                    "repo": {"name": "x", "commit": "y"},
                    "date": "d",
                    "score": 1.0,
                    "checks": [
                        {"name": "SAST", "score": 8, "reason": "a"},
                        {"name": "SAST", "score": 9, "reason": "b"},
                    ],
                }
            )


# =================================================================================================
# 7 · The CLI carries the exit contract the module defines
# =================================================================================================


class TestCLIExitContract(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "scripts", "scorecard_delta.py")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, self.SCRIPT] + list(args),
            capture_output=True,
            text=True,
        )

    def test_a_drop_exits_1_and_names_the_check(self):
        p = self.run_cli("--previous", BASELINE_JSON, "--current", CURRENT_JSON)
        self.assertEqual(SD.EXIT_REGRESSION, p.returncode, p.stdout + p.stderr)
        self.assertIn("SAST", p.stdout)

    def test_a_declared_first_run_exits_3_not_0(self):
        p = self.run_cli("--current", CURRENT_JSON, "--first-run")
        self.assertEqual(SD.EXIT_NOT_COMPARED, p.returncode, p.stdout + p.stderr)
        self.assertIn("NO PREVIOUS READING", p.stdout)

    def test_a_first_run_must_be_DECLARED_and_is_never_inferred_from_an_omitted_flag(self):
        # The third face of §7g here, and the one that would rot quietly: forgetting --previous
        # must not mean "first run". If omission implied it, every mis-invocation would grade
        # nothing and say so in the reassuring voice of a genuine first run.
        p = self.run_cli("--current", CURRENT_JSON)
        self.assertNotIn(p.returncode, (SD.EXIT_OK, SD.EXIT_NOT_COMPARED))
        self.assertIn("--first-run", p.stderr)

    def test_first_run_and_previous_together_are_refused_rather_than_ranked(self):
        p = self.run_cli("--current", CURRENT_JSON, "--previous", BASELINE_JSON, "--first-run")
        self.assertNotIn(p.returncode, (SD.EXIT_OK, SD.EXIT_REGRESSION, SD.EXIT_NOT_COMPARED))

    def test_an_unchanged_pair_exits_0(self):
        p = self.run_cli("--previous", CURRENT_JSON, "--current", CURRENT_JSON)
        self.assertEqual(SD.EXIT_OK, p.returncode, p.stdout + p.stderr)

    def test_a_missing_previous_FILE_is_an_error_not_a_first_run(self):
        # §7g at the CLI boundary: "you did not give me a previous" and "the previous you named
        # is not there" are different answers, and the second must never silently become the
        # first — that would turn a lost history file into a clean-looking first run forever.
        p = self.run_cli("--previous", os.path.join(HERE, "_no_such_reading.json"),
                         "--current", CURRENT_JSON)
        self.assertNotIn(p.returncode, (SD.EXIT_OK, SD.EXIT_NOT_COMPARED))
        self.assertIn("--first-run", p.stderr)


if __name__ == "__main__":
    unittest.main()
