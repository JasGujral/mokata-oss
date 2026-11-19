"""0.0.29 D4: the real-CRG leg is WIRED, and the wiring is graded (verifier D36 M1).

D4 opted the real-code-review-graph classes in (`test_o13_crg_auto_build.ARealBuildOnATinyRepo`,
`test_p39_ide_first_run`'s real-IDE step) and gave them one place to run: `real-crg.yml`, weekly and on
dispatch, never on a PR. Three properties make that leg mean something, and before this module
nothing read any of them — so "the real-CRG classes never ran" could come back without a red:

1. `real-crg.yml` runs on `schedule` + `workflow_dispatch` ONLY (no push / pull_request /
   pull_request_target / workflow_run): a weekly signal, never a merge gate (ruling D-Q4);
2. the step that discovers o13 and p39 sets `MOKATA_REQUIRE_REAL_CRG: "1"`;
3. each real-CRG skip in o13 and p39 is `skipUnless(_HAS_CRG or _REQUIRE_REAL_CRG, ...)` with
   `_REQUIRE_REAL_CRG` read from that variable — so under the leg a missing package is a RED, not a skip.

`wiring_faults` is pure (text in, faults out) and is graded against a planted corpus first.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import re
import unittest

import _support  # noqa: F401  (puts src/ on the path; PyYAML is guaranteed by it)
import yaml

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "real-crg.yml")
REQUIRE = "MOKATA_REQUIRE_REAL_CRG"
#: The modules whose real-CRG classes the leg runs, and must name.
MODULES = ("test_o13_crg_auto_build", "test_p39_ide_first_run")
ALLOWED_TRIGGERS = {"schedule", "workflow_dispatch"}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _require_bound(tree):
    """True when the module binds `_REQUIRE_REAL_CRG = os.environ.get("MOKATA_REQUIRE_REAL_CRG") == "1"`."""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_REQUIRE_REAL_CRG"):
            src = ast.unparse(node.value)
            return src in ('os.environ.get(%r) == %r' % (REQUIRE, "1"),
                           "os.environ.get('%s') == '1'" % REQUIRE)
    return False


def _crg_skips(tree):
    """Every `skipUnless(...)` decorator whose condition names `_HAS_CRG`, as its unparsed test."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "skipUnless" and dec.args
                        and "_HAS_CRG" in ast.unparse(dec.args[0])):
                    out.append((node.name, dec.args[0]))
    return out


def _has_require_arm(test):
    return (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)
            and any(isinstance(v, ast.Name) and v.id == "_REQUIRE_REAL_CRG" for v in test.values))


def wiring_faults(workflow_text, sources):
    """Why the real-CRG leg is not wired (empty when it is). `sources` maps module name -> text."""
    faults = []
    doc = yaml.safe_load(workflow_text) or {}
    on = doc.get("on", doc.get(True)) or {}
    triggers = set(on) if isinstance(on, dict) else set(on if isinstance(on, list) else [on])
    if triggers != ALLOWED_TRIGGERS:
        faults.append("triggers are %s, not exactly schedule + workflow_dispatch" % sorted(triggers))
    steps = [s for job in (doc.get("jobs") or {}).values() for s in (job.get("steps") or [])]
    for mod in MODULES:
        line = re.compile(r'discover -s tests -t tests -p "%s\.py"' % re.escape(mod))
        runs = [s for s in steps if line.search(str(s.get("run", "")))]
        if not runs:
            faults.append("no step discovers %s" % mod)
        elif not any(str((s.get("env") or {}).get(REQUIRE, "")) == "1" for s in runs):
            faults.append("the step that runs %s does not set %s=1" % (mod, REQUIRE))
    for mod in MODULES:
        tree = ast.parse(sources[mod])
        if not _require_bound(tree):
            faults.append("%s does not read _REQUIRE_REAL_CRG from %s" % (mod, REQUIRE))
        skips = _crg_skips(tree)
        if not skips:
            faults.append("%s has no real-CRG skipUnless" % mod)
        for name, test in skips:
            if not _has_require_arm(test):
                faults.append("%s.%s skips without the require arm" % (mod, name))
    return faults


