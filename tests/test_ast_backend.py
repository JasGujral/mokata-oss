"""GR.S1 — the embedded stdlib-AST graph floor.

A real, zero-dependency structural floor ABOVE the grep floor for Python: mokata walks
Python's own `ast` into typed edges (imports / defs / calls) and answers every
`QUERY_KINDS` query with `degraded=False`. Grep stays the EMERGENCY floor only — non-`.py`
targets, files `ast.parse` rejects, and repos with zero `.py` files fall through to it
exactly as before (`degraded=True`).

Deliverable → test map (GR.S1 prompt):
  1. AstBackend edges/queries ......... TestAstBackendEdges (+ test_gr_s1_regression)
  2. incremental per-file edge cache .. TestAstIncrementalCache
  3. selection prefers AST ............ TestAstSelection
  4. honesty (degraded=False + note) .. TestAstBackendEdges.test_ast_note_and_not_degraded
  5. no behaviour change .............. TestGrepEmergencyFloor + TestAstSelection
  7. secret-safety (cache content) .... TestAstIncrementalCache.test_edge_cache_stores_no_source
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import GrepBackend, KnowledgeLayer, QueryResult
from mokata.knowledge import ast_backend as ast_backend_mod
from mokata.knowledge.ast_backend import AstBackend
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


def no_graph_router():
    """A router whose code_graph resolves to the lexical floor (no real graph tool)."""
    m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
    return Router(m, Detector(overrides={"code-review-graph": False, "serena": False,
                                         "ripgrep": False}))


def graph_router():
    m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
    return Router(m, Detector(overrides={"code-review-graph": True}))


class _FakeGraphClient:
    def __init__(self, rows=None):
        self.rows = rows or {}

    def query(self, kind, target, root, depth=1):
        return list(self.rows.get(kind, []))


def _ast_backend(root):
    """An AstBackend wired with the same grep floor the layer would give it."""
    return AstBackend(root=root, grep=GrepBackend(root=root))


class TestAstBackendEdges(unittest.TestCase):
    """Deliverable 1 + 4 — AST answers every kind with real edges, non-degraded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = write_sample_repo(self.tmp.name)
        self.backend = _ast_backend(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _files(self, r):
        return {ref.path for ref in r.references}

    def _syms(self, r):
        return {ref.symbol for ref in r.references}

    def test_callers_ast(self):
        r = self.backend.query("callers", "compute")
        self.assertFalse(r.degraded)
        # compute() is called in Impl.run (mod_a) and main (mod_b)
        self.assertEqual(self._files(r), {"mod_a.py", "mod_b.py"})
        self.assertEqual(self._syms(r), {"run", "main"})

    def test_callees_ast(self):
        r = self.backend.query("callees", "compute")
        self.assertFalse(r.degraded)
        # compute() calls helper()
        self.assertEqual(self._files(r), {"mod_a.py"})
        self.assertIn("helper", self._syms(r))

    def test_implementers_ast(self):
        r = self.backend.query("implementers", "Base")
        self.assertFalse(r.degraded)
        # Impl (mod_a) and OtherImpl (mod_b) subclass Base
        self.assertEqual(self._files(r), {"mod_a.py", "mod_b.py"})
        self.assertEqual(self._syms(r), {"Impl", "OtherImpl"})

    def test_imports_ast(self):
        r = self.backend.query("imports", "mod_a")
        self.assertFalse(r.degraded)
        # mod_b imports from mod_a
        self.assertEqual(self._files(r), {"mod_b.py"})

    def test_blast_radius_ast(self):
        r = self.backend.query("blast_radius", "helper", depth=2)
        self.assertFalse(r.degraded)
        # helper <- compute <- {run, main} : the transitive caller surface
        self.assertEqual(self._files(r), {"mod_a.py", "mod_b.py"})

    def test_ast_note_and_not_degraded(self):
        r = self.backend.query("callers", "compute")
        self.assertFalse(r.degraded)
        self.assertEqual(r.backend, "ast")
        self.assertIn("AST", r.note)              # names the embedded AST floor
        self.assertIn("name-resolution", r.note)  # documents the limit (not type inference)

    def test_gr_s1_regression(self):
        """The stage's headline: on a Python repo the layer answers structural queries from
        the AST floor with degraded=False — the SAME queries were degraded=True on old code."""
        layer = KnowledgeLayer.from_router(no_graph_router(), root=self.root)
        self.assertFalse(layer.uses_graph)         # AST is a FLOOR, not the adopted graph
        for kind, target in (("callers", "compute"), ("callees", "compute"),
                             ("imports", "mod_a"), ("blast_radius", "helper")):
            res = layer._run(kind, target)
            self.assertFalse(res.degraded, f"{kind} should be answered by the AST floor")
            self.assertTrue(res.references, f"{kind} should have real edges")

    def test_unparseable_py_falls_through_to_grep(self):
        """Deliverable 5 — a .py file ast.parse rejects does not crash the walk; a query whose
        only evidence is the broken file falls through to the grep floor (degraded=True)."""
        with open(os.path.join(self.root, "broken.py"), "w", encoding="utf-8") as fh:
            fh.write("def oops(:\n    zzz_only_here(\n")   # syntax error
        backend = _ast_backend(self.root)
        # the good files still parse and answer non-degraded
        good = backend.query("callers", "compute")
        self.assertFalse(good.degraded)
        # a target that only appears in the unparseable file → per-query grep fallthrough
        r = backend.query("callers", "zzz_only_here")
        self.assertTrue(r.degraded)               # emergency floor answered
        self.assertEqual(r.backend, "grep")


class TestAstSelection(unittest.TestCase):
    """Deliverable 3 + 5 — selection prefers AST over grep when no graph, never over a
    configured graph, and leaves zero-.py repos on the grep floor exactly as today."""

    def test_prefers_ast_when_no_graph_and_python_present(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = KnowledgeLayer.from_router(no_graph_router(), root=d)
            self.assertIsInstance(layer.primary, AstBackend)
            self.assertEqual(layer.backend_name, "ast")
            self.assertFalse(layer.uses_graph)     # still a floor, not the adopted graph

    def test_adopted_graph_never_constructs_ast(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = KnowledgeLayer.from_router(
                graph_router(), root=d, client=_FakeGraphClient({"callers": []}))
            self.assertTrue(layer.uses_graph)
            self.assertNotIsInstance(layer.primary, AstBackend)

    def test_zero_python_repo_is_grep_floor(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("function compute(){ return helper(); }\n")
            layer = KnowledgeLayer.from_router(no_graph_router(), root=d)
            self.assertIsInstance(layer.primary, GrepBackend)
            self.assertEqual(layer.backend_name, "grep")


class TestGrepEmergencyFloor(unittest.TestCase):
    """Deliverable 5 — a non-Python repo gets byte-identical grep behaviour."""

    def test_non_python_repo_byte_identical_to_grep(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("function main(){ return compute(); }\n"
                         "function compute(){ return 1; }\n")
            via_layer = KnowledgeLayer.from_router(no_graph_router(), root=d).callers("compute")
            direct = GrepBackend(root=d, name="grep").query("callers", "compute")
            self.assertEqual(via_layer.to_dict(), direct.to_dict())
            self.assertTrue(via_layer.degraded)


class TestAstIncrementalCache(unittest.TestCase):
    """Deliverable 2 + 7 — an incremental per-file edge cache; unchanged files are never
    re-parsed; a torn cache is a silent full re-parse; the cache holds no source content."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write_sample_repo(self.root)
        self.cache_dir = os.path.join(self.root, "cache")

    def tearDown(self):
        self.tmp.cleanup()

    def _backend(self):
        return AstBackend(root=self.root, grep=GrepBackend(root=self.root),
                          cache_dir=self.cache_dir)

    def test_unchanged_files_not_reparsed(self):
        # first backend parses both files and persists the cache
        with mock.patch.object(ast_backend_mod, "parse_source",
                               wraps=ast_backend_mod.parse_source) as spy1:
            self._backend().query("callers", "compute")
            self.assertEqual(spy1.call_count, 2)     # mod_a.py + mod_b.py

        # touch exactly ONE file (change its content + mtime), keep the other unchanged
        p = os.path.join(self.root, "mod_b.py")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n# touched\n")
        os.utime(p, (os.path.getmtime(p) + 5, os.path.getmtime(p) + 5))

        # a fresh backend reads the disk cache and re-parses ONLY the touched file
        with mock.patch.object(ast_backend_mod, "parse_source",
                               wraps=ast_backend_mod.parse_source) as spy2:
            r = self._backend().query("callers", "compute")
            self.assertEqual(spy2.call_count, 1)     # exactly one re-parse
        self.assertFalse(r.degraded)
        self.assertEqual({ref.path for ref in r.references}, {"mod_a.py", "mod_b.py"})

    def test_torn_cache_is_silent_full_reparse(self):
        self._backend().query("callers", "compute")      # writes the cache
        cache_file = os.path.join(self.cache_dir, "edges.json")
        self.assertTrue(os.path.exists(cache_file))
        # simulate a SIGKILL-torn write: truncated / corrupt JSON
        with open(cache_file, "w", encoding="utf-8") as fh:
            fh.write('{"entries": {"mod_a.py": {"mtime"')     # invalid JSON

        with mock.patch.object(ast_backend_mod, "parse_source",
                               wraps=ast_backend_mod.parse_source) as spy:
            r = self._backend().query("callers", "compute")   # must NOT crash
            self.assertEqual(spy.call_count, 2)               # full re-parse
        self.assertFalse(r.degraded)
        self.assertEqual({ref.path for ref in r.references}, {"mod_a.py", "mod_b.py"})

    def test_edge_cache_stores_no_source(self):
        """Secret-safety — the cache holds symbols / paths / lines only, no source text."""
        self._backend().query("callers", "compute")
        with open(os.path.join(self.cache_dir, "edges.json"), encoding="utf-8") as fh:
            raw = fh.read()
        # symbols are allowed (they ARE the edges); raw source lines are NOT
        self.assertIn("compute", raw)                     # a symbol name
        self.assertNotIn("raise NotImplementedError", raw)   # a source line body
        self.assertNotIn("return helper() + helper()", raw)  # a source line body


if __name__ == "__main__":
    unittest.main()
