"""B1 — the tests that grade the release tooling meet their subject, and a SKIP stops passing for a PASS.

0.0.19 stage 05. Row B1 (`internal-tests-meet-their-subject`), building to doc 104 §8c (F3).

THE DEFECT, STATED AS THE ROW DID NOT
--------------------------------------
A block of shipped tests grades `scripts/release.sh`, `scripts/sync-public.sh` and `CLAUDE.md`'s
ships-list. Those files are excluded from the public mirror by BOTH controls, so on the mirror —
the one CI a billing ruling did not close — every one of those tests reports `... skipped`, the run
reports `OK`, and nothing anywhere distinguishes *"the subject is absent, correctly"* from *"this
graded nothing."* On the dev repo, where the subjects exist, Actions is exhausted by ruling
(2026-08-16). **Grader and subject never met on a runner.**

⭐ **THE ACTUAL DEFECT IS NOT THAT THEY SKIP.** Skipping on the mirror is right — the subject really
is not there. The defect is that a skip and a pass are the same colour, so a class that has stopped
grading anything ANYWHERE looks exactly like one that graded and passed. That is §7g in the test
layer, and §7g's fix is always the same: split the representation.

WHAT THE ROW GOT WRONG, MEASURED (§7j)
---------------------------------------
The row scoped this to four files and called it "the 105 false greens". Deriving the property
instead of typing its scope gives different numbers, and two independent mechanisms agree on them:

  * the four named files hold **105 tests**, of which **26** are green-by-skip on the mirror. The
    other 79 grade identically on both sides, and one whole file — `test_stage9_removal_release.py`,
    the one the brief could not find a skip mechanism for — carries **no existence guard at all**
    and runs its full 37 on the mirror.
  * the CLASS is **27 guarded classes / 109 tests across 14 files**, plus **4 run-time-decided
    companion skips** — **113 tests** that grade here and report `... skipped` there, measured on a
    mirror this harness builds. A B1 fix scoped to four files would have left 21 classes standing.

So nothing below takes the four files as input. `_internal_subject.guarded_classes` sweeps the whole
shipped corpus, and the four files are an ASSERTION ABOUT the result — the meta-test in
`TestTheGuardedCorpusIsDerivedNotTyped`, which reds if the sweep stops reaching them.

THE FIX, IN THREE PARTS
------------------------
1. **A tree names itself** (`_internal_subject.tree_state`): DEV / MIRROR / INCOHERENT — three
   states, three representations. A checkout missing `CLAUDE.md` while still carrying `docs/build/`
   is a dev tree that LOST a file, and it is now a different answer from the mirror rather than the
   same one.
2. **A census pairs the tree with the outcome**, and BOTH sides are asserted. On the dev tree every
   guarded class must GRADE; on the mirror every one must be ABSENT_BY_DESIGN and the census NAMES
   them. "Did not execute" acquires a rendering that "passed" does not have.
3. **The sync harness makes subject and grader meet** (F3): the real `scripts/sync-public.sh` is run
   against the real dev tree into a throwaway public checkout, and every guarded module is then
   executed TWICE — in the dev tree, where it must grade with zero skips, and in the mirror the
   harness just built, where it must skip exactly the derived set. One runner, both colours, on the
   machine where the subjects live.

TWO DERIVATIONS OF THE SAME SET, AND WHY THAT IS NOT §7f REDUNDANCY. The ungraded set is derived
twice by unrelated means: statically, from the decorator AST (`_internal_subject`), and dynamically,
from `unittest`'s own load-time `__unittest_skip__` state (`skip_inventory.collect`, which exists
already and answers in ~1s without running anything). They are not two defences covering for each
other — neither is asserted to be right on its own. Their AGREEMENT is the assertion, and either one
drifting reds. Measured at build time: both say 109 tests in the same 27 classes, and the run-time
arm (`runtime_boundary_skips`) accounts for the remaining 4 that neither decorator reader can see.

⛔ WHAT THIS STAGE REFUSED TO DO. The mirror exclusion is NOT inverted — shipping a graded artifact
so its grader could run would put a hole in what `CLAUDE.md` calls the ONLY control on the
public/OSS boundary, in order to fix a test-coverage problem. `TestTheBoundaryIsUnchangedByThisStage`
pins both halves against a transcript taken before this stage. Nor is any of this a `release.sh`
preflight: a script may not be its own grader, and `release.sh` is untouched.

WHY A GREEN RUN IS NOT THIS FILE'S DELIVERABLE (§7i). Every class here is shown RED against a
mutated subject; `tests/_b1_mutants.sh` is the driver and the stage report pastes the failures. A
harness that has only ever seen a healthy tree grades nothing, and 0.0.18's close booked that exact
shape twice on one release.

SECRET-SAFETY. The harness runs subprocesses over a real checkout on a developer machine whose
environment may carry a live DSN. Children get a strict ALLOWLIST environment (`_child_env`) — no
`*DSN*`, `*PASSWORD*`, `*TOKEN*`, `*SECRET*`, `*KEY*` or `MOKATA_*` value is inherited, so none can
be printed by a child at all — and every captured stream goes through `_redact` against the parent's
own sensitive values before it can reach an assertion message.

Offline; no network. The harness class needs `bash`, `rsync` and `git`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _internal_subject as isub
import _mirror_bookkeeping as mb
import _shipped_reads as sr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Internal: excluded from the mirror by both controls. `_shipped_reads` reads a module-level
# anchored constant as TAINT, so every scope that names this outside an existence probe must be
# class-decorator guarded — and the census classes below must stay unguarded to run on the mirror.
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

#: This module's own basename. The harness runs every guarded module; running ITSELF would put a
#: sync harness inside a sync harness. Derived from `__file__`, never typed.
SELF = "tests/%s" % os.path.basename(os.path.abspath(__file__))

#: Three of the four files the row named. NOT the sweep's input — an assertion about its output.
ROW_MODULES = (
    "tests/test_claude_md_ships_list.py",
    "tests/test_stage61b_release_process.py",
    "tests/test_sync_public_nested_checkout.py",
)

#: The row's FOURTH file, pinned by its ABSENCE from the guarded set. The brief flagged that its
#: skip mechanism "was NOT located by the coordinator — derive it; it may differ." It differs: it
#: has none. No existence guard, no boundary-crossing read, all 37 tests run on the mirror.
ROW_UNGUARDED_MODULE = "tests/test_stage9_removal_release.py"

_TOOLS = [t for t in ("bash", "rsync", "git") if shutil.which(t) is None]

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

#: Environment names whose VALUES may never reach a child process or a failure message.
_SENSITIVE = re.compile(r"DSN|PASSWORD|PASSWD|SECRET|TOKEN|_KEY|APIKEY|CREDENTIAL|MOKATA_",
                        re.IGNORECASE)

#: The only names a child inherits. An ALLOWLIST, because a denylist of secret-shaped names is a
#: guess about what a future environment variable will be called.
_ENV_ALLOW = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "COMSPEC", "PATHEXT")


def _child_env():
    env = {name: os.environ[name] for name in _ENV_ALLOW if name in os.environ}
    env.update(_GIT_ENV)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _redact(text):
    """Replace any sensitive parent-environment VALUE with a marker. Belt-and-braces: no child ever
    received one, so this can only fire on something the harness itself printed."""
    for name, value in os.environ.items():
        if value and len(value) > 3 and _SENSITIVE.search(name):
            text = text.replace(value, "<redacted:%s>" % name)
    return text


def _corpus(root):
    return sr.shipped_test_sources(root)


def guarded_modules(root):
    """Every shipped test module carrying an existence-guarded class, minus this one."""
    return tuple(sorted({g.filename for g in isub.guarded_classes(_corpus(root))
                         if g.filename != SELF}))


class RunReport(object):
    """Read-only diagnostic: what one `unittest` invocation did, in one tree."""

    __slots__ = ("rc", "ran", "skipped", "output")

    def __init__(self, rc, ran, skipped, output):
        self.rc = rc
        self.ran = ran
        self.skipped = skipped
        self.output = output

    def render(self, limit=60):
        """The failing part of the child's output, not all of it.

        A 505-test run emits a 500-character dot line and every passing case; pasting the lot into
        an assertion message buries the one thing a reader needs. The FULL text stays on `.output`
        for anything that wants it — this is the view, and it says when it truncated (no silent
        caps)."""
        body = self.output
        marker = body.find("=" * 70)
        if marker == -1:
            marker = body.find("FAIL:")
        if marker != -1:
            body = body[marker:]
        lines = body.splitlines()
        if len(lines) > limit:
            lines = lines[:limit] + ["... (%d more lines; the full text is on .output)"
                                     % (len(body.splitlines()) - limit)]
        return ("rc=%s Ran=%s skipped=%s\n--- the child's failures ---\n%s"
                % (self.rc, self.ran, self.skipped, "\n".join(lines)))


#: `_run_modules` refused to run because it was handed nothing. Its own `rc`, never 0 and never 1.
NO_MODULES = -2


def _run_modules(root, modules):
    """Run `modules` with unittest inside `root/tests`.

    ★ AN EMPTY MODULE LIST IS A REFUSAL, NOT A RUN — found by mutant D01, which blinds the guard
    sweep. `python -m unittest` **with no test names falls back to DISCOVERY** and runs the entire
    7,300-test suite. So a harness whose derivation had gone blind would have selected zero modules,
    run everything, reported `rc=0`, and passed: "I graded the guarded modules" and "I graded
    nothing, so I graded everything instead" sharing one representation, inside the stage whose
    subject is that exact collapse. Caught because the mutant took eleven minutes instead of thirty
    seconds — the §7i point, that a mutant earns its place by telling you something a green run
    could not.
    """
    if not modules:
        return RunReport(NO_MODULES, 0, 0,
                         "NO MODULES WERE SELECTED. The guarded-class derivation returned nothing, "
                         "so there was nothing to run. `unittest` with no names would have "
                         "discovered and run the WHOLE suite and reported success; this refuses "
                         "instead.")
    names = [os.path.basename(m)[:-3] for m in modules]
    proc = subprocess.run([sys.executable, "-m", "unittest", *names],
                          cwd=os.path.join(root, "tests"),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          env=_child_env())
    output = _redact(proc.stdout + proc.stderr)
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    skipped = re.search(r"skipped=(\d+)", output)
    return RunReport(proc.returncode,
                     int(ran.group(1)) if ran else -1,
                     int(skipped.group(1)) if skipped else 0,
                     output)


def _inventory(root):
    """`skip_inventory.collect` in `root`, as JSON. The SECOND, independent derivation: it reads
    `unittest`'s own load-time skip state rather than any AST of ours."""
    proc = subprocess.run([sys.executable, os.path.join(root, "tests", "skip_inventory.py"),
                           "--json"],
                          cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          env=_child_env())
    if proc.returncode != 0:
        raise RuntimeError(_redact(proc.stdout + proc.stderr))
    return json.loads(proc.stdout)


