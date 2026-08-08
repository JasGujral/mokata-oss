"""Stage 27 — RELEASE-PREFLIGHT-NOT-CI-EQUIVALENT: the preflight's install set stops being typed.

WHAT WENT WRONG (the evidence this file starts from). The first real 0.0.17 cut aborted in
`run_test_preflight` with 17 errors + 2 failures, all `ModuleNotFoundError: No module named
'yaml'`, then `REFUSING TO RELEASE: unit suite FAILED with jsonschema present.` Three gates fired
in sequence and all three were right — the workflow sweep refused to grade an unchecked tree, the
scorecard pin refused to pass on an unparsed workflow, and release.sh refused to tag on a red
suite. The DEFECT is what they caught: the preflight venv installed `-e .` and (present leg only)
`requirements/jsonschema.txt`, never `requirements/ci.txt`, where PyYAML lives. It reproduced
CI's jsonschema dimension and not CI's test-tooling dimension.

★ THE SECOND INSTANCE OF A CLASS STAGE 10 ALREADY FIXED. `embeddings-leg.yml` was the only
full-suite runner without PyYAML — 17 tests skipping silently while the job reported OK — and
stage 10 fixed it with exactly the install this stage adds to release.sh. The release script had
the identical hole and stage 10 did not look there. So the deliverable is NOT the missing line:
it is that the preflight's install set is DERIVED from CI's, never maintained beside it.

WHAT THIS FILE PINS, in one sentence: every `requirements/*.txt` that ci.yml installs for the
unit suite is also installed by release.sh's `run_test_preflight`.

§7i — THE OFFENDER IS SUPPLIED. With the fix applied the real tree has no offender, so a pin that
only ever reads the real tree grades nothing and passes whether or not it works. `resolve()` is a
pure function over a SUPPLIED CORPUS (a ci.yml text + a release.sh text), and
`TestSyntheticOffender` hands it a ci.yml naming a requirements file the release.sh text does not
install, requires RED, then removes that one step and requires GREEN.

§7g — the collapse this pin is most exposed to is specific and is refused by construction: "ci.yml
installs no requirements files for the unit suite" makes the subset test VACUOUSLY true, which is
byte-identical to "release.sh covers them all". It renders UNDECIDABLE instead. So does a
release.sh with no locatable preflight function, and an unreadable side of either corpus; and a
corpus PyYAML cannot read RAISES (`MissingParser`) rather than answering.

§7h — SCOPE, STATED HONESTLY. "For the unit suite" means: every ci.yml job that invokes
`unittest discover` over `tests/`, minus any job declaring service containers. On the real tree
that reads `test` (the matrix job — the unit suite, the integration suite, both jsonschema legs)
and `hooks-execute` (which runs a `-k`-filtered slice of the same unit suite). It EXCLUDES
`live-db`, which boots postgres+pgvector and neo4j as service containers and installs the
`.[postgres,neo4j]` extras rather than a requirements file — a local preflight cannot stand those
up and `run_test_preflight`'s own comment says it does not try. Nothing else in ci.yml runs the
suite. The exclusions are RETURNED WITH THEIR REASONS and asserted below, so a narrowing of this
pin's meaning is a visible change rather than a silent one.

THE MIRROR BOUNDARY, because this file SHIPS and half of what it reads does not. `ci.yml` is
public; `scripts/release.sh` is internal, excluded by both controls in `sync-public.sh`. An
unguarded read of it here would raise FileNotFoundError on public CI — which is the exact defect
`run_public_subset_preflight` exists to catch, so it would abort the very cut this stage unblocks.
The release.sh-dependent assertions therefore live in their own class behind
`@unittest.skipUnless(os.path.exists(RELEASE_SH), ...)` — `test_stage68_supply_chain.py:342`'s
guard, copied rather than re-invented — and a class DECORATOR rather than a `setUpClass` skip, so
the tests stay in `Ran N` (`test_suite_count_integrity.py`'s rule). And because a guard that hides
a deletion is §7g wearing a skip, `test_release_sh_is_absent_ONLY_because_of_the_mirror_boundary`
holds the private tree to having the file at all.
"""

