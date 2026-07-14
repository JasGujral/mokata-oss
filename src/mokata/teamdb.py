"""TM.S2 — the team shared-DB layer: the real fail-closed preflight probe.

`team` mode activates against a **reachable, compatible** Postgres and refuses — with a named
fix — when it isn't. This module owns the READ-ONLY probe that makes that call:

  * reachability — connect (via the doc-48 connection manager, timeout-bounded) + a cheap
    `SELECT 1` round-trip, wall-clock bounded to a ≤500ms budget (doc 48 E2);
  * schema compatibility — READ `mokata_schema_version` and compare to the version this build
    speaks. The table + all DDL are owned by `team init` (TM.S3, doc 48 C4) — this probe
    **never runs DDL**, only SELECTs.

D1 (0.0.13) makes this module the SINGLE SOURCE OF SCHEMA TRUTH. It always claimed `team init`
owned DDL, but four runtime backends hand-mirrored the schema and re-ran their copy on EVERY
connect (`memory/backends`, `memory/vector`, `session_transport`, `team_audit`, all via
`_pg.connect_psycopg(setup_sql=…)`). Postgres checks the schema ACL *before* the IF-NOT-EXISTS
short-circuit, and `ADD COLUMN IF NOT EXISTS` demands table ownership — so a least-privilege
DML-only runtime role (the two-role model `team init` itself RECOMMENDS) was denied CREATE
(SQLSTATE 42501) even against a perfectly provisioned, current-schema database, and the denial
degraded to the SQLite floor. The mirrors are gone: runtime connections now VERIFY (one cached
probe, E2) and never write schema. `ensure_schema` is the seam they all pass through.

D2 (0.0.13) makes the version artifact a RANGE. An exact-match check (`version == VERSION`)
partitioned a team on ANY bump — the first client to upgrade refused until the DB migrated, and
the migrated DB then refused every client still on the old build. The artifact now carries
`(min_supported, current)` and `compatibility()` is the ONE predicate both directions read.

Golden path = plain Postgres ≥14, NO extensions (ADR-54): the probe requires none. `psycopg`
stays an optional extra (lazy import via `memory/_pg.py`); a missing driver degrades to a
clear "driver absent" verdict, never a crash.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Type
from .degrade import FAILURE_SCHEMA
from .errors import DegradedCapability, MokataError

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

# D2 — the OLDEST shared schema this build can still serve. Not a guess: the live SQL SELECTs
# `revision` (memory/backends `SELECT doc, revision …`; the flush's revision-guarded CAS in
# team_journal), which arrived in v2. NOTHING in the runtime SQL touches a v3 column — v3's
# scope/precedence fields are provisioned for future SQL-side filtering while the authoritative
# value lives in the item `doc` JSON. So v2 is genuinely the floor, and a v2 team keeps working
# (with an upgrade warning) instead of being partitioned off by a version bump.
TEAM_SCHEMA_MIN_SUPPORTED = 2

# A pre-D2 artifact is a bare `version` row with no `min_supported` column: it declares no range.
# Read its floor as its OWN version — it certainly served the build that wrote it, and it made no
# promise about a client it never saw. That keeps an existing deployment IN RANGE (a legacy v3
# artifact still serves a v3 client; a legacy v2 one still serves us, with a warning) without the
# far worse converse: reading the floor as v1 would have this build cheerfully attach to a schema
# 40 versions AHEAD of it and read/write columns it has never heard of — the silent corruption D6
# exists to forbid. Unknown is not permission.
MIN_SUPPORTED_COLUMN = "min_supported"


def effective_min(db_version: int, db_min_supported: Optional[int]) -> int:
    """The floor an artifact actually promises: its declared `min_supported`, or — for a pre-D2
    artifact that declares none — its own version."""
    return int(db_version) if db_min_supported is None else int(db_min_supported)

# Why a schema is incompatible — the two directions are NOT the same failure and must not share a
# remediation. Both are LOUD; neither is ever silent.
REASON_SCHEMA_ABSENT = "schema-absent"      # nothing provisioned yet
REASON_SCHEMA_TOO_OLD = "schema-too-old"    # the DB is below this build's floor → migrate the DB
REASON_CLIENT_TOO_OLD = "client-too-old"    # the DB no longer serves this build → upgrade mokata

_SCHEMA_FIX = {
    REASON_SCHEMA_ABSENT: "run `mokata team init` to provision the shared schema",
    REASON_SCHEMA_TOO_OLD: "run `mokata team init` to upgrade the shared schema",
    REASON_CLIENT_TOO_OLD: "upgrade mokata (`pip install -U mokata`) — the shared schema is "
                           "ahead of this build and no longer serves it",
}


def schema_fix(reason: str) -> str:
    """The ONE remediation string for a schema incompatibility — the exact command to run. Every
    surface (preflight, the CM.S2 degrade notice, the typed backend errors) renders THIS, so a
    user is never told to `mokata sync` a connection that is already healthy."""
    return _SCHEMA_FIX.get(reason, _SCHEMA_FIX[REASON_SCHEMA_ABSENT])


@dataclass
class SchemaVerdict:
    """Can this build operate against a shared schema at `(min_supported, current)`?"""

    compatible: bool
    reason: str = ""        # "" when compatible; else REASON_*
    warning: str = ""       # set when compatible BUT the versions differ (upgrade advised)
    detail: str = ""


def compatibility(db_version: Optional[int], db_min_supported: Optional[int], *,
                  speaks: int = TEAM_SCHEMA_VERSION,
                  min_supported: int = TEAM_SCHEMA_MIN_SUPPORTED) -> SchemaVerdict:
    """The ONE compatibility predicate (D2). Two ranges must overlap:

      * the CLIENT declares it can serve a schema in [`min_supported`, `speaks`];
      * the ARTIFACT declares the schema is at `db_version` and still serves clients back to
        `db_min_supported`.

    Compatible iff `db_min_supported <= speaks` (the schema still serves us) AND
    `min_supported <= db_version` (the schema is new enough for the columns our SQL touches).
    Anything in range works; a difference in either direction only WARNS — that is the whole
    point of D2, because a version bump must not partition a team mid-upgrade."""
    if db_version is None:
        return SchemaVerdict(False, REASON_SCHEMA_ABSENT,
                             detail="the shared schema is not provisioned")
    db_min = effective_min(db_version, db_min_supported)

    if db_version < min_supported:
        return SchemaVerdict(
            False, REASON_SCHEMA_TOO_OLD,
            detail=f"the shared schema is v{db_version}, below the oldest this build can serve "
                   f"(v{min_supported})")
    if db_min > speaks:
        return SchemaVerdict(
            False, REASON_CLIENT_TOO_OLD,
            detail=f"the shared schema is v{db_version} and serves clients from v{db_min}; this "
                   f"build speaks v{speaks}")

    warning = ""
    if db_version < speaks:
        warning = (f"the shared schema is v{db_version}, behind this build (v{speaks}) — run "
                   f"`mokata team init` to upgrade it (working normally meanwhile)")
    elif db_version > speaks:
        warning = (f"the shared schema is v{db_version}, ahead of this build (v{speaks}) — it "
                   f"still serves v{speaks} clients, but upgrade mokata when you can")
    return SchemaVerdict(True, "", warning=warning,
                         detail=f"schema v{db_version} in range [v{db_min}, this build speaks "
                                f"v{speaks}]")

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
# …and "column does not exist" — a PRE-D2 artifact has no `min_supported` column. That is a
# legacy deployment, not a failure: fall back to the single-version read.
_UNDEFINED_COLUMN = "42703"

# Read-only SELECTs of the newest schema-version row. NEVER DDL (C4 — DDL is team init's).
_VERSION_SQL = (
    f"SELECT version, {MIN_SUPPORTED_COLUMN} FROM {SCHEMA_VERSION_TABLE} "
    f"ORDER BY version DESC LIMIT 1"
)
_VERSION_SQL_LEGACY = (
    f"SELECT version FROM {SCHEMA_VERSION_TABLE} ORDER BY version DESC LIMIT 1"
)


class _ProbeUnavailable(DegradedCapability):
    """Internal — the connection manager's typed failure for the probe path."""


