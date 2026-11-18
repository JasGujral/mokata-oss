"""A TEST THAT NEEDS THE REAL code-review-graph MUST SAY SO, AND THE TREE CHECKS THAT IT DOES.
(0.0.28 lane A — `HERMETIC-OPT-IN-UNGRADED`.)

The hermetic suite (`_support`, 0.0.26) sets `MOKATA_CRG=off` for every test unless the test opts
in with `_support.real_crg(self)` or the run sets `MOKATA_TEST_REAL_CRG=1`. A test that drives the
REAL tool and forgets to opt in does not skip and does not pass: `crg_build.build_now` answers
"switched off (MOKATA_CRG=off)", and the test reds with a build failure that has nothing to do
with the code it was written to grade. `tests/integration/test_o2_real_crg_blast_radius.py` shipped
exactly that for a whole release, and nothing noticed because nothing checked.

THE RULE. Every test module that exercises the real build/serve path must, inside a function body
(never at module level, which would switch the tool back on for the whole process):

  * call `real_crg(...)`, or
  * pop `MOKATA_CRG` from `os.environ` (the class-level form, for a build that runs in
    `setUpClass`, paired with `addClassCleanup` / `mock.patch.dict`) or from the environment copy a
    real subprocess is launched with.

A module "exercises the real path" when either

  * it calls `build_now(...)` WITHOUT a `runner=` argument (a `runner` is the injected fake), or
  * it gates itself on the real package: a `find_spec` / `find_command` / `which` call whose
    argument names `code_review_graph` / `code-review-graph`.

The detector is derived over the AST of every module under `tests/` (unit, integration, e2e), and
is itself graded below against SUPPLIED sources, both directions, so a detector that has gone blind
cannot report an empty tree as clean.

0.0.29 D4 (`HERMETIC-GRADER-GRANULARITY`, `REAL-CRG-CLASSES-NEVER-RUN`): the verdict is PER CLASS,
an unpatched `os.environ.pop("MOKATA_CRG")` (a process-wide leak) convicts instead of counting as an
opt-in, and the `KNOWN_UNRUN_REAL_CRG` exemption table is deleted — its two classes now opt in and
run live in `real-crg.yml`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

TESTS = os.path.dirname(os.path.abspath(__file__))

_REAL_NAMES = ("code_review_graph", "code-review-graph")
_GATE_CALLS = ("find_spec", "find_command", "which")
_OPT_IN_VAR = "MOKATA_CRG"


def _callee(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _names_the_real_tool(call):
    return any(isinstance(a, ast.Constant) and a.value in _REAL_NAMES for a in call.args)


def _is_pop_of_the_switch(call):
    """`<env>.pop("MOKATA_CRG"[, default])` — on `os.environ` or on a copy of it."""
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "pop"
            and bool(call.args) and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == _OPT_IN_VAR)


def _is_os_environ(node):
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _patches_os_environ(fn):
    """The function starts a `patch.dict(os.environ, ...)` (a `with`, or `.start()`)."""
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and _callee(node) == "dict" and node.args
                and _is_os_environ(node.args[0])):
            return True
    return False


def _is_real_call(node):
    if not isinstance(node, ast.Call):
        return False
    name = _callee(node)
    if name == "build_now" and not any(k.arg == "runner" for k in node.keywords):
        return True
    return name in _GATE_CALLS and _names_the_real_tool(node)


def _real_names(tree):
    """Module-level names that STAND FOR the real tool: a function whose body makes a real call,
    an assignment whose value does (`_HAS = find_spec("code_review_graph") ...`), and — to a fixed
    point — anything defined from one of those (`_WHY = ... if not _HAS ...`)."""
    names, top = set(), list(tree.body)
    changed = True
    while changed:
        changed = False
        for node in top:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                targets = [node.name]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                tl = node.targets if isinstance(node, ast.Assign) else [node.target]
                targets = [t.id for t in tl if isinstance(t, ast.Name)]
            else:
                continue
            if not targets or set(targets) <= names:
                continue
            if any(_is_real_call(n) or (isinstance(n, ast.Name) and n.id in names)
                   for n in ast.walk(node)):
                names.update(targets)
                changed = True
    return names


def classify(source):
    """{class name: (exercises_the_real_path, opts_in, leaks)} for one module's source — PER
    CLASS (0.0.29 D4 `HERMETIC-GRADER-GRANULARITY`): a module with one opting-in class and one that
    does not is not "graded".

    * a class EXERCISES the real path when anything in it (its decorators, bases, methods) makes a
      real call or names a module-level stand-in for one (`_real_names`);
    * it OPTS IN when one of its own methods calls `real_crg(...)`, pops the switch from a COPY
      (`env.pop("MOKATA_CRG")`), or pops it from `os.environ` in a method that patches
      `os.environ` (restored after); a base class in the module that opts in counts;
    * it LEAKS when a method pops the switch from `os.environ` with NO patch: that turns the real
      tool on for the rest of the PROCESS, and convicts even though it "opts in"."""
    tree = ast.parse(source)
    stand_ins = _real_names(tree)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    out = {}
    for name, cls in classes.items():
        real = any(_is_real_call(n) or (isinstance(n, ast.Name) and n.id in stand_ins)
                   for n in ast.walk(cls))
        opts_in = leaks = False
        for fn in cls.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            patched = _patches_os_environ(fn)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                if _callee(node) == "real_crg":
                    opts_in = True
                elif _is_pop_of_the_switch(node):
                    if _is_os_environ(node.func.value) and not patched:
                        leaks = True
                    else:
                        opts_in = True
        out[name] = (real, opts_in, leaks)
    for name, cls in classes.items():                 # a base in this module that opts in
        real, opts_in, leaks = out[name]
        for b in cls.bases:
            if isinstance(b, ast.Name) and b.id in out:
                opts_in = opts_in or out[b.id][1]
                leaks = leaks or out[b.id][2]
                real = real or out[b.id][0]
        out[name] = (real, opts_in, leaks)
    return out


def convicted(source):
    """The classes in `source` the rule convicts: real and not opted in, or leaking."""
    return sorted(n for n, (real, opts_in, leaks) in classify(source).items()
                  if leaks or (real and not opts_in))


def _test_modules(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.startswith("test_") and name.endswith(".py"):
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, root).replace(os.sep, "/"), fh.read()


def ungraded_classes(root):
    """`<module>::<Class>` for every class under `root` the rule convicts."""
    return ["%s::%s" % (rel, c) for rel, src in _test_modules(root) for c in convicted(src)]


def real_path_modules(root):
    return sorted(os.path.basename(rel) for rel, src in _test_modules(root)
                  if any(real for real, _o, _l in classify(src).values()))


class TheTreeOptsIn(unittest.TestCase):

    def test_every_class_that_needs_the_real_crg_opts_in(self):
        """No exemption table: an empty one is a standing fail-open, and the two it held
        (o13, p39) now opt in and run live in `real-crg.yml`."""
        self.assertEqual(
            [], ungraded_classes(TESTS),
            "these test CLASSES drive the real code-review-graph but never opt in (under the "
            "hermetic MOKATA_CRG=off they fail with 'switched off') or pop the switch from "
            "os.environ with no patch (it stays on for the whole process) — call "
            "`_support.real_crg(self)` in setUp, or pop MOKATA_CRG under a patch (see o2)")

    def test_the_detector_still_sees_the_known_real_crg_modules(self):
        """Anti-vacuity on the LIVE tree: a detector that matched nothing would also report
        'nothing ungraded'."""
        seen = real_path_modules(TESTS)
        for known in ("test_o2_real_crg_blast_radius.py", "test_o13_crg_auto_build.py",
                      "test_vscale_full_rebuild_of_a_copied_repo.py", "test_p39_ide_first_run.py"):
            self.assertIn(known, seen)


_GOOD_CLASS_LEVEL = """
import importlib.util, os, unittest
from unittest import mock
_HAS = importlib.util.find_spec("code_review_graph") is not None
@unittest.skipUnless(_HAS, "no graph")
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = mock.patch.dict(os.environ); p.start(); cls.addClassCleanup(p.stop)
        os.environ.pop("MOKATA_CRG", None)
