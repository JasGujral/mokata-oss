"""0.0.22 stage B2 — `internal-suite-gate`: the instruments 0.0.21 learned it was missing.

Rows (doc 84): `A-SENTENCE-ABOUT-A-COMMAND-IS-NOT-A-COMMAND` (one shared comment-stripper),
`INTERNAL-ONLY-TESTS-RUN-ON-NO-RUNNER` (a recorded local gate), `THE-SUITE-IS-GREEN-IN-ONE-ARRANGEMENT-…`
(seeds on a schedule), `MUTANT-DRIVER-HEADER-OVER-CLAIMED-BY-FOUR` + `A-MUTANT-CAN-BE-KILLED-BY-A-LATER-FIX…`
(a header's claimed ids are held to its call sites, and a withdrawn one must be named).
"""
import datetime
import glob
import os
import sys
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _code_lines as CL
import _mutant_driver_contract as MDC
from _workflow_pins import safe_load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

#: The two readers that inspect `#` lines for a DIFFERENT question than "drop the prose": the corpus
#: sweep looks FOR a declaration comment, and f8a counts comment lines. Named, with the reason.
NOT_A_STRIPPER = {
    "_corpus_sweep.py": "reads a `# CORPUS:` declaration comment — it wants the comment, not the code",
    "test_f8a_preview_covers_the_write.py": "skips comment lines while scanning for a write call",
}


class ASentenceAboutACommandIsNotACommand(unittest.TestCase):

    def test_a_sentence_about_a_command_is_not_a_command_one_reader(self):
        """No test-tree module carries its own comment-stripping predicate any more."""
        offenders = []
        # CORPUS: THE WORKING TREE — what a person running the suite here would load.
        for path in sorted(glob.glob(os.path.join(TESTS, "*.py"))):
            name = os.path.basename(path)
            if name in ("_code_lines.py", os.path.basename(__file__)) or name in NOT_A_STRIPPER:
                continue
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if 'lstrip().startswith("#")' in line:
                        offenders.append("%s:%d" % (name, n))
        self.assertEqual([], offenders,
                         "a private comment-stripper is back — import tests/_code_lines.py instead")

    def test_the_shared_reader_keeps_line_numbers_and_strings(self):
        text = "a\n  # prose naming --force\necho 'x # not a comment'\n#\nb"
        self.assertEqual([(1, "a"), (3, "echo 'x # not a comment'"), (5, "b")], CL.code_lines(text))
        self.assertEqual("a\necho 'x # not a comment'\nb", CL.code_text(text))
        self.assertEqual(["a", "", "echo 'x # not a comment'", "", "b"],
                         CL.code_lines_blanked(text))
        self.assertEqual(["a", "echo 'x # not a comment'", "b"], CL.code_line_texts(text))
        self.assertEqual([], CL.code_lines(None))

    def test_every_former_private_reader_now_imports_the_shared_one(self):
        for name in ("_release_retry.py", "_mirror_bookkeeping.py", "_preflight_parity.py",
                     "test_sync_public_nested_checkout.py", "test_s12_release_publisher.py",
                     "test_stage68_supply_chain.py", "test_tm_s12a_branch_protection.py",
                     "_invocation_parity.py", "test_a32_one_mirror_one_truth.py",
                     "test_a33_every_gate_reports_before_it_stops.py"):
            with self.subTest(module=name):
                with open(os.path.join(TESTS, name), encoding="utf-8") as fh:
                    self.assertIn("from _code_lines import", fh.read())