@dataclass
class ProbeResult:
    """The verdict of one team-DB probe. Every field is derived read-only; `compatible` is True
    only when the DB is reachable, the schema is present, AND its version is IN RANGE (D2).
    `warning` is set when a compatible schema is nonetheless behind/ahead of this build."""

    driver_present: bool = True
    reachable: bool = False
    schema_present: bool = False
    schema_version: Optional[int] = None
    schema_min_supported: Optional[int] = None
    compatible: bool = False
    reason: str = ""
    warning: str = ""
    elapsed_ms: float = 0.0
    detail: str = ""
    error: str = ""

    @property
    def fix(self) -> str:
        """The remediation for an incompatible schema (empty when compatible)."""
        return "" if self.compatible else schema_fix(self.reason)


def _read_schema_version(conn: Any) -> "tuple[bool, Optional[int], Optional[int]]":
    """(schema_present, version, min_supported). A `42P01` (undefined table) means the schema
    isn't provisioned yet → (False, None, None). No rows → (True, None, None) (table exists but
    empty). A `42703` means a PRE-D2 (single-version) artifact → re-read without the range column
    and report `min_supported=None`, whose floor `compatibility()` reads as the artifact's OWN
    version — so an existing deployment parses as IN-RANGE rather than exploding."""
    try:
        row = conn.execute(_VERSION_SQL).fetchone()
        ranged = True
    except Exception as exc:
        state = getattr(exc, "sqlstate", None)
        if state == _UNDEFINED_TABLE:
            return False, None, None
        if state != _UNDEFINED_COLUMN:
            raise
        try:                                   # the legacy artifact — version only.
            row = conn.execute(_VERSION_SQL_LEGACY).fetchone()
        except Exception as exc2:
            if getattr(exc2, "sqlstate", None) == _UNDEFINED_TABLE:
                return False, None, None
            raise
        ranged = False
    if not row:
        return True, None, None
    try:
        version = int(row[0])
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return True, None, None
    min_supported = None
    if ranged and len(row) > 1 and row[1] is not None:
        try:
            min_supported = int(row[1])
        except (TypeError, ValueError):  # pragma: no cover - defensive
            min_supported = None
    return True, version, min_supported


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
            present, version, min_supported = _read_schema_version(conn)
            box["schema_present"] = present
            box["schema_version"] = version
            box["schema_min_supported"] = min_supported
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
    min_supported = box.get("schema_min_supported")
    if not present:
        return ProbeResult(reachable=True, schema_present=False, compatible=False,
                           reason=REASON_SCHEMA_ABSENT, elapsed_ms=elapsed_ms,
                           detail=f"reachable, but the shared schema is not provisioned "
                                  f"(no {SCHEMA_VERSION_TABLE})")
    if version is None:
        return ProbeResult(reachable=True, schema_present=True, schema_version=None,
                           compatible=False, reason=REASON_SCHEMA_ABSENT, elapsed_ms=elapsed_ms,
                           detail=f"reachable, but {SCHEMA_VERSION_TABLE} has no version row")

    # D2 — a RANGE check, never an equality one. In-range works (with a warning when the versions
    # differ); out of range is LOUD, and the two directions carry DIFFERENT remediations.
    v = compatibility(version, min_supported)
    detail = ("reachable + " + v.detail) if v.compatible else ("reachable, but " + v.detail)
    return ProbeResult(reachable=True, schema_present=True, schema_version=version,
                       schema_min_supported=effective_min(version, min_supported),
                       compatible=v.compatible, reason=v.reason, warning=v.warning,
                       elapsed_ms=elapsed_ms, detail=detail)


