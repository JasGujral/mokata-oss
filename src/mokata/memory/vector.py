"""Stage 35e — vector memory backend (pgvector first), mokata-owned schema.

A semantic store: like :class:`PostgresBackend` it OWNS its schema — it runs
``CREATE EXTENSION IF NOT EXISTS vector`` and creates its own ``mokata_memory_vectors`` table
with an ``embedding`` column — and implements the full ``MemoryBackend`` contract, plus
``semantic_search(query_vector, top_k)`` that returns the nearest items via the pgvector index
(no full-store scan). Embeddings are computed on the gated WRITE by an injected embedder
(local-first, optional). Degrade-clean: psycopg / the pgvector extension / no embedder ⇒
``VectorUnavailable`` so selection falls back to the lexical floor — never a hard failure.

No live Postgres/pgvector in CI, so this is DEGRADE-tested here and MANUALLY verified live
(see test_stage35e_vector_memory.py); the local semantic tier (zero-dep, over any backend's
stored embeddings) is what the tests exercise for ranking behaviour.
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

from .backends import MemoryBackend
from .embed import EMBED_DIM, Embedder
from .item import ACTIVE, DEFAULT_TOP_K, MemoryItem


class VectorUnavailable(Exception):
    """Raised when the vector backend can't be built — psycopg/pgvector missing, the DB
    unreachable, or no embedder configured. Callers degrade to the lexical floor."""


class PgVectorBackend(MemoryBackend):
    """pgvector-backed semantic memory; mokata owns the `mokata_memory_vectors` schema."""

    name = "pgvector"
    TABLE = "mokata_memory_vectors"

    def __init__(self, dsn: str, embedder: Embedder, dim: int = EMBED_DIM,
                 name: str = "pgvector") -> None:
        if embedder is None:
            raise VectorUnavailable("no embedder configured — semantic tier is off")
        from ._pg import connect_psycopg
        self.name = name
        self.dim = dim
        self._embed = embedder
        # mokata-OWNED schema: the extension + our own namespaced table.
        self._conn = connect_psycopg(dsn, VectorUnavailable, setup_sql=[
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
            "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT, status TEXT,"
            f"  doc TEXT, embedding vector({dim}), seq BIGSERIAL)",
        ])

    # --- contract ---------------------------------------------------------------
    def put(self, item: MemoryItem) -> None:
        vec = self._embed(f"{item.subject} {item.value}")
        self._conn.execute(
            f"INSERT INTO {self.TABLE} (id, mtype, subject, status, doc, embedding)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET mtype=EXCLUDED.mtype,"
            " subject=EXCLUDED.subject, status=EXCLUDED.status, doc=EXCLUDED.doc,"
            " embedding=EXCLUDED.embedding",
            (item.id, item.mtype, item.subject, item.status,
             json.dumps(item.to_dict()), _vlit(vec)))

    def get(self, item_id: str) -> Optional[MemoryItem]:
        row = self._conn.execute(
            f"SELECT doc FROM {self.TABLE} WHERE id=%s", (item_id,)).fetchone()
        return MemoryItem.from_dict(json.loads(row[0])) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        rows = self._conn.execute(f"SELECT doc FROM {self.TABLE} ORDER BY seq").fetchall()
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in rows]
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

    def delete(self, item_id: str) -> bool:
        cur = self._conn.execute(f"DELETE FROM {self.TABLE} WHERE id=%s", (item_id,))
        return cur.rowcount > 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass

    # --- semantic search (index-backed top-k; no full-store scan) ---------------
    def semantic_search(self, query: str, top_k: int = DEFAULT_TOP_K,
                        statuses: Tuple[str, ...] = (ACTIVE,)
                        ) -> List[Tuple[MemoryItem, float]]:
        """Nearest items to `query` by cosine distance, best first — via the pgvector index."""
        qv = _vlit(self._embed(query))
        rows = self._conn.execute(
            f"SELECT doc, 1 - (embedding <=> %s) AS score FROM {self.TABLE} "
            "ORDER BY embedding <=> %s LIMIT %s", (qv, qv, top_k)).fetchall()
        out = []
        for doc, score in rows:
            it = MemoryItem.from_dict(json.loads(doc))
            if it.status in statuses:
                out.append((it, float(score)))
        return out


def _vlit(vec: List[float]) -> str:
    """pgvector accepts a vector literal as the string '[f1,f2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def build_pgvector_backend(config: dict, embedder: Optional[Embedder]) -> Optional["PgVectorBackend"]:
    """Build a pgvector backend from per-tool `config` + an embedder, or None to degrade.
    Honors ONLY `config.dsn_env` (never an inline DSN). Returns None when the env var is
    unset, no embedder is configured, psycopg/pgvector is absent, or the DB is unreachable."""
    import os
    if embedder is None:
        return None
    dsn_env = (config or {}).get("dsn_env")
    if not dsn_env:
        return None
    dsn = os.environ.get(dsn_env)
    if not dsn:
        return None
    try:
        return PgVectorBackend(dsn, embedder)
    except VectorUnavailable:
        return None
