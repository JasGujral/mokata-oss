"""0.0.26 P37 `GRAPH-AT-SCALE` — every graph view works on a multi-million-line repo, readable,
and streams (PRD G19, G4 UI; R4, R24, R25).

Asserted (business level — each fails if its behaviour is removed):
  * R4  — with code-review-graph's index, the ripple's rings are read from the index and handed to
          the page as a PARTIAL answer before the tool's first (slow) answer comes back; what no
          answer confirms is taken away at the end; the path is found in the index and confirmed
          hop by hop instead of the breadth-first walk; the index reader returns exactly the
          proven edges and the shortest chain, and checks the job's Cancel.
  * R24 — the page never draws more than 150 nodes: the most relevant, then one cluster per
          directory group with its count; every hidden node is in exactly one cluster.
  * R25 — one basis word per picture (structural / approximate / floor / pending), derived from
          the drawn edges; graph.js's status line, legend and summary all read `data.basis`.
"""
import os
import re
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.ide import visuals as V
from mokata.knowledge import cancel, crg_nodes
from mokata.knowledge.query import QueryResult, Reference

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_JS = os.path.join(HERE, "..", "src", "mokata", "ide", "static", "graph.js")


def _graph_js():
    with open(GRAPH_JS, encoding="utf-8") as fh:
        return fh.read()


def _q(kind, target, refs, basis="structural"):
    return QueryResult(kind=kind, target=target, backend="code-review-graph", basis=basis,
                       references=refs)


# ----------------------------------------------------------------------------- an index fixture
def _index(root, nodes, edges):
    """A built code-review-graph DB with the columns mokata reads. nodes: (name, rel, line,
    is_test); qualified name = <abs>::<name>. edges: (src name, dst name or bare, rel, line)."""
    d = os.path.join(root, ".code-review-graph")
    os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(os.path.join(d, "graph.db"))
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, "
                 "qualified_name TEXT UNIQUE, file_path TEXT, line_start INTEGER, line_end INTEGER, "
                 "language TEXT, parent_name TEXT, file_hash TEXT, symbol TEXT, is_test INTEGER)")
    conn.execute("CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, "
                 "source_qualified TEXT, target_qualified TEXT, file_path TEXT, line INTEGER)")
    conn.execute("CREATE INDEX idx_edges_target_kind ON edges(target_qualified, kind)")
    conn.execute("CREATE INDEX idx_edges_source_kind ON edges(source_qualified, kind)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("last_updated", "2026-10-22T08:00:00"), ("repo_root", root),
                      ("build_state", "complete")])
    qn = {}
    for name, rel, line, is_test in nodes:
        ab = os.path.join(root, *rel.split("/"))
        qn[(name, rel)] = f"{ab}::{name}"
        conn.execute("INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, "
                     "language, symbol, is_test) VALUES ('Function', ?, ?, ?, ?, 'go', ?, ?)",
                     (name, f"{ab}::{name}", ab, line, name, is_test))
    by_name = {}
    for (name, rel), q in qn.items():
        by_name.setdefault(name, []).append(q)
    for src, dst, rel, line in edges:
        ab = os.path.join(root, *rel.split("/"))
        for s in by_name[src]:
            targets = by_name.get(dst) or [dst]        # an unknown name stays a bare target
            for t in targets:
                conn.execute("INSERT INTO edges (kind, source_qualified, target_qualified, "
                             "file_path, line) VALUES ('CALLS', ?, ?, ?, ?)", (s, t, ab, line))
    conn.commit()
    conn.close()
    return crg_nodes.NodeReader(root, os.path.join(d, "graph.db"))


NODES = [("Hub", "lib/hub.go", 3, 0), ("Hub", "lib/other/hub.go", 9, 0),
         ("A", "pkg/a/a.go", 1, 0), ("B", "pkg/b/b.go", 1, 0), ("C", "pkg/c/c.go", 1, 0),
         ("TestA", "pkg/a/a_test.go", 1, 1), ("Root", "cmd/root.go", 1, 0)]