# ==================================================== D1 · the runtime VERIFY seam (zero DDL)
# Every runtime connection passes through here instead of running its own schema copy. It is the
# ONE place a runtime path may ask "is the schema I depend on present and in range?" — and the
# answer is a cached SELECT, never a CREATE. Cached per-process per-DSN (E2's one-probe
# discipline): N backend builds in one run cost ONE probe, not N.
_VERIFIED: Dict[str, ProbeResult] = {}
_TABLE_PRESENT: Dict[str, bool] = {}


def reset_schema_cache() -> None:
    """Drop the per-process verify cache (tests + a forced re-probe)."""
    _VERIFIED.clear()
    _TABLE_PRESENT.clear()


def verify_schema(dsn: str, *, force: bool = False) -> ProbeResult:
    """The cached, VERIFY-ONLY schema probe a runtime connect makes (D1). Read-only by
    construction — it reuses `probe`, which SELECTs and never runs DDL."""
    if not force:
        cached = _VERIFIED.get(dsn)
        if cached is not None:
            return cached
    res = probe(dsn)
    _VERIFIED[dsn] = res
    return res


def table_present(dsn: str, table: str, unavailable: Type[Exception]) -> bool:
    """Is `table` present? A cheap `to_regclass` lookup (no DDL), cached per-process. Used for the
    schema mokata owns OUTSIDE the version artifact — today only pgvector's opt-in table."""
    key = f"{dsn}\x00{table}"
    if key in _TABLE_PRESENT:
        return _TABLE_PRESENT[key]
    from .memory import _pg
    conn = _pg.get_connection(dsn, unavailable)
    try:
        row = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    except Exception as exc:
        raise unavailable(f"database unavailable: {exc}") from exc
    present = bool(row and row[0])
    _TABLE_PRESENT[key] = present
    return present