"""
_GOOD_HELPER = """
import _support
from mokata.knowledge import crg_build as B
class T:
    def setUp(self):
        _support.real_crg(self)
    def test_x(self):
        B.build_now("r")
"""
_GOOD_COPY = """
import os, subprocess
_HAS = __import__("importlib.util").util.find_spec("code_review_graph") is not None
class T:
    @unittest.skipUnless(_HAS, "no graph")
    def test_x(self):
        env = dict(os.environ)
        env.pop("MOKATA_CRG", None)
        subprocess.run(["mokata", "ide"], env=env)
"""
_BAD_GATE_ONLY = """
import importlib.util
_HAS = importlib.util.find_spec("code_review_graph") is not None
@unittest.skipUnless(_HAS, "no graph")
class T:
    def test_x(self):
        pass
"""
_BAD_BUILD = """
from mokata.knowledge import crg_build as B
class T:
    def test_x(self):
        B.build_now("r", full=True)
"""
_FAKE_RUNNER = """
from mokata.knowledge import crg_build as B
class T:
    def test_x(self):
        B.build_now("r", runner=lambda *a, **k: None)
"""
_MODULE_LEVEL_POP = """
import os
from mokata.knowledge import crg_build as B
os.environ.pop("MOKATA_CRG", None)
class T:
    def test_x(self):
        B.build_now("r")
