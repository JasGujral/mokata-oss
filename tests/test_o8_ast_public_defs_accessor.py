"""0.0.26 O8 (AST-PUBLIC-DEFS-ACCESSOR) — ⌘P reads the AST floor's definitions through a PUBLIC
accessor (`AstBackend.definitions()` + `index_generation`), never through the private `_edges`.

Asserted:
  * `definitions()` returns every definition the index holds as (name, path, line, kind), loading
    the index itself, and reflects a change once the index is invalidated;
  * `index_generation` is None while no index is loaded and moves with every rebuild;
  * ⌘P's symbol list picks up a new definition after the floor's index is rebuilt (the staleness
    check reads the public generation);
  * no module under src/mokata outside the AST backend touches `_edges` (source scan).
"""
import os
import re
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.init import init_repo
from mokata.knowledge.ast_backend import AstBackend

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src", "mokata"))
OWNER = "knowledge/ast_backend.py"          # a NAME: POSIX-spelled on every platform


def _silent(*_a, **_k):
    pass


def _write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class ThePublicAccessor(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-o8-a-")
        self.addCleanup(shutil.rmtree, self.root, True)
        _write(self.root, "pkg/a.py", "def alpha():\n    pass\n\n\nclass Beta:\n    def gamma(self):\n"
                                      "        pass\n")
        _write(self.root, "pkg/b.py", "def delta():\n    pass\n")
        self.b = AstBackend(self.root, cache_dir=os.path.join(self.root, ".cache"))

    def test_definitions_lists_every_def_and_loads_the_index(self):
        self.assertIsNone(self.b.index_generation, "an unloaded index has no generation")
        rows = self.b.definitions()
        names = {(n, p) for n, p, _l, _k in rows}
        self.assertTrue({("alpha", "pkg/a.py"), ("Beta", "pkg/a.py"), ("gamma", "pkg/a.py"),
                         ("delta", "pkg/b.py")} <= names, rows)
        self.assertEqual(sorted(r[1] for r in rows), [r[1] for r in rows], "ordered by path")
        alpha = next(r for r in rows if r[0] == "alpha")
        self.assertEqual(1, alpha[2])
        self.assertIsInstance(alpha[2], int)
        self.assertIsNotNone(self.b.index_generation)

    def test_the_generation_moves_with_a_rebuild_and_definitions_follow(self):
        self.b.definitions()
        g1 = self.b.index_generation
        _write(self.root, "pkg/c.py", "def epsilon():\n    pass\n")
        self.b.invalidate()
        self.assertIsNone(self.b.index_generation)
        rows = self.b.definitions()
        self.assertIn("epsilon", {r[0] for r in rows})
        self.assertNotEqual(g1, self.b.index_generation)


class QuickOpenReadsThePublicAccessor(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-o8-q-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(nav.close_layers)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        _write(self.root, "src/app/one.py", "def first_thing():\n    pass\n")

    def test_symbols_rebuild_when_the_floors_index_does(self):
        names = {r[0] for r in nav.symbol_index(self.root)}
        self.assertIn("first_thing", names)
        floor = nav._ast_floor(nav._layer(self.root))
        if floor is None:
            self.skipTest("no AST floor in this layer")
        _write(self.root, "src/app/two.py", "def second_thing():\n    pass\n")
        floor.invalidate()           # what the freshness controller does on a real change
        names = {r[0] for r in nav.symbol_index(self.root)}
        self.assertIn("second_thing", names, "⌘P kept serving rows from a dropped index")


class NoCallerOutsideTheBackendTouchesEdges(unittest.TestCase):

    PRIVATE = re.compile(r"""\._edges\b|['"]_edges['"]""")

    def test_source_scan(self):
        offenders = []
        # CORPUS: THE WORKING TREE — what mokata SHIPS (`sync-public.sh` rsyncs the tree), so an
        # untracked module reaching into `_edges` is exactly the one this must see.
        for dirpath, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = _support.posix_rel(path, SRC)
                if rel == OWNER:
                    continue
                with open(path, encoding="utf-8") as fh:
                    for n, line in enumerate(fh, 1):
                        if self.PRIVATE.search(line):
                            offenders.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual([], offenders, "private AstBackend._edges read outside the backend")

    def test_the_scan_would_catch_the_old_spelling(self):
        self.assertTrue(self.PRIVATE.search('getattr(floor, "_edges", None)'))
        self.assertTrue(self.PRIVATE.search("floor._edges"))
        self.assertFalse(self.PRIVATE.search("from .memory import edges as _edges"))
        self.assertFalse(self.PRIVATE.search("_edges.SRC_COLUMN"))


if __name__ == "__main__":
    unittest.main()