def ensure_schema(dsn: str, unavailable: Type[Exception], *,
                  require_tables: Iterable[str] = ()) -> ProbeResult:
    """VERIFY that the shared schema this runtime path depends on is provisioned and IN RANGE —
    and raise `unavailable` LOUDLY, naming the exact remediation, when it is not (D1: never a
    silent degrade to the local floor; D2: in-range is fine, out-of-range says which way).

    The raised exception carries `failure_class` (the CM.S2 vocabulary — `degrade.FAILURE_SCHEMA`
    / `FAILURE_UNREACHABLE`) and `fix`, so the caller can class the degrade honestly instead of
    calling every failure "unreachable"."""
    from .degrade import FAILURE_SCHEMA, FAILURE_UNREACHABLE
    res = verify_schema(dsn)
    if not res.reachable:
        exc = unavailable(f"database unavailable: {res.detail or res.error}")
        exc.failure_class = FAILURE_UNREACHABLE          # type: ignore[attr-defined]
        exc.fix = ""                                     # type: ignore[attr-defined]
        raise exc
    if not res.compatible:
        exc = unavailable(f"the shared schema is unusable: {res.detail} — {res.fix}")
        exc.failure_class = FAILURE_SCHEMA               # type: ignore[attr-defined]
        exc.fix = res.fix                                # type: ignore[attr-defined]
        exc.reason = res.reason                          # type: ignore[attr-defined]
        raise exc
    for table in require_tables:
        if not table_present(dsn, table, unavailable):
            fix = f"run `mokata team init` to provision the `{table}` schema"
            exc = unavailable(f"the shared schema is unusable: `{table}` is not provisioned "
                              f"— {fix}")
            exc.failure_class = FAILURE_SCHEMA           # type: ignore[attr-defined]
            exc.fix = fix                                # type: ignore[attr-defined]
            exc.reason = REASON_SCHEMA_ABSENT            # type: ignore[attr-defined]
            raise exc
    return res


# ============================================================ provisioning (team init OWNS DDL)
# `team init` (TM.S3) is the SOLE owner of DDL (doc 48 C4): runtime connects never CREATE/ALTER,
# so there is no concurrent-create race and a least-privilege runtime role can be DML-only. Every
# statement is IF NOT EXISTS / ON CONFLICT so ONE idempotent pass (doc 48 E5) is safe to re-run.
# Golden path = vanilla Postgres ≥14, NO extensions (pgvector stays opt-in, off this path).

class ProvisionError(MokataError):
    """Raised when the one-pass provision cannot run (driver absent / DB unreachable / DDL error)."""

    failure_class = FAILURE_SCHEMA


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
        # D2 — the artifact carries a RANGE. Idempotent ADD-COLUMN-IF-NOT-EXISTS, so re-running
        # `team init` upgrades a PRE-D2 (single-version) artifact in place; until it does, the
        # probe reads that artifact as in-range (floor = its own version), never exploding.
        f"ALTER TABLE {SCHEMA_VERSION_TABLE} ADD COLUMN IF NOT EXISTS "
        f"{MIN_SUPPORTED_COLUMN} INT",
        # memory items — the ONE definition (the runtime backends verify this, never re-create it).
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
        # session bundles — the ONE definition (session_transport verifies it, never creates it).
        f"CREATE TABLE IF NOT EXISTS {SESSION_TABLE} ("
        "  tag TEXT, blob TEXT, seq BIGSERIAL, project TEXT,"
        "  PRIMARY KEY (project, tag))",
        f"ALTER TABLE {SESSION_TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {SESSION_TABLE}_project_tag"
        f"  ON {SESSION_TABLE} (project, tag)",
        # audit ledger — the ONE definition (team_audit verifies it). Append-only by id.
        f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
        "  id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, actor TEXT NOT NULL,"
        "  seq BIGINT, kind TEXT, at TEXT, entry TEXT)",
        # events — provisioned only; local-first population until a later UI (doc 48).
        f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} ("
        "  id BIGSERIAL PRIMARY KEY, namespace TEXT, project TEXT, kind TEXT,"
        "  at TEXT, actor TEXT, payload TEXT)",
        # the schema-version row LAST — the (current, min_supported) RANGE (D2). ON CONFLICT DO
        # UPDATE (not DO NOTHING) so re-running init also refreshes the range on an artifact that
        # already carries this version but predates the range column. Idempotent either way (E5).
        f"INSERT INTO {SCHEMA_VERSION_TABLE} (version, {MIN_SUPPORTED_COLUMN})"
        f"  VALUES ({TEAM_SCHEMA_VERSION}, {TEAM_SCHEMA_MIN_SUPPORTED})"
        f"  ON CONFLICT (version) DO UPDATE SET "
        f"{MIN_SUPPORTED_COLUMN} = EXCLUDED.{MIN_SUPPORTED_COLUMN}",
    ]


