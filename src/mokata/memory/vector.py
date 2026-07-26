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
from typing import Any, Callable, List, Optional, Tuple

from .backends import MemoryBackend
from .embed import EMBED_DIM, Embedder, embedder_identity
from .item import ACTIVE, DEFAULT_TOP_K, MemoryItem
from ..degrade import FAILURE_ENGINE
from ..errors import DegradedCapability

# DB.S4 — the hard ceiling on an index-backed top-k. Generous enough that no real recall ever
# notices it, small enough that no caller can turn `semantic_search` back into a store dump.
MAX_TOP_K = 200


class VectorUnavailable(DegradedCapability):
    """Raised when the vector backend can't be built — psycopg/pgvector missing, the DB
    unreachable, or no embedder configured. Callers degrade to the lexical floor."""


class EmbedderStampMismatch(VectorUnavailable):
    """DB.S4 — the index was built by a DIFFERENT embedder than the one now configured.

    A subclass of `VectorUnavailable` so every existing caller degrades correctly without
    knowing this exists; caught SPECIFICALLY where the remediation differs, because it does:
    the other unavailability causes are "something is missing/unreachable", this one is
    "something would be WRONG". Refusing is the whole point — comparing a query vector from
    embedder B against index vectors from embedder A yields plausible numbers and meaningless
    order, and a wrong ranking is invisible in a way an absent tier never is.

    Carries the stamped and current identities so the caller can name both in its finding, and
    `MIGRATION_COMMAND` so the fix is in the message rather than in the docs."""

    MIGRATION_COMMAND = "mokata memory reembed"
    # `engine-unavailable`, NOT `unreachable` (which `VectorUnavailable` carries): the database is
    # perfectly reachable and the index is perfectly healthy. What is unavailable is a MEANINGFUL
    # comparison between two embedders' vectors — and telling a user to check their connection
    # when the fix is a re-embed is exactly how a misclassified failure wastes an afternoon.
    failure_class = FAILURE_ENGINE
    fix = f"re-embed the index with `{MIGRATION_COMMAND}` (previewed and gated)"

    def __init__(self, stamped: Tuple[str, int], current: Tuple[str, int]) -> None:
        self.stamped = stamped
        self.current = current
        super().__init__(
            f"the vector index was built with embedder '{stamped[0]}' (dim {stamped[1]}) but "
            f"'{current[0]}' (dim {current[1]}) is configured — vectors from two embedders are "
            f"not comparable, so the semantic tier is OFF rather than silently wrong. "
            f"Re-embed with `{self.MIGRATION_COMMAND}`.")


