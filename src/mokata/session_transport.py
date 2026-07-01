"""Stage 55b — pluggable TRANSPORT for portable session bundles (remote / shared storage).

55a packaged a session into a gated, machine-path-free, secret-scanned, content-hashed BUNDLE
and shared it as a LOCAL file. 55b lets that SAME bundle travel over more than one byte store so
a teammate can pull it — without touching any of the gates. The split is deliberate:

  * the GATED logic stays in `session_bundle` — secret-scan + human gate (push AND pull),
    content-hash verify, repo-fingerprint check. The transport is storage ONLY;
  * a transport just moves bytes: `write_bundle(tag, blob)` / `read_bundle(tag)` /
    `list_tags()` / `delete_bundle(tag)`. Because every push/pull still runs through the
    `session_bundle` gates regardless of transport, a remote can NEVER downgrade security.

Three implementations, backend-agnostic for team sharing:
  * `local`   — 55a's `.mokata/session-bundles/` file store (the default);
  * `vault`   — the committed/synced artifact store (`.mokata/vault/sessions/`, Stage 35d's
                pattern) — bundles travel with the repo for a teammate to pull;
  * `postgres`— a shared, OWNED, namespaced table (`mokata_session_bundle`) reached by an
                env-var DSN, mirroring the shared-memory Postgres backend. OPT-IN and
                LOCAL-FIRST: absent psycopg or DSN, it DEGRADES CLEAN (a clear
                `SessionTransportUnavailable`, never a crash, never a silent fallback).

`psycopg` is an optional extra (like the memory Postgres backend); the core stays
dependency-free. Clean-room. Copyright 2026 MoStack. Licensed under the Apache License,
Version 2.0.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from . import MOKATA_DIR

# The env vars the Postgres transport reads its DSN from (never inline in the committed manifest,
# exactly like the shared-memory backend). The session-specific var wins; the shared one is the
# fallback so a team that already points memory at one DB needs no extra config.
PG_DSN_ENVS = ("MOKATA_SESSION_PG_DSN", "MOKATA_PG_DSN")
PG_TABLE = "mokata_session_bundle"          # mokata-OWNED, namespaced (never a generic name)

LOCAL_DIRNAME = "session-bundles"           # 55a's store (kept identical)
VAULT_SUBDIR = "sessions"                   # bundles namespaced inside `.mokata/vault/`

# Stage 71a — sentinel meaning "scope to the CURRENT project (derive from root)"; distinct from
# ALL_PROJECTS (None → span all) and from a concrete project-id string.
_PROJECT_CURRENT = object()


class SessionTransportUnavailable(Exception):
    """Raised when a transport can't be built — e.g. psycopg missing or no DSN. The caller
    degrades cleanly (a clear message, no write anywhere) and NEVER silently falls back to a
    less-secure store."""


def _safe_tag(tag: str) -> str:
    # reuse 55a's path-free tag check (lazy import avoids an import cycle with session_bundle)
    from .session_bundle import _safe_tag as _impl
    return _impl(tag)


# --------------------------------------------------------------------------------- file stores
class _FileTransport:
    """Shared implementation for the file-backed transports (local + vault): a directory of
    `<tag>.json` blobs. Degrade-clean — a missing dir lists empty, never raises."""

    name = "file"

    def __init__(self, directory: str) -> None:
        self.dir = directory

    def _path(self, tag: str) -> str:
        return os.path.join(self.dir, f"{_safe_tag(tag)}.json")

    def location(self, tag: str) -> str:
        return self._path(tag)

    def write_bundle(self, tag: str, blob: str) -> str:
        path = self._path(tag)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(blob)
        return path

    def read_bundle(self, tag: str) -> Optional[str]:
        path = self._path(tag)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def list_tags(self) -> List[str]:
        if not os.path.isdir(self.dir):
            return []
        return sorted(fn[:-len(".json")] for fn in os.listdir(self.dir)
                      if fn.endswith(".json"))

    def delete_bundle(self, tag: str) -> bool:
        path = self._path(tag)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


class LocalTransport(_FileTransport):
    """55a's default — `.mokata/session-bundles/`. Byte-identical to the Stage 55a file store."""

    name = "local"

    def __init__(self, root: str) -> None:
        super().__init__(os.path.join(root, MOKATA_DIR, LOCAL_DIRNAME))


class VaultTransport(_FileTransport):
    """The committed/synced artifact store (Stage 35d pattern): `.mokata/vault/sessions/`, so a
    pushed session travels with the repo for a teammate to pull + resume."""

    name = "vault"

    def __init__(self, root: str) -> None:
        super().__init__(os.path.join(root, MOKATA_DIR, "vault", VAULT_SUBDIR))


