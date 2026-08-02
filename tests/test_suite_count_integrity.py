"""A skipped test must still be COUNTED — no test may vanish from `Ran N`.

Where this came from. The DB.S10 audit recorded one thing it could not attribute: a dev
checkout reported `Ran 5629` while CI reported `Ran 5623`, on files it had verified were
identical blob-for-blob. It ruled out optional dependencies by experiment and stated the gap
as unverified rather than guessing. It was right to; the cause is not where anyone was
looking.

**It was never a COLLECTION difference.** Both trees discover exactly 5,629 tests — the
mirror tarball CI actually checks out discovers 5,629 too. The difference is at RUN time and
the mechanism is in unittest itself: a `SkipTest` raised inside `setUpClass` short-circuits
`TestSuite._handleClassSetUp`, so the class's tests never START. `startTest` is never called
for any of them, `testsRun` never increments, and the whole class is reported as ONE skip. A
class-level `@unittest.skipUnless` decorator does the opposite — each test starts, is skipped
individually, and stays in the count.

`test_tm_s12a_branch_protection.py` had two such classes, `ReleaseScriptWiring` and
`SyncBoundaryEnvHardening`, three tests each, skipping when `scripts/release.sh` and
`scripts/sync-public.sh` are absent — i.e. exactly on the public mirror, which is where CI
runs. 6 tests reported as 2 skips. 5629 − 6 = 5623, to the test.

WHY IT IS WORTH A GUARD rather than a one-line fix and a shrug. This project has spent a
release closing variants of one hazard — LIVE-LEG-ORPHANS (a skipped leg reads green),
LIVE-LEG-NEVER-RAN (a leg that could never import), the DB.S8g anti-silent-cap pin — and this
is the same family: a number that quietly shrinks looks identical to a number that was always
that size. Nobody could tell from `Ran 5623` whether six tests were skipped, deleted, or never
written, and an unexplained delta cost real audit time.

THE SWEEP'S BOUNDARY, and why there are two arms. The DB.S10 re-verification (doc 98,
non-blocking finding #4) pointed out that a sweep for a *direct* `raise ... SkipTest` sees only
the shape the two real offenders happened to have. A `setUpClass` that instead CALLS a helper
which raises — `_require_live_pg()` — collapses the class exactly the same way and would walk
straight past a direct-raise sweep. That gap was latent, not active (no helper anywhere under
`tests/` raises `SkipTest` today), so this is a guard placed before the hazard rather than
after it. `_skiptest_raising_fixtures` covers the direct shape; `_indirect_skiptest_fixtures`
covers the helper shape, resolving helper names to a fixed point so a helper that calls a
helper that raises is still caught.

What the second arm deliberately does NOT do: it matches helpers by BARE NAME across the whole
`tests/` tree rather than resolving imports, because the tree shares `_support` and a real
import graph would be a lot of machinery for a rule whose job is to fire loudly and be read by
a human. The cost is a theoretical false positive (two unrelated helpers sharing a name, one of
which skips); the failure message names the helper, so that is a five-second diagnosis. Fixture
names and `test_*` names are excluded from the helper set on purpose — a fixture that raises is
the FIRST arm's business, and without that exclusion an ordinary `super().setUpClass()` would
be read as a call to a skip-raising helper.

Pure/offline; deterministic.
"""

import ast
import glob
import io
import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = ("setUpClass", "setUpModule")


def _raises_skiptest(fn):
    """True if `fn`'s body contains a `raise` mentioning SkipTest, including behind an `if`."""
    return any(isinstance(s, ast.Raise) and "SkipTest" in ast.dump(s) for s in ast.walk(fn))