import os
import unittest
from unittest import mock

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _preflight_parity as pp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `scripts/release.sh` is INTERNAL — excluded from the public mirror by both controls in
# sync-public.sh. This file ships, so the half of it that reads release.sh must guard on the
# file's presence or it errors with FileNotFoundError on public CI. That is exactly the failure
# `run_public_subset_preflight` exists to catch, and the guard below is `test_stage68_supply_
# chain.py:342`'s, copied rather than re-invented.
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
# The discriminator that keeps that guard from becoming a §7g hole: sync-public.sh is internal
# too, so its presence means we are on the PRIVATE tree, where an absent release.sh is a deletion
# rather than a mirror boundary.
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

# The requirements files ci.yml installs for the unit suite TODAY. Named rather than counted, and
# asserted for anti-vacuity only — the property itself is fully derived. If CI starts installing a
# third file this list reds, which is the point: adding one must be a deliberate act that also
# reaches release.sh, not a silent divergence discovered at tag time.
DERIVED_TODAY = frozenset(("requirements/ci.txt", "requirements/jsonschema.txt"))

# ci.yml jobs that run the suite the preflight runs, and the one that is deliberately out of scope.
UNIT_SUITE_JOBS_TODAY = frozenset(("test", "hooks-execute"))
EXCLUDED_FOR_SERVICES = "live-db"

_CI = """\
name: CI
on:
  push:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Install mokata
        run: pip install -e .
      - name: Install workflow-lint test tooling
        run: pip install --require-hashes -r requirements/ci.txt
      - name: Run unit test suite
        run: python -m unittest discover -s tests -t tests
"""

_EXTRA_STEP = """\
      - name: Install something else
        run: pip install --require-hashes -r requirements/extra.txt
"""

_RELEASE = """\
run_test_preflight() {
  local leg tmp venv py
  "$py" -m pip install -q -e . >/dev/null
  "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null
  "$py" -m unittest discover -s tests -t tests < /dev/null
}

run_public_subset_preflight() {
  "$py" -m pip install -q --require-hashes -r "$sub/requirements/extra.txt" >/dev/null
}
"""


class TestTheDerivationIsWellFormed(unittest.TestCase):
    """The vocabulary, before any use of it."""

    def test_every_basis_maps_to_exactly_one_verdict(self):
        for basis in sorted(pp._VERDICT_OF):
            self.assertIn(pp._VERDICT_OF[basis], (pp.GREEN, pp.RED, pp.UNDECIDABLE))

    def test_verdict_is_derived_from_basis_not_stored_beside_it(self):
        self.assertNotIn("verdict", pp.ParityResolution.__slots__)
        self.assertEqual(pp.GREEN, pp.ParityResolution(pp.BASIS_ALL_INSTALLED).verdict)
        self.assertEqual(pp.RED, pp.ParityResolution(pp.BASIS_MISSING_INSTALLS).verdict)

    def test_an_unknown_basis_is_refused(self):
        with self.assertRaises(ValueError):
            pp.ParityResolution("probably_fine")

    def test_an_UNDECIDABLE_that_cannot_say_why_is_refused(self):
        for basis in sorted(pp.UNDECIDABLE_BASES):
            with self.assertRaises(ValueError, msg="basis %r accepted an empty reason" % basis):
                pp.ParityResolution(basis)


