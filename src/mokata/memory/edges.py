"""DB.S7a — the memory EDGE SUBSTRATE: one typed, bi-temporal relation table, on both engines.

THE ONE definition of what an edge IS. The kind vocabulary, the projection from an item, the
column names, the validity rule and the idempotency predicate all live HERE, so the local SQLite
floor, the shared Postgres table and the team-flush CAS path cannot disagree about any of them —
the same argument `lifecycle.py` makes for the v4 item window, applied to relations.

NEVER A SECOND GRAPH DB (doc 84 DB.S7, doc 04). Edges are rows in the store that already exists,
traversed with recursive CTEs. Nothing here opens a connection, imports a graph engine, or writes
a file. `neo4j_backend.py` is the deprecated counter-example, not the precedent.


THE FOUR DECISIONS THIS MODULE ENCODES
======================================

**1 · ONE VALIDITY AXIS (the DB.S7a temporal resolution, doc 02 decision #1).** An edge carries
`valid_from`/`valid_to` — the SAME two names, meaning the same thing, as the item window DB.S5
put on `MemoryItem` — plus `created_at` as PROVENANCE, which doc 55's canonical edge shape
(`55:27-28`) already specified. There is NO `expired` and no transaction-time pair. The reason is
not economy, it is symmetry: items express "no longer believed" structurally (`status=SUPERSEDED`
+ `supersedes` lineage) and deliberately have no transaction-time close stamp, so giving edges one
would make this table the only place in mokata where the valid/transaction distinction is even
representable — edges strictly more temporally sophisticated than the items they connect. R3's
actual requirement ("contradiction = invalidation, never deletion") is met by a closed `valid_to`
window, which is what `close_edges` does and the only retirement operation here.

**2 · INLINE IS AUTHORITATIVE; THIS TABLE IS A DERIVED PROJECTION.** `MemoryItem.supersedes`,
`.depends_on` and `.about_code` remain the MASTER for their three kinds — they are what
`to_dict`/`from_dict` round-trip, what an export carries, what a teammate reads, and what the
gate shows a human. The edge rows are re-derivable from them at any time (that is exactly what
the migration does), so a crash between the item write and the projection loses nothing that a
re-run cannot rebuild. Kinds with NO inline field (the five unwired ones) will be table-authoritative
when they gain a producer. **ONE MASTER PER EDGE KIND** — never two for the same kind, which is
the rule that keeps "derived" from quietly becoming "diverged". Deprecating the inline fields is
0.1.3 work and is deliberately NOT started here.

**3 · THE CLOSED SET IS THE CONTRACT.** All eight kinds are declared; admitting a ninth is a
schema change, not a runtime decision. `validate_kind` REFUSES anything else rather than storing
it — an unrecognised kind in this table would be a relation no traversal knows how to interpret.
Only the three with a live producer are WIRED (see `WIRED_KINDS`); the other five are declared and
inert, which is honest: wiring them would mean INVENTING edges, not moving existing ones.

**4 · AT MOST ONE OPEN EDGE PER (src, dst, kind).** Enforced by a PARTIAL unique index
(`WHERE valid_to IS NULL`) on both engines rather than a plain primary key, and that choice is
load-bearing: a plain PK would force a re-asserted relation either to rewrite its own history or
to be refused, while the partial index lets a closed edge stay on disk forever and a later
re-assertion open a NEW window beside it. Every write is idempotent by the same predicate — "insert
only where no OPEN edge already says this" — so re-running any migration a second time matches zero
rows (E1) instead of duplicating the corpus.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import MokataError

# ---------------------------------------------------------------- the closed typed-edge set
# Doc 55 K3 / doc 84 DB.S7. DECLARED IN FULL at DB.S7a because the SET is the contract: a kind
# admitted later is a schema change someone reviews, not a string a caller invents. Grounded by
# grep over src/ (2026-07-30) — exactly three have a live producer today.
SUPERSEDES = "supersedes"          # this item replaces that one (lineage; never a delete)
DEPENDS_ON = "depends_on"          # this item is only true while that one is
DERIVES_FROM = "derives_from"      # this item was distilled/summarised out of that one
CONTRADICTS = "contradicts"        # these two cannot both be true
ABOUT_CODE = "about_code"          # this item concerns that code symbol/file
DECIDED_IN = "decided_in"          # this item was settled in that decision/run
USED_BY = "used_by"                # this item was consumed by that prompt/skill (K5)
PROMOTED_FROM = "promoted_from"    # this item was promoted out of that lower-scope one

EDGE_KINDS: Tuple[str, ...] = (SUPERSEDES, DEPENDS_ON, DERIVES_FROM, CONTRADICTS,
                               ABOUT_CODE, DECIDED_IN, USED_BY, PROMOTED_FROM)

# The three kinds with a LIVE PRODUCER — all three are persisted doc-JSON fields on `MemoryItem`
# (`item.py:286-287,320`), which is precisely what makes them MIGRATABLE rather than inventable.
# The other five are declared and inert:
#   * `contradicts` — contradiction is detected at READ time by `healing.detect_issues` and is
#     never persisted as a relation, so there is no producer to migrate. Wiring it would mean
#     inventing edges.
#   * `used_by` — K5 prompt linkage is not built; the string appears nowhere in `src/`.
#   * `derives_from` / `decided_in` / `promoted_from` — likewise no persisted producer today.
# `_ITEM_FIELD` below is the whole wiring: the map IS the list of wired kinds, so the two cannot
# drift apart (`WIRED_KINDS` is derived from it, never written out a second time).
_ITEM_FIELD: Dict[str, str] = {
    SUPERSEDES: "supersedes",
    DEPENDS_ON: "depends_on",
    ABOUT_CODE: "about_code",
}
WIRED_KINDS: Tuple[str, ...] = tuple(k for k in EDGE_KINDS if k in _ITEM_FIELD)

# The kinds whose DST is another MEMORY ITEM — i.e. the ones where a reference can DANGLE. An
# `about_code` dst is a code path/symbol, which lives in the repo and not in the memory table, so
# "the target does not exist as an item" is not a defect there and must never be counted as one.
ITEM_TARGET_KINDS: Tuple[str, ...] = (SUPERSEDES, DEPENDS_ON, DERIVES_FROM, CONTRADICTS,
                                      DECIDED_IN, PROMOTED_FROM)

# ---------------------------------------------------------------- column names (ONE definition)
# Named here and imported everywhere, exactly as `lifecycle.py` names the v4 item columns: the two
# engines' DDL, the projection SQL and the read SQL all spell them from these constants, so a
# rename is one edit and cannot half-land.
SRC_COLUMN = "src_id"
DST_COLUMN = "dst_id"
KIND_COLUMN = "kind"
VALID_FROM_COLUMN = "valid_from"       # THE validity axis — the item's own two names, reused
VALID_TO_COLUMN = "valid_to"
CREATED_AT_COLUMN = "created_at"       # PROVENANCE (doc 55) — when the ROW was written
CREATED_BY_COLUMN = "created_by"
APPROVAL_LEDGER_COLUMN = "approval_ledger_id"

EDGE_COLUMNS: Tuple[str, ...] = (SRC_COLUMN, DST_COLUMN, KIND_COLUMN,
                                 VALID_FROM_COLUMN, VALID_TO_COLUMN,
                                 CREATED_AT_COLUMN, CREATED_BY_COLUMN, APPROVAL_LEDGER_COLUMN)

# The LOCAL floor's table (a per-repo SQLite file mokata wholly owns) and the SHARED one. Two
# names because they are two tables in two engines; one shape, defined by `EDGE_COLUMNS` above.
LOCAL_EDGES_TABLE = "memory_edges"
SHARED_EDGES_TABLE = "mokata_memory_edges"


class EdgeKindError(MokataError):
    """An edge kind outside the closed set (E5). A HARD refusal, never a coerce-to-default: an
    unrecognised kind stored in the table would be a relation no traversal knows how to read, and
    the whole point of a CLOSED set is that admitting one is a reviewed schema change."""


def validate_kind(kind: str) -> str:
    """The ONE gate on the closed set. Returns `kind` unchanged, or raises `EdgeKindError`.

    Every write path calls this — the local projection, the shared projection, and the team-flush
    projection — so there is no arrangement of the code in which an out-of-set kind reaches disk.
    """
    if kind not in EDGE_KINDS:
        raise EdgeKindError(
            f"unknown memory-edge kind '{kind}' — the typed-edge set is CLOSED at DB.S7a "
            f"({', '.join(EDGE_KINDS)}). Admitting a new kind is a schema change (declare it in "
            f"`memory/edges.EDGE_KINDS`), never a value a caller invents.")
    return kind


@dataclass(frozen=True)
class MemoryEdge:
    """One typed relation, in doc 55's shape plus the R3 validity window.

    Frozen because an edge is a FACT about a moment, not a mutable record: retiring one closes its
    window (a new value in the `valid_to` column), it never edits the assertion in place.
    """

    src_id: str
    dst_id: str
    kind: str
    valid_from: str = ""
    valid_to: str = ""                       # "" / None == OPEN (the live relation)
    created_at: str = ""
    created_by: str = ""
    approval_ledger_id: Optional[int] = None

    def __post_init__(self) -> None:
        validate_kind(self.kind)

    @property
    def is_open(self) -> bool:
        """True while the relation is still asserted. Absence reads as OPEN, for the same reason
        `lifecycle.is_open` does: every edge that exists right now is unambiguously live, and
        reading a missing window as closed would retire the whole projection on upgrade."""
        return not (self.valid_to or "")

    def as_row(self) -> tuple:
        """The column VALUES in `EDGE_COLUMNS` order — the single tuple both engines bind."""
        return (self.src_id, self.dst_id, self.kind, self.valid_from,
                self.valid_to or None, self.created_at, self.created_by,
                self.approval_ledger_id)


def _refs(doc: Dict[str, Any], key: str) -> List[str]:
    """The inline ref list at `key`, defensively. A hand-edited doc, a teammate's import or a
    newer-mokata doc can put anything here; a non-list, a nested list or a non-string entry yields
    NOTHING rather than raising — a malformed inline field must not be able to fail a write (the
    `normalize_applicability` posture DB.S5 landed for the same class of input)."""
    raw = doc.get(key)
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry)
    return out


def open_window_of(doc: Dict[str, Any]) -> str:
    """The `valid_from` an edge projected out of `doc` should carry — `lifecycle.open_window`'s
    rule, expressed over the doc the write path is about to serialize.

    **This is why an edge is never more temporally sophisticated than its item.** The relation is
    part of the item's asserted state, so its window opens when the ITEM's does: the item's own
    `valid_from`, else its `provenance.created_at`. One rule, used identically by the live
    projection and by the migration backfill, so an edge rebuilt from a doc lands on the same
    instant as the edge that was written from it. (Contrast `created_at` on the edge ROW, which is
    when THIS ROW was written and therefore legitimately differs between the two — that is exactly
    the provenance-vs-validity split doc 02 decision #1 settled.)
    """
    provenance = doc.get("provenance")
    created = (provenance or {}).get("created_at") if isinstance(provenance, dict) else ""
    return (doc.get("valid_from") or "") or (created or "") or ""


def edges_from_doc(doc: Dict[str, Any], *, created_at: str = "",
                   approval_ledger_id: Optional[int] = None) -> List[MemoryEdge]:
    """THE PROJECTION — every edge the three WIRED inline fields of `doc` assert, in a stable order.

    It takes the DOC, not the item, and that is deliberate: each write path passes the very dict it
    is about to `json.dumps` into the `doc` column, so the edge rows and the doc are derived from
    the same object in the same statement (the `scope_columns_from_doc` / `validity_columns_from_doc`
    argument, one level up). There is no arrangement of the code in which they disagree.

    Duplicates within one inline list collapse to ONE edge — `supersedes: [a, a]` asserts one
    relation, and two identical open edges are exactly what the partial unique index forbids.
    """
    src = doc.get("id") or ""
    if not src:
        return []
    valid_from = open_window_of(doc)
    author = ((doc.get("provenance") or {}).get("author")
              if isinstance(doc.get("provenance"), dict) else "") or ""
    out: List[MemoryEdge] = []
    seen: set = set()
    for kind in WIRED_KINDS:
        for dst in _refs(doc, _ITEM_FIELD[kind]):
            if (dst, kind) in seen:
                continue
            seen.add((dst, kind))
            out.append(MemoryEdge(src_id=src, dst_id=dst, kind=kind, valid_from=valid_from,
                                  valid_to="", created_at=created_at or valid_from,
                                  created_by=author, approval_ledger_id=approval_ledger_id))
    return out


def edges_from_item(item: Any, **kw) -> List[MemoryEdge]:
    """`edges_from_doc` over an item's durable serialization. Convenience only — the doc form is
    the one every write path uses, because the write path already HAS the doc."""
    return edges_from_doc(item.to_doc(), **kw)


# ---------------------------------------------------------------- SQL, built once for both engines
# The statements are assembled HERE from the column constants so the SQLite floor, the shared
# Postgres table and the team-flush projection are literally the same SQL with a different
# placeholder. `p` is the engine's placeholder ("?" on SQLite, "%s" on psycopg) and `table` is one
# of the two module constants above — both are mokata CONSTANTS, never caller input, so the f-string
# interpolation carries no value and every real value travels bound.

def insert_open_sql(table: str, *, placeholder: str = "?") -> str:
    """INSERT one OPEN edge, IDEMPOTENT BY PREDICATE (E1).

    `WHERE NOT EXISTS (an open edge already saying this)` is the whole idempotency story, and it is
    the DB.S2b/DB.S5 backfill shape rather than an `ON CONFLICT` clause on purpose: `ON CONFLICT`
    would need the partial unique index to be the conflict target, which SQLite and Postgres spell
    differently, and a predicate that reads the same on both engines is worth more than a saved
    round trip. A second run matches zero rows and touches nothing.
    """
    p = placeholder
    cols = ", ".join(EDGE_COLUMNS)
    return (f"INSERT INTO {table} ({cols}) "                                   # nosec B608
            f"SELECT {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p} "
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} "
            f"                  WHERE {SRC_COLUMN}={p} AND {DST_COLUMN}={p} "
            f"                    AND {KIND_COLUMN}={p} AND {VALID_TO_COLUMN} IS NULL)")


def insert_open_params(edge: MemoryEdge) -> tuple:
    """The bound values for `insert_open_sql` — the row, then the three the predicate re-reads."""
    return (*edge.as_row(), edge.src_id, edge.dst_id, edge.kind)


def close_withdrawn_sql(table: str, kind: str, keep: Sequence[str], *,
                        placeholder: str = "?") -> Tuple[str, tuple]:
    """CLOSE (never delete) every OPEN `kind` edge out of one src whose dst is no longer asserted.

    This is R3's "contradiction = invalidation, never deletion" on the projection side, and the
    reason inline-authoritative is safe: when a human's gated edit removes a ref from
    `supersedes[]`, the corresponding edge stops being true, and the honest record of that is a
    CLOSED window — the row, its `valid_from` and its provenance all survive and stay traversable
    as history. A DELETE here would silently destroy the only record that the relation ever held.

    `keep` empty is the meaningful case, not an edge case: an item that now asserts NO edges of
    this kind closes all of them, and the generated SQL says exactly that.
    """
    p = placeholder
    sql = (f"UPDATE {table} SET {VALID_TO_COLUMN}={p} "                         # nosec B608
           f"WHERE {SRC_COLUMN}={p} AND {KIND_COLUMN}={p} AND {VALID_TO_COLUMN} IS NULL")
    params: List[Any] = []
    if keep:
        sql += f" AND {DST_COLUMN} NOT IN ({', '.join([p] * len(keep))})"
        params = list(keep)
    return sql, tuple(params)


def open_edges_sql(table: str, *, placeholder: str = "?") -> str:
    """Every OPEN edge out of one src, newest-window-last. The substrate's own read — bounded
    ≤2-hop traversal over recursive CTEs is DB.S7b and deliberately not started here."""
    p = placeholder
    cols = ", ".join(EDGE_COLUMNS)
    return (f"SELECT {cols} FROM {table} "                                     # nosec B608
            f"WHERE {SRC_COLUMN}={p} AND {VALID_TO_COLUMN} IS NULL "
            f"ORDER BY {KIND_COLUMN}, {DST_COLUMN}")


def edge_from_row(row: Sequence[Any]) -> MemoryEdge:
    """A DB row (in `EDGE_COLUMNS` order) back into a `MemoryEdge`. NULL `valid_to` reads as the
    empty string, which `is_open` and the item-side `lifecycle.is_open` both treat as OPEN."""
    ledger = row[7]
    return MemoryEdge(src_id=row[0], dst_id=row[1], kind=row[2],
                      valid_from=row[3] or "", valid_to=row[4] or "",
                      created_at=row[5] or "", created_by=row[6] or "",
                      approval_ledger_id=int(ledger) if ledger is not None else None)


def project_edges(conn: Any, table: str, doc: Dict[str, Any], *, now: str,
                  placeholder: str = "?",
                  approval_ledger_id: Optional[int] = None) -> int:
    """MAINTAIN the projection for one item, on an OPEN connection, in the caller's transaction.

    Two halves, and the order is deliberate — CLOSE the withdrawn refs first, then OPEN the
    asserted ones. Doing it the other way round would let a re-assertion of a ref that is also
    being withdrawn (the same dst removed from one kind and added to another in one edit) be closed
    immediately after it was opened.

    It runs INSIDE the caller's transaction and commits nothing of its own. That is what makes E6
    provable rather than asserted: on the local floor this executes on the same connection as the
    item's own INSERT and lands in the same `commit()`, and on the team path it executes on the
    flush connection only AFTER the item's compare-and-set matched, inside the approval group's
    transaction. An item write that is declined, refused or rolled back therefore cannot leave an
    edge row behind — there is no code path that writes one on its own.

    Returns the number of OPEN edges asserted (for the caller's report; the close count is not
    interesting — an edge that was never open cannot be withdrawn).
    """
    src = doc.get("id") or ""
    if not src:
        return 0
    asserted = edges_from_doc(doc, created_at=now, approval_ledger_id=approval_ledger_id)
    by_kind: Dict[str, List[str]] = {k: [] for k in WIRED_KINDS}
    for edge in asserted:
        by_kind[edge.kind].append(edge.dst_id)
    for kind in WIRED_KINDS:
        sql, params = close_withdrawn_sql(table, kind, by_kind[kind], placeholder=placeholder)
        conn.execute(sql, (now, src, validate_kind(kind), *params))
    stmt = insert_open_sql(table, placeholder=placeholder)
    for edge in asserted:
        validate_kind(edge.kind)
        conn.execute(stmt, insert_open_params(edge))
    return len(asserted)
