"""Stage 35f — Neo4j graph adapter.

Wires an EXTERNAL Neo4j graph database into the existing knowledge layer through the SAME
boundary the code-review-graph adapter uses: a `GraphQueryClient` that translates mokata's
typed structural queries (callers/callees/implementers/imports/blast_radius) into Cypher and
the rows back into the typed shape. mokata does NOT build the graph — it adopts whatever graph
the team populated; the conventional schema it queries is documented in the how-to.

OPTIONAL provider for `code_graph`, never a hard dependency: the `neo4j` driver is lazy-
imported behind the (existing) `postgres`/extra-style optionality, the URI + credentials come
from ENV VARS only (never inline in the committed manifest), and any of {no driver, no
`NEO4J_*` env, DB unreachable} ⇒ `build_neo4j_client` returns None so the knowledge layer
degrades cleanly to the ripgrep→grep floor (the queries still answer).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Conventional code-graph schema mokata queries (documented; the team's graph populates it).
# nodes (:Symbol {name, path, line}); rels [:CALLS] [:IMPLEMENTS] [:IMPORTS].
_CYPHER = {
    "callers": "MATCH (c:Symbol)-[:CALLS]->(t:Symbol {name:$target}) "
               "RETURN c.name AS symbol, c.path AS path, c.line AS line",
    "callees": "MATCH (t:Symbol {name:$target})-[:CALLS]->(c:Symbol) "
               "RETURN c.name AS symbol, c.path AS path, c.line AS line",
    "implementers": "MATCH (c:Symbol)-[:IMPLEMENTS]->(t:Symbol {name:$target}) "
                    "RETURN c.name AS symbol, c.path AS path, c.line AS line",
    "imports": "MATCH (c:Symbol)-[:IMPORTS]->(t:Symbol {name:$target}) "
               "RETURN c.name AS symbol, c.path AS path, c.line AS line",
}


class Neo4jUnavailable(Exception):
    """Raised when the Neo4j adapter can't be built — driver missing, no NEO4J_* env, or the
    DB unreachable. The caller degrades to the grep floor (never a hard failure)."""


class Neo4jGraphClient:
    """A `GraphQueryClient` (the existing adapter boundary) backed by Neo4j. `driver` is the
    neo4j driver; it's injected so the wire protocol lives in one place and tests can drive
    the live path with a double. Returns row dicts {path,line,snippet,symbol}."""

    def __init__(self, driver: Any, database: Optional[str] = None) -> None:
        if driver is None:
            raise Neo4jUnavailable("no neo4j driver provided")
        self._driver = driver
        self._database = database
        # Verify the DB is reachable at construction (mirrors PgVectorBackend/PostgresBackend):
        # an unreachable DB raises the typed signal so `build_neo4j_client` degrades cleanly.
        try:
            driver.verify_connectivity()
        except Neo4jUnavailable:
            raise
        except Exception as exc:
            raise Neo4jUnavailable(f"neo4j unreachable: {exc}") from exc

    def query(self, kind: str, target: str, root: str,
              depth: int = 1) -> List[Dict[str, Any]]:
        if kind == "blast_radius":
            # variable-length CALLS within `depth`; depth is an int -> safe to inline.
            cypher = (f"MATCH (t:Symbol {{name:$target}})<-[:CALLS*1..{int(depth)}]-"
                      "(c:Symbol) RETURN DISTINCT c.name AS symbol, c.path AS path, "
                      "c.line AS line")
        else:
            cypher = _CYPHER.get(kind)
            if cypher is None:
                raise ValueError(f"unknown query kind '{kind}'")
        with self._driver.session(database=self._database) as session:
            records = session.run(cypher, target=target)
            rows: List[Dict[str, Any]] = []
            for rec in records:
                d = dict(rec)
                rows.append({"path": d.get("path") or "", "line": d.get("line") or 0,
                             "symbol": d.get("symbol") or "", "snippet": d.get("symbol") or ""})
            return rows

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:  # pragma: no cover
            pass


def build_neo4j_client(config: Optional[Dict[str, Any]] = None) -> Optional[Neo4jGraphClient]:
    """Build a Neo4j client from env (URI + credentials), or None to degrade. Honors env-var
    references only — `config.uri_env`/`user_env`/`password_env` name the vars (defaults
    NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD); never an inline URI/credential. Returns None
    when the driver is absent, the env vars are unset, or the DB is unreachable."""
    config = config or {}
    uri = os.environ.get(config.get("uri_env", "NEO4J_URI"))
    if not uri:
        return None
    user = os.environ.get(config.get("user_env", "NEO4J_USERNAME"))
    password = os.environ.get(config.get("password_env", "NEO4J_PASSWORD"))
    database = config.get("database")
    try:
        try:
            import neo4j  # optional extra; lazy so the core stays dependency-free
        except ImportError as exc:
            raise Neo4jUnavailable(
                f"the 'neo4j' driver is not installed (pip install \"mokata[neo4j]\"): {exc}"
            ) from exc
        auth = (user, password) if user is not None else None
        try:
            driver = neo4j.GraphDatabase.driver(uri, auth=auth)
        except Exception as exc:
            raise Neo4jUnavailable(f"could not create the neo4j driver: {exc}") from exc
        # Neo4jGraphClient.__init__ verifies connectivity and raises Neo4jUnavailable if down.
        return Neo4jGraphClient(driver, database=database)
    except Neo4jUnavailable:
        # Typed degrade — fall back to the ripgrep→grep floor; never a hard failure.
        return None
