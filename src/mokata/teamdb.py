"""TM.S2 — the team shared-DB layer: the real fail-closed preflight probe.

`team` mode activates against a **reachable, compatible** Postgres and refuses — with a named
fix — when it isn't. This module owns the READ-ONLY probe that makes that call:

  * reachability — connect (via the doc-48 connection manager, timeout-bounded) + a cheap
    `SELECT 1` round-trip, wall-clock bounded to a ≤500ms budget (doc 48 E2);
  * schema compatibility — READ `mokata_schema_version` and compare to the version this build
    speaks. The table + all DDL are owned by `team init` (TM.S3, doc 48 C4) — this probe
    **never runs DDL**, only SELECTs.

Golden path = plain Postgres ≥14, NO extensions (ADR-54): the probe requires none. `psycopg`
stays an optional extra (lazy import via `memory/_pg.py`); a missing driver degrades to a
clear "driver absent" verdict, never a crash.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

# The shared-schema version this build of mokata speaks. `team init` (TM.S3) writes/migrates
# the row in `mokata_schema_version`; the probe reads it and refuses on a mismatch. Bump this
# in lockstep with a breaking shared-schema change.
#
# v1 → v2 (TM.S5, doc 48 C1): `mokata_memory` gains `revision` + `updated_at` so team memory
# writes are compare-and-set (a concurrent-writer conflict SURFACES + is human-gated, never a
# silent last-writer-wins). The migration is idempotent ADD-COLUMN-IF-NOT-EXISTS, owned here by
# `team init` (never at runtime — C4). An existing team upgrades by RE-RUNNING `mokata team init`
# (the S2 incompatible-version check refuses activation until the shared schema is migrated).
#
# v2 → v3 (TM.S6, doc 62 §2–3): `mokata_memory` gains the scope + precedence fields
# (`scope_level`, `scope_id`, `pin`, `priority`) so a shared item records its home level in the
# broad→narrow hierarchy and its precedence hints. Same idempotent ADD-COLUMN-IF-NOT-EXISTS
# migration, owned by `team init`; the authoritative scope value also lives in the item's `doc`
# JSON (so a runtime read needs no schema change), while these columns are provisioned for
# future SQL-side scope filtering (memory-at-scale, 0.1.3). An existing team upgrades by
# RE-RUNNING `mokata team init`; the S2 incompatible-version check fail-closes older clients
# until they do.
TEAM_SCHEMA_VERSION = 3
SCHEMA_VERSION_TABLE = "mokata_schema_version"

# CAS columns on the memory table (doc 48 C1). `revision` starts at 1 on insert and bumps on
# every accepted update; `updated_at` is advisory provenance. Runtime NEVER adds these (DDL is
# `team init`'s — C4); the flush's compare-and-set assumes a v2-provisioned table.
MEMORY_REVISION_COLUMN = "revision"
MEMORY_UPDATED_AT_COLUMN = "updated_at"

# v3 scope + precedence columns on the memory table (TM.S6, doc 62 §2–3). Provisioned by
# `team init`; the item `doc` JSON remains the authoritative store of these values.
MEMORY_SCOPE_LEVEL_COLUMN = "scope_level"
MEMORY_SCOPE_ID_COLUMN = "scope_id"
MEMORY_PIN_COLUMN = "pin"
MEMORY_PRIORITY_COLUMN = "priority"

# The mokata-OWNED shared tables `team init` provisions (doc 48 §3). Each name mirrors the
# table the corresponding runtime backend already uses, so a runtime connect finds it present
# and its own IF-NOT-EXISTS is a no-op:
#   memory items    -> memory/backends.py PostgresBackend
#   session bundles -> session_transport.PostgresTransport
#   audit ledger    -> team_audit.SharedAuditLog (append-only)
#   events          -> provisioned only; local-first population until a later UI (doc 48)
MEMORY_TABLE = "mokata_memory"
SESSION_TABLE = "mokata_session_bundle"
AUDIT_TABLE = "mokata_audit_log"
EVENTS_TABLE = "mokata_events"

# doc 48 E2 — the session-start health probe is hard-capped at 500ms wall-clock.
PROBE_BUDGET_MS = 500

# Postgres SQLSTATE for "relation does not exist" — the schema-version table not being present
# is the reachable-but-not-provisioned signal (→ `mokata team init`).
_UNDEFINED_TABLE = "42P01"

# A read-only SELECT of the newest schema-version row. NEVER DDL (C4 — DDL is team init's).
_VERSION_SQL = (
    f"SELECT version FROM {SCHEMA_VERSION_TABLE} ORDER BY version DESC LIMIT 1"
)


class _ProbeUnavailable(Exception):
    """Internal — the connection manager's typed failure for the probe path."""