class TestSyntheticOffender(unittest.TestCase):
    """§7i — the real tree has no offender once the fix lands, so one is supplied.

    The offender is exactly the shape of the live defect: a ci.yml naming a requirements file the
    release.sh text does not install.
    """

    def test_the_intact_pair_derives_GREEN(self):
        r = pp.resolve(_CI, _RELEASE)
        self.assertEqual(pp.GREEN, r.verdict, r.render())
        self.assertEqual(pp.BASIS_ALL_INSTALLED, r.basis)
        self.assertEqual(("requirements/ci.txt",), r.required)

    def test_a_ci_install_the_preflight_does_not_make_is_RED_and_names_the_file(self):
        """THE offender. Adding one step to ci.yml — nothing else changes — must red."""
        r = pp.resolve(_CI + _EXTRA_STEP, _RELEASE)
        self.assertEqual(
            pp.RED, r.verdict,
            "ci.yml installs requirements/extra.txt for the unit suite and the preflight never "
            "does, which is the live 0.0.17 defect with the filename changed: %s" % r.render())
        self.assertEqual(pp.BASIS_MISSING_INSTALLS, r.basis)
        self.assertEqual(("requirements/extra.txt",), r.missing)
        self.assertIn("requirements/extra.txt", r.render())

    def test_removing_that_one_step_greens_again(self):
        """The other half of §7i: a guard that reds on everything grades nothing either."""
        offending = _CI + _EXTRA_STEP
        self.assertEqual(pp.RED, pp.resolve(offending, _RELEASE).verdict)
        self.assertEqual(
            pp.GREEN, pp.resolve(offending.replace(_EXTRA_STEP, ""), _RELEASE).verdict)

    def test_the_missing_install_is_caught_in_ANY_unit_suite_job_not_just_the_first(self):
        """A derivation that stopped at the first suite-running job would miss a file installed
        only by a later one — which is precisely how the release script's hole survived."""
        second = _CI + """\
  hooks-execute:
    runs-on: ubuntu-latest
    steps:
      - run: pip install --require-hashes -r requirements/extra.txt
      - run: python -m unittest discover -s tests -t tests -k test_hook_shell_agnostic
"""
        r = pp.resolve(second, _RELEASE)
        self.assertEqual(pp.RED, r.verdict, r.render())
        self.assertEqual(("requirements/extra.txt",), r.missing)


class TestWhatIsLegitimatelyExcluded(unittest.TestCase):
    """A pin that demands the preflight reproduce ALL of CI is a pin nobody can satisfy."""

    def test_a_job_with_service_containers_is_excluded_and_says_why(self):
        ci = _CI + """\
  live-db:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
    steps:
      - run: pip install --require-hashes -r requirements/extra.txt
      - run: python -m unittest discover -s tests/integration -t tests/integration
"""
        included, excluded = pp.unit_suite_jobs(ci)
        self.assertEqual({"test"}, {j for j, _ in included})
        reasons = dict(excluded)
        self.assertIn("live-db", reasons)
        self.assertIn("service containers", reasons["live-db"])
        self.assertEqual(
            pp.GREEN, pp.resolve(ci, _RELEASE).verdict,
            "a live-service job's installs must not be demanded of a LOCAL preflight")

    def test_a_job_that_never_runs_the_suite_is_excluded_and_says_why(self):
        ci = _CI + """\
  publish:
    runs-on: ubuntu-latest
    steps:
      - run: pip install --require-hashes -r requirements/extra.txt
      - run: python -m build
"""
        reasons = dict(pp.unit_suite_jobs(ci)[1])
        self.assertIn("publish", reasons)
        self.assertIn("unittest discover", reasons["publish"])
        self.assertEqual(pp.GREEN, pp.resolve(ci, _RELEASE).verdict)

    def test_the_editable_install_of_mokata_itself_is_not_a_requirements_file(self):
        """`pip install -e .` is deliberately unpinned (requirements/README.md) and names no
        requirements file — it must not appear in the derived set as some empty entry."""
        self.assertEqual(frozenset(("requirements/ci.txt",)), pp.ci_requirements(_CI))


