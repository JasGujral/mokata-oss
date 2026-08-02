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
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from . import edges as _edges
from ._sqlite import connect_sqlite, is_memory_path
from .item import DEFAULT_TOP_K, MemoryItem, now_iso
from .lifecycle import (
    HIT_COUNT_COLUMN,
    LAST_RECALLED_AT_COLUMN,
    VALID_FROM_COLUMN,
    VALID_TO_COLUMN,
)
from ..errors import DegradedCapability


# ------------------------------------------------------------------- DB.S2a filter pushdown
# DB.S2b — how a scope column is READ, in one place. The columns are nullable/defaulted at the DDL
# level while `scope._item_scope` normalizes in Python with `or DEFAULT_SCOPE_LEVEL` / `or ""`; SQL
# must apply the SAME normalization or the two would disagree on a legacy row. `nullif(…, '')`
# folds the empty string into NULL so `coalesce` can supply the model's default — so a NULL column,
# an empty column and an absent one all read as `personal`/`''`, exactly as Python reads them.
_SCOPE_LEVEL_EXPR = "coalesce(nullif(scope_level, ''), 'personal')"
_SCOPE_ID_EXPR = "coalesce(scope_id, '')"

# DB.S2b — the LOCAL store's backfill stamp, written to `PRAGMA user_version` (see
# `SQLiteBackend._backfill_scope_columns`). It marks "the scope columns in this file are a faithful
# projection of the docs", which is the same claim `teamdb.SCOPE_BACKFILLED_COLUMN` makes for a
# shared store. Bump it if a later stage changes what the columns must contain — a store stamped
# with an older generation re-runs the migration rather than being trusted.
_SCOPE_BACKFILL_STAMP = 1

# DB.S5 — the LOCAL store's v4 stamp, in the SAME `PRAGMA user_version` field and on the SAME
# monotonic scale. The generations are CUMULATIVE, not exclusive: each migration runs when the
# stamp is BELOW its own generation, and the last one to run leaves the field at the highest
# generation, so a store at 2 has provably had generation 1 AND 2 applied. That is why the scope
# migration's guard is `>= 1` rather than `== 1` — writing the v4 stamp must not make the store
# look un-backfilled for scope.
#
# The whole point of copying this seam rather than inventing a second one: a v3 store (stamp 1)
# opens, sees 1 < 2, adds the four lifecycle columns, backfills the window, and stamps 2 — the
# DB.S2b upgrade discipline, unchanged, one field, one scale.
_LIFECYCLE_BACKFILL_STAMP = 2

# DB.S7a — the LOCAL store's v5 stamp, in the SAME `PRAGMA user_version` field on the SAME
# cumulative scale. The third generation, and the third time this seam is REUSED rather than
# re-invented: a store at 2 opens, sees 2 < 3, creates `memory_edges`, migrates the three implicit
# doc-JSON edge kinds into it, and stamps 3. Because the generations are cumulative and every
# guard reads `>=`, a store at 3 has provably had 1, 2 AND 3 applied — the scope pushdown stays on
# and the lifecycle window stays backfilled, which is exactly what a second `user_version` field
# (or a second mechanism) would have put at risk.
_EDGES_BACKFILL_STAMP = 3


def scope_columns_from_doc(doc: Dict[str, Any]) -> tuple:
    """DB.S2b — the four scope/precedence column VALUES for a doc, in DDL order
    (`scope_level, scope_id, pin, priority`). THE one definition, used by every write path
    (SQLite, Postgres, the team-journal CAS flush), so no two of them can project the same item
    differently.

    It takes the DOC, not the item, and that is the point: each write path passes the very dict it
    is about to `json.dumps` into the `doc` column, so the columns are derived from the same object
    in the same statement. There is no arrangement of the code in which they disagree.

    Normalized exactly as `scope._item_scope` normalizes on read (`or DEFAULT_SCOPE_LEVEL` /
    `or ""`), so an item with an empty `scope_level` lands on `personal` in the column just as it
    reads as `personal` in Python — the SQL and the Python filter must agree on legacy rows or the
    pushdown changes results.

    `pin` is a real Python `bool`, NOT an int, and that distinction is load-bearing rather than
    cosmetic: the shared column is `BOOLEAN`, and Postgres rejects an integer for it outright
    (`DatatypeMismatch: column "pin" is of type boolean but expression is of type smallint`) — it
    does no implicit int→bool cast. SQLite has no boolean type and stores a Python bool as 0/1, so
    `bool` is the one value both engines accept. (A SQLite-shaped test double accepts the int
    happily, which is why this is pinned by the live-Postgres leg in
    tests/integration/test_db_s2b_live_db.py rather than by a shim.)
    """
    from .scope import DEFAULT_SCOPE_LEVEL
    return (doc.get("scope_level") or DEFAULT_SCOPE_LEVEL,
            doc.get("scope_id") or "",
            bool(doc.get("pin", False)),
            int(doc.get("priority") or 0))


def validity_columns_from_doc(doc: Dict[str, Any]) -> tuple:
    """DB.S5 — the two bi-temporal validity column VALUES for a doc, in DDL order
    (`valid_from, valid_to`). The exact twin of `scope_columns_from_doc`, and for the same reason:
    it takes the DOC the write path is about to serialize, so the columns are derived from the same
    object in the same statement and cannot drift from it.

    `valid_from` falls back to the doc's `provenance.created_at` — the ONE place the
    `lifecycle.open_window` rule is expressed in SQL-facing terms, so a column read and a Python
    read of an item that never carried an explicit window agree on when that window opened. An open
    window writes `valid_to` as NULL rather than `''`: NULL is what "no end" means to both engines'
    ordering and aggregation, and the read side (`lifecycle.is_open`) treats both as open anyway.

    The USAGE columns (`hit_count`/`last_recalled_at`) are deliberately absent from this function
    and from every write path that calls it. They are transient run-state and are never projected
    from a doc — a `put()` that carried them would reset a live item's usage counter to whatever
    stale value its in-memory doc happened to hold. They are written ONLY by `record_usage`.
    """
    provenance = doc.get("provenance") or {}
    valid_from = doc.get("valid_from") or provenance.get("created_at") or ""
    return (valid_from, doc.get("valid_to") or None)


def scope_clause_for(path: Sequence[Any], *, placeholder: str = "?",
                     qualifier: str = "") -> Tuple[str, tuple]:
    """DB.S2b — the scope-path predicate: `scope.on_path()` expressed as SQL, condition for
    condition.

    `on_path` is the SPEC — this must not invent its own semantics, because any divergence is a
    visibility bug in one direction or the other (rows silently missing, or another tenant's rows
    silently visible). The mapping, per ref on the broad→narrow path, OR-ed together because the
    read is a UNION over the path (doc 62 §2 — additive accumulation, broader context is never
    dropped):

      * `global`             -> the level alone; a global ref matches any id (`on_path` returns
                                True on the level match without consulting `sid`);
      * a ref with no id     -> the level alone; `ref.id is None` means "match any id at this
                                level" (the any-user personal read);
      * a ref with an id     -> level AND id;
      * `personal` with an id-> level AND (id OR ''), because a legacy/local-default item carries
                                an empty `scope_id` and matches ANY personal reader.

    An EMPTY path yields `1=0`, not the empty string. `union_read([], …)` over an empty path
    returns nothing, so "no rows" is the faithful answer; emitting no clause would degrade to "no
    filter" and return the WHOLE table — failing open on a visibility predicate, which is the one
    failure mode this must never have.

    Parameterized ONLY, exactly like the mtype/status half: every scope id travels bound.
    """
    if not path:
        return "1=0", ()
    from .scope import GLOBAL, PERSONAL
    # R-1 (DB.S8) — `qualifier` prefixes each column when this predicate is composed into a
    # JOIN, where a bare `scope_level` would be ambiguous. Built from mokata constants only;
    # the caller never supplies a column name.
    level_expr = _SCOPE_LEVEL_EXPR.replace("scope_level", qualifier + "scope_level")
    id_expr = _SCOPE_ID_EXPR.replace("scope_id", qualifier + "scope_id")

    ors: List[str] = []
    params: List[Any] = []
    for ref in path:
        level = getattr(ref, "level", None)
        ref_id = getattr(ref, "id", None)
        if level == GLOBAL or ref_id is None:
            ors.append(f"{level_expr}={placeholder}")
            params.append(level)
            continue
        if level == PERSONAL:
            ors.append(f"({level_expr}={placeholder} AND "
                       f"({id_expr}={placeholder} OR {id_expr}=''))")
            params.extend([level, ref_id])
            continue
        ors.append(f"({level_expr}={placeholder} AND {id_expr}={placeholder})")
        params.extend([level, ref_id])
    return "(" + " OR ".join(ors) + ")", tuple(params)