@dataclass
class ProbeResult:
    """The verdict of one team-DB probe. Every field is derived read-only; `compatible` is
    True only when the DB is reachable, the schema is present, AND its version matches."""

    driver_present: bool = True
    reachable: bool = False
    schema_present: bool = False
    schema_version: Optional[int] = None
    compatible: bool = False
    elapsed_ms: float = 0.0
    detail: str = ""
    error: str = ""


def _read_schema_version(conn: Any) -> "tuple[bool, Optional[int]]":
    """(schema_present, version). A `42P01` (undefined table) means the schema-version table
    isn't provisioned yet → (False, None). No rows → (True, None) (table exists but empty)."""
    try:
        row = conn.execute(_VERSION_SQL).fetchone()
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == _UNDEFINED_TABLE:
            return False, None
        raise
    if not row:
        return True, None
    try:
        return True, int(row[0])
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return True, None


def probe(dsn: str, *, budget_ms: int = PROBE_BUDGET_MS) -> ProbeResult:
    """Probe `dsn` for team activation: reachability + schema compatibility, wall-clock bounded
    to `budget_ms`. Fail-closed — any error / timeout yields a NOT-reachable-or-not-compatible
    result, never an exception. NEVER runs DDL.

    Bounded even when connect hangs: the work runs in a daemon thread joined for `budget_ms`;
    if it hasn't finished, the verdict is unreachable (the orphaned connect dies on its own
    `connect_timeout` backstop)."""
    from .memory import _pg
    try:
        import importlib.util
        if importlib.util.find_spec("psycopg") is None:
            return ProbeResult(driver_present=False, reachable=False, compatible=False,
                               detail="psycopg driver not installed (optional extra 'postgres')",
                               error="driver-absent")
    except Exception:  # pragma: no cover - find_spec is robust
        pass

    box: dict = {}

    def _work() -> None:
        try:
            conn = _pg.get_connection(dsn, _ProbeUnavailable)
            conn.execute("SELECT 1").fetchone()          # reachability round-trip
            box["reachable"] = True
            present, version = _read_schema_version(conn)
            box["schema_present"] = present
            box["schema_version"] = version
        except Exception as exc:                          # connect / query failure
            box["error"] = str(exc)

    start = time.monotonic()
    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(budget_ms / 1000.0)
    elapsed_ms = (time.monotonic() - start) * 1000.0

    if t.is_alive():
        return ProbeResult(reachable=False, compatible=False, elapsed_ms=elapsed_ms,
                           detail=f"unreachable — no response within {budget_ms}ms",
                           error="timeout")

    if not box.get("reachable"):
        return ProbeResult(reachable=False, compatible=False, elapsed_ms=elapsed_ms,
                           detail="unreachable — could not connect / round-trip",
                           error=box.get("error", "unreachable"))

    present = bool(box.get("schema_present"))
    version = box.get("schema_version")
    if not present:
        return ProbeResult(reachable=True, schema_present=False, compatible=False,
                           elapsed_ms=elapsed_ms,
                           detail=f"reachable, but the shared schema is not provisioned "
                                  f"(no {SCHEMA_VERSION_TABLE})")
    if version is None:
        return ProbeResult(reachable=True, schema_present=True, schema_version=None,
                           compatible=False, elapsed_ms=elapsed_ms,
                           detail=f"reachable, but {SCHEMA_VERSION_TABLE} has no version row")
    compatible = version == TEAM_SCHEMA_VERSION
    detail = (f"reachable + schema v{version} compatible" if compatible
              else f"reachable, but schema v{version} != required v{TEAM_SCHEMA_VERSION}")
    return ProbeResult(reachable=True, schema_present=True, schema_version=version,
                       compatible=compatible, elapsed_ms=elapsed_ms, detail=detail)


# ============================================================ provisioning (team init OWNS DDL)
# `team init` (TM.S3) is the SOLE owner of DDL (doc 48 C4): runtime connects never CREATE/ALTER,
# so there is no concurrent-create race and a least-privilege runtime role can be DML-only. Every
# statement is IF NOT EXISTS / ON CONFLICT so ONE idempotent pass (doc 48 E5) is safe to re-run.
# Golden path = vanilla Postgres ≥14, NO extensions (pgvector stays opt-in, off this path).

