"""Which shipped tests are GRADING their subject, which are ABSENT-BY-DESIGN, and which are neither.

0.0.19 stage 05, row B1 (`internal-tests-meet-their-subject`). The defect is not that a block of
tests skips. It is that **a SKIP and a PASS are the same colour** — `@unittest.skipUnless(
os.path.exists(SUBJECT))` renders *"the subject is absent, correctly"* and *"this grades nothing,
silently"* identically, and a run reports `OK` for both. That is §7g in the test layer: an ABSENT
answer and a REAL answer sharing one representation, failing open every time, because the
safe-looking meaning is always "nothing to see here."

THREE STATES, THREE REPRESENTATIONS (§7g)
------------------------------------------
    GRADED            the subject is present in this tree; the class ran its assertions against it
    ABSENT_BY_DESIGN  the subject is absent AND this tree is a coherent public mirror
    UNDECIDABLE       the subject is absent and this tree is NOT a coherent mirror — a dev checkout
                      that has LOST a file, which today is byte-identical to the line above

The first two are both legitimate and both must be *asserted*, never merely observed: the caller
pairs the tree's state with the run's outcome, so "did not execute" and "passed" stop matching.
`UNDECIDABLE` is never green anywhere.

WHY THE SCOPE IS THE WHOLE SHIPPED CORPUS, NOT THE FOUR FILES THE ROW NAMED (§7j)
---------------------------------------------------------------------------------
The row was written against four files. Deriving the property instead of typing its scope finds
**27 guarded classes across 14 files** — the four named ones hold 6 of them. A fix scoped to the
files somebody remembered is `_shipped_reads`'s own lesson wearing this stage's clothes: *"a guard
established for two call sites protects two call sites; only a derivation protects the class."*
So `guarded_classes()` sweeps the shipped test corpus and the four files are an assertion ABOUT the
result, never the input to it.

ONE GUARD READER, NOT TWO. The guard shape comes from `_shipped_reads.guard_of` and the paths it is
keyed on from `_shipped_reads.guard_subjects` — the module that already owns this question, whose
reader is audited and mutant-graded. A second AST guard reader beside it would be the `badge_run` /
`find_active_run` mistake committed inside the stage that exists because a class fix was applied to
one instance.

THE WITNESS LISTS ARE LITERALS, AND THEY ARE GRADED (§7j)
----------------------------------------------------------
`tree_state` has to answer "is this the public mirror?" in a tree where `scripts/sync-public.sh` —
the only control that could derive the answer — is precisely what is missing. The recognition set is
therefore necessarily a literal. It is not left ungraded: `test_b1_internal_tests_meet_their_subject`
holds every `INTERNAL_WITNESSES` entry to `sync-public.sh`'s real `--exclude` list and every
`SHIPPED_WITNESSES` entry to its absence from it, on the dev side where the script exists. The
literal cannot drift without a red.

NO CONSTANT-TAILED JOIN IN THIS MODULE, ON PURPOSE. Paths arrive as `"scripts/sync-public.sh"`
STRINGS and are joined with `os.path.join(root, *rel.split("/"))`. `_shipped_reads` reads a
module-level `def f(base): os.path.join(base, "scripts", "sync-public.sh")` as a path ACCESSOR, and
every call to it from an unguarded scope as a boundary-crossing READ — which is what the census
classes below are, and must be, since their whole job is to run on the mirror too. A bare literal is
a needle, not a path; the join is over data. This is a structural requirement of the sweep, not a
blind spot being leaned on: nothing here ever OPENS an internal file, it only probes for one.

Pure/offline; no subprocess, no network; deterministic.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os

import _shipped_reads as sr


# ---- the three states ---------------------------------------------------------------------------

#: The subject is here. The class graded it.
GRADED = "graded"
#: The subject is absent and the tree is a coherent public mirror. Legitimately ungraded.
ABSENT_BY_DESIGN = "absent_by_design"
#: The subject is absent and the tree is not a coherent mirror. Never green.
UNDECIDABLE = "undecidable"

#: Tree kinds. `INCOHERENT` is the one that must never be reported by a healthy checkout.
TREE_DEV = "dev"
TREE_MIRROR = "mirror"
TREE_INCOHERENT = "incoherent"


# ---- the recognition sets (literal, graded on the dev side) --------------------------------------

#: Paths carried by THIS repo and dropped by BOTH mirror controls. All absent ⇒ this is the mirror.
#: Every entry is held to `sync-public.sh`'s real `--exclude` list by the stage's own test.
INTERNAL_WITNESSES = (
    "CLAUDE.md",
    "docs/build",
    "scripts/sync-public.sh",
    "scripts/release.sh",
    "scripts/check-tracker-tables.py",
)

#: Paths every checkout of mokata carries, mirror included. All present ⇒ this is a mokata checkout
#: rather than a half-copied directory. Held to their ABSENCE from `--exclude` by the same test.
SHIPPED_WITNESSES = (
    "pyproject.toml",
    "src/mokata/__init__.py",
    "tests/_support.py",
    "README.md",
)


def _at(root, rel):
    """`root` joined to a repo-relative POSIX path. The tail is DATA — see the module docstring."""
    return os.path.join(root, *rel.split("/"))


def _present(root, rels):
    return tuple(rel for rel in rels if os.path.exists(_at(root, rel)))


# ---- what kind of tree is this? -------------------------------------------------------------------

class TreeReport(object):
    """Read-only diagnostic: which side of the mirror boundary this checkout is on, and why.

    `kind` names WHICH rung answered and `render()` says so in words, so a caller can never mistake
    one kind of answer for another (§7g, the `RunResolution` model). `TREE_INCOHERENT` carries the
    evidence with it — `internal_present` for a dev tree that lost a file, `shipped_missing` for a
    directory that is not a mokata checkout — because a refusal that cannot say why gets overridden.
    """

    __slots__ = ("kind", "internal_present", "shipped_missing")

    def __init__(self, kind, internal_present=(), shipped_missing=()):
        self.kind = kind
        self.internal_present = tuple(internal_present)
        self.shipped_missing = tuple(shipped_missing)

    @property
    def coherent(self):
        return self.kind in (TREE_DEV, TREE_MIRROR)

    def render(self):
        if self.kind == TREE_DEV:
            return ("this is the DEV checkout: all %d internal witnesses are present, so every "
                    "guarded class must GRADE its subject here" % len(INTERNAL_WITNESSES))
        if self.kind == TREE_MIRROR:
            return ("this is the PUBLIC MIRROR: every internal witness is absent and every "
                    "shippable witness is present, so an ungraded subject is absent BY DESIGN")
        if self.shipped_missing:
            return ("this is not a mokata checkout at all — %s missing. Nothing below can be "
                    "graded or excused." % (list(self.shipped_missing),))
        return (
            "this checkout carries SOME internal paths (%s) and is missing others (%s). It is "
            "neither the dev tree nor the mirror, so a skipped guard here does NOT mean 'absent by "
            "design' — it means a dev checkout has lost a file. That is the ambiguity this module "
            "exists to refuse: do not read the skip as an excuse."
            % (list(self.internal_present),
               [r for r in INTERNAL_WITNESSES if r not in self.internal_present]))


def tree_state(root):
    """Which side of the mirror boundary `root` is on. Never guesses; `TREE_INCOHERENT` is real."""
    shipped_missing = tuple(r for r in SHIPPED_WITNESSES if not os.path.exists(_at(root, r)))
    internal_present = _present(root, INTERNAL_WITNESSES)
    if shipped_missing:
        return TreeReport(TREE_INCOHERENT, internal_present, shipped_missing)
    if len(internal_present) == len(INTERNAL_WITNESSES):
        return TreeReport(TREE_DEV, internal_present)
    if not internal_present:
        return TreeReport(TREE_MIRROR, internal_present)
    return TreeReport(TREE_INCOHERENT, internal_present)


def subject_state(root, subjects, tree=None):
    """The state of one guarded class's subjects in `root`. `subjects` is repo-relative POSIX."""
    tree = tree if tree is not None else tree_state(root)
    if not subjects:
        # THE VACUITY THIS MUST REFUSE: an empty subject set makes "nothing is missing" true
        # having asked nothing, and a vacuous GRADED is byte-identical to a real one. A class
        # whose guard the reader could not resolve is UNDECIDABLE, in either tree.
        return UNDECIDABLE
    missing = tuple(rel for rel in subjects if not os.path.exists(_at(root, rel)))
    if not missing:
        return GRADED
    if tree.kind == TREE_MIRROR:
        return ABSENT_BY_DESIGN
    return UNDECIDABLE


