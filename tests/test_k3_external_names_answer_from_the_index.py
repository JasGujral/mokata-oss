"""0.0.25 K3 — a name the repo does not define is answered from the index, not by re-reading the repo.

`str`, `append`, a library call: the AST floor holds no definition of it, so an empty answer fell
through to the grep floor, which re-read EVERY source file (3.6 s per query on the 1.2M-line repo)
to re-discover, for the Python files, what the index had already answered. Now the lexical floor
searches only the files the index does NOT hold (non-Python sources, `.py` that `ast.parse`
rejected). What must stay true (doc 85 §7g):
  * the verdict does not move — such an answer is NOT a verified zero (basis stays lexical,
    `degraded` stays True) and its note says why it is not observable;
  * the genuinely-unindexed case still answers: a symbol defined only in a non-Python file, or in
    a Python file that does not parse, is still found lexically;
  * a leaf the index DOES hold stays a verified zero.

Mutations this module was checked against (each went RED, then was restored):
  M1 `_outside_the_index` scans the whole repo again (`files=None`) -> test_no_python_file_is_re_read
  M2 the uncovered set drops unparseable `.py`                       -> test_a_python_file_that_does_not_parse_is_still_searched
"""

import _support  # noqa: F401

import os
import tempfile
import unittest
from unittest import mock

from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.grep_backend import GrepBackend


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestExternalNames(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k3-")
        write(self.root, "app/a.py", "import json\n\n\ndef work(x):\n    s = str(x)\n"
                                     "    return json.dumps(s)\n\n\ndef leaf():\n    return 1\n")
        write(self.root, "app/b.py", "from app.a import work\n\n\ndef run():\n    return work(2)\n")
        # a non-Python source the AST floor cannot index (the TS walker is optional; Go never is)
        write(self.root, "svc/main.go", "package main\n\nfunc goOnly() int {\n\treturn helperGo()\n}\n")
        # a Python file the stdlib parser rejects — a coverage gap, never a crash
        write(self.root, "app/broken.py", "def brokenDef(:\n    return nowhere()\n")
        self.b = AstBackend(root=self.root, grep=GrepBackend(root=self.root),
                            cache_dir=tempfile.mkdtemp(prefix="k3-cache-"))

    def test_a_builtin_is_not_observable_and_says_why(self):
        r = self.b.query("callees", "str")
        self.assertEqual(r.references, [])
        self.assertTrue(r.degraded, "an external name's zero is NOT a structural answer")
        self.assertFalse(r.verified_empty, "the floor holds no definition — it cannot certify zero")
        self.assertIn("not observable", r.note)
        self.assertIn("NOT a verified zero", r.note)
        self.assertIn("external or undefined", r.note)

    def test_no_python_file_is_re_read(self):
        read = []
        real = GrepBackend._read_file

        def spy(path):
            read.append(os.path.relpath(path, self.root).replace(os.sep, "/"))
            return real(path)
        self.b._ensure_index()
        with mock.patch.object(GrepBackend, "_read_file", staticmethod(spy)):
            self.b.query("callees", "str")
            self.b.query("blast_radius", "dumps", depth=2)
            self.b.query("defs", "nothing_like_this")
        indexed = {p for p in read if p in ("app/a.py", "app/b.py")}
        self.assertEqual(indexed, set(), f"the lexical floor re-read indexed Python: {read}")
        self.assertIn("svc/main.go", read, "the unindexed source must still be searched")

    def test_a_symbol_defined_only_outside_python_is_still_found(self):
        r = self.b.query("defs", "goOnly")
        self.assertEqual([x.path for x in r.references], ["svc/main.go"])
        self.assertTrue(r.degraded)
        c = self.b.query("callees", "goOnly")
        self.assertIn("helperGo", [x.symbol for x in c.references])

    def test_a_python_file_that_does_not_parse_is_still_searched(self):
        r = self.b.query("defs", "brokenDef")
        self.assertEqual([x.path for x in r.references], ["app/broken.py"])

    def test_a_leaf_the_index_holds_stays_a_verified_zero(self):
        r = self.b.query("callers", "leaf")
        self.assertTrue(r.verified_empty)
        self.assertFalse(r.degraded)

    def test_an_external_name_with_call_sites_is_still_structural(self):
        r = self.b.query("callers", "str")
        self.assertEqual([(x.path, x.symbol) for x in r.references], [("app/a.py", "work")])
        self.assertEqual(r.basis, "structural")


if __name__ == "__main__":
    unittest.main()