class PgVectorBackend(MemoryBackend):
    """pgvector-backed semantic memory; mokata owns the `mokata_memory_vectors` schema."""

    name = "pgvector"
    TABLE = "mokata_memory_vectors"

    def __init__(self, dsn: Optional[str] = None, embedder: Optional[Embedder] = None,
                 dim: int = EMBED_DIM, name: str = "pgvector",
                 project: Optional[str] = None, conn: Any = None,
                 verify_stamp: bool = True) -> None:
        if embedder is None:
            raise VectorUnavailable("no embedder configured — semantic tier is off")
        self.name = name
        # DB.S4 — the DIM is the EMBEDDER's, not a caller-supplied constant. The old default
        # (`EMBED_DIM`, the hashing embedder's 64) silently mis-described any other embedder, and a
        # dim recorded wrong is a stamp that certifies the wrong thing.
        self.embedder_id, embedder_dim = embedder_identity(embedder)
        self.dim = embedder_dim or dim
        self._embed = embedder
        # Stage 71a — scope every row by the current project (None spans all, review only). `conn`
        # injects a connection so the scoping is testable without a live pgvector DB.
        self.project = project
        if conn is not None:
            self._conn = conn                     # an injected connection: already provisioned.
            if verify_stamp:
                self.verify_stamp()
            return
        # D1 — VERIFY the mokata-owned vector table; never create it. This connect used to run
        # `CREATE EXTENSION IF NOT EXISTS vector` — the most privileged statement in the codebase
        # — on a path a DML-only runtime role is supposed to be able to use. The schema now lives
        # in the init path (`teamdb.vector_provision_sql` / `provision_vector`), and an unprovisioned
        # tier degrades LOUDLY with the exact remediation rather than being conjured at runtime.
        from ..teamdb import VECTOR_TABLE
        from ._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, VectorUnavailable, require_tables=(VECTOR_TABLE,))
        if verify_stamp:
            self.verify_stamp()

    # --- DB.S4 · the stamp binding -----------------------------------------------
    def read_stamp(self) -> Optional[Tuple[str, int]]:
        """The `(embedder_id, dim)` this index was built with, or None when it carries no stamp.

        None is the PRE-DB.S4 index (provisioned before the stamp table existed) — and it is
        treated as compatible, not as a mismatch. Refusing every existing index on upgrade would
        turn a safety feature into an outage for the exact users who already opted in; the first
        `mokata memory reembed` stamps it, and until then the tier behaves as it did yesterday."""
        from ..teamdb import VECTOR_STAMP_TABLE
        try:
            row = self._conn.execute(
                f"SELECT embedder, dim FROM {VECTOR_STAMP_TABLE} WHERE id=1").fetchone()  # nosec B608
            if not row or len(row) < 2 or not row[0]:
                return None
            return str(row[0]), int(row[1] or 0)
        except Exception:
            # DEGRADE_CLEAN: three shapes, one honest answer. No stamp TABLE at all (a pre-DB.S4
            # provision), a table the role may not read, or a row that is not the (embedder, dim)
            # pair this expects — none of them is a stamp, so all of them read as UNSTAMPED, which
            # is the documented pre-DB.S4 behaviour. Broad because the raisers are the optional
            # psycopg driver's error tree (unnameable at module scope without a hard dependency)
            # plus the decode of a row this code did not write. Note the reading is DELIBERATELY
            # permissive rather than fail-closed: an unreadable stamp is indistinguishable from an
            # index provisioned before stamps existed, and refusing every such index would take the
            # semantic tier away from the users who opted in earliest — a safety feature turned
            # into an outage. A stamp that IS readable and DOES disagree still refuses (that is
            # `verify_stamp`, and it is the case the binding exists for).
            return None

    def write_stamp(self, embedder_id: str, dim: int) -> None:
        """Record `(embedder_id, dim)` as this index's stamp — the LAST step of a re-embed.

        DML, not DDL: the stamp TABLE is created by `team init` (`teamdb.vector_provision_sql`);
        this only upserts its single row, so it runs on the DML-only runtime role the D1 rule
        assumes. An index whose stamp table was never provisioned raises rather than pretending
        the migration completed — an unwritable stamp means the next run cannot tell whether these
        vectors are trustworthy, which is exactly the ambiguity the stamp exists to remove."""
        from ..teamdb import VECTOR_STAMP_TABLE
        self._conn.execute(
            f"INSERT INTO {VECTOR_STAMP_TABLE} (id, embedder, dim) VALUES (1, %s, %s)"  # nosec B608
            " ON CONFLICT (id) DO UPDATE SET embedder=EXCLUDED.embedder, dim=EXCLUDED.dim",
            (str(embedder_id), int(dim)))

    def verify_stamp(self) -> None:
        """Raise `EmbedderStampMismatch` when the stamped embedder is not the configured one.

        An UNSTAMPED index passes (see `read_stamp`). A stamped one must match on BOTH id and
        dim: the dim alone would let two 256-dim models pass for each other, and the id alone
        would miss a model that changed shape under the same name."""
        stamped = self.read_stamp()
        if stamped is None:
            return
        current = (self.embedder_id, self.dim)
        if stamped != current:
            raise EmbedderStampMismatch(stamped, current)

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
        neighbours into a recall.

        DB.S4 — `top_k` is BOUNDED (`MAX_TOP_K`) at the backend, not merely at the caller. This
        query's cost is the caller's `top_k` and `tiered_recall` passes `max(top_k, len(items))`,
        so on a large store an unbounded LIMIT would quietly reinstate the full-store scan the
        index exists to replace. The bound belongs here because this is where the cost is paid."""
        top_k = max(1, min(int(top_k), MAX_TOP_K))
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
                           project: Optional[str] = None,
                           on_unavailable: Optional[Callable[[Exception], None]] = None
                           ) -> Optional["PgVectorBackend"]:
    """Build a pgvector backend from per-tool `config` + an embedder, or None to degrade.
    Honors ONLY `config.dsn_env` (never an inline DSN). Returns None when the env var is
    unset, no embedder is configured, psycopg/pgvector is absent, or the DB is unreachable.
    `project` (Stage 71a) scopes all rows to the current project; None spans all (review).

    DB.S4 — `on_unavailable` receives the typed failure BEFORE the None, mirroring
    `build_postgres_backend`'s D1 signature and for the same reason: a bare None makes an
    `EmbedderStampMismatch` (fix: re-embed) indistinguishable from an unreachable database (fix:
    check the DSN), and the caller can only name the right remediation if it is told which."""
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
    except VectorUnavailable as exc:
        if on_unavailable is not None:
            on_unavailable(exc)
        return None