def _first_class(source):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    return sr.guard_of(cls), sr.guard_subjects(cls, tree)


# =================================================================================================
# THE DERIVATION — the guarded corpus is swept, never typed (§7j)
# =================================================================================================

class TestTheGuardedCorpusIsDerivedNotTyped(unittest.TestCase):
    """Pure over supplied sources, plus one sweep of the SHIPPED test corpus — which ships, so every
    assertion here holds identically on both sides of the boundary. Nothing here opens an internal
    file, which is why it carries no guard and must never acquire one."""

    # --- the reader, against planted guards (§7i) -------------------------------------------------
    def test_a_guard_keyed_on_a_module_constant_names_its_subject(self):
        guard, subjects = _first_class(
            'import os, unittest\n'
            'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
            'RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")\n'
            '@unittest.skipUnless(os.path.exists(RELEASE_SH), "dev-only")\n'
            'class T(unittest.TestCase):\n    def test_x(self):\n        pass\n')
        self.assertEqual(guard, sr.GUARD_DECORATOR)
        self.assertEqual(subjects, frozenset({"scripts/release.sh"}))

    def test_a_guard_that_joins_in_place_names_its_subject_too(self):
        _guard, subjects = _first_class(
            'import os, unittest\n'
            'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
            '@unittest.skipUnless(os.path.exists(os.path.join(ROOT, "CLAUDE.md")), "dev-only")\n'
            'class T(unittest.TestCase):\n    def test_x(self):\n        pass\n')
        self.assertEqual(subjects, frozenset({"CLAUDE.md"}))

    def test_an_AND_of_two_probes_names_BOTH_subjects(self):
        _guard, subjects = _first_class(
            'import os, unittest\n'
            'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
            'A = os.path.join(ROOT, "CLAUDE.md")\n'
            'B = os.path.join(ROOT, "scripts", "sync-public.sh")\n'
            '@unittest.skipUnless(os.path.exists(A) and os.path.exists(B), "dev-only")\n'
            'class T(unittest.TestCase):\n    def test_x(self):\n        pass\n')
        self.assertEqual(subjects, frozenset({"CLAUDE.md", "scripts/sync-public.sh"}))

    def test_a_guard_whose_subject_cannot_be_resolved_is_UNDECIDABLE_not_graded(self):
        """★ THE VACUITY THIS REFUSES. An unresolved guard yields an EMPTY subject set, and
        "nothing is missing" is then true having asked nothing — byte-identical to a real GRADED.
        `_shipped_reads`' declared blind spot 1 (paths built at run time) is exactly where such a
        guard comes from, so this is a live shape rather than a hypothetical one."""
        _guard, subjects = _first_class(
            'import os, unittest\n'
            'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
            'NAME = "release.sh"\n'
            '@unittest.skipUnless(os.path.exists(os.path.join(ROOT, "scripts", NAME)), "x")\n'
            'class T(unittest.TestCase):\n    def test_x(self):\n        pass\n')
        self.assertEqual(subjects, frozenset())
        self.assertEqual(isub.subject_state(ROOT, subjects), isub.UNDECIDABLE)

    def test_a_setUpClass_skip_is_not_an_accepted_guard_and_names_nothing(self):
        guard, subjects = _first_class(
            'import os, unittest\n'
            'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
            'RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")\n'
            'class T(unittest.TestCase):\n'
            '    @classmethod\n'
            '    def setUpClass(cls):\n'
            '        if not os.path.exists(RELEASE_SH):\n'
            '            raise unittest.SkipTest("dev-only")\n'
            '    def test_x(self):\n        pass\n')
        self.assertEqual(guard, sr.GUARD_SETUPCLASS_SKIP)
        self.assertNotIn(guard, sr.ACCEPTED_GUARDS)
        self.assertEqual(subjects, frozenset())

    # --- the real corpus --------------------------------------------------------------------------
    def test_the_sweep_of_the_real_corpus_is_not_vacuous(self):
        corpus = _corpus(ROOT)
        self.assertGreater(len(corpus), 100, "the shipped test corpus could not be read")
        self.assertEqual(isub.unparseable(corpus), (),
                         "a shipped test file could not be parsed, so the sweep is blind to it — "
                         "an unparseable file is NOT a clean one")
        guards = isub.guarded_classes(corpus)
        self.assertGreaterEqual(
            len(guards), 20,
            "only %d existence-guarded classes were found. The sweep has stopped seeing them and "
            "every assertion built on it is now vacuously true." % len(guards))

    def test_a_file_the_sweep_CANNOT_READ_is_reported_rather_than_read_as_clean(self):
        """★ MUTANT D03 SURVIVED, and §7i says why: the real corpus has no broken file, so the
        reporting arm never executed and `unparseable() == ()` was true for the same reason on a
        healthy tree whether the arm worked or not. The offenders are supplied here.

        This is the vacuity that matters most in this module: a file that will not parse
        contributes ZERO guarded classes, which is indistinguishable from a file that has none —
        so a silent drop would shrink the sweep and every assertion built on it, invisibly."""
        corpus = {"tests/broken.py": "class T(:\n", "tests/gone.py": None}
        bad = dict(isub.unparseable(corpus))
        self.assertIn("tests/broken.py", bad)
        self.assertTrue(bad["tests/broken.py"].startswith("unparseable:"), bad)
        self.assertEqual(bad.get("tests/gone.py"), "unreadable")
        self.assertEqual(
            isub.guarded_classes(corpus), (),
            "a file the sweep cannot read must contribute NOTHING — which is exactly why its "
            "absence has to be REPORTED and not inferred from an empty result")

    def test_every_guarded_class_names_at_least_one_subject(self):
        nameless = [g.key for g in isub.guarded_classes(_corpus(ROOT)) if not g.subjects]
        self.assertEqual(
            nameless, [],
            "these classes are guarded on a path the reader could not resolve, so nothing can say "
            "whether they graded or not: %s" % nameless)

    def test_the_row_modules_are_all_REACHED_by_the_derivation(self):
        """★ THE META-TEST. A harness that silently stops running the files it exists for is the
        original bug with a new address. The sweep's INPUT is the whole corpus; these three are an
        assertion about its OUTPUT."""
        reached = {g.filename for g in isub.guarded_classes(_corpus(ROOT))}
        for module in ROW_MODULES:
            self.assertIn(module, reached,
                          "%s carries the guarded classes this stage exists for, and the "
                          "derivation no longer reaches it" % module)

    def test_the_rows_fourth_file_is_guarded_by_nothing_and_that_is_correct(self):
        """Derived where the brief could not: this file has NO skip mechanism. Pinned so the
        finding cannot quietly rot back into the row's assumption."""
        corpus = _corpus(ROOT)
        self.assertIn(ROW_UNGUARDED_MODULE, corpus, "the row's fourth file has left the corpus")
        guarded = {g.filename for g in isub.guarded_classes(corpus)}
        self.assertNotIn(
            ROW_UNGUARDED_MODULE, guarded,
            "%s has acquired an existence guard. It had none, which is why it is the one file of "
            "the four grading identically on both sides — if that changed, this stage's measured "
            "26-of-105 is stale." % ROW_UNGUARDED_MODULE)

    def test_an_empty_module_list_is_REFUSED_rather_than_run_as_discovery(self):
        """★ THE MUTANT THAT PAID FOR ITSELF (D01). `python -m unittest` with no names discovers
        and runs the entire suite, so a blinded derivation would have selected nothing, run
        everything, and reported success. The refusal has its own rc, distinct from both a pass
        and an ordinary failure."""
        report = _run_modules(ROOT, ())
        self.assertEqual(report.rc, NO_MODULES)
        self.assertNotEqual(report.rc, 0)
        self.assertEqual(report.ran, 0)
        self.assertIn("NO MODULES WERE SELECTED", report.render())

    def test_the_harness_never_lists_ITSELF_among_the_modules_it_runs(self):
        """A sync harness inside a sync harness is not a deeper check, it is a loop."""
        self.assertNotIn(SELF, guarded_modules(ROOT))
        self.assertIn(SELF, {g.filename for g in isub.guarded_classes(_corpus(ROOT))},
                      "this module has stopped carrying a guarded class, so the exclusion above "
                      "is now removing nothing and no longer proves anything")


