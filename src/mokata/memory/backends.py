"""C4 — pluggable memory storage backends (STORAGE ONLY).

All three live behind one `MemoryBackend` contract; the memory *logic* (gating, healing,
toggles, instrumentation) is mokata's own and lives in `store.py`. SQLite is the
guaranteed default floor (stdlib, no dependency). Obsidian (markdown vault) is a real
local adapter. native-memory is an optional adapter delegating to an injected client —
when no client is wired, selection degrades to the SQLite floor (never a hard failure).
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from contextlib import closing, contextmanager
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from ._sqlite import connect_sqlite, is_memory_path
from .item import DEFAULT_TOP_K, MemoryItem
from ..errors import DegradedCapability


# ------------------------------------------------------------------- DB.S2a filter pushdown
def filter_clause_for(mtype: Optional[str] = None,
                      statuses: Optional[Tuple[str, ...]] = None,
                      *, placeholder: str = "?", prefix: str = "WHERE") -> Tuple[str, tuple]:
    """DB.S2a — the ONE source of the `all()` filter semantics, shared by BOTH SQL backends.

    Returns `(clause, params)` for the requested filters (the same shape `_scope()` returns), so
    SQLite and Postgres differ ONLY in the `placeholder` token (`?` vs `%s`) and the `prefix`
    (`WHERE`, or `AND` when a project clause already opened the WHERE). Two hand-written queries
    would be two sets of semantics to keep in step; this is one.

    Only `mtype` and `status` are pushed. They are the ONLY columns proven to be a faithful
    projection of the `doc` JSON: `put()` writes them from `item.mtype`/`item.status` in the very
    statement that writes `doc=json.dumps(item.to_doc())`, so `WHERE mtype=?` can never disagree
    with `MemoryItem.from_dict(doc).mtype`. The v3 scope/precedence columns (`scope_level`,
    `scope_id`, `pin`, `priority`) are deliberately NOT pushed — no write path populates them, so
    every row carries the DDL default while the authoritative value lives in the doc. Filtering on
    them would silently return wrong rows (a cross-tenant visibility bug); activating them needs a
    write-path backfill + schema-min bump, which is DB.S2b's job, not this one.

    Parameterized ONLY: every filter VALUE travels as a bound parameter and never touches the SQL
    string, so a hostile `mtype` is compared, never executed.
    """
    conds: List[str] = []
    params: List[Any] = []
    if mtype is not None:
        conds.append(f"mtype={placeholder}")
        params.append(mtype)
    if statuses is not None:
        if len(statuses) == 0:
            # `status IN ()` is a syntax error in both engines, but the Python filter it replaces
            # (`i.status in ()`) matched NOTHING — so emit a constant-false condition to keep the
            # empty-tuple result set identical rather than raising.
            conds.append("1=0")
        else:
            conds.append("status IN (%s)" % ", ".join([placeholder] * len(statuses)))
            params.extend(statuses)
    if not conds:
        return "", ()
    return f" {prefix} " + " AND ".join(conds), tuple(params)


# ------------------------------------------------------------- DB.S3 lexical (FTS) retrieval
# The lexical tier's MODE, reported honestly (P16) so a caller/doctor can say which engine is
# actually ranking. `jaccard` is the floor — the Python token-overlap DB.S3 replaces.
LEXICAL_MODE_FTS5 = "fts5"
LEXICAL_MODE_TSVECTOR = "tsvector"
LEXICAL_MODE_JACCARD = "jaccard"

# SQLite's index lives beside the rows it indexes, in the SAME governed store file.
FTS_TABLE = "memory_fts"
# The text an FTS row indexes — `subject + value`, i.e. exactly `tiered._text(item)`. `value` is
# NOT a column on either table (it lives inside the `doc` JSON), so both engines project it out of
# the doc: `json_extract` on SQLite, `->>` on Postgres. One definition of "the searchable text".
_SQLITE_TEXT_EXPR = "{p}subject || ' ' || coalesce(json_extract({p}doc, '$.value'), '')"
_PG_TEXT_EXPR = "coalesce(subject, '') || ' ' || coalesce((doc::jsonb->>'value'), '')"
# The text-search configuration. English stemming is Postgres's default and matches FTS5's
# unicode61 tokenizer closely enough that the two tiers agree on what a "term" is.
TS_CONFIG = "english"


def lexical_tokens(query: str) -> List[str]:
    """The ONE tokenizer both FTS dialects are fed from — the same `[a-z0-9]+` word rule the
    Jaccard floor uses (`episodic._WORD`), so switching tiers cannot change what counts as a term.

    It is also the SANITIZER. Both engines have a query LANGUAGE (`"`, `*`, `NEAR`, `:` in FTS5;
    `&`, `|`, `!`, `:*` in tsquery), and a user's recall query is TEXT, not syntax — feeding it
    raw would let an unbalanced quote raise on an innocent search. Reducing to bare word tokens
    means no user input can ever be parsed as an operator. Order-preserving + deduped so the
    emitted query string is deterministic for a given query.
    """
    from .episodic import _WORD
    seen: set = set()
    out: List[str] = []
    for tok in _WORD.findall((query or "").lower()):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def normalize_lexical_scores(raw: List[float], *, higher_is_better: bool) -> List[float]:
    """Map an engine's native relevance scores onto the [0,1] range the fusion weights assume.

    The two engines disagree on both direction and scale: SQLite's `bm25()` is a DISTANCE (more
    negative = better, unbounded), Postgres's `ts_rank` is a positive score (higher = better,
    typically ≪1). The fusion in `tiered.py` multiplies the lexical tier by `LEXICAL_WEIGHT`, which
    only means what it meant while the tier's contribution stays in [0,1] — so each engine is
    normalized against the BEST score in its own result set. Relative gaps are preserved (it is a
    scale, not a rank collapse), so a document that matches twice as strongly still fuses twice as
    strongly.

    `raw` arrives in the engine's own ranked order. A degenerate all-equal run (every score 0 —
    possible when an engine cannot differentiate) falls back to reciprocal rank, which is still
    deterministic and still bounded, rather than dividing by zero.
    """
    if not raw:
        return []
    vals = [float(r) for r in raw] if higher_is_better else [-float(r) for r in raw]
    low = min(vals)
    if low < 0:
        vals = [v - low for v in vals]     # shift a signed scale onto a non-negative one
    top = max(vals)
    if top <= 0:
        return [1.0 / (1 + i) for i in range(len(vals))]
    return [v / top for v in vals]


def _limit_clause(placeholder: str, limit: Optional[int]) -> Tuple[str, tuple]:
    """The optional `LIMIT` fragment — appended AFTER `ORDER BY seq`, so it takes the correct
    seq-ordered N and not an arbitrary N. Parameterized like every other value."""
    if limit is None:
        return "", ()
    return f" LIMIT {placeholder}", (int(limit),)


class MemoryBackend(ABC):
    name: str = ""

    @abstractmethod
    def put(self, item: MemoryItem) -> None: ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[MemoryItem]: ...

    @abstractmethod
    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None) -> List[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: str) -> bool: ...

    def update(self, item: MemoryItem) -> None:
        """Upsert (storage is keyed by id)."""
        self.put(item)

    # --- DB.S3: the lexical-search seam -------------------------------------
    # OPTIONAL by design. A backend that can rank in the database implements `lexical_search` and
    # reports a non-jaccard `lexical_mode`; everything else (Obsidian's files, the injected native
    # client) simply doesn't, and `tiered_recall` uses the Jaccard floor for it — which is that
    # backend's DESIGN, not a degrade, so it must not be reported as one.
    lexical_mode: str = LEXICAL_MODE_JACCARD

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- sqlite
class SQLiteBackend(MemoryBackend):
    name = "sqlite"

    def __init__(self, path: str, name: str = "sqlite") -> None:
        self.path = path
        self.name = name
        # A file-backed DB uses short-lived per-operation connections (see _connect): a
        # persistent handle would keep the .db file open — fine on POSIX, but it blocks
        # deletion on Windows (WinError 32) and leaks a handle. An in-memory DB (":memory:"
        # or a private "") exists ONLY inside its connection, so it MUST keep a persistent
        # one — but it touches no file, so it has no Windows hazard.
        self._memory = is_memory_path(path)
        if not self._memory:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # MS.S4 — every connection (this one and every per-op one below) is opened by the ONE
        # factory, so WAL + the bounded busy_timeout can never be missed at a call site.
        self._mem_conn = connect_sqlite(path) if self._memory else None
        with self._connect() as conn:
            # TM.S6 — the local table gains the same scope + precedence fields as the shared
            # Postgres schema (doc 62 §2–3). A fresh DB creates them here; an existing DB is
            # migrated by `_ensure_scope_columns` (idempotent ADD COLUMN). The authoritative scope
            # value lives in the `doc` JSON (reads use from_dict), so these columns are provisioned
            # for future SQL-side filtering — no runtime read depends on them.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS memory (
                       seq INTEGER PRIMARY KEY AUTOINCREMENT,
                       id TEXT UNIQUE,
                       mtype TEXT,
                       subject TEXT,
                       status TEXT,
                       doc TEXT,
                       scope_level TEXT NOT NULL DEFAULT 'personal',
                       scope_id TEXT,
                       pin INTEGER NOT NULL DEFAULT 0,
                       priority INTEGER NOT NULL DEFAULT 0
                   )"""
            )
            self._ensure_scope_columns(conn)
            # DB.S3 — the FTS5 index + its sync triggers + the backfill of any pre-existing rows.
            # Probed, never assumed: `False` means this sqlite3 has no FTS5 and the lexical tier
            # falls back to the Jaccard floor.
            self._fts = self._ensure_fts(conn)
            conn.commit()

    @property
    def lexical_mode(self) -> str:
        """Which engine is ranking the lexical tier RIGHT NOW (P16 — honest about the tier)."""
        return LEXICAL_MODE_FTS5 if self._fts else LEXICAL_MODE_JACCARD

    @staticmethod
    def fts5_available(conn: Any) -> bool:
        """Is FTS5 compiled into THIS sqlite3? The capability probe, not an assumption.

        FTS5 is a core SQLite extension and is present in CPython's bundled sqlite3 on every
        platform mokata ships to — but it is a COMPILE-TIME option, so a distro build can omit it.
        Probing costs one temp virtual table; assuming costs a crash on someone's machine. A False
        verdict is not an error: the lexical tier degrades to the Jaccard floor (and says so).

        It lives ON `SQLiteBackend`, not at module scope, because it issues DDL — and the D1
        no-runtime-DDL guard exempts exactly this class: the LOCAL floor is a per-repo file mokata
        wholly owns (no roles, no shared DB, nothing to lock down), and the probe table is a `temp.`
        one that dies with the connection. The exemption is the honest home for it.
        """
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.mokata_fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp.mokata_fts5_probe")
            return True
        except Exception:
            # Deliberately broad: the whole POINT is "whatever this sqlite3 does when it lacks
            # FTS5". `sqlite3.OperationalError` is what CPython raises today, but the contract we
            # depend on is "the CREATE did not work" — and there is nothing to act on beyond
            # falling back to the floor, which the caller does.
            return False

    @staticmethod
    def _ensure_fts(conn: Any) -> bool:
        """DB.S3 — provision the FTS5 index and keep it honest. Idempotent; safe on an EXISTING
        populated table (that is the upgrade path, not a special case).

        Three parts, and the ORDER matters:

        1. the virtual table — a standalone FTS5 table keyed by `rowid = memory.seq`, holding the
           searchable text. Standalone rather than `content='memory'` (external-content) because
           the text is not a column on `memory`: `value` lives inside the `doc` JSON, so the index
           must store a projection, and an external-content table can only mirror real columns.
        2. the TRIGGERS — sync lives in the DATABASE, not in `put()`/`delete()`. That is the whole
           anti-drift argument: a row written by ANY client (an older mokata, `psql`, a migration
           script) fires the trigger and lands in the index. Python-side maintenance would only
           cover writes that happened to go through this class, and the index would silently rot.
           `put()`'s `ON CONFLICT DO UPDATE` fires the UPDATE trigger, so an upsert re-indexes.
        3. the BACKFILL — insert only the rows the index is missing (`seq NOT IN (rowid …)`), so a
           pre-DB.S3 store becomes searchable on first open and reopening never duplicates a row.

        SCHEMA POSTURE: this ADDS a virtual table + triggers. It does NOT touch the `memory` table,
        so an older client reads and writes the store exactly as before (it just doesn't query the
        index — and the triggers keep it correct anyway). No schema-version bump, no min-version
        break (the DB.S2b lesson).
        """
        if not SQLiteBackend.fts5_available(conn):
            return False
        new = _SQLITE_TEXT_EXPR.format(p="new.")
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(id UNINDEXED, text)")
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ai AFTER INSERT ON memory BEGIN
                        INSERT INTO {FTS_TABLE}(rowid, id, text)
                        VALUES (new.seq, new.id, {new});
                    END""")
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_au AFTER UPDATE ON memory BEGIN
                        DELETE FROM {FTS_TABLE} WHERE rowid = old.seq;
                        INSERT INTO {FTS_TABLE}(rowid, id, text)
                        VALUES (new.seq, new.id, {new});
                    END""")
            conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {FTS_TABLE}_ad AFTER DELETE ON memory BEGIN
                        DELETE FROM {FTS_TABLE} WHERE rowid = old.seq;
                    END""")
            conn.execute(
                f"""INSERT INTO {FTS_TABLE}(rowid, id, text)
                    SELECT seq, id, {_SQLITE_TEXT_EXPR.format(p="")} FROM memory
                     WHERE seq NOT IN (SELECT rowid FROM {FTS_TABLE})""")
            return True
        except Exception:
            # Broad on purpose and degrade-clean: the probe said FTS5 exists, so anything raising
            # HERE is an environment fault (a store whose `memory_fts` was created with a different
            # shape, a read-only file, a malformed `doc` the backfill's json_extract chokes on).
            # None of it is worth failing a whole store's construction over — the tier drops to the
            # Jaccard floor, which is exactly what the floor is FOR.
            return False

    def lexical_search(self, query: str, top_k: int = DEFAULT_TOP_K
                       ) -> List[Tuple[MemoryItem, float]]:
        """DB.S3 — the lexical tier as ONE ranked SQL query: FTS5 `MATCH` + `bm25()`, top-k in the
        database. This is what replaces `tiered_recall`'s Python `lexical_score` scan over every
        active item — only MATCHING rows are ever materialized, and they arrive already ranked.

        Returns `[(item, score)]` with `score` normalized into [0,1] (bm25 is a distance — see
        `normalize_lexical_scores`). An empty list when FTS is unavailable or the query has no
        terms; the caller treats an empty lexical map as "no lexical signal", never as an error.
        """
        if not self._fts:
            return []
        tokens = lexical_tokens(query)
        if not tokens:
            return []
        # OR (not AND): the tier RANKS, it does not gate — a partial match should surface below a
        # full one, not vanish. Each token is quoted so it is a bare string term, never an operator.
        match = " OR ".join(f'"{t}"' for t in tokens)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT m.doc, bm25({FTS_TABLE}) AS lex_rank
                      FROM {FTS_TABLE}
                      JOIN memory m ON m.seq = {FTS_TABLE}.rowid
                     WHERE {FTS_TABLE} MATCH ?
                     ORDER BY lex_rank ASC, m.seq ASC
                     LIMIT ?""",                                   # nosec B608 (fixed identifiers)
                (match, int(top_k)),
            ).fetchall()
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in rows]
        scores = normalize_lexical_scores([r[1] for r in rows], higher_is_better=False)
        return list(zip(items, scores))

    @staticmethod
    def _ensure_scope_columns(conn: Any) -> None:
        """Idempotently add the TM.S6 scope columns to a pre-existing `memory` table. SQLite has
        no ADD COLUMN IF NOT EXISTS, so we read PRAGMA table_info and ALTER only what's missing."""
        have = {row[1] for row in conn.execute("PRAGMA table_info(memory)").fetchall()}
        for col, ddl in (
            ("scope_level", "scope_level TEXT NOT NULL DEFAULT 'personal'"),
            ("scope_id", "scope_id TEXT"),
            ("pin", "pin INTEGER NOT NULL DEFAULT 0"),
            ("priority", "priority INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in have:
                conn.execute(f"ALTER TABLE memory ADD COLUMN {ddl}")

    @contextmanager
    def _connect(self):
        """A connection scoped to one operation. File-backed DBs open and close per call so no
        OS handle outlives the operation; an in-memory DB reuses its persistent connection
        (closing it would discard the data — and it has no file handle to leak).

        MS.S4 — `busy_timeout` is a PER-CONNECTION pragma, so a per-op connection must set it
        every time; `journal_mode=WAL` is a property of the FILE, so after the first connect it
        re-asserts as a no-op. `connect_sqlite` does both. The per-op close is also what keeps the
        WAL sidecars honest: the LAST connection to close checkpoints and removes `-wal`/`-shm`,
        so at rest the store is a single complete `memory.db`."""
        if self._memory:
            yield self._mem_conn
        else:
            with closing(connect_sqlite(self.path)) as conn:
                yield conn

    def put(self, item: MemoryItem) -> None:
        doc = json.dumps(item.to_doc())   # D6 — the durable serializer: refuses a newer-than-us doc
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO memory (id, mtype, subject, status, doc)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       mtype=excluded.mtype, subject=excluded.subject,
                       status=excluded.status, doc=excluded.doc""",
                (item.id, item.mtype, item.subject, item.status, doc),
            )
            conn.commit()

    def get(self, item_id: str) -> Optional[MemoryItem]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT doc FROM memory WHERE id=?", (item_id,)
            ).fetchone()
        return MemoryItem.from_dict(json.loads(row[0])) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None) -> List[MemoryItem]:
        """DB.S2a — filters in the DB, not in Python. This used to `SELECT doc FROM memory ORDER BY
        seq` and then drop rows in a list comprehension, i.e. pull the WHOLE table over the wire
        (and through `from_dict`) on every recall. Now the filter is a WHERE and only matching rows
        are ever materialized. Result set + order are unchanged: same `ORDER BY seq`, and the
        `mtype`/`status` columns are a faithful projection of the doc (see `filter_clause_for`).

        There is no `project` column on the local table — a local store IS one project (the DB file
        lives in the repo), so unlike Postgres there is nothing to scope by. Not an omission.
        """
        # The B608 suppression below is the same false positive the Postgres half carries: the SQL
        # interpolates ONLY builder-generated fragments made of fixed column names and `?`
        # placeholders. Every VALUE is bound, never formatted in.
        clause, params = filter_clause_for(mtype, statuses, placeholder="?")
        tail, tail_params = _limit_clause("?", limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT doc FROM memory{clause} ORDER BY seq{tail}",  # nosec B608
                (*params, *tail_params),
            ).fetchall()
        return [MemoryItem.from_dict(json.loads(r[0])) for r in rows]

    def delete(self, item_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memory WHERE id=?", (item_id,))
            conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        # File-backed DBs hold no persistent handle (per-operation connections). An in-memory
        # DB keeps one — close it here (its data is discarded, which is the point).
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None


# ------------------------------------------------------------------------- obsidian
_FENCE = "```"


class ObsidianBackend(MemoryBackend):
    """Stores each item as a human-readable markdown note in a vault directory; the
    authoritative item dict lives in a fenced JSON block so edges round-trip exactly."""

    name = "obsidian"

    def __init__(self, vault: str, name: str = "obsidian") -> None:
        self.vault = vault
        self.name = name
        os.makedirs(vault, exist_ok=True)

    def _path(self, item_id: str) -> str:
        return os.path.join(self.vault, f"{item_id}.md")

    def put(self, item: MemoryItem) -> None:
        body = (
            f"# memory: {item.subject}  ({item.mtype})\n\n"
            f"{item.value}\n\n"
            f"{_FENCE}json\n{json.dumps(item.to_doc(), indent=2)}\n{_FENCE}\n"
        )
        with open(self._path(item.id), "w", encoding="utf-8") as fh:
            fh.write(body)

    @staticmethod
    def _parse(text: str) -> Optional[MemoryItem]:
        start = text.find(_FENCE + "json")
        if start == -1:
            return None
        start = text.find("\n", start) + 1
        end = text.find(_FENCE, start)
        if end == -1:
            return None
        return MemoryItem.from_dict(json.loads(text[start:end]))

    def get(self, item_id: str) -> Optional[MemoryItem]:
        path = self._path(item_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return self._parse(fh.read())

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None) -> List[MemoryItem]:
        # DB.S2a — a vault is FILES, not a queryable store: there is no WHERE to push into, so the
        # filter stays in Python here. Only the SQL backends gained the pushdown; the contract
        # (including `limit`) is uniform so callers never branch on backend.
        items: List[MemoryItem] = []
        for fn in sorted(os.listdir(self.vault)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(self.vault, fn), encoding="utf-8") as fh:
                it = self._parse(fh.read())
            if it is not None:
                items.append(it)
        # stable order by creation time, then id
        items.sort(key=lambda i: (i.created_at, i.id))
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items[:limit] if limit is not None else items

    def delete(self, item_id: str) -> bool:
        path = self._path(item_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


# -------------------------------------------------------------------- native-memory
class MemoryClient(Protocol):
    """Contract for the Anthropic native memory tool (or any external store). The
    adapter delegates storage entirely to this; mokata's logic stays in the store."""

    def put(self, doc: Dict[str, Any]) -> None: ...
    def get(self, item_id: str) -> Optional[Dict[str, Any]]: ...
    def all(self) -> List[Dict[str, Any]]: ...
    def delete(self, item_id: str) -> bool: ...


class NativeMemoryBackend(MemoryBackend):
    name = "native-memory"

    def __init__(self, client: MemoryClient, name: str = "native-memory") -> None:
        self.client = client
        self.name = name

    def put(self, item: MemoryItem) -> None:
        self.client.put(item.to_doc())

    def get(self, item_id: str) -> Optional[MemoryItem]:
        doc = self.client.get(item_id)
        return MemoryItem.from_dict(doc) if doc else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None) -> List[MemoryItem]:
        # DB.S2a — the injected `MemoryClient.all()` takes no filter arguments (it is someone
        # else's contract, not ours to widen), so the filter stays in Python on this adapter.
        items = [MemoryItem.from_dict(d) for d in self.client.all()]
        items.sort(key=lambda i: (i.created_at, i.id))
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items[:limit] if limit is not None else items

    def delete(self, item_id: str) -> bool:
        return self.client.delete(item_id)


# -------------------------------------------------------------------------- postgres
def _with_revision(doc: str, revision: Any) -> MemoryItem:
    """TM.S5c — rebuild an item from its `doc` and stamp the shared-row `revision` onto it as a
    TRANSIENT attribute (`_revision`, NOT a model field, so it never enters `to_dict`/the stored
    doc). The store reads it as the compare-and-set base for a subsequent gated update/delete;
    an item from a backend that doesn't track revisions simply has no `_revision` (base=None)."""
    item = MemoryItem.from_dict(json.loads(doc))
    item._revision = revision
    return item


class PostgresUnavailable(DegradedCapability):
    """Raised when the Postgres backend can't be built — psycopg missing or the DB
    unreachable. The caller catches this and degrades to the SQLite floor (never a
    hard failure: 'degrade, never break')."""


class PostgresBackend(MemoryBackend):
    """The team's LIVE shared memory store — a hosted/remote backend (`kind: "external"`) that
    implements the full `MemoryBackend` contract (put/upsert/get/all/delete/close), so pointing a
    whole team's mokata at one Postgres DSN makes everyone read/write the same store live.
    `autocommit` is on so one client's committed write is immediately visible to the others;
    conflicts are surfaced (not silently merged) by the store's self-healing layer, writes stay
    human-gated (P2) and provenance-carrying, and the adapter is trust-dialed (P15) — this class
    is storage only, the policy lives in the store.

    D1: it does NOT own its schema. It used to re-run a hand-mirrored `CREATE TABLE …` on every
    connect, which a DML-only role is denied even on a provisioned DB — so a locked-down team
    silently fell back to SQLite. `team init` (teamdb) owns the schema; this backend VERIFIES it
    (one cached probe) and raises `PostgresUnavailable` carrying `failure_class` + the exact
    `mokata team init` fix when it is absent or out of range — so the fallback is LOUD (CM.S2).

    Opt-in / local-first (P8): nothing connects unless the user wires `config.dsn_env`. The
    DSN comes from that env var (never inline in the committed manifest). `psycopg` is an
    optional extra, lazy-imported; its absence — or an unreachable database — raises
    `PostgresUnavailable` so selection degrades to the SQLite floor, never a hard failure."""

    name = "postgres"
    # mokata-OWNED, namespaced schema (Stage 39): a dedicated table, never the generic `memory`,
    # so mokata's store can't collide with an app's own `memory` table in a shared database.
    TABLE = "mokata_memory"

    def __init__(self, dsn: Optional[str] = None, name: str = "postgres",
                 project: Optional[str] = None, conn: Any = None) -> None:
        self.name = name
        # Stage 71a — SCOPE every row by the current project key so one shared DSN safely hosts
        # many projects. `project=None` spans ALL projects (review `--all` only). The CREATE gains
        # the `project` column; the ADD-COLUMN-IF-MISSING migrates a pre-71a table (its old rows
        # read back as legacy/unscoped under `--all`). `conn` injects a connection (tests / a
        # host-provided client) so the scoping is exercisable without a live DB.
        self.project = project
        if conn is not None:
            self._conn = conn                     # an injected connection: already provisioned.
            return
        from ._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, PostgresUnavailable)

    def _scope(self, prefix: str = "AND") -> Tuple[str, tuple]:
        """The project WHERE fragment (empty when spanning all projects)."""
        if self.project is None:
            return "", ()
        return f" {prefix} project=%s", (self.project,)

    # Justification for the B608 suppressions below (bandit false positive): every SQL string
    # here interpolates ONLY the mokata-OWNED constant `self.TABLE` (+ a fixed `_scope()`
    # fragment) — never user input. All VALUES go through the driver's parameterized `%s`
    # placeholders, so there is no injection surface. Suppression markers only, no behaviour change.
    def put(self, item: MemoryItem) -> None:
        self._conn.execute(
            f"INSERT INTO {self.TABLE} (id, mtype, subject, status, doc, project)"  # nosec B608
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET mtype=EXCLUDED.mtype,"
            " subject=EXCLUDED.subject, status=EXCLUDED.status, doc=EXCLUDED.doc,"
            " project=EXCLUDED.project",
            (item.id, item.mtype, item.subject, item.status,
             json.dumps(item.to_doc()), self.project),   # D6 — refuses a newer-than-us doc
        )

    def get(self, item_id: str) -> Optional[MemoryItem]:
        clause, params = self._scope()
        row = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE} WHERE id=%s{clause}",  # nosec B608
            (item_id, *params)).fetchone()
        return _with_revision(row[0], row[1]) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None) -> List[MemoryItem]:
        """DB.S2a — the mtype/status filter is pushed into the WHERE (see the SQLite twin). The
        `project` scoping is UNCHANGED: `_scope()` already put it in the WHERE at Stage 71a, and it
        stays the FIRST condition so its bound parameter keeps leading `params`."""
        scope, scope_params = self._scope(prefix="WHERE")
        clause, params = filter_clause_for(
            mtype, statuses, placeholder="%s", prefix="AND" if scope else "WHERE")
        tail, tail_params = _limit_clause("%s", limit)
        rows = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE}{scope}{clause} ORDER BY seq{tail}",  # nosec B608
            (*scope_params, *params, *tail_params)).fetchall()
        return [_with_revision(r[0], r[1]) for r in rows]

    def delete(self, item_id: str) -> bool:
        clause, params = self._scope()
        cur = self._conn.execute(
            f"DELETE FROM {self.TABLE} WHERE id=%s{clause}", (item_id, *params))  # nosec B608
        return cur.rowcount > 0

    # DB.S3 — the tsvector tier. CORE Postgres: `to_tsvector`/`to_tsquery`/`ts_rank` are built in,
    # so this needs NO `CREATE EXTENSION` (unlike the opt-in pgvector tier) and therefore stays on
    # the ADR-54 vanilla-Postgres golden path.
    lexical_mode = LEXICAL_MODE_TSVECTOR

    def lexical_search(self, query: str, top_k: int = DEFAULT_TOP_K
                       ) -> List[Tuple[MemoryItem, float]]:
        """DB.S3 — the lexical tier as ONE ranked SQL query: `@@` for the match, `ts_rank` for the
        order, top-k in the database. Replaces the Python Jaccard scan (see the SQLite twin).

        NO RUNTIME DDL (D1/C4): the tsvector is computed from the row's own columns, so this runs
        correctly on a DML-only role against a table that has never been touched by `team init`.
        The GIN expression index provisioned by `team init` (`teamdb.provision_sql`) makes it FAST;
        its absence makes it SLOW, not wrong — so "index missing" degrades in performance only,
        with nothing to report and nothing to build at runtime.

        SCOPE: `_scope()` — the SAME project predicate every other read uses — is ANDed onto the
        match, so an FTS hit in project B can never surface in project A's recall. The FTS
        predicate composes WITH the visibility filter; it never replaces it.
        """
        tokens = lexical_tokens(query)
        if not tokens:
            return []
        # `|` (OR), matching the SQLite twin: the tier ranks, it does not gate. Tokens are bare
        # words (see `lexical_tokens`), and the whole tsquery travels as a BOUND parameter — a
        # hostile query is parsed as search terms by Postgres, never executed as SQL.
        tsquery = " | ".join(tokens)
        vec = f"to_tsvector('{TS_CONFIG}', {_PG_TEXT_EXPR})"
        tsq = f"to_tsquery('{TS_CONFIG}', %s)"
        scope, scope_params = self._scope(prefix="AND")
        rows = self._conn.execute(
            f"SELECT doc, revision, ts_rank({vec}, {tsq}) AS lex_rank"      # nosec B608
            f"  FROM {self.TABLE}"
            f" WHERE {vec} @@ {tsq}{scope}"
            f" ORDER BY lex_rank DESC, seq ASC LIMIT %s",
            (tsquery, tsquery, *scope_params, int(top_k)),
        ).fetchall()
        items = [_with_revision(r[0], r[1]) for r in rows]
        scores = normalize_lexical_scores([r[2] for r in rows], higher_is_better=True)
        return list(zip(items, scores))

    def list_projects(self) -> List[str]:
        """The distinct project keys present in the shared table — for review `--list-projects`.
        A pre-71a (NULL) row reads back as the LEGACY bucket. Never raises on an odd row."""
        rows = self._conn.execute(
            f"SELECT DISTINCT project FROM {self.TABLE}").fetchall()  # nosec B608
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


