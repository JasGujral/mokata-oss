"""0.0.21 stage 03 — `every-gate-reports-before-it-stops`. Two rows plus the lane's own purpose.

  * `RELEASE-NOTES-CHECK-RC-0-IS-UNREACHABLE` — a stage brief demanded rc=0 from a command for
    which 0 was unreachable by construction. **The defect is not the exit code; it is that nothing
    could tell you the bar was unclearable.**
  * `B5-BLIND-TO-VERSIONLESS-RELEASE-CLAIMS` — all nine claim patterns required a version literal,
    so a promise phrased *"the next release"* was invisible to the check built to catch exactly
    that class. ⭐ **And the versionless shape is the WORSE of the two: a dated promise can be
    found false; one that re-targets itself at whatever is being cut can never be false, and
    therefore can never be checked.**
  * **The lane's purpose:** `release.sh` aborted at the first red gate, so a retry discovered the
    NEXT problem instead of all of them.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import io
import os
import re
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import disclosure as D
from mokata.packaging import ReleaseNotesCheck

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RELEASE_SH = os.path.join(_REPO, "scripts", "release.sh")
_INTERNAL = "scripts/release.sh is dev-only, excluded from the public mirror"


def _claims(text, where="CHANGELOG.md"):
    return D.scheduling_claims({where: text})


# ============================================================ row: the versionless promise
class AVersionlessPromiseIsUNRESOLVABLE(unittest.TestCase):
    """`B5-BLIND-TO-VERSIONLESS-RELEASE-CLAIMS`.

    ⚠ PLANTED OFFENDERS, because the tree is CLEAN of this shape today — the row's own live
    instance was fixed at 0.0.19 stage 09a *by making no promise at all*. §7i: a guard whose corpus
    is the tree it walks has no offender in it, so the subjects are supplied."""

    def test_a_forward_looking_promise_with_NO_version_is_found(self):
        claims = _claims("Known issue (fix scheduled for the next release): invalid rows.")
        self.assertEqual(1, len(claims), claims)
        # ⚠ `owed-this-release` and not `scheduled-this-release`: the verb pattern is listed first
        # and its verb set includes `fix`, so it convicts *"fix scheduled for the next release"*
        # before the scheduling pattern is reached. Both convictions are correct and the remedy is
        # identical; the ONE-VERDICT-PER-LINE rule is what makes the order observable at all.
        self.assertEqual("owed-this-release", claims[0].pattern)

    def test_it_is_UNRESOLVABLE_and_that_is_NOT_the_same_state_as_UNRESOLVED(self):
        """⛔ §7g, AND THE REMEDIES ARE WHY. `UNRESOLVED` means *we looked and the plan does not
        say that* — fixed by correcting the plan or the version. `UNRESOLVABLE` means *there is no
        question a plan could answer* — fixed only by naming a release or deleting the promise.
        Same colour, different actions, so they must not share a representation."""
        claim = _claims("The fix lands in the next release.")[0]
        verdict = D.resolve(claim, plan_index={}, cutting="0.0.21")
        self.assertEqual(D.UNRESOLVABLE, verdict.state)
        self.assertNotEqual(D.UNRESOLVED, verdict.state)
        self.assertIn("can never be false", verdict.detail)
        self.assertIn("WORSE than a dated promise", verdict.detail)

    def test_UNRESOLVABLE_is_RED_and_has_its_OWN_bucket(self):
        claim = _claims("The fix lands in the next release.")[0]
        report = D.DisclosureReport(verdicts=(D.resolve(claim, plan_index={}, cutting="0.0.21"),),
                                    corpus_supplied=True, cutting="0.0.21")
        self.assertTrue(report.failed, "a promise that can never be checked must not pass")
        self.assertEqual(1, len(report.unresolvable))
        self.assertEqual(0, len(report.unresolved),
                         "it must not be counted as UNRESOLVED — the remedy is different")
        self.assertFalse(report.undecided,
                         "and it is not NOT_CHECKABLE either: this leg DID decide, and the answer "
                         "is that there is nothing to decide against")

    def test_it_is_NOT_reported_when_a_DATED_claim_covers_the_same_line(self):
        """*"deferred to 0.0.22 in a future release"* is odd prose, but the dated half IS
        resolvable and is the stronger claim. Convicting both would report one sentence twice and
        the fix for the pair is a single edit."""
        claims = _claims("ROW-NAME-HERE deferred to 0.0.22 in a future release.")
        self.assertEqual(["deferred-to"], [c.pattern for c in claims])

    def test_a_DESCRIPTIVE_sentence_about_the_release_is_NOT_a_promise(self):
        """⭐ `_subject_position`. A changelog says *"The next release adds X"* constantly — the
        phrase as grammatical SUBJECT is description, not commitment. Position, not part of speech:
        asking 'is the next word a verb' needs a lexicon that would rot."""
        for text in ("The next release adds the graph lens.",
                     "- **The next release** ships the floor.",
                     "Shipped. The next release will be smaller."):
            self.assertEqual((), _claims(text), text)

    def test_the_LIVE_FALSE_POSITIVE_my_first_version_produced(self):
        """🔴 MEASURED ON THE REAL TREE. My first version convicted a bare `this release`, and the
        live tree had exactly one such line:

            branch_protection.py:290  "NOT OBTAINED — these four assurances were NOT verified
                                       for this release:"

        ⛔ That is a correct degraded-state message measuring the run it is printed in. It does not
        re-target itself at a future cut, because it is not about a future cut — so convicting it
        would make B5 red on an honest disclosure. ⭐ `this release` is as often a factual SCOPE
        statement as a promise; `the next release` cannot be. That is why the bare pattern covers
        only the forward-looking phrases."""
        self.assertEqual(
            (), _claims("NOT OBTAINED — these four assurances were NOT verified for this release:"),
            "B5 is red on a correct degraded-state message")

    def test_past_tense_is_history_for_the_relative_patterns_too(self):
        self.assertEqual((), _claims("The fix landed in the next release, as promised."))

    def test_a_relative_promise_IS_caught_in_a_python_RUNTIME_CONSTANT(self):
        """Not just markdown: the row's class is about shipped SURFACES, and a printable constant
        reaches a user exactly as the changelog does."""
        module = 'MSG = "Graph parity is planned for an upcoming release."\n'
        claims = D.scheduling_claims({"src/mokata/thing.py": module})
        self.assertEqual(1, len(claims), claims)
        self.assertEqual("scheduled-this-release", claims[0].pattern)


class ARelativePromiseIsDATED_BY_ITS_SECTION(unittest.TestCase):
    """🔴 THE DEFECT MY OWN FIRST VERSION SHIPPED, caught by running it against the real tree: it
    reddened on *"(fix scheduled for the next release)"* sitting in a CHANGELOG section from
    several releases ago.

    ⛔ A dated claim ages out on its own — `0.0.17 <= cutting` is SPENT. A relative claim has no
    version, so **nothing in its text can age it**, and the only way to go green was to EDIT
    HISTORY — which this project refuses everywhere else. ⭐ **The promise is history by POSITION,
    and the position is readable:** the changelog is sectioned by release, and its newest `##` IS
    the release being cut (`test_dg7` enforces that)."""

    CHANGELOG = (
        "# Changelog\n"
        "\n"
        "## 0.0.21\n"
        "Known issue (fix scheduled for the next release): the live one.\n"
        "\n"
        "## 0.0.16\n"
        "Known issue (fix scheduled for the next release): the historical one.\n"
    )

    def setUp(self):
        self.claims = _claims(self.CHANGELOG)
        self.assertEqual(2, len(self.claims), "the premise: both lines are found")

    def test_the_section_is_attached_to_the_claim(self):
        self.assertEqual(["0.0.21", "0.0.16"], [c.section for c in self.claims])

    def test_the_HISTORICAL_one_is_SPENT_and_the_LIVE_one_is_UNRESOLVABLE(self):
        live, historical = self.claims
        self.assertEqual(D.UNRESOLVABLE,
                         D.resolve(live, plan_index={}, cutting="0.0.21").state,
                         "the newest section IS the release being cut, so a relative promise "
                         "there is live and uncheckable")
        spent = D.resolve(historical, plan_index={}, cutting="0.0.21")
        self.assertEqual(D.SPENT, spent.state,
                         "a promise printed under 0.0.16's heading was answered five releases ago")
        self.assertIn("sits under the 0.0.16 section", spent.detail)
        self.assertIn("OLDER than the version being cut", spent.detail,
                      "⛔ STRICTLY OLDER. A claim under the section BEING CUT is live — that "
                      "section IS this release. `<=` made the live case SPENT, which is the "
                      "fail-open direction and is what my first draft did.")

    def test_an_UNRECOGNISED_heading_leaves_its_lines_on_the_PREVIOUS_section(self):
        """⚠ THE DEGRADE DIRECTION, and it is chosen rather than inherited. A changelog whose
        format drifts should degrade towards 'dated by the section above', NOT towards 'undatable'
        — because the second silently turns historical prose into live promises, which is the
        fail-open direction."""
        sections = D.section_of_line("## 0.0.16\na\n## Unreleased\nb\n")
        self.assertEqual("0.0.16", sections[2])
        self.assertEqual("0.0.16", sections[4],
                         "a heading the reader does not recognise must not reset the dating to "
                         "None, which would make every line under it a live promise")

    def test_a_claim_ABOVE_the_first_heading_has_no_section_and_stays_RED(self):
        claims = _claims("The fix lands in the next release.\n\n## 0.0.16\nold\n")
        self.assertIsNone(claims[0].section)
        self.assertEqual(D.UNRESOLVABLE,
                         D.resolve(claims[0], plan_index={}, cutting="0.0.21").state,
                         "no section means no dating, and an undated relative promise is the "
                         "very thing this row is about — it must not become SPENT by default")

    def test_an_UNSECTIONED_surface_is_not_silently_dated(self):
        """README.md has no release sections, so nothing there can be aged out by position."""
        claims = D.scheduling_claims(
            {"README.md": "Windows parity lands in an upcoming release.\n"})
        self.assertEqual(1, len(claims))
        self.assertIsNone(claims[0].section)


# ============================================================ row: the unreachable rc=0
class TheCheckStatesItsOwnREACHABLECeiling(unittest.TestCase):
    """`RELEASE-NOTES-CHECK-RC-0-IS-UNREACHABLE`.

    A stage brief demanded `release-notes-check 0.0.19` return rc=0. It returns 2, and 0 was
    unreachable by construction: the one undecided claim is a keyed one the SHIPPED leg cannot
    resolve, because the planning corpus is dropped by both mirror controls.

    ⭐ **THE DEFECT IS NOT THE EXIT CODE. It is that nothing could TELL you the bar was
    unclearable.** The coordinator set it; the builder derived the real ceiling by hand and declined
    to chase it; and the only reason that went well is that the builder looked. **A criterion a
    human types from reading a docstring is a criterion that can be wrong about the tool.**"""

    def test_a_leg_that_cannot_decide_a_claim_reports_a_ceiling_of_2(self):
        report = D.DisclosureReport(
            verdicts=(D.Verdict(D.Claim("CHANGELOG.md", 1, "0.0.99", "scheduled-for", "x",
                                        key="SOME-ROW-KEY"),
                                D.NOT_CHECKABLE, "no corpus here"),),
            corpus_supplied=False, cutting="0.0.21")
        res = ReleaseNotesCheck(target="0.0.21", notes_version="0.0.21", section_found=True,
                               claims=report)
        self.assertTrue(res.claims_undecided, "the premise")
        self.assertEqual(2, res.reachable_exit)
        self.assertIn("0 is UNREACHABLE here", res.reachable_note())
        self.assertIn("2 is the pass-equivalent for this leg", res.reachable_note())

    def test_a_leg_that_CAN_decide_everything_reports_a_ceiling_of_0(self):
        """THE CONTROL. Without it the ceiling could be hard-coded to 2 and every assertion above
        would still pass — which would make the resolving gate's bar unclearable instead."""
        report = D.DisclosureReport(verdicts=(), corpus_supplied=True, cutting="0.0.21")
        res = ReleaseNotesCheck(target="0.0.21", notes_version="0.0.21", section_found=True,
                               claims=report)
        self.assertEqual(0, res.reachable_exit)
        self.assertIn("0 is a fair bar", res.reachable_note())

    def test_the_ceiling_is_printed_on_a_PASSING_run_too(self):
        """⚠ ON EVERY RUN, which is the point rather than noise: a note that appeared only when the
        ceiling was 2 would be absent from exactly the runs someone reads to write a brief."""
        report = D.DisclosureReport(verdicts=(), corpus_supplied=True, cutting="0.0.21")
        res = ReleaseNotesCheck(target="0.0.21", notes_version="0.0.21", section_found=True,
                               claims=report)
        self.assertTrue(res.ok, "the premise: this run passes")
        self.assertIn("best attainable exit", res.render())

    def test_the_ceiling_is_NOT_the_exit_code(self):
        """A run with a real failure returns 1 while its ceiling may be 0. The ceiling is a
        property of WHERE the invocation runs, not of whether this tree is currently right —
        collapsing the two would make the note useless for setting a bar."""
        report = D.DisclosureReport(verdicts=(), corpus_supplied=True, cutting="0.0.21")
        broken = ReleaseNotesCheck(target="0.0.21", notes_version="0.0.19", section_found=True,
                                   claims=report)
        self.assertFalse(broken.ok, "the premise: this run fails on the version")
        self.assertEqual(0, broken.reachable_exit,
                         "its ceiling is still 0 — the failure is fixable, which is exactly what "
                         "a ceiling of 0 means")


