"""P41 MCP-FOLLOWS-WORKTREE — a code question is answered for the tree the session is EDITING.

Claude Code starts mokata's MCP server once, in the main checkout, and `EnterWorktree` never moves
it; P31 put every session in its own worktree. So before this stage every code answer (defs,
callers, blast radius, …) described the MAIN checkout's files. Business-level, on REAL
`git worktree add` checkouts laid out as Claude Code lays them out:

  * a symbol added only in the worktree is found — by the MCP `query` tool (path = the worktree,
    or the main checkout + the session this MCP serves) and by the IDE focused on that session;
  * a callers / blast-radius query reflects a call added in the worktree;
  * two parallel sessions in two worktrees each see ONLY their own changes;
  * the session cannot be identified → the main checkout's answer, SAID in the note (never another
    session's worktree); a gone worktree → the main checkout, said;
  * an adopted graph (code-review-graph's shape) is overlaid: references into changed files are
    replaced by the worktree's edges; unparseable changed files are lexical and demote the answer;
  * nothing is ever written inside a worktree.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_worktrees as SW
from mokata import tree_scope as TS
from mokata.knowledge.query import (BASIS_STRUCTURAL, GraphBackend, QueryResult, Reference)
from mokata.mcp import tools_read as TR
from mokata.session_ancestry import ProcInfo, record_session

GIT = shutil.which("git")


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def _write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


APP = ("def helper():\n    return 1\n\n\n"
       "def use():\n    return helper()\n")
OTHER = "def other():\n    return 2\n"


def _paths(res):
    return sorted({r["path"] for r in res["references"]})


def _syms(res):
    return sorted({(r["path"], r["symbol"]) for r in res["references"]})


class FakeProcs:
    """A process table: pid -> (ppid, start). Two Claude Code processes (10, 20), each with a hook
    (11 / 21) and an MCP server (12 / 22) under it, all under one terminal (1)."""

    def __init__(self):
        self.t = {1: (0, 1.0), 10: (1, 2.0), 20: (1, 2.0), 11: (10, 3.0), 21: (20, 3.0),
                  12: (10, 3.0), 22: (20, 3.0), 99: (1, 3.0)}
        self.dead = set()

    def __call__(self, pid):
        if pid in self.dead or pid not in self.t:
            return None
        ppid, start = self.t[pid]
        return ProcInfo(ppid, f"tok{pid}", start)


@unittest.skipUnless(GIT, "git is required")
class _Repo(unittest.TestCase):
    """A mokata repo in git, with Claude Code-shaped worktrees."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.main = os.path.realpath(os.path.join(self._t.name, "repo"))
        os.makedirs(self.main)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        from mokata.init import init_repo
        init_repo(root=self.main, profile="standard", assume_yes=True, out=lambda _: None)
        _write(self.main, ".gitignore", ".mokata/\n.claude/worktrees/\n")
        _write(self.main, "src/app.py", APP)
        _write(self.main, "src/other.py", OTHER)
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

    def tearDown(self):
        self._t.cleanup()

    def worktree(self, name):
        path = os.path.join(self.main, ".claude", "worktrees", name)
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-{name}", path, "HEAD")
        return path

    def bind(self, sid, tree, hook_pid, procs):
        """What a session's hooks record: its process ancestry (SessionStart / prompt) and, on
        its first gated tool call inside the worktree, its worktree binding (P31)."""
        self.assertTrue(record_session(self.main, sid, lookup=procs, pid=hook_pid,
                                       now=time.time()))
        procs.dead.add(hook_pid)                 # a hook process exits once it has run
        self.assertIsNotNone(SW.bind(self.main, sid, tree))

    def query(self, path, kind, target, scope=None, depth=2):
        with TS.serving(scope):
            return TR.query(path=path, kind=kind, target=target, depth=depth)


