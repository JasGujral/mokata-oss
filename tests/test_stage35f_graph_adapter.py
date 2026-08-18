"""Stage 35f — the live graph-proximity memory tier, over an ADOPTED graph.

⚠ THIS FILE USED TO BE HALF A NEO4J-ADAPTER SUITE. The Neo4j `code_graph` provider was REMOVED at
0.0.18 lane D stage 14, and with it `Neo4jGraphClient`, `build_neo4j_client`, `Neo4jUnavailable`
and `select_backends`' neo4j arm — so `TestNeo4jGraphClient`, `TestBuildNeo4jClient` and
`TestSelectBackendsNeo4j` went with their subject, along with the fake driver and fake `neo4j`
module they drove it with.

★ WHAT SURVIVES IS THE PROPERTY, NOT ITS WITNESS. `make_graph_scorer` and the auto-wired memory
graph tier were never about Neo4j: they key on `layer.uses_graph`, i.e. on ANY adopted graph, and
they were exercised through an injected `GraphQueryClient` double the whole time — the Neo4j name
on the backend was decoration. The doubles below are unchanged; only the name they carry moved to
a provider that still exists.

Both jsonschema states (no jsonschema is imported here — these exercise the knowledge + memory
layers, which are dependency-free, so behaviour is identical ABSENT/PRESENT).

⚠ THE MANUAL-VERIFICATION NOTE THAT USED TO SIT HERE IS DELETED, NOT RE-POINTED. It told a reader
to `pip install neo4j`, populate a conventional schema and confirm `mokata index` reports
`code graph 'neo4j' wired` — a live gap that can no longer be closed by anyone, because the code
it verifies does not exist. A named gap nobody can ever discharge is worse than no note at all.
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
    make_graph_scorer,
)
from mokata.knowledge.layer import GRAPH_TOOLS, select_backends
from mokata.memory import DECISION, MemoryItem, MemoryStore, SQLiteBackend


# ----------------------------------------------------------------- doubles

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


# ----------------------------------------------------------------- select_backends (adopted graph)

class TestSelectBackendsAdoptedGraph(unittest.TestCase):
    """The routing property the deleted `TestSelectBackendsNeo4j` graded, re-pointed at a provider
    that still exists. ⚠ The neo4j-specific half of it — "a present driver module is not a
    reachable DB, so there is a BUILD-TIME probe" — is not re-pointed onto anything, because no
    surviving provider has that shape: `code-review-graph` and `serena` are probed by the detector
    like every other command tool, so `router.resolve` never returns an absent one."""

    def test_an_adopted_graph_is_a_real_graph_tool(self):
        self.assertEqual(GRAPH_TOOLS, ("code-review-graph", "serena"))
        self.assertNotIn("neo4j", GRAPH_TOOLS)

    def test_injected_client_makes_the_adopted_graph_primary(self):
        primary, fallback = select_backends(
            _Router("code-review-graph"), root=".", client=_FakeGraphClient({"foo"}))
        self.assertIsInstance(primary, CodeReviewGraphBackend)
        self.assertEqual(primary.name, "code-review-graph")
        self.assertTrue(primary.is_graph)
        self.assertIsInstance(fallback, GrepBackend)

    def test_queries_still_answer_on_the_floor(self):
        # Degrade-clean: callers() returns a (floor) result even with no graph wired.
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {}, clear=True):
            with open(os.path.join(d, "m.py"), "w", encoding="utf-8") as fh:
                fh.write("def run():\n    return 1\n")
            primary, fallback = select_backends(_Router("ripgrep"), root=d)
            layer = KnowledgeLayer(primary, fallback)
            res = layer.callers("run")
            self.assertFalse(layer.uses_graph)
            self.assertIsNotNone(res)


# ----------------------------------------------------------------- make_graph_scorer

class TestMakeGraphScorer(unittest.TestCase):
    def _graph_layer(self, known):
        return KnowledgeLayer(
            CodeReviewGraphBackend(name="code-review-graph", root=".",
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
            CodeReviewGraphBackend(name="code-review-graph", root=".",
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
            with open(os.path.join(d, "m.py"), "w", encoding="utf-8") as fh:
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
