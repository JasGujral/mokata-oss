"""Stage 35f — Neo4j graph adapter + live graph-proximity memory tier.

Both jsonschema states (no jsonschema is imported here — these exercise the knowledge +
memory layers, which are dependency-free, so behaviour is identical ABSENT/PRESENT).

No live Neo4j in CI, so the adapter is proven two ways:
  - DEGRADE paths: no env / no driver / unreachable DB ⇒ `build_neo4j_client` returns None and
    `select_backends` falls back to the grep floor (queries still answer).
  - LIVE path with a DOUBLE: a fake neo4j driver drives `Neo4jGraphClient.query` so the Cypher
    row → typed-row mapping is covered, and a fake `GraphQueryClient` stands in as an in-process
    graph so the auto-wired memory graph tier fuses by default (and stays silent on the floor).

MANUAL VERIFICATION (named live gap): with `pip install neo4j`, a reachable Neo4j, the
conventional schema populated (`(:Symbol {name,path,line})` + `[:CALLS]/[:IMPLEMENTS]/
[:IMPORTS]`), and `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD` exported, run
`mokata query callers <sym>` and confirm it answers via the graph (not the grep floor), then
`mokata index` reports `code graph 'neo4j' wired`. Exercised live only where a DB exists.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import (
    CodeReviewGraphBackend,
    GrepBackend,
    KnowledgeLayer,
    Neo4jGraphClient,
    build_neo4j_client,
    make_graph_scorer,
)
from mokata.knowledge.layer import GRAPH_TOOLS, select_backends
from mokata.memory import DECISION, MemoryItem, MemoryStore, SQLiteBackend


# ----------------------------------------------------------------- doubles

class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, **params):
        self.last = (cypher, params)
        return list(self._rows)


class _FakeDriver:
    """Stands in for a neo4j driver so the live query path is covered without a DB."""

    def __init__(self, rows, fail_connect=False):
        self._rows = rows
        self._fail = fail_connect
        self.closed = False

    def session(self, database=None):
        return _FakeSession(self._rows)

    def verify_connectivity(self):
        if self._fail:
            raise RuntimeError("connection refused")

    def close(self):
        self.closed = True


def _fake_neo4j_module(driver):
    mod = types.ModuleType("neo4j")

    class _GDB:
        @staticmethod
        def driver(uri, auth=None):
            return driver

    mod.GraphDatabase = _GDB
    return mod


class _FakeGraphClient:
    """In-process `GraphQueryClient`: a tiny graph where only `known` symbols have callers."""

    def __init__(self, known):
        self.known = set(known)

    def query(self, kind, target, root, depth=1):
        if target in self.known:
            return [{"path": "pay.py", "line": 7, "symbol": target, "snippet": ""}]
        return []


class _Res:
    def __init__(self, tool, available=True):
        self.tool = tool
        self.available = available


class _Manifest:
    def __init__(self, cfg=None):
        self._cfg = cfg or {}

    def tool_config(self, name):
        return dict(self._cfg)


class _Router:
    def __init__(self, tool, cfg=None):
        self._tool = tool
        self.manifest = _Manifest(cfg)

    def resolve(self, cap):
        return _Res(self._tool)


# ----------------------------------------------------------------- Neo4jGraphClient (live path via double)

class TestNeo4jGraphClient(unittest.TestCase):
    def test_callers_maps_rows_to_typed_shape(self):
        rows = [{"symbol": "main", "path": "app.py", "line": 3}]
        client = Neo4jGraphClient(_FakeDriver(rows))
        out = client.query("callers", "run", root=".")
        self.assertEqual(out, [{"path": "app.py", "line": 3,
                                "symbol": "main", "snippet": "main"}])

    def test_blast_radius_inlines_depth_safely(self):
        drv = _FakeDriver([{"symbol": "x", "path": "x.py", "line": 1}])
        out = Neo4jGraphClient(drv).query("blast_radius", "core", root=".", depth=3)
        self.assertEqual(out[0]["symbol"], "x")

    def test_unknown_kind_raises(self):
        client = Neo4jGraphClient(_FakeDriver([]))
        with self.assertRaises(ValueError):
            client.query("nonsense", "t", root=".")

    def test_close_is_safe(self):
        drv = _FakeDriver([])
        Neo4jGraphClient(drv).close()
        self.assertTrue(drv.closed)


# ----------------------------------------------------------------- build_neo4j_client (degrade paths)

class TestBuildNeo4jClient(unittest.TestCase):
    def test_no_uri_env_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(build_neo4j_client({}))

    def test_driver_absent_returns_none(self):
        # setting the module to None in sys.modules makes `import neo4j` raise ImportError.
        with mock.patch.dict(os.environ, {"NEO4J_URI": "bolt://x"}), \
                mock.patch.dict(sys.modules, {"neo4j": None}):
            self.assertIsNone(build_neo4j_client({}))

    def test_unreachable_db_returns_none(self):
        fake = _fake_neo4j_module(_FakeDriver([], fail_connect=True))
        with mock.patch.dict(os.environ, {"NEO4J_URI": "bolt://x"}), \
                mock.patch.dict(sys.modules, {"neo4j": fake}):
            self.assertIsNone(build_neo4j_client({}))

    def test_reachable_db_builds_client(self):
        fake = _fake_neo4j_module(_FakeDriver([]))
        with mock.patch.dict(os.environ, {"NEO4J_URI": "bolt://x",
                                          "NEO4J_USERNAME": "u", "NEO4J_PASSWORD": "p"}), \
                mock.patch.dict(sys.modules, {"neo4j": fake}):
            client = build_neo4j_client({})
        self.assertIsInstance(client, Neo4jGraphClient)

    def test_custom_env_var_names_honored(self):
        fake = _fake_neo4j_module(_FakeDriver([]))
        with mock.patch.dict(os.environ, {"GRAPH_URL": "bolt://y"}, clear=True), \
                mock.patch.dict(sys.modules, {"neo4j": fake}):
            client = build_neo4j_client({"uri_env": "GRAPH_URL"})
        self.assertIsInstance(client, Neo4jGraphClient)


# ----------------------------------------------------------------- select_backends (neo4j routing)

class TestSelectBackendsNeo4j(unittest.TestCase):
    def test_neo4j_is_a_real_graph_tool(self):
        self.assertIn("neo4j", GRAPH_TOOLS)

    def test_neo4j_without_env_degrades_to_grep_floor(self):
        # router resolves neo4j, but no env/driver -> build returns None -> grep floor.
        with mock.patch.dict(os.environ, {}, clear=True):
            primary, fallback = select_backends(_Router("neo4j"), root=".")
        self.assertIsInstance(primary, GrepBackend)
        self.assertIsNone(fallback)
        self.assertFalse(primary.is_graph)

    def test_injected_client_makes_neo4j_primary(self):
        primary, fallback = select_backends(
            _Router("neo4j"), root=".", client=_FakeGraphClient({"foo"}))
        self.assertIsInstance(primary, CodeReviewGraphBackend)
        self.assertEqual(primary.name, "neo4j")
        self.assertTrue(primary.is_graph)
        self.assertIsInstance(fallback, GrepBackend)

    def test_neo4j_queries_still_answer_on_floor(self):
        # Degrade-clean: callers() returns a (grep) result even with no graph wired.
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {}, clear=True):
            with open(os.path.join(d, "m.py"), "w") as fh:
                fh.write("def run():\n    return 1\n")
            primary, fallback = select_backends(_Router("neo4j"), root=d)
            layer = KnowledgeLayer(primary, fallback)
            res = layer.callers("run")
            self.assertFalse(layer.uses_graph)
            self.assertIsNotNone(res)


# ----------------------------------------------------------------- make_graph_scorer

class TestMakeGraphScorer(unittest.TestCase):
    def _graph_layer(self, known):
        return KnowledgeLayer(
            CodeReviewGraphBackend(name="neo4j", root=".",
                                   client=_FakeGraphClient(known)),
            GrepBackend(root="."))

    def test_none_on_grep_floor(self):
        layer = KnowledgeLayer(GrepBackend(root="."), None)
        self.assertIsNone(make_graph_scorer(layer, "process_payment flow"))

    def test_none_when_no_anchor_resolves(self):
        layer = self._graph_layer(known=set())  # graph wired but nothing matches
        self.assertIsNone(make_graph_scorer(layer, "process_payment flow"))

    def test_scorer_boosts_items_mentioning_anchor(self):
        layer = self._graph_layer(known={"process_payment"})
        scorer = make_graph_scorer(layer, "how does process_payment work")
        self.assertIsNotNone(scorer)
        hit = MemoryItem.create("billing", "process_payment retries on 500")
        miss = MemoryItem.create("weather", "it is sunny today")
        self.assertEqual(scorer("", hit), 1.0)
        self.assertEqual(scorer("", miss), 0.0)


# ----------------------------------------------------------------- memory graph tier LIVE by default

class TestMemoryGraphTierAutoWired(unittest.TestCase):
    def _store(self, d, layer=None):
        return MemoryStore(SQLiteBackend(os.path.join(d, "mem.db")),
                           knowledge_layer=layer)

    def test_graph_tier_fuses_by_default_when_layer_wired(self):
        layer = KnowledgeLayer(
            CodeReviewGraphBackend(name="neo4j", root=".",
                                   client=_FakeGraphClient({"process_payment"})),
            GrepBackend(root="."))
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, layer=layer)
            # Two items with EQUAL lexical overlap to the query word "flow"; only one
            # mentions the graph-confirmed symbol, so the graph tier must lift it on top.
            store.remember(MemoryItem.create("billing flow",
                                             "process_payment handles the flow",
                                             mtype=DECISION), assume_yes=True)
            store.remember(MemoryItem.create("weather flow",
                                             "the wind controls the flow",
                                             mtype=DECISION), assume_yes=True)
            hits = store.recall_relevant("process_payment flow", top_k=2)
            self.assertEqual(len(hits), 2)
            self.assertIn("process_payment", hits[0].item.value)
            self.assertGreater(hits[0].graph, 0.0)   # graph tier actually contributed

    def test_graph_tier_silent_without_layer(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, layer=None)
            store.remember(MemoryItem.create("a", "alpha"), assume_yes=True)
            store.remember(MemoryItem.create("b", "beta"), assume_yes=True)
            hits = store.recall_relevant("alpha", top_k=2)
            self.assertTrue(hits)
            self.assertTrue(all(h.graph == 0.0 for h in hits))  # tier silent

    def test_graph_tier_silent_on_grep_floor(self):
        # A layer with no real graph contributes nothing — lexical still ranks.
        layer = KnowledgeLayer(GrepBackend(root="."), None)
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, layer=layer)
            store.remember(MemoryItem.create("a", "alpha"), assume_yes=True)
            hits = store.recall_relevant("alpha", top_k=1)
            self.assertTrue(hits)
            self.assertEqual(hits[0].graph, 0.0)


# ----------------------------------------------------------------- index/lat-check over the wired backend

class TestIndexOverBackend(unittest.TestCase):
    def test_index_reports_grep_floor_when_no_graph(self):
        from mokata import cli
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {}, clear=True):
            cli.main(["init", "--path", d, "--yes"])
            with open(os.path.join(d, "m.py"), "w") as fh:
                fh.write("def run():\n    return 1\n")
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["index", "--path", d])
            out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("grep floor", out)


if __name__ == "__main__":
    unittest.main()
