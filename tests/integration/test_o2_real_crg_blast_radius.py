"""0.0.26 O2 — the blast radius against a REAL code-review-graph, end to end.

Builds a tiny Python repo, indexes it with the real `code-review-graph build`, then asks mokata's
real client (real `code-review-graph serve` over MCP stdio) for a blast radius. Skips cleanly —
with the reason — when the `code-review-graph` package or its console script is not installed.

    pkg/core.py:  leaf()   mid() -> leaf()   unused()
    pkg/app.py:   from pkg.core import mid;  top() -> mid();  entry() -> top() + top()

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

_HAS_CRG = importlib.util.find_spec("code_review_graph") is not None


def _crg_command():
    exe = shutil.which("code-review-graph")
    if exe:
        return exe
    bindir = os.path.dirname(sys.executable)
    for name in ("code-review-graph", "code-review-graph.exe"):
        for d in (bindir, os.path.join(bindir, "Scripts")):
            cand = os.path.join(d, name)
            if os.path.isfile(cand):
                return cand
    return None


_CMD = _crg_command() if _HAS_CRG else None
_WHY = ("code-review-graph is not importable (pip install code-review-graph)" if not _HAS_CRG
        else "the code-review-graph console script was not found next to this Python")

_CORE = "def leaf():\n    return 1\n\n\ndef mid():\n    return leaf() + 1\n\n\ndef unused():\n" \
        "    return 0\n"
_APP = "from pkg.core import mid\n\n\ndef top():\n    return mid()\n\n\ndef entry():\n" \
       "    return top() + top()\n"


@unittest.skipUnless(_HAS_CRG and _CMD, _WHY)
class TestRealCrgBlastRadius(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="o2-real-crg-"))
        os.makedirs(os.path.join(cls.root, "pkg"))
        for rel, text in (("pkg/__init__.py", ""), ("pkg/core.py", _CORE),
                          ("pkg/app.py", _APP)):
            with open(os.path.join(cls.root, *rel.split("/")), "w", encoding="utf-8",
                      newline="\n") as fh:
                fh.write(text)
        cls.client = CodeReviewGraphClient(name=_CMD, root=cls.root, timeout=60.0)
        # NOT a SkipTest here (`test_suite_count_integrity`): a SkipTest from setUpClass makes the
        # class's tests vanish from `Ran N`. The environment condition (CRG installed) is already
        # the class-level `skipUnless`; with CRG PRESENT, a build that fails on a three-file repo
        # is a real failure of the path this file exists to prove, so each test fails, counted.
        cls.built = bool(cls.client.refresh(cls.root, full=True))

    def setUp(self):
        if not self.built:
            self.fail("code-review-graph is installed but `build` failed on the three-file temp "
                      "repo — the real-CRG blast radius could not be exercised")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        shutil.rmtree(cls.root, True)

    def test_the_blast_radius_is_the_transitive_callers(self):
        rows = self.client.query("blast_radius", "leaf", root=self.root, depth=3)
        self.assertEqual([(r["path"], r["line"], r["symbol"]) for r in rows],
                         [("pkg/core.py", 6, "pkg/core.py::mid"),
                          ("pkg/app.py", 5, "pkg/app.py::top"),
                          ("pkg/app.py", 9, "pkg/app.py::entry")])

    def test_an_unindexed_symbol_is_refused_not_zeroed(self):
        from mokata.knowledge.query import GraphNotIndexed
        with self.assertRaises(GraphNotIndexed):
            self.client.query("blast_radius", "no_such_symbol_xyz", root=self.root, depth=2)

    def test_the_layer_answers_from_crg_and_falls_back_to_ast(self):
        from mokata.knowledge.ast_backend import AstBackend
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend
        from mokata.knowledge.grep_backend import GrepBackend
        from mokata.knowledge.layer import KnowledgeLayer
        layer = KnowledgeLayer(
            CodeReviewGraphBackend("code-review-graph", self.root, client=self.client),
            AstBackend(root=self.root, grep=GrepBackend(root=self.root, name="grep")))
        res = layer.blast_radius("leaf", depth=3)
        self.assertEqual((res.backend, res.count, res.degraded), ("code-review-graph", 3, False))
        absent = layer.blast_radius("no_such_symbol_xyz", depth=2)
        # The AST floor answers it — and, holding no definition either, hands the name to its own
        # lexical floor (grep), which is the backend that is named.
        self.assertIn(absent.backend, ("ast", "grep"))
        self.assertIn("'code-review-graph' does not index `no_such_symbol_xyz`", absent.note)


if __name__ == "__main__":
    unittest.main()
