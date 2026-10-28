"""0.0.26 P35 `NAV-AT-SCALE` — code navigation on multi-million-line repos.

Business-level tests, one class per PRD row (R14, R15, R19–R23, R37, R38; G33). Each fails if the
behaviour it names is removed. The big-repo numbers live in the P35 report; these tests pin the
behaviour on small fixtures that reproduce the measured shapes (a Go repo, an ambiguous name, a
68k-file tree, a pathological TypeScript file).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path; the A31 interpreter guard)


def _tmp(prefix: str) -> str:
    d = tempfile.mkdtemp(prefix=prefix)
    return d


def _write(root: str, rel: str, text: str) -> str:
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


class R18_APathologicalTsFileIsNeverARaw500(unittest.TestCase):
    """Audit A #4/#5: a RecursionError in the TS parse killed the ⌘P symbol build (restarting from
    zero forever) and 500'd meaning search. The walk is iterative (O13); nav guards the seam so a
    recursion anywhere under it is an outline note / an unparsed file, never a raise."""

    def test_R18_outline_of_a_file_whose_parse_recurses_is_a_note(self):
        from mokata.ide import nav
        from mokata.knowledge import ts_edges as TS

        def boom(_src, _path):
            raise RecursionError("maximum recursion depth exceeded")
        with mock.patch.object(TS, "available", lambda: True), \
                mock.patch.object(TS, "parse_source", boom):
            out = nav.outline("deep.ts", "export function f() {}\n")
        self.assertEqual([], out["symbols"])
        self.assertIn("does not parse", out["note"])

    def test_R18_the_symbol_walk_survives_a_file_whose_parse_recurses(self):
        from mokata.ide import nav
        from mokata.knowledge import ts_edges as TS
        root = _tmp("p35-rec-")
        self.addCleanup(shutil.rmtree, root, True)
        p = _write(root, "src/deep.ts", "export function f() {}\n")

        def boom(_src, _path):
            raise RecursionError("maximum recursion depth exceeded")
        with mock.patch.object(TS, "available", lambda: True), \
                mock.patch.object(TS, "parse_source", boom):
            self.assertEqual([], nav._defs_of(p, "src/deep.ts"))

    def test_R18_a_deeply_nested_ts_file_outlines(self):
        from mokata.ide import nav
        from mokata.knowledge import ts_edges as TS
        if not TS.available():
            self.skipTest("graph-ts extra not installed here")
        src = "export function top() {\n  return " + "f(" * 3000 + "1" + ")" * 3000 + ";\n}\n"
        out = nav.outline("deep.ts", src)
        self.assertEqual(["top"], [s["name"] for s in out["symbols"]])


class R14_TheSymbolBuildNeverRestartsFromZero(unittest.TestCase):
    """Audit A #4: `self.walked` was saved only at the end, so a build that died re-read all
    13,167 files on every restart."""

    def test_R14_a_cancelled_walk_resumes_from_the_files_it_already_read(self):
        from mokata.ide import nav
        from mokata.knowledge.cancel import Cancelled
        root = _tmp("p35-resume-")
        self.addCleanup(shutil.rmtree, root, True)
        for i in range(10):
            _write(root, f"pkg/m{i:02d}.py", f"def f{i}():\n    return {i}\n")
        sx = nav._Symbols(root)
        read = []
        real = nav._defs_of

        def counting(ab, rel):
            if len(read) == 6 and not getattr(counting, "resumed", False):
                raise Cancelled("cancelled")
            read.append(rel)
            return real(ab, rel)
        with mock.patch.object(nav, "_defs_of", counting):
            with self.assertRaises(Cancelled):
                sx._walk()
            self.assertEqual(6, len(read))
            counting.resumed = True
            read.clear()
            rows = sx._walk()
        # only the 4 files the first walk never reached are read again
        self.assertEqual(4, len(read))
        self.assertEqual(10, len(rows))


class TheAutoBuildNeverDeletesAWorkingGraph(unittest.TestCase):
    """Measured 2026-10-22 on kubernetes (P35): another process ran `code-review-graph update`
    on a COMPLETE 2.8 GB index; the update marked it `in-progress`, a query saw "incomplete", the
    automatic build failed on the database lock, and its cleanup (`discard_partial`) deleted
    graph.db. An index that ever completed, or one another process is writing, is never removed."""

    def setUp(self):
        from mokata.knowledge import crg_build as B
        self.B = B
        self.root = _tmp("p35-discard-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, ".mokata"))

    def _complete_graph_with_a_live_update(self):
        import sqlite3
        B = self.B
        os.makedirs(B.index_dir(self.root), exist_ok=True)
        conn = sqlite3.connect(B.db_path(self.root))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO metadata VALUES ('last_updated', '2026-10-21T19:06:04')")
        conn.execute("INSERT INTO metadata VALUES ('build_state', 'complete')")
        conn.executemany("INSERT INTO nodes (name) VALUES (?)", [(f"n{i}",) for i in range(50)])
        conn.commit()
        # the concurrent `update`: marks the graph in-progress, then holds the write lock
        conn.execute("UPDATE metadata SET value='in-progress' WHERE key='build_state'")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO nodes (name) VALUES ('mid-update')")
        self.addCleanup(conn.close)
        return conn

    def test_a_failed_auto_build_during_a_concurrent_update_keeps_graph_db(self):
        from test_o13_crg_auto_build import _FakeProc
        B = self.B
        self._complete_graph_with_a_live_update()
        self.assertEqual(B.INDEX_INCOMPLETE, B.probe_index(self.root))
        res = B.run_build(self.root, command="crg", popen=lambda *a, **k: _FakeProc(
            ["Error: another process is updating this graph (database is locked)."], rc=1))
        self.assertFalse(res.ok)
        self.assertTrue(os.path.exists(B.db_path(self.root)), "a working graph was deleted")
        self.assertFalse(B.discard_partial(self.root))
        self.assertTrue(os.path.exists(B.db_path(self.root)))

    def test_an_index_that_ever_completed_is_updated_not_rebuilt_in_place(self):
        from test_o13_crg_auto_build import _FakeProc
        B = self.B
        conn = self._complete_graph_with_a_live_update()
        conn.rollback()                      # the update died; its marker stays in-progress
        argv, dirs = [], []

        def popen(cmd, **k):
            argv.append(cmd[1])
            dirs.append((k.get("env") or {}).get("CRG_DATA_DIR"))
            return _FakeProc(["Error: locked"], rc=1)
        B.build_now(self.root, runner=lambda root, **k: B.run_build(
            root, command="crg", popen=popen, **k))
        # 0.0.26 P37 (R39): code-review-graph's `update` over an `in-progress` graph IS a full
        # rebuild in place (`build_was_interrupted`); the repair is now that full build BESIDE the
        # index in use (CRG_DATA_DIR = the side directory), swapped in only when complete.
        self.assertEqual(["build"], argv)
        self.assertEqual([os.path.abspath(B.next_dir(self.root))], dirs)
        self.assertTrue(os.path.exists(B.db_path(self.root)))

    def test_a_first_build_that_never_finished_is_still_discarded(self):
        import sqlite3
        B = self.B
        os.makedirs(B.index_dir(self.root), exist_ok=True)
        conn = sqlite3.connect(B.db_path(self.root))
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO metadata VALUES ('build_state', 'in-progress')")
        conn.commit()
        conn.close()
        self.assertTrue(B.discard_partial(self.root))
        self.assertFalse(os.path.exists(B.db_path(self.root)))


# ----------------------------------------------------------------------------- a code-review-graph
def _crg_index(root: str, nodes, complete: bool = True) -> None:
    """A BUILT code-review-graph index with the 2.3.9 node-table columns mokata reads. `nodes`:
    (kind, name, rel, start, end, parent, language). File nodes carry the sha256 of the file."""
    import hashlib
    import sqlite3
    d = os.path.join(root, ".code-review-graph")
    os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(os.path.join(d, "graph.db"))
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, "
                 "qualified_name TEXT UNIQUE, file_path TEXT, line_start INTEGER, line_end INTEGER, "
                 "language TEXT, parent_name TEXT, file_hash TEXT, symbol TEXT)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("last_updated", "2026-10-22T08:00:00"), ("repo_root", root),
                      ("build_state", "complete" if complete else "in-progress")])
    files = sorted({n[2] for n in nodes})
    for rel in files:
        ab = os.path.join(root, *rel.split("/"))
        try:
            with open(ab, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            digest = ""
        conn.execute("INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, "
                     "language, file_hash) VALUES ('File', ?, ?, ?, 1, ?, ?)",
                     (ab, ab, ab, nodes[0][6], digest))
    for kind, name, rel, start, end, parent, lang in nodes:
        ab = os.path.join(root, *rel.split("/"))
        sym = f"{parent}.{name}" if parent else name
        conn.execute("INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, "
                     "line_end, language, parent_name, symbol) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (kind, name, f"{ab}::{sym}", ab, start, end, lang, parent, sym))
    conn.commit()
    conn.close()


_GO = ("package kubelet\n\ntype Kubelet struct {\n\tname string\n}\n\n"
       "func NewMainKubelet(name string) *Kubelet {\n\treturn &Kubelet{name: name}\n}\n\n"
       "func (kl *Kubelet) Run() {\n\tutilruntime.HandleError(nil)\n}\n")
_GO_NODES = [("Class", "Kubelet", "pkg/kubelet/kubelet.go", 3, 5, None, "go"),
             ("Function", "NewMainKubelet", "pkg/kubelet/kubelet.go", 7, 9, None, "go"),
             ("Function", "Run", "pkg/kubelet/kubelet.go", 11, 13, "Kubelet", "go")]


def _go_repo(with_graph: bool = True) -> str:
    root = _tmp("p35-go-")
    _write(root, "pkg/kubelet/kubelet.go", _GO)
    for i in range(6):
        _write(root, f"pkg/util/u{i}.go", f"package util\n\nfunc U{i}() {{}}\n")
    _write(root, "staging/runtime/runtime.go",
           "package runtime\n\nfunc HandleError(err error) {\n}\n")
    if with_graph:
        _crg_index(root, _GO_NODES + [
            ("Function", "HandleError", "staging/runtime/runtime.go", 3, 4, None, "go"),
            ("Function", f"U0", "pkg/util/u0.go", 3, 3, None, "go")])
    return root


def _fresh_nav(root):
    from mokata.ide import nav
    nav._PER_ROOT.clear()
    nav._DIR_INDEX.clear()
    return nav


class R20_GoAndTsOutlinesComeFromTheCodeGraph(unittest.TestCase):
    """Audit A #9 / B-F9: a Go file said "no outline for this file type" although
    code-review-graph held 150k Go definitions."""

    def setUp(self):
        self.root = _go_repo()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_R20_a_go_file_is_outlined_from_the_node_table_with_ends_and_nesting(self):
        nav = _fresh_nav(self.root)
        out = nav.outline("pkg/kubelet/kubelet.go", _GO, self.root)
        self.assertEqual("code-review-graph", out["backend"])
        self.assertEqual("structural", out["basis"])
        self.assertEqual("", out["note"])
        got = [(s["name"], s["kind"], s["line"], s["end"], s["depth"]) for s in out["symbols"]]
        self.assertEqual([("Kubelet", "class", 3, 5, 0), ("NewMainKubelet", "function", 7, 9, 0),
                          ("Run", "method", 11, 13, 1)], got)

    def test_R20_an_outline_older_than_the_file_says_so(self):
        nav = _fresh_nav(self.root)
        _write(self.root, "pkg/kubelet/kubelet.go", "// edited\n" + _GO)
        out = nav.outline("pkg/kubelet/kubelet.go", "// edited\n" + _GO, self.root)
        self.assertEqual("code-review-graph", out["backend"])
        self.assertIn("older version", out["note"])

    def test_G33_a_file_too_large_to_read_is_outlined_from_the_graph(self):
        nav = _fresh_nav(self.root)
        out = nav.outline("pkg/kubelet/kubelet.go", None, self.root)
        self.assertEqual(3, len(out["symbols"]))

    def test_G33_too_large_with_no_graph_says_how_large(self):
        root = _go_repo(with_graph=False)
        self.addCleanup(shutil.rmtree, root, True)
        nav = _fresh_nav(root)
        with mock.patch.object(nav, "_read_cap", lambda: 10):
            out = nav.outline("pkg/kubelet/kubelet.go", None, root)
        self.assertRegex(out["note"], r"^too large to outline \(0\.0 MB\)")

    def test_G7_a_language_nothing_indexes_is_stated(self):
        root = _go_repo(with_graph=False)
        self.addCleanup(shutil.rmtree, root, True)
        nav = _fresh_nav(root)
        out = nav.outline("pkg/kubelet/kubelet.go", _GO, root)
        self.assertEqual([], out["symbols"])
        self.assertTrue(out["note"].startswith("no index for Go"), out["note"])


class R14_SymbolsComeFromTheCodeGraphForEveryLanguage(unittest.TestCase):
    """B-F9: ⌘P `@NewMainKubelet` on kubernetes answered `ready`, 0 symbols, no note."""

    def test_R14_a_go_symbol_is_found_from_the_node_table(self):
        root = _go_repo()
        self.addCleanup(shutil.rmtree, root, True)
        nav = _fresh_nav(root)
        out = nav.quick_open(root, "@NewMainKubelet")
        nav.quick_open(root, "@NewMainKubelet")
        out = nav.quick_open(root, "@NewMainKubelet")
        self.assertEqual("ready", out["symbols_state"])
        top = out["symbols"][0]
        self.assertEqual(("NewMainKubelet", "pkg/kubelet/kubelet.go", 7, "code-review-graph"),
                         (top["name"], top["path"], top["line"], top["backend"]))
        self.assertNotIn("symbols_note", out)

    def test_R14_a_language_with_no_symbol_index_is_stated(self):
        root = _go_repo(with_graph=False)
        self.addCleanup(shutil.rmtree, root, True)
        nav = _fresh_nav(root)
        nav.quick_open(root, "@NewMainKubelet")
        out = nav.quick_open(root, "@NewMainKubelet")
        self.assertEqual([], out["symbols"])
        self.assertEqual(["Go"], out["symbols_unindexed"])
        self.assertIn("no symbol index for Go", out["symbols_note"])

    def test_R14_a_failed_build_is_a_state_not_loading_for_ever(self):
        root = _go_repo(with_graph=False)
        self.addCleanup(shutil.rmtree, root, True)
        nav = _fresh_nav(root)

        def boom(self_):
            raise OSError("disk went away")
        with mock.patch.object(nav._Symbols, "_walk", boom), \
                mock.patch.object(nav, "_layer", side_effect=nav.MokataError("no layer")):
            nav.quick_open(root, "@x")
            out = nav.quick_open(root, "@x")
        self.assertEqual("failed", out["symbols_state"])
        self.assertIn("disk went away", out["symbols_note"])


class R21_DefinitionsComeFromTheNodeTable(unittest.TestCase):
    """A #1: CRG exposes no `defs` op, so every go-to-definition walked the grep floor (13–17 s
    on kubernetes)."""

    def test_R21_defs_is_structural_from_the_graph(self):
        from mokata.knowledge import crg_nodes
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend
        root = _go_repo()
        self.addCleanup(shutil.rmtree, root, True)
        client = CodeReviewGraphClient(call_tool=lambda _t, _p: {}, nodes=crg_nodes.reader)
        self.assertTrue(client.supports_kind("defs"))
        res = CodeReviewGraphBackend("code-review-graph", root, client=client).query(
            "defs", "NewMainKubelet")
        self.assertEqual("structural", res.basis)
        self.assertEqual([("pkg/kubelet/kubelet.go", 7)],
                         [(r.path, r.line) for r in res.references])

    def test_R21_a_name_the_graph_does_not_hold_falls_to_the_floor(self):
        from mokata.knowledge import crg_nodes
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        from mokata.knowledge.query import GraphNotIndexed
        root = _go_repo()
        self.addCleanup(shutil.rmtree, root, True)
        client = CodeReviewGraphClient(call_tool=lambda _t, _p: {}, nodes=crg_nodes.reader)
        with self.assertRaises(GraphNotIndexed):
            client.query("defs", "NoSuchThing", root=root)


class R37_AnAmbiguousNameIsAnsweredOverItsExactDefinitions(unittest.TestCase):
    """A #2 + P35: `HandleError` matched 18 nodes; CRG's candidate list is a fuzzy search (capped
    at 20) that also lists `HandleErrorWithContext`. The answer is the union over the EXACT
    definitions, labelled."""

    def _client(self, root, nodes):
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        asked = []

        def call_tool(_tool, params):
            t = params["target"]
            asked.append(t)
            if t == "HandleError":
                return {"status": "ambiguous", "candidates": [
                    {"qualified_name": os.path.join(root, "a.go") + "::HandleError", "name": "HandleError"},
                    {"qualified_name": os.path.join(root, "b.go") + "::HandleError", "name": "HandleError"},
                    {"qualified_name": os.path.join(root, "c.go") + "::HandleErrorWithContext",
                     "name": "HandleErrorWithContext"}]}
            return {"status": "ok", "results": [
                {"qualified_name": os.path.join(root, "x.go") + "::caller_of_" + t[-12:],
                 "file_path": os.path.join(root, "x.go"), "line_start": 3}]}
        return CodeReviewGraphClient(call_tool=call_tool, nodes=nodes), asked

    def test_R37_callers_merge_the_exact_candidates_and_say_how_many(self):
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend
        root = _tmp("p35-amb-")
        self.addCleanup(shutil.rmtree, root, True)
        client, asked = self._client(root, None)
        res = CodeReviewGraphBackend("code-review-graph", root, client=client).query(
            "callers", "HandleError")
        self.assertEqual("structural", res.basis)
        self.assertEqual(2, res.definitions)
        self.assertNotIn(os.path.join(root, "c.go") + "::HandleErrorWithContext", asked)
        self.assertEqual(["a.go::HandleError", "b.go::HandleError"],
                         [c["qualified_name"] for c in res.to_dict()["candidates"]])
        self.assertIn("2 definitions named `HandleError`", res.note)

    def test_R37_the_node_table_supplies_every_exact_definition(self):
        from mokata.knowledge import crg_nodes
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend
        root = _go_repo()
        self.addCleanup(shutil.rmtree, root, True)
        client, asked = self._client(root, crg_nodes.reader)
        res = CodeReviewGraphBackend("code-review-graph", root, client=client).query(
            "callers", "HandleError")
        # the node table holds ONE HandleError (staging/runtime) — not the tool's fuzzy three
        self.assertEqual(None, res.definitions)
        self.assertIn(os.path.join(root, "staging", "runtime", "runtime.go") + "::HandleError",
                      asked)


class R38_TheGoFloorCountsPackageQualifiedCalls(unittest.TestCase):
    """A #7: the floor's regex excluded `pkg.X(` — kubernetes `HandleError`: 3 callers found,
    501 call sites exist."""

    def test_R38_go_package_qualified_calls_are_callers(self):
        from mokata.knowledge.grep_backend import GrepBackend
        root = _tmp("p35-gofloor-")
        self.addCleanup(shutil.rmtree, root, True)
        _write(root, "runtime/runtime.go", "package runtime\n\nfunc HandleError(err error) {\n}\n")
        for i in range(5):
            _write(root, f"pkg/c{i}.go", f"package pkg\n\nfunc C{i}() {{\n"
                   f"\tutilruntime.HandleError(nil)\n}}\n")
        _write(root, "pkg/local.go", "package runtime\n\nfunc L() {\n\tHandleError(nil)\n}\n")
        _write(root, "pkg/other.go", "package pkg\n\nfunc O() {\n\tMyHandleError(nil)\n}\n")
        res = GrepBackend(root=root).query("callers", "HandleError")
        self.assertEqual(6, res.count, [(r.path, r.line) for r in res.references])

    def test_R38_python_keeps_excluding_obj_method(self):
        from mokata.knowledge.grep_backend import GrepBackend
        root = _tmp("p35-pyfloor-")
        self.addCleanup(shutil.rmtree, root, True)
        _write(root, "m.py", "def run():\n    pass\n\nrun()\nobj.run()\n")
        res = GrepBackend(root=root).query("callers", "run")
        self.assertEqual([4], [r.line for r in res.references])

    def test_R38_java_rust_and_ts_qualifiers_count(self):
        from mokata.knowledge.grep_backend import GrepBackend
        root = _tmp("p35-qual-")
        self.addCleanup(shutil.rmtree, root, True)
        _write(root, "A.java", "class A {\n  void x() {\n    Util.go();\n  }\n}\n")
        _write(root, "a.rs", "fn x() {\n    util::go();\n}\n")
        _write(root, "a.ts", "function x() {\n  ns.go();\n}\n")
        res = GrepBackend(root=root).query("callers", "go")
        self.assertEqual(["A.java", "a.rs", "a.ts"], sorted(r.path for r in res.references))


class R19_TheExplorerIsLazyAndFiltersServerSide(unittest.TestCase):
    """B-F9: TypeScript's 67,887 files were sent as 50,000 (4.2 MB, `truncated`) and filtered in
    the page, so 17.9k files could not be found from Files."""

    def setUp(self):
        import json as _json
        self.json = _json
        self.root = _tmp("p35-tree-")
        self.addCleanup(shutil.rmtree, self.root, True)
        files = []
        for top in range(12):
            for sub in range(20):
                for f in range(290):
                    files.append(f"tests/cases/t{top:02d}/s{sub:02d}/case_{f:04d}.ts")
        files.append("src/compiler/checker.ts")
        self.files = sorted(files)                     # 69,601 paths
        from mokata.ide import nav
        self.nav = _fresh_nav(self.root)
        p = mock.patch.object(nav, "files_of", lambda _root: self.files)
        p.start()
        self.addCleanup(p.stop)

    def _size(self, out):
        return len(self.json.dumps(out).encode("utf-8"))

    def test_R19_a_directory_answers_its_children_with_counts(self):
        out = self.nav.tree(self.root)
        self.assertEqual(len(self.files), out["count"])
        self.assertEqual([("dir", "src", 1), ("dir", "tests", len(self.files) - 1)],
                         [(e["kind"], e["path"], e["files"]) for e in out["entries"]])
        self.assertLess(self._size(out), 300 * 1024)

    def test_R19_every_call_stays_under_300_kb_and_paging_loses_nothing(self):
        biggest = 0
        seen = []
        stack = [""]
        while stack:
            d = stack.pop()
            offset = 0
            while offset is not None:
                out = self.nav.tree(self.root, d, "", offset)
                biggest = max(biggest, self._size(out))
                for e in out["entries"]:
                    (stack.append(e["path"]) if e["kind"] == "dir" else seen.append(e["path"]))
                offset = out["next"]
        self.assertLess(biggest, 300 * 1024)
        self.assertEqual(sorted(self.files), sorted(seen), "a file the explorer cannot reach")

    def test_R19_the_filter_searches_every_path_basename_hits_first(self):
        out = self.nav.tree(self.root, "", "checker.ts")
        self.assertEqual(1, out["total"])
        self.assertEqual("src/compiler/checker.ts", out["entries"][0]["path"])
        many = self.nav.tree(self.root, "", "case_0001")
        self.assertEqual(240, many["total"])
        self.assertIsNone(many["next"])
        wide = self.nav.tree(self.root, "", "tests/cases")
        self.assertEqual(len(self.files) - 1, wide["total"])
        self.assertEqual(len(wide["entries"]), wide["next"])
        self.assertLess(self._size(wide), 300 * 1024)

    def test_R19_long_paths_page_by_bytes_not_only_by_count(self):
        """Measured on TypeScript: 2,000 entries of `tsc/testdata/tests/cases/compiler/…` came to
        361 KB; the page is cut by its byte budget too."""
        deep = "tsc/testdata/tests/cases/compiler/" + "x" * 120
        self.files = sorted(f"{deep}/case_{i:05d}_with_a_long_descriptive_name.ts" for i in range(7000))
        self.nav._DIR_INDEX.clear()
        out = self.nav.tree(self.root, deep)
        self.assertLess(self._size(out), 300 * 1024)
        self.assertIsNotNone(out["next"])


if __name__ == "__main__":
    unittest.main()
