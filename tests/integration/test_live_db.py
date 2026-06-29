"""Stage 47 — live-DB integration legs (ADDITIVE, opt-in; CI-only by default).

Turns the previously degrade-tested-only / MANUAL-VERIFICATION residual risks into real
round-trips against real services:

  - shared Postgres memory (D3/D17) — two clients see one store + a contradiction surfaces;
  - pgvector semantic recall (D21) — a real pgvector index ranks the semantically-near item;
  - Neo4j graph (D22) — a real Cypher query answers callers / blast-radius.

Gate (explicit, never accidental): these run ONLY when MOKATA_LIVE_DB=1 AND the matching
service env vars are present AND the optional driver is installed AND the DB is reachable.
On a dev box (no hosted DB) they skip cleanly; in the CI `live-db` job they run and are
required. The env-var-only DSN contract is unchanged (no inline credentials):
  - Postgres / pgvector:  MOKATA_PG_DSN  (password via PGPASSWORD, never inline)
  - Neo4j:                NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD

The dependency-free core is untouched — the drivers stay optional extras, installed only in
the live-db job.
"""

import importlib.util
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path when not pip-installed)

from mokata.memory import (
    CONTRADICTION,
    HashingEmbedder,
    MemoryItem,
    MemoryStore,
    PgVectorBackend,
    PostgresBackend,
)

LIVE = os.environ.get("MOKATA_LIVE_DB") == "1"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _pg_dsn():
    # The standard env var (Stage 47); MOKATA_TEST_PG_DSN kept as a manual-use fallback.
    return os.environ.get("MOKATA_PG_DSN") or os.environ.get("MOKATA_TEST_PG_DSN")


_PG_LIVE = LIVE and _have("psycopg") and bool(_pg_dsn())
_NEO4J_LIVE = LIVE and _have("neo4j") and bool(os.environ.get("NEO4J_URI"))

_PG_REASON = "live PG off (need MOKATA_LIVE_DB=1 + MOKATA_PG_DSN + psycopg + reachable DB)"
_NEO4J_REASON = "live Neo4j off (need MOKATA_LIVE_DB=1 + NEO4J_URI + neo4j driver + reachable DB)"


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestLiveSharedPostgresMemory(unittest.TestCase):
    """D3/D17 — two clients on the SAME Postgres store see each other's writes."""

    def setUp(self):
        self.dsn = _pg_dsn()
        a = PostgresBackend(self.dsn)            # isolate: clear the mokata-owned table
        for it in a.all():
            a.delete(it.id)
        a.close()

    def test_two_clients_share_one_store_with_provenance(self):
        a = MemoryStore(PostgresBackend(self.dsn))
        b = MemoryStore(PostgresBackend(self.dsn))
        a.remember(MemoryItem.create("db.engine", "postgres", source="alice"),
                   assume_yes=True)
        seen = b.recall("db.engine")             # the OTHER client reads it back
        self.assertEqual([i.value for i in seen], ["postgres"])
        self.assertEqual(seen[0].provenance.get("source"), "alice")
        # a conflicting write from b surfaces a contradiction (not a silent merge)
        b.remember(MemoryItem.create("db.engine", "mysql", source="bob"), assume_yes=True)
        self.assertTrue(any(p.kind == CONTRADICTION for p in b.detect_issues()))
        a.close()
        b.close()


@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TestLivePgVectorSemanticRecall(unittest.TestCase):
    """D21 — a real pgvector index ranks the semantically-near item first."""

    def test_real_vector_index_ranks_nearest_first(self):
        be = PgVectorBackend(_pg_dsn(), HashingEmbedder())
        try:
            for it in be.all():                  # isolate
                be.delete(it.id)
            be.put(MemoryItem.create("db", "postgres database connection pooling"))
            be.put(MemoryItem.create("ui", "react frontend component styling theme"))
            be.put(MemoryItem.create("ci", "github actions workflow yaml pipeline"))
            hits = be.semantic_search("postgres database connection", top_k=3)
            self.assertTrue(hits, "the pgvector index returned no rows")
            # nearest-first via the index (ORDER BY embedding <=> query); not a full scan
            self.assertEqual(hits[0][0].subject, "db")
        finally:
            be.close()


@unittest.skipUnless(_NEO4J_LIVE, _NEO4J_REASON)
class TestLiveNeo4jGraph(unittest.TestCase):
    """D22 — a real Cypher query answers callers + blast-radius over a seeded graph."""

    def test_real_cypher_answers_callers_and_blast_radius(self):
        from mokata.knowledge.neo4j_backend import build_neo4j_client
        client = build_neo4j_client({})          # reads NEO4J_URI/USERNAME/PASSWORD
        self.assertIsNotNone(client, "build_neo4j_client returned None against a live DB")
        try:
            driver = client._driver
            with driver.session() as s:
                # seed: main -> helper -> util  (CALLS edges); :Symbol{name,path,line}
                s.run("MATCH (n) DETACH DELETE n")
                s.run("CREATE (:Symbol {name:'util', path:'u.py', line:1})")
                s.run("CREATE (:Symbol {name:'helper', path:'h.py', line:2})")
                s.run("CREATE (:Symbol {name:'main', path:'m.py', line:3})")
                s.run("MATCH (a:Symbol {name:'helper'}), (b:Symbol {name:'util'}) "
                      "CREATE (a)-[:CALLS]->(b)")
                s.run("MATCH (a:Symbol {name:'main'}), (b:Symbol {name:'helper'}) "
                      "CREATE (a)-[:CALLS]->(b)")
            callers = client.query("callers", "util", root=".")
            self.assertEqual({r["symbol"] for r in callers}, {"helper"})
            blast = client.query("blast_radius", "util", root=".", depth=2)
            self.assertEqual({r["symbol"] for r in blast}, {"helper", "main"})
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