# ---- the guarded corpus, derived ------------------------------------------------------------------

class GuardedClass(object):
    """One class whose tests run only where its subject exists. Read-only."""

    __slots__ = ("filename", "classname", "subjects", "tests")

    def __init__(self, filename, classname, subjects, tests):
        self.filename = filename
        self.classname = classname
        self.subjects = tuple(sorted(subjects))
        self.tests = tuple(sorted(tests))

    @property
    def key(self):
        return "%s::%s" % (self.filename, self.classname)

    def render(self):
        return "%s (%d tests) keyed on %s" % (self.key, len(self.tests), list(self.subjects))

    def __repr__(self):                                   # pragma: no cover - debugging aid
        return "<GuardedClass %s>" % self.key


def guarded_classes(corpus):
    """Every existence-guarded class in `corpus` ({filename: source}), with the paths it is keyed on.

    A file that will not parse is SKIPPED, not silently counted clean — the caller asks for the
    unparseable set separately via `unparseable()`. A guarded class naming NO subject is returned
    with `subjects == ()` rather than dropped, because "guarded on something this reader could not
    resolve" and "not guarded" must not collapse into one another either.
    """
    found = []
    for filename in sorted(corpus):
        source = corpus[filename]
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if sr.guard_of(node) != sr.GUARD_DECORATOR:
                continue
            tests = [m.name for m in node.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and m.name.startswith("test_")]
            found.append(GuardedClass(filename, node.name,
                                      sr.guard_subjects(node, tree), tests))
    return tuple(found)


