"""PYYAML-SKIP-CLUSTER — no test in this suite may report a PASS for a check it did not perform.

THE DEFECT. Seventeen sites across seven files reacted to a missing PyYAML by carrying on. Four
shapes, in ascending order of dishonesty:

  (i)   SKIP        `@skipUnless(_HAVE_YAML, …)` / `skipTest(…)` — visible in the run summary.
  (ii)  WEAKEN      falls through to a substring assertion UNDER THE SAME TEST NAME. Reports a
                    pass it did not earn, and a substring cannot tell `contents: read` in a
                    permissions block from `contents: read` in a comment.
  (iii) RETURN      `if not _HAVE_YAML: return` — no assertion, no fallback, no skip marker.
                    Indistinguishable from a test that ran and passed. One site had this.
  (iv)  REFUSE      `self.fail("PyYAML is required … pip install -r requirements/ci.txt")`.

THE RULING, and it is not "make (ii) and (iii) into (i)". Converting the dishonest shapes into
honest skips would leave the checks equally un-run — the bug solving itself into itself. PyYAML
is a REQUIRED TEST DEPENDENCY (requirements/ci.txt); its absence is a broken environment, not a
supported mode. So every site now goes through `_workflow_pins.safe_load`, which raises
`MissingParser`. That is one representation of "the parser is absent", carrying the name of the
check that went unperformed (doc 85 §7g — split the representation, and let the answer carry its
own provenance).

THIS FILE IS NOT SKIPPABLE, AND THAT IS LOAD-BEARING. It imports no `yaml`, declares no
`skipUnless` and calls no `skipTest`; `test_this_guard_cannot_skip_itself` asserts exactly that
against this file's own source. A guard for "checks that vanish quietly" that could itself vanish
quietly would be the joke version of itself — the same trap `GATE-COUNT-TRUTH` names one stage
over, where a `skipUnless` trades a loud crash for a silent skip.

AND THE SWEEP IS GRADED ON PLANTED OFFENDERS (doc 85 §7i). The real tree now holds zero
offenders, so a guard that walked only the real tree would pass whether or not it worked. Every
shape above is planted as a synthetic module and the classifier is required to catch it — and
required NOT to flag the two refusal shapes, which is the half that stops a working guard from
being "fixed" into a noisy one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import unittest
from unittest import mock

import _support  # noqa: F401 - puts src/ on the path
import _pyyaml_sweep as SWEEP
import _workflow_pins as wp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ─────────────────────────────────────────────────────────────────── planted offenders (§7i)
# One per shape, minimal and self-contained. These are STRINGS, never files: the sweep takes its
# corpus as an argument precisely so a violation can be handed to it without existing on disk.

PLANTED = {
    "shape_i_skip_decorator.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

@unittest.skipUnless(_HAVE_YAML, "PyYAML not installed")
class T(unittest.TestCase):
    def test_x(self):
        self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    "shape_i_skip_call.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        if not _HAVE_YAML:
            self.skipTest("PyYAML not installed")
        self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    # ⚠ NOT a duplicate of the one above, and the difference was a real hole in this guard.
    # `test_ci_matrix_floor` skipped through a tolerant FACTORY (`if self.yaml is None:`), so the
    # skip is not gated on a boolean flag and the flag-conditional rule cannot see it. Without
    # this fixture the message-matching rule was graded by nothing — the real tree's only two
    # instances of it having just been converted (doc 85 §7i, found while writing the mutants).
    "shape_i_skip_call_no_flag.py": '''
import unittest

def _yaml():
    try:
        import yaml
    except ImportError:
        return None
    return yaml

class T(unittest.TestCase):
    def test_x(self):
        parser = _yaml()
        if parser is None:
            self.skipTest("PyYAML not installed - structural assertions need a real parse")
        self.assertIn("jobs", parser.safe_load("jobs: {}"))
''',
    "shape_ii_weakened.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        text = "jobs: {}"
        if _HAVE_YAML:
            self.assertIn("jobs", yaml.safe_load(text))
        else:
            self.assertIn("jobs", text)
''',
    "shape_ii_inline_handler.py": '''
import unittest

class T(unittest.TestCase):
    def test_x(self):
        text = "jobs: {}"
        try:
            import yaml
        except ImportError:
            self.assertIn("jobs", text)
            return
        self.assertIn("jobs", yaml.safe_load(text))
''',
    "shape_iii_silent_return.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        if not _HAVE_YAML:
            return
        self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    "shape_iii_fallthrough.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        if _HAVE_YAML:
            self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    "shape_iii_tolerant_factory.py": '''
def _yaml():
    try:
        import yaml
    except ImportError:
        return None
    return yaml
''',
}

# The two shapes that must NOT be flagged. A sweep that reddens on correct code gets disabled.
CLEAN = {
    "clean_raises_in_handler.py": '''
class MissingParser(RuntimeError):
    pass

def safe_load(text):
    try:
        import yaml
    except ImportError as exc:
        raise MissingParser("PyYAML is required. Install it: requirements/ci.txt") from exc
    return yaml.safe_load(text)
''',
    "clean_fails_at_the_pin.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    yaml = None
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        if not _HAVE_YAML:
            self.fail("PyYAML is required. Install it: pip install -r requirements/ci.txt")
        self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    # ⚠ ADDED BECAUSE A MUTANT SURVIVED. M03 blinds `_refuses` to `ast.Raise` and the suite
    # stayed green: the only fixture that refused by RAISING did so inside an `except` handler,
    # where `_returns` already answers, and no fixture refused by raising inside a FLAG branch.
    # So the `Raise` arm was graded by nothing — §7i inside this stage's own §7i guard. This is
    # the shape that grades it, and it is a shape real code can take.
    "clean_raises_at_the_pin.py": '''
import unittest
try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

class T(unittest.TestCase):
    def test_x(self):
        if not _HAVE_YAML:
            raise RuntimeError("PyYAML is required. Install it: requirements/ci.txt")
        self.assertIn("jobs", yaml.safe_load("jobs: {}"))
''',
    "clean_no_yaml_at_all.py": '''
import unittest

class T(unittest.TestCase):
    def test_x(self):
        self.assertEqual(1, 1)
''',
}

# ────────────────────────────────────────────────────── the inventory, as an executable list
# Every converted site: the test that reached it, and the shape it had before this stage. This
# is the stage's inventory in the only form that cannot drift from the code — a list the suite
# runs. If a site is un-converted, or a name changes, the load-bearing test below reddens.

CONVERTED_SITES = (
    ("test_ci_matrix_floor", "TheDeclaredFloorRunsOnThePRGate", "test_b_the_pr_gate_runs_the_declared_floor", "i-skipTest"),
    ("test_ci_matrix_floor", "TheReleaseGateStillCoversTheFloor", "test_a_release_still_runs_the_floor", "i-skipTest"),
    ("test_repo_hardening", "TestRepoHardening", "test_all_github_yaml_parses", "i-skipUnless"),
    ("test_stage58_ci_check", "TestAction", "test_action_yml_is_a_valid_composite_action", "ii-weakened"),
    ("test_stage58_ci_check", "TestAction", "test_example_workflow_is_least_privilege", "ii-weakened"),
    ("test_stage61b_release_process", "TestDocsDeployGatedToMain", "test_a_deploy_job_is_gated_to_main_only", "i-skipUnless"),
    ("test_stage66_cross_platform", "TestCIMatrixCoversAllOSes", "test_matrix_lists_windows_and_linux", "ii-weakened"),
    ("test_stage66_cross_platform", "TestCIMatrixCoversAllOSes", "test_test_job_runs_on_the_matrix_os", "ii-weakened"),
    ("test_stage68_supply_chain", "TestReleaseWorkflowSigningAndSBOM", "test_release_yaml_parses", "i-skipTest"),
    ("test_stage68_supply_chain", "TestReleaseWorkflowSigningAndSBOM", "test_signing_steps_are_gated_to_the_real_repo", "iii-SILENT-RETURN"),
    ("test_stage68_supply_chain", "TestReleaseWorkflowSigningAndSBOM", "test_least_privilege_permissions", "ii-weakened"),
    ("test_stage68_supply_chain", "TestEveryReleaseJobIsRepoGated", "test_every_job_is_gated_to_the_oss_repo", "ii-fallthrough"),
    ("test_stage68_supply_chain", "TestPyPIPublishJob", "test_pypi_job_exists_gated_and_oidc", "i-skipTest"),
    ("test_stage68_supply_chain", "TestPyPIPublishJob", "test_pypi_publishes_the_built_artifact_not_a_rebuild", "ii-weakened"),
    ("test_workflow_module_guard", "TestEveryNamedModuleResolvesWhereTheStepRuns", "test_no_step_names_a_module_that_is_not_there", "i-skipUnless"),
)

_REAL_IMPORT = __import__


def _import_without_yaml(name, *args, **kwargs):
    if name == "yaml" or name.startswith("yaml."):
        raise ImportError("No module named 'yaml'")
    return _REAL_IMPORT(name, *args, **kwargs)


def _real_corpus():
    """The shipped test corpus, minus this file's own planted-offender strings (they are not
    on disk, so nothing to exclude — but the helper is named so the intent is not guessed at)."""
    corpus = dict(SWEEP.read_corpus(HERE))
    corpus.update({"integration/" + k: v
                   for k, v in SWEEP.read_corpus(os.path.join(HERE, "integration")).items()})
    return corpus


# ══════════════════════════════════════════════════════════ 1. the regression, named for the ID
class TestPyyamlSkipClusterIsClosed(unittest.TestCase):
    """RED before this stage (17 offenders across 7 files), green after."""

    def test_no_test_in_the_suite_tolerates_a_missing_parser(self):
        offenders = SWEEP.tolerant_sites(_real_corpus())
        self.assertEqual(
            offenders, [],
            "PYYAML-SKIP-CLUSTER: %d site(s) let a run report OK with the check unperformed.\n%s"
            "\n\nPyYAML is a required TEST dependency (requirements/ci.txt). Parse through "
            "`_workflow_pins.safe_load(text, what=…)`, which RAISES `MissingParser` — do not add "
            "a skip, a substring fallback or a bare `return`."
            % (len(offenders), SWEEP.render(offenders)))

    def test_the_corpus_is_the_real_one_and_is_not_empty(self):
        """Non-degeneracy. `tolerant_sites` over an empty corpus is also `[]`, and that green
        would mean the reader was broken rather than the tree clean."""
        corpus = _real_corpus()
        self.assertGreater(len(corpus), 300, "the test corpus did not load — the assertion above "
                                             "would pass over nothing")
        self.assertIn("test_stage68_supply_chain.py", corpus)


# ══════════════════════════════════════════════ 2. the sweep is graded on PLANTED offenders (§7i)
class TestTheSweepCatchesEveryShape(unittest.TestCase):
    """The real tree has no offender left, so the classifier is graded against synthetic ones."""

    def test_every_planted_offender_is_caught(self):
        missed = [name for name in sorted(PLANTED)
                  if not SWEEP.tolerant_sites({name: PLANTED[name]})]
        self.assertEqual(missed, [], "the sweep did not flag: " + ", ".join(missed))

    def test_each_shape_is_classified_as_the_shape_it_is(self):
        expected = {
            "shape_i_skip_decorator.py": SWEEP.SKIP_DECORATOR,
            "shape_i_skip_call.py": SWEEP.SKIP_CALL,
            "shape_ii_weakened.py": SWEEP.WEAKENED,
            "shape_ii_inline_handler.py": SWEEP.WEAKENED,
            "shape_iii_silent_return.py": SWEEP.SILENT_RETURN,
            "shape_iii_fallthrough.py": SWEEP.FALLTHROUGH,
            "shape_iii_tolerant_factory.py": SWEEP.TOLERANT_HANDLER,
        }
        for name, idiom in sorted(expected.items()):
            with self.subTest(shape=name):
                sites = SWEEP.tolerant_sites({name: PLANTED[name]})
                self.assertEqual([s.idiom for s in sites], [idiom])

    def test_a_skip_not_gated_on_a_flag_is_still_caught(self):
        """The message-matching rule, graded on its own. `test_ci_matrix_floor` skipped through a
        tolerant factory, so its skip hung off `if self.yaml is None:` — invisible to the
        flag-conditional rule. Both of that shape's real instances were converted at this stage,
        which left the rule graded by nothing until this fixture existed."""
        sites = SWEEP.tolerant_sites({"x.py": PLANTED["shape_i_skip_call_no_flag.py"]})
        self.assertEqual(sorted(s.idiom for s in sites),
                         [SWEEP.SKIP_CALL, SWEEP.TOLERANT_HANDLER])

    def test_a_refusal_is_NOT_flagged(self):
        """The other half of a working guard. A sweep that reddens on `raise` or `self.fail`
        would push the next author back toward a skip to quiet it."""
        for name in sorted(CLEAN):
            with self.subTest(clean=name):
                self.assertEqual(SWEEP.tolerant_sites({name: CLEAN[name]}), [],
                                 name + " is correct code and must not be flagged")

    def test_the_planted_set_covers_all_four_shapes(self):
        """A §7i grading is only as good as its offender set; pin that it did not shrink."""
        idioms = {s.idiom for name in PLANTED
                  for s in SWEEP.tolerant_sites({name: PLANTED[name]})}
        self.assertEqual(idioms, {SWEEP.SKIP_DECORATOR, SWEEP.SKIP_CALL, SWEEP.WEAKENED,
                                  SWEEP.SILENT_RETURN, SWEEP.FALLTHROUGH,
                                  SWEEP.TOLERANT_HANDLER})

    def test_one_site_is_counted_once(self):
        """`if not _HAVE_YAML: self.skipTest(…)` matches two detection rules. Counting it twice
        would inflate the inventory — a small lie in the family this file exists to catch."""
        sites = SWEEP.sweep({"x.py": PLANTED["shape_i_skip_call.py"]})
        self.assertEqual(len(sites), 1, SWEEP.render(sites))


# ═══════════════════════════════════ 3. 🔴 THE LOAD-BEARING TEST — the fix fires with PyYAML GONE
class TestEveryConvertedSiteRaisesWithoutPyYAML(unittest.TestCase):
    """A fix verified only where PyYAML is present has not been verified at all — that is the
    defect's own shape. Each converted site is RUN with the `yaml` import blocked, and must
    ERROR. Not skip: a skip here would mean the stage converted one silent shape into another.
    """

    def _run(self, module, cls, method):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName("%s.%s.%s" % (module, cls, method))
        result = unittest.TestResult()
        with mock.patch("builtins.__import__", _import_without_yaml):
            suite.run(result)
        return result

    def test_each_converted_site_errors_rather_than_skipping_or_passing(self):
        for module, cls, method, was in CONVERTED_SITES:
            with self.subTest(site="%s.%s.%s" % (module, cls, method), before=was):
                result = self._run(module, cls, method)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(
                    result.skipped, [],
                    "converted a silent pass into a silent SKIP — still un-run (%s)" % was)
                # `>= 1`, not `== 1`: a site using subTest (test_all_github_yaml_parses walks
                # every .github YAML) reports one error per subtest. What matters is that it did
                # not report zero.
                self.assertGreaterEqual(
                    len(result.errors) + len(result.failures), 1,
                    "reported a PASS with no parser: this site is NOT converted (was %s)" % was)
                blob = "".join(t for _, t in result.errors + result.failures)
                self.assertIn("MissingParser", blob,
                              "it failed, but not for the stated reason — the refusal must name "
                              "itself so the next reader is not sent hunting")
                self.assertIn("requirements/ci.txt", blob,
                              "a refusal must carry its remedy (doc 85 §7g corollary)")

    def test_the_harness_can_tell_a_pass_from_a_refusal(self):
        """Control for the test above: a site with NO parser dependency still passes under the
        same blocked import. Without this, an environment that broke every test would look like
        a stage-2 success."""
        result = self._run("test_stage66_cross_platform",
                           "TestBasenameAnySeparatorAgnostic", "test_posix_basename")
        self.assertEqual((len(result.errors), len(result.failures), result.skipped), (0, 0, []))

    def test_safe_load_carries_the_name_of_the_check_it_could_not_make(self):
        with mock.patch("builtins.__import__", _import_without_yaml):
            with self.assertRaises(wp.MissingParser) as caught:
                wp.safe_load("jobs: {}", "verify the thing under test")
        message = str(caught.exception)
        self.assertIn("verify the thing under test", message)
        self.assertIn("HARD FAILURE", message)
        self.assertIn("requirements/ci.txt", message)


# ═══════════════════════════════════════════════════ 4. the positive control on the guard itself
class TestThisGuardCannotVanishQuietly(unittest.TestCase):
    """⚠ `GATE-COUNT-TRUTH`, one stage over: a `skipUnless` trades a loud crash for a silent
    skip. A guard against silent skips must not be skippable, and "it isn't today" is not a
    control — this asserts it against the file's own source."""

    def _own_tree(self):
        """This file's own AST.

        ⚠ It must be the AST and not a substring search: the planted offenders below are STRING
        LITERALS containing `@unittest.skipUnless`, `self.skipTest` and `import yaml`, so a grep
        over this file convicts it of the very thing it is asserting the absence of. A parse sees
        code as code and data as data — which is this stage's whole lesson, applied to itself.
        """
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=__file__)

    def test_this_guard_cannot_skip_itself(self):
        tree = self._own_tree()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    name = dec.func.attr if isinstance(dec, ast.Call) and \
                        isinstance(dec.func, ast.Attribute) else ""
                    self.assertFalse(name.startswith("skip"),
                                     "%s carries @%s — this file guards against checks that "
                                     "vanish quietly and must not acquire a way to vanish "
                                     "quietly itself" % (node.name, name))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotEqual(node.func.attr, "skipTest",
                                    "a skipTest appeared in the guard at line %d" % node.lineno)

    def test_this_guard_needs_no_parser(self):
        """The positive control. It RUNS in the environment the defect lives in — the sweep is
        an `ast` walk over text, and `ast` is stdlib."""
        for node in ast.walk(self._own_tree()):
            if isinstance(node, ast.Import):
                self.assertNotIn("yaml", [a.name for a in node.names])
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "yaml")
        with mock.patch("builtins.__import__", _import_without_yaml):
            offenders = SWEEP.tolerant_sites({"x.py": PLANTED["shape_iii_silent_return.py"]})
        self.assertEqual(len(offenders), 1,
                         "the sweep must work in the very environment it exists to police")

    def test_the_sweep_module_is_pure(self):
        """It takes its corpus as an argument. A sweep hard-wired to the real tree would pass
        forever now that the tree is clean (doc 85 §7i)."""
        with open(os.path.join(HERE, "_pyyaml_sweep.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count("os.listdir"), 1,
                         "`read_corpus` is the only function here allowed to touch disk")


# ═════════════════════════════════════ 5. the negative test — shape (iv) is UNCHANGED, not "fixed"
class TestTheAlreadyCorrectSitesWereNotTouched(unittest.TestCase):
    """The two sites that already refused are the model this stage copied. Changing them would
    have been the reviewer's problem, not the reader's: a fixed site must stay distinguishable
    from an unexamined one, and these were neither."""

    def test_tm_s12a_still_refuses_at_the_pin_in_its_own_words(self):
        with open(os.path.join(HERE, "test_tm_s12a_branch_protection.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('self.fail("PyYAML is required to verify the scorecard-action wiring '
                      'from the PARSED "', source,
                      "test_tm_s12a's refusal is shape (iv), the model — it must not have moved")
        self.assertIn("pip install -r requirements/ci.txt", source)

    def test_workflow_pins_still_raises_MissingParser(self):
        with mock.patch("builtins.__import__", _import_without_yaml):
            with self.assertRaises(wp.MissingParser):
                wp.safe_load("jobs: {}")

    def test_MissingParser_is_still_not_swallowed_as_an_ordinary_failure(self):
        self.assertTrue(issubclass(wp.MissingParser, RuntimeError))
        self.assertFalse(issubclass(wp.MissingParser, ValueError))

    def test_there_is_exactly_one_representation_of_an_absent_parser(self):
        """§7g's fix is to SPLIT one representation carrying two meanings — not to grow a second
        representation of one meaning. `_preflight_parity` already re-exports this class rather
        than re-inventing it; every converted site now does the same."""
        defining = []
        for name, source in SWEEP.read_corpus(HERE).items():
            # By AST, not substring: this file's own planted-clean fixtures declare a
            # `class MissingParser` inside a string literal, and a grep would count it.
            for node in ast.walk(ast.parse(source, filename=name)):
                if isinstance(node, ast.ClassDef) and node.name == "MissingParser":
                    defining.append(name)
                    break
        self.assertEqual(defining, ["_workflow_pins.py"],
                         "a second 'parser absent' type appeared: " + ", ".join(defining))


# ══════════════════════════════════════ 6. the CI side — the half stage 10 found, generalised
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")

# A job that RUNS the whole unit suite with no parser installed. This is `embeddings-leg.yml`
# before stage 10, reduced to its bones.
PLANTED_WORKFLOW_OFFENDER = """
jobs:
  broken:
    steps:
      - name: Install mokata
        run: pip install -e .
      - name: Run unit test suite
        run: python -m unittest discover -s tests -t tests
"""

# The three shapes that are NOT offenders, and each was suspected at this stage's open:
# a targeted module, a `-p` pattern, and a `-k` filter. None of them runs a parser-dependent test.
PLANTED_WORKFLOW_CLEAN = """
jobs:
  targeted:
    steps:
      - run: pip install -e .
      - run: python -m unittest -v test_db_s8g_quality_at_scale
  pattern:
    steps:
      - run: pip install -e .
      - run: python -m unittest discover -s tests -t tests -p "test_gr_s2_*.py" -v
  filtered:
    steps:
      - run: pip install -e .
      - run: python -m unittest discover -s tests -t tests -k test_hook_shell_agnostic
  integration_only:
    steps:
      - run: pip install -e .
      - run: python -m unittest discover -s tests/integration -t tests/integration
  correct:
    steps:
      - run: pip install -e .
      - run: pip install --require-hashes -r requirements/ci.txt
      - run: python -m unittest discover -s tests -t tests
"""


class TestEveryJobRunningTheSuiteInstallsTheParser(unittest.TestCase):
    """Stage 10 fixed the job AND made its own sweep raise. This is that sweep, for the parser.

    Now that the seventeen sites RAISE, such a job goes red at CI time from an import error —
    true, but a worse message than a lint that names the job. And the failure mode being guarded
    is not hypothetical: `embeddings-leg.yml` ran the full suite parser-less for as long as the
    job existed, and nothing said so.
    """

    def _corpus(self):
        corpus = {}
        # CORPUS: THE WORKING TREE. `sync-public.sh` rsyncs `tests/` to the public mirror, so an
        # untracked test file ships and must be held to this rule. Converting to the index here is
        # what would reintroduce `SHIPPED-TEST-READS-INTERNAL-FILE` from the blind side.
        for name in sorted(os.listdir(WORKFLOW_DIR)):
            if name.endswith((".yml", ".yaml")):
                with open(os.path.join(WORKFLOW_DIR, name), encoding="utf-8") as fh:
                    corpus[name] = fh.read()
        return corpus

    def test_no_job_runs_the_whole_unit_suite_without_pyyaml(self):
        offenders = SWEEP.jobs_without_the_parser(self._corpus(), wp.safe_load)
        self.assertEqual(
            offenders, [],
            "job(s) run the whole unit suite with no PyYAML installed: %s. Add the "
            "`pip install --require-hashes -r requirements/ci.txt` step, as ci.yml, "
            "release.yml and embeddings-leg.yml all do."
            % ", ".join("%s::%s" % o for o in offenders))

    def test_the_corpus_is_the_real_one(self):
        """Non-degeneracy: `[]` over an empty corpus is also `[]`."""
        corpus = self._corpus()
        self.assertGreaterEqual(len(corpus), 9)
        self.assertIn("embeddings-leg.yml", corpus)

    def test_the_sweep_catches_a_planted_parser_less_job(self):
        """§7i — the tree is clean, so the guard is graded on a violation that is not in it."""
        offenders = SWEEP.jobs_without_the_parser({"planted.yml": PLANTED_WORKFLOW_OFFENDER},
                                                  wp.safe_load)
        self.assertEqual(offenders, [("planted.yml", "broken")])

    def test_a_targeted_or_filtered_job_is_NOT_flagged(self):
        """⚠ THE DISTINCTION THE OPENING SWEEP OF THIS STAGE COULD NOT MAKE. Three workflows were
        suspected of the same defect on a grep of "runs unittest, installs no PyYAML"; all three
        run a targeted module or a `-p` pattern that loads no parser-dependent test. A guard that
        flagged them would demand a dependency for tests those jobs do not run."""
        self.assertEqual(
            SWEEP.jobs_without_the_parser({"clean.yml": PLANTED_WORKFLOW_CLEAN}, wp.safe_load), [])

    def test_the_predicate_is_pinned_shape_by_shape(self):
        cases = {
            "discover -s tests -t tests": True,
            "discover -s tests -t tests -v": True,
            'discover -s tests -t tests -p "test_gr_s2_*.py" -v': False,
            "discover -s tests -t tests -k test_hook_shell_agnostic": False,
            "discover -s tests/integration -t tests/integration": False,
            "-v test_db_s8g_quality_at_scale": False,
        }
        for args, expected in sorted(cases.items()):
            with self.subTest(invocation=args):
                self.assertEqual(SWEEP.runs_the_whole_unit_suite(args), expected)


if __name__ == "__main__":
    unittest.main()