EDGES = [("A", "Hub", "pkg/a/a.go", 4), ("B", "Hub", "pkg/b/b.go", 5),
         ("C", "A", "pkg/c/c.go", 6), ("TestA", "A", "pkg/a/a_test.go", 7),
         ("Root", "C", "cmd/root.go", 2), ("Root", "Unresolved", "cmd/root.go", 3)]


class R4TheIndexReaderAnswersInOneRead(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p37-idx-")
        self.rd = _index(self.root, NODES, EDGES)

    def test_R4_callers_by_name_are_the_proven_edges_into_every_definition_of_the_name(self):
        rows = sorted(self.rd.callers_by_name(["Hub"]))
        # both definitions named Hub are targets; every row is (callee, caller, site, line)
        self.assertEqual(rows, [("Hub", "A", "pkg/a/a.go", 4), ("Hub", "A", "pkg/a/a.go", 4),
                                ("Hub", "B", "pkg/b/b.go", 5), ("Hub", "B", "pkg/b/b.go", 5)])
        self.assertEqual(sorted({r[1] for r in self.rd.callers_by_name(["A"])}), ["C", "TestA"])

    def test_R4_call_path_is_the_shortest_chain_with_the_site_of_each_call(self):
        self.assertEqual(self.rd.call_path("Root", "Hub", 5),
                         [("Root", None, 0), ("C", "cmd/root.go", 2), ("A", "pkg/c/c.go", 6),
                          ("Hub", "pkg/a/a.go", 4)])
        self.assertIsNone(self.rd.call_path("Root", "Hub", 2))        # 3 hops do not fit in 2
        self.assertEqual(self.rd.call_path("Root", "Unresolved", 1)[-1][0], "Unresolved")

    def test_R4_a_cancelled_job_stops_the_index_read(self):
        flag = threading.Event()
        flag.set()
        with cancel.scope(flag):
            with self.assertRaises(cancel.Cancelled):
                self.rd.callers_by_name(["Hub"])


class _SlowLayer:
    backend_name = "code-review-graph"


class R4TheRingsAreDrawnBeforeTheToolAnswers(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p37-rip-")
        self.rd = _index(self.root, NODES, EDGES)
        self.events = []

    def _run(self, root, kind, target, depth=2, layer=None, budget=None):
        # what the tool answers: the index's edges into `target` minus B (an edit removed it)
        self.events.append(("answer", kind, target))
        if kind == "callers":
            refs = [Reference(p, ln, "", c) for _t, c, p, ln in self.rd.callers_by_name([target])
                    if c != "B"]
            return _q(kind, target, refs)
        if kind == "defs":
            return _q(kind, target, [Reference("lib/hub.go", 3, "", target)])
        if kind == "callees":
            out = {"Root": [Reference("cmd/root.go", 2, "", "C")],
                   "C": [Reference("pkg/c/c.go", 6, "", "A")],
                   "A": [Reference("pkg/a/a.go", 4, "", "Hub")]}
            return _q(kind, target, out.get(target, []))
        return _q(kind, target, [])

    def _report(self, **kw):
        if kw.get("partial"):
            self.events.append(("partial", kw["partial"]))

    def test_R4_ring_one_is_a_partial_answer_before_the_first_tool_answer(self):
        with mock.patch.object(nav, "run", side_effect=self._run), \
                cancel.reporting(self._report):
            out = V.ripple(self.root, "Hub", 2, layer=_SlowLayer(), index=self.rd)
        first_answer = next(i for i, e in enumerate(self.events) if e[0] == "answer")
        early = [e[1] for e in self.events[:first_answer] if e[0] == "partial"]
        drawn = [{n["label"] for n in p["nodes"]} for p in early]
        self.assertTrue(any({"A", "B"} <= d for d in drawn), drawn)        # ring 1 from the index
        ring1 = next(p for p in early if "A" in {n["label"] for n in p["nodes"]})
        self.assertTrue(ring1["partial"])
        self.assertTrue(all(n.get("pending") for n in ring1["nodes"] if n["kind"] != "center"))
        # the final answer: what the tool confirmed, nothing pending, B (unconfirmed) taken away
        labels = {n["label"] for n in out["nodes"]}
        self.assertIn("A", labels)
        self.assertIn("C", labels)
        self.assertNotIn("B", labels)
        self.assertFalse([n for n in out["nodes"] if n.get("pending")])
        self.assertFalse([e for e in out["edges"] if e.get("pending")])
        self.assertEqual(out["index_rings"], 2)
        self.assertEqual(out["basis"], "structural")

    def test_R4_without_the_index_the_ripple_is_the_answers_alone(self):
        with mock.patch.object(nav, "run", side_effect=self._run), \
                cancel.reporting(self._report):
            out = V.ripple(self.root, "Hub", 2, layer=object(), index=None)
        self.assertNotIn("index_rings", out)
        self.assertEqual(self.events[1][0], "answer")    # 0 = the empty "asking" partial

    def test_R4_focus_draws_the_proven_callers_first(self):
        with mock.patch.object(nav, "run", side_effect=self._run), \
                cancel.reporting(self._report):
            out = V.focus(self.root, "Hub", layer=_SlowLayer(), index=self.rd)
        first_answer = next(i for i, e in enumerate(self.events) if e[0] == "answer")
        self.assertTrue(any("B" in {n["label"] for n in e[1]["nodes"]}
                            for e in self.events[:first_answer] if e[0] == "partial"))
        self.assertNotIn("B", {n["label"] for n in out["nodes"]})

    def test_R4_the_path_is_found_in_the_index_and_confirmed_hop_by_hop(self):
        with mock.patch.object(nav, "run", side_effect=self._run), \
                cancel.reporting(self._report):
            out = V.path(self.root, "Root", "Hub", 5, layer=_SlowLayer(), index=self.rd)
        self.assertTrue(out["found"])
        self.assertTrue(out["index_path"])
        self.assertEqual([n["label"] for n in out["nodes"]], ["Root", "C", "A", "Hub"])
        asked = [e[2] for e in self.events if e[0] == "answer"]
        self.assertEqual(asked, ["Root", "C", "A"])               # 3 hops, not a walk
        partials = [e[1] for e in self.events if e[0] == "partial"]
        self.assertTrue(partials and partials[0]["partial"] and partials[0]["found"])

    def test_R4_an_unconfirmed_index_path_falls_back_to_the_walk(self):
        def no_c(root, kind, target, depth=2, layer=None, budget=None):
            if kind == "callees" and target == "C":
                return _q(kind, target, [])
            return self._run(root, kind, target, depth, layer, budget)
        with mock.patch.object(nav, "run", side_effect=no_c):
            out = V.path(self.root, "Root", "Hub", 5, layer=_SlowLayer(), index=self.rd)
        self.assertFalse(out["found"])
        self.assertNotIn("index_path", out)


# ----------------------------------------------------------------------------- R24
def _many(n_dirs=60, per=20):
    nodes = [{"id": "sym:X", "label": "X", "kind": "center", "ring": 0}]
    edges = []
    for d in range(n_dirs):
        for i in range(per):
            nid = f"sym:f{d}_{i}"
            nodes.append({"id": nid, "label": f"f{d}_{i}", "kind": "caller",
                          "ring": 1 if i == 0 else 2, "path": f"pkg/area{d % 7}/sub{d}/f.go"})
            edges.append({"id": f"e{len(edges)}", "source": nid, "target": "sym:X",
                          "kind": "CALLS", "basis": "structural", "site": "x:1"})
    return nodes, edges


class R24NeverMoreThan150Nodes(unittest.TestCase):

    def test_R24_a_1201_node_picture_draws_at_most_150_with_clusters_holding_the_rest(self):
        nodes, edges = _many()
        plan = V.draw_plan(nodes, edges)
        self.assertEqual(plan["total"], 1201)
        self.assertLessEqual(len(plan["shown"]) + len(plan["clusters"]), V.DRAW_LIMIT)
        self.assertEqual(V.DRAW_LIMIT, 150)
        hidden = [m for c in plan["clusters"] for m in c["members"]]
        self.assertEqual(len(hidden), len(set(hidden)))
        self.assertEqual(set(hidden) | set(plan["shown"]), {n["id"] for n in nodes})
        self.assertFalse(set(hidden) & set(plan["shown"]))
        for c in plan["clusters"]:
            self.assertEqual(c["count"], len(c["members"]))
            self.assertIn(str(c["count"]), c["label"])
        # relevance: the centre and every ring-1 node are drawn before any ring-2 node
        self.assertEqual(plan["shown"][0], "sym:X")
        ring = {n["id"]: n["ring"] for n in nodes}
        self.assertTrue(all(ring[i] == 1 for i in plan["shown"][1:61]))

    def test_R24_groups_split_the_crowded_directories_within_forty(self):
        nodes, edges = _many()
        plan = V.draw_plan(nodes, edges)
        self.assertLessEqual(len(plan["clusters"]), V.DRAW_CLUSTERS)
        path = {n["id"]: n.get("path", "") for n in nodes}
        for c in plan["clusters"]:                # every member lives under its group's directory
            for m in c["members"]:
                self.assertTrue(path[m].startswith(c["dir"] + "/"), (c["dir"], path[m]))
        dirs = {c["dir"] for c in plan["clusters"]}
        self.assertTrue(any(d.count("/") == 2 for d in dirs), dirs)   # a crowded area was split
        self.assertGreater(len(dirs), 7)                              # finer than the 7 areas

    def test_R24_a_picture_that_fits_is_drawn_whole(self):
        nodes, edges = _many(n_dirs=5, per=10)
        plan = V.draw_plan(nodes, edges)
        self.assertEqual(plan["clusters"], [])
        self.assertEqual(len(plan["shown"]), 51)

    def test_R24_every_ripple_payload_carries_the_plan(self):
        refs = [Reference(f"pkg/d{i % 30}/f.go", i, "", f"caller{i}") for i in range(400)]

        def run(root, kind, target, depth=2, layer=None, budget=None):
            return _q(kind, target, refs if (kind == "callers" and target == "Hub") else [])
        with mock.patch.object(nav, "run", side_effect=run):
            out = V.ripple(tempfile.mkdtemp(), "Hub", 1, layer=object())
        self.assertEqual(len(out["nodes"]), 401)
        self.assertLessEqual(len(out["draw"]["shown"]) + len(out["draw"]["clusters"]), 150)

    def test_R24_graph_js_draws_the_plan_and_keeps_labels_readable(self):
        src = _graph_js()
        self.assertIn("data.draw", src)                       # the server's plan is what is drawn
        self.assertRegex(src, r"\"font-size\": 11\b")
        self.assertRegex(src, r"zoom\(\) < 1\)[\s\S]{0,120}zoom\(1\)")   # never fit below 1:1


# ----------------------------------------------------------------------------- no hard cap
class NoHardCapAPageNeverAStop(unittest.TestCase):
    """U10 overruled (Jas): a big picture is PAGED — "showing N affected — Continue" resumes the
    same walk where it stopped; it never re-asks what an earlier page asked."""

    def setUp(self):
        V._RIPPLES.clear()
        # hub <- c0..c39 ; each c<i> <- d<i>a, d<i>b  (121 nodes in all)
        self.asked = []

        def run(root, kind, target, depth=2, layer=None, budget=None):
            self.asked.append((kind, target))
            if kind != "callers":
                return _q(kind, target, [])
            if target == "hub":
                refs = [Reference(f"pkg/c{i}.go", i + 1, "", f"c{i}") for i in range(40)]
            elif target.startswith("c"):
                refs = [Reference(f"pkg/d/{target}.go", 1, "", f"d{target}{x}") for x in "ab"]
            else:
                refs = []
            return _q(kind, target, refs)
        self.run = run
        self.layer = object()

    def test_the_pages_add_up_to_the_whole_picture_and_never_re_ask(self):
        with mock.patch.object(nav, "run", side_effect=self.run), \
                mock.patch.object(V, "PAGE_NODES", 50):
            pages = [V.ripple("/r", "hub", 2, layer=self.layer, page=1)]
            first_asked = list(self.asked)
            while pages[-1]["more"]:
                self.assertIn("Continue", pages[-1]["more_note"])
                pages.append(V.ripple("/r", "hub", 2, layer=self.layer, page=len(pages) + 1))
            later = self.asked[len(first_asked):]
        self.assertEqual(3, len(pages))                        # 121 nodes, 50 per page
        self.assertEqual(121, len(pages[-1]["nodes"]))
        self.assertFalse(pages[-1]["more"])
        callers = [t for k, t in self.asked if k == "callers"]
        self.assertEqual(len(callers), len(set(callers)), "a page asked again")
        self.assertFalse(set(t for k, t in later if k == "callers") &
                         set(t for k, t in first_asked if k == "callers"))
        for p in pages:                                         # the canvas is still ≤150
            self.assertLessEqual(len(p["draw"]["shown"]) + len(p["draw"]["clusters"]),
                                 V.DRAW_LIMIT)

    def test_a_page_whose_walk_was_forgotten_is_walked_again_up_to_it(self):
        with mock.patch.object(nav, "run", side_effect=self.run), \
                mock.patch.object(V, "PAGE_NODES", 50):
            V.ripple("/r", "hub", 2, layer=self.layer, page=1)
            V._RIPPLES.clear()                                  # the server restarted, say
            two = V.ripple("/r", "hub", 2, layer=self.layer, page=2)
        self.assertEqual(100, len(two["nodes"]))
        self.assertTrue(two["more"])

    def test_focus_pages_its_callers(self):
        with mock.patch.object(nav, "run", side_effect=self.run), \
                mock.patch.object(V, "PAGE_NODES", 10):
            one = V.focus("/r", "hub", layer=self.layer, index=None)
            two = V.focus("/r", "hub", layer=self.layer, index=None, page=2)
        self.assertTrue(one["more"])
        self.assertIn("of 40 callers", one["more_note"])
        self.assertGreater(len(two["nodes"]), len(one["nodes"]))


# ----------------------------------------------------------------------------- R25
class R25OneBasisWord(unittest.TestCase):

    def test_R25_the_word_follows_the_drawn_edges(self):
        self.assertEqual(V.basis_word({"structural": 3}, ["structural"]), "structural")
        self.assertEqual(V.basis_word({"lexical": 3}, ["lexical"]), "floor")
        self.assertEqual(V.basis_word({"structural": 2, "lexical": 1}, []), "approximate")
        self.assertEqual(V.basis_word({"verified_empty": 1}, []), "structural")
        self.assertEqual(V.basis_word({}, ["structural", "lexical"]), "floor")
        self.assertEqual(V.basis_word({}, [], partial=True), "pending")

    def test_R25_a_lexical_ripple_says_floor(self):
        def run(root, kind, target, depth=2, layer=None, budget=None):
            refs = [Reference("a.py", 3, "", "caller")] if (kind == "callers" and
                                                            target == "Hub") else []
            return QueryResult(kind=kind, target=target, backend="grep", basis="lexical",
                               references=refs)
        with mock.patch.object(nav, "run", side_effect=run):
            out = V.ripple(tempfile.mkdtemp(), "Hub", 1, layer=object())
        self.assertEqual(out["basis"], "floor")

    def test_R25_status_line_legend_and_summary_read_the_one_field(self):
        src = _graph_js()
        self.assertIn("function basisWord(data) { return (data && data.basis)", src)
        status = src[src.index("  function status(data) {"):src.index("  function clearChrome")]
        self.assertIn('"basis: " + word', status)                         # the status line
        self.assertIn('lg.replaceChildren(el("b", "", "basis: " + word))', status)   # the legend
        self.assertIn("Basis: ${basisWord(data)}.", src)                  # the summary
        self.assertNotIn("/api/caps", src)                                # no second query


# ----------------------------------------------------------------------------- R39 (P37 fix)
def _governed_index(state="complete", last="2026-10-22T08:00:00"):
    """A repo with `.mokata/` and a code-review-graph index whose marker is `state`."""
    root = tempfile.mkdtemp(prefix="p37-r39-")
    os.makedirs(os.path.join(root, ".mokata"))
    d = os.path.join(root, ".code-review-graph")
    os.makedirs(d)
    _meta(os.path.join(d, "graph.db"), state, last)
    return root


def _meta(path, state, last):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                     [("build_state", state), ("last_updated", last)])
    conn.commit()
    conn.close()


def _dead_pid():
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class R39AnInterruptedUpdateNeverDemotesACompleteGraph(unittest.TestCase):

    def _killed_update(self, root):
        """What an IDE closed mid-`update` leaves: the attempt recorded, code-review-graph's
        marker `in-progress`, and the process that ran it gone."""
        from mokata.knowledge import crg_build as B
        self.assertTrue(B._begin_update(root))
        _meta(B.db_path(root), "in-progress", "2026-10-22T08:00:00")
        rec = B._read_json(B._update_path(root))
        rec["pid"] = _dead_pid()
        B._write_json(B._update_path(root), rec)
        return B

    def test_R39_a_killed_update_leaves_the_index_BUILT_and_says_so(self):
        root = _governed_index()
        B = self._killed_update(root)
        self.assertEqual(B.probe_db(B.db_path(root)), B.INDEX_INCOMPLETE)   # the tool's marker
        self.assertEqual(B.probe_index(root), B.INDEX_BUILT)                # still the user's graph
        self.assertEqual(B.update_state(root), B.UPDATE_INTERRUPTED)
        self.assertIn("did not finish", B.update_note(root))
        self.assertEqual(crg_nodes.reader(root).db, B.db_path(root))     # the views still read it

    def test_R39_a_running_update_is_updating(self):
        from mokata.knowledge import crg_build as B
        root = _governed_index()
        self.assertTrue(B._begin_update(root))
        _meta(B.db_path(root), "in-progress", "2026-10-22T08:00:00")
        self.assertEqual(B.update_state(root), B.UPDATING)
        self.assertIn("updating", B.update_note(root))

    def test_R39_a_full_build_that_died_is_not_vouched_for(self):
        from mokata.knowledge import crg_build as B
        root = _governed_index(state="in-progress")          # no attempt recorded
        self.assertEqual(B.probe_index(root), B.INDEX_INCOMPLETE)
        root2 = _governed_index()
        self._killed_update(root2)
        _meta(B.db_path(root2), "in-progress", "2026-10-22T09:99:99")   # not the state it began from
        self.assertEqual(B.probe_index(root2), B.INDEX_INCOMPLETE)

    def test_R39_an_interrupted_update_is_redone_INCREMENTALLY_beside_the_graph(self):
        root = _governed_index()
        B = self._killed_update(root)
        calls = []

        def runner(r, on_progress=None, incremental=False, data_dir=None):
            calls.append((incremental, data_dir))
            self.assertEqual(B.probe_index(r), B.INDEX_BUILT)     # the old graph serves meanwhile
            copy = os.path.join(data_dir, "graph.db")
            # the COPY carries the complete marker the update began from: `update` there is
            # incremental, not code-review-graph's full rebuild of an `in-progress` graph
            self.assertEqual(B.probe_db(copy), B.INDEX_BUILT)
            self.assertEqual(B.probe_db(B.db_path(r)), B.INDEX_INCOMPLETE)   # in use: untouched
            _meta(copy, "complete", "2026-10-22T10:00:00")
            return B.BuildResult(ok=True)
        res = B.build_now(root, runner=runner)
        self.assertTrue(res.ok, res.error)
        self.assertEqual([(True, B.next_dir(root))], calls)       # incremental, beside
        self.assertEqual(B.REPAIR_INCREMENTAL, res.repair)
        self.assertIn("redone incrementally", res.detail)
        self.assertEqual(B.probe_db(B.db_path(root)), B.INDEX_BUILT)
        self.assertEqual(B._last_updated(B.db_path(root)), "2026-10-22T10:00:00")
        self.assertFalse(os.path.exists(B._update_path(root)))
        self.assertFalse(os.path.exists(os.path.join(B.next_dir(root), "graph.db")))

    def test_R39_a_copy_that_fails_its_integrity_check_is_rebuilt_in_full_and_says_why(self):
        root = _governed_index()
        B = self._killed_update(root)
        calls = []

        def runner(r, on_progress=None, incremental=False, data_dir=None):
            calls.append(incremental)
            _meta(os.path.join(data_dir, "graph.db"), "complete", "2026-10-22T10:00:00")
            return B.BuildResult(ok=True)
        with mock.patch.object(B, "_seed_beside", return_value="the graph failed its integrity "
                               "check (page 7 is never used)"):
            res = B.build_now(root, runner=runner)
        self.assertTrue(res.ok)
        self.assertEqual([False], calls)
        self.assertEqual(B.REPAIR_FULL, res.repair)
        self.assertIn("integrity check", res.detail)

    def test_R39_too_little_free_disk_refuses_the_build_and_keeps_the_graph(self):
        import shutil
        root = _governed_index()
        B = self._killed_update(root)
        ran = []
        tiny = shutil.disk_usage(root)._replace(free=10)
        with mock.patch("shutil.disk_usage", return_value=tiny):
            res = B.build_now(root, runner=lambda *a, **k: ran.append(1))
        self.assertFalse(res.ok)
        self.assertEqual([], ran)
        self.assertIn("not enough free disk", res.error)
        self.assertEqual(B.INDEX_BUILT, B.probe_index(root))          # still the user's graph
        self.assertFalse(os.path.exists(os.path.join(B.next_dir(root), "graph.db")))

    def test_R39_the_seed_copies_checks_and_restores_only_the_copy(self):
        root = _governed_index()
        B = self._killed_update(root)
        self.assertEqual("", B._seed_beside(root))
        copy = os.path.join(B.next_dir(root), "graph.db")
        self.assertEqual(B.INDEX_BUILT, B.probe_db(copy))
        self.assertEqual(B.INDEX_INCOMPLETE, B.probe_db(B.db_path(root)))
        self.assertNotEqual("", _bad_seed(B, root))           # not a database: refused

    def test_R39_a_copy_whose_integrity_check_fails_is_refused_and_not_marked_complete(self):
        from mokata.memory import _sqlite
        root = _governed_index()
        B = self._killed_update(root)
        real = _sqlite.connect_sqlite

        class Conn:
            def __init__(self, c):
                self.c = c

            def execute(self, sql, *a):
                if sql.startswith("PRAGMA quick_check"):
                    return mock.Mock(fetchone=lambda: ("Page 7: never used",))
                return self.c.execute(sql, *a)

            def __getattr__(self, name):
                return getattr(self.c, name)
        with mock.patch.object(_sqlite, "connect_sqlite", lambda *a, **k: Conn(real(*a, **k))):
            why = B._seed_beside(root)
        self.assertIn("integrity check (Page 7: never used)", why)
        self.assertEqual(B.INDEX_INCOMPLETE,
                         B.probe_db(os.path.join(B.next_dir(root), "graph.db")))


    def test_R39_an_unrecorded_interrupted_index_is_also_repaired_beside_it(self):
        from mokata.knowledge import crg_build as B
        root = _governed_index(state="in-progress")          # killed before this fix, say
        seen = []

        def runner(r, on_progress=None, incremental=False, data_dir=None):
            seen.append((incremental, data_dir))
            _meta(os.path.join(data_dir, "graph.db"), "complete", "2026-10-22T10:00:00")
            return B.BuildResult(ok=True)
        self.assertTrue(B.build_now(root, runner=runner).ok)
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0][1])
        self.assertEqual(B.probe_index(root), B.INDEX_BUILT)

    def test_R39_a_finished_update_clears_its_record_a_failed_one_keeps_the_graph(self):
        from mokata.knowledge import crg_build as B
        root = _governed_index()

        def ok(r, on_progress=None, incremental=False, data_dir=None):
            self.assertTrue(incremental)
            self.assertTrue(os.path.exists(B._update_path(r)))      # recorded BEFORE it ran
            _meta(B.db_path(r), "complete", "2026-10-22T09:00:00")
            return B.BuildResult(ok=True)
        self.assertTrue(B.build_now(root, runner=ok).ok)
        self.assertFalse(os.path.exists(B._update_path(root)))

        def fails(r, on_progress=None, incremental=False, data_dir=None):
            _meta(B.db_path(r), "in-progress", "2026-10-22T09:00:00")
            return B.BuildResult(ok=False, error="boom")
        self.assertFalse(B.build_now(root, runner=fails).ok)
        self.assertEqual(B.probe_index(root), B.INDEX_BUILT)
        self.assertEqual(B.update_state(root), B.UPDATE_INTERRUPTED)

    def test_R39_the_freshness_update_has_no_time_limit_and_runs_under_the_lock(self):
        from mokata.knowledge import crg_build as B
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        root = _governed_index()
        c = CodeReviewGraphClient(root=root)
        self.assertTrue(c.can_build_index)
        with mock.patch.object(B, "build_now", return_value=B.BuildResult(ok=True)) as bn, \
                mock.patch.object(c, "_run_cli", side_effect=AssertionError("timed CLI update")):
            self.assertTrue(c.refresh(root, full=False))
        bn.assert_called_once_with(root)

    def test_R39_a_dead_writers_wal_does_not_block_the_repair_a_live_one_does(self):
        from mokata.knowledge import crg_build as B
        root = _governed_index()
        os.makedirs(B.next_dir(root))
        _meta(os.path.join(B.next_dir(root), "graph.db"), "complete", "2026-10-22T10:00:00")
        wal = B.db_path(root) + "-wal"
        with open(wal, "wb") as fh:
            fh.write(b"x" * 64)
        self.assertIn("WAL", B.swap_in(root))                   # a live writer: refused
        old = os.path.getmtime(wal) - B.WRITER_ACTIVE_S - 5
        os.utime(wal, (old, old))
        self.assertEqual(B.swap_in(root), "")                  # a dead writer's: swapped
        self.assertEqual(B._last_updated(B.db_path(root)), "2026-10-22T10:00:00")
        # with the build's start time: a WAL written DURING the build is a live writer's …
        os.makedirs(B.next_dir(root), exist_ok=True)
        _meta(os.path.join(B.next_dir(root), "graph.db"), "complete", "2026-10-22T11:00:00")
        with open(wal, "wb") as fh:
            fh.write(b"x" * 64)
        self.assertIn("WAL", B.swap_in(root, since=os.path.getmtime(wal) - 1))
        # … one untouched since the build began (a killed update's, copied by the repair) is not
        _meta(os.path.join(B.next_dir(root), "graph.db"), "complete", "2026-10-22T11:00:00")
        self.assertEqual("", B.swap_in(root, since=os.path.getmtime(wal) + 1))
        self.assertEqual(B._last_updated(B.db_path(root)), "2026-10-22T11:00:00")


