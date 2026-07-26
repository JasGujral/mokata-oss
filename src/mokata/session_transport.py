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
from contextlib import nullcontext
from typing import Any, List, Optional

from . import MOKATA_DIR
from .atomicfile import atomic_write_text, lock_path_for
from .dsn import DEFAULT_DSN_ENV
from .oslock import file_lock
from .errors import DegradedCapability

# The env vars the Postgres transport reads its DSN from (never inline in the committed manifest,
# exactly like the shared-memory backend). The session-specific override var wins; then the
# CONFIGURED team DSN name (via the ONE resolver — so `team connect --dsn-env CUSTOM` reaches
# sessions too, C-1); then the literal default (which `resolve_dsn_env` supplies). The default
# NAME lives only in `dsn.py`.
SESSION_OVERRIDE_ENV = "MOKATA_SESSION_PG_DSN"
# Kept for the degrade-clean "needs a DSN in $X / $Y" hint (the two well-known names).
PG_DSN_ENVS = (SESSION_OVERRIDE_ENV, DEFAULT_DSN_ENV)
PG_TABLE = "mokata_session_bundle"          # mokata-OWNED, namespaced (never a generic name)

LOCAL_DIRNAME = "session-bundles"           # 55a's store (kept identical)
VAULT_SUBDIR = "sessions"                   # bundles namespaced inside `.mokata/vault/`

# Stage 71a — sentinel meaning "scope to the CURRENT project (derive from root)"; distinct from
# ALL_PROJECTS (None → span all) and from a concrete project-id string.
_PROJECT_CURRENT = object()


class SessionTransportUnavailable(DegradedCapability):
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

    def lock(self, tag: str):
        """MS.S6 — hold the cross-process lock for THIS tag (`oslock`, the shared MS.S1 primitive),
        so a push/rename can check whether the name is free and claim it INDIVISIBLY. The handle is
        a dot-prefixed `.lock` sibling, so `list_tags` (which matches `*.json`) never sees it."""
        return file_lock(lock_path_for(self._path(tag)))

    def write_bundle(self, tag: str, blob: str) -> str:
        # MS.S6 — atomic replace: a teammate reading a bundle mid-push can never get a half-written
        # one (which `verify_bundle` would reject as corrupt, losing a session that was in fact fine).
        path = self._path(tag)
        atomic_write_text(path, blob)
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
            self._conn = client               # an injected connection: already provisioned.
            return
        if not dsn:
            raise SessionTransportUnavailable(
                "the Postgres session transport needs a DSN in $"
                + " / $".join(PG_DSN_ENVS) + " (never inline in the committed manifest)")
        # D1 — VERIFY-ONLY: `team init` (teamdb) owns this table. The `ON CONFLICT (project, tag)`
        # upsert below resolves via the composite PK it provisions.
        from .memory._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, SessionTransportUnavailable)

    def _scope(self, prefix: str = "AND") -> tuple:
        if self.project is None:
            return "", ()
        return f" {prefix} project=%s", (self.project,)

    def location(self, tag: str) -> str:
        return f"postgres:{self.TABLE}/{_safe_tag(tag)}"

    def lock(self, tag: str):
        """No local lock: the DB is the serialization point (the composite PK + `ON CONFLICT` upsert
        is already indivisible, and a lock file on THIS machine could not serialise a teammate on
        another one anyway). The claim re-check in `commit_session_push` still runs — it reads the
        row back before overwriting, so a push that would clobber a different session still fails
        honestly rather than silently."""
        return nullcontext(self.location(tag))

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
def _root_manifest_data(root: Optional[str]) -> Optional[dict]:
    """Best-effort manifest dict for `root` — for CONFIGURED-team-name resolution. Reads the
    manifest AT `root` (never guesses from cwd); degrade-clean → None (→ the default env name)
    for an uninitialized/broken repo, never raises."""
    if not root:
        return None
    from .config import ConfigError, Surface
    try:
        if not Surface.is_initialized(root):
            return None
        return Surface.load(root).manifest.data or None
    except (ConfigError, OSError, ValueError):
        # D5 — the real raisers: `Surface.load` re-raises a broken/unparseable manifest as
        # `ConfigError` (this is the one that actually fires — WITHOUT it the narrowing would turn
        # the swallow into a CRASH on the session path), plus an unreadable manifest file (OSError)
        # and a torn JSON document (ValueError). None → the DEFAULT DSN env name, unchanged.
        return None