# =================================================================================================
# THE RUN-TIME COMPANIONS — the population a decorator sweep cannot see
# =================================================================================================

class TestTheRuntimeDecidedSkipsAreCountedToo(unittest.TestCase):
    """★ WHERE THE FIRST CUT OF THIS HARNESS WAS WRONG, AND THE RED THAT SAID SO. It asserted the
    mirror would skip exactly the decorator-derived 109 and the run reported **113**. The four extra
    are `self.skipTest(...)` calls in test BODIES — the §7g companions in `test_tag_is_not_a_publish`,
    `test_s11_bookkeeping_derived`, `test_s12_release_publisher` and `test_s27_preflight_parity`,
    each asking *"sync-public.sh is here, so why is release.sh gone?"*. `skip_inventory` declares
    this blind spot in its own header. Reporting 109 and calling the other four noise would have been
    a count standing in for an inventory — this stage's own subject, committed inside its fix.

    Unguarded: pure over supplied sources plus the shipped corpus, so it runs on both sides."""

    @staticmethod
    def _rows(body):
        source = ('import os, unittest\n'
                  'ROOT = os.path.dirname(os.path.abspath(__file__))\n'
                  'SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")\n'
                  'class T(unittest.TestCase):\n'
                  '    def test_x(self):\n' + body)
        return isub.runtime_boundary_skips({"tests/t.py": source})

    def test_a_companion_that_skips_when_the_subject_is_ABSENT_is_counted(self):
        rows = self._rows('        if not os.path.exists(SYNC_SH):\n'
                          '            self.skipTest("public subset")\n'
                          '        self.assertTrue(True)\n')
        self.assertEqual([r.key for r in rows], ["tests/t.py::T.test_x"])
        self.assertEqual(rows[0].subjects, ("scripts/sync-public.sh",))

    def test_a_skip_that_fires_when_the_subject_is_PRESENT_is_NOT_counted(self):
        """The negation is load-bearing. `if exists(X): skipTest()` fires on the DEV tree, and
        counting it in the mirror column would put a skip where none will happen."""
        self.assertEqual(self._rows('        if os.path.exists(SYNC_SH):\n'
                                    '            self.skipTest("dev only")\n'), ())

    def test_a_unary_operator_that_is_NOT_negation_does_not_make_a_mirror_skip(self):
        """★ A SHAPE GUARD, AND IT IS LABELLED AS ONE (§7f). Mutant R02 widened
        `isinstance(child, ast.UnaryOp) and isinstance(child.op, ast.Not)` to accept ANY unary
        operator and **SURVIVED**: the offender above (`if exists(X): skipTest()`) carries no
        `UnaryOp` at all, so it grades the first clause and is blind to the second. Two halves
        covering for each other, neither graded — the §7f shape exactly.

        The offender below is deliberately ARTIFICIAL. `-os.path.exists(...)` is not code anyone
        writes, and saying so is the point: doc 85 §7f's remedy for two mutually-covering defences
        is to give each one an offender only IT can see, not to delete a clause that is
        semantically right. `ast.Not` is the only unary operator that means "absent"; without this
        the widening is free."""
        self.assertEqual(self._rows('        if -os.path.exists(SYNC_SH):\n'
                                    '            self.skipTest("a UnaryOp that is not a negation")\n'),
                         ())

    def test_a_skip_on_something_that_is_not_the_boundary_is_NOT_counted(self):
        """A platform or tooling skip is a real skip and a different fact. `test_mutation_harness`
        has eight of them; sweeping those in would inflate the boundary's cost eightfold."""
        self.assertEqual(self._rows('        if not shutil.which("git"):\n'
                                    '            self.skipTest("git not on PATH")\n'), ())

    def test_a_skipTest_in_PROSE_is_not_a_use_of_one(self):
        """Parsed, never grepped — `skip_inventory`'s own rule, and this corpus is full of tests
        whose fixtures are source code ABOUT skipping."""
        self.assertEqual(self._rows('        """if not os.path.exists(SYNC_SH):\n'
                                    '        self.skipTest("x")"""\n'
                                    '        self.assertTrue(True)\n'), ())

    def test_the_real_corpus_carries_exactly_the_four_known_companions(self):
        rows = isub.runtime_boundary_skips(_corpus(ROOT))
        self.assertEqual(
            sorted({r.filename for r in rows}),
            ["tests/test_s11_bookkeeping_derived.py",
             "tests/test_s12_release_publisher.py",
             "tests/test_s27_preflight_parity.py",
             "tests/test_tag_is_not_a_publish.py"],
            "the §7g companion population changed: %s" % sorted(r.key for r in rows))

    def test_every_companion_lives_in_a_file_skip_inventory_also_flags(self):
        """The existing tool names the FILES that contain a run-time skip; this names the METHODS
        and the subject. If the two ever disagree about a file, one of them has gone blind."""
        inventory = _inventory(ROOT)
        sites = set(inventory["runtime_skip_sites"])
        for row in isub.runtime_boundary_skips(_corpus(ROOT)):
            self.assertIn(os.path.basename(row.filename), sites, row.render())

    def test_the_census_total_is_LARGER_than_its_decorator_count(self):
        """The two populations are kept apart and added up in one place. A `skips_expected` equal
        to `ungraded_tests` would mean the run-time arm found nothing and the harness is back to
        predicting 109 against a run that reports 113."""
        report = isub.census(ROOT, _corpus(ROOT))
        if report.tree.kind == isub.TREE_DEV:
            self.assertEqual([r.key for r in report.runtime_ungraded], [],
                             "a companion is counted as skipping in the tree where its subject "
                             "EXISTS — it will run there, so the prediction is wrong by four")
        planted = isub.CensusReport(isub.TreeReport(isub.TREE_MIRROR), (),
                                    isub.guarded_classes(_corpus(ROOT)),
                                    (), isub.runtime_boundary_skips(_corpus(ROOT)))
        self.assertGreater(planted.skips_expected(), planted.ungraded_tests)
        self.assertIn("run-time-decided", planted.render())