# --------------------------------------------------------------------------------- postgres
class PostgresTransport:
    """A shared, OWNED, namespaced session-bundle table reached by an env-var DSN — the
    backend-agnostic team-sharing leg (mirrors the shared-memory Postgres backend). `autocommit`
    so one client's push is immediately visible to the others. The bundle bytes are still gated
    by `session_bundle`; this class is storage only."""

    name = "postgres"
    TABLE = PG_TABLE

    def __init__(self, dsn: Optional[str] = None, client: Any = None,
                 project: Optional[str] = None) -> None:
        # Stage 71a — SCOPE every bundle by the current project so one shared DSN safely hosts many
        # projects (a `tag` like "auth" no longer collides across projects). `project=None` spans
        # ALL projects (review `--all` / `list_projects` only).
        self.project = project
        if client is not None:
            # an injected connection (tests / a host-provided client); ensure the owned table.
            self._conn = client
            for stmt in self._setup_sql():
                try:
                    self._conn.execute(stmt)
                except Exception:  # pragma: no cover - a fake may no-op DDL
                    pass
            return
        if not dsn:
            raise SessionTransportUnavailable(
                "the Postgres session transport needs a DSN in $"
                + " / $".join(PG_DSN_ENVS) + " (never inline in the committed manifest)")
        from .memory._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, SessionTransportUnavailable,
                                     setup_sql=self._setup_sql())

    @classmethod
    def _setup_sql(cls) -> List[str]:
        # Fresh table: composite PRIMARY KEY (project, tag) → full multi-project correctness.
        # The ADD-COLUMN + UNIQUE-INDEX migrate a pre-71a table (PK on `tag` alone); its old rows
        # read back as legacy/unscoped under `--all`. `ON CONFLICT (project, tag)` resolves via the
        # composite PK (fresh) or the unique index (migrated).
        return [
            f"CREATE TABLE IF NOT EXISTS {cls.TABLE} ("
            "  tag TEXT, blob TEXT, seq BIGSERIAL, project TEXT,"
            "  PRIMARY KEY (project, tag))",
            f"ALTER TABLE {cls.TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
            f"CREATE UNIQUE INDEX IF NOT EXISTS {cls.TABLE}_project_tag"
            f"  ON {cls.TABLE} (project, tag)",
        ]

    def _scope(self, prefix: str = "AND") -> tuple:
        if self.project is None:
            return "", ()
        return f" {prefix} project=%s", (self.project,)

    def location(self, tag: str) -> str:
        return f"postgres:{self.TABLE}/{_safe_tag(tag)}"

    # Justification for the B608 suppressions below (bandit false positive): the SQL interpolates
    # ONLY the mokata-OWNED constant `self.TABLE` (+ the fixed `_scope()` fragment), never user
    # input; every VALUE (tag/blob/project) rides the driver's `%s` placeholders. Suppression
    # markers only — no injection surface, no behaviour change.
    def write_bundle(self, tag: str, blob: str) -> str:
        tag = _safe_tag(tag)
        self._conn.execute(
            f"INSERT INTO {self.TABLE} (project, tag, blob) VALUES (%s, %s, %s)"  # nosec B608
            " ON CONFLICT (project, tag) DO UPDATE SET blob=EXCLUDED.blob",
            (self.project, tag, blob))
        return self.location(tag)

    def read_bundle(self, tag: str) -> Optional[str]:
        clause, params = self._scope()
        row = self._conn.execute(
            f"SELECT blob FROM {self.TABLE} WHERE tag=%s{clause}",  # nosec B608
            (_safe_tag(tag), *params)).fetchone()
        return row[0] if row else None

    def list_tags(self) -> List[str]:
        clause, params = self._scope(prefix="WHERE")
        rows = self._conn.execute(
            f"SELECT tag FROM {self.TABLE}{clause} ORDER BY tag", params).fetchall()  # nosec B608
        return [r[0] for r in rows]

    def delete_bundle(self, tag: str) -> bool:
        clause, params = self._scope()
        cur = self._conn.execute(
            f"DELETE FROM {self.TABLE} WHERE tag=%s{clause}", (_safe_tag(tag), *params))  # nosec B608
        return getattr(cur, "rowcount", 0) > 0

    def list_projects(self) -> List[str]:
        """Distinct project keys with bundles present — for `session --list-projects`."""
        from .project import LEGACY_PROJECT
        rows = self._conn.execute(
            f"SELECT DISTINCT project FROM {self.TABLE}").fetchall()  # nosec B608
        return sorted({(r[0] if r and r[0] else LEGACY_PROJECT) for r in rows})


# --------------------------------------------------------------------------------- factory
def resolve_pg_dsn(dsn_env: Optional[str] = None) -> Optional[str]:
    """The DSN for the Postgres transport: an explicit env-var name first, else the standard
    session / shared vars. Returns None when none is set (the caller degrades clean)."""
    names = (dsn_env,) + PG_DSN_ENVS if dsn_env else PG_DSN_ENVS
    for name in names:
        val = os.environ.get(name) if name else None
        if val:
            return val
    return None


def make_transport(kind: Optional[str], root: str, *, dsn_env: Optional[str] = None,
                   client: Any = None, project: Any = _PROJECT_CURRENT) -> Any:
    """Build a transport by name (`local` default, `vault`, `postgres`). The Postgres leg is
    OPT-IN and degrades clean: with no injected client and no DSN it raises
    `SessionTransportUnavailable` (a clear message) rather than crashing or silently downgrading
    to a less-secure store. Stage 71a — the Postgres leg is SCOPED to the current project by
    default (derived from `root`); pass `project=` a specific id, or `ALL_PROJECTS` (None) to span
    all. Local/vault are per-repo already and ignore it."""
    kind = (kind or "local").lower()
    if kind == "local":
        return LocalTransport(root)
    if kind == "vault":
        return VaultTransport(root)
    if kind == "postgres":
        from .project import derive_project_id
        scope = derive_project_id(root) if project is _PROJECT_CURRENT else project
        if client is not None:
            return PostgresTransport(client=client, project=scope)
        return PostgresTransport(dsn=resolve_pg_dsn(dsn_env), project=scope)
    raise SessionTransportUnavailable(
        f"unknown session transport '{kind}' (use local | vault | postgres)")


TRANSPORT_KINDS = ("local", "vault", "postgres")