# pgvector's schema — mokata-owned, but OPT-IN and OFF the golden path (ADR-54: vanilla Postgres,
# no extensions). It lives HERE, in the init path, because D1 admits no runtime DDL anywhere: the
# `PgVectorBackend` used to run `CREATE EXTENSION vector` on every connect — the single most
# privileged statement in the codebase, on a path meant for a DML-only role. It is provisioned by
# `provision_vector` (never by the default `team init` pass); the backend only VERIFIES the table.
VECTOR_TABLE = "mokata_memory_vectors"


def vector_provision_sql(dim: int) -> "list[str]":
    """The ordered, idempotent DDL for the OPT-IN pgvector tier (needs a role that may CREATE
    EXTENSION). Not part of `provision_sql` — the golden path stays extension-free."""
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"CREATE TABLE IF NOT EXISTS {VECTOR_TABLE} ("
        "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT, status TEXT,"
        f"  doc TEXT, embedding vector({dim}), seq BIGSERIAL, project TEXT)",
        f"ALTER TABLE {VECTOR_TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
    ]


def provision_vector(dsn: str, *, dim: int) -> ProvisionResult:
    """Provision the opt-in pgvector schema (the init path — never a runtime connect)."""
    return _run_ddl(dsn, vector_provision_sql(dim), [VECTOR_TABLE])


def _run_ddl(dsn: str, stmts: "list[str]", tables: "list[str]") -> ProvisionResult:
    """Execute an idempotent DDL pass. The ONLY function in mokata that writes schema — every
    caller is an init path, and the AST guard in the D1 test enforces that."""
    from .memory import _pg
    conn = _pg.get_connection(dsn, ProvisionError)
    try:
        for stmt in stmts:
            conn.execute(stmt)
    except Exception as exc:                              # any DDL failure fails closed + clean
        raise ProvisionError(f"provisioning failed: {exc}") from exc
    reset_schema_cache()             # the schema just changed — the verify cache is now stale.
    return ProvisionResult(statements=stmts, version=TEAM_SCHEMA_VERSION, tables=tables)


def provision(dsn: str, *, project_id: Optional[str] = None) -> ProvisionResult:
    """Run the one idempotent provision pass on `dsn` (doc 48 E5). This is the ONLY DDL path in
    mokata — runtime connects (incl. the probe) never run these. Raises `ProvisionError` on a
    missing driver / unreachable DB / DDL failure (the caller surfaces the S2 fail-closed fixes)."""
    return _run_ddl(dsn, provision_sql(project_id),
                    [SCHEMA_VERSION_TABLE, MEMORY_TABLE, SESSION_TABLE, AUDIT_TABLE, EVENTS_TABLE])


# ------------------------------------------------------------------- E5 · the idempotent pass
@dataclass
class UpgradePlan:
    """What re-running `team init` would DO to an existing shared schema — computed BEFORE any
    DDL, so the human gate can state the change and a true no-op can skip the prompt entirely."""

    needed: bool                # False → the schema is already current AND carries the D2 range
    label: str = ""             # what the human is approving ("provision" / "upgrade v2 → v3")
    from_version: Optional[int] = None
    blocked: str = ""           # a reason the pass CANNOT proceed (a client-too-old schema)


def upgrade_plan(res: ProbeResult) -> UpgradePlan:
    """The E5 plan for one probe result. Re-init on a CURRENT schema is a no-op (nothing to
    approve, nothing to run); on an old-but-in-range schema it is an upgrade; on an absent schema
    it is the first-time provision. A schema AHEAD of this build is refused, not downgraded — a
    newer teammate's schema must never be rewritten by an older client."""
    if not res.schema_present or res.schema_version is None:
        return UpgradePlan(True, "provision the shared schema")
    if res.reason == REASON_CLIENT_TOO_OLD:
        return UpgradePlan(False, blocked=res.detail or "the shared schema is ahead of this build")
    if res.schema_version > TEAM_SCHEMA_VERSION:
        return UpgradePlan(
            False, from_version=res.schema_version,
            blocked=f"the shared schema is v{res.schema_version}, ahead of this build "
                    f"(v{TEAM_SCHEMA_VERSION}) — it must not be rewritten by an older client")
    if (res.schema_version == TEAM_SCHEMA_VERSION
            and res.schema_min_supported == TEAM_SCHEMA_MIN_SUPPORTED):
        return UpgradePlan(False, from_version=res.schema_version)     # true no-op
    return UpgradePlan(
        True,
        f"upgrade the shared schema v{res.schema_version} → v{TEAM_SCHEMA_VERSION}",
        from_version=res.schema_version)