class ProvisionError(Exception):
    """Raised when the one-pass provision cannot run (driver absent / DB unreachable / DDL error)."""


@dataclass
class ProvisionResult:
    statements: "list[str]"
    version: int
    tables: "list[str]"


def provision_sql(project_id: Optional[str] = None) -> "list[str]":
    """The ordered, IDEMPOTENT DDL for the shared schema (E5). Each statement matches the table
    the corresponding runtime backend already creates, so a runtime connect finds it present.
    The `mokata_schema_version` row (what the probe reads) is upserted LAST. `project_id` is
    accepted for signature stability (the tables are project-SCOPED by a `project` column, not
    per-project tables) — the id is pinned into the shared manifest by the caller, not the DDL."""
    return [
        # the version table the probe reads (created before its row is inserted).
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (version INT PRIMARY KEY)",
        # memory items — mirrors memory/backends.py PostgresBackend._setup_sql.
        f"CREATE TABLE IF NOT EXISTS {MEMORY_TABLE} ("
        "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT,"
        "  status TEXT, doc TEXT, seq BIGSERIAL, project TEXT)",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
        # v2 (TM.S5, doc 48 C1) — compare-and-set columns. Idempotent ADD-COLUMN-IF-NOT-EXISTS
        # so re-running `team init` migrates a v1 table in place (existing rows default to
        # revision 1). Runtime connects never run these — DDL is team init's alone (C4).
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_REVISION_COLUMN} "
        "INT NOT NULL DEFAULT 1",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_UPDATED_AT_COLUMN} "
        "TIMESTAMPTZ",
        # v3 (TM.S6, doc 62 §2–3) — scope + precedence fields. Idempotent ADD-COLUMN-IF-NOT-EXISTS
        # so re-running `team init` migrates a v2 table in place (existing rows default to a
        # personal, unpinned, priority-0 scope — matching the item model's defaults). Runtime
        # connects never run these (DDL is team init's alone — C4).
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_SCOPE_LEVEL_COLUMN} "
        "TEXT NOT NULL DEFAULT 'personal'",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_SCOPE_ID_COLUMN} TEXT",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_PIN_COLUMN} "
        "BOOLEAN NOT NULL DEFAULT FALSE",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_PRIORITY_COLUMN} "
        "INT NOT NULL DEFAULT 0",
        # session bundles — mirrors session_transport.PostgresTransport._setup_sql.
        f"CREATE TABLE IF NOT EXISTS {SESSION_TABLE} ("
        "  tag TEXT, blob TEXT, seq BIGSERIAL, project TEXT,"
        "  PRIMARY KEY (project, tag))",
        f"ALTER TABLE {SESSION_TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {SESSION_TABLE}_project_tag"
        f"  ON {SESSION_TABLE} (project, tag)",
        # audit ledger — mirrors team_audit.SharedAuditLog._create_sql (append-only by id).
        f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
        "  id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, actor TEXT NOT NULL,"
        "  seq BIGINT, kind TEXT, at TEXT, entry TEXT)",
        # events — provisioned only; local-first population until a later UI (doc 48).
        f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} ("
        "  id BIGSERIAL PRIMARY KEY, namespace TEXT, project TEXT, kind TEXT,"
        "  at TEXT, actor TEXT, payload TEXT)",
        # the schema-version row LAST — ON CONFLICT so a re-run adds no duplicate (idempotent).
        f"INSERT INTO {SCHEMA_VERSION_TABLE} (version) VALUES ({TEAM_SCHEMA_VERSION})"
        f"  ON CONFLICT (version) DO NOTHING",
    ]


def provision(dsn: str, *, project_id: Optional[str] = None) -> ProvisionResult:
    """Run the one idempotent provision pass on `dsn` (doc 48 E5). This is the ONLY DDL path in
    mokata — runtime connects (incl. the probe) never run these. Raises `ProvisionError` on a
    missing driver / unreachable DB / DDL failure (the caller surfaces the S2 fail-closed fixes)."""
    from .memory import _pg
    conn = _pg.get_connection(dsn, ProvisionError)
    stmts = provision_sql(project_id)
    try:
        for stmt in stmts:
            conn.execute(stmt)
    except Exception as exc:                              # any DDL failure fails closed + clean
        raise ProvisionError(f"provisioning failed: {exc}") from exc
    tables = [SCHEMA_VERSION_TABLE, MEMORY_TABLE, SESSION_TABLE, AUDIT_TABLE, EVENTS_TABLE]
    return ProvisionResult(statements=stmts, version=TEAM_SCHEMA_VERSION, tables=tables)
