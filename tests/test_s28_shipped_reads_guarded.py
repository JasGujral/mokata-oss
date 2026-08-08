"""Stage 28 — SHIPPED-TEST-READS-INTERNAL-FILE: the guard stops being a habit and becomes a rule.

THE DEFECT, AND WHY IT IS NOT TWO DECORATORS. `tests/` ships to the public mirror;
`scripts/release.sh`, `scripts/sync-public.sh` and `docs/build/` do not. `test_s11_*` and
`test_s12_*` read those paths unguarded, so twelve tests that are green here ERROR on the tree
users clone — and 0.0.17's own cut is blocked by `run_public_subset_preflight`, which runs the
suite from exactly that tree.

The guard those two files needed already existed. `test_stage68_supply_chain.py:342` and `:369`
have carried `@unittest.skipUnless(os.path.exists(RELEASE_SH), ...)` since stage 68. Stages 11 and
12 walked past it. That is the third appearance of one shape in this release:

    stage 10 fixed `embeddings-leg.yml`'s missing PyYAML install and not `release.sh`'s
        -> found by the 0.0.17 cut, fixed in stage 27
    `test_stage68` established the mirror-boundary guard at its own two call sites
        -> stages 11 and 12 wrote new call sites; found by the subset preflight, fixed here

Each was found by the NEXT gate, never by the stage that established the rule, because each time
the rule was applied to the INSTANCES in front of it. **A guard established for two call sites
protects two call sites; only a derivation protects the class.** So the fix here is
`_shipped_reads`, which derives the property over the whole shipped corpus from `sync-public.sh`'s
own exclude list; the two decorators are its first consequence, not its substance.

WHAT THIS FILE HOLDS
  * the derivation is well-formed (verdict from basis, undecidable must say why);
  * READ vs MENTION is decided the way the docstring claims — anchored joins are reads, string
    needles and fixture trees are not, and an existence PROBE is not a read either (condemning
    probes would condemn the absent-only-because-of-the-mirror companions this stage adds);
  * a `setUpClass` skip is REFUSED as a guard, and the reason is MEASURED here rather than
    asserted: the decorator keeps every test in `Ran N` (`Ran 2 ... skipped=2`) while a setUpClass
    skip collapses the class and drops them (`Ran 0 ... skipped=1`), so the mirror's count would
    diverge from this repo's — and `test_s11`'s setUpClass read the file before its own skip could
    fire, which is how the guard failed in the first place;
  * §7g — an empty exclude set is UNDECIDABLE, never a vacuous GREEN;
  * §7i — a synthetic offender REDs and its guarded twin GREENs, so the sweep still grades once
    the real tree is clean;
  * and the real shipped corpus is swept.

Pure/offline apart from the last class, which is guarded because reading the exclude list means
reading `sync-public.sh`, which does not ship. The sweep therefore classifies THIS file as
guarded-green — the property applied to its own enforcement.
"""

import ast
import types
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

import _mirror_bookkeeping as mb
import _shipped_reads as sr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")

#: The fixture exclude set. Two entries, both real, so the fixtures exercise a file path and a
#: directory prefix without depending on the tree.
FIXTURE_EXCLUDES = frozenset(["scripts/release.sh", "docs/build"])

_ROOT_LINE = "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
_HEAD = "import os\nimport unittest\n" + _ROOT_LINE

#: §7i's offender: a shipped test that opens an excluded path from an unguarded class.
OFFENDER = _HEAD + (
    "RELEASE = os.path.join(ROOT, 'scripts', 'release.sh')\n"
    "class TestReadsTheScript(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        with open(RELEASE) as fh:\n"
    "            self.assertIn('set -euo pipefail', fh.read())\n")

#: The same file with the one thing that fixes it.
GUARDED = OFFENDER.replace(
    "class TestReadsTheScript",
    "@unittest.skipUnless(os.path.exists(RELEASE), 'dev-only, excluded from the mirror')\n"
    "class TestReadsTheScript")

#: The shape that must NOT be accepted: it skips, but it is still COLLECTED.
SETUPCLASS_SKIP = OFFENDER.replace(
    "    def test_it(self):",
    "    @classmethod\n"
    "    def setUpClass(cls):\n"
    "        if not os.path.exists(RELEASE):\n"
    "            raise unittest.SkipTest('dev-only')\n"
    "    def test_it(self):")

#: The offender with the read removed — §7i's other half.
NO_READ = _HEAD + (
    "class TestReadsNothing(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        self.assertTrue(True)\n")

#: A MENTION, not a read: the path is a needle being searched for in other text.
MENTION = _HEAD + (
    "class TestNoShippedFileNamesAnInternalDoc(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        self.assertNotIn('docs/build', open(os.path.join(ROOT, 'README.md')).read())\n"
    "        self.assertNotIn('scripts/release.sh', 'whatever')\n")

#: A FIXTURE the test built itself under a temp directory — the boundary has no opinion on it.
FIXTURE_TREE = _HEAD + (
    "import tempfile\n"
    "class TestBuildsItsOwnTree(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        tmp = tempfile.mkdtemp()\n"
    "        os.makedirs(os.path.join(tmp, 'docs', 'build'))\n"
    "        self.assertTrue(os.path.isdir(os.path.join(tmp, 'docs', 'build')))\n")

