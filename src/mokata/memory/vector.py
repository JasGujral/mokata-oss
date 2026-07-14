"""Stage 35e — vector memory backend (pgvector first), mokata-owned schema.

A semantic store over the mokata-owned ``mokata_memory_vectors`` table (an ``embedding`` column),
implementing the full ``MemoryBackend`` contract plus ``semantic_search(query_vector, top_k)``,
which returns the nearest items via the pgvector index (no full-store scan). Embeddings are
computed on the gated WRITE by an injected embedder (local-first, optional). Degrade-clean:
psycopg / the pgvector extension / no embedder ⇒ ``VectorUnavailable`` so selection falls back to
the lexical floor — never a hard failure.

D1: this backend does NOT own its schema. It used to run the extension + table DDL on every
connect; the schema now lives in the init path (``teamdb.provision_vector``), OFF the golden path
(ADR-54: vanilla Postgres, no extensions), and the connect VERIFIES the table instead.

No live Postgres/pgvector in CI, so this is DEGRADE-tested here and MANUALLY verified live
(see test_stage35e_vector_memory.py); the local semantic tier (zero-dep, over any backend's
stored embeddings) is what the tests exercise for ranking behaviour.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from .backends import MemoryBackend
from .embed import EMBED_DIM, Embedder
from .item import ACTIVE, DEFAULT_TOP_K, MemoryItem
from ..errors import DegradedCapability


class VectorUnavailable(DegradedCapability):
    """Raised when the vector backend can't be built — psycopg/pgvector missing, the DB
    unreachable, or no embedder configured. Callers degrade to the lexical floor."""


class PgVectorBackend(MemoryBackend):
    """pgvector-backed semantic memory; mokata owns the `mokata_memory_vectors` schema."""

    name = "pgvector"
    TABLE = "mokata_memory_vectors"

    def __init__(self, dsn: Optional[str] = None, embedder: Optional[Embedder] = None,
                 dim: int = EMBED_DIM, name: str = "pgvector",
                 project: Optional[str] = None, conn: Any = None) -> None:
        if embedder is None:
            raise VectorUnavailable("no embedder configured — semantic tier is off")
        self.name = name
        self.dim = dim
        self._embed = embedder
        # Stage 71a — scope every row by the current project (None spans all, review only). `conn`
        # injects a connection so the scoping is testable without a live pgvector DB.
        self.project = project
        if conn is not None:
            self._conn = conn                     # an injected connection: already provisioned.
            return
        # D1 — VERIFY the mokata-owned vector table; never create it. This connect used to run
        # `CREATE EXTENSION IF NOT EXISTS vector` — the most privileged statement in the codebase
        # — on a path a DML-only runtime role is supposed to be able to use. The schema now lives
        # in the init path (`teamdb.vector_provision_sql` / `provision_vector`), and an unprovisioned
        # tier degrades LOUDLY with the exact remediation rather than being conjured at runtime.
        from ..teamdb import VECTOR_TABLE
        from ._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, VectorUnavailable, require_tables=(VECTOR_TABLE,))

    def _scope(self, prefix: str = "AND") -> Tuple[str, tuple]:
        if self.project is None:
            return "", ()
        return f" {prefix} project=%s", (self.project,)

    # --- contract ---------------------------------------------------------------
    # Justification for the B608 suppressions below (bandit false positive): every SQL string
    # here interpolates ONLY the mokata-OWNED constant `self.TABLE` (+ the fixed `_scope()`
    # fragment), never user input; all VALUES ride the driver's `%s` placeholders. Suppression
    # markers only — no injection surface, no behaviour change.
    def put(self, item: MemoryItem) -> None:
        vec = self._embed(f"{item.subject} {item.value}")
        self._conn.execute(
            f"INSERT INTO {self.TABLE} (id, mtype, subject, status, doc, embedding, project)"  # nosec B608
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET mtype=EXCLUDED.mtype,"
            " subject=EXCLUDED.subject, status=EXCLUDED.status, doc=EXCLUDED.doc,"
            " embedding=EXCLUDED.embedding, project=EXCLUDED.project",
            (item.id, item.mtype, item.subject, item.status,
             json.dumps(item.to_doc()), _vlit(vec), self.project))   # D6 — durable serializer

    def get(self, item_id: str) -> Optional[MemoryItem]:
        clause, params = self._scope()
        row = self._conn.execute(
            f"SELECT doc FROM {self.TABLE} WHERE id=%s{clause}", (item_id, *params)).fetchone()  # nosec B608
        return MemoryItem.from_dict(json.loads(row[0])) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        clause, params = self._scope(prefix="WHERE")
        rows = self._conn.execute(
            f"SELECT doc FROM {self.TABLE}{clause} ORDER BY seq", params).fetchall()  # nosec B608
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in rows]
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

    def delete(self, item_id: str) -> bool:
        clause, params = self._scope()
        cur = self._conn.execute(
            f"DELETE FROM {self.TABLE} WHERE id=%s{clause}", (item_id, *params))  # nosec B608
        return cur.rowcount > 0

    def list_projects(self) -> List[str]:
        from .backends import _distinct_projects
        rows = self._conn.execute(f"SELECT DISTINCT project FROM {self.TABLE}").fetchall()  # nosec B608
        return _distinct_projects(rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            # D5 — deliberately left BROAD, with no narrow class to name: `self._conn` is a
            # THIRD-PARTY (psycopg) or injected connection whose `close()` raises driver classes
            # mokata cannot import without a hard dependency on the optional `postgres` extra.
            # Teardown has nothing to degrade TO and nothing a user could act on.
            pass

    # --- semantic search (index-backed top-k; no full-store scan) ---------------
    def semantic_search(self, query: str, top_k: int = DEFAULT_TOP_K,
                        statuses: Tuple[str, ...] = (ACTIVE,)
                        ) -> List[Tuple[MemoryItem, float]]:
        """Nearest items to `query` by cosine distance, best first — via the pgvector index.
        SCOPED to the current project (Stage 71a) so a shared DB never leaks another project's
        neighbours into a recall."""
        qv = _vlit(self._embed(query))
        clause, sparams = self._scope(prefix="WHERE")
        rows = self._conn.execute(
            f"SELECT doc, 1 - (embedding <=> %s) AS score FROM {self.TABLE}{clause} "  # nosec B608
            "ORDER BY embedding <=> %s LIMIT %s", (qv, *sparams, qv, top_k)).fetchall()
        out = []
        for doc, score in rows:
            it = MemoryItem.from_dict(json.loads(doc))
            if it.status in statuses:
                out.append((it, float(score)))
        return out


def _vlit(vec: List[float]) -> str:
    """pgvector accepts a vector literal as the string '[f1,f2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def build_pgvector_backend(config: dict, embedder: Optional[Embedder],
                           project: Optional[str] = None) -> Optional["PgVectorBackend"]:
    """Build a pgvector backend from per-tool `config` + an embedder, or None to degrade.
    Honors ONLY `config.dsn_env` (never an inline DSN). Returns None when the env var is
    unset, no embedder is configured, psycopg/pgvector is absent, or the DB is unreachable.
    `project` (Stage 71a) scopes all rows to the current project; None spans all (review)."""
    if embedder is None:
        return None
    dsn_env = (config or {}).get("dsn_env")
    if not dsn_env:
        return None
    # Route the env-var read through the ONE resolver (CM.S1) — same funnel as the postgres
    # backend + health/flush, so a custom `dsn_env` can never split reads from writes (C-1).
    from ..dsn import resolve_dsn
    res = resolve_dsn(override=dsn_env)
    if not res.is_set:
        return None
    try:
        return PgVectorBackend(res.dsn, embedder, project=project)
    except VectorUnavailable:
        return None
