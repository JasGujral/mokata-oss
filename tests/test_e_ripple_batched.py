"""0.0.29 E `GRAPH-TS-IMPACT-RIPPLE-74S` + `W5-SQLITE-RO-CHECK-THEN-OPEN` — the impact ripple's cost
does not grow with the number of callers it confirms.

Rings 2..N are ONE batched read of code-review-graph's index per ring (chunked), and the tool is
asked only about the frontier names that have BARE-NAME call edges (`D`: the rows its own resolution
adds, which mokata does not copy). Asserted on a synthetic code-review-graph-shaped index:

  * RoundTrips — 20,000 callers of the hub, depth 3: the tool's `callers` questions are 1 + |D| for
                 |D| = 0 and |D| = 50, and every ring read's statements are bounded by its frontier
                 in chunks, never by its callers;
  * Parity     — the drawn picture equals the per-node picture (the tool asked about every name and
                 answering exactly the proven rows + the bare-name rows) for D = 0 and D != 0,
                 module-level callers included, and no absolute path reaches the page;
  * Paging     — pages add up to the whole picture, Continue resumes mid-ring, nothing read or asked
                 twice;
  * StableRead — a read the index changed under is read again (the picture is the second read's);
                 while code-review-graph BUILDS its index the ring is confirmed name by name, saying so;
  * CancelStops — Cancel inside the D asks and between ring reads stops the job with no further
                 statement on the index;
  * PathB      — (ruling D6) on the last ring the D names are not asked in the walk: their
                 name-matched rows are drawn pending, labelled, and Continue confirms them;
  * RealCrgParity — the REAL code-review-graph on a 3-file TypeScript repo: the batched picture
                 (all pages) equals the per-node picture. Opt-in: MOKATA_TEST_REAL_CRG=1 with
                 code-review-graph installed (the weekly real-crg leg).
"""
import math
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.ide import visuals as V
from mokata.knowledge import cancel, crg_build, crg_nodes
from mokata.knowledge.query import QueryResult, Reference


def _db(root):
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
    conn.execute("CREATE INDEX idx_nodes_symbol ON nodes(symbol)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [("last_updated", "2026-11-13T08:00:00"), ("repo_root", root),
                      ("build_state", "complete")])
    return conn