# =================================================================================================
# THE TREE NAMES ITSELF — three states, three representations (§7g)
# =================================================================================================

class TestTheTreeNamesItselfInsteadOfBeingGuessed(unittest.TestCase):
    """Unguarded on purpose: this is a class that has to run ON THE MIRROR, where every internal
    witness is absent. It only ever PROBES for those paths and never opens one."""

    def tmp(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        return directory

    @staticmethod
    def _plant(root, rels):
        for rel in rels:
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path) or root, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("x\n")

    def _shipped_only(self):
        root = self.tmp()
        self._plant(root, isub.SHIPPED_WITNESSES)
        return root

    def test_this_checkout_knows_which_side_of_the_boundary_it_is_on(self):
        report = isub.tree_state(ROOT)
        self.assertTrue(report.coherent, report.render())
        self.assertIn(report.kind, (isub.TREE_DEV, isub.TREE_MIRROR))

    def test_a_complete_mirror_is_recognised_as_one(self):
        self.assertEqual(isub.tree_state(self._shipped_only()).kind, isub.TREE_MIRROR)

    def test_a_complete_dev_tree_is_recognised_as_one(self):
        root = self._shipped_only()
        self._plant(root, isub.INTERNAL_WITNESSES)
        self.assertEqual(isub.tree_state(root).kind, isub.TREE_DEV)

    def test_a_dev_tree_that_LOST_a_file_is_INCOHERENT_not_a_mirror(self):
        """★ THE OFFENDER NO COMPANION IN THIS REPO HAS EVER SEEN. `CLAUDE.md` gone while
        `docs/build/` is still here is a broken dev checkout, and under `skipUnless(exists(...))`
        alone it is indistinguishable from the mirror: the guarded classes skip and the run says OK.
        Here it is a THIRD answer with its own rendering, and it is never green."""
        root = self._shipped_only()
        self._plant(root, [r for r in isub.INTERNAL_WITNESSES if r != "CLAUDE.md"])
        report = isub.tree_state(root)
        self.assertEqual(report.kind, isub.TREE_INCOHERENT)
        self.assertIn("lost a file", report.render())
        self.assertEqual(isub.subject_state(root, ("CLAUDE.md",), report), isub.UNDECIDABLE)

    def test_the_mirror_and_the_broken_dev_tree_give_the_SAME_subject_TWO_answers(self):
        """§7g written as an equality that must NOT hold: the identical missing file is
        ABSENT_BY_DESIGN in one tree and UNDECIDABLE in the other. Collapse those and the whole
        mechanism is back to one colour."""
        mirror = self._shipped_only()
        broken = self._shipped_only()
        self._plant(broken, ["docs/build"])
        self.assertEqual(isub.subject_state(mirror, ("CLAUDE.md",)), isub.ABSENT_BY_DESIGN)
        self.assertEqual(isub.subject_state(broken, ("CLAUDE.md",)), isub.UNDECIDABLE)

    def test_a_directory_that_is_not_mokata_is_INCOHERENT_and_names_what_is_missing(self):
        report = isub.tree_state(self.tmp())
        self.assertEqual(report.kind, isub.TREE_INCOHERENT)
        self.assertIn("not a mokata checkout", report.render())
        self.assertEqual(sorted(report.shipped_missing), sorted(isub.SHIPPED_WITNESSES))

    def test_the_three_tree_kinds_render_three_different_sentences(self):
        """A split representation that renders the same text in two states is not split."""
        mirror = self._shipped_only()
        dev = self._shipped_only()
        self._plant(dev, isub.INTERNAL_WITNESSES)
        broken = self._shipped_only()
        self._plant(broken, ["docs/build"])
        rendered = [isub.tree_state(t).render() for t in (dev, mirror, broken)]
        self.assertEqual(len(set(rendered)), 3, rendered)