def _bad_seed(B, root):
    """`_seed_beside` over an index whose bytes are not a database."""
    with open(B.db_path(root), "r+b") as fh:
        fh.seek(0)
        fh.write(b"not a database" * 64)
    return B._seed_beside(root)


class R39TheReadPathReconcileIsAJob(unittest.TestCase):

    def test_R39_a_slow_reconcile_runs_as_a_job_and_the_query_answers_meanwhile(self):
        from mokata.ide import jobs as J
        from mokata.knowledge.freshness import FreshnessOutcome
        gate = threading.Event()

        class Slow:
            root = tempfile.mkdtemp()

            def ensure_fresh(self, _layer):
                gate.wait(10)
                return FreshnessOutcome(fresh=False, rebuilt=True, changed=["a.go"])
        reg = J.JobRegistry()
        J.install(reg)
        try:
            w = nav._OncePerWindow(Slow())
            import time as _t
            t0 = _t.monotonic()
            out = w.ensure_fresh(object())
            self.assertLess(_t.monotonic() - t0, 2.0)
            self.assertIn("updating", out.note)
            self.assertFalse(out.answer_from_floor)
            job = reg.find("index.fresh", {"root": os.path.abspath(Slow.root)})
            self.assertIsNotNone(job)
            self.assertIn(job.snapshot()["state"], ("queued", "running"))
            gate.set()
            job.finished.wait(5)
            self.assertTrue(w.ensure_fresh(object()).fresh is False)   # the job's outcome, cached
        finally:
            gate.set()
            J.install(None)
            reg.close()

    def test_R39_without_a_job_registry_the_reconcile_is_inline_as_before(self):
        from mokata.knowledge.freshness import FreshnessOutcome
        inner = mock.Mock()
        inner.ensure_fresh.return_value = FreshnessOutcome(fresh=False, rebuilt=True)
        out = nav._OncePerWindow(inner).ensure_fresh(object())
        self.assertTrue(out.rebuilt)


if __name__ == "__main__":
    unittest.main()