def _skiptest_raising_fixtures():
    """(file, fixture, owner) for every class/module fixture that can raise SkipTest.

    AST-level: it looks for a `raise` whose exception mentions `SkipTest` anywhere inside a
    `setUpClass`/`setUpModule` body, including behind an `if`, which is how both of the real
    ones were written.

    Scoped to MODULE-LEVEL classes and functions on purpose: a TestCase defined inside a test
    method (as the demonstrations above do) is never discovered by unittest, so it cannot make
    anything vanish from anyone's count and is not this rule's business."""
    hits = []
    for path in sorted(glob.glob(os.path.join(TESTS_DIR, "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, TESTS_DIR)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in tree.body:                       # module level only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in _FIXTURES and _raises_skiptest(node):
                    hits.append((rel, node.name, "<module>"))
            elif isinstance(node, ast.ClassDef):
                for member in node.body:
                    if (isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and member.name in _FIXTURES and _raises_skiptest(member)):
                        hits.append((rel, member.name, node.name))
    return hits


def _called_names(fn):
    """Every bare callee name inside `fn` — `helper()` and `mod.helper()` alike."""
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                out.add(func.id)
            elif isinstance(func, ast.Attribute):
                out.add(func.attr)
    return out


def _parsed_test_trees():
    for path in sorted(glob.glob(os.path.join(TESTS_DIR, "**", "*.py"), recursive=True)):
        with open(path, encoding="utf-8") as fh:
            yield os.path.relpath(path, TESTS_DIR), ast.parse(fh.read(), filename=path)


def _module_fixtures(tree):
    """(fixture_node, owner) for every MODULE-LEVEL class/module fixture in `tree`."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _FIXTURES:
            yield node, "<module>"
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if (isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and member.name in _FIXTURES):
                    yield member, node.name


def _skiptest_raising_helper_names(trees):
    """Names of helpers that raise SkipTest, directly or through another helper.

    Fixed point, so `_require_pg` -> `_require_dsn` -> `raise SkipTest` is caught. Fixture and
    `test_*` names are excluded: a fixture that raises is the direct arm's business, and
    including them would make `super().setUpClass()` look like a call to a skipping helper."""
    defs = {}
    for _rel, tree in trees:
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name not in _FIXTURES and not node.name.startswith("test_")):
                defs.setdefault(node.name, []).append(node)

    skippy = {name for name, nodes in defs.items() if any(_raises_skiptest(n) for n in nodes)}
    while True:
        grown = {name for name, nodes in defs.items()
                 if name not in skippy and any(_called_names(n) & skippy for n in nodes)}
        if not grown:
            return skippy
        skippy |= grown


def _indirect_skiptest_fixtures():
    """(file, fixture, owner, helpers) for fixtures that reach SkipTest through a helper."""
    trees = list(_parsed_test_trees())
    skippy = _skiptest_raising_helper_names(trees)
    if not skippy:
        return []

    hits = []
    for rel, tree in trees:
        for fixture, owner in _module_fixtures(tree):
            if _raises_skiptest(fixture):
                continue                      # the direct arm already owns this one
            reached = sorted(_called_names(fixture) & skippy)
            if reached:
                hits.append((rel, fixture.name, owner, reached))
    return hits


class TheMechanismIsRealNotFolklore(unittest.TestCase):
    """Prove the claim this file is built on, rather than asserting it. If a future CPython
    changes how a class-fixture skip is accounted for, this fails first and the rule below can
    be retired knowingly instead of enforced out of habit."""

    def _run(self, suite):
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        return suite.countTestCases(), result.testsRun, len(result.skipped)

    def test_a_setupclass_skip_removes_its_tests_from_the_count(self):
        class ViaFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("env")

            def test_a(self): pass
            def test_b(self): pass
            def test_c(self): pass

        collected, ran, skipped = self._run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ViaFixture))
        self.assertEqual(3, collected, "three tests are discovered either way")
        self.assertEqual(0, ran, "…and NONE of them are counted as run")
        self.assertEqual(1, skipped, "the whole class collapses to a single skip")

    def test_a_decorator_skip_keeps_every_test_in_the_count(self):
        @unittest.skipUnless(False, "env")
        class ViaDecorator(unittest.TestCase):
            def test_a(self): pass
            def test_b(self): pass
            def test_c(self): pass

        collected, ran, skipped = self._run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ViaDecorator))
        self.assertEqual((3, 3, 3), (collected, ran, skipped),
                         "each test starts, is skipped, and stays in `Ran N`")


class NoTestMayVanishFromTheCount(unittest.TestCase):
    def test_no_class_or_module_fixture_raises_skiptest(self):
        hits = _skiptest_raising_fixtures()
        self.assertEqual(
            [], hits,
            "\n" + "\n".join(f"  {f} · {owner}.{fixture}" for f, fixture, owner in hits)
            + "\n\nA SkipTest raised in setUpClass/setUpModule makes that class's tests "
              "DISAPPEAR from `Ran N` instead of appearing as skips, so the suite size silently "
              "changes with the environment and two honest runs report different totals. Use a "
              "class-level @unittest.skipUnless(<condition>, <reason>) instead — same skip, same "
              "reason, and the tests stay counted.",
        )

    def test_no_class_or_module_fixture_reaches_skiptest_through_a_helper(self):
        # doc 98 finding #4: the direct-raise sweep sees only the shape the two real offenders
        # had. A fixture calling a helper that raises collapses the class identically.
        hits = _indirect_skiptest_fixtures()
        self.assertEqual(
            [], hits,
            "\n" + "\n".join(f"  {f} · {owner}.{fixture} → {', '.join(helpers)}()"
                             for f, fixture, owner, helpers in hits)
            + "\n\nThis fixture does not raise SkipTest itself, but it CALLS a helper that "
              "does — which collapses the class's tests out of `Ran N` exactly as a direct "
              "raise would, just less visibly. Use a class-level "
              "@unittest.skipUnless(<condition>, <reason>) instead, so every test starts, is "
              "skipped individually, and stays counted.",
        )

    def test_the_sweep_can_actually_see_a_helper_raised_one(self):
        # Same discipline as the direct arm: an AST sweep that finds nothing because it is
        # looking wrong is indistinguishable from a clean tree. Prove it fires.
        tree = ast.parse(
            "import unittest\n"
            "def _require_live_pg():\n"
            "    raise unittest.SkipTest('no DSN')\n"
            "def _require_backend():\n"
            "    _require_live_pg()\n"
            "class T(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        _require_backend()\n"
        )
        trees = [("synthetic.py", tree)]
        skippy = _skiptest_raising_helper_names(trees)
        self.assertEqual({"_require_live_pg", "_require_backend"}, skippy,
                         "the fixed point must follow helper → helper → raise")

        fixture, owner = next(_module_fixtures(tree))
        self.assertEqual("T", owner)
        self.assertFalse(_raises_skiptest(fixture), "the fixture itself raises nothing")
        self.assertEqual({"_require_backend"}, _called_names(fixture) & skippy,
                         "…yet the sweep still reaches SkipTest through the call")

    def test_a_super_call_in_a_fixture_is_not_mistaken_for_a_skipping_helper(self):
        # The false-positive this arm's name exclusions exist to prevent: `super().setUpClass()`
        # is a call to something *named* setUpClass, and some setUpClass somewhere raises.
        tree = ast.parse(
            "import unittest\n"
            "class Base(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        raise unittest.SkipTest('env')\n"
            "class Child(Base):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        super().setUpClass()\n"
        )
        self.assertEqual(set(), _skiptest_raising_helper_names([("synthetic.py", tree)]),
                         "fixture names are excluded from the helper set, so super() is clean")

    def test_the_sweep_can_actually_see_one(self):
        # The failure mode of an AST sweep is finding nothing because it is looking wrong.
        tree = ast.parse(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        if not True:\n"
            "            raise unittest.SkipTest('x')\n"
        )
        found = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "setUpClass"
            and any(isinstance(s, ast.Raise) and "SkipTest" in ast.dump(s) for s in ast.walk(n))
        ]
        self.assertEqual(1, len(found), "the sweep must see a raise nested behind an `if`")


if __name__ == "__main__":
    unittest.main()
