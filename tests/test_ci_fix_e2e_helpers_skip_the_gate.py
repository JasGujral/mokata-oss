"""The IDE browser e2e runs in a slim interpreter, so nothing it imports may pull the provisioning gate.

ci.yml's `ide-e2e` job installs playwright and mokata's runtime deps — no PyYAML, no console scripts
— on purpose. `_support` runs `_provisioning.refuse_unless_provisioned()`, which REFUSES with
`os._exit(2)` in exactly that interpreter. `tests/_explain_fixture.py` did `import _support`, and
`e2e_ide/test_explainer.py` imports the fixture, so the refusal killed the WHOLE e2e run mid-flight
and hid every later result (the 0.0.27 release PR's `ide-e2e` red).

Derived, not hand-listed: every `tests/` helper reachable from any `tests/e2e_ide/*.py` module (by
`import X` / `from X import ...`, including inside functions and generated scripts), transitively.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import re
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
E2E = os.path.join(TESTS, "e2e_ide")
GATED = ("_support", "_provisioning")
_IMPORT = re.compile(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", re.M)
_INLINE = re.compile(r"(?:from\s+(\w+)\s+import|\bimport\s+(\w+))")


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _helpers_named_in(text):
    names = {a or b for a, b in _IMPORT.findall(text)} | {a or b for a, b in _INLINE.findall(text)}
    return {n for n in names if os.path.isfile(os.path.join(TESTS, n + ".py"))}


def _reachable_from_e2e():
    """{helper: the chain that reaches it}"""
    seen, todo = {}, []
    # CORPUS: THE WORKING TREE — the e2e modules and helpers as they sit on disk, which is what the
    # e2e job runs (and what sync-public.sh mirrors).
    for name in sorted(os.listdir(E2E)):
        if name.endswith(".py"):
            for h in _helpers_named_in(_read(os.path.join(E2E, name))):
                if h not in seen:
                    seen[h] = "e2e_ide/%s -> %s" % (name, h)
                    todo.append(h)
    while todo:
        cur = todo.pop()
        for h in _helpers_named_in(_read(os.path.join(TESTS, cur + ".py"))):
            if h not in seen:
                seen[h] = "%s -> %s" % (seen[cur], h)
                todo.append(h)
    return seen


class TheE2eRunsInTheSlimInterpreter(unittest.TestCase):
    def test_no_helper_an_e2e_module_reaches_pulls_the_provisioning_gate(self):
        reach = _reachable_from_e2e()
        self.assertIn("_explain_fixture", reach, "the walk must find the fixture that was the red")
        self.assertIn("_plan", reach)
        bad = {h: chain for h, chain in reach.items() if h in GATED}
        self.assertEqual({}, bad, "an e2e module reaches the provisioning gate, which REFUSES "
                                  "(os._exit 2) in ci.yml's slim ide-e2e interpreter: %s" % bad)

    def test_the_walk_sees_the_gate_when_it_is_there(self):
        # the instrument must be able to be red: a helper that imports `_support` is reached by name
        self.assertEqual({"_support"}, _helpers_named_in("import _support  # noqa\n"))
        self.assertEqual({"_plan"}, _helpers_named_in("    from _plan import commit_planned\n"))


if __name__ == "__main__":
    unittest.main()
