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
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing, contextmanager
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .item import MemoryItem


class MemoryBackend(ABC):
    name: str = ""

    @abstractmethod
    def put(self, item: MemoryItem) -> None: ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[MemoryItem]: ...

    @abstractmethod
    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: str) -> bool: ...

    def update(self, item: MemoryItem) -> None:
        """Upsert (storage is keyed by id)."""
        self.put(item)

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
        self._memory = path in (":memory:", "") or "mode=memory" in path
        if not self._memory:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._mem_conn = sqlite3.connect(path) if self._memory else None
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
            conn.commit()

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
        (closing it would discard the data — and it has no file handle to leak)."""
        if self._memory:
            yield self._mem_conn
        else:
            with closing(sqlite3.connect(self.path)) as conn:
                yield conn

    def put(self, item: MemoryItem) -> None:
        doc = json.dumps(item.to_dict())
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
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT doc FROM memory ORDER BY seq"
            ).fetchall()
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in rows]
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

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
            f"{_FENCE}json\n{json.dumps(item.to_dict(), indent=2)}\n{_FENCE}\n"
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
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
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
        return items

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
        self.client.put(item.to_dict())

    def get(self, item_id: str) -> Optional[MemoryItem]:
        doc = self.client.get(item_id)
        return MemoryItem.from_dict(doc) if doc else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        items = [MemoryItem.from_dict(d) for d in self.client.all()]
        items.sort(key=lambda i: (i.created_at, i.id))
        if mtype is not None:
            items = [i for i in items if i.mtype == mtype]
        if statuses is not None:
            items = [i for i in items if i.status in statuses]
        return items

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


class PostgresUnavailable(Exception):
    """Raised when the Postgres backend can't be built — psycopg missing or the DB
    unreachable. The caller catches this and degrades to the SQLite floor (never a
    hard failure: 'degrade, never break')."""


class PostgresBackend(MemoryBackend):
    """The team's LIVE shared memory store — a hosted/remote backend (`kind: "external"`)
    whose schema mokata OWNS: it runs `CREATE TABLE IF NOT EXISTS mokata_memory (…)` on connect and
    implements the full `MemoryBackend` contract (put/upsert/get/all/delete/close), so
    pointing a whole team's mokata at one Postgres DSN makes everyone read/write the same
    store live. `autocommit` is on so one client's committed write is immediately visible to
    the others; conflicts are surfaced (not silently merged) by the store's self-healing
    layer, writes stay human-gated (P2) and provenance-carrying, and the adapter is
    trust-dialed (P15) — this class is storage only, the policy lives in the store.

    Opt-in / local-first (P8): nothing connects unless the user wires `config.dsn_env`. The
    DSN comes from that env var (never inline in the committed manifest). `psycopg` is an
    optional extra, lazy-imported here; its absence — or an unreachable database — raises
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
            self._conn = conn
            for stmt in self._setup_sql():
                try:
                    self._conn.execute(stmt)
                except Exception:  # pragma: no cover - a fake may no-op DDL
                    pass
            return
        from ._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, PostgresUnavailable, setup_sql=self._setup_sql())

    @classmethod
    def _setup_sql(cls) -> List[str]:
        # IF-NOT-EXISTS mirrors of the shared schema `team init` owns (teamdb.provision_sql) —
        # a no-op on a provisioned DB. TM.S6 (doc 62 §2–3) adds the scope + precedence columns;
        # the authoritative scope value still lives in the item `doc` JSON, so runtime reads work
        # regardless, and these columns are provisioned for future SQL-side scope filtering.
        return [
            f"CREATE TABLE IF NOT EXISTS {cls.TABLE} ("
            "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT,"
            "  status TEXT, doc TEXT, seq BIGSERIAL, project TEXT)",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
            # TM.S5c — mirror the team-init-owned CAS columns (teamdb v2) so this backend's read
            # path can surface the row `revision` (the compare-and-set base). Same idempotent
            # IF-NOT-EXISTS mirror pattern as the scope columns below; DDL ownership stays team
            # init's — this is a provisioned mirror, not a new migration.
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS revision INT NOT NULL DEFAULT 1",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS scope_level "
            "TEXT NOT NULL DEFAULT 'personal'",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS scope_id TEXT",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS pin BOOLEAN NOT NULL DEFAULT FALSE",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 0",
        ]

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
             json.dumps(item.to_dict()), self.project),
        )

    def get(self, item_id: str) -> Optional[MemoryItem]:
        clause, params = self._scope()
        row = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE} WHERE id=%s{clause}",  # nosec B608
            (item_id, *params)).fetchone()
        return _with_revision(row[0], row[1]) if row else None

    def all(self, mtype: Optional[str] = None,
            statuses: Optional[Tuple[str, ...]] = None) -> List[MemoryItem]:
        clause, params = self._scope(prefix="WHERE")
        rows = self._conn.execute(
            f"SELECT doc, revision FROM {self.TABLE}{clause} ORDER BY seq",  # nosec B608
            params).fetchall()
        items = [_with_revision(r[0], r[1]) for r in rows]
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
        """The distinct project keys present in the shared table — for review `--list-projects`.
        A pre-71a (NULL) row reads back as the LEGACY bucket. Never raises on an odd row."""
        rows = self._conn.execute(
            f"SELECT DISTINCT project FROM {self.TABLE}").fetchall()  # nosec B608
        return _distinct_projects(rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass


def _distinct_projects(rows: List[tuple]) -> List[str]:
    """Normalize a `SELECT DISTINCT project` result: NULL/empty → the LEGACY bucket; sorted."""
    from ..project import LEGACY_PROJECT
    out = {(r[0] if r and r[0] else LEGACY_PROJECT) for r in rows}
    return sorted(out)


def build_postgres_backend(config: Dict[str, Any],
                           project: Optional[str] = None) -> Optional["PostgresBackend"]:
    """Build a Postgres backend from a per-tool `config`, or return None to degrade.

    Honors ONLY `config.dsn_env` — the name of an env var holding the DSN. An inline
    `dsn` is never read (the manifest is committed; a plaintext credential would be a
    leak the secret-guard blocks). Returns None when the env var is unset/empty, psycopg
    is absent, or the database is unreachable — so the caller falls to the SQLite floor.
    `project` (Stage 71a) SCOPES all rows to the current project; None spans all (review).
    """
    dsn_env = (config or {}).get("dsn_env")
    if not dsn_env:
        return None
    dsn = os.environ.get(dsn_env)
    if not dsn:
        return None
    try:
        return PostgresBackend(dsn, project=project)
    except PostgresUnavailable:
        return None