#: A module-level name that is NOT derived from `__file__`, so it anchors NOTHING. Without this,
#: `_root_names` could accept any module-level assignment and no fixture would notice.
UNANCHORED_BASE = "import os\nimport unittest\n" + (
    "BASE = '/opt/vendor'\n"
    "RELEASE = os.path.join(BASE, 'scripts', 'release.sh')\n"
    "class TestReadsSomethingElse(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        with open(RELEASE) as fh:\n"
    "            fh.read()\n")

#: A module-level function whose join is headed by a LOCAL, not by one of its own parameters. It
#: is therefore not a path accessor, and calling it with the repo root is not a read.
NON_PARAM_JOIN = _HEAD + (
    "def _elsewhere(unused):\n"
    "    base = '/opt/vendor'\n"
    "    return os.path.join(base, 'scripts', 'release.sh')\n"
    "class TestCallsIt(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        with open(_elsewhere(ROOT)) as fh:\n"
    "            fh.read()\n")

#: A real path ACCESSOR called with a TEMPDIR rather than the repo root. The accessor itself reads
#: nothing; only the caller decides, and this caller decided on a tree it built.
ACCESSOR_ON_A_TMPDIR = _HEAD + (
    "import tempfile\n"
    "def read_script(base):\n"
    "    with open(os.path.join(base, 'scripts', 'release.sh')) as fh:\n"
    "        return fh.read()\n"
    "class TestBuildsItsOwnTree(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        tmp = tempfile.mkdtemp()\n"
    "        self.assertTrue(read_script(tmp))\n")

#: A skip raised in `setUp` rather than `setUpClass`. Still REFUSED — but it must be recognised as
#: the skip shape it is, or the report tells the author nothing about what to change.
SETUP_SKIP = OFFENDER.replace(
    "    def test_it(self):",
    "    def setUp(self):\n"
    "        if not os.path.exists(RELEASE):\n"
    "            self.skipTest('dev-only')\n"
    "    def test_it(self):")

#: A PROBE, not a read — the shape every absent-only-because-of-the-mirror companion uses.
PROBE_ONLY = OFFENDER.replace(
    "        with open(RELEASE) as fh:\n"
    "            self.assertIn('set -euo pipefail', fh.read())\n",
    "        if not os.path.exists(RELEASE):\n"
    "            self.skipTest('public subset')\n")

#: An import-time read: no class decorator can guard it, because the import itself fails.
MODULE_LEVEL = _HEAD + (
    "RELEASE = os.path.join(ROOT, 'scripts', 'release.sh')\n"
    "TEXT = open(RELEASE).read()\n"
    "class TestUsesIt(unittest.TestCase):\n"
    "    def test_it(self):\n"
    "        self.assertIn('set', TEXT)\n")


def _one(source, excludes=FIXTURE_EXCLUDES, name="tests/test_fixture.py"):
    return sr.resolve(name, source, excludes)


#: The two skip shapes, as RUNNABLE two-test classes, so the `Ran N` claim can be measured instead
#: of believed. It was written backwards the first time; a fixture that executes cannot be.
_DECORATED_PAIR = (
    "import os, unittest\n"
    "@unittest.skipUnless(os.path.exists('/definitely/not/here'), 'absent')\n"
    "class TestPair(unittest.TestCase):\n"
    "    def test_a(self): pass\n"
    "    def test_b(self): pass\n")

_SETUPCLASS_PAIR = (
    "import os, unittest\n"
    "class TestPair(unittest.TestCase):\n"
    "    @classmethod\n"
    "    def setUpClass(cls):\n"
    "        if not os.path.exists('/definitely/not/here'):\n"
    "            raise unittest.SkipTest('absent')\n"
    "    def test_a(self): pass\n"
    "    def test_b(self): pass\n")


def _module(source):
    """`source` as a real, loadable module object — no file, no import side effects."""
    module = types.ModuleType("_s28_probe")
    exec(compile(source, "<_s28_probe>", "exec"), module.__dict__)
    return module


class TestTheDerivationIsWellFormed(unittest.TestCase):
    """Same contract as `_mirror_bookkeeping` and `_preflight_parity`: verdict comes FROM basis."""

    def test_every_basis_maps_to_exactly_one_verdict(self):
        for basis, verdict in sr._VERDICT_OF.items():
            self.assertIn(verdict, (sr.GREEN, sr.RED, sr.UNDECIDABLE), basis)

    def test_verdict_is_derived_and_not_storable(self):
        res = sr.FileResolution("f.py", sr.BASIS_NO_EXCLUDED_READS)
        self.assertEqual(sr.GREEN, res.verdict)
        with self.assertRaises(AttributeError):
            res.basis = sr.BASIS_UNGUARDED_READS

    def test_an_undecidable_without_a_reason_is_refused_at_construction(self):
        for basis in sorted(sr.UNDECIDABLE_BASES):
            with self.assertRaises(ValueError):
                sr.FileResolution("f.py", basis)

    def test_an_unknown_basis_is_refused(self):
        with self.assertRaises(ValueError):
            sr.FileResolution("f.py", "probably_fine")

    def test_a_read_is_immutable(self):
        read = sr.Read("TestX", "scripts/release.sh", sr.GUARD_NONE)
        with self.assertRaises(AttributeError):
            read.guard = sr.GUARD_DECORATOR

    def test_the_exclude_reader_is_the_audited_one_and_not_a_second_parser(self):
        """A second exclude parser beside `_mirror_bookkeeping`'s is the defect this stage is
        about, committed in the module that exists to stop it."""
        self.assertIs(sr.exclude_entries, mb.exclude_entries)