# =================================================================================================
# THE CENSUS — the tree's state and the run's outcome are asserted TOGETHER
# =================================================================================================

class TestTheCensusPairsTheTreeWithTheOutcome(unittest.TestCase):
    """Also unguarded, and that is the point: on the mirror this class RUNS and asserts that the
    guarded classes did NOT grade. A run in which they did not execute is now a run that says so."""

    def setUp(self):
        self.report = isub.census(ROOT, _corpus(ROOT))

    def test_nothing_in_this_tree_is_UNDECIDABLE(self):
        self.assertEqual([g.key for g in self.report.undecidable], [], self.report.render())

    def test_the_tree_and_the_grading_agree_in_BOTH_directions(self):
        """The pairing. Either every guarded class grades (dev) or none does (mirror); anything in
        between is a tree that has lost a file while still reporting OK."""
        if self.report.tree.kind == isub.TREE_DEV:
            self.assertEqual(
                [g.key for g in self.report.ungraded], [],
                "this is the dev checkout and these guarded classes are NOT grading their "
                "subject:\n%s" % self.report.render())
            self.assertGreaterEqual(len(self.report.graded), 20, self.report.render())
        else:
            self.assertEqual(
                [g.key for g in self.report.graded], [],
                "this is the public mirror and these classes claim to grade a file the mirror "
                "does not carry:\n%s" % self.report.render())
            self.assertGreaterEqual(
                self.report.ungraded_tests, 90,
                "the mirror reports only %d ungraded tests. Either the sweep stopped seeing them "
                "or they stopped being guarded — both are the false green this ends.\n%s"
                % (self.report.ungraded_tests, self.report.render()))

    def test_a_skipped_class_is_NAMED_by_the_census_and_a_passing_one_is_not(self):
        """The whole stage in one assertion: the two outcomes have different renderings."""
        here = self.report.render()
        as_mirror = isub.CensusReport(isub.TreeReport(isub.TREE_MIRROR),
                                      (), isub.guarded_classes(_corpus(ROOT)), ()).render()
        self.assertIn("ABSENT_BY_DESIGN", as_mirror)
        self.assertIn("tests/test_sync_public_nested_checkout.py::"
                      "TestNestedCheckoutNeverReachesTheMirror", as_mirror)
        self.assertNotEqual(here, as_mirror)

    def test_an_UNDECIDABLE_class_is_rendered_as_NEVER_GREEN(self):
        guards = isub.guarded_classes(_corpus(ROOT))
        report = isub.CensusReport(isub.TreeReport(isub.TREE_INCOHERENT, ("docs/build",)),
                                   (), (), guards[:1])
        self.assertIn("NEVER GREEN", report.render())

    def test_the_static_census_and_unittests_OWN_skip_state_agree_here(self):
        """The second derivation, in THIS tree, cheaply: `skip_inventory` reads `__unittest_skip__`
        after import — nothing of ours — and must reach the same verdict about the same classes."""
        inventory = _inventory(ROOT)
        skipped_classes = {entry["test"].rsplit(".", 1)[0] for entry in inventory["skipped"]}
        for guard in self.report.graded:
            key = "%s.%s" % (os.path.basename(guard.filename)[:-3], guard.classname)
            self.assertNotIn(
                key, skipped_classes,
                "the census says %s GRADED its subject and unittest skipped it. One of the two "
                "readings is wrong, and a green run cannot tell you which." % guard.key)
        for guard in self.report.ungraded:
            key = "%s.%s" % (os.path.basename(guard.filename)[:-3], guard.classname)
            self.assertIn(
                key, skipped_classes,
                "the census says %s did NOT grade and unittest ran it anyway" % guard.key)


