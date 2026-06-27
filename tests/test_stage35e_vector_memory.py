"""Stage 35e — vector memory backend + tiered semantic retrieval.

Both jsonschema states. No live Postgres / pgvector in CI, so the pgvector backend is
DEGRADE-tested and the ranking behaviour is proven with the zero-dep local embedder + a
SQLite backend as the in-memory vector double. A synonym-aware embedder double demonstrates
semantic != lexical (so we can show semantic ranking really fires).

MANUAL VERIFICATION (named live gap): with psycopg + pgvector installed and a reachable DB,
build PgVectorBackend(dsn, HashingEmbedder()) and confirm semantic_search ranks the nearest
items via the pgvector index (no full-store scan). Exercised live only where a DB exists.
"""

import importlib.util
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory import (
    DECISION,
    HashingEmbedder,
    MemoryItem,
    MemoryStore,
    PgVectorBackend,
    SQLiteBackend,
    VectorUnavailable,
    build_pgvector_backend,
    cosine,
    make_embedder,
    tiered_recall,
)

_HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None


class SynonymEmbedder:
    """A tiny deterministic embedder where chosen synonyms map to the SAME basis vector, so
    semantic similarity diverges from lexical overlap (e.g. 'pg' ~ 'postgres' though they
    share no tokens). Lets the tests prove the semantic tier actually fires."""

    GROUPS = {
        0: {"pg", "postgres", "postgresql", "database", "db"},
        1: {"auth", "authentication", "login", "jwt"},
        2: {"cache", "caching", "redis"},
    }
    DIM = 8

    def __call__(self, text):
        import re
        toks = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
        vec = [0.0] * self.DIM
        for axis, group in self.GROUPS.items():
            if toks & group:
                vec[axis] = 1.0
        # tokens outside any group land on a shared "other" axis so unrelated text != 0
        if toks and not any(vec):
            vec[self.DIM - 1] = 1.0
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec] if norm else vec


def _store(d, embedder=None):
    return MemoryStore(SQLiteBackend(os.path.join(d, "mem.db")), embedder=embedder)


# ------------------------------------------------------------------ the embedder seam

class TestEmbedder(unittest.TestCase):
    def test_hashing_embedder_is_deterministic(self):
        e = HashingEmbedder()
        self.assertEqual(e("postgres for the team"), e("postgres for the team"))
        self.assertEqual(len(e("x")), 64)

    def test_cosine_and_make_embedder(self):
        e = HashingEmbedder()
        self.assertGreater(cosine(e("shared memory"), e("shared memory")), 0.99)
        self.assertIsNone(make_embedder(None))         # no embedder => semantic off
        self.assertIsNone(make_embedder("nope"))
        self.assertIsInstance(make_embedder("hashing"), HashingEmbedder)


# ------------------------------------------------------------------ tiered retrieval

class TestTieredRetrieval(unittest.TestCase):
    def test_semantic_ranks_embedding_near_above_lexical_only(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=SynonymEmbedder())
            # near in MEANING to "pg" (synonym) but shares NO query tokens lexically:
            store.remember(MemoryItem.create("database engine", "postgres", mtype=DECISION),
                           assume_yes=True)
            # shares a token with the query lexically ("the"), but semantically unrelated:
            store.remember(MemoryItem.create("the office plant", "ficus"), assume_yes=True)

            hits = store.recall_relevant("which pg do we use", top_k=2)
            self.assertEqual(hits[0].item.value, "postgres")   # semantic wins
            self.assertGreater(hits[0].semantic, hits[0].lexical)

    def test_lexical_floor_when_semantic_off(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=None)               # no embedder => semantic off
            store.remember(MemoryItem.create("caching layer", "use redis"), assume_yes=True)
            store.remember(MemoryItem.create("unrelated", "weather"), assume_yes=True)
            hits = store.recall_relevant("caching layer", top_k=2)
            self.assertTrue(hits)                           # lexical still returns results
            self.assertEqual(hits[0].item.subject, "caching layer")
            self.assertEqual(hits[0].semantic, 0.0)        # semantic tier absent

    def test_deterministic_ordering(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=SynonymEmbedder())
            for s, v in [("a", "auth"), ("b", "cache"), ("c", "database")]:
                store.remember(MemoryItem.create(s, v), assume_yes=True)
            r1 = [h.item.id for h in store.recall_relevant("login + db", top_k=3)]
            r2 = [h.item.id for h in store.recall_relevant("login + db", top_k=3)]
            self.assertEqual(r1, r2)                        # stable across runs

    def test_graph_tier_is_optional_and_fuses(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=None)
            store.remember(MemoryItem.create("parser", "handles tokens"), assume_yes=True)
            store.remember(MemoryItem.create("logger", "writes lines"), assume_yes=True)
            # a graph scorer boosts items whose subject the query "references"
            def gscore(query, item):
                return 1.0 if item.subject in query else 0.0
            hits = store.recall_relevant("change the parser", graph_scorer=gscore, top_k=2)
            self.assertEqual(hits[0].item.subject, "parser")
            self.assertGreater(hits[0].graph, 0.0)

    def test_top_k_is_frugal(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=HashingEmbedder())
            for i in range(10):
                store.remember(MemoryItem.create(f"s{i}", f"value {i}"), assume_yes=True)
            self.assertEqual(len(store.recall_relevant("value", top_k=3)), 3)

    def test_embedding_stamped_on_write(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d, embedder=HashingEmbedder())
            store.remember(MemoryItem.create("k", "v"), assume_yes=True)
            it = store.all_active()[0]
            self.assertIn("_embedding", it.provenance)     # computed once, on write
            # no embedder => not stamped
            store2 = _store(d + "2") if False else MemoryStore(
                SQLiteBackend(os.path.join(d, "m2.db")))
            store2.remember(MemoryItem.create("k", "v"), assume_yes=True)
            self.assertNotIn("_embedding", store2.all_active()[0].provenance)


# ------------------------------------------------------------------ pgvector degrade

class TestPgVectorDegrade(unittest.TestCase):
    def test_build_returns_none_without_embedder(self):
        self.assertIsNone(build_pgvector_backend({"dsn_env": "X"}, embedder=None))

    def test_build_returns_none_without_dsn(self):
        self.assertIsNone(build_pgvector_backend({}, embedder=HashingEmbedder()))
        self.assertIsNone(build_pgvector_backend({"dsn_env": "MOKATA_NO_VEC_DSN"},
                                                 embedder=HashingEmbedder()))

    def test_build_returns_none_when_unreachable(self):
        with mock.patch.dict(os.environ, {"MOKATA_VEC_DSN": "postgresql://x/db"}):
            self.assertIsNone(build_pgvector_backend({"dsn_env": "MOKATA_VEC_DSN"},
                                                     embedder=HashingEmbedder()))

    @unittest.skipIf(_HAS_PSYCOPG, "psycopg installed — the absent-driver path can't be shown")
    def test_backend_raises_unavailable_without_psycopg(self):
        with self.assertRaises(VectorUnavailable):
            PgVectorBackend("postgresql://x/db", HashingEmbedder())

    def test_backend_raises_without_embedder(self):
        with self.assertRaises(VectorUnavailable):
            PgVectorBackend("postgresql://x/db", None)


if __name__ == "__main__":
    unittest.main()