class TestBothSidesAreParsedNotGrepped(unittest.TestCase):
    """PIN-SUBSTRING-COMMENT-HOLE, on the side of the pin that is shell rather than YAML."""

    def test_a_COMMENTED_OUT_install_does_not_count_as_an_install(self):
        commented = _RELEASE.replace(
            '  "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null\n',
            '  # was: pip install --require-hashes -r requirements/ci.txt (see '
            'requirements/README.md)\n')
        r = pp.resolve(_CI, commented)
        self.assertEqual(
            pp.RED, r.verdict,
            "the install is GONE and only the sentence explaining it remains — a substring "
            "reader reports covered exactly when coverage is lost: %s" % r.render())
        self.assertEqual(("requirements/ci.txt",), r.missing)

    def test_an_install_in_a_DIFFERENT_function_does_not_count(self):
        """`run_public_subset_preflight` installs from `"$sub/requirements/..."` into a different
        venv thirty lines below. A whole-file read would credit the preflight with it."""
        moved = _RELEASE.replace(
            '  "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null\n', "")
        moved = moved.replace(
            '-r "$sub/requirements/extra.txt"', '-r "$sub/requirements/ci.txt"')
        self.assertIn("requirements/ci.txt", moved)          # the string IS in the file
        r = pp.resolve(_CI, moved)
        self.assertEqual(pp.RED, r.verdict, r.render())
        self.assertEqual(("requirements/ci.txt",), r.missing)

    def test_a_requirements_path_that_is_not_a_dash_r_argument_is_not_an_install(self):
        mentioned = _RELEASE.replace(
            '  "$py" -m pip install -q --require-hashes -r requirements/ci.txt >/dev/null\n',
            '  "$py" -m pip install -q requirements/ci.txt >/dev/null\n')
        self.assertEqual(pp.RED, pp.resolve(_CI, mentioned).verdict)

    def test_a_dash_r_that_is_not_an_INSTALL_does_not_count_on_the_release_side(self):
        """`pip download -r` fetches wheels into a directory; it puts nothing in the venv the
        suite then runs in. Added after a first mutation pass: the `pip install` guard on this
        side survived every mutant, i.e. nothing was grading it."""
        downloaded = _RELEASE.replace(
            '"$py" -m pip install -q --require-hashes -r requirements/ci.txt',
            '"$py" -m pip download -q -r requirements/ci.txt')
        r = pp.resolve(_CI, downloaded)
        self.assertEqual(pp.RED, r.verdict, r.render())
        self.assertEqual(("requirements/ci.txt",), r.missing)

    def test_a_dash_r_that_is_not_an_INSTALL_does_not_count_on_the_ci_side_either(self):
        """The mirror of the above, and the one that would over-report: a CI step that merely
        downloads (or compiles) a requirements file must not become something the preflight is
        held to installing."""
        ci = _CI + """\
      - name: Prefetch wheels
        run: pip download -r requirements/extra.txt -d /tmp/wheels
"""
        self.assertEqual(frozenset(("requirements/ci.txt",)), pp.ci_requirements(ci))
        self.assertEqual(pp.GREEN, pp.resolve(ci, _RELEASE).verdict)

    def test_a_prefixed_requirements_path_still_resolves_to_the_repo_relative_file(self):
        """`"$sub/requirements/ci.txt"` and `requirements/ci.txt` are the same file; the pin
        compares repo-relative paths, not the literal argument."""
        prefixed = _RELEASE.replace(
            "-r requirements/ci.txt", '-r "$sub/requirements/ci.txt"')
        self.assertEqual(pp.GREEN, pp.resolve(_CI, prefixed).verdict)


class TestAbsenceNeverRendersAsAPass(unittest.TestCase):
    """§7g — the collapses this particular pin is exposed to, each given its own representation."""

    def test_a_ci_that_installs_NO_requirements_is_UNDECIDABLE_not_GREEN(self):
        """★ THE load-bearing case. An empty derived set makes the subset test vacuously true,
        and a vacuous true is byte-identical to a covered preflight."""
        bare = _CI.replace(
            "      - name: Install workflow-lint test tooling\n"
            "        run: pip install --require-hashes -r requirements/ci.txt\n", "")
        r = pp.resolve(bare, _RELEASE)
        self.assertEqual(pp.UNDECIDABLE, r.verdict, r.render())
        self.assertEqual(pp.BASIS_NO_CI_REQUIREMENTS, r.basis)
        self.assertTrue(r.render().startswith("UNKNOWN"))
        self.assertIn("vacuous", r.detail)

    def test_a_ci_that_runs_the_suite_nowhere_is_UNDECIDABLE(self):
        nosuite = _CI.replace("python -m unittest discover -s tests -t tests", "python -m build")
        r = pp.resolve(nosuite, _RELEASE)
        self.assertEqual(pp.UNDECIDABLE, r.verdict)
        self.assertEqual(pp.BASIS_NO_UNIT_SUITE_JOBS, r.basis)

    def test_a_release_script_with_no_preflight_function_is_UNDECIDABLE(self):
        r = pp.resolve(_CI, _RELEASE.replace("run_test_preflight() {", "run_something_else() {"))
        self.assertEqual(pp.UNDECIDABLE, r.verdict)
        self.assertEqual(pp.BASIS_NO_PREFLIGHT_FUNCTION, r.basis)
        self.assertIn(pp.PREFLIGHT_FUNCTION, r.detail)

    def test_an_unterminated_preflight_function_is_UNDECIDABLE(self):
        self.assertIsNone(pp.preflight_body("run_test_preflight() {\n  pip install -e .\n"))

    def test_an_unreadable_ci_is_UNDECIDABLE_and_not_either_verdict(self):
        r = pp.resolve(None, _RELEASE)
        self.assertEqual(pp.UNDECIDABLE, r.verdict)
        self.assertEqual(pp.BASIS_CI_UNREADABLE, r.basis)

    def test_an_unreadable_release_script_is_UNDECIDABLE_and_not_either_verdict(self):
        r = pp.resolve(_CI, None)
        self.assertEqual(pp.UNDECIDABLE, r.verdict)
        self.assertEqual(pp.BASIS_RELEASE_UNREADABLE, r.basis)

    def test_read_returns_None_for_a_missing_file_rather_than_raising(self):
        self.assertIsNone(pp.read(os.path.join(ROOT, "no", "such", "file.yml")))