class RuntimeSkip(object):
    """One test method that decides AT RUN TIME to skip when an internal path is absent.

    ★ WHY THIS EXISTS, AND IT IS NOT AN EDGE CASE. `skip_inventory` states the limit plainly: a
    `self.skipTest(...)` inside a test BODY is invisible to anything reading decorators. This repo
    has four of them and they are not incidental — they are the §7g COMPANIONS, the tests that ask
    *"sync-public.sh is here, so why is release.sh gone?"*. Measured on the mirror this harness
    builds: 109 tests skip by decorator and **113 skip in total**. A census that reported 109 and
    called the other four noise would be a count standing in for an inventory, which is the exact
    substitution this whole stage is about.
    """

    __slots__ = ("filename", "classname", "testname", "subjects")

    def __init__(self, filename, classname, testname, subjects):
        self.filename = filename
        self.classname = classname
        self.testname = testname
        self.subjects = tuple(sorted(subjects))

    @property
    def key(self):
        return "%s::%s.%s" % (self.filename, self.classname, self.testname)

    def render(self):
        return "%s (run-time) keyed on %s" % (self.key, list(self.subjects))


def _negated_probe_subjects(node, constants):
    """The paths `not os.path.exists(P)` asks about, inside `node`. Empty for anything else.

    The NEGATION is required, not incidental: `if not exists(X): skipTest()` skips when X is
    ABSENT — the mirror case. `if exists(X): skipTest()` skips when it is PRESENT, which is a
    different test with an opposite meaning, and counting it here would put a skip in the mirror
    column that only ever fires on the dev tree.
    """
    subjects = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.UnaryOp) or not isinstance(child.op, ast.Not):
            continue
        for inner in ast.walk(child.operand):
            if not isinstance(inner, ast.Call) or sr._dotted(inner.func) not in sr._EXISTS_NAMES:
                continue
            for arg in inner.args:
                if isinstance(arg, ast.Name) and arg.id in constants:
                    subjects.add(constants[arg.id])
    return subjects