class TestReadVersusMention(unittest.TestCase):
    """§7h — the sweep's scope is asserted, not left in the docstring."""

    def test_an_anchored_join_is_a_read(self):
        res = _one(OFFENDER)
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual("scripts/release.sh", res.reads[0].path)

    def test_a_bare_string_literal_is_a_mention_and_not_a_read(self):
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, _one(MENTION).basis)

    def test_a_path_under_a_temp_directory_is_not_a_read(self):
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, _one(FIXTURE_TREE).basis)

    def test_an_existence_probe_is_not_a_read(self):
        """Condemning probes would condemn the very companions this stage adds."""
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, _one(PROBE_ONLY).basis)

    def test_a_variable_segment_is_not_reconstructed_and_says_so(self):
        """Blind spot 1, pinned so it stays a KNOWN limit rather than a silent one."""
        source = _HEAD + (
            "SUB = 'scripts'\n"
            "class TestX(unittest.TestCase):\n"
            "    def test_it(self):\n"
            "        open(os.path.join(ROOT, SUB, 'release.sh')).read()\n")
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, _one(source).basis)
        self.assertIn("Paths built at run time", sr.__doc__)

    def test_an_import_time_read_is_reported_against_the_module_itself(self):
        res = _one(MODULE_LEVEL)
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual(sr.SCOPE_MODULE, res.unguarded[0].scope)
        self.assertIn("no class decorator can guard this", res.render())

    def test_only_LITERAL_excludes_decide(self):
        """Blind spot 3: an rsync glob is dropped rather than fnmatched."""
        self.assertEqual(frozenset(["docs/build"]),
                         sr.literal_excludes(frozenset(["docs/build/", "/mokata-*/", "*.pyc"])))

    def test_the_match_is_on_segments_and_not_substrings(self):
        self.assertEqual("docs/build", sr.excluded_match("docs/build/02.md", {"docs/build"}))
        self.assertIsNone(sr.excluded_match("docs/buildings/x.md", {"docs/build"}))


class TestGuardShape(unittest.TestCase):
    """The discriminator this stage exists to encode: which skip actually removes the test."""

    def test_the_class_decorator_is_a_guard(self):
        self.assertEqual(sr.GUARD_DECORATOR, sr.guard_of_source(GUARDED))
        self.assertEqual(sr.BASIS_ALL_READS_GUARDED, _one(GUARDED).basis)

    def test_a_setUpClass_skip_is_NOT_a_guard(self):
        self.assertEqual(sr.GUARD_SETUPCLASS_SKIP, sr.guard_of_source(SETUPCLASS_SKIP))
        self.assertEqual(sr.RED, _one(SETUPCLASS_SKIP).verdict)

    def test_the_setUpClass_refusal_says_WHY_it_is_not_equivalent(self):
        """Prose in a docstring is not enforcement; the reason ships in the finding."""
        rendered = _one(SETUPCLASS_SKIP).render()
        self.assertIn("Ran N", rendered)
        self.assertIn("ONE skip", rendered)

    def test_the_two_skip_shapes_really_do_count_differently(self):
        """The claim above, MEASURED rather than asserted — it was first written backwards.

        A class decorator collects every test and marks each skipped, so they stay in `Ran N`. A
        setUpClass raising SkipTest collapses the class into one skip and drops its tests out of
        `Ran N` entirely. That is the divergence `test_suite_count_integrity.py` pins, and it is
        the reason `ACCEPTED_GUARDS` holds the decorator and not the other one.
        """
        def _count(body):
            suite = unittest.TestLoader().loadTestsFromModule(_module(body))
            result = unittest.TestResult()
            suite.run(result)
            return result.testsRun, len(result.skipped)

        self.assertEqual((2, 2), _count(_DECORATED_PAIR),
                         "the class decorator should keep both tests in Ran N")
        self.assertEqual((0, 1), _count(_SETUPCLASS_PAIR),
                         "a setUpClass skip should collapse the class and drop both from Ran N")

    def test_only_the_decorator_shape_is_accepted(self):
        self.assertEqual(frozenset([sr.GUARD_DECORATOR]), sr.ACCEPTED_GUARDS)
        self.assertNotIn(sr.GUARD_SETUPCLASS_SKIP, sr.ACCEPTED_GUARDS)
        self.assertNotIn(sr.GUARD_NONE, sr.ACCEPTED_GUARDS)

    def test_a_skipUnless_that_does_not_test_existence_is_not_this_guard(self):
        """`skipUnless(sys.platform == 'win32')` skips for an unrelated reason and must not be
        read as mirror-boundary protection."""
        wrong = OFFENDER.replace(
            "class TestReadsTheScript",
            "@unittest.skipUnless(os.environ.get('SLOW'), 'slow')\nclass TestReadsTheScript")
        self.assertEqual(sr.GUARD_NONE, sr.guard_of_source(wrong))
        self.assertEqual(sr.RED, _one(wrong).verdict)

    def test_an_undecorated_class_carries_no_guard(self):
        self.assertEqual(sr.GUARD_NONE, sr.guard_of_source(OFFENDER))

    def test_an_AND_of_probes_is_a_guard(self):
        """A class reading TWO internal files must be able to require both — `test_s11`'s
        `TestTheDocIsHeldToTheDerivation` reads sync-public.sh AND doc 02. Requiring more is
        strictly stronger, so it stays a guard."""
        both = OFFENDER.replace(
            "class TestReadsTheScript",
            "@unittest.skipUnless(os.path.exists(RELEASE) and os.path.exists(RELEASE), 'dev')\n"
            "class TestReadsTheScript")
        self.assertEqual(sr.GUARD_DECORATOR, sr.guard_of_source(both))

    def test_an_OR_of_probes_is_NOT_a_guard(self):
        """`or` runs the class when only ONE of the files it reads is present — the un-guard
        wearing a guard's clothes, and the exact shape a careless copy of the `and` produces."""
        either = OFFENDER.replace(
            "class TestReadsTheScript",
            "@unittest.skipUnless(os.path.exists(RELEASE) or os.path.exists('x'), 'dev')\n"
            "class TestReadsTheScript")
        self.assertEqual(sr.GUARD_NONE, sr.guard_of_source(either))
        self.assertEqual(sr.RED, _one(either).verdict)