class TestFailsLoudWithoutPyYAML(unittest.TestCase):
    """§7g again — an unparseable corpus is a THIRD answer, not a pass.

    This is not incidental to this stage: the release that surfaced the defect surfaced it AS 17
    `ModuleNotFoundError: No module named 'yaml'`. A pin about PyYAML's absence that skipped
    itself away when PyYAML was absent would be the joke version of itself.
    """

    @staticmethod
    def _import_without_yaml(name, *args, **kwargs):
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("No module named 'yaml'")
        return _REAL_IMPORT(name, *args, **kwargs)

    def test_resolve_RAISES_rather_than_returning_any_verdict(self):
        with mock.patch("builtins.__import__", self._import_without_yaml):
            with self.assertRaises(pp.MissingParser):
                pp.resolve(_CI, _RELEASE)

    def test_MissingParser_is_not_swallowed_as_an_ordinary_failure(self):
        self.assertTrue(issubclass(pp.MissingParser, RuntimeError))
        self.assertFalse(issubclass(pp.MissingParser, ValueError))


class TestAgainstTheRealTree(unittest.TestCase):
    """The CI half of the real tree — the half that ships, so it runs on the public mirror too."""

    @classmethod
    def setUpClass(cls):
        cls.ci = pp.read_ci(ROOT)

    def test_the_ci_workflow_is_readable_at_all(self):
        self.assertIsNotNone(self.ci, ".github/workflows/ci.yml could not be read")

    def test_release_sh_is_absent_ONLY_because_of_the_mirror_boundary(self):
        """★ The assertion that keeps the skip below from becoming a §7g hole.

        `TestTheRealPreflightIsCIEquivalent` skips when release.sh is missing, which is correct on
        the public subset and would be a silent hole anywhere else — a deleted release.sh would
        take the entire pin with it and the run would still report OK. sync-public.sh is internal
        by the same controls, so if IT is here, so must release.sh be.
        """
        if not os.path.exists(SYNC_SH):
            self.skipTest("public subset — neither internal script ships, as intended")
        self.assertTrue(
            os.path.exists(RELEASE_SH),
            "scripts/sync-public.sh is present, so this is the PRIVATE tree — but "
            "scripts/release.sh is gone. The parity pin below would SKIP rather than fail, and "
            "the preflight would go unchecked while the run reported OK.")

    def test_the_unit_suite_jobs_are_the_ones_this_pin_claims_to_cover(self):
        """§7h — the scope is asserted, not assumed. A job that starts running the suite must be
        added here deliberately, and a job that stops must be noticed."""
        included, excluded = pp.unit_suite_jobs(self.ci)
        self.assertEqual(
            UNIT_SUITE_JOBS_TODAY, {j for j, _ in included},
            "the set of ci.yml jobs running the unit suite changed; the derived install set now "
            "means something other than what this file's docstring says it means")
        reasons = dict(excluded)
        self.assertIn(
            EXCLUDED_FOR_SERVICES, reasons,
            "%s is excluded ON PURPOSE (service containers a local preflight cannot start). If "
            "it stopped being excluded, this pin quietly grew a scope it cannot satisfy."
            % EXCLUDED_FOR_SERVICES)
        self.assertIn("service containers", reasons[EXCLUDED_FOR_SERVICES])

    def test_the_derived_set_is_the_one_named_in_this_files_docstring(self):
        """Anti-vacuity. An empty or half-parsed derived set would satisfy the subset check just
        as loudly as a genuinely covered preflight."""
        self.assertEqual(DERIVED_TODAY, pp.ci_requirements(self.ci))


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestTheRealPreflightIsCIEquivalent(unittest.TestCase):
    """THE pin, against both real files. Split out from the class above and guarded, because
    `scripts/release.sh` does not ship: an unguarded read here would error with FileNotFoundError
    on public CI, which is the one defect `run_public_subset_preflight` is there to catch.
    `test_release_sh_is_absent_ONLY_because_of_the_mirror_boundary` above stops the guard from
    quietly swallowing a deletion on the private tree."""

    @classmethod
    def setUpClass(cls):
        cls.ci = pp.read_ci(ROOT)
        cls.release = pp.read_release(ROOT)

    def test_the_release_script_is_readable_at_all(self):
        self.assertIsNotNone(self.release, "scripts/release.sh could not be read")

    def test_the_preflight_installs_every_requirements_file_ci_installs(self):
        """THE pin. Before this stage it was RED: ci.yml installed requirements/ci.txt for the
        unit suite and `run_test_preflight` installed only requirements/jsonschema.txt, so the
        preflight ran 2800 tests against a venv with no PyYAML."""
        r = pp.resolve(self.ci, self.release)
        self.assertEqual(
            pp.GREEN, r.verdict,
            "release.sh's preflight is not CI-equivalent. %s\nAdd the install to "
            "run_test_preflight (BOTH legs — the absent leg runs the same suite)." % r.render())
        self.assertEqual(pp.BASIS_ALL_INSTALLED, r.basis)

    def test_the_ci_install_is_made_before_the_jsonschema_leg_branches(self):
        """BOTH LEGS, not just `present`. The `absent` leg runs the same unit suite and fails
        identically without PyYAML, so an install placed inside the `present` branch would leave
        half the preflight exactly as broken as it was — and the subset check above cannot see
        WHERE in the body an install sits, only that it is there."""
        body = pp.preflight_body(self.release)
        self.assertIsNotNone(body)
        install = body.find("requirements/ci.txt")
        branch = body.find('if [ "$leg" = "present" ]')
        self.assertNotEqual(-1, install, "run_test_preflight does not install requirements/ci.txt")
        self.assertNotEqual(-1, branch, "the jsonschema leg branch is no longer locatable")
        self.assertLess(
            install, branch,
            "requirements/ci.txt is installed INSIDE (or after) the `present` branch; the "
            "`absent` leg runs the same suite and would still fail with ModuleNotFoundError")