class MutantDriverHeaderOverClaimed(unittest.TestCase):

    def _corpus(self):
        # CORPUS: THE WORKING TREE — the drivers a person can run from this checkout.
        out = {}
        for p in glob.glob(os.path.join(TESTS, "_*mutants*.sh")):
            with open(p, encoding="utf-8") as fh:
                out[os.path.basename(p)] = fh.read()
        return out

    def test_mutant_driver_header_over_claimed_no_header_claims_an_unwritten_mutant(self):
        self.assertEqual({}, MDC.header_overclaims(self._corpus()))

    def test_the_check_convicts_the_shape_that_shipped(self):
        """The :746 shape: the header says R01-R04, the file defines three."""
        planted = ("# R01-R04 the four refusals\n"
                   'mutant "R01 a" "$T" x y\nmutant "R02 b" "$T" x y\nmutant "R03 c" "$T" x y\n')
        self.assertEqual({"d.sh": ["R04"]}, MDC.header_overclaims({"d.sh": planted}))

    def test_a_withdrawn_equivalent_must_be_named_to_be_excused(self):
        """The :723 shape: a gap is legitimate only when a WITHDRAWN note names it."""
        base = '# N01-N02 the pair\nmutant "N01 a" "$T" x y\n'
        self.assertEqual({"d.sh": ["N02"]}, MDC.header_overclaims({"d.sh": base}))
        named = base + "# ⛔ N02 WAS WITHDRAWN AS AN EQUIVALENT MUTANT (the -z fix made it unobservable)\n"
        self.assertEqual({}, MDC.header_overclaims({"d.sh": named}))

    def test_a_suffixed_label_and_an_equivalent_call_both_count(self):
        text = ('# C06-C06 the pair\n# L01-L01 late answer\n'
                'mutant "C06a x" "$S" a b\nequivalent "L01 y" "$A" a b\n')
        self.assertEqual({}, MDC.header_overclaims({"d.sh": text}))


class InternalOnlyTestsRunOnNoRunner(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        self.addCleanup(sys.path.pop, 0)
        import internal_suite
        self.IS = internal_suite

    def test_internal_only_tests_run_on_no_runner_the_record_line_is_a_verdict(self):
        when = datetime.datetime(2026, 10, 8, 11, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual("INTERNAL-SUITE 2026-10-08T11:00:00Z abc classes=62 tests=291 "
                         "skipped=0 failed=0 GRADED",
                         self.IS.record_line("abc", 62, 291, 0, 0, now=when))
        self.assertTrue(self.IS.record_line("abc", 1, 1, 1, 0, now=when).endswith("REFUSED"),
                        "a SKIP in the dev tree must not print a graded line")
        self.assertTrue(self.IS.record_line("abc", 1, 1, 0, 2, now=when).endswith("REFUSED"))

    def test_off_the_dev_tree_it_refuses_rather_than_reporting(self):
        import _internal_subject as ISUB
        mirror = ISUB.TreeReport(ISUB.TREE_MIRROR)
        with mock.patch.object(ISUB, "tree_state", return_value=mirror):
            self.assertEqual(2, self.IS.main([]))

    def test_an_empty_derivation_refuses(self):
        import _internal_subject as ISUB
        dev = ISUB.TreeReport(ISUB.TREE_DEV)
        with mock.patch.object(ISUB, "tree_state", return_value=dev), \
                mock.patch.object(ISUB, "guarded_classes", return_value=()):
            self.assertEqual(2, self.IS.main([]))


class TheSuiteIsGreenInOneArrangement(unittest.TestCase):
    """Seeds on a schedule: the weekly `order-seeds.yml` job."""

    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "order-seeds.yml"), encoding="utf-8") as fh:
            self.doc = safe_load(fh.read(), "grade the weekly seed job")
        self.job = self.doc["jobs"]["order-seeds"]
        self.runs = "\n".join(s.get("run", "") for s in self.job["steps"])

    def test_the_suite_is_green_in_one_arrangement_a_seed_runs_every_week(self):
        on = self.doc.get("on") or self.doc.get(True)
        self.assertTrue(on.get("schedule"), "no schedule — the search would be by hand again")
        self.assertIn("scripts/shuffle-order.py --seed", self.runs)

    def test_the_seed_is_derived_and_replayable_never_random(self):
        self.assertIn("date -u +%G%V", self.runs)
        self.assertNotIn("$RANDOM", self.runs)

    def test_every_seed_keeps_its_log(self):
        upload = [s for s in self.job["steps"] if "upload-artifact" in str(s.get("uses", ""))]
        self.assertEqual(1, len(upload))
        self.assertEqual("always()", upload[0].get("if"))

    def test_it_is_mirror_only_by_the_billing_ruling(self):
        self.assertEqual("github.repository == 'JasGujral/mokata-oss'", self.job.get("if"))


if __name__ == "__main__":
    unittest.main()