"""
_POP_OF_SOMETHING_ELSE = """
import os
from mokata.knowledge import crg_build as B
class T:
    def setUp(self):
        os.environ.pop("HOME", None)
    def test_x(self):
        B.build_now("r")
"""
_UNRELATED = """
class T:
    def test_x(self):
        self.assertTrue(True)
"""
#: One class opts in, the other drives the real tool too and does not: per-MODULE grading passed it.
_TWO_CLASSES_ONE_OPTS_IN = """
import _support
from mokata.knowledge import crg_build as B
class Good:
    def setUp(self):
        _support.real_crg(self)
    def test_x(self):
        B.build_now("r")
class Forgot:
    def test_y(self):
        B.build_now("r")
"""
#: The switch popped from the PROCESS environment with no patch: on for every later test.
_UNPATCHED_POP = """
import os
from mokata.knowledge import crg_build as B
class T:
    @classmethod
    def setUpClass(cls):
        os.environ.pop("MOKATA_CRG", None)
    def test_x(self):
        B.build_now("r")
"""

#: Opts in properly in ONE test and leaks the switch in another: the opt-in does not excuse the leak
#: (the leak arm of `convicted` is the only thing that convicts it — `_UNPATCHED_POP` is also
#: "real and not opted in", so it alone cannot grade that arm; local mutant D4-a survived on it).
_OPTS_IN_AND_LEAKS = """
import os
import _support
from mokata.knowledge import crg_build as B
class T:
    def setUp(self):
        _support.real_crg(self)
    def tearDown(self):
        os.environ.pop("MOKATA_CRG", None)
    def test_x(self):
        B.build_now("r")
"""


class TheDetectorIsGradedAgainstSuppliedSources(unittest.TestCase):
    """§7i — each case is a SUPPLIED module, so the verdicts do not depend on today's tree.
    FIRING CONTROLS: per-module grading again (`_TWO_CLASSES_ONE_OPTS_IN` passes); the leak arm
    removed (`_OPTS_IN_AND_LEAKS` passes)."""

    def test_the_class_level_opt_in_is_accepted(self):
        self.assertEqual({"T": (True, True, False)}, classify(_GOOD_CLASS_LEVEL))

    def test_the_helper_opt_in_is_accepted(self):
        self.assertEqual({"T": (True, True, False)}, classify(_GOOD_HELPER))

    def test_a_pop_from_the_copy_a_subprocess_gets_is_accepted(self):
        self.assertEqual({"T": (True, True, False)}, classify(_GOOD_COPY))

    def test_gating_on_the_package_without_an_opt_in_is_ungraded(self):
        self.assertEqual(["T"], convicted(_BAD_GATE_ONLY))

    def test_a_real_build_without_an_opt_in_is_ungraded(self):
        self.assertEqual(["T"], convicted(_BAD_BUILD))

    def test_an_injected_runner_is_a_fake_and_needs_no_opt_in(self):
        self.assertEqual({"T": (False, False, False)}, classify(_FAKE_RUNNER))

    def test_a_module_level_pop_is_not_an_opt_in(self):
        """It would switch the tool on for the whole process, not for the test that needs it."""
        self.assertEqual(["T"], convicted(_MODULE_LEVEL_POP))

    def test_popping_some_other_variable_is_not_an_opt_in(self):
        self.assertEqual(["T"], convicted(_POP_OF_SOMETHING_ELSE))

    def test_an_unrelated_module_is_neither(self):
        self.assertEqual({"T": (False, False, False)}, classify(_UNRELATED))

    def test_a_second_class_that_forgot_is_convicted_although_its_neighbour_opts_in(self):
        self.assertEqual(["Forgot"], convicted(_TWO_CLASSES_ONE_OPTS_IN))

    def test_an_unpatched_pop_of_the_process_switch_is_a_leak_not_an_opt_in(self):
        self.assertEqual({"T": (True, False, True)}, classify(_UNPATCHED_POP))
        self.assertEqual(["T"], convicted(_UNPATCHED_POP))

    def test_an_opt_in_does_not_excuse_a_leak_elsewhere_in_the_class(self):
        self.assertEqual({"T": (True, True, True)}, classify(_OPTS_IN_AND_LEAKS))
        self.assertEqual(["T"], convicted(_OPTS_IN_AND_LEAKS))

    def test_the_sweep_names_a_planted_offender_in_a_planted_tree(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("test_bad.py", _BAD_BUILD), ("test_good.py", _GOOD_HELPER),
                               ("test_two.py", _TWO_CLASSES_ONE_OPTS_IN), ("helper.py", _BAD_BUILD)):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write(body)
            self.assertEqual(["test_bad.py::T", "test_two.py::Forgot"], ungraded_classes(tmp))


if __name__ == "__main__":
    unittest.main()