# =================================================================================================
# F3 — THE SYNC HARNESS. Subject and grader meet on one runner.
# =================================================================================================

@unittest.skipUnless(os.path.exists(SYNC_SH),
                     "sync-public.sh is dev-only; on the mirror the three unguarded classes above "
                     "carry this file's grading and they do NOT skip")
@unittest.skipIf(_TOOLS, "needs %s on PATH" % ", ".join(_TOOLS))
class TestTheSyncHarnessGradesTheSubjects(unittest.TestCase):
    """The real `sync-public.sh`, the real dev tree, a throwaway public checkout as the fixture.

    The mirror is built ONCE for the class and every guarded module is run once per side (~15s
    total, against a 340s suite). `setUpClass` may do this work precisely BECAUSE the decorator
    above is evaluated at class creation: when the guard bites, `setUpClass` never runs at all. It
    must never raise `SkipTest` — that would collapse the class into one skip and drop its tests out
    of `Ran N` (`test_suite_count_integrity`)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        dest = os.path.join(cls._tmp, "public")
        os.makedirs(dest)
        with open(os.path.join(dest, "seed.txt"), "w", encoding="utf-8") as handle:
            handle.write("seed\n")
        for args in (("init", "-q", "."), ("add", "-A"), ("commit", "-qm", "seed"),
                     ("branch", "-M", "main")):
            subprocess.run(["git", *args], cwd=dest, check=True,
                           capture_output=True, text=True, env=_child_env())
        proc = subprocess.run(
            _support.bash_argv(_support.as_posix(SYNC_SH), _support.as_posix(dest)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_child_env())
        cls.mirror = dest
        cls.build_rc = proc.returncode
        cls.build_log = _redact(proc.stdout + proc.stderr)

        cls.modules = guarded_modules(ROOT)
        cls.dev_run = _run_modules(ROOT, cls.modules)
        cls.mirror_run = _run_modules(dest, cls.modules)
        cls.mirror_census = isub.census(dest, _corpus(dest))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # --- the fixture is a real mirror --------------------------------------------------------------
    def test_the_sync_succeeded(self):
        self.assertEqual(self.build_rc, 0, self.build_log)

    def test_the_shippable_tree_arrived(self):
        """The anti-vacuity floor: without it, "no internal file reached the mirror" would also be
        true of a harness that copied nothing at all."""
        for rel in isub.SHIPPED_WITNESSES + ROW_MODULES:
            self.assertTrue(os.path.exists(os.path.join(self.mirror, *rel.split("/"))),
                            "%s did not reach the throwaway mirror" % rel)

    def test_no_internal_witness_reached_the_mirror(self):
        present = [rel for rel in isub.INTERNAL_WITNESSES
                   if os.path.exists(os.path.join(self.mirror, *rel.split("/")))]
        self.assertEqual(present, [], "internal paths reached the public checkout: %s" % present)

    def test_the_harness_built_a_tree_that_NAMES_itself_the_mirror(self):
        report = isub.tree_state(self.mirror)
        self.assertEqual(report.kind, isub.TREE_MIRROR, report.render())

    # --- the two colours, in one run ---------------------------------------------------------------
    def test_the_guarded_modules_GRADE_their_subjects_in_the_dev_tree(self):
        """⭐ F3, delivered: the graders and their subjects are on one runner, and the graders PASS
        against the real `release.sh`, the real `sync-public.sh` and the real ships-list. This is
        the assertion a mutated subject turns RED."""
        self.assertGreaterEqual(len(self.modules), 13, self.modules)
        self.assertEqual(self.dev_run.rc, 0, self.dev_run.render())
        self.assertGreater(self.dev_run.ran, 400, self.dev_run.render())
        self.assertEqual(
            self.dev_run.skipped, 0,
            "these modules skipped %d tests IN THE DEV TREE, where every subject exists. A skip "
            "here is exactly the false green this stage closes.\n%s"
            % (self.dev_run.skipped, self.dev_run.render()))

    def test_the_same_modules_report_SKIPS_in_the_mirror_and_the_count_is_DERIVED(self):
        """★ THE SPLIT, EXECUTED. Identical modules, identical assertions, one runner — and the
        expected skip count comes from a census of the tree the harness just built, never from a
        number typed here."""
        expected = self.mirror_census.skips_expected(self.modules)
        self.assertGreater(expected, 90, self.mirror_census.render())
        self.assertEqual(self.mirror_run.rc, 0, self.mirror_run.render())
        self.assertEqual(
            self.mirror_run.skipped, expected,
            "the mirror skipped %d of these tests; the census derived %d. Two readings of the same "
            "boundary disagree, so one is wrong.\n%s\n%s"
            % (self.mirror_run.skipped, expected, self.mirror_census.render(),
               self.mirror_run.render()))

    def test_the_run_COUNT_is_identical_across_the_boundary(self):
        """`test_suite_count_integrity` pins the SHAPE of the guard; this pins its effect, live, on
        a mirror the script actually built. A class that quietly moved to a `setUpClass` skip would
        drop tests out of `Ran N` on this side only."""
        self.assertEqual(
            self.dev_run.ran, self.mirror_run.ran,
            "dev ran %d, mirror ran %d — a test VANISHED from the count instead of being skipped.\n"
            "%s\n%s" % (self.dev_run.ran, self.mirror_run.ran,
                        self.dev_run.render(), self.mirror_run.render()))

    def test_the_two_colours_are_actually_different(self):
        """The stage's premise, asserted rather than assumed: if the mirror skipped nothing, there
        was never anything to split."""
        self.assertEqual(self.dev_run.skipped, 0, self.dev_run.render())
        self.assertGreater(self.mirror_run.skipped, 90, self.mirror_run.render())

    def test_the_census_of_the_built_mirror_grades_nothing_and_names_everything(self):
        self.assertEqual([g.key for g in self.mirror_census.graded], [],
                         self.mirror_census.render())
        self.assertEqual([g.key for g in self.mirror_census.undecidable], [],
                         self.mirror_census.render())
        self.assertGreaterEqual(len(self.mirror_census.ungraded), 20,
                                self.mirror_census.render())

    def test_the_AST_census_and_unittests_OWN_skip_state_agree_ON_THE_MIRROR(self):
        """The two derivations, on the side that matters. `skip_inventory` reads `__unittest_skip__`
        after import; the census reads decorators from source. Neither is trusted alone — their
        agreement is the assertion, and either one drifting reds here."""
        inventory = _inventory(self.mirror)
        by_unittest = {entry["test"].rsplit(".", 1)[0] for entry in inventory["skipped"]}
        by_census = {"%s.%s" % (os.path.basename(g.filename)[:-3], g.classname)
                     for g in self.mirror_census.ungraded}
        self.assertTrue(by_census, self.mirror_census.render())
        self.assertEqual(
            by_census - by_unittest, set(),
            "the census calls these ungraded on the mirror and unittest ran them: %s"
            % sorted(by_census - by_unittest))


# =================================================================================================
# THE BOUNDARY DID NOT MOVE
# =================================================================================================

@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is dev-only")
class TestTheBoundaryIsUnchangedByThisStage(unittest.TestCase):
    """⛔ F3's hard constraint. `sync-public.sh`'s `--exclude` list plus its `INTERNAL_PATHS` guard
    is what `CLAUDE.md` calls the ONLY control on the public/OSS boundary. Shipping a graded artifact
    so its grader could run would put a hole in the single control to fix a test-coverage problem,
    so both halves are transcribed here as they read at `b9b203b`, before this stage. History cannot
    be derived (§7j) — that is why these are literals, and it is the one place in this file where a
    literal is the correct shape."""

    #: `--exclude=` clauses, at `b9b203b`.
    EXCLUDES = frozenset({
        "*.REFUSED-RESTORE*.bak", "*.egg-info", "*.pyc", "*.tgz", ".DS_Store", ".claude", ".env*",
        ".git", ".mokata", ".mutate.lock", ".pytest_cache", ".venv", "/*.mp4", "/mokata-*",
        "/release-backup-*", "CLAUDE.md", "__pycache__", "_to_delete", "build", "dist",
        "docs/build", "docs/launch", "docs/marketing", "docs/talks", "editors/vscode/node_modules",
        "editors/vscode/out", "push-to-github.command", "scripts/check-tracker-tables.py",
        "scripts/release.sh", "scripts/sync-public.sh", "site",
    })

    #: `INTERNAL_PATHS=( … )`, at `b9b203b`.
    GUARDS = frozenset({
        "*.egg-info", "*.mp4", "*.pyc", "*.tgz", ".DS_Store", ".claude", ".env", ".mokata",
        ".pytest_cache", ".venv", "CLAUDE.md", "__pycache__", "_to_delete", "build", "dist",
        "docs/build", "docs/launch", "docs/marketing", "docs/talks",
        "editors/vscode/node_modules", "editors/vscode/out", "push-to-github.command",
        "release-backup-*", "scripts/check-tracker-tables.py", "scripts/release.sh",
        "scripts/sync-public.sh", "site",
    })

    def setUp(self):
        self.script = mb.read_script(ROOT)

    def test_the_exclude_set_is_exactly_what_it_was_before_this_stage(self):
        now = frozenset(mb.exclude_entries(self.script))
        self.assertEqual(
            now, self.EXCLUDES,
            "the mirror's --exclude list MOVED. added=%s removed=%s. A REMOVED entry means the "
            "exclusion has been inverted and something internal now ships; an ADDED one belongs in "
            "a stage that owns the boundary, not in one about where tests run."
            % (sorted(now - self.EXCLUDES), sorted(self.EXCLUDES - now)))

    def test_the_hard_guard_array_is_exactly_what_it_was_before_this_stage(self):
        now = frozenset(mb.guard_entries(self.script))
        self.assertEqual(
            now, self.GUARDS,
            "INTERNAL_PATHS MOVED. added=%s removed=%s."
            % (sorted(now - self.GUARDS), sorted(self.GUARDS - now)))

    def test_no_control_names_anything_this_stage_added(self):
        """The inversion asked from the other side: this stage's files live in `tests/`, which ships
        in full. If either control had grown an entry pointing at one, the boundary moved."""
        both = frozenset(mb.exclude_entries(self.script)) | frozenset(mb.guard_entries(self.script))
        for entry in both:
            self.assertFalse(entry.startswith("tests/"),
                             "a mirror control now excludes %r — tests/ ships in full" % entry)

    def test_the_internal_witnesses_are_all_REALLY_excluded_by_both_controls(self):
        """`_internal_subject`'s recognition set is a literal by necessity — the mirror is the one
        tree where the deriving control is absent. It is not left ungraded: here, where the script
        exists, every witness is held to it."""
        for rel in isub.INTERNAL_WITNESSES:
            result = mb.resolve(rel, self.script)
            self.assertEqual(
                result.verdict, mb.GREEN,
                "%s is used to RECOGNISE the mirror and %s. A witness the controls do not drop is "
                "present on the mirror too, so the recognition is wrong." % (rel, result.render()))

    def test_no_shipped_witness_is_excluded_by_either_control(self):
        """The other arm: a "shippable" witness the mirror does not carry would make every real
        mirror look INCOHERENT and turn this whole mechanism into noise."""
        for rel in isub.SHIPPED_WITNESSES:
            result = mb.resolve(rel, self.script)
            self.assertEqual(
                result.verdict, mb.RED,
                "%s is used to recognise a mokata checkout and %s" % (rel, result.render()))
            self.assertEqual(result.basis, mb.BASIS_VERIFIED_NEITHER)


if __name__ == "__main__":
    unittest.main()