class Repo:
    """A code-review-graph index built from (caller, callee, rel, line) calls. A callee written
    `"~name"` is a BARE-NAME edge (target_qualified = the plain name); a caller `"file:<rel>"` is a
    module-level call (its source is the File node)."""

    def __init__(self, funcs, calls):
        self.root = tempfile.mkdtemp(prefix="e-rip-")
        conn = _db(self.root)
        self.qn = {}
        rows = []
        for name, rel, is_test in funcs:
            ab = os.path.join(self.root, *rel.split("/"))
            self.qn[name] = f"{ab}::{name}"
            rows.append(("Function", name, f"{ab}::{name}", ab, 1, name, is_test))
        files = {rel for caller, _c, rel, _l in calls if caller.startswith("file:")}
        for rel in files:
            ab = os.path.join(self.root, *rel.split("/"))
            self.qn["file:" + rel] = ab
            rows.append(("File", ab, ab, ab, 1, None, 0))
        conn.executemany("INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, "
                         "symbol, is_test) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        self.calls = calls
        conn.executemany(
            "INSERT INTO edges (kind, source_qualified, target_qualified, file_path, line) VALUES "
            "('CALLS', ?, ?, ?, ?)",
            [(self.qn[c], callee[1:] if callee.startswith("~") else self.qn[callee],
              os.path.join(self.root, *rel.split("/")), line) for c, callee, rel, line in calls])
        conn.commit()
        conn.close()
        self.db = os.path.join(self.root, ".code-review-graph", "graph.db")
        self.index = crg_nodes.NodeReader(self.root, self.db)

    def tool(self, name):
        """What code-review-graph's `callers_of(name)` answers here: the proven rows, then the
        bare-name rows — named as `crg_client` names them."""
        from mokata.knowledge.crg_client import _rel
        from mokata.knowledge.query import floor_symbol
        refs = [Reference(p, ln, "", c) for _t, c, p, ln in self.index.callers_by_name([name])]
        base = name.split(":")[0]      # a crg local name `d:S<hash>:<n>` is name-matched as `d`
        for c, callee, rel, line in self.calls:
            if callee in ("~" + name, "~" + base):
                refs.append(Reference(rel, line, "", floor_symbol(_rel(self.qn[c], self.root))))
        return refs


class _Layer:
    backend_name = "code-review-graph"


def _q(kind, target, refs):
    return QueryResult(kind=kind, target=target, backend="code-review-graph", basis="structural",
                       references=refs)


class _Rig:
    """nav.run answered from the repo (counting the `callers` questions), the index's statements
    counted, probe_index BUILT unless told otherwise."""

    def __init__(self, repo, building=False):
        self.repo = repo
        self.asked = []
        self.statements = 0
        self.notes = []
        self.partials = []
        self.on_ask = None
        real_select = crg_nodes._select

        def select(conn, sql, params=()):
            self.statements += 1
            return real_select(conn, sql, params)

        def run(root, kind, target, depth=2, layer=None, budget=None):
            cancel.checkpoint()
            if kind != "callers":
                return _q(kind, target, [])
            self.asked.append(target)
            if self.on_ask:
                self.on_ask(target)
            return _q(kind, target, repo.tool(target))
        self.patches = [
            mock.patch.object(crg_nodes, "_select", select),
            mock.patch.object(nav, "run", side_effect=run),
            mock.patch.object(crg_build, "probe_index",
                              return_value=crg_build.INDEX_INCOMPLETE if building
                              else crg_build.INDEX_BUILT),
            mock.patch.object(nav, "graph_health", return_value=None)]

    def report(self, **kw):
        if kw.get("note"):
            self.notes.append(kw["note"])
        if kw.get("partial"):
            self.partials.append(kw["partial"])

    def __enter__(self):
        V._RIPPLES.clear()
        for p in self.patches:
            p.start()
        self._rep = cancel.reporting(self.report)
        self._rep.__enter__()
        return self

    def __exit__(self, *exc):
        self._rep.__exit__(*exc)
        for p in self.patches:
            p.stop()


def _hub(n_callers, n_bare, ring3=0):
    """hub <- c0..c{n-1} (+ the first `ring3` of them called by r<i>); `n_bare` of the c<i> are also
    the target of a bare-name edge from b<i>."""
    funcs = [("hub", "lib/hub.go", 0)]
    calls = []
    for i in range(n_callers):
        funcs.append((f"c{i}", f"pkg/p{i % 50}/c{i}.go", 0))
        calls.append((f"c{i}", "hub", f"pkg/p{i % 50}/c{i}.go", 3))
    for i in range(ring3):
        funcs.append((f"r{i}", f"cmd/r{i}.go", 0))
        calls.append((f"r{i}", f"c{i}", f"cmd/r{i}.go", 4))
    for i in range(n_bare):
        funcs.append((f"b{i}", f"ext/b{i}.go", 0))
        calls.append((f"b{i}", f"~c{i}", f"ext/b{i}.go", 5))
    return Repo(funcs, calls)


class RoundTrips(unittest.TestCase):

    def _ripple(self, n_bare):
        repo = _hub(20000, n_bare, ring3=100)
        reads = []
        real = getattr(V, "_ring_read", None)

        def ring_read(index, names, cache=None):
            before = rig.statements
            out = real(index, names, cache)
            reads.append((len(names), len({r[1] for r in out[0]}), rig.statements - before))
            return out
        with _Rig(repo) as rig, mock.patch.object(V, "PAGE_NODES", 100000), \
                mock.patch.object(V, "_ring_read", ring_read, create=True):
            out = V.ripple(repo.root, "hub", 3, layer=_Layer(), index=repo.index)
        return out, rig, reads

    def test_the_tool_is_asked_one_plus_D_times_whatever_the_callers(self):
        for n_bare in (0, 50):
            out, rig, reads = self._ripple(n_bare)
            self.assertEqual(len(rig.asked), 1 + n_bare, n_bare)
            self.assertEqual(rig.asked[0], "hub")
            self.assertEqual(sorted(rig.asked[1:]), sorted(f"c{i}" for i in range(n_bare)))
            self.assertFalse(out["more"])
            self.assertEqual(out["direct_callers"], 20000)
            labels = {n["label"] for n in out["nodes"]}
            self.assertEqual(len([lb for lb in labels if lb.startswith("r")]), 100)
            self.assertEqual(len([lb for lb in labels if lb.startswith("b")]), n_bare)
            for frontier, callers, statements in reads:   # chunked: frontier, then callers' names
                bound = 2 * math.ceil(frontier / crg_nodes.FTS_CHUNK) + \
                    2 * math.ceil(frontier / crg_nodes.SQL_CHUNK) + \
                    math.ceil(callers / crg_nodes.SQL_CHUNK) + 3
                self.assertLessEqual(statements, bound, (frontier, callers, statements))
            self.assertLessEqual(len(reads), 2 * 3)                  # a read per ring, per pass
            self.assertTrue(any(f"confirming {n_bare:,} name(s) matched by name" in n
                                for n in rig.notes), rig.notes[-3:])


def _parity_repo(with_bare):
    funcs = [("Hub", "lib/hub.go", 0), ("A", "pkg/a/a.go", 0), ("B", "pkg/b/b.go", 0),
             ("C", "pkg/c/c.go", 0), ("D", "pkg/d/d.go", 0), ("TestA", "pkg/a/a_test.go", 1),
             ("X", "ext/x.go", 0), ("Y", "ext/y.go", 0)]
    calls = [("A", "Hub", "pkg/a/a.go", 4), ("B", "Hub", "pkg/b/b.go", 5),
             ("C", "A", "pkg/c/c.go", 6), ("C", "A", "pkg/c/c.go", 9),
             ("TestA", "A", "pkg/a/a_test.go", 7), ("D", "B", "pkg/d/d.go", 2),
             ("Y", "C", "ext/y.go", 8), ("file:cmd/main.go", "B", "cmd/main.go", 1)]
    if with_bare:
        calls += [("X", "~A", "ext/x.go", 3), ("X", "~C", "ext/x.go", 11)]
    return Repo(funcs, calls)


def _picture(out):
    nodes = sorted((n["id"], n.get("ring"), n["kind"], n.get("coverage"), n.get("path"),
                    n.get("line")) for n in out["nodes"])
    edges = sorted((e["source"], e["target"], e["kind"], e["basis"], e["site"])
                   for e in out["edges"])
    return nodes, edges, out["direct_callers"], out["untested_chains"], \
        out["untested_chains_total"], out["basis"]


class Parity(unittest.TestCase):

    def test_the_batched_picture_is_the_per_node_picture(self):
        for with_bare in (False, True):
            repo = _parity_repo(with_bare)
            with _Rig(repo) as rig:
                batched = _every_page(repo, "Hub", 3)[-1]     # Path B: Continue confirms
                partials = list(rig.partials)
            with _Rig(repo) as rig2:
                per_node = V.ripple(repo.root, "Hub", 3, layer=object(), index=None)
            self.assertEqual(_picture(batched), _picture(per_node), with_bare)
            labels = {n["label"] for n in batched["nodes"]}
            self.assertEqual("X" in labels, with_bare)
            self.assertIn("go", labels)                    # the module-level caller, as the tool
            self.assertGreater(len(rig2.asked), len(rig.asked))
            for p in partials + [batched]:                  # no absolute path on the page
                for n in p["nodes"]:
                    self.assertNotIn(repo.root, str(n.get("label")) + str(n.get("path")))


class LocalQualifiedNames(unittest.TestCase):
    """The step-0 probe on the TypeScript corpus: a frontier name that is one of code-review-graph's
    local names (`d:Se992793a6f16:14`) has NO index rows, but the tool name-matches it by its
    leading name and answers 563 rows. Such a name is confirmed with the tool, never read alone."""

    def test_a_local_name_on_the_frontier_is_confirmed(self):
        funcs = [("Hub", "lib/hub.ts", 0), ("d:S1:3", "lib/a.ts", 0), ("Z", "ext/z.js", 0)]
        calls = [("d:S1:3", "Hub", "lib/a.ts", 4), ("Z", "~d", "ext/z.js", 9)]
        repo = Repo(funcs, calls)
        with _Rig(repo) as rig:
            batched = _every_page(repo, "Hub", 2)[-1]
        with _Rig(repo):
            per_node = V.ripple(repo.root, "Hub", 2, layer=object(), index=None)
        self.assertIn("d:S1:3", rig.asked)
        self.assertEqual(_picture(batched), _picture(per_node))
        self.assertIn("Z", {n["label"] for n in batched["nodes"]})


def _every_page(repo, symbol, depth, layer=None):
    """Page 1, then Continue until the ripple says there is no more."""
    layer = layer or _Layer()
    pages = [V.ripple(repo.root, symbol, depth, layer=layer, index=repo.index, page=1)]
    while pages[-1]["more"]:
        pages.append(V.ripple(repo.root, symbol, depth, layer=layer, index=repo.index,
                              page=len(pages) + 1))
    return pages


class _Refusing(Repo):
    """A repo whose tool does NOT confirm the name-matched rows from callers in `refused`."""

    refused = ()

    def tool(self, name):
        return [r for r in super().tool(name) if r.symbol not in self.refused]


class PathB(unittest.TestCase):
    """Ruling D6 (step 0: TypeScript still 173.5 s after batching). On the LAST ring the names the
    tool would name-match are not asked during the walk: their name-matched rows are read from the
    index and drawn PENDING, labelled, never as resolved; the page reaches done and Continue asks
    the tool about them and keeps only what it confirms."""

    def _repo(self, refused=()):
        funcs = [("Hub", "lib/hub.ts", 0), ("A", "pkg/a.ts", 0), ("B", "pkg/b.ts", 0),
                 ("C", "pkg/c.ts", 0), ("X", "ext/x.js", 0), ("Q", "ext/q.js", 0),
                 ("TestA", "pkg/a.test.ts", 1)]
        calls = [("A", "Hub", "pkg/a.ts", 4), ("B", "Hub", "pkg/b.ts", 5),
                 ("C", "A", "pkg/c.ts", 6), ("TestA", "A", "pkg/a.test.ts", 7),
                 ("X", "~A", "ext/x.js", 3), ("Q", "~B", "ext/q.js", 8)]
        repo = _Refusing(funcs, calls)
        repo.refused = refused
        return repo

    def test_page_one_asks_only_ring_one_and_draws_the_name_matched_rows_pending(self):
        repo = self._repo()
        with _Rig(repo) as rig:
            first = V.ripple(repo.root, "Hub", 2, layer=_Layer(), index=repo.index)
        self.assertEqual(rig.asked, ["Hub"])
        self.assertTrue(first["more"])
        self.assertIn("2 call(s) matched by name only — not confirmed", first["more_note"])
        byid = {n["label"]: n for n in first["nodes"]}
        for name in ("X", "Q"):
            self.assertTrue(byid[name].get("pending"), name)
        named = [e for e in first["edges"] if e["source"] in (byid["X"]["id"], byid["Q"]["id"])]
        self.assertEqual(len(named), 2)
        for e in named:
            self.assertTrue(e.get("pending"))
            self.assertNotIn(e["basis"], V.STRUCTURAL)        # never shown as resolved
        self.assertNotEqual(first["basis"], "structural")
        for e in first["edges"]:                               # proven rows are confirmed
            if e["source"] in (byid["C"]["id"], byid["TestA"]["id"]):
                self.assertFalse(e.get("pending"), e)

    def test_the_note_counts_the_pending_name_matched_calls_it_draws_not_the_rows_it_read(self):
        """Verifier F2: the note's number is what the picture shows faded — one per pending
        name-matched caller -> callee edge. A second call site of the same pair draws no second
        edge, and a name-matched row whose edge the graph already PROVED draws nothing pending;
        neither may be counted as "matched by name only"."""
        funcs = [("Hub", "lib/hub.ts", 0), ("A", "pkg/a.ts", 0), ("C", "pkg/c.ts", 0),
                 ("X", "ext/x.js", 0)]
        calls = [("A", "Hub", "pkg/a.ts", 4), ("C", "A", "pkg/c.ts", 6),
                 ("X", "~A", "ext/x.js", 3), ("X", "~A", "ext/x.js", 9),      # two sites, one edge
                 ("C", "~A", "pkg/c.ts", 11)]                                  # edge already proved
        repo = _Refusing(funcs, calls)
        with _Rig(repo):
            first = V.ripple(repo.root, "Hub", 2, layer=_Layer(), index=repo.index)
        pending = [e for e in first["edges"] if e.get("pending") and e["basis"] == V.NAME_MATCHED]
        self.assertEqual(1, len(pending), first["edges"])
        self.assertIn("1 call(s) matched by name only — not confirmed", first["more_note"])

    def test_continue_confirms_them_and_the_picture_is_the_per_node_picture(self):
        repo = self._repo()
        with _Rig(repo) as rig:
            pages = _every_page(repo, "Hub", 2)
        with _Rig(repo):
            per_node = V.ripple(repo.root, "Hub", 2, layer=object(), index=None)
        self.assertEqual(len(pages), 2)
        self.assertEqual(sorted(rig.asked), ["A", "B", "Hub"])
        self.assertEqual(_picture(pages[-1]), _picture(per_node))
        self.assertFalse(any(e.get("pending") for e in pages[-1]["edges"]))

    def test_a_name_matched_row_the_tool_does_not_confirm_is_dropped(self):
        repo = self._repo(refused=("Q",))
        with _Rig(repo):
            pages = _every_page(repo, "Hub", 2)
        self.assertIn("Q", {n["label"] for n in pages[0]["nodes"]})
        self.assertNotIn("Q", {n["label"] for n in pages[-1]["nodes"]})
        self.assertIn("X", {n["label"] for n in pages[-1]["nodes"]})

    def test_an_inner_ring_is_still_confirmed_before_the_next_ring_is_read(self):
        """Depth 3: ring 2's name-matched callers seed ring 3, so ring 2's D names are asked in the
        walk; only the last ring's are deferred (disclosed)."""
        repo = self._repo()
        with _Rig(repo) as rig:
            V.ripple(repo.root, "Hub", 3, layer=_Layer(), index=repo.index)
        self.assertEqual(sorted(rig.asked), ["A", "B", "Hub"])


class Paging(unittest.TestCase):

    def test_pages_add_up_and_continue_never_reads_or_asks_again(self):
        repo = _hub(60, 5, ring3=40)
        with _Rig(repo) as rig, mock.patch.object(V, "PAGE_NODES", 100000):
            whole = V.ripple(repo.root, "hub", 3, layer=_Layer(), index=repo.index)
        reads = []
        real = getattr(V, "_ring_read", None)

        def ring_read(index, names, cache=None):
            reads.append(frozenset(names))
            return real(index, names, cache)
        with _Rig(repo) as rig, mock.patch.object(V, "PAGE_NODES", 30), \
                mock.patch.object(V, "_ring_read", ring_read, create=True):
            layer = _Layer()
            pages = [V.ripple(repo.root, "hub", 3, layer=layer, index=repo.index, page=1)]
            while pages[-1]["more"]:
                pages.append(V.ripple(repo.root, "hub", 3, layer=layer, index=repo.index,
                                      page=len(pages) + 1))
        self.assertGreater(len(pages), 3)
        self.assertEqual(_picture(pages[-1])[:2], _picture(whole)[:2])
        self.assertEqual(len(rig.asked), len(set(rig.asked)), "a page asked again")
        self.assertEqual(len(reads), len(set(reads)), "a page read a ring again")


class StableRead(unittest.TestCase):

    def test_a_read_the_index_changed_under_is_read_again(self):
        repo = _parity_repo(False)
        real = crg_nodes.NodeReader.callers_by_name
        state = {"n": 0}

        def torn(self_, names, bare=None, matched=None):
            out = real(self_, names, bare=bare, matched=matched)
            state["n"] += 1
            if state["n"] == 1:                    # code-review-graph writes while we read
                conn = sqlite3.connect(repo.db)
                conn.execute("INSERT INTO edges (kind, source_qualified, target_qualified, "
                             "file_path, line) VALUES ('CALLS', ?, ?, ?, 1)",
                             (repo.qn["Y"], repo.qn["Hub"],
                              os.path.join(repo.root, "ext", "y.go")))
                conn.commit()
                conn.close()
            return out
        with _Rig(repo) as rig, mock.patch.object(crg_nodes.NodeReader, "callers_by_name", torn):
            V._ring_read(repo.index, ["Hub"])
            rows = V._ring_read(repo.index, ["Hub"])[0]
        self.assertEqual(state["n"], 3)                         # torn → read again, then once
        self.assertIn(("Hub", "Y", "ext/y.go", 1), rows)
        self.assertTrue(any("changed while it was read" in n for n in rig.notes))

    def test_while_the_index_is_built_each_name_is_asked_and_the_job_says_why(self):
        repo = _parity_repo(False)
        with _Rig(repo, building=True) as rig:
            out = V.ripple(repo.root, "Hub", 3, layer=_Layer(), index=repo.index)
        self.assertNotIn("index_rings", out)
        self.assertEqual(sorted(rig.asked), sorted(["Hub", "A", "B", "C", "D", "go"]))
        self.assertTrue(any("building its index" in n for n in rig.notes), rig.notes)


class CancelStops(unittest.TestCase):

    def _cancel(self, when):
        repo = _hub(400, 10, ring3=50)
        flag = threading.Event()
        after = {}
        with _Rig(repo) as rig, mock.patch.object(V, "PAGE_NODES", 100000):
            def stop(target=None):
                if not flag.is_set() and when(target, rig):
                    flag.set()
                    after["at"] = rig.statements
            rig.on_ask = stop
            real_report = rig.report

            def report(**kw):
                real_report(**kw)
                stop()
            with cancel.scope(flag), cancel.reporting(report):
                with self.assertRaises(cancel.Cancelled):
                    V.ripple(repo.root, "hub", 3, layer=_Layer(), index=repo.index)
            self.assertIn("at", after)
            self.assertEqual(rig.statements, after["at"], "a statement ran after Cancel")
        return rig

    def test_cancel_inside_the_D_asks(self):
        rig = self._cancel(lambda target, rig: target == "c3")
        self.assertNotIn("c4", rig.asked)

    def test_cancel_between_ring_reads(self):
        self._cancel(lambda target, rig: any(n.startswith("ring 2 of 3:") for n in rig.notes))


class FullDiskSaysToRerunFull(unittest.TestCase):
    """0.0.29 E `IDE-BENCH-GUARD-UNTESTED` (second half) — `graph build --full` refused for want of
    disk names the command the user ran, not "the next update"."""

    def _build(self, full, state):
        root = tempfile.mkdtemp(prefix="e-disk-")
        with mock.patch.object(crg_build, "index_built", return_value=True), \
                mock.patch.object(crg_build, "probe_db", return_value=state), \
                mock.patch.object(crg_build, "update_state", return_value=None), \
                mock.patch.object(crg_build, "_index_bytes", return_value=10 ** 9), \
                mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1)):
            return crg_build.build_now(root, full=full, runner=lambda *a, **k: self.fail("ran"))

    def test_a_full_build_the_user_asked_for(self):
        res = self._build(True, crg_build.INDEX_BUILT)
        self.assertFalse(res.ok)
        self.assertIn("then run `mokata graph build --full` again", res.error)
        self.assertNotIn("next update", res.error)

    def test_an_automatic_repair_still_says_the_next_update(self):
        res = self._build(False, crg_build.INDEX_INCOMPLETE)
        self.assertFalse(res.ok)
        self.assertIn("repaired on the next update", res.error)