class TestVacuityIsUndecidable(unittest.TestCase):
    """§7g, three answers not two. The collapse refused here is specific and easy to fall into."""

    def test_an_empty_exclude_set_is_UNDECIDABLE_not_a_pass(self):
        res = _one(OFFENDER, excludes=frozenset())
        self.assertEqual(sr.UNDECIDABLE, res.verdict)
        self.assertEqual(sr.BASIS_NO_EXCLUDES, res.basis)
        self.assertFalse(res.decided)

    def test_the_undecidable_says_why_and_names_the_vacuity(self):
        res = _one(OFFENDER, excludes=frozenset())
        self.assertIn("vacuously true", res.detail)
        self.assertIn("UNKNOWN", res.render())

    def test_an_all_glob_exclude_set_is_also_vacuous_and_also_UNDECIDABLE(self):
        """Globs are dropped, so a list of nothing but globs leaves nothing to decide with —
        which must not be mistaken for a clean tree."""
        res = _one(OFFENDER, excludes=frozenset(["*.pyc", "/mokata-*/"]))
        self.assertEqual(sr.BASIS_NO_EXCLUDES, res.basis)

    def test_an_unreadable_source_is_UNDECIDABLE_not_clean(self):
        res = sr.resolve("tests/gone.py", None, FIXTURE_EXCLUDES)
        self.assertEqual(sr.BASIS_SOURCE_UNREADABLE, res.basis)
        self.assertFalse(res.decided)

    def test_an_unparseable_source_is_UNDECIDABLE_not_clean(self):
        res = _one("class Broken(:\n")
        self.assertEqual(sr.BASIS_SOURCE_UNPARSEABLE, res.basis)
        self.assertIn("unknown rather than absent", res.detail)

    def test_undecided_and_offenders_are_different_questions(self):
        results = (_one(OFFENDER), _one(OFFENDER, excludes=frozenset()), _one(NO_READ))
        self.assertEqual(1, len(sr.offenders(results)))
        self.assertEqual(1, len(sr.undecided(results)))