class TestTheScopeIsDerivedAndNotNamed(unittest.TestCase):
    """★ Stage 28. This file pinned ONE function by name and the class recurred one function over.

    `run_public_subset_preflight` runs the same suite in its own throwaway venv and installed
    `jsonschema.txt` without `ci.txt`, so the very next cut failed 37 tests on `No module named
    'yaml'` — the identical defect stage 27 had just fixed in `run_test_preflight`, and the
    identical defect stage 10 had fixed in `embeddings-leg.yml` before that. Three fixes, three
    instances, one class untouched.

    So the scope stopped being a name. `preflight_functions` reads it from what release.sh RUNS,
    and every one of them is held to CI. Supplied corpora throughout — §7i — so this still grades
    once the real tree is clean.
    """

    _RUNS = ("run_a() {\n"
             "  pip install -q --require-hashes -r requirements/jsonschema.txt\n"
             "  python -m unittest discover -s tests -t tests\n"
             "}\n"
             "run_b() {\n"
             "  pip install -q --require-hashes -r requirements/ci.txt\n"
             "  pip install -q --require-hashes -r requirements/jsonschema.txt\n"
             "  python -m unittest discover -s tests -t tests\n"
             "}\n"
             "helper() {\n"
             "  echo 'runs no tests at all'\n"
             "}\n")

    def test_a_function_is_in_scope_because_it_RUNS_the_suite(self):
        self.assertEqual(("run_a", "run_b"), pp.preflight_functions(self._RUNS))

    def test_a_function_that_runs_no_tests_is_not_a_preflight(self):
        self.assertNotIn("helper", pp.preflight_functions(self._RUNS))

    def test_a_commented_out_function_definition_is_not_in_scope(self):
        """A whole definition behind `#` defines nothing."""
        commented = "# run_ghost() {\n#   python -m unittest discover -s tests\n# }\n"
        self.assertEqual((), pp.preflight_functions(commented))

    def test_a_function_whose_only_mention_of_the_suite_is_a_COMMENT_is_not_in_scope(self):
        """PIN-SUBSTRING-COMMENT-HOLE, and the shape that actually bites.

        release.sh is more comment than code, and its prose NAMES `unittest discover` while
        explaining the preflights. A scope read off the raw text would enrol every function whose
        commentary mentions the suite — `wait_for_ci_green`, for one — and then demand each of
        them install CI's requirements. The body is comment-stripped first; this is the mutant
        that proves the stripping is load-bearing rather than decorative.
        """
        prose_only = ("wait_for_thing() {\n"
                      "  # the local run cannot cover what `unittest discover` runs in CI\n"
                      "  gh run watch\n"
                      "}\n")
        self.assertEqual((), pp.preflight_functions(prose_only))
        self.assertIn("unittest discover", prose_only,
                      "the fixture must NAME the suite, or it grades nothing")

    def test_EVERY_suite_running_function_is_graded_not_just_the_first(self):
        ci = _CI if isinstance(_CI, str) else None
        results = pp.resolve_all(ci, self._RUNS)
        self.assertEqual(("run_a", "run_b"), tuple(r.function for r in results))
        by_fn = {r.function: r for r in results}
        self.assertEqual(pp.RED, by_fn["run_a"].verdict, "run_a omits requirements/ci.txt")
        self.assertEqual(pp.GREEN, by_fn["run_b"].verdict)

    def test_the_RED_names_the_function_it_is_about(self):
        """One resolution per function, so a failure says WHICH preflight — the old renderer
        printed `run_test_preflight` no matter which body it had read."""
        red = [r for r in pp.resolve_all(_CI, self._RUNS) if r.verdict == pp.RED][0]
        self.assertIn("run_a", red.render())
        self.assertNotIn("run_test_preflight", red.render())

    def test_an_empty_scope_is_UNDECIDABLE_and_not_a_clean_sweep(self):
        """§7g. "release.sh runs the suite nowhere" must not read as "every preflight is fine"."""
        results = pp.resolve_all(_CI, "helper() {\n  echo nothing\n}\n")
        self.assertEqual(1, len(results))
        self.assertEqual(pp.BASIS_NO_PREFLIGHT_FUNCTIONS, results[0].basis)
        self.assertFalse(results[0].decided)
        self.assertIn("scope is empty", results[0].detail)

    def test_an_unreadable_release_script_yields_no_functions(self):
        self.assertEqual((), pp.preflight_functions(None))