# ===================================================================== (1) the symbol is found
class ASymbolAddedOnlyInTheWorktreeIsFound(_Repo):

    def test_defs_by_worktree_path_and_by_the_session_this_mcp_serves(self):
        wt = self.worktree("mk-a")
        _write(wt, "src/fresh.py", "from app import helper\n\n\ndef brand_new():\n"
                                   "    return helper()\n")
        # control: the main checkout has no such symbol
        main_ans = self.query(self.main, "defs", "brand_new")
        self.assertEqual(main_ans["references"], [])
        # the worktree's path
        ans = self.query(wt, "defs", "brand_new")
        self.assertEqual(_paths(ans), ["src/fresh.py"])
        self.assertIn("graph + 1 file from your worktree", ans["note"])
        self.assertEqual(ans["basis"], BASIS_STRUCTURAL)
        # the main checkout's path, from an MCP process that serves the bound session
        procs = FakeProcs()
        self.bind("S-A", wt, 11, procs)
        scope = TS.scope_for(self.main, lookup=procs, pid=12)
        self.assertEqual(scope.basis, TS.BASIS_SESSION)
        self.assertEqual(os.path.realpath(scope.tree), os.path.realpath(wt))
        ans = self.query(self.main, "defs", "brand_new", scope)
        self.assertEqual(_paths(ans), ["src/fresh.py"])

    def test_a_deleted_file_s_symbols_are_gone_in_the_worktree(self):
        wt = self.worktree("mk-a")
        os.remove(os.path.join(wt, "src", "other.py"))
        self.assertEqual(_paths(self.query(self.main, "defs", "other")), ["src/other.py"])
        self.assertEqual(self.query(wt, "defs", "other")["references"], [])

    def test_a_worktree_of_a_non_mokata_checkout_with_its_own_state_is_its_own_repo(self):
        # the main checkout is NOT a mokata repo; the worktree holds its own `.mokata/` (the shape
        # this very suite runs in): it answers as a repo in its own right, no overlay, no error
        bare = os.path.realpath(os.path.join(self._t.name, "plain"))
        os.makedirs(bare)
        _git(bare, "init", "-q", "-b", "main")
        _git(bare, "config", "user.email", "t@t")
        _git(bare, "config", "user.name", "t")
        _write(bare, "src/app.py", APP)
        _git(bare, "add", "-A")
        _git(bare, "commit", "-qm", "init")
        wt = os.path.join(bare, "wt")
        _git(bare, "worktree", "add", "-q", "-b", "side", wt, "HEAD")
        from mokata.init import init_repo
        init_repo(root=wt, profile="standard", assume_yes=True, out=lambda _: None)
        self.assertIsNone(TS.code_tree_for(wt))
        self.assertEqual(TS.scope_for(wt).basis, TS.BASIS_MAIN)
        ans = self.query(wt, "defs", "helper")
        self.assertEqual(_paths(ans), ["src/app.py"])
        self.assertNotIn("from your worktree", ans["note"])

    def test_nothing_is_written_inside_the_worktree(self):
        wt = self.worktree("mk-a")
        _write(wt, "src/fresh.py", "def brand_new():\n    return 1\n")
        before = sorted(os.listdir(wt))
        for kind in ("defs", "callers", "blast_radius", "refs"):
            self.query(wt, kind, "brand_new")
        self.assertEqual(sorted(os.listdir(wt)), before)
        self.assertFalse(os.path.exists(os.path.join(wt, ".mokata")))