class TestTheArmsTheContaminatedScoreHid(unittest.TestCase):
    """★★ FOUR ARMS OF `_shipped_reads` THAT STAGE 28's 27/27 NEVER ACTUALLY GRADED.

    They were not missed by inspection — they were HIDDEN BY THE MUTATOR ITSELF. `scripts/
    mutate.sh` creates `.mutate.lock` in the repo root for the duration of a run, and
    `.mutate.lock` is a literal `sync-public.sh --exclude` entry. Under the old **disk-probe**
    derivation it therefore entered the deriving set *while and only while the mutator ran*, and
    `test_mutation_harness._InterlockFixture` reads it from `setUp` — an unguarded boundary read.
    So `test_no_shipped_test_reads_an_internal_file_without_a_guard` was RED for **every mutant in
    the batch**, and `FAILED (failures=1)` was read as "the mutant was caught" when it meant
    "the lock is here". Measured: B02/W3/W6/W7 all report `Ran 44 ... failures=1` under the batch
    and all four survive with no failure at all when the lock is absent.

    ⚠ **The instrument was contaminating the corpus it was grading** — the same shape as the floor
    this rider fixed, one level up. The index-derived set closes it by construction: `.mutate.lock`
    is untracked, so no mutation run can ever put it back into the deriving set.

    These four tests are what the score should have been buying. Each is a FIXTURE — none depends
    on the tree — so none of them can be contaminated the same way.
    """

    def test_an_unparseable_source_reports_the_UNDECIDABLE_VERDICT_not_only_the_basis(self):
        """B02. The basis was asserted; the verdict it maps to was not, so flipping
        `_VERDICT_OF[BASIS_SOURCE_UNPARSEABLE]` to GREEN changed nothing any test could see."""
        res = _one("class Broken(:\n")
        self.assertEqual(sr.UNDECIDABLE, res.verdict)
        self.assertFalse(res.decided)
        self.assertIn("UNKNOWN", res.render())

    def test_an_unreadable_source_reports_the_UNDECIDABLE_VERDICT_too(self):
        res = sr.resolve("tests/gone.py", None, FIXTURE_EXCLUDES)
        self.assertEqual(sr.UNDECIDABLE, res.verdict)
        self.assertIn("UNKNOWN", res.render())

    def test_a_module_level_name_not_derived_from___file___anchors_nothing(self):
        """W3. `BASE = '/opt/vendor'` is not the repo root, so a join headed by it is not a
        boundary read. Without this, `_root_names` could accept every module-level assignment and
        the whole read/mention discriminator would collapse into 'any join wins'."""
        res = _one(UNANCHORED_BASE)
        self.assertEqual(sr.GREEN, res.verdict)
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, res.basis)
        self.assertEqual((), res.reads)

    def test_an_accessor_must_be_headed_by_a_PARAMETER_not_any_local(self):
        """W7. A function that joins its OWN local with an excluded tail decides the path itself —
        the caller has no say, so passing it the repo root cannot make it a boundary read."""
        res = _one(NON_PARAM_JOIN)
        self.assertEqual(sr.GREEN, res.verdict)
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, res.basis)

    def test_an_accessor_called_with_a_TEMPDIR_is_not_a_boundary_read(self):
        """B10. `read_script(tmp)` and `read_script(ROOT)` are the same call on different trees,
        and only the second crosses the boundary. ⚠ This arm's old RED was itself machine-dependent
        — it fired on developer-local exclude entries (`.venv`, `build`, `.claude`, `docs/
        marketing`) that no clone carries, so the score for it was measuring the laptop too."""
        res = _one(ACCESSOR_ON_A_TMPDIR)
        self.assertEqual(sr.GREEN, res.verdict)
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, res.basis)
        self.assertEqual((), res.reads)

    def test_the_same_accessor_called_with_the_ROOT_IS_a_boundary_read(self):
        """The other half — without it the test above is satisfied by a reader that sees nothing."""
        res = _one(ACCESSOR_ON_A_TMPDIR.replace("read_script(tmp)", "read_script(ROOT)"))
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual("scripts/release.sh", res.reads[0].path)

    def test_a_skip_raised_in_setUp_is_RECOGNISED_as_the_skip_shape_and_still_refused(self):
        """W6. `setUp` and `setUpClass` are both the wrong shape, but they must be REPORTED as
        that shape — a `GUARD_NONE` tells the author to add a guard they already wrote, instead of
        to change the one they have."""
        self.assertEqual(sr.GUARD_SETUPCLASS_SKIP, sr.guard_of_source(SETUP_SKIP))
        self.assertNotIn(sr.GUARD_SETUPCLASS_SKIP, sr.ACCEPTED_GUARDS)
        res = _one(SETUP_SKIP)
        self.assertEqual(sr.RED, res.verdict)


