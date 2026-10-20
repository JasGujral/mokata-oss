"""0.0.25 K1 — the AST floor answers from reverse indexes built ONCE, and writes its cache ONCE.

What must be true for the user (measured on a 1.2M-line repo, 453k call edges):
  * every `callers` / `callees` / `defs` / `implementers` / `imports` question used to scan EVERY
    edge (25-100 ms each, multiplied by ripple / path / blast radius). It is now a dict lookup —
    and the answers are ELEMENT-FOR-ELEMENT the ones the linear scan gave (same order, same
    basis, same notes). `LinearOracle` below is the pre-0.0.25 scan, kept verbatim as the oracle.
  * a cold build wrote the ~30 MB edge cache TWICE (cold walk → invalidate → rebuild, the second
    write byte-identical, ~28 s). The cache is now written only when an edge actually changed,
    and an `invalidate()` rebuild re-uses unchanged files from memory instead of re-reading it.

Mutations this module was checked against (each went RED, then was restored):
  M1 queries scan `_edges` again (the pre-fix `_callers`)       -> test_queries_never_scan_the_edge_list
  M2 `_rev` walks the files in reverse order                     -> test_every_answer_equals_the_linear_scan
  M3 `_ensure_index` saves unconditionally                       -> test_a_warm_start_does_not_rewrite_the_cache
  M4 `invalidate()` forgets the prior index (re-reads the cache) -> test_a_rebuild_reuses_unchanged_files_from_memory
  M5 an unparseable file is not remembered as UNPARSED           -> test_an_unchanged_broken_file_is_neither_reparsed_nor_rewritten
"""

import _support  # noqa: F401

import os
import tempfile
import unittest
from unittest import mock

from mokata.knowledge import ast_backend as ast_mod
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.query import Reference

FILES = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "import os\n"
        "from pkg.util import helper, other as alias_other\n"
        "\n"
        "class Base:\n"
        "    def run(self):\n"
        "        return helper(1) + helper(2)\n"           # two calls, one line
        "\n"
        "class Child(Base, Base2):\n"
        "    def run(self):\n"
        "        x = helper(3)\n"
        "        self.log(x)\n"
        "        return self.run()\n"                       # a scope calling its own name
        "\n"
        "def compute(v):\n"
        "    def inner(w):\n"
        "        return helper(w)\n"
        "    return inner(v) + str(v)\n"
    ),
    "pkg/util.py": (
        "def helper(x):\n"
        "    return other(x)\n"
        "\n"
        "def other(y):\n"
        "    return y\n"
        "\n"
        "class Base2:\n"
        "    pass\n"
        "\n"
        "class Grand(Child, Base):\n"
        "    pass\n"
    ),
    "app/main.py": (
        "from pkg.core import compute, Child\n"
        "import pkg.util\n"
        "\n"
        "def main():\n"
        "    compute(1)\n"
        "    Child().run()\n"
        "    pkg.util.helper(9)\n"
        "    return compute(2)\n"
        "\n"
        "def lonely():\n"
        "    return 0\n"
    ),
    "tests/test_main.py": (
        "from app.main import main\n"
        "\n"
        "def test_main():\n"
        "    assert main() is None or True\n"
    ),
}


def write_repo(root):
    for rel, text in FILES.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class LinearOracle:
    """The pre-0.0.25 scans, verbatim, over the same `_edges` — the definition of a right answer."""

    def __init__(self, edges):
        self.edges = edges

    def callers(self, target):
        out, seen = [], set()
        for rel, fe in self.edges.items():
            for callee, line, scope in fe.calls:
                if callee != target or (rel, line) in seen:
                    continue
                seen.add((rel, line))
                out.append(Reference(rel, line, "", scope or None))
        return out

    def callees(self, target):
        out, seen = [], set()
        for rel, fe in self.edges.items():
            for callee, line, scope in fe.calls:
                if scope != target or callee == target or (rel, line, callee) in seen:
                    continue
                seen.add((rel, line, callee))
                out.append(Reference(rel, line, "", callee))
        return out

    def implementers(self, target):
        return [Reference(rel, line, "", cname) for rel, fe in self.edges.items()
                for cname, line, bases in fe.classes if target in bases]

    def defs(self, target):
        return [Reference(rel, line, "", name, metadata={"kind": dkind})
                for rel, fe in self.edges.items() for name, line, dkind in fe.defs
                if name == target]

    def imports(self, target):
        out, seen = [], set()
        for rel, fe in self.edges.items():
            for token, line, _k in fe.imports:
                if token != target or (rel, line) in seen:
                    continue
                seen.add((rel, line))
                out.append(Reference(rel, line, "", None))
        return out

    def blast_radius(self, target, depth):
        seen_syms, seen_refs, out, frontier = {target}, set(), [], [target]
        for _ in range(max(1, depth)):
            nxt = []
            for sym in frontier:
                for r in self.callers(sym):
                    if (r.path, r.line) not in seen_refs:
                        seen_refs.add((r.path, r.line))
                        out.append(r)
                    if r.symbol and r.symbol not in seen_syms:
                        seen_syms.add(r.symbol)
                        nxt.append(r.symbol)
            frontier = nxt
            if not frontier:
                break
        return out


def dicts(refs):
    return [r.to_dict() for r in refs]


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k1-")
        self.cache = tempfile.mkdtemp(prefix="k1-cache-")
        write_repo(self.root)

    def backend(self):
        return AstBackend(root=self.root, grep=GrepBackend(root=self.root), cache_dir=self.cache)