@unittest.skipUnless(os.path.exists(RELEASE_SH),
                     "release.sh is dev-only, excluded from the public mirror")
class TestEveryRealPreflightIsCIEquivalent(unittest.TestCase):
    """The class pin, against the real release.sh — what stage 27 should have written."""

    @classmethod
    def setUpClass(cls):
        cls.ci = pp.read_ci(ROOT)
        cls.release = pp.read_release(ROOT)

    def test_release_sh_still_has_more_than_one_preflight(self):
        """Anti-vacuity: if the reader found only one function, the sweep below would pass by
        checking exactly what stage 27 already checked."""
        functions = pp.preflight_functions(self.release)
        self.assertIn("run_test_preflight", functions)
        self.assertIn("run_public_subset_preflight", functions)

    def test_EVERY_preflight_installs_what_ci_installs(self):
        results = pp.resolve_all(self.ci, self.release)
        offenders = [r.render() for r in results if r.verdict != pp.GREEN]
        self.assertEqual(
            [], offenders,
            "a release.sh function runs the suite against a dependency set CI does not use. "
            "Every function invoking `unittest discover` owes CI the same installs; adding the "
            "line to only the one that failed is what produced this row three times.\n  "
            + "\n  ".join(offenders))


_REAL_IMPORT = __import__


if __name__ == "__main__":
    unittest.main()
