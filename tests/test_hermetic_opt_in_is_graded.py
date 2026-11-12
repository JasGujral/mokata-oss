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
    """`<env>.pop("MOKATA_CRG"[, default])` — on `os.environ` (the class-level form) or on the
    copy a subprocess is launched with (`env = dict(os.environ, ...)`)."""
    f = call.func
    return (isinstance(f, ast.Attribute) and f.attr == "pop"
            and bool(call.args) and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == _OPT_IN_VAR)


def classify(source):
    """(exercises_the_real_path, opts_in) for one module's source."""
    tree = ast.parse(source)
    real = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name == "build_now" and not any(k.arg == "runner" for k in node.keywords):
            real = True
        elif name in _GATE_CALLS and _names_the_real_tool(node):
            real = True
    opts_in = False
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and (_callee(node) == "real_crg"
                                               or _is_pop_of_the_switch(node)):
                opts_in = True
    return real, opts_in


def ungraded_modules(root):
    """Relative paths of every test module that exercises the real path and does not opt in."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                real, opts_in = classify(fh.read())
            if real and not opts_in:
                out.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return out


def real_path_modules(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.startswith("test_") and name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    if classify(fh.read())[0]:
                        found.append(name)
    return sorted(found)


#: ⚠ KNOWN GAP, NOT A PASS. These two modules drive the real tool but gate themselves with
#: `mokata.detect.find_command("code-review-graph")`, which honours the hermetic `MOKATA_CRG=off`
#: and answers None — so their real-CRG classes are SKIPPED in every default run, and no CI leg
#: runs them live (`real-crg.yml` runs only `test_gr_s2_*`). An opt-in inside the class would never
#: execute, so none is added here (0.0.28 lane A, verifier M2). They are listed so the gap is
#: visible and so the sweep stays green for everything else; fixing one (gate it on the package, not
#: on `find_command`, then opt in) must delete its entry — `test_the_known_gap_entries_are_live`
#: reds on a stale one. Candidate for doc 84.
KNOWN_UNRUN_REAL_CRG = {
    "test_o13_crg_auto_build.py": "ARealBuildOnATinyRepo skips under MOKATA_CRG=off",
    "test_p39_ide_first_run.py": "InTheBrowser_RealIde graph test skips under MOKATA_CRG=off",
}


class TheTreeOptsIn(unittest.TestCase):
    def test_the_known_gap_entries_are_live(self):
        """An entry that names a module that opts in now (or is gone) is a stale exemption."""
        ungraded = {os.path.basename(m) for m in ungraded_modules(TESTS)}
        for name in KNOWN_UNRUN_REAL_CRG:
            self.assertIn(name, ungraded, "%s no longer needs its exemption — delete the entry" % name)

    def test_every_module_that_needs_the_real_crg_opts_in(self):
        self.assertEqual(
            [], [m for m in ungraded_modules(TESTS)
                 if os.path.basename(m) not in KNOWN_UNRUN_REAL_CRG],
            "these test modules drive the real code-review-graph but never opt in, so under the "
            "hermetic MOKATA_CRG=off they fail with 'switched off' — call `_support.real_crg(self)` "
            "in setUp, or pop MOKATA_CRG under a patch in setUpClass (see o2)")

    def test_the_detector_still_sees_the_known_real_crg_modules(self):
        """Anti-vacuity on the LIVE tree: a detector that matched nothing would also report
        'nothing ungraded'."""
        seen = real_path_modules(TESTS)
        for known in ("test_o2_real_crg_blast_radius.py", "test_o13_crg_auto_build.py",
                      "test_vscale_full_rebuild_of_a_copied_repo.py"):
            self.assertIn(known, seen)


_GOOD_CLASS_LEVEL = '''
import importlib.util, os, unittest
from unittest import mock
_HAS = importlib.util.find_spec("code_review_graph") is not None
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = mock.patch.dict(os.environ); p.start(); cls.addClassCleanup(p.stop)
        os.environ.pop("MOKATA_CRG", None)
'''
_GOOD_HELPER = '''
import _support
from mokata.knowledge import crg_build as B
class T:
    def setUp(self):
        _support.real_crg(self)
    def test_x(self):
        B.build_now("r")
'''
_BAD_GATE_ONLY = '''
import importlib.util
_HAS = importlib.util.find_spec("code_review_graph") is not None
class T:
    def test_x(self):
        pass
'''
_BAD_BUILD = '''
from mokata.knowledge import crg_build as B
class T:
    def test_x(self):
        B.build_now("r", full=True)
'''
_FAKE_RUNNER = '''
from mokata.knowledge import crg_build as B
class T:
    def test_x(self):
        B.build_now("r", runner=lambda *a, **k: None)
'''
_MODULE_LEVEL_POP = '''
import os
from mokata.knowledge import crg_build as B
os.environ.pop("MOKATA_CRG", None)
class T:
    def test_x(self):
        B.build_now("r")
'''
_POP_OF_SOMETHING_ELSE = '''
import os
from mokata.knowledge import crg_build as B
class T:
    def setUp(self):
        os.environ.pop("HOME", None)
    def test_x(self):
        B.build_now("r")
'''
_UNRELATED = '''
class T:
    def test_x(self):
        self.assertTrue(True)
'''


class TheDetectorIsGradedAgainstSuppliedSources(unittest.TestCase):
    """§7i — each case is a SUPPLIED module, so the verdicts do not depend on today's tree."""

    def test_the_class_level_opt_in_is_accepted(self):
        self.assertEqual((True, True), classify(_GOOD_CLASS_LEVEL))

    def test_the_helper_opt_in_is_accepted(self):
        self.assertEqual((True, True), classify(_GOOD_HELPER))

    def test_gating_on_the_package_without_an_opt_in_is_ungraded(self):
        self.assertEqual((True, False), classify(_BAD_GATE_ONLY))

    def test_a_real_build_without_an_opt_in_is_ungraded(self):
        self.assertEqual((True, False), classify(_BAD_BUILD))

    def test_an_injected_runner_is_a_fake_and_needs_no_opt_in(self):
        self.assertEqual((False, False), classify(_FAKE_RUNNER))

    def test_a_module_level_pop_is_not_an_opt_in(self):
        """It would switch the tool on for the whole process, not for the test that needs it."""
        self.assertEqual((True, False), classify(_MODULE_LEVEL_POP))

    def test_popping_some_other_variable_is_not_an_opt_in(self):
        self.assertEqual((True, False), classify(_POP_OF_SOMETHING_ELSE))

    def test_an_unrelated_module_is_neither(self):
        self.assertEqual((False, False), classify(_UNRELATED))

    def test_the_sweep_names_a_planted_offender_in_a_planted_tree(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("test_bad.py", _BAD_BUILD), ("test_good.py", _GOOD_HELPER),
                               ("helper.py", _BAD_BUILD)):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write(body)
            self.assertEqual(["test_bad.py"], ungraded_modules(tmp))


if __name__ == "__main__":
    unittest.main()