_TS = {
    "src/hub.ts": "export function hub(n: number): number { return n + 1; }\n",
    "src/a.ts": "import { hub } from './hub';\nexport function a(): number { return hub(1); }\n"
                "export function a2(): number { return hub(2) + a(); }\n",
    "src/b.ts": "import { a } from './a';\ndeclare const anyObj: any;\n"
                "export function b(): number { return a() + anyObj.a(); }\n"
                "export function b2(): number { return anyObj.hub(3); }\n"
                "export function b3(): number { return b() + b2(); }\n"
                "export function b4(): number { return anyObj.a(); }\n",
}


def _crg_cli():
    import shutil
    import sys
    near = os.path.join(os.path.dirname(sys.executable), "code-review-graph")
    return shutil.which("code-review-graph") or (near if os.path.exists(near) else None)


@unittest.skipUnless(os.environ.get("MOKATA_TEST_REAL_CRG") == "1" and _crg_cli(),
                     "opt-in: MOKATA_TEST_REAL_CRG=1 and code-review-graph installed (weekly leg)")
class RealCrgParity(unittest.TestCase):
    """Row 6: the rows a ring reads from the index plus the tool's answers for D ARE what the tool
    answers name by name — against the real code-review-graph, whose resolution mokata does not
    copy. `anyObj.a()` / `anyObj.hub()` are its bare-name edges (D != 0 on ring 1 and ring 2)."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        from mokata.init import init_repo
        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="e-realcrg-"))
        for rel, text in _TS.items():
            path = os.path.join(cls.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        for a in (["init", "-q"], ["add", "-A"],
                  ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "ts"]):
            subprocess.run(["git", "-C", cls.root, *a], check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
        init_repo(root=cls.root, profile="full", assume_yes=True, out=lambda *_a: None)
        r = subprocess.run([_crg_cli(), "build", "--repo", cls.root], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        if r.returncode:
            raise AssertionError("code-review-graph build failed: " + r.stderr[-400:])

    def setUp(self):
        _support.real_crg(self)
        V._RIPPLES.clear()

    def test_the_batched_picture_is_the_tools_name_by_name_picture(self):
        layer = nav._layer(self.root)
        self.assertEqual("code-review-graph", getattr(layer, "backend_name", None))
        index = crg_nodes.reader(self.root)
        self.assertIsNotNone(index)
        bare = set()
        index.callers_by_name(["hub", "a", "b"], bare=bare)
        self.assertTrue({"hub", "a"} <= bare, bare)                   # D is not empty here
        for depth in (2, 3):
            with self.subTest(depth=depth):
                V._RIPPLES.clear()
                pages = [V.ripple(self.root, "hub", depth, layer=layer, index=index, page=1)]
                while pages[-1]["more"]:
                    pages.append(V.ripple(self.root, "hub", depth, layer=layer, index=index,
                                          page=len(pages) + 1))
                V._RIPPLES.clear()
                with mock.patch.object(V, "_index_for", return_value=None):   # name by name
                    per_node = V.ripple(self.root, "hub", depth, layer=layer, index=None)
                self.assertEqual(_picture(pages[-1])[:5], _picture(per_node)[:5])
                if depth == 2:                       # `a` is in D on the last ring: Continue asked
                    self.assertGreaterEqual(len(pages), 2)
                    self.assertTrue(any(e.get("pending") and e["basis"] == V.NAME_MATCHED
                                        for e in pages[0]["edges"]), pages[0]["edges"])


if __name__ == "__main__":
    unittest.main()