def resolve_pg_dsn(dsn_env: Optional[str] = None, *, data: Optional[dict] = None,
                   root: Optional[str] = None, environ: Optional[dict] = None) -> Optional[str]:
    """The DSN for the Postgres transport. Resolution order (C-1, matches memory/journal): an
    explicit per-call name → the session override var (`$MOKATA_SESSION_PG_DSN`) → the CONFIGURED
    team DSN name (via `dsn.resolve_dsn_env`, which also supplies the literal default). `data`
    (or `root`, from which the manifest is loaded — never cwd) lets a `team connect --dsn-env
    CUSTOM` reach sessions. None when none is set (the caller degrades clean)."""
    from .dsn import resolve_dsn_env
    env = os.environ if environ is None else environ
    if data is None and root is not None:
        data = _root_manifest_data(root)
    names = ([dsn_env] if dsn_env else []) + [SESSION_OVERRIDE_ENV, resolve_dsn_env(data)]
    for name in names:
        val = env.get(name) if name else None
        if val:
            return val
    return None


def transport_kind_for_mode(root: Optional[str]) -> str:
    """SIMP.S1 — DERIVE the session transport from the repo's MODE (never picked from a channel
    zoo). A TEAM-CONNECTED repo → 'postgres' (the ONE configured Postgres DSN, the same DB shared
    memory/journal use); otherwise → 'local' (SQLite+files). The team signal is `team.connect_status`
    — the CM.S1 one-DSN resolver state (`settings.team.dsn_env` + postgres in the memory chain), the
    SAME persisted signal the memory/journal side keys on (C-1: never a second mode-resolution path).
    It reflects team-CONNECT config, NOT whether the DSN env var VALUE is currently exported — so a
    team member with an unset DSN still derives 'postgres' and gets a clean refusal from
    `make_transport` (deliverable 4), never a silent downgrade to a local file.

    FAIL CLOSED on unknown mode. This function decides WHERE DURABLE BYTES GO, so it must never
    GUESS 'local' when the mode is undetermined:
      * manifest ABSENT / repo uninitialized → 'local' (genuinely not a team repo);
      * manifest PRESENT but unreadable / torn / invalid → raise `SessionTransportUnavailable`
        (mode is unknown — guessing 'local' would write a PRIVATE file while a team human believes
        it reached the shared store: deliverable 4's silent downgrade through the config-read door).
    The explicit-kind and `--file` overrides remain the human's escape hatch and MUST still work on
    such a repo. Returns a kind NAME only (never a DSN value)."""
    if not root:
        return "local"
    from .config import ConfigError, Surface
    # `is_initialized` is a bare `os.path.exists` (never raises): manifest ABSENT → genuinely not a
    # team repo → 'local'. Only a PRESENT manifest reaches `load`, so the except below can only mean
    # "manifest present but broken", never "no repo here".
    if not Surface.is_initialized(root):
        return "local"
    try:
        surface = Surface.load(root)
    except (ConfigError, OSError, ValueError) as exc:
        # FAIL CLOSED: manifest present but unreadable/torn/invalid — mode is UNKNOWN. Refuse loudly
        # rather than silently downgrade a team repo to a private local file.
        raise SessionTransportUnavailable(
            "cannot determine repo mode — manifest unreadable; fix .mokata/manifest.json or pass "
            "an explicit transport / --file") from exc
    from .team import connect_status
    return "postgres" if connect_status(surface) else "local"


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
        # SIMP.S2 — the vault transport kind is DEPRECATED (removed 0.0.17). It keeps working
        # (the shim), warning once per repo, with `mokata migrate vault` to re-home the bundles.
        from . import deprecation
        deprecation.warn_deprecated("vault", os.path.join(root, MOKATA_DIR))
        return VaultTransport(root)
    if kind == "postgres":
        from .project import derive_project_id
        scope = derive_project_id(root) if project is _PROJECT_CURRENT else project
        if client is not None:
            return PostgresTransport(client=client, project=scope)
        # thread `root` so the resolver honors the CONFIGURED team DSN name (C-1) — the same DB
        # memory/journal use — instead of silently falling through to the literal default.
        return PostgresTransport(dsn=resolve_pg_dsn(dsn_env, root=root), project=scope)
    raise SessionTransportUnavailable(
        f"unknown session transport '{kind}' (use local | vault | postgres)")


TRANSPORT_KINDS = ("local", "vault", "postgres")