class TestTheIndexAnswersExactlyAsTheScanDid(_Base):
    def test_every_answer_equals_the_linear_scan(self):
        b = self.backend()
        b._ensure_index()
        oracle = LinearOracle(b._edges)
        names = set()
        for fe in b._edges.values():
            names.update(c[0] for c in fe.calls)
            names.update(c[2] for c in fe.calls)
            names.update(d[0] for d in fe.defs)
            names.update(i[0] for i in fe.imports)
            for c in fe.classes:
                names.update(c[2])
        names.add("no_such_symbol")
        self.assertGreater(len(names), 20)
        for n in sorted(names):
            for kind in ("callers", "callees", "implementers", "defs", "imports"):
                self.assertEqual(dicts(getattr(b, "_" + kind)(n)), dicts(getattr(oracle, kind)(n)),
                                 f"{kind}({n})")
            for depth in (1, 2, 3, 5):
                self.assertEqual(dicts(b._blast_radius(n, depth)),
                                 dicts(oracle.blast_radius(n, depth)), f"blast_radius({n}, {depth})")

    def test_the_public_answer_is_unchanged_in_basis_and_note(self):
        b = self.backend()
        r = b.query("callers", "helper")
        self.assertEqual(r.basis, "structural")
        self.assertEqual(r.note, ast_mod.AST_NOTE)
        self.assertFalse(r.truncated)
        leaf = b.query("callers", "lonely")
        self.assertEqual(leaf.basis, "verified_empty")
        self.assertEqual(leaf.note, ast_mod.AST_EMPTY_NOTE)

    def test_queries_never_scan_the_edge_list(self):
        """After the build, a query must not touch `_edges` at all — that is the whole fix."""
        b = self.backend()
        b._ensure_index()
        expected = {k: dicts(getattr(b, "_" + k)("helper"))
                    for k in ("callers", "callees", "defs", "implementers", "imports")}

        class NoScan(dict):
            def items(self):
                raise AssertionError("a query scanned every edge")

            def values(self):
                raise AssertionError("a query scanned every edge")

        b._edges = NoScan(b._edges)
        for k, want in expected.items():
            self.assertEqual(dicts(getattr(b, "_" + k)("helper")), want)
        self.assertTrue(b._blast_radius("other", 4))


class TestTheCacheIsWrittenOnlyWhenItChanged(_Base):
    def _count_saves(self, b):
        calls = []
        real = b._save_cache

        def spy(entries):
            calls.append(len(entries))
            return real(entries)
        b._save_cache = spy
        return calls

    def test_a_cold_build_writes_the_cache_exactly_once(self):
        b = self.backend()
        saves = self._count_saves(b)
        b.query("callers", "helper")
        self.assertEqual(len(saves), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.cache, ast_mod.CACHE_FILENAME)))

    def test_a_warm_start_does_not_rewrite_the_cache(self):
        self.backend().query("callers", "helper")
        path = os.path.join(self.cache, ast_mod.CACHE_FILENAME)
        before = os.stat(path).st_mtime_ns
        b = self.backend()
        saves = self._count_saves(b)
        b.query("callers", "helper")
        b.invalidate()                                # the freshness rebuild, nothing changed
        b.query("callers", "helper")
        self.assertEqual(saves, [], "an unchanged repo rewrote the edge cache")
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_a_rebuild_reuses_unchanged_files_from_memory(self):
        b = self.backend()
        b.query("callers", "helper")
        saves = self._count_saves(b)
        with open(os.path.join(self.root, "pkg", "util.py"), "a", encoding="utf-8") as fh:
            fh.write("\ndef added():\n    return helper(0)\n")
        parsed = []
        real_parse = ast_mod.parse_source
        b.invalidate()
        with mock.patch.object(ast_mod, "parse_source",
                               side_effect=lambda s: parsed.append(1) or real_parse(s)), \
                mock.patch.object(AstBackend, "_load_cache",
                                  side_effect=AssertionError("re-read the cache from disk")):
            r = b.query("callers", "helper")
        self.assertEqual(len(parsed), 1, "only the edited file is re-parsed")
        self.assertIn(("pkg/util.py", "added"), {(x.path, x.symbol) for x in r.references})
        self.assertEqual(len(saves), 1, "a real change is written, once")

    def test_an_unchanged_broken_file_is_neither_reparsed_nor_rewritten(self):
        with open(os.path.join(self.root, "pkg", "broken.py"), "w", encoding="utf-8") as fh:
            fh.write("def nope(:\n    pass\n")
        self.backend().query("callers", "helper")          # cold: parses everything once
        b = self.backend()
        saves = self._count_saves(b)
        parsed = []
        real_parse = ast_mod.parse_source
        with mock.patch.object(ast_mod, "parse_source",
                               side_effect=lambda s: parsed.append(1) or real_parse(s)):
            b.query("callers", "helper")
            b.invalidate()
            b.query("callers", "helper")
        self.assertEqual(parsed, [], "a file that did not parse last time was parsed again")
        self.assertEqual(saves, [], "a broken-but-unchanged file forced a cache rewrite")
        self.assertEqual(b.query("defs", "nope").references[0].path, "pkg/broken.py",
                         "the broken file is still searched lexically (K3)")

    def test_a_deleted_file_leaves_the_cache_and_the_answers(self):
        b = self.backend()
        b.query("callers", "helper")
        os.remove(os.path.join(self.root, "app", "main.py"))
        b.invalidate()
        saves = self._count_saves(b)
        r = b.query("callers", "compute")
        self.assertNotIn("app/main.py", {x.path for x in r.references})
        self.assertEqual(len(saves), 1)
        fresh = self.backend()
        self.assertNotIn("app/main.py", fresh._load_cache())


if __name__ == "__main__":
    unittest.main()
