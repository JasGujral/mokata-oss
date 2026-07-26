"""GR.S4 — the SessionStart briefing graph structure slice (HP.S1, pulled here).

The SessionStart briefing gains ONE bounded, graph-derived structure line — inside the
existing ≤2k budget, NO second injection channel. A graph-absent repo degrades clean: the line
is simply absent, so the briefing is byte-identical to before.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest


def _init(root, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=root, profile=profile, assume_yes=True, out=lambda _: None)


def _surface(root):
    from mokata.config import Surface
    return Surface.load(root)


def _write(root, rel, text):
    ab = os.path.join(root, rel)
    os.makedirs(os.path.dirname(ab) or root, exist_ok=True)
    with open(ab, "w", encoding="utf-8") as fh:
        fh.write(text)


_PY = ("def helper():\n    return 1\n\n\nclass Widget:\n    def run(self):\n"
       "        return helper()\n\n\ndef compute():\n    return helper()\n")


class TestBriefingStructureLine(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_structure_line_present_on_python_repo(self):
        _init(self.root)
        _write(self.root, "widgets.py", _PY)
        _write(self.root, "helpers.py", "def other():\n    return 2\n")
        from mokata.bootstrap import build_bootstrap
        text = build_bootstrap(_surface(self.root)).text
        self.assertIn("Code structure", text)
        # top-N modules are named (graph-derived, bounded)
        self.assertTrue("widgets" in text or "helpers" in text)

    def test_byte_identical_without_a_graph(self):
        # A repo with NO Python (nothing the AST/graph floor can answer): the structure line is
        # ABSENT, so the briefing carries no trace of the feature (byte-identical to before).
        _init(self.root)
        _write(self.root, "README.md", "# hi\n")
        from mokata.bootstrap import build_bootstrap
        from mokata.knowledge.layer import graph_structure_line
        self.assertIsNone(graph_structure_line(_surface(self.root)))
        text = build_bootstrap(_surface(self.root)).text
        self.assertNotIn("Code structure", text)

    def test_budget_respected_with_structure_line(self):
        _init(self.root)
        for i in range(12):
            _write(self.root, f"mod_{i}.py", _PY)
        from mokata.bootstrap import build_bootstrap, BOOTSTRAP_TOKEN_BUDGET
        result = build_bootstrap(_surface(self.root))
        self.assertIn("Code structure", result.text)
        self.assertLessEqual(result.token_estimate, BOOTSTRAP_TOKEN_BUDGET)
        self.assertTrue(result.within_budget)

    def test_structure_line_is_bounded_top_n(self):
        _init(self.root)
        for i in range(40):
            _write(self.root, f"mod_{i}.py", _PY)
        from mokata.knowledge.layer import graph_structure_line
        line = graph_structure_line(_surface(self.root), top_n=5)
        self.assertIsNotNone(line)
        # the count reflects the whole repo, but the NAMED modules are bounded to top_n
        self.assertIn("40", line)
        self.assertLessEqual(line.count("mod_"), 5)


if __name__ == "__main__":
    unittest.main()