class TestTheFloorItselfIsGraded(unittest.TestCase):
    """§7i on the ANTI-VACUITY FLOOR, which had only ever been run against a healthy corpus.

    A floor that has never been fed a bad corpus proves nothing about bad corpora — and that is
    exactly how it went a whole release being met by three untracked files. So it is fed a
    synthetic empty deriving set and a synthetic shrunken one here, and the two must not answer the
    same way: empty is UNDECIDABLE (the reader saw nothing), shrunken is RED (the reader saw
    something and it was wrong). Collapsing those two into one failure would rebuild the defect one
    level up.
    """

    HEALTHY = sr.DECLARED_TRACKED_EXCLUDES

    def test_the_healthy_corpus_greens(self):
        res = sr.resolve_deriving_set(self.HEALTHY)
        self.assertEqual(sr.GREEN, res.verdict)
        self.assertEqual(sr.BASIS_DERIVING_SET_DECLARED, res.basis)

    def test_an_empty_corpus_is_UNDECIDABLE_with_a_reason(self):
        res = sr.resolve_deriving_set(frozenset())
        self.assertEqual(sr.UNDECIDABLE, res.verdict)
        self.assertEqual(sr.BASIS_DERIVING_SET_EMPTY, res.basis)
        self.assertIn("vacuously true", res.detail)
        self.assertIn("UNKNOWN", res.render())

    def test_an_unreadable_index_is_UNDECIDABLE_and_NOT_the_same_as_empty(self):
        res = sr.resolve_deriving_set(None)
        self.assertEqual(sr.UNDECIDABLE, res.verdict)
        self.assertEqual(sr.BASIS_INDEX_UNREADABLE, res.basis)
        self.assertNotEqual(sr.resolve_deriving_set(frozenset()).basis, res.basis)
        self.assertNotEqual(sr.resolve_deriving_set(frozenset()).render(), res.render())

    def test_a_shrunken_corpus_is_RED_and_names_what_it_lost(self):
        shrunken = self.HEALTHY - {"docs/build"}
        res = sr.resolve_deriving_set(shrunken)
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual(sr.BASIS_DERIVING_SET_DRIFTED, res.basis)
        self.assertEqual(("docs/build",), res.lost)
        self.assertEqual((), res.gained)
        self.assertIn("docs/build", res.render())

    def test_empty_and_shrunken_fail_DIFFERENTLY(self):
        """The whole point of the split representation: 'saw nothing' and 'saw the wrong thing'
        are different findings and must not share a message."""
        empty = sr.resolve_deriving_set(frozenset())
        shrunken = sr.resolve_deriving_set(self.HEALTHY - {"docs/build"})
        self.assertNotEqual(empty.verdict, shrunken.verdict)
        self.assertNotEqual(empty.basis, shrunken.basis)
        self.assertNotEqual(empty.render(), shrunken.render())

    def test_a_GROWN_corpus_is_RED_too_so_the_floor_cannot_hide_an_addition(self):
        """A floor only ever notices shrinkage. An internal path added to `sync-public.sh` and
        tracked, but never declared here, is a real change to what the sweep judges — reported,
        not absorbed."""
        res = sr.resolve_deriving_set(self.HEALTHY | {"docs/secrets"})
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual(("docs/secrets",), res.gained)
        self.assertIn("docs/secrets", res.render())

    def test_removing_the_offender_greens_again(self):
        """The other half of §7i: the grader must also come back GREEN, or it grades nothing."""
        planted = self.HEALTHY | {"docs/secrets"}
        self.assertEqual(sr.RED, sr.resolve_deriving_set(planted).verdict)
        self.assertEqual(sr.GREEN, sr.resolve_deriving_set(planted - {"docs/secrets"}).verdict)

    def test_an_UNDECIDABLE_deriving_basis_must_carry_a_reason(self):
        for basis in sorted(sr.DERIVING_UNDECIDABLE_BASES):
            with self.subTest(basis=basis):
                with self.assertRaises(ValueError):
                    sr.DerivingSetResolution(basis)

    def test_the_deriving_verdict_is_derived_from_the_basis_and_not_stored(self):
        res = sr.resolve_deriving_set(self.HEALTHY)
        with self.assertRaises(AttributeError):
            res.basis = sr.BASIS_DERIVING_SET_DRIFTED