# ===================================================================== (2) callers / impact
class ACallAddedInTheWorktreeIsACaller(_Repo):

    def test_callers_and_blast_radius_follow_the_worktree(self):
        wt = self.worktree("mk-a")
        # committed on the worktree's branch: a new caller of `helper`, and a caller of THAT
        _write(wt, "src/other.py", "from app import helper\n\n\ndef other():\n"
                                   "    return helper()\n\n\ndef top():\n    return other()\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "wt")
        main_callers = self.query(self.main, "callers", "helper")
        self.assertEqual(_syms(main_callers), [("src/app.py", "use")])
        callers = self.query(wt, "callers", "helper")
        self.assertEqual(_syms(callers), [("src/app.py", "use"), ("src/other.py", "other")])
        blast = self.query(wt, "blast_radius", "helper", depth=2)
        self.assertIn(("src/other.py", "top"), _syms(blast))
        self.assertNotIn(("src/other.py", "top"), _syms(self.query(self.main, "blast_radius",
                                                                   "helper", depth=2)))

    def test_a_call_removed_in_the_worktree_is_no_longer_a_caller(self):
        wt = self.worktree("mk-a")
        _write(wt, "src/app.py", "def helper():\n    return 1\n\n\ndef use():\n    return 3\n")
        self.assertEqual(_syms(self.query(self.main, "callers", "helper")),
                         [("src/app.py", "use")])
        ans = self.query(wt, "callers", "helper")
        self.assertEqual(ans["references"], [])
        self.assertTrue(ans["basis"] in ("verified_empty", BASIS_STRUCTURAL))


# ===================================================================== (3) parallel sessions
class TwoParallelSessionsSeeOnlyTheirOwnChanges(_Repo):

    def test_each_mcp_answers_for_its_own_session(self):
        a, b = self.worktree("mk-a"), self.worktree("mk-b")
        _write(a, "src/a_only.py", "from app import helper\n\n\ndef only_a():\n"
                                   "    return helper()\n")
        _write(b, "src/b_only.py", "from app import helper\n\n\ndef only_b():\n"
                                   "    return helper()\n")
        procs = FakeProcs()
        self.bind("S-A", a, 11, procs)
        self.bind("S-B", b, 21, procs)
        sa = TS.scope_for(self.main, lookup=procs, pid=12)
        sb = TS.scope_for(self.main, lookup=procs, pid=22)
        self.assertEqual((sa.session, sb.session), ("S-A", "S-B"))
        self.assertEqual(_paths(self.query(self.main, "defs", "only_a", sa)), ["src/a_only.py"])
        self.assertEqual(self.query(self.main, "defs", "only_b", sa)["references"], [])
        self.assertEqual(_paths(self.query(self.main, "defs", "only_b", sb)), ["src/b_only.py"])
        self.assertEqual(self.query(self.main, "defs", "only_a", sb)["references"], [])
        ca = _syms(self.query(self.main, "callers", "helper", sa))
        cb = _syms(self.query(self.main, "callers", "helper", sb))
        self.assertIn(("src/a_only.py", "only_a"), ca)
        self.assertNotIn(("src/b_only.py", "only_b"), ca)
        self.assertIn(("src/b_only.py", "only_b"), cb)
        self.assertNotIn(("src/a_only.py", "only_a"), cb)


# ===================================================================== (4) ambiguity is said
class AnUnknownSessionGetsTheMainCheckoutAndIsTold(_Repo):

    def test_ambiguous_mapping_answers_main_with_a_note(self):
        a = self.worktree("mk-a")
        _write(a, "src/a_only.py", "def only_a():\n    return 1\n")
        procs = FakeProcs()
        self.bind("S-A", a, 11, procs)
        # an MCP process under NO recorded Claude process (pid 99 is a sibling under the terminal;
        # the terminal itself has more than a hook's launch depth below it, so it never matches)
        scope = TS.scope_for(self.main, lookup=procs, pid=99)
        self.assertEqual(scope.basis, TS.BASIS_AMBIGUOUS)
        self.assertEqual(os.path.realpath(scope.tree), os.path.realpath(self.main))
        self.assertIn("answered for the MAIN checkout", scope.note)
        self.assertEqual(self.query(self.main, "defs", "only_a", scope)["references"], [])

    def test_the_mcp_wrapper_attaches_the_scope_and_the_note(self):
        from mokata.mcp.server import _serve
        a = self.worktree("mk-a")
        _write(a, "src/a_only.py", "def only_a():\n    return 1\n")
        procs = FakeProcs()
        self.bind("S-A", a, 11, procs)
        served = _serve(TR.query, name="query", kind="read")
        amb = TS.scope_for(self.main, lookup=procs, pid=99)
        with mock.patch.object(TS, "scope_for", return_value=amb):
            out = served(path=self.main, kind="defs", target="only_a")
        self.assertEqual(out["references"], [])
        self.assertEqual(out["tree"]["basis"], TS.BASIS_AMBIGUOUS)
        self.assertIn("will not guess", out["note"])
        mine = TS.scope_for(self.main, lookup=procs, pid=12)
        with mock.patch.object(TS, "scope_for", return_value=mine):
            out = served(path=self.main, kind="defs", target="only_a")
        self.assertEqual(_paths(out), ["src/a_only.py"])
        self.assertEqual(out["tree"]["basis"], TS.BASIS_SESSION)
        self.assertEqual(out["tree"]["session"], "S-A")
        # a tool that does not read code is not scoped (no per-call resolution, no field)
        with mock.patch.object(TS, "scope_for", side_effect=AssertionError("not a code tool")):
            out = _serve(TR.rules, name="rules", kind="read")(path=self.main)
        self.assertNotIn("tree", out)

    def test_a_gone_worktree_answers_main_and_says_so(self):
        a = self.worktree("mk-a")
        procs = FakeProcs()
        self.bind("S-A", a, 11, procs)
        _git(self.main, "worktree", "remove", "--force", a)
        scope = TS.scope_for(self.main, lookup=procs, pid=12)
        self.assertEqual(scope.basis, TS.BASIS_GONE)
        self.assertIn("no longer exists", scope.note)

    def test_no_worktrees_and_no_session_is_a_plain_main_answer(self):
        scope = TS.scope_for(self.main, lookup=FakeProcs(), pid=99)
        self.assertEqual((scope.basis, scope.note, scope.reportable), (TS.BASIS_MAIN, "", False))


class EveryCodeToolIsScoped(unittest.TestCase):
    """A tool whose answer is built from the code (it builds the knowledge layer or reads the
    tree's files) is in the wrapper's TREE_TOOLS — a new code tool cannot silently answer for the
    main checkout again."""

    #: Reads the code but is ABOUT the main checkout: the freshness index is the main graph's.
    MAIN_ONLY = {"index_status"}

    def test_the_set_is_derived_from_what_the_bodies_read(self):
        import inspect
        from mokata.mcp.registry import TOOLS
        from mokata.mcp.server import TREE_TOOLS
        reads_code = re.compile(r"KnowledgeLayer|_knowledge_layer|run_ci_check|lat_check")
        need = {t.name for t in TOOLS if reads_code.search(inspect.getsource(t.fn))}
        self.assertTrue({"query", "decompose", "ci_check", "lat_check"} <= need, need)
        self.assertEqual(sorted(need - self.MAIN_ONLY - TREE_TOOLS), [])
        self.assertEqual(sorted(TREE_TOOLS - {t.name for t in TOOLS}), [])


# ===================================================================== (5) an adopted graph
class StubGraph(GraphBackend):
    """code-review-graph's shape, answering from a fixed table of the MAIN checkout's edges."""

    name = "code-review-graph"
    is_graph = True

    def __init__(self, root, callers):
        self.root, self.callers, self.asked = root, callers, []

    def query(self, kind, target, depth=1):
        self.asked.append((kind, target))
        if kind == "callers":
            refs = [Reference(p, ln, "", s) for p, ln, s in self.callers.get(target, [])]
        elif kind == "blast_radius":
            refs, seen, frontier = [], {target}, [target]
            for _ in range(max(1, depth)):
                nxt = []
                for sym in frontier:
                    for p, ln, s in self.callers.get(sym, []):
                        refs.append(Reference(p, ln, "", s))
                        if s not in seen:
                            seen.add(s)
                            nxt.append(s)
                frontier = nxt
        else:
            refs = []
        return QueryResult(kind=kind, target=target, references=refs, backend=self.name,
                           basis=BASIS_STRUCTURAL, note="graph")


class AnAdoptedGraphIsOverlaid(_Repo):

    def layer(self, wt, graph):
        from mokata.knowledge.ast_backend import AstBackend
        from mokata.knowledge.layer import KnowledgeLayer
        from mokata.knowledge.tree_overlay import TreeLayer
        main_layer = KnowledgeLayer(graph, AstBackend(root=self.main))
        return TreeLayer.over(main_layer, wt)

    def test_references_into_changed_files_are_replaced_by_the_worktrees(self):
        wt = self.worktree("mk-a")
        # main graph: helper <- use (app.py) <- main_entry (cli.py, which the worktree rewrites)
        graph = StubGraph(self.main, {"helper": [("src/app.py", 6, "use")],
                                      "use": [("src/cli.py", 2, "main_entry")],
                                      "late": [("src/far.py", 9, "farther")]})
        _write(wt, "src/cli.py", "from app import helper\n\n\ndef new_entry():\n"
                                 "    return helper()\n\n\ndef runner():\n    return late()\n")
        _write(wt, "src/late.py", "def late():\n    return new_entry()\n")
        layer = self.layer(wt, graph)
        res = layer.callers("use")
        self.assertEqual(res.references, [])          # the old cli.py caller is gone
        res = layer.callers("helper")
        got = {(r.path, r.symbol) for r in res.references}
        self.assertEqual(got, {("src/app.py", "use"), ("src/cli.py", "new_entry")})
        self.assertTrue(all(r.metadata.get("worktree") for r in res.references
                            if r.path == "src/cli.py"))
        self.assertIn("graph + 2 files from your worktree", res.note)
        self.assertEqual(res.basis, BASIS_STRUCTURAL)
        # blast radius: through the worktree's new caller, then ASKS THE GRAPH about a symbol the
        # worktree introduced (`late` is defined in the worktree and called from `runner`; the
        # main graph's own callers of `late` — far.py — come back too)
        blast = layer.blast_radius("helper", depth=4)
        syms = {r.symbol for r in blast.references}
        self.assertTrue({"use", "new_entry", "late", "runner", "farther"} <= syms, syms)
        self.assertNotIn("main_entry", syms)
        self.assertIn(("callers", "late"), graph.asked)

    def test_an_unparseable_changed_file_is_lexical_and_demotes_the_answer(self):
        wt = self.worktree("mk-a")
        graph = StubGraph(self.main, {"helper": [("src/app.py", 6, "use")]})
        _write(wt, "svc/server.go", "package svc\n\nfunc Serve() {\n\thelper()\n}\n")
        res = self.layer(wt, graph).callers("helper")
        got = {r.path for r in res.references}
        self.assertEqual(got, {"src/app.py", "svc/server.go"})
        self.assertTrue(res.degraded)
        self.assertIn("lexical", res.note)

    def test_a_worktree_with_no_changes_answers_from_the_graph_and_says_so(self):
        wt = self.worktree("mk-a")
        graph = StubGraph(self.main, {"helper": [("src/app.py", 6, "use")]})
        res = self.layer(wt, graph).callers("helper")
        self.assertEqual({r.path for r in res.references}, {"src/app.py"})
        self.assertIn("has no changes against the main checkout", res.note)


# ===================================================================== (6) the IDE
@unittest.skipUnless(GIT, "git is required")
class TheIdeReadsTheFocusedSessionsWorktree(_Repo):

    def ide(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        self.state = AppState(self.main, "tok", 47998, None, False)
        c = TestClient(build_app(self.state), base_url="http://127.0.0.1:47998",
                       follow_redirects=False)
        c.get("/pair?t=tok")
        return c

    def focus(self, c, sid):
        return c.post("/api/focus", json={"session": sid},
                      headers={"Origin": "http://127.0.0.1:47998"}).json()

    def test_code_view_search_query_and_impact_follow_focus(self):
        try:
            import starlette  # noqa: F401
        except ImportError:
            self.skipTest("the [ide] extra is not installed")
        wt = self.worktree("mk-a")
        _write(wt, "src/fresh.py", "from app import helper\n\n\ndef brand_new():\n"
                                   "    return helper()  # wt-only-marker\n")
        _write(wt, "src/app.py", APP + "\n\ndef added_here():\n    return helper()\n")
        SW.bind(self.main, "S-A", wt)
        c = self.ide()
        # unfocused: the main checkout
        self.assertEqual(c.get("/api/code-tree").json()["worktree"], False)
        self.assertNotIn("wt-only-marker", c.get("/api/file?path=src/fresh.py").text)
        q = c.get("/api/query?kind=defs&target=brand_new").json()
        self.assertEqual(q["references"], [])
        # focused on the worktree session
        self.focus(c, "S-A")
        ct = c.get("/api/code-tree").json()
        self.assertTrue(ct["worktree"])
        self.assertEqual(ct["label"].replace("\\", "/"), ".claude/worktrees/mk-a")
        f = c.get("/api/file?path=src/fresh.py")
        self.assertEqual(f.status_code, 200, f.text)
        self.assertIn("wt-only-marker", f.text)
        s = c.post("/api/search", json={"q": "wt-only-marker"},
                   headers={"Origin": "http://127.0.0.1:47998"}).json()
        self.assertIn("src/fresh.py", str(s))
        q = c.get("/api/query?kind=defs&target=brand_new").json()
        self.assertEqual(_paths(q), ["src/fresh.py"])
        self.assertIn("from your worktree", q["note"])
        callers = c.get("/api/query?kind=callers&target=helper").json()
        self.assertIn(("src/fresh.py", "brand_new"), _syms(callers))
        imp = c.get("/api/impact?path=src/app.py&lines=8-9").json()
        self.assertEqual([s_["name"] for s_ in imp["symbols"]], ["added_here"])
        quick = c.post("/api/quickopen", json={"q": "brand_new"},
                       headers={"Origin": "http://127.0.0.1:47998"}).json()
        self.assertIn("brand_new", str(quick))
        # unfocus: back to the main checkout
        self.focus(c, None)
        self.assertEqual(c.get("/api/query?kind=defs&target=brand_new").json()["references"], [])
        self.assertFalse(os.path.exists(os.path.join(wt, ".mokata")))


if __name__ == "__main__":
    unittest.main()