# ============================================================ the lane's purpose
@unittest.skipUnless(os.path.exists(RELEASE_SH), _INTERNAL)
class TheCheapGatesRunToCompletion(unittest.TestCase):
    """⭐ THE LANE'S OWN PURPOSE. `release.sh` aborted at the first red gate, so a retry discovered
    the NEXT problem instead of all of them. At the 0.0.19 cut that shape cost three days across
    nine failures — **six of them machinery rather than product, and most visible in the first
    thirty seconds.**

    ⛔ THE SPLIT IS BY COST AND IT IS DELIBERATE. Cheap gates collect; the three test preflights
    stay fail-fast, because collecting those would mean running ~24,000 tests to report two
    problems — a worse trade than the one it replaces."""

    def setUp(self):
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            self.script = fh.read()
        raw = [ln for ln in self.script.splitlines() if not ln.lstrip().startswith("#")]
        # ⚠ LINE CONTINUATIONS ARE JOINED, and the first version of this class did not do it: four
        # of the collected gates are written as `gate "label" \` with the command on the next
        # line, so a reader that looked at physical lines reported them as uncollected. A shell
        # command is not a line (§7g, in the reader rather than the subject).
        self.code = []
        pending = ""
        for ln in raw:
            if ln.rstrip().endswith("\\"):
                pending += ln.rstrip()[:-1]
                continue
            self.code.append(pending + ln)
            pending = ""
        if pending:
            self.code.append(pending)

    def test_the_cheap_gates_are_COLLECTED(self):
        collected = [ln.strip() for ln in self.code if ln.startswith("gate ")]
        self.assertGreaterEqual(len(collected), 5,
                                "the cheap preflight gates are not going through `gate`:\n%s"
                                % "\n".join(collected))
        for needle in ("verify_branch_protection", "verify_mirror_gate", "verify_release_notes",
                       "verify_disclosure_resolves", "verify_tag_sets"):
            self.assertTrue(any(needle in ln for ln in collected),
                            f"{needle} is no longer collected — it aborts the cut on its own "
                            f"again, so a second cheap failure stays hidden until the retry")

    def test_the_run_stops_ONCE_with_the_WHOLE_list(self):
        self.assertIn("gates_report_or_abort", "\n".join(self.code))
        self.assertIn("this is the WHOLE list and not the first item of it", self.script)
        self.assertIn("a retry will not discover a new one", self.script)

    def test_the_EXPENSIVE_preflights_are_deliberately_NOT_collected(self):
        """§7f read the other way: the exception has to be stated or someone will 'finish the job'
        and make the cut take thirty minutes to report two problems."""
        for expensive in ("run_test_preflight", "run_hostile_path_preflight",
                          "run_public_subset_preflight"):
            calls = [ln.strip() for ln in self.code if ln.strip() == expensive]
            self.assertEqual(1, len(calls), f"{expensive} should be called once, plainly")
        self.assertIn("FROM HERE DOWN THE GATES ARE FAIL-FAST AGAIN, ON PURPOSE", self.script)

    # --- the gate machinery, RUN rather than read ---------------------------
    def _gate_machinery(self):
        """`gate` and `gates_report_or_abort`'s real bodies, lifted from the real script.

        ⚠ EXTRACTED RATHER THAN RE-WRITTEN (§7c — the observer is not the repo), and added because
        a MUTANT SURVIVED: emptying the report's loop was GREEN against every assertion in this
        class, because they all read `release.sh` as TEXT. A collected-gate design whose report
        prints nothing is the worst of both worlds — it runs every gate and then tells you about
        none of them — and nothing could see it."""
        start = self.script.index("GATE_FAIL_LABELS=()")
        end = self.script.index("\n}\n", self.script.index("gates_report_or_abort() {"))
        return self.script[start:end + 3]

    def _drive(self, *gate_lines):
        import subprocess
        script = ("set -uo pipefail\n"
                  "say() { printf '== %s ==\\n' \"$1\"; }\n"
                  + self._gate_machinery()
                  + "\n".join(gate_lines) + "\ngates_report_or_abort\n")
        return subprocess.run(_support.bash_argv("-c", script, "bash"),
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env=dict(os.environ))

    def test_the_report_NAMES_EVERY_failed_gate_and_not_just_the_first(self):
        """⭐ THE ASSERTION THE WHOLE STAGE IS FOR, and the one that caught the survivor. Three
        gates, two of them red: BOTH must appear, with their own output, in one run."""
        proc = self._drive(
            'gate "alpha" bash -c "echo alpha-detail >&2; exit 1"',
            'gate "beta" bash -c "echo beta-is-fine"',
            'gate "gamma" bash -c "echo gamma-detail; exit 3"')
        blob = proc.stdout + proc.stderr
        self.assertNotEqual(0, proc.returncode, blob)
        self.assertIn("2 of 3 cheap preflight gates FAILED", blob)
        self.assertIn("alpha (exit 1)", blob)
        self.assertIn("gamma (exit 3)", blob,
                      "the SECOND failure must be reported too — a report that stops at the "
                      "first one is the fail-fast behaviour this stage replaced, wearing a "
                      "summary's clothes")
        self.assertIn("alpha-detail", blob, "and each failure carries its OWN output")
        self.assertIn("gamma-detail", blob)
        self.assertIn("the WHOLE list and not the first item", blob)

    def test_EVERY_gate_RUNS_even_after_one_has_failed(self):
        """Not just reported — RUN. A `gate` that short-circuited after the first failure would
        produce the same summary while never executing the third."""
        proc = self._drive(
            'gate "alpha" bash -c "exit 1"',
            'gate "beta" bash -c "echo BETA-RAN; exit 1"',
            'gate "gamma" bash -c "echo GAMMA-RAN"')
        blob = proc.stdout + proc.stderr
        self.assertIn("BETA-RAN", blob)
        self.assertIn("GAMMA-RAN", blob, "a gate after two failures must still have run")

    def test_the_CONTROL_all_green_passes_quietly_and_says_how_many(self):
        """Without this, every assertion above is also true of a block that always aborts."""
        proc = self._drive('gate "alpha" bash -c "true"', 'gate "beta" bash -c "true"')
        blob = proc.stdout + proc.stderr
        self.assertEqual(0, proc.returncode, blob)
        self.assertIn("All 2 cheap preflight gates PASSED", blob)
        self.assertNotIn("REFUSING", blob)

    def test_the_abort_says_nothing_was_pushed_or_tagged(self):
        """The one thing an operator needs to know before reading the list: the cut did not half
        happen."""
        proc = self._drive('gate "alpha" bash -c "exit 1"')
        self.assertIn("Nothing was pushed, nothing was tagged", proc.stdout + proc.stderr)

    def test_a_gate_still_prints_its_own_output_where_it_RAN(self):
        """A summary that replaced the live output would make a passing run quieter and a failing
        run harder to read — the opposite trade."""
        self.assertIn("if [ -n \"$out\" ]; then printf '%s\\n' \"$out\"; fi", self.script)

    def test_the_mirror_check_moved_BEFORE_the_test_preflights(self):
        """⭐ THE BIGGEST OPERATOR-FACING CHANGE. It used to run immediately before the sync, i.e.
        after three preflights that take minutes — so *"you are in the stale checkout"* was a
        ten-minute-old answer to a two-second question. It is read-only; there was no reason for it
        to be late."""
        body = "\n".join(self.code)
        # ⚠ The bare CALL, not the definition: `run_test_preflight() {` appears hundreds of lines
        # earlier and matching it compared the wrong two positions.
        mirror_at = body.index('gate "the mirror checkout')
        preflight_at = body.index("\nrun_test_preflight\n")
        self.assertLess(mirror_at, preflight_at)

    def test_the_mirror_gate_hands_its_VALUE_back_through_a_FILE(self):
        """⚠ `gate` runs its command in a command substitution, so a variable set inside dies with
        the subshell. One derivation still — just carried across a process boundary."""
        self.assertIn("MIRROR_BRANCH_FILE", self.script)
        self.assertIn('PUB_DEFAULT_BRANCH="$(cat "$MIRROR_BRANCH_FILE"', self.script)

    def test_the_sync_step_REFUSES_if_the_derived_branch_never_arrived(self):
        """⛔ Fail-closed, and the message says it is a release.sh bug rather than an operator
        error — because an empty `PUB_DEFAULT_BRANCH` here would hardcode `main` by omission."""
        self.assertIn("the mirror's default branch is not known at the sync step", self.script)
        self.assertIn("This is a release.sh bug, not an operator error", self.script)

    def test_DG7_and_B5_stay_TWO_gates(self):
        """§7g. They answer different questions — whether a limitation is still PRINTED, and
        whether a published PROMISE still resolves — and collapsing them would hide one behind the
        other's verdict, which is how five published commitments reached PyPI with every check
        green."""
        collected = [ln for ln in self.code if ln.startswith("gate ")]
        notes = [ln for ln in collected if "verify_release_notes" in ln]
        claims = [ln for ln in collected if "verify_disclosure_resolves" in ln]
        self.assertEqual(1, len(notes))
        self.assertEqual(1, len(claims))
        self.assertNotEqual(notes[0], claims[0])


if __name__ == "__main__":
    unittest.main()