def _calls_skiptest(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and (sr._dotted(child.func) or "").endswith("skipTest"):
            return True
    return False


def runtime_boundary_skips(corpus):
    """Every test method that skips itself when an internal path is absent. Parsed, never grepped —
    a `skipTest` inside a docstring is prose about the mechanism, not a use of it."""
    found = []
    for filename in sorted(corpus):
        source = corpus[filename]
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        constants = sr.anchored_constants(tree)
        if not constants:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not method.name.startswith("test_"):
                    continue
                subjects = set()
                for stmt in ast.walk(method):
                    if not isinstance(stmt, ast.If) or not _calls_skiptest(stmt):
                        continue
                    subjects |= _negated_probe_subjects(stmt.test, constants)
                if subjects:
                    found.append(RuntimeSkip(filename, cls.name, method.name, subjects))
    return tuple(found)


def unparseable(corpus):
    """The files `guarded_classes` could not read or parse. Reported, never rounded to zero."""
    bad = []
    for filename in sorted(corpus):
        source = corpus[filename]
        if source is None:
            bad.append((filename, "unreadable"))
            continue
        try:
            ast.parse(source)
        except SyntaxError as exc:
            bad.append((filename, "unparseable: %s" % (exc,)))
    return tuple(bad)


# ---- the census ------------------------------------------------------------------------------------

class CensusReport(object):
    """Read-only diagnostic: what this tree grades, what it legitimately does not, and what it
    cannot say. The pairing of `tree` with `graded`/`ungraded` is the thing that makes a skip and a
    pass different colours — assert BOTH sides of it, never one."""

    __slots__ = ("tree", "graded", "ungraded", "undecidable", "runtime_ungraded")

    def __init__(self, tree, graded, ungraded, undecidable, runtime_ungraded=()):
        self.tree = tree
        self.graded = tuple(graded)
        self.ungraded = tuple(ungraded)
        self.undecidable = tuple(undecidable)
        self.runtime_ungraded = tuple(runtime_ungraded)

    @property
    def ungraded_tests(self):
        """Decorator-decided only. `skips_expected` is the number a RUN will report."""
        return sum(len(g.tests) for g in self.ungraded)

    @property
    def graded_tests(self):
        return sum(len(g.tests) for g in self.graded)

    def skips_expected(self, modules=None):
        """Every test that will report `... skipped` in this tree, decorator- AND run-time-decided.

        The two populations are counted together HERE and kept apart above, because a caller
        comparing against `Ran N ... skipped=K` needs the total and a caller asking "which classes
        grade nothing" needs the classes. One number that meant both would be the substitution this
        module exists to refuse.
        """
        keep = (lambda name: True) if modules is None else (lambda name: name in modules)
        return (sum(len(g.tests) for g in self.ungraded if keep(g.filename))
                + sum(1 for r in self.runtime_ungraded if keep(r.filename)))

    def render(self):
        lines = [self.tree.render(),
                 "  GRADED            %3d classes / %3d tests"
                 % (len(self.graded), self.graded_tests),
                 "  ABSENT_BY_DESIGN  %3d classes / %3d tests (decorator-decided)"
                 % (len(self.ungraded), self.ungraded_tests)]
        for guard in self.ungraded:
            lines.append("      - %s" % guard.render())
        if self.runtime_ungraded:
            lines.append("  ABSENT_BY_DESIGN  %3d tests (run-time-decided — the §7g companions)"
                         % len(self.runtime_ungraded))
            for skip in self.runtime_ungraded:
                lines.append("      - %s" % skip.render())
        if self.undecidable:
            lines.append("  UNDECIDABLE       %3d classes — NEVER GREEN:" % len(self.undecidable))
            for guard in self.undecidable:
                lines.append("      - %s" % guard.render())
        return "\n".join(lines)


def census(root, corpus):
    """Grade every guarded class in `corpus` against the tree at `root`."""
    tree = tree_state(root)
    graded, ungraded, undecided = [], [], []
    for guard in guarded_classes(corpus):
        state = subject_state(root, guard.subjects, tree)
        if state == GRADED:
            graded.append(guard)
        elif state == ABSENT_BY_DESIGN:
            ungraded.append(guard)
        else:
            undecided.append(guard)
    runtime = tuple(skip for skip in runtime_boundary_skips(corpus)
                    if subject_state(root, skip.subjects, tree) != GRADED)
    return CensusReport(tree, graded, ungraded, undecided, runtime)