def _distinct_projects(rows: List[tuple]) -> List[str]:
    """Normalize a `SELECT DISTINCT project` result: NULL/empty → the LEGACY bucket; sorted."""
    from ..project import LEGACY_PROJECT
    out = {(r[0] if r and r[0] else LEGACY_PROJECT) for r in rows}
    return sorted(out)


def build_postgres_backend(config: Dict[str, Any], project: Optional[str] = None,
                           on_unavailable: Optional[Callable[[Exception], None]] = None
                           ) -> Optional["PostgresBackend"]:
    """Build a Postgres backend from a per-tool `config`, or return None to degrade.

    Honors ONLY `config.dsn_env` — the name of an env var holding the DSN. An inline
    `dsn` is never read (the manifest is committed; a plaintext credential would be a
    leak the secret-guard blocks). Returns None when the env var is unset/empty, psycopg
    is absent, or the database is unreachable — so the caller falls to the SQLite floor.
    `project` (Stage 71a) SCOPES all rows to the current project; None spans all (review).

    D1: `on_unavailable` receives the typed failure (carrying `failure_class` + `fix`) before the
    None. Returning a bare None is exactly how a denied CREATE became an indistinguishable
    "degraded somehow" — the caller needs to know it was the SCHEMA, so the notice can say
    `mokata team init` instead of telling a user to sync a connection that is perfectly healthy.
    """
    dsn_env = (config or {}).get("dsn_env")
    if not dsn_env:
        return None
    # Route the env-var read through the ONE resolver (CM.S1) so reads resolve the DSN via the
    # SAME path health/flush/sync use — the configured name can never split the two again (C-1).
    from ..dsn import resolve_dsn
    res = resolve_dsn(override=dsn_env)
    if not res.is_set:
        return None
    try:
        return PostgresBackend(res.dsn, project=project)
    except PostgresUnavailable as exc:
        if on_unavailable is not None:
            on_unavailable(exc)
        return None