class TestTheIndexReaderIsNotADiskProbe(unittest.TestCase):
    """`tracked_paths` on real, synthetic checkouts — the fix's own mechanism, graded where the
    answer is known because the fixture built it."""

    def _repo(self, tracked=(), untracked=()):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for rel in list(tracked) + list(untracked):
            path = os.path.join(d, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        subprocess.run(["git", "init", "-q", d], check=True, env=env)
        if tracked:
            subprocess.run(["git", "-C", d, "add"] + list(tracked), check=True, env=env)
        return d

    def test_an_untracked_file_is_not_in_the_index(self):
        d = self._repo(tracked=["a/keep.txt"], untracked=["b/local.txt"])
        paths = sr.tracked_paths(d)
        self.assertIn("a/keep.txt", paths)
        self.assertIn("a", paths, "a tracked file must imply its directories")
        self.assertNotIn("b/local.txt", paths)
        self.assertNotIn("b", paths)

    def test_the_exclude_set_drops_the_untracked_entry_the_disk_would_have_kept(self):
        """The defect, reproduced in miniature and then fixed: `b` is ON DISK and in NO clone."""
        d = self._repo(tracked=["a/keep.txt"], untracked=["b/local.txt"])
        self.assertTrue(os.path.exists(os.path.join(d, "b")))
        self.assertEqual(frozenset(["a"]), sr.tracked_excludes(d, ["a/", "b/"]))

    def test_a_directory_that_is_not_a_checkout_is_None_and_not_an_empty_set(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        env = dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(d))
        with mock.patch.dict(os.environ, env):
            self.assertIsNone(sr.tracked_paths(d))
            self.assertIsNone(sr.tracked_excludes(d, ["docs/build"]))

    def test_a_missing_git_binary_is_None_and_not_an_empty_set(self):
        with mock.patch.object(sr.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(sr.tracked_paths(ROOT))

    def test_an_empty_checkout_is_an_empty_set_and_NOT_None(self):
        """The other side of §7g: 'this repo tracks nothing excluded' is a real answer."""
        d = self._repo()
        self.assertEqual(frozenset(), sr.tracked_paths(d))
        self.assertEqual(frozenset(), sr.tracked_excludes(d, ["docs/build"]))


class TestSyntheticOffender(unittest.TestCase):
    """§7i — the real tree is about to be clean, so the sweep is graded on supplied defects."""

    def test_the_offender_REDs_and_names_the_class_and_the_path(self):
        res = _one(OFFENDER)
        self.assertEqual(sr.RED, res.verdict)
        self.assertEqual(sr.BASIS_UNGUARDED_READS, res.basis)
        self.assertEqual("TestReadsTheScript", res.unguarded[0].scope)
        self.assertIn("scripts/release.sh", res.render())

    def test_adding_the_decorator_GREENs_it(self):
        self.assertEqual(sr.GREEN, _one(GUARDED).verdict)
        self.assertEqual(sr.BASIS_ALL_READS_GUARDED, _one(GUARDED).basis)

    def test_removing_the_read_GREENs_it_by_a_DIFFERENT_basis(self):
        """Two ways to be green, and they are not the same fact — one file reads nothing, the
        other reads and is protected. Collapsing them would hide a guard that got deleted."""
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, _one(NO_READ).basis)
        self.assertNotEqual(_one(NO_READ).basis, _one(GUARDED).basis)

    def test_the_report_lines_name_the_file(self):
        lines = sr.report((_one(OFFENDER, name="tests/test_bad.py"), _one(NO_READ)))
        self.assertEqual(1, len(lines))
        self.assertTrue(lines[0].startswith("tests/test_bad.py: RED"))

    def test_taint_crosses_ONE_module_hop_through_an_accessor(self):
        """`mb.read_script(ROOT)` is how the real stage-11 offender hid; supplied here so the
        mechanism is graded even after that file is fixed."""
        helper = ("import os\n"
                  "def read_it(base):\n"
                  "    return open(os.path.join(base, 'scripts', 'release.sh')).read()\n")
        caller = _HEAD + ("import _helper as h\n"
                          "class TestViaHelper(unittest.TestCase):\n"
                          "    def test_it(self):\n"
                          "        self.assertIn('set', h.read_it(ROOT))\n")
        results = sr.resolve_all(
            {"tests/_helper.py": helper, "tests/test_caller.py": caller}, FIXTURE_EXCLUDES)
        by_name = {r.filename: r for r in results}
        self.assertEqual(sr.RED, by_name["tests/test_caller.py"].verdict)
        self.assertIn("h.read_it(ROOT)", by_name["tests/test_caller.py"].render())
        self.assertEqual(sr.BASIS_NO_EXCLUDED_READS, by_name["tests/_helper.py"].basis,
                         "the helper itself reads nothing — its caller decides")

    def test_an_accessor_called_with_a_temp_dir_is_not_a_read(self):
        helper = ("import os\n"
                  "def build(base):\n"
                  "    os.makedirs(os.path.join(base, 'docs', 'build'))\n")
        caller = _HEAD + ("import tempfile\n"
                          "import _helper as h\n"
                          "class TestFixture(unittest.TestCase):\n"
                          "    def test_it(self):\n"
                          "        h.build(tempfile.mkdtemp())\n")
        results = sr.resolve_all(
            {"tests/_helper.py": helper, "tests/test_caller.py": caller}, FIXTURE_EXCLUDES)
        self.assertEqual(
            [], [r.filename for r in sr.offenders(results)],
            "a helper handed a temp directory builds a fixture; the boundary has no opinion")


class TestTheCorpusIsSuppliedAndNotDiscovered(unittest.TestCase):
    """The sweep is a pure function over a supplied corpus — a walk that discovers AND judges
    cannot be graded on a defect the tree does not have."""

    def test_resolve_all_opens_nothing(self):
        results = sr.resolve_all({"tests/test_a.py": OFFENDER, "tests/test_b.py": NO_READ},
                                 FIXTURE_EXCLUDES)
        self.assertEqual(("tests/test_a.py",), tuple(r.filename for r in sr.offenders(results)))

    def test_a_None_source_in_the_corpus_becomes_UNDECIDABLE_and_not_a_skip(self):
        results = sr.resolve_all({"tests/test_a.py": None}, FIXTURE_EXCLUDES)
        self.assertEqual(sr.BASIS_SOURCE_UNREADABLE, results[0].basis)

    def test_the_shipped_dirs_are_named_in_exactly_one_place(self):
        """Blind spot 6 has one address, so widening the corpus is a deliberate edit."""
        self.assertEqual(("tests", "tests/integration"), sr.SHIPPED_TEST_DIRS)


@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only, excluded from the public mirror")
class TestTheRealShippedCorpusIsClean(unittest.TestCase):
    """The sweep, over the tree it is meant to protect.

    Guarded for the reason the whole stage is about: deriving the exclude set means READING
    `sync-public.sh`, which does not ship. On the mirror there is no exclude list, every
    derivation would be `BASIS_NO_EXCLUDES`, and a class that can only return UNDECIDABLE should
    not be collected at all.
    """

    @classmethod
    def setUpClass(cls):
        script = mb.read_script(ROOT)
        cls.excludes = sr.tracked_excludes(ROOT, mb.exclude_entries(script)) if script else None
        cls.corpus = sr.shipped_test_sources(ROOT)
        cls.results = sr.resolve_all(cls.corpus, cls.excludes or frozenset())

    def test_release_sh_is_absent_ONLY_because_of_the_mirror_boundary(self):
        """★ The companion that stops the guard above from hiding a deletion.

        `sync-public.sh` and `release.sh` are held back by the same two controls, so on any tree
        where one is present the other must be. Without this, `rm scripts/sync-public.sh` would
        silently uncollect this whole class and the run would still report OK — "the file is
        excluded from the mirror" and "someone deleted the file" would share a green.
        """
        self.assertTrue(
            os.path.exists(RELEASE_SH),
            "scripts/sync-public.sh is present, so this is the PRIVATE tree — but "
            "scripts/release.sh is gone. The sweep below would still pass while the mirror "
            "boundary it derives from had been half deleted.")

    def test_the_exclude_set_is_real_and_not_an_empty_read(self):
        """Anti-vacuity: an empty deriving set would make every file GREEN while proving nothing.

        WHAT THE FLOOR COUNTS, AND WHY THAT NUMBER. It counts the literal `--exclude` entries this
        repo's **git index** carries — `len(sr.DECLARED_TRACKED_EXCLUDES)`, seven — and it holds
        the derived set to that declaration in BOTH directions. Seven is not a constant chosen to
        make the run pass: it is the size of a reviewed list of internal paths, and losing one is
        reported as loudly as gaining one, which a bare `>= n` cannot do.

        Not met would mean one of three things, and each has its own answer: the index could not be
        read (UNDECIDABLE), nothing excluded is tracked at all (UNDECIDABLE — the sweep is judging
        against nothing), or the repo's internal-path set moved and the declaration was not
        (RED, naming the drift in both directions).

        ★ It used to count what was ON DISK, and that is the defect this replaced. `docs/marketing/`
        is gitignored, so the old floor of 10 was met on exactly one laptop and RED in every clone
        — the anti-vacuity guard itself passing vacuously, on state no clone has.
        """
        self.assertIsNotNone(self.excludes, "sync-public.sh could not be read")
        res = sr.resolve_deriving_set(self.excludes)
        self.assertEqual(sr.GREEN, res.verdict, res.render())
        self.assertGreaterEqual(
            len(self.excludes), len(sr.DECLARED_TRACKED_EXCLUDES),
            "fewer literal excludes are tracked in this repo than the declaration names (%s); the "
            "sweep would be judging against less than it claims" % sorted(self.excludes))
        for required in ("docs/build", "scripts/release.sh", "scripts/sync-public.sh"):
            self.assertIn(required, self.excludes)

    def test_the_deriving_set_is_read_from_the_index_and_not_from_the_disk(self):
        """★ The pin on the fix itself. An untracked path present in this working tree must not
        enter the deriving set — that is what made the floor above machine-dependent.

        Asserted structurally rather than by naming `docs/marketing/`: the claim is that NO entry
        of the deriving set is absent from the index, whatever this particular disk holds.
        """
        tracked = sr.tracked_paths(ROOT)
        self.assertIsNotNone(tracked, "git ls-files could not be read in this checkout")
        untracked = sorted(e for e in self.excludes if e not in tracked)
        self.assertEqual([], untracked,
                         "these deriving-set entries are not in the git index, so they are in no "
                         "clone and the set measures this disk: %s" % untracked)

    def test_the_corpus_is_the_whole_shipped_test_tree(self):
        self.assertGreaterEqual(
            len(self.corpus), 300,
            "the shipped-test corpus collapsed to %d files; the sweep is judging a fraction of "
            "what ships" % len(self.corpus))
        self.assertIn("tests/test_stage68_supply_chain.py", self.corpus)
        self.assertIn("tests/integration/test_mcp_server.py", self.corpus)

    def test_no_result_is_UNDECIDABLE(self):
        """An UNDECIDABLE row is not a pass; it means a shipped file could not be judged."""
        undecided = sr.undecided(self.results)
        self.assertEqual(
            [], [r.render() for r in undecided],
            "these shipped test files could not be judged at all: %s"
            % ", ".join(r.filename for r in undecided))

    def test_the_sweep_still_sees_the_guards_it_is_meant_to_see(self):
        """Anti-vacuity for the OTHER direction: if the reader stopped resolving reads entirely,
        every file would be `no_excluded_reads` and the sweep would pass by seeing nothing."""
        guarded = [r.filename for r in self.results if r.basis == sr.BASIS_ALL_READS_GUARDED]
        self.assertIn("tests/test_stage68_supply_chain.py", guarded)
        self.assertGreaterEqual(
            len(guarded), 4,
            "the sweep resolved almost no boundary reads at all (%s) — a reader that sees "
            "nothing passes everything" % guarded)

    def test_no_shipped_test_reads_an_internal_file_without_a_guard(self):
        lines = sr.report(self.results)
        self.assertEqual(
            [], list(lines),
            "these SHIPPED test files read a path the public mirror does not carry. On the "
            "mirror the read raises and the test ERRORS, so the suite is green here and broken "
            "in the repo users clone — and `run_public_subset_preflight` refuses the cut.\n"
            "The fix is the class DECORATOR `@unittest.skipUnless(os.path.exists(PATH), ...)` "
            "(test_stage68_supply_chain.py:342), never a setUpClass skip, plus a companion "
            "asserting the file is absent ONLY because of the mirror boundary.\n\n  "
            + "\n  ".join(lines))


if __name__ == "__main__":
    unittest.main()