def _in_clause(column: str, values: Sequence[Any], placeholder: str) -> Tuple[str, tuple]:
    """`column IN (…)` with every value BOUND. An EMPTY `values` yields `1=0`, never the empty
    string — for the same reason `filter_clause_for` maps an empty `statuses` to `1=0`: the Python
    membership test it stands in for matched nothing, and degrading to "no condition" would widen
    the read to the whole table."""
    if not values:
        return "1=0", ()
    return (f"{column} IN (%s)" % ", ".join([placeholder] * len(values))), tuple(values)


def filter_clause_for(mtype: Optional[str] = None,
                      statuses: Optional[Tuple[str, ...]] = None,
                      *, scope_path: Optional[Sequence[Any]] = None,
                      ids: Optional[Sequence[str]] = None,
                      subjects: Optional[Sequence[str]] = None,
                      kinds: Optional[Sequence[str]] = None,
                      kind_expr: Optional[str] = None,
                      placeholder: str = "?", prefix: str = "WHERE",
                      qualifier: str = "") -> Tuple[str, tuple]:
    """DB.S2a/DB.S2b — the ONE source of the `all()` filter semantics, shared by BOTH SQL backends.

    Returns `(clause, params)` for the requested filters (the same shape `_scope()` returns), so
    SQLite and Postgres differ ONLY in the `placeholder` token (`?` vs `%s`) and the `prefix`
    (`WHERE`, or `AND` when a project clause already opened the WHERE). Two hand-written queries
    would be two sets of semantics to keep in step; this is one.

    Everything pushed here is a faithful projection of the `doc` JSON, and that is the ONLY reason
    it may be pushed. `put()` writes `mtype`/`status` AND — since DB.S2b — `scope_level`/
    `scope_id`/`pin`/`priority` in the very statement that writes `doc=json.dumps(item.to_doc())`,
    so `WHERE mtype=?` can never disagree with `MemoryItem.from_dict(doc).mtype`, and neither can
    the scope columns.

    THE ORDERING THAT MAKES THE SCOPE HALF SAFE, because it is the whole hazard: until DB.S2b no
    write path populated the scope columns, so every row carried the DDL default while the real
    value sat in the doc — filtering on them would have returned wrong rows (a cross-tenant
    visibility bug). Population alone does not fix rows written BEFORE it; the backfill does
    (`SQLiteBackend._backfill_scope_columns`, `teamdb.provision_sql`). So a scope predicate is
    emitted only when the CALLER passes a `scope_path`, and a caller passes one only when its store
    reports `supports_scope_pushdown` — which is true only once that store's backfill has run. A
    store that hasn't been backfilled gets NO scope clause and every row back, and the caller's
    `scope.union_read` filters from the doc: slower, and correct. Never a stale-column filter.

    `pin`/`priority` are populated and backfilled alongside the other two, but nothing FILTERS on
    them — they are precedence inputs that `precedence.resolve_items` consumes after the read, not
    visibility predicates. They are a faithful projection now, so pushing them later needs no
    further migration.

    Parameterized ONLY: every filter VALUE travels as a bound parameter and never touches the SQL
    string, so a hostile `mtype` — or a hostile scope id — is compared, never executed.
    """
    conds: List[str] = []
    params: List[Any] = []
    if mtype is not None:
        conds.append(f"{qualifier}mtype={placeholder}")
        params.append(mtype)
    if statuses is not None:
        if len(statuses) == 0:
            # `status IN ()` is a syntax error in both engines, but the Python filter it replaces
            # (`i.status in ()`) matched NOTHING — so emit a constant-false condition to keep the
            # empty-tuple result set identical rather than raising.
            conds.append("1=0")
        else:
            conds.append(f"{qualifier}status IN (%s)"
                         % ", ".join([placeholder] * len(statuses)))
            params.extend(statuses)
    if scope_path is not None:
        scope_cond, scope_params = scope_clause_for(scope_path, placeholder=placeholder,
                                                   qualifier=qualifier)
        conds.append(scope_cond)
        params.extend(scope_params)
    # JIT-STAMP-SEAM — the KIND predicate, emitted only when the caller both asks for kinds AND
    # hands over its engine's `effective_kind` expression. No expression ⇒ no clause: a backend
    # that has not been taught to spell it silently returns every kind, and the caller's own
    # Python filter (`tiered_recall`) is what actually enforces the rule. That is the same
    # optimization-not-definition posture the scope predicate carries — the difference here is
    # that the expression reads the `doc` directly, so it needs no population and no backfill.
    if kinds is not None and kind_expr:
        if len(kinds) == 0:
            conds.append("1=0")               # `IN ()` is a syntax error; `kind in ()` matched nothing
        else:
            expr = kind_expr.format(p=qualifier)
            conds.append(f"{expr} IN (%s)" % ", ".join([placeholder] * len(kinds)))
            params.extend(kinds)
    # R-1 (DB.S8) — the CANDIDATE predicate. `ids` and `subjects` are OR-ed with each other and
    # AND-ed with everything above, because they answer two halves of one question: "the rows the
    # tiers nominated" plus "the rows that COMPETE with them for precedence".
    #
    # The second half is not an optimisation, it is a correctness requirement, and it is the
    # subtlest thing in this function. `precedence.resolve_items` collapses a scope union to one
    # winner per `item.subject`. Hydrating only the nominated ids would hand it a partial group —
    # so a narrow-scope item that LOSES to a broader pinned one would be returned as a winner
    # purely because its winner was not nominated. Reading the whole group keeps the resolution
    # exactly what it was when the full set was materialized.
    if ids is not None or subjects is not None:
        halves: List[str] = []
        if ids is not None:
            cond, ps = _in_clause(qualifier + "id", ids, placeholder)
            halves.append(cond)
            params.extend(ps)
        if subjects is not None:
            cond, ps = _in_clause(qualifier + "subject", subjects, placeholder)
            halves.append(cond)
            params.extend(ps)
        conds.append("(" + " OR ".join(halves) + ")")
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
# JIT-STAMP-SEAM — `effective_kind` in SQL, i.e. `item.py:485`'s `self.kind or self.mtype`, spelled
# once per engine. `kind` is NOT a column on either table (it lives in the `doc` JSON) and this
# deliberately does NOT add one: a projected column would need population + a backfill + a
# capability gate before it could be filtered on (the whole DB.S2b hazard `filter_clause_for`
# documents above), whereas reading the doc IS the doc, so the predicate cannot disagree with
# `MemoryItem.from_dict(doc).effective_kind` by construction. `nullif(…, '')` is what makes it
# `or` rather than `coalesce` — an item whose `kind` is the empty string falls back to `mtype` in
# Python, and would not in SQL without it.
_SQLITE_KIND_EXPR = "coalesce(nullif(json_extract({p}doc, '$.kind'), ''), {p}mtype)"
_PG_KIND_EXPR = "coalesce(nullif(doc::jsonb->>'kind', ''), mtype)"
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
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: str) -> bool: ...

    # --- DB.S2b: the scope-pushdown seam ------------------------------------
    # OFF by default, and the default is the SAFE one. A backend advertises this only if it can
    # filter on scope columns it KNOWS are a faithful projection of each item's doc; a caller
    # passes `scope_path` only to a backend that advertises it. Everything else — a vault of
    # files, an injected native client, a shared table whose backfill hasn't run — leaves it False
    # and gets the whole (correctly unfiltered) set back, which `scope.union_read` then narrows
    # from the doc. The cost of False is a slower read; the cost of a wrongly-True is another
    # tenant's rows going missing, so False is where an unknown belongs.
    supports_scope_pushdown: bool = False

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
        # DB.S7a (E7) — how many item→item edge refs the v5 migration skipped on THIS store because
        # their target item is absent. Set by the constructor below; readable afterwards so the skip
        # is REPORTED rather than silently dropped.
        self.edge_backfill_skipped = 0
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
                       priority INTEGER NOT NULL DEFAULT 0,
                       valid_from TEXT,
                       valid_to TEXT,
                       hit_count INTEGER NOT NULL DEFAULT 0,
                       last_recalled_at TEXT
                   )"""
            )
            # R-1 (DB.S8) — the precedence-group read (`hydrate(subjects=…)`) filters on `subject`,
            # which carried no index: a bounded candidate read that then scanned the table to find
            # each nominee's precedence group would have moved the scan rather than removed it.
            # `id` already has one (it is UNIQUE in the CREATE above), so this is the only one
            # Option A adds. IF NOT EXISTS, so an existing store gains it on its next open.
            conn.execute("CREATE INDEX IF NOT EXISTS memory_subject ON memory(subject)")
            self._ensure_scope_columns(conn)
            # DB.S2b — the columns exist; now make them TRUE of every row. Ordered deliberately:
            # add-then-backfill, both before the constructor returns, so nothing can query this
            # store before its scope columns are a faithful projection of the docs.
            self._backfill_scope_columns(conn)
            # DB.S5 — the v4 half, in the SAME add-then-backfill order and for the same reason.
            # A v3 store reaches these two lines with its scope work already done above; a fresh
            # store finds the columns already in the CREATE and both calls become no-ops.
            self._ensure_lifecycle_columns(conn)
            self._backfill_lifecycle_columns(conn)
            # DB.S7a — the v5 half, in the SAME add-then-backfill order, for the third time. The
            # skip count is kept on the instance (never swallowed) so E7's report has somewhere to
            # come from; it is non-zero only on the ONE open that actually ran the migration.
            self._ensure_edges(conn)
            self.edge_backfill_skipped = self._backfill_edges(conn)
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

    def lexical_search(self, query: str, top_k: int = DEFAULT_TOP_K,
                       *, scope_path: Optional[Sequence[Any]] = None,
                       statuses: Optional[Tuple[str, ...]] = None,
                       kinds: Optional[Sequence[str]] = None
                       ) -> List[Tuple[MemoryItem, float]]:
        """DB.S3 — the lexical tier as ONE ranked SQL query: FTS5 `MATCH` + `bm25()`, top-k in the
        database. This is what replaces `tiered_recall`'s Python `lexical_score` scan over every
        active item — only MATCHING rows are ever materialized, and they arrive already ranked.

        JIT-STAMP-SEAM — `kinds` travels with the ranked query for the SAME reason `scope_path`
        does: the LIMIT must be taken over rows the caller can actually use. The per-turn injection
        wants the top-k of `JIT_KINDS`, and on a store whose best lexical matches are rules and
        decisions, a top-k taken across ALL kinds and filtered afterwards returns fewer items than
        exist — the filter would silence the channel rather than bound it.

        Returns `[(item, score)]` with `score` normalized into [0,1] (bm25 is a distance — see
        `normalize_lexical_scores`). An empty list when FTS is unavailable or the query has no
        terms; the caller treats an empty lexical map as "no lexical signal", never as an error.

        R-1 (DB.S8) — `scope_path` and `statuses` now travel WITH the ranked query, and that is the
        load-bearing change rather than a tidy-up. Before it, this returned the store's global
        top-N and the caller intersected the result with a separately-materialized visible set —
        which is correct, but it means the LIMIT is taken before visibility is known. On a shared
        100k store whose reader may see a fraction of it, the top 50 rows by rank can be entirely
        another tenant's, and the intersection then yields NOTHING while the reader's own matching
        rows sit unread below the cut. Pushing the predicate INTO the ranked query makes the
        top-N a top-N of the rows this identity may actually read.

        Same posture as everywhere else in this file: the predicate is emitted only when a caller
        passes a path, and a caller passes one only for a store reporting `supports_scope_pushdown`.
        """
        if not self._fts:
            return []
        tokens = lexical_tokens(query)
        if not tokens:
            return []
        # OR (not AND): the tier RANKS, it does not gate — a partial match should surface below a
        # full one, not vanish. Each token is quoted so it is a bare string term, never an operator.
        match = " OR ".join(f'"{t}"' for t in tokens)
        # Built by the SHARED builder, prefixed `AND` because the FTS MATCH already opened the
        # WHERE. Every column it names is on `m`, the memory table the join already carries.
        clause, params = filter_clause_for(None, statuses, scope_path=scope_path,
                                           kinds=kinds, kind_expr=_SQLITE_KIND_EXPR,
                                           placeholder="?", prefix="AND", qualifier="m.")
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT m.doc, bm25({FTS_TABLE}) AS lex_rank
                      FROM {FTS_TABLE}
                      JOIN memory m ON m.seq = {FTS_TABLE}.rowid
                     WHERE {FTS_TABLE} MATCH ?{clause}
                     ORDER BY lex_rank ASC, m.seq ASC
                     LIMIT ?""",                                   # nosec B608 (fixed identifiers)
                (match, *params, int(top_k)),
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

    @staticmethod
    def _backfill_scope_columns(conn: Any) -> None:
        """DB.S2b — project every PRE-EXISTING row's `doc` into the scope columns, so the whole
        table (not just rows written since `put()` learned to populate them) is a faithful
        projection. This is what earns the local store the right to be filtered on those columns.

        Runs at OPEN, immediately after `_ensure_scope_columns` and before `__init__` returns —
        so on SQLite the "backfill before filter" ordering is a structural invariant, not a flag
        somebody has to remember to check: no query can be issued against this store until the
        constructor has finished, and the constructor has already backfilled.

        One `UPDATE`, not a Python row loop: `json_extract` reads the doc in SQL, so the whole
        migration is a single statement and a corrupt row can't half-apply it. `json_extract`
        yields SQLite's own 0/1 for a JSON boolean, which is exactly what the INTEGER `pin`
        column stores.

        Stamped in `PRAGMA user_version` (a free 4-byte header field, unused elsewhere in mokata)
        so the scan happens ONCE per store rather than on every open — a store file is opened per
        operation, and an unconditional full-table UPDATE on each of those would be a real cost
        for a migration that is a no-op after the first run. The predicate below is still written
        to be idempotent on its own, so a store whose stamp is cleared re-converges rather than
        double-applying anything.
        """
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _SCOPE_BACKFILL_STAMP:
            return
        conn.execute(
            """UPDATE memory SET
                   scope_level = coalesce(nullif(json_extract(doc, '$.scope_level'), ''),
                                          'personal'),
                   scope_id    = coalesce(json_extract(doc, '$.scope_id'), ''),
                   pin         = coalesce(json_extract(doc, '$.pin'), 0),
                   priority    = coalesce(json_extract(doc, '$.priority'), 0)
               WHERE doc IS NOT NULL AND json_valid(doc)"""
        )
        conn.execute(f"PRAGMA user_version={_SCOPE_BACKFILL_STAMP}")

    @staticmethod
    def _ensure_lifecycle_columns(conn: Any) -> None:
        """DB.S5 — idempotently add the v4 lifecycle columns to a pre-existing `memory` table.
        The DB.S2b seam, copied verbatim (PRAGMA table_info → ALTER only what's missing), because
        SQLite still has no ADD COLUMN IF NOT EXISTS and inventing a second mechanism for the same
        problem is how two migrations end up disagreeing about what "already applied" means.

        Every column is NULLABLE or DEFAULT-ed, so the ALTER is instant on a populated table and an
        older mokata reading this store afterwards sees columns it does not name and is unaffected.
        """
        have = {row[1] for row in conn.execute("PRAGMA table_info(memory)").fetchall()}
        for col, ddl in (
            (VALID_FROM_COLUMN, f"{VALID_FROM_COLUMN} TEXT"),
            (VALID_TO_COLUMN, f"{VALID_TO_COLUMN} TEXT"),
            (HIT_COUNT_COLUMN, f"{HIT_COUNT_COLUMN} INTEGER NOT NULL DEFAULT 0"),
            (LAST_RECALLED_AT_COLUMN, f"{LAST_RECALLED_AT_COLUMN} TEXT"),
        ):
            if col not in have:
                conn.execute(f"ALTER TABLE memory ADD COLUMN {ddl}")

    @staticmethod
    def _backfill_lifecycle_columns(conn: Any) -> None:
        """DB.S5 — open every pre-existing row's validity window, from its own doc.

        ONLY `valid_from` is backfilled, and only where it is missing. That is the whole migration,
        and each half of the restraint is load-bearing:

          * `valid_to` is NOT written. An untouched `valid_to` is an OPEN window, which is the
            truth about every item that exists today — writing anything there would retire the
            corpus on upgrade, which is the exact opposite of "never delete".
          * `hit_count`/`last_recalled_at` are NOT backfilled. They are transient telemetry with no
            history to recover: an item that has never been recalled under a build that could count
            has honestly been recalled zero times. Synthesising a count from `created_at` would
            invent usage that never happened and feed it straight into the ranking.

        The value comes from `provenance.created_at` — an item's window opened when the item did.
        Idempotent by predicate (`WHERE valid_from IS NULL OR valid_from = ''`), so re-running
        matches nothing, and stamped in `PRAGMA user_version` like the DB.S2b twin so the scan
        happens once per store rather than on every per-operation connect.
        """
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _LIFECYCLE_BACKFILL_STAMP:
            return
        conn.execute(
            f"""UPDATE memory
                   SET {VALID_FROM_COLUMN} =
                       coalesce(nullif(json_extract(doc, '$.valid_from'), ''),
                                json_extract(doc, '$.provenance.created_at'), '')
                 WHERE ({VALID_FROM_COLUMN} IS NULL OR {VALID_FROM_COLUMN} = '')
                   AND doc IS NOT NULL AND json_valid(doc)"""      # nosec B608 (fixed identifiers)
        )
        conn.execute(f"PRAGMA user_version={_LIFECYCLE_BACKFILL_STAMP}")

    # ---------------------------------------------------------------- DB.S7a the edge substrate
    @staticmethod
    def _ensure_edges(conn: Any) -> None:
        """DB.S7a — idempotently provision the LOCAL typed-edge table (v5).

        The DB.S2b/DB.S5 seam, used a THIRD time rather than forked: `CREATE TABLE IF NOT EXISTS`
        for a fresh store, then `PRAGMA table_info` → `ALTER TABLE … ADD COLUMN` for anything a
        store created by an earlier build is missing. SQLite still has no `ADD COLUMN IF NOT
        EXISTS`, and inventing a second mechanism for the same problem is how two migrations end up
        disagreeing about what "already applied" means — the DB.S5 sentence, and it is still true.

        The PARTIAL unique index is the substrate's one real constraint: at most ONE OPEN edge per
        (src, dst, kind), while any number of CLOSED ones may sit beside it as history. It is the
        same index the shared Postgres schema creates, spelled identically, so the two engines
        enforce the same thing rather than one of them merely intending to.
        """
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {_edges.LOCAL_EDGES_TABLE} (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    {_edges.SRC_COLUMN} TEXT NOT NULL,
                    {_edges.DST_COLUMN} TEXT NOT NULL,
                    {_edges.KIND_COLUMN} TEXT NOT NULL,
                    {_edges.VALID_FROM_COLUMN} TEXT,
                    {_edges.VALID_TO_COLUMN} TEXT,
                    {_edges.CREATED_AT_COLUMN} TEXT,
                    {_edges.CREATED_BY_COLUMN} TEXT,
                    {_edges.APPROVAL_LEDGER_COLUMN} INTEGER
                )""")                                        # nosec B608 (fixed identifiers)
        have = {row[1] for row in
                conn.execute(f"PRAGMA table_info({_edges.LOCAL_EDGES_TABLE})").fetchall()}
        for col in _edges.EDGE_COLUMNS:
            if col not in have:
                ddl = "INTEGER" if col == _edges.APPROVAL_LEDGER_COLUMN else "TEXT"
                conn.execute(
                    f"ALTER TABLE {_edges.LOCAL_EDGES_TABLE} ADD COLUMN {col} {ddl}")  # nosec B608
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_edges.LOCAL_EDGES_TABLE}_open "     # nosec B608
            f"ON {_edges.LOCAL_EDGES_TABLE} "
            f"({_edges.SRC_COLUMN}, {_edges.DST_COLUMN}, {_edges.KIND_COLUMN}) "
            f"WHERE {_edges.VALID_TO_COLUMN} IS NULL")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {_edges.LOCAL_EDGES_TABLE}_dst "             # nosec B608
            f"ON {_edges.LOCAL_EDGES_TABLE} ({_edges.DST_COLUMN}, {_edges.KIND_COLUMN}) "
            f"WHERE {_edges.VALID_TO_COLUMN} IS NULL")

    @staticmethod
    def _backfill_edges(conn: Any) -> int:
        """DB.S7a — migrate the three IMPLICIT doc-JSON edge kinds into explicit rows. Returns the
        number of item→item refs SKIPPED because their target item is not in this store (E7).

        **Zero human re-approval (E2), and this is the argument, not an assertion.** Every ref this
        reads is a field of a `doc` that already went through the WriteGate when its item was
        written. The migration derives no new content, changes no item, adds no value a reviewer has
        not seen, and asks nothing: it moves an already-approved relation from one representation to
        another. Re-prompting would be asking a human to re-approve a fact they approved when they
        wrote it, which is how a gate stops meaning anything.

        **Dangling refs are SKIPPED and REPORTED (E7), never orphaned and never fatal.** A
        `supersedes`/`depends_on` ref whose target is absent — a pruned store, a partial import —
        yields NO row rather than an edge pointing into nothing, and the migration completes. The
        count comes back so `edge_backfill_skipped` can surface it: skip-and-say-so, the same
        degrade-clean posture `_backfill_lifecycle_columns` takes on a malformed doc. `about_code`
        refs are excluded from the count entirely — their dst is a code path in the repo, so "not an
        item" is what a CORRECT one looks like.

        Idempotent by predicate AND stamped, exactly like its two predecessors: the insert is
        `WHERE NOT EXISTS (an open edge already saying this)` so a store whose stamp is cleared
        re-converges instead of duplicating, and the stamp keeps the scan off every per-operation
        open.
        """
        if int(conn.execute("PRAGMA user_version").fetchone()[0]) >= _EDGES_BACKFILL_STAMP:
            return 0
        rows = conn.execute("SELECT doc FROM memory WHERE doc IS NOT NULL AND json_valid(doc)"
                            ).fetchall()
        known = {r[0] for r in conn.execute("SELECT id FROM memory").fetchall()}
        stmt = _edges.insert_open_sql(_edges.LOCAL_EDGES_TABLE)
        now = now_iso()
        skipped = 0
        seen_skips: set = set()
        for (raw,) in rows:
            try:
                doc = json.loads(raw)
            except (json.JSONDecodeError, ValueError):   # pragma: no cover - json_valid filtered
                continue
            if not isinstance(doc, dict):
                continue
            for edge in _edges.edges_from_doc(doc, created_at=now):
                if edge.kind in _edges.ITEM_TARGET_KINDS and edge.dst_id not in known:
                    # SKIP — and count it ONCE per (src, dst, kind), so one missing target listed
                    # in two fields is not reported as two separate problems.
                    key = (edge.src_id, edge.dst_id, edge.kind)
                    if key not in seen_skips:
                        seen_skips.add(key)
                        skipped += 1
                    continue
                conn.execute(stmt, _edges.insert_open_params(edge))
        conn.execute(f"PRAGMA user_version={_EDGES_BACKFILL_STAMP}")
        return skipped

    def open_edges(self, item_id: str) -> "List[_edges.MemoryEdge]":
        """Every OPEN edge out of `item_id`. The substrate's read — capability-probed by callers
        with `hasattr`, never assumed (the `lexical_search`/`record_usage` posture), so a backend
        without an edge table simply supplies no edges and the caller falls back to the inline
        lists it has always read."""
        with self._connect() as conn:
            rows = conn.execute(
                _edges.open_edges_sql(_edges.LOCAL_EDGES_TABLE), (item_id,)).fetchall()
        return [_edges.edge_from_row(r) for r in rows]

    def expand_from(self, seed_ids: "Sequence[str]", max_hops: int) -> "List[tuple]":
        """DB.S7b — the bounded ≤2-hop walk out of `seed_ids`, as ONE recursive-CTE statement.

        Returns the walked EDGES (`seed, src, dst, kind, depth`); `expansion.walk_paths` chains
        them into paths. The twin of `PostgresBackend.expand_from`, running the SAME SQL from the
        SAME builder with a different placeholder — which is what makes "the two engines agree" a
        property of one statement rather than a coincidence between two."""
        from . import expansion as _exp
        seeds = [s for s in seed_ids if s]
        if not seeds:
            return []
        with self._connect() as conn:
            return conn.execute(
                _exp.expansion_sql(_edges.LOCAL_EDGES_TABLE, len(seeds)),
                _exp.expansion_params(seeds, max_hops)).fetchall()

    # ---------------------------------------------------------------- DB.S5 usage telemetry
    # SQLite binds each id as a `?` parameter, and a single statement may carry only so many
    # (`SQLITE_MAX_VARIABLE_NUMBER` — 999 on older builds, 32766 on modern ones). A recall ranks
    # EVERY active item, so on the 100k-item store DB.S8 contracts against, one statement per call
    # would exceed the limit and raise. The seams above it swallow that, which would be the WORST
    # outcome: the usage signal would vanish silently on exactly the large stores it exists to
    # help, and ranking would quietly revert to the three-term floor with nothing to notice.
    # Chunking removes the failure instead of degrading it. 500 is comfortably under the oldest
    # limit, so it is safe on any sqlite3 mokata can be built against.
    _ID_CHUNK = 500

    @staticmethod
    def _chunks(ids: List[str]) -> "List[List[str]]":
        return [ids[i:i + SQLiteBackend._ID_CHUNK]
                for i in range(0, len(ids), SQLiteBackend._ID_CHUNK)]

    # THE ONE PLACE the local store's usage columns are written, and the ONE place they are read.
    # Both are deliberately NOT part of the `MemoryBackend` ABC: a backend without them (Obsidian's
    # files, the native client) is not broken, it simply supplies no usage signal, and the fusion
    # falls back to its three original terms. Capability is probed with `hasattr`, never assumed —
    # the same posture as `lexical_search`/`semantic_search`.
    def record_usage(self, item_ids: Sequence[str], now: str) -> int:
        """Stamp a recall against `item_ids`: bump `hit_count`, set `last_recalled_at`. Returns the
        number of rows touched.

        Transient RUN-STATE, per the D5 policy — NOT a governed durable write. It touches no
        approved content: the `doc` column, every field the human reviewed, and the validity window
        are all untouched, and the statement is a bare counter increment on the row. That is
        precisely why it may run ungated on a read path where a `put()` never could.

        It raises on failure rather than swallowing, and that is on purpose: the degrade-clean
        boundary belongs at ONE seam (`store.record_usage`), where it can be seen and tested, not
        scattered into every backend where each would decide for itself what "failed quietly"
        means. This layer's job is to say honestly whether the write happened.
        """
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return 0
        touched = 0
        with self._connect() as conn:
            for chunk in self._chunks(ids):
                placeholders = ", ".join("?" * len(chunk))
                cur = conn.execute(
                    f"UPDATE memory SET {HIT_COUNT_COLUMN} = coalesce({HIT_COUNT_COLUMN}, 0) + 1, "
                    f"{LAST_RECALLED_AT_COLUMN} = ? "
                    f"WHERE id IN ({placeholders})",       # nosec B608 (fixed identifiers + ?)
                    (now, *chunk),
                )
                touched += cur.rowcount
            # ONE commit for the whole batch: the chunking is an engine limit, not a transaction
            # boundary, and committing per chunk would fsync N times for one recall's telemetry.
            conn.commit()
        return touched

    def usage_stats(self, item_ids: Sequence[str]) -> Dict[str, tuple]:
        """The `{item_id: (hit_count, last_recalled_at)}` telemetry for `item_ids`.

        Read in ONE bounded query keyed by the ids the caller already holds — never a full-store
        scan, and never a per-item round trip inside the ranking loop. An id with no row (or a
        NULL counter) is simply absent from the result, and the caller reads absence as the
        zero-signal `UsageSignal()` — which is the state the whole back-compat argument rests on.
        """
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return {}
        out: Dict[str, tuple] = {}
        with self._connect() as conn:
            for chunk in self._chunks(ids):
                placeholders = ", ".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT id, coalesce({HIT_COUNT_COLUMN}, 0), {LAST_RECALLED_AT_COLUMN} "
                    f"FROM memory WHERE id IN ({placeholders})",   # nosec B608 (identifiers + ?)
                    tuple(chunk),
                ).fetchall()
                out.update({r[0]: (int(r[1] or 0), r[2]) for r in rows})
        return out

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
        with self._connect() as conn:
            self._put_on(conn, item)
            conn.commit()

    def _put_on(self, conn: Any, item: MemoryItem) -> None:
        """THE local write, with its CONNECTION and its COMMIT hoisted out to the caller.

        `put` above is this plus "open a connection, commit it" — and that is the ONLY production
        caller. It is split out for exactly one reason: a caller with N items to land pays N
        connection opens and N commits through `put`, which is 71.7s for the 100k-row store DB.S8
        contracts against and 1.4s for the same rows through one transaction. The alternative — a
        loader that writes its own INSERT — is the SHIM-FALSE-GREEN shape doc 84 already carries as
        a 🔴 row: a fixture whose rows are built by a second, hand-mirrored statement proves the
        second statement, not the one users run. There is exactly one INSERT for the local store and
        it is below.
        """
        payload = item.to_doc()           # D6 — the durable serializer: refuses a newer-than-us doc
        doc = json.dumps(payload)
        # DB.S2b — the scope/precedence columns are written by THIS statement, the one that
        # writes `doc`. Single-sourced on purpose: one writer means column and doc cannot
        # drift, which is the property that makes `filter_clause_for` allowed to push a scope
        # predicate at them. The upsert branch moves them too — a re-put at a new scope that
        # left a stale column behind would be the same cross-tenant bug as never writing one.
        # DB.S5 — the validity columns ride the SAME statement, on the same single-source
        # argument as the scope half. The usage columns are conspicuously NOT in this list:
        # an upsert must never reset a live row's `hit_count`, so the write path does not name
        # them and `record_usage` owns them alone.
        conn.execute(
            """INSERT INTO memory (id, mtype, subject, status, doc,
                                   scope_level, scope_id, pin, priority,
                                   valid_from, valid_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   mtype=excluded.mtype, subject=excluded.subject,
                   status=excluded.status, doc=excluded.doc,
                   scope_level=excluded.scope_level, scope_id=excluded.scope_id,
                   pin=excluded.pin, priority=excluded.priority,
                   valid_from=excluded.valid_from, valid_to=excluded.valid_to""",
            (item.id, item.mtype, item.subject, item.status, doc,
             *scope_columns_from_doc(payload), *validity_columns_from_doc(payload)),
        )
        # DB.S7a — the EDGE PROJECTION rides the SAME connection and lands in the SAME
        # `commit()` the caller runs, which is what makes E6 structural instead of asserted.
        # `put` is reached only from `store._commit`, the closure the WriteGate runs on approval,
        # so: an approved write commits the item row and its edges together or neither, and a
        # DECLINED write never calls this at all — there is no path that writes an edge row
        # without a human having approved the item it projects. The projection derives from
        # `payload`, the very dict serialized into `doc` one statement above, on the same
        # single-source argument the scope and validity columns are written by.
        #
        # The commit moved OUT to `put` (DB.S8) and the together-or-neither property is unchanged
        # by that: it was never this line's commit that provided it, it was the fact that both
        # writes ride ONE connection and ONE transaction. A bulk caller that commits after N items
        # holds the same property over a wider unit — items and their edges still land together.
        _edges.project_edges(conn, _edges.LOCAL_EDGES_TABLE, payload, now=now_iso())

    def get(self, item_id: str) -> Optional[MemoryItem]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT doc FROM memory WHERE id=?", (item_id,)
            ).fetchone()
        return MemoryItem.from_dict(json.loads(row[0])) if row else None

    # DB.S2b — a LOCAL store is backfilled by its own constructor (see `_backfill_scope_columns`),
    # so by the time any caller holds this object the scope columns are already a faithful
    # projection. Unconditionally true, and true by construction rather than by stamp-reading.
    supports_scope_pushdown = True

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        """DB.S2a/DB.S2b — filters in the DB, not in Python. This used to `SELECT doc FROM memory
        ORDER BY seq` and then drop rows in a list comprehension, i.e. pull the WHOLE table over
        the wire (and through `from_dict`) on every recall. Now the filter is a WHERE and only
        matching rows are ever materialized. Result set + order are unchanged: same `ORDER BY seq`,
        and every pushed column is a faithful projection of the doc (see `filter_clause_for`).

        `scope_path` (DB.S2b) pushes the broad→narrow UNION read down as well, so a scoped recall
        no longer drags every other scope's rows through `from_dict` to discard them in
        `scope.union_read`. Omitted (the local/zero-config default) it changes nothing.

        There is no `project` column on the local table — a local store IS one project (the DB file
        lives in the repo), so unlike Postgres there is nothing to scope by. Not an omission.
        """
        # The B608 suppression below is the same false positive the Postgres half carries: the SQL
        # interpolates ONLY builder-generated fragments made of fixed column names and `?`
        # placeholders. Every VALUE is bound, never formatted in.
        clause, params = filter_clause_for(mtype, statuses, scope_path=scope_path,
                                           placeholder="?")
        tail, tail_params = _limit_clause("?", limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT doc FROM memory{clause} ORDER BY seq{tail}",  # nosec B608
                (*params, *tail_params),
            ).fetchall()
        return [MemoryItem.from_dict(json.loads(r[0])) for r in rows]

    #: R-1 (DB.S8) — the local store CAN nominate candidates in SQL (it has FTS5 and an id index),
    #: so recall need not materialize the active set to rank it. Probed by the caller with
    #: `getattr`, never assumed — the same posture as `lexical_search` / `expand_from`.
    supports_candidate_selection = True

    def hydrate(self, ids: Sequence[str] = (), *, subjects: Sequence[str] = (),
                statuses: Optional[Tuple[str, ...]] = None,
                scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        """R-1 (DB.S8) — the BOUNDED read: the rows the tiers nominated, plus the rows that compete
        with them for precedence. Never a scan.

        This is the half of Option A that replaces `all(statuses=(ACTIVE,))` on the recall path.
        `ids` are the nominated candidates; `subjects` pulls in each nominee's full precedence
        group (see `filter_clause_for` — a partial group would let a loser be returned as a winner).
        Both are OR-ed, then AND-ed with the same status and scope predicates every other read uses,
        so a candidate outside the reader's scope is dropped by the DATABASE rather than after it.

        Chunked at `_ID_CHUNK` for the reason `record_usage` is: a single statement binds one `?`
        per id and `SQLITE_MAX_VARIABLE_NUMBER` is 999 on older builds. The chunk loop keeps this
        read correct on any sqlite3 mokata can be built against rather than raising on the large
        stores it exists to serve. Result order is `seq`, matching `all()`.
        """
        ids, subjects = list(ids), list(subjects)
        if not ids and not subjects:
            return []
        seen: dict = {}
        # The two halves are chunked INDEPENDENTLY: a 150-id candidate set and a 150-subject
        # group set would otherwise bind 300 parameters in one statement.
        for kind, values in (("ids", ids), ("subjects", subjects)):
            for chunk in self._chunks(values):
                clause, params = filter_clause_for(
                    None, statuses, scope_path=scope_path, placeholder="?",
                    **{kind: chunk})
                with self._connect() as conn:
                    rows = conn.execute(
                        f"SELECT seq, doc FROM memory{clause} ORDER BY seq",  # nosec B608
                        params).fetchall()
                for seq, doc in rows:
                    seen.setdefault(seq, doc)
        return [MemoryItem.from_dict(json.loads(doc)) for _seq, doc in sorted(seen.items())]

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
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        # DB.S2a — a vault is FILES, not a queryable store: there is no WHERE to push into, so the
        # filter stays in Python here. Only the SQL backends gained the pushdown; the contract
        # (including `limit`) is uniform so callers never branch on backend.
        #
        # DB.S2b — `scope_path` is accepted for that same uniformity and deliberately IGNORED:
        # `supports_scope_pushdown` stays False, so no caller passes one, and `scope.union_read`
        # does the scope filtering over the returned items exactly as before.
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
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        # DB.S2a — the injected `MemoryClient.all()` takes no filter arguments (it is someone
        # else's contract, not ours to widen), so the filter stays in Python on this adapter.
        # DB.S2b — and for the same reason `scope_path` is accepted-and-ignored here:
        # `supports_scope_pushdown` stays False, so `scope.union_read` keeps doing the work.
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
        self._scope_ready: Optional[bool] = None  # DB.S2b — lazily probed, see the property
        self._edges_ready: Optional[bool] = None  # DB.S7a — same, for the v5 edge table
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
        self._put_on(self._conn, item)

    def _put_on(self, conn: Any, item: MemoryItem) -> None:
        """THE shared write, with its CONNECTION passed in — the twin of `SQLiteBackend._put_on`,
        split out for the identical reason and with the identical contract.

        `_pg` connections are AUTOCOMMIT, so through `put` each item is its own transaction and its
        own server round trip: at DB.S8's 100k rows that is 100k commits. A bulk caller wraps N of
        these in ONE explicit transaction and pays one. `put` — the only production caller — passes
        `self._conn` and is therefore byte-identical to what it always did.
        """
        # DB.S2b — the scope/precedence columns ride the SAME statement as `doc` (the SQLite twin
        # does the identical thing with the identical `_scope_columns` values). One writer per row
        # means the column and the doc cannot drift, which is precisely what lets `all()` push a
        # scope predicate at them. The DO UPDATE branch moves them too, so a re-put at a new scope
        # never leaves the old scope behind in the column.
        # DB.S5 — the validity columns join the same statement (the SQLite twin does the identical
        # thing). The usage columns stay out of it, as they do locally: an upsert that named
        # `hit_count` would reset a live row's counter from a stale in-memory doc.
        payload = item.to_doc()           # D6 — the durable serializer: refuses a newer-than-us doc
        conn.execute(
            f"INSERT INTO {self.TABLE} (id, mtype, subject, status, doc, project,"  # nosec B608
            " scope_level, scope_id, pin, priority, valid_from, valid_to)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET mtype=EXCLUDED.mtype,"
            " subject=EXCLUDED.subject, status=EXCLUDED.status, doc=EXCLUDED.doc,"
            " project=EXCLUDED.project, scope_level=EXCLUDED.scope_level,"
            " scope_id=EXCLUDED.scope_id, pin=EXCLUDED.pin, priority=EXCLUDED.priority,"
            " valid_from=EXCLUDED.valid_from, valid_to=EXCLUDED.valid_to",
            (item.id, item.mtype, item.subject, item.status,
             json.dumps(payload), self.project,
             *scope_columns_from_doc(payload), *validity_columns_from_doc(payload)),
        )
        # DB.S7a — the edge projection, on the same connection, immediately after the row it
        # projects. Guarded by `supports_edges` because this backend does NOT own the shared schema
        # (C4: DDL is `team init`'s alone) and therefore cannot assume v5: against a v4 store the
        # probe is False and this is a no-op, so an un-migrated team keeps writing byte-identically.
        #
        # HONEST BOUNDARY, stated rather than implied: `_pg` connections are AUTOCOMMIT, so on this
        # path the item row and its edges are two commits, not one. It costs nothing that matters —
        # the projection is DERIVED from inline fields the item row itself carries, so a crash
        # between them leaves a store the next `team init` backfill rebuilds exactly. The path where
        # atomicity IS load-bearing is the team flush, and there the projection runs inside the
        # approval group's explicit transaction (see `team_journal._project_edges_for`).
        if self.supports_edges:
            _edges.project_edges(conn, _edges.SHARED_EDGES_TABLE, payload,
                                 now=now_iso(), placeholder="%s")

    @property
    def supports_edges(self) -> bool:
        """Does THIS shared store carry the v5 edge table? Probed once, never assumed — the same
        posture (and the same fail-closed direction) as `supports_scope_pushdown` directly below.

        False on a v4 store means the projection is skipped and nothing else changes, which is what
        `TEAM_SCHEMA_MIN_SUPPORTED` staying at 3 is BUYING: an un-migrated team degrades to exactly
        its pre-DB.S7a behaviour instead of failing. The day a read becomes mandatory on this table,
        that trade stops being available and the floor moves — see the constant's comment."""
        if self._edges_ready is None:
            self._edges_ready = self._read_edges_present()
        return self._edges_ready

    def _read_edges_present(self) -> bool:
        from ..degrade import FAILURE_SCHEMA, note_degraded
        try:
            row = self._conn.execute("SELECT to_regclass(%s)",
                                     (_edges.SHARED_EDGES_TABLE,)).fetchone()
        except Exception as exc:
            # D5 — BROAD, fail-CLOSED, and LOUD; the exact twin of `team_journal._edges_present`,
            # and registered alongside it. Every way this can fail (no such function on an ancient
            # server, a driver error, a dead connection) means one thing to the caller: we could
            # not establish that the table is there. Unknown is not permission — projecting into a
            # table that may not exist would abort the caller's transaction, which is far worse
            # than skipping the projection. A genuinely v4 store answers NULL and never lands here,
            # so reaching this handler is an anomaly and says so once, with its fix.
            note_degraded("memory-edges", FAILURE_SCHEMA, detail=str(exc),
                          fallback="edge projection skipped — the item write is unaffected",
                          fix="run `mokata team init` to (re-)provision and re-derive the edges")
            return False
        return bool(row and row[0])

    def open_edges(self, item_id: str) -> "List[_edges.MemoryEdge]":
        """Every OPEN edge out of `item_id` in the shared store; `[]` on a v4 store rather than a
        raise, so a caller that probes with `hasattr` still degrades clean on an un-migrated team."""
        if not self.supports_edges:
            return []
        rows = self._conn.execute(
            _edges.open_edges_sql(_edges.SHARED_EDGES_TABLE, placeholder="%s"), (item_id,)).fetchall()
        return [_edges.edge_from_row(r) for r in rows]

    def expand_from(self, seed_ids: "Sequence[str]", max_hops: int) -> "List[tuple]":
        """DB.S7b — the bounded ≤2-hop walk, on the shared store. `[]` on a v4 store rather than a
        raise, for the same reason `open_edges` does it: an un-migrated team degrades to exactly its
        pre-DB.S7b ranking instead of failing a recall over a table it never provisioned."""
        from . import expansion as _exp
        if not self.supports_edges:
            return []
        seeds = [s for s in seed_ids if s]
        if not seeds:
            return []
        return self._conn.execute(
            _exp.expansion_sql(_edges.SHARED_EDGES_TABLE, len(seeds), placeholder="%s"),
            _exp.expansion_params(seeds, max_hops)).fetchall()

    def get(self, item_id: str) -> Optional[MemoryItem]:
        clause, params = self._scope()
        row = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE} WHERE id=%s{clause}",  # nosec B608
            (item_id, *params)).fetchone()
        return _with_revision(row[0], row[1]) if row else None

    @property
    def supports_scope_pushdown(self) -> bool:
        """DB.S2b — may a scope predicate be pushed at THIS store? True only once its backfill has
        run, read from `teamdb.SCOPE_BACKFILLED_COLUMN` (see that constant for why the schema
        VERSION cannot answer this: a pre-DB.S2b `team init` also stamped v3, having created the
        columns without populating them).

        Unlike the SQLite twin this cannot be an invariant — the backend does not own the shared
        table and runs no migration (C4: DDL is `team init`'s alone), so it must ASK. Fail-closed
        on every uncertainty: an absent column, an absent row, an unreadable table or any driver
        error all read as False, which costs a slower doc-side filter and leaks nothing. Cached
        after the first read — the stamp only changes when `team init` runs, which is not mid-
        session, and a per-`all()` probe would add a round trip to every recall.
        """
        if self._scope_ready is None:
            self._scope_ready = self._read_scope_backfilled()
        return self._scope_ready

    def _read_scope_backfilled(self) -> bool:
        from .. import teamdb
        try:
            row = self._conn.execute(
                f"SELECT {teamdb.SCOPE_BACKFILLED_COLUMN} "  # nosec B608 (mokata-owned identifiers)
                f"FROM {teamdb.SCHEMA_VERSION_TABLE} ORDER BY version DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return False        # no column / no table / driver error — unknown is not permission
        return bool(row and row[0])

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None,
            limit: Optional[int] = None,
            scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        """DB.S2a/DB.S2b — the mtype/status filter is pushed into the WHERE (see the SQLite twin),
        and since DB.S2b the scope-path UNION with it. The `project` scoping is UNCHANGED:
        `_scope()` already put it in the WHERE at Stage 71a, and it stays the FIRST condition so
        its bound parameter keeps leading `params`.

        THE GUARD: a `scope_path` is honoured only when this store reports
        `supports_scope_pushdown`. On a store whose backfill has not run the columns are still at
        their DDL default, so filtering on them would drop every row whose real home scope isn't
        `personal` — silently, and differently per tenant. Dropping the predicate instead returns a
        SUPERSET, which the caller's `scope.union_read` then narrows correctly from the doc. Slower
        and right beats faster and wrong; this is the one place where that trade is not a
        judgement call."""
        scope, scope_params = self._scope(prefix="WHERE")
        if scope_path is not None and not self.supports_scope_pushdown:
            scope_path = None
        clause, params = filter_clause_for(
            mtype, statuses, scope_path=scope_path,
            placeholder="%s", prefix="AND" if scope else "WHERE")
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

    # ---------------------------------------------------------------- DB.S7c2 STALE-REF
    def index_epoch(self) -> str:
        """The whole-index epoch of THIS store's project scope — the stamp DB.S7c2 puts on a
        citation that will outlive the store, so it can be validated later by comparison rather
        than by re-reading every cited row (`memory.staleness`).

        THE ITEM TABLE ONLY. The aggregate never touches `mokata_memory_edges`, and that is the
        structural reason a window-closed edge never trips STALE-REF: closing an edge's validity
        window is R3 HISTORY, not index staleness, and an epoch that could see it would invalidate
        every outstanding citation on every K1/K2 relation withdrawal.

        WHY THREE NUMBERS AND NOT ONE. No single existing column is a whole-index counter, so this
        composes the three the shared table already has, and the triple is EXACT for the gated
        write path rather than merely probable:
          * `revision` bumps by exactly 1 per CAS UPDATE (`team_journal._UPDATE_SQL`) and starts at
            1 per INSERT, so `sum(revision)` moves on every update;
          * `count(*)` moves on every insert and every prune;
          * `max(seq)` is BIGSERIAL — an insert always draws a value strictly greater than any ever
            assigned, so it moves on every insert that survives.
        Take them together: an unchanged `count` with an unchanged `max(seq)` means no insert and
        no prune landed, which leaves only updates — and each of those adds 1 to `sum(revision)`.
        So an unchanged triple means an unchanged index. The one case it reads as "unchanged" after
        real writes is a round trip that restores the index to its exact prior state (insert then
        prune the same row), and that is CORRECT rather than a miss: the citation is not stale,
        because nothing it could refer to has changed.

        SCOPE: `_scope()` — the same project predicate every other read here carries — so one
        tenant's writes never age another tenant's citations.
        """
        from .. import teamdb
        from .staleness import INDEX_EPOCH_OFF
        scope, scope_params = self._scope(prefix="WHERE")
        row = self._conn.execute(
            f"SELECT count(*), coalesce(sum({teamdb.MEMORY_REVISION_COLUMN}), 0), "  # nosec B608
            f"coalesce(max(seq), 0) FROM {self.TABLE}{scope}", scope_params).fetchone()
        if not row:
            return INDEX_EPOCH_OFF
        return f"{int(row[0])}.{int(row[1])}.{int(row[2])}"

    # DB.S3 — the tsvector tier. CORE Postgres: `to_tsvector`/`to_tsquery`/`ts_rank` are built in,
    # so this needs NO `CREATE EXTENSION` (unlike the opt-in pgvector tier) and therefore stays on
    # the ADR-54 vanilla-Postgres golden path.
    lexical_mode = LEXICAL_MODE_TSVECTOR

    #: R-1 (DB.S8) — the shared store nominates candidates in SQL too. Guarded at the READ, not
    #: here: `hydrate` composes a scope predicate only when `supports_scope_pushdown` says the
    #: backfill has run, so a v3/un-backfilled store still nominates and still filters from the doc.
    supports_candidate_selection = True

    def hydrate(self, ids: Sequence[str] = (), *, subjects: Sequence[str] = (),
                statuses: Optional[Tuple[str, ...]] = None,
                scope_path: Optional[Sequence[Any]] = None) -> List[MemoryItem]:
        """R-1 (DB.S8) — the bounded read on the shared store; the twin of the SQLite one, built
        from the SAME `filter_clause_for`, so "the two engines filter identically" is a property of
        one builder rather than a coincidence between two hand-written queries.

        No chunk loop: psycopg binds a list as one parameter and Postgres has no
        `SQLITE_MAX_VARIABLE_NUMBER` analogue to trip over. It carries the project clause every
        other read on this backend carries.
        """
        ids, subjects = list(ids), list(subjects)
        if not ids and not subjects:
            return []
        pclause, pparams = self._scope("WHERE")
        prefix = "AND" if pclause else "WHERE"
        clause, params = filter_clause_for(None, statuses, scope_path=scope_path,
                                           ids=ids or None, subjects=subjects or None,
                                           placeholder="%s", prefix=prefix)
        rows = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE}{pclause}{clause} "   # nosec B608
            "ORDER BY id", (*pparams, *params)).fetchall()
        return [_with_revision(r[0], r[1]) for r in rows]

    def lexical_search(self, query: str, top_k: int = DEFAULT_TOP_K,
                       *, scope_path: Optional[Sequence[Any]] = None,
                       statuses: Optional[Tuple[str, ...]] = None,
                       kinds: Optional[Sequence[str]] = None
                       ) -> List[Tuple[MemoryItem, float]]:
        """DB.S3 — the lexical tier as ONE ranked SQL query: `@@` for the match, `ts_rank` for the
        order, top-k in the database. Replaces the Python Jaccard scan (see the SQLite twin).

        JIT-STAMP-SEAM — `kinds` travels with the ranked query, for the reason spelled out on the
        SQLite twin: a top-k taken across all kinds and filtered afterwards under-fills.

        R-1 (DB.S8) — `scope_path`/`statuses` travel WITH the ranked query, for the reason spelled
        out on the SQLite twin: the LIMIT must be taken over rows this identity may READ. On a
        shared store the project predicate was already here; the SCOPE predicate was not, so the
        top-N could be filled entirely with rows the reader's scope path excludes.

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
        clause, params = filter_clause_for(None, statuses, scope_path=scope_path,
                                           kinds=kinds, kind_expr=_PG_KIND_EXPR,
                                           placeholder="%s", prefix="AND")
        rows = self._conn.execute(
            f"SELECT doc, revision, ts_rank({vec}, {tsq}) AS lex_rank"      # nosec B608
            f"  FROM {self.TABLE}"
            f" WHERE {vec} @@ {tsq}{scope}{clause}"
            f" ORDER BY lex_rank DESC, seq ASC LIMIT %s",
            (tsquery, tsquery, *scope_params, *params, int(top_k)),
        ).fetchall()
        items = [_with_revision(r[0], r[1]) for r in rows]
        scores = normalize_lexical_scores([r[2] for r in rows], higher_is_better=True)
        return list(zip(items, scores))

    # ---------------------------------------------------------------- DB.S5 usage telemetry
    # The shared-store twins of the SQLite pair. Both carry the project predicate every other read
    # and write here carries, so one project can neither stamp nor read another's usage — a usage
    # counter is as tenant-scoped as the row it counts.
    def record_usage(self, item_ids: Sequence[str], now: str) -> int:
        """Stamp a recall against `item_ids` on the shared store (transient run-state, D5).

        NOT DDL and not a governed write: a counter increment on existing columns, which a DML-only
        team role may run. On a v3 store the columns do not exist and this raises — deliberately
        unhandled here, because `store.record_usage` is the ONE degrade-clean seam and a v3 team
        simply carries no usage signal until `mokata team init` migrates it to v4.
        """
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return 0
        clause, params = self._scope()
        cur = self._conn.execute(
            f"UPDATE {self.TABLE} SET {HIT_COUNT_COLUMN} = "        # nosec B608
            f"coalesce({HIT_COUNT_COLUMN}, 0) + 1, {LAST_RECALLED_AT_COLUMN} = %s "
            f"WHERE id = ANY(%s){clause}",
            (now, ids, *params),
        )
        return cur.rowcount

    def usage_stats(self, item_ids: Sequence[str]) -> Dict[str, tuple]:
        """The `{item_id: (hit_count, last_recalled_at)}` telemetry for `item_ids` — one bounded,
        project-scoped query, never a scan. `last_recalled_at` is a TIMESTAMPTZ here and a TEXT
        column on SQLite; both are normalized to an ISO string so `lifecycle.parse_iso` sees one
        shape whichever engine answered."""
        ids = [str(i) for i in item_ids if i]
        if not ids:
            return {}
        clause, params = self._scope()
        rows = self._conn.execute(
            f"SELECT id, coalesce({HIT_COUNT_COLUMN}, 0), "      # nosec B608
            f"{LAST_RECALLED_AT_COLUMN} FROM {self.TABLE} WHERE id = ANY(%s){clause}",
            (ids, *params),
        ).fetchall()
        return {r[0]: (int(r[1] or 0), r[2].isoformat() if hasattr(r[2], "isoformat") else r[2])
                for r in rows}

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