def _real():
    return _read(WORKFLOW), {m: _read(os.path.join(TESTS, m + ".py")) for m in MODULES}


class ThePlantedCorpusIsConvicted(unittest.TestCase):
    """FIRING CONTROLS: each plant is one way the leg stops meaning anything."""

    def setUp(self):
        self.wf, self.src = _real()

    def _faults(self, wf=None, **src):
        return wiring_faults(wf or self.wf, dict(self.src, **src))

    def _swap(self, text, old, new):
        self.assertEqual(1, text.count(old), "the plant's anchor moved: %r" % old)
        return text.replace(old, new)

    def test_a_pull_request_trigger_is_convicted(self):
        wf = self._swap(self.wf, "  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n")
        self.assertTrue(any("triggers" in f for f in self._faults(wf)))

    def test_a_push_trigger_is_convicted(self):
        wf = self._swap(self.wf, "  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n")
        self.assertTrue(any("triggers" in f for f in self._faults(wf)))

    def test_a_dropped_schedule_is_convicted(self):
        wf = re.sub(r"  schedule:\n(?:    #[^\n]*\n)*    - cron: \"[^\"]*\"\n", "", self.wf)
        self.assertTrue(wf != self.wf, "the plant's anchor moved")
        self.assertTrue(any("triggers" in f for f in self._faults(wf)))

    def test_the_require_env_removed_is_convicted(self):
        wf = re.sub(r"\n *%s: \"1\"[^\n]*" % REQUIRE, "", self.wf)
        self.assertTrue(wf != self.wf, "the plant's anchor moved")
        faults = self._faults(wf)
        self.assertEqual(2, sum("does not set" in f for f in faults), faults)

    def test_a_dropped_discover_line_is_convicted(self):
        wf = re.sub(r"\n *python -m unittest discover[^\n]*test_p39_ide_first_run\.py[^\n]*", "", self.wf)
        self.assertTrue(wf != self.wf, "the plant's anchor moved")
        self.assertIn("no step discovers test_p39_ide_first_run", self._faults(wf))

    def test_the_require_arm_dropped_from_p39_is_convicted(self):
        p39 = self._swap(self.src["test_p39_ide_first_run"],
                         "@unittest.skipUnless(_HAS_CRG or _REQUIRE_REAL_CRG,",
                         "@unittest.skipUnless(_HAS_CRG,")
        self.assertTrue(any("without the require arm" in f
                            for f in self._faults(test_p39_ide_first_run=p39)))

    def test_the_require_arm_dropped_from_o13_is_convicted(self):
        o13 = self._swap(self.src["test_o13_crg_auto_build"],
                         "@unittest.skipUnless(_HAS_CRG or _REQUIRE_REAL_CRG,",
                         "@unittest.skipUnless(_HAS_CRG,")
        self.assertTrue(any("without the require arm" in f
                            for f in self._faults(test_o13_crg_auto_build=o13)))

    def test_a_require_read_from_another_variable_is_convicted(self):
        o13 = self._swap(self.src["test_o13_crg_auto_build"],
                         '_REQUIRE_REAL_CRG = os.environ.get("%s") == "1"' % REQUIRE,
                         '_REQUIRE_REAL_CRG = os.environ.get("MOKATA_OTHER") == "1"')
        self.assertTrue(any("does not read" in f for f in self._faults(test_o13_crg_auto_build=o13)))


class TheRealLegIsWired(unittest.TestCase):
    def test_real_crg_yml_and_the_two_modules_are_wired(self):
        # CORPUS: THE WORKING TREE — the workflow and modules a run on this checkout uses.
        wf, src = _real()
        self.assertEqual([], wiring_faults(wf, src))


if __name__ == "__main__":
    unittest.main()
