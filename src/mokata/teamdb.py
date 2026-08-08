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

Golden path = plain Postgres ≥15, NO extensions (ADR-54; floor ratified >=15 target 17 on 2026-08-03 — PG14 EOL 2026-11-12): the probe requires none. `psycopg`
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
from .memory import edges as _edges
from .memory import lifecycle as _lifecycle

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
# migration, owned by `team init`. DB.S2b made these columns LOAD-BEARING: `put()` now populates
# them in the same statement that writes `doc`, `provision_sql()` backfills the rows that predate
# that, and `filter_clause_for` pushes a scope predicate at them. An existing team upgrades by
# RE-RUNNING `mokata team init`; the S2 incompatible-version check fail-closes older clients
# until they do.
#
# v3 → v4 (DB.S5, doc 62 lifecycle): `mokata_memory` gains the bi-temporal validity window
# (`valid_from`, `valid_to`) and the usage telemetry (`hit_count`, `last_recalled_at`). Same
# idempotent ADD-COLUMN-IF-NOT-EXISTS migration + a `valid_from` backfill, owned by `team init`.
# Note what does NOT move with it: `TEAM_SCHEMA_MIN_SUPPORTED` stays at 3, because unlike the v3
# scope columns NOTHING in the runtime REQUIRES these. A v3 team keeps working exactly as it does
# today — it simply carries no usage signal (the fusion falls back to its three original terms) and
# no explicit windows (every item reads as open). Raising the floor would fail-close every existing
# team on upgrade for a purely additive feature, which is the opposite of what a floor is for.
#
# v4 → v5 (DB.S7a, doc 55 K3 + doc 84 DB.S7): the shared schema gains `mokata_memory_edges` — the
# closed typed-edge table, with the R3 validity window (`valid_from`/`valid_to`, ONE axis — see
# `memory/edges.py`) and doc 55's `created_at` provenance. Purely ADDITIVE: a NEW TABLE plus its
# partial unique index, and not one column on `mokata_memory` moves. Same idempotent seam as v2–v4
# (CREATE TABLE IF NOT EXISTS + an add-then-backfill that is idempotent by predicate), owned by
# `team init` alone (C4) — no runtime DDL. `TEAM_SCHEMA_MIN_SUPPORTED` stays 3 for the DB.S5 reason,
# restated below where the floor lives.
TEAM_SCHEMA_VERSION = 5
SCHEMA_VERSION_TABLE = "mokata_schema_version"

# D2 — the OLDEST shared schema this build can still serve. Not a guess: the live SQL SELECTs
# `revision` (memory/backends `SELECT doc, revision …`; the flush's revision-guarded CAS in
# team_journal), which arrived in v2 — AND, since DB.S2b, it filters on `scope_level`/`scope_id`,
# which arrived in v3. The v3 columns used to be inert provisioning, which is exactly why the
# floor sat at 2; they are now read by the runtime SQL, so a v2 table (which does not HAVE them)
# can no longer be served. Raised 2 → 3 in lockstep with that activation.
#
# What happens to a store that can't upgrade: a v2 store is out of range → `compatibility()`
# returns `schema-too-old` and team mode FAILS CLOSED with "run `mokata team init`" (which is the
# migration: idempotent ADD COLUMN + the backfill below). It is refused loudly, never silently
# mis-filtered — the one outcome that would be worse than refusing it. Nothing is lost: the local
# journal keeps queueing writes, and LOCAL mode is untouched by any of this.
#
# DB.S7a holds the floor at 3 for the DB.S5 reason, and states the TRIPWIRE explicitly so the next
# stage does not have to re-derive it: **the day any runtime read becomes MANDATORY on
# `mokata_memory_edges`, THIS FLOOR MOVES IN THE SAME CHANGE.** Right now nothing requires the edge
# table — the projection is DERIVED from inline doc fields that a v4 store already carries, every
# edge read is capability-probed and absent-degrades to the inline lists, and the flush skips the
# projection entirely on a store that has no such table (so a v4 team keeps flushing byte-identically).
# That is what earns the additive treatment. A mandatory edge read would make a v4 store genuinely
# unservable, and serving it anyway would mean silently answering a graph question from a table that
# is not there — the exact class of failure the floor exists to refuse LOUDLY. Floor and requirement
# move together or the floor is a lie.
TEAM_SCHEMA_MIN_SUPPORTED = 3

# A pre-D2 artifact is a bare `version` row with no `min_supported` column: it declares no range.
# Read its floor as its OWN version — it certainly served the build that wrote it, and it made no
# promise about a client it never saw. That keeps an existing deployment IN RANGE (a legacy v3
# artifact still serves a v3 client; a legacy v2 one still serves us, with a warning) without the
# far worse converse: reading the floor as v1 would have this build cheerfully attach to a schema
# 40 versions AHEAD of it and read/write columns it has never heard of — the silent corruption D6
# exists to forbid. Unknown is not permission.
MIN_SUPPORTED_COLUMN = "min_supported"

# DB.S2b — the BACKFILL STAMP, and the reason it has to exist separately from the version number.
#
# The version stamp alone cannot answer the question the scope pushdown must ask. `team init`
# wrote `version=3` from TM.S6 onward, when v3 meant only "the four columns EXIST" — every row
# still carried the DDL default. DB.S2b changes what v3 must mean: "the columns exist AND are a
# faithful projection of each row's `doc`". Those are different guarantees wearing the same
# number, and a store provisioned by a pre-DB.S2b `team init` is indistinguishable from a
# backfilled one by version alone. Pushing a scope predicate at that store would drop every row
# whose real home scope isn't `personal` — the cross-tenant visibility bug this stage exists to
# make impossible.
#
# So the stamp is explicit: `provision_sql()` runs the backfill and sets this column true in the
# same ordered run. The runtime READS it and pushes a scope predicate only when it is true;
# otherwise it emits no scope clause at all and the caller's `scope.union_read` filters from the
# doc as before — slower, and CORRECT. Unknown (no column, no row, unreadable) reads as FALSE:
# unknown is not permission, exactly as for `min_supported` above.
SCOPE_BACKFILLED_COLUMN = "scope_backfilled"


def effective_min(db_version: int, db_min_supported: Optional[int]) -> int:
    """The floor an artifact actually promises: its declared `min_supported`, or — for a pre-D2
    artifact that declares none — its own version."""
    return int(db_version) if db_min_supported is None else int(db_min_supported)

# Why a schema is incompatible — the two directions are NOT the same failure and must not share a
# remediation. Both are LOUD; neither is ever silent.
REASON_SCHEMA_ABSENT = "schema-absent"      # nothing provisioned yet
REASON_SCHEMA_TOO_OLD = "schema-too-old"    # the DB is below this build's floor → migrate the DB
REASON_CLIENT_TOO_OLD = "client-too-old"    # the DB no longer serves this build → upgrade mokata

# DB.S1 — why the CONNECTION layer failed (a DIFFERENT axis than the schema reasons above: this
# names WHY we could not complete a reachable, authenticated round-trip). Typed here rather than
# string-sniffed at the call site, so `doctor`'s DSN deep-check can tell auth apart from network
# without parsing an exception message. `""` == the connection layer is fine (a reachable probe).
CONN_DRIVER_ABSENT = "driver-absent"        # psycopg not installed → the `postgres` extra
CONN_AUTH_FAILED = "auth-failed"            # the host answered, the credentials were rejected
CONN_NETWORK_UNREACHABLE = "network-unreachable"  # DNS / host down / port closed — no server reply
CONN_TIMEOUT = "timeout"                    # no response inside the wall-clock probe budget

# Postgres SQLSTATE class 28 = invalid authorization (28000 invalid_authorization_specification,
# 28P01 invalid_password): the server ANSWERED and rejected the credentials — an auth failure, not
# a network one.
_AUTH_SQLSTATE_CLASS = "28"

# …but a CONNECT-PHASE auth failure carries NO sqlstate. Grounded against the live driver
# (psycopg 3.3): a wrong password raises `OperationalError` with `.sqlstate is None` AND
# `.diag.sqlstate is None` — libpq folds the server's 28P01 into a generic
# "connection failed: … FATAL: password authentication failed for user …" MESSAGE. So sqlstate
# alone misses the most common case; the message text is the only signal that tells auth from
# network. It is read HERE, inside teamdb, to produce the TYPED reason (never string-sniffed at the
# doctor call site) — and the message (which carries host/user but NEVER the password libpq refuses
# to echo) stays internal: `db_doctor` renders only the typed reason + the env-var NAME, never this.
_AUTH_MESSAGE_MARKERS = (
    "password authentication failed",   # the common wrong-password case
    "authentication failed",            # md5 / scram / PAM / peer / GSS variants
    "no password supplied",             # the server required one, none was sent
    'role "',                           # 'role "x" does not exist' (a 28000-class identity failure)
)


def _conn_reason_from_exc(exc: BaseException) -> str:
    """Classify a connect/round-trip failure as auth vs network. First the typed signal — a
    SQLSTATE of class 28 on the exception or its cause/context chain (the connection manager wraps
    the psycopg error in a typed `unavailable`, so the SQLSTATE rides on `__cause__`); this catches
    a query-phase 28xxx. Then the MESSAGE fallback for the sqlstate-less connect-phase auth failure
    the live driver actually raises. No auth signal anywhere → the connect never authenticated →
    network. Never raises."""
    seen = 0
    cur: Optional[BaseException] = exc
    texts: list = []
    while cur is not None and seen < 6:
        state = getattr(cur, "sqlstate", None)
        if not state:
            diag = getattr(cur, "diag", None)
            state = getattr(diag, "sqlstate", None) if diag is not None else None
        if state and str(state).startswith(_AUTH_SQLSTATE_CLASS):
            return CONN_AUTH_FAILED
        texts.append(str(cur))
        cur = cur.__cause__ or cur.__context__
        seen += 1
    blob = " ".join(texts).lower()
    if any(marker in blob for marker in _AUTH_MESSAGE_MARKERS):
        return CONN_AUTH_FAILED
    return CONN_NETWORK_UNREACHABLE

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
# `team init`. The item `doc` JSON remains AUTHORITATIVE — these columns are its projection, not a
# second source of truth, and every write path derives them from the doc it is writing
# (`memory.backends.scope_columns_from_doc`). Since DB.S2b they are load-bearing: `scope_level` +
# `scope_id` carry the runtime's scope predicate, so the schema floor rose to v3 to match. `pin` +
# `priority` are populated and backfilled on the same terms but nothing filters on them yet — they
# feed `precedence.resolve_items` after the read.
MEMORY_SCOPE_LEVEL_COLUMN = "scope_level"
MEMORY_SCOPE_ID_COLUMN = "scope_id"
MEMORY_PIN_COLUMN = "pin"
MEMORY_PRIORITY_COLUMN = "priority"

# v4 lifecycle columns on the memory table (DB.S5). Aliased from `memory.lifecycle`, which is THE
# definition — a rename there moves both engines at once and cannot leave Postgres on the old name.
#
# The two halves are governed differently and it matters at the schema level too. `valid_from` /
# `valid_to` are a PROJECTION of the doc (like the v3 scope columns): every write path derives them
# from the doc it is writing, and the doc stays authoritative. `hit_count` / `last_recalled_at` are
# NOT a projection of anything — they exist ONLY as columns, are written ONLY by `record_usage` on
# the read path (transient run-state, D5), and no doc, export or bundle carries them.
MEMORY_VALID_FROM_COLUMN = _lifecycle.VALID_FROM_COLUMN
MEMORY_VALID_TO_COLUMN = _lifecycle.VALID_TO_COLUMN
MEMORY_HIT_COUNT_COLUMN = _lifecycle.HIT_COUNT_COLUMN
MEMORY_LAST_RECALLED_AT_COLUMN = _lifecycle.LAST_RECALLED_AT_COLUMN

# The mokata-OWNED shared tables `team init` provisions (doc 48 §3). Each name mirrors the
# table the corresponding runtime backend already uses, so a runtime connect finds it present
# and its own IF-NOT-EXISTS is a no-op:
#   memory items    -> memory/backends.py PostgresBackend
#   session bundles -> session_transport.PostgresTransport
#   audit ledger    -> team_audit.SharedAuditLog (append-only)
#   events          -> provisioned only; local-first population until a later UI (doc 48)
#   memory edges    -> memory/edges.py (DB.S7a, v5) — rows in the store that already exists,
#                      traversed with recursive CTEs. NEVER a second graph DB.
MEMORY_TABLE = "mokata_memory"
SESSION_TABLE = "mokata_session_bundle"
AUDIT_TABLE = "mokata_audit_log"
EVENTS_TABLE = "mokata_events"

# v5 (DB.S7a) — the shared typed-edge table. Aliased from `memory.edges`, which is THE definition
# of the name, the closed kind set, the column list and the idempotency predicate, exactly as the
# v4 columns above are aliased from `memory.lifecycle`: one rename moves both engines at once.
EDGES_TABLE = _edges.SHARED_EDGES_TABLE

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
    # DB.S1 — the typed CONNECTION-layer reason (CONN_*), distinct from `reason` (the schema axis).
    # Set only when the connection did NOT complete a reachable round-trip; `""` on a reachable
    # probe. Lets `doctor` name auth vs network vs driver-absent vs timeout without string-sniffing.
    conn_reason: str = ""

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


def _abandon_probe(box: dict, handover: "threading.Lock") -> Any:
    """Give up on a probe worker. Returns the connection the CALLER must now close, or `None`.

    Extracted so it can be pinned DIRECTLY, because the interleaving it guards cannot be forced
    through the thread: the worker's `finally` may complete between `join` returning and this lock
    being taken, and `Thread.is_alive()` is still True during teardown. In that window the
    connection is already in the box and its owner has walked away — `abandoned` only instructs a
    worker that has NOT yet reached its finally, so without the `pop` the socket leaks.

    Both halves happen under ONE lock acquisition on purpose: setting the flag and taking the
    connection must be atomic with respect to the worker's `finally`, or the two can both decide
    they are not responsible for it.
    """
    with handover:
        box["abandoned"] = True
        return box.pop("conn", None)


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
                               error="driver-absent", conn_reason=CONN_DRIVER_ABSENT)
    except Exception:  # pragma: no cover - find_spec is robust
        pass

    box: dict = {}
    # PROBE-ORPHAN (doc 84, fixed 2026-08-01). The worker below may OUTLIVE this call — that is
    # what "bounded even when connect hangs" means, and it is the design, not a bug. The bug was
    # that the worker connected through `_pg.get_connection`, which PUBLISHES into the process-
    # global `_MANAGER`; so a probe that had already returned "unreachable" could still install a
    # live connection with nobody waiting for it, and the next caller would silently reuse a
    # connection whose own probe said the database was down.
    #
    # Fixed STRUCTURALLY rather than with a flag the worker consults on its way past: it now opens
    # an UNMANAGED connection it holds privately, and only a caller that is STILL WAITING adopts
    # it. A worker that cannot publish cannot orphan. This lock is what makes the handover
    # race-free in both directions — exactly one of {adopt, close} happens to any connection.
    handover = threading.Lock()

    def _work() -> None:
        conn = None
        try:
            conn = _pg.open_unmanaged(dsn, _ProbeUnavailable)
            conn.execute("SELECT 1").fetchone()          # reachability round-trip
            box["reachable"] = True
            present, version, min_supported = _read_schema_version(conn)
            box["schema_present"] = present
            box["schema_version"] = version
            box["schema_min_supported"] = min_supported
        except Exception as exc:                          # connect / query failure
            box["error"] = str(exc)
            box["conn_reason"] = _conn_reason_from_exc(exc)
        finally:
            with handover:
                if box.get("abandoned"):
                    # Nobody is waiting for this any more. CLOSE it — never cache it, and never
                    # leave the socket open either. This is the branch that used to leak.
                    if conn is not None:
                        _pg.close_quietly(conn)
                else:
                    box["conn"] = conn

    start = time.monotonic()
    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(budget_ms / 1000.0)
    elapsed_ms = (time.monotonic() - start) * 1000.0

    if t.is_alive():
        stranded = _abandon_probe(box, handover)
        if stranded is not None:
            _pg.close_quietly(stranded)
        return ProbeResult(reachable=False, compatible=False, elapsed_ms=elapsed_ms,
                           detail=f"unreachable — no response within {budget_ms}ms",
                           error="timeout", conn_reason=CONN_TIMEOUT)

    # Still waiting, so this caller owns the result — publish the connection it opened, which is
    # what preserves the manager's one-connection-per-DSN benefit for the success path.
    with handover:
        opened = box.get("conn")
    if opened is not None and box.get("reachable"):
        _pg.adopt(dsn, opened)
    elif opened is not None:
        # Connected but not usable (the round-trip or the schema read failed). It is nobody's
        # connection; closing beats caching one the probe just declared unreachable.
        _pg.close_quietly(opened)

    if not box.get("reachable"):
        return ProbeResult(reachable=False, compatible=False, elapsed_ms=elapsed_ms,
                           detail="unreachable — could not connect / round-trip",
                           error=box.get("error", "unreachable"),
                           conn_reason=box.get("conn_reason", CONN_NETWORK_UNREACHABLE))

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
# Golden path = vanilla Postgres ≥15, NO extensions (pgvector stays opt-in, off this path).

class ProvisionError(MokataError):
    """Raised when the one-pass provision cannot run (driver absent / DB unreachable / DDL error)."""

    failure_class = FAILURE_SCHEMA


@dataclass
class ProvisionResult:
    statements: "list[str]"
    version: int
    tables: "list[str]"
    # DB.S7a (E7) — how many item→item edge refs the v5 migration SKIPPED because their target
    # item is not in the store. It is a REPORT, never a failure: a dangling ref is a fact about a
    # store that has been pruned or partially imported, and refusing to migrate the other ten
    # thousand relations because of it would be the wrong trade in every direction. Surfaced by
    # `team init` so the skip is visible rather than silent — a silently-skipped edge is
    # indistinguishable from a migration that simply did not run.
    skipped_dangling_edges: int = 0


# ---------------------------------------------------------------- v5 · the edge migration (DB.S7a)
# The three WIRED kinds and the doc-JSON array each is stored in. Read from `memory.edges`, which
# owns the mapping, so the SQL cannot wire a kind the module does not declare (and a kind added
# there without a producer here would be caught by the closed-set pin, not shipped silently).
def _wired_jsonb_sources() -> "list[tuple]":
    """(edge kind, the `doc` JSON array it is stored in, does its dst have to BE an item)."""
    return [(kind, _edges._ITEM_FIELD[kind], kind in _edges.ITEM_TARGET_KINDS)
            for kind in _edges.WIRED_KINDS]


def _item_approval_id_sql(alias: str = "m") -> str:
    """M-1/R9 — the SQL that reads an item's stamped `approval_ledger_id` off its doc, or NULL.

    This is `memory.item.approval_ledger_id_of` spelled in SQL, and it has to stay that way: the
    Python coercion and this expression answer the same question about the same doc key, and an
    edge whose id disagreed with its item's would be worse than an edge with no id at all.

    Two guards, and both are load-bearing rather than defensive habit:
      * `jsonb_typeof(...) = 'number'` — a JSON `true` is a boolean here, not a `1`. Postgres would
        not silently fold it the way Python's `bool`-is-an-`int` does, but excluding it keeps the
        two implementations answering identically on the same doc, which is the point.
      * `~ '^[0-9]+$'` on the TEXT form — a `number` in JSON may be `1.5` or `-3`, and casting
        either to BIGINT raises and would fail the whole provisioning pass. A ledger seq is a
        positive integer; anything else is not an id we can join on.

    Everything that fails either guard — absent key, JSON `null`, a string, a float, the journal's
    `"floor-recovery"` sentinel — yields NULL. **Unknown stays NULL. Nothing here invents an id.**
    """
    key = "approval_ledger_id"
    return (f"CASE WHEN jsonb_typeof({alias}.doc::jsonb->'{key}') = 'number' "
            f"       AND ({alias}.doc::jsonb->>'{key}') ~ '^[0-9]+$' "
            f"      THEN ({alias}.doc::jsonb->>'{key}')::bigint END")


def _edge_approval_backfill_sql() -> str:
    """M-1/R9 — fill the `approval_ledger_id` the DB.S7a migration had to leave NULL.

    DB.S7a migrated the three implicit doc-JSON edge kinds into real rows, and could only stamp
    them `NULL::bigint` because the ITEM carried no approval id to inherit — its own comment said
    so, naming this stage as the thing that would close it ("that is M-1/R9's `approved_by`, still
    open"). Now that items are stamped, a migrated edge can inherit the approval its item carries,
    which is exactly what the LIVE projection already does for every edge written through the flush
    (`team_journal._project_edges_for` passes `entry.ledger_id`). Same rule, same source, both
    halves — a relation's link back to the human decision that created it should not depend on
    whether the relation happened to be written before or after the graph existed.

    DERIVED, never invented, and the WHERE clause is the whole guarantee:
      * `e.approval_ledger_id IS NULL` — only ever fills a hole. An edge that already carries an
        id (every live-projected one) is untouched, so this cannot rewrite a real approval with a
        derived one, and cannot disagree with the flush.
      * the item's id `IS NOT NULL` — a ROW-CHURN guard, and worth naming accurately rather than
        overselling: the rows it excludes are ones this UPDATE would set from NULL to NULL, so it
        changes no value (confirmed by mutation against a live server). What it buys is that a
        re-run does not rewrite every unstamped edge in the store. The never-invent guarantee is
        `_item_approval_id_sql`'s, not this predicate's: an item with no readable stamp yields NULL
        there, so pre-M-1/R9 items are not retro-approved by this pass — "we do not know" stays
        "we do not know", the same call `_backfill_lifecycle_columns` made for the usage columns.

    IDEMPOTENT by construction: the second run finds no NULL where the item has an id, so it
    updates zero rows. Ordinary DML (it creates no schema), so it re-runs safely under the same
    role that owns the rest of the migration, and it is placed AFTER `_edge_backfill_sql` so the
    rows it fills exist by the time it runs.
    """
    return (f"UPDATE {EDGES_TABLE} e "                                          # nosec B608
            f"   SET {_edges.APPROVAL_LEDGER_COLUMN} = src.approval_id "
            f"  FROM (SELECT m.id, {_item_approval_id_sql('m')} AS approval_id "
            f"          FROM {MEMORY_TABLE} m "
            f"         WHERE m.doc IS NOT NULL "
            f"           AND jsonb_typeof(m.doc::jsonb) = 'object') src "
            f" WHERE e.{_edges.SRC_COLUMN} = src.id "
            f"   AND e.{_edges.APPROVAL_LEDGER_COLUMN} IS NULL "
            f"   AND src.approval_id IS NOT NULL")


def _edge_backfill_sql() -> "list[str]":
    """One INSERT…SELECT per wired kind: every ref in every doc becomes an OPEN edge row.

    Per-kind rather than one UNION ALL because each kind reads a different JSON array and only some
    are existence-checked — three short statements each doing one legible thing beat one statement
    nobody can review. All three are ordinary DML (they create no schema), so they are safe to
    re-run and safe for the same role that already owns the migration.

    `valid_from` is `coalesce(doc->>'valid_from', doc->'provenance'->>'created_at', '')` — the SQL
    spelling of `memory.edges.open_window_of`, which is itself `lifecycle.open_window`. The edge's
    window opens when its ITEM's did, so an edge rebuilt by this migration lands on the same instant
    as the edge the live projection would have written. `created_at` on the ROW is `now()` — this
    row was written now, and that provenance/validity split is doc 02 decision #1 in one line of SQL.

    Degrades clean on a malformed doc, like every backfill before it: a `doc` that is not valid JSON
    or whose field is not an array yields no rows for that item instead of failing the pass.
    """
    stmts: "list[str]" = []
    cols = ", ".join(_edges.EDGE_COLUMNS)
    valid_from = (f"coalesce(nullif(m.doc::jsonb->>'{_edges.VALID_FROM_COLUMN}', ''), "
                  f"         m.doc::jsonb->'provenance'->>'created_at', '')")
    for kind, field, item_target in _wired_jsonb_sources():
        exists = ""
        if item_target:
            exists = (f"   AND EXISTS (SELECT 1 FROM {MEMORY_TABLE} t WHERE t.id = ref.value) ")
        stmts.append(
            f"INSERT INTO {EDGES_TABLE} ({cols}) "                              # nosec B608
            f"SELECT DISTINCT m.id, ref.value, '{kind}', {valid_from}, NULL, "
            f"       to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"+00:00\"'), "
            # M-1/R9 — the migrated edge INHERITS the approval its item carries. This was
            # `NULL::bigint` at DB.S7a, honestly, because the item had no id to inherit; the
            # comment there named this stage as the one that would close it. `_item_approval_id_sql`
            # is already BIGINT-typed (or NULL), so the DB.S7a typing note it replaces still holds:
            # an untyped NULL in a SELECT list is `text`, and Postgres refuses to insert text into
            # a BIGINT column (observed — the un-cast form failed the provision outright).
            #
            # An item with no stamp still yields NULL here, and the separate
            # `_edge_approval_backfill_sql` pass fills rows that an EARLIER run of this migration
            # already created as NULL. Both derive from the same expression, so a store lands in
            # the same state whether it migrated before or after items were stamped.
            f"       coalesce(m.doc::jsonb->'provenance'->>'author', ''), "
            f"       {_item_approval_id_sql('m')} "
            f"  FROM {MEMORY_TABLE} m, "
            f"       LATERAL jsonb_array_elements_text("
            f"           CASE jsonb_typeof(m.doc::jsonb->'{field}') WHEN 'array' "
            f"                THEN m.doc::jsonb->'{field}' ELSE '[]'::jsonb END) AS ref "
            f" WHERE m.doc IS NOT NULL AND jsonb_typeof(m.doc::jsonb) = 'object' "
            f"   AND nullif(ref.value, '') IS NOT NULL "
            + exists +
            f"   AND NOT EXISTS (SELECT 1 FROM {EDGES_TABLE} e "
            f"                    WHERE e.{_edges.SRC_COLUMN} = m.id "
            f"                      AND e.{_edges.DST_COLUMN} = ref.value "
            f"                      AND e.{_edges.KIND_COLUMN} = '{kind}' "
            f"                      AND e.{_edges.VALID_TO_COLUMN} IS NULL)")
    return stmts


def dangling_edge_refs_sql() -> str:
    """COUNT the item→item refs the backfill SKIPPED — E7's surfaced number.

    Deliberately a SEPARATE statement rather than a `RETURNING`/rowcount off the INSERT, because the
    two answer different questions: the INSERT's rowcount is "how many edges did this RUN create",
    which is ZERO on the second (correctly idempotent) pass, while this is "how many refs in this
    store point at an item that is not here" — a property of the DATA, stable across re-runs and
    still true the tenth time someone asks. Reporting the rowcount would tell a re-running operator
    that the dangling refs had gone away.

    It counts DISTINCT (item, ref, kind) so a store is not reported as having ten problems when one
    item lists the same missing target in two fields.
    """
    unions = []
    for kind, field, item_target in _wired_jsonb_sources():
        if not item_target:
            continue                    # an `about_code` dst is a code path — it cannot dangle
        unions.append(
            f"SELECT m.id, ref.value, '{kind}' AS k "                            # nosec B608
            f"  FROM {MEMORY_TABLE} m, "
            f"       LATERAL jsonb_array_elements_text("
            f"           CASE jsonb_typeof(m.doc::jsonb->'{field}') WHEN 'array' "
            f"                THEN m.doc::jsonb->'{field}' ELSE '[]'::jsonb END) AS ref "
            f" WHERE m.doc IS NOT NULL AND jsonb_typeof(m.doc::jsonb) = 'object' "
            f"   AND nullif(ref.value, '') IS NOT NULL "
            f"   AND NOT EXISTS (SELECT 1 FROM {MEMORY_TABLE} t WHERE t.id = ref.value)")
    if not unions:                                       # pragma: no cover - WIRED_KINDS is non-empty
        return "SELECT 0"
    return "SELECT count(*) FROM (SELECT DISTINCT * FROM (" + " UNION ALL ".join(unions) + ") u) d"


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
        # DB.S2b — THE BACKFILL, and it MUST sit here: after the ADD COLUMNs that create the
        # columns (they cannot be populated before they exist) and before the version row that
        # stamps the store as backfilled. Ordering is the safety property, not a preference.
        #
        # Every row written before DB.S2b carries the DDL default while its true scope sits in the
        # `doc` JSON, so each row is corrected FROM ITS OWN DOC — the doc is authoritative, this
        # only projects it into the columns. DML, not DDL: it rewrites no schema, so it is safe to
        # re-run and safe for the same `team init` role that already owns the migration.
        #
        # Idempotent by predicate, not by luck: the WHERE compares each column against what the
        # doc says it should be, so a second run matches ZERO rows and touches nothing. Degrades
        # clean on a malformed doc — `->>` yields NULL, `coalesce` supplies the item model's own
        # default, so a bad row lands on `personal`/''/false/0 (the conservative NARROWEST scope,
        # which leaks nothing) instead of failing the migration.
        f"UPDATE {MEMORY_TABLE} SET "
        f"  {MEMORY_SCOPE_LEVEL_COLUMN} = coalesce(nullif(doc::jsonb->>'scope_level', ''), "
        f"'personal'), "
        f"  {MEMORY_SCOPE_ID_COLUMN} = coalesce(doc::jsonb->>'scope_id', ''), "
        f"  {MEMORY_PIN_COLUMN} = coalesce((doc::jsonb->>'pin')::boolean, FALSE), "
        f"  {MEMORY_PRIORITY_COLUMN} = coalesce((doc::jsonb->>'priority')::int, 0) "
        f"WHERE {MEMORY_SCOPE_LEVEL_COLUMN} IS DISTINCT FROM "
        f"    coalesce(nullif(doc::jsonb->>'scope_level', ''), 'personal') "
        f"   OR coalesce({MEMORY_SCOPE_ID_COLUMN}, '') IS DISTINCT FROM "
        f"    coalesce(doc::jsonb->>'scope_id', '') "
        f"   OR {MEMORY_PIN_COLUMN} IS DISTINCT FROM "
        f"    coalesce((doc::jsonb->>'pin')::boolean, FALSE) "
        f"   OR {MEMORY_PRIORITY_COLUMN} IS DISTINCT FROM "
        f"    coalesce((doc::jsonb->>'priority')::int, 0)",
        # v4 (DB.S5, doc 62 lifecycle) — the bi-temporal window + the usage telemetry. The SAME
        # idempotent ADD-COLUMN-IF-NOT-EXISTS seam the v2 and v3 blocks above use, so re-running
        # `team init` migrates a v3 table in place. Every column is nullable or defaulted, so the
        # ALTER is instant on a populated table and an older client is unaffected by columns it
        # never names.
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_VALID_FROM_COLUMN} TEXT",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_VALID_TO_COLUMN} TEXT",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS {MEMORY_HIT_COUNT_COLUMN} "
        "INT NOT NULL DEFAULT 0",
        f"ALTER TABLE {MEMORY_TABLE} ADD COLUMN IF NOT EXISTS "
        f"{MEMORY_LAST_RECALLED_AT_COLUMN} TIMESTAMPTZ",
        # DB.S5 — OPEN every pre-existing row's validity window, from its own doc. Ordered after
        # the ADD COLUMNs for the same reason the v3 backfill is (a column cannot be populated
        # before it exists) and BEFORE the version row that claims it all ran.
        #
        # `valid_from` ONLY, and the restraint is the safety property: an untouched `valid_to` is
        # an OPEN window, which is the truth about every item that exists today. Writing anything
        # into `valid_to` here would retire the entire shared corpus on upgrade — the precise
        # opposite of the never-delete invariant this column exists to serve. `hit_count` /
        # `last_recalled_at` are likewise NOT backfilled: an item nobody has recalled under a build
        # that could count has honestly been recalled zero times, and synthesising a count would
        # feed invented usage straight into the ranking.
        #
        # Idempotent by PREDICATE, in the v3 backfill's exact `IS DISTINCT FROM` shape rather than
        # a looser "where it is null" — the column is a faithful PROJECTION of the doc (the same
        # one `backends.validity_columns_from_doc` writes), so the honest predicate is "where the
        # column disagrees with the doc". A second run matches zero rows, and a column that somehow
        # drifted from its doc is re-converged rather than left wrong.
        #
        # Degrades clean on a malformed doc — `->>` yields NULL, `coalesce` supplies '' — so a bad
        # row simply keeps an unset window instead of failing the migration.
        f"UPDATE {MEMORY_TABLE} SET {MEMORY_VALID_FROM_COLUMN} = "
        f"  coalesce(nullif(doc::jsonb->>'valid_from', ''), "
        f"           doc::jsonb->'provenance'->>'created_at', '') "
        f"WHERE coalesce({MEMORY_VALID_FROM_COLUMN}, '') IS DISTINCT FROM "
        f"  coalesce(nullif(doc::jsonb->>'valid_from', ''), "
        f"           doc::jsonb->'provenance'->>'created_at', '')",
        # DB.S2b — the stamp the runtime reads before it dares push a scope predicate. Added here
        # (not on the memory table) because it describes the STORE, and read by the same probe
        # that already reads the version range. See SCOPE_BACKFILLED_COLUMN for why the version
        # number alone cannot carry this.
        f"ALTER TABLE {SCHEMA_VERSION_TABLE} ADD COLUMN IF NOT EXISTS "
        f"{SCOPE_BACKFILLED_COLUMN} BOOLEAN NOT NULL DEFAULT FALSE",
        # DB.S3 — the lexical tier's GIN index over the searchable text (subject + the doc's
        # `value`), matching `PostgresBackend.lexical_search`'s expression EXACTLY so the planner
        # can actually use it. Core Postgres: `to_tsvector` needs no `CREATE EXTENSION`, so this
        # stays on the ADR-54 vanilla-PG path (unlike the opt-in pgvector tier).
        #
        # ADDITIVE, and deliberately not a schema-version bump: it adds no column and changes no
        # row, so an older client reads and writes this table unchanged — it simply doesn't use the
        # index. The runtime query works WITHOUT it too (the expression is computed per row), so a
        # DML-only role that never ran `team init` gets correct results, just slower. That is why
        # this lives in init's DDL and nowhere near a runtime connect (D1/C4: no runtime DDL).
        f"CREATE INDEX IF NOT EXISTS {MEMORY_TABLE}_fts ON {MEMORY_TABLE} USING GIN ("
        f"to_tsvector('english', coalesce(subject, '') || ' ' || "
        f"coalesce((doc::jsonb->>'value'), '')))",
        # ---------------------------------------------------------------- v5 (DB.S7a) memory edges
        # A NEW TABLE, not a column on `mokata_memory` — so the v5 migration cannot rewrite a single
        # existing row, and an older client that has never heard of it reads and writes the memory
        # table exactly as before. That is why the floor stays at 3 (see TEAM_SCHEMA_MIN_SUPPORTED).
        #
        # `valid_to` is NULLABLE and NULL means OPEN — the same convention the item window uses, and
        # the reason the unique index below can be PARTIAL.
        f"CREATE TABLE IF NOT EXISTS {EDGES_TABLE} ("
        f"  {_edges.SRC_COLUMN} TEXT NOT NULL,"
        f"  {_edges.DST_COLUMN} TEXT NOT NULL,"
        f"  {_edges.KIND_COLUMN} TEXT NOT NULL,"
        f"  {_edges.VALID_FROM_COLUMN} TEXT,"
        f"  {_edges.VALID_TO_COLUMN} TEXT,"
        f"  {_edges.CREATED_AT_COLUMN} TEXT,"
        f"  {_edges.CREATED_BY_COLUMN} TEXT,"
        f"  {_edges.APPROVAL_LEDGER_COLUMN} BIGINT,"
        f"  seq BIGSERIAL PRIMARY KEY)",
        # AT MOST ONE OPEN EDGE per (src, dst, kind) — a PARTIAL unique index, not a primary key,
        # and the difference is the whole never-delete story. A plain PK would force a relation that
        # is withdrawn and later re-asserted either to rewrite its own closed window or to be
        # refused; the partial index lets the closed row stay on disk forever as history while a new
        # OPEN window is inserted beside it. It is also what makes the `WHERE NOT EXISTS` predicate
        # in `memory/edges.insert_open_sql` an INDEX probe rather than a scan.
        f"CREATE UNIQUE INDEX IF NOT EXISTS {EDGES_TABLE}_open "
        f"  ON {EDGES_TABLE} ({_edges.SRC_COLUMN}, {_edges.DST_COLUMN}, {_edges.KIND_COLUMN})"
        f"  WHERE {_edges.VALID_TO_COLUMN} IS NULL",
        # The traversal index. DB.S7b's ≤2-hop expansion walks src→dst over OPEN edges; without
        # this every hop is a sequential scan. Provisioned HERE for the same reason DB.S3's GIN and
        # DB.S4's HNSW are: the query is CORRECT without it and merely slow, so the index is an
        # artifact of the init path and never something a runtime connect conjures (D1/C4).
        f"CREATE INDEX IF NOT EXISTS {EDGES_TABLE}_dst "
        f"  ON {EDGES_TABLE} ({_edges.DST_COLUMN}, {_edges.KIND_COLUMN})"
        f"  WHERE {_edges.VALID_TO_COLUMN} IS NULL",
        # THE v5 BACKFILL — migrate the three IMPLICIT doc-JSON edge kinds into explicit rows.
        # Ordered here for the reason the v3 and v4 backfills are: after the DDL that creates the
        # table it populates, before the version row that claims the whole pass ran.
        #
        # Read the three properties this statement is built to have, because each is a pin:
        #
        #   * ZERO HUMAN RE-APPROVAL (E2). It moves relations a human ALREADY approved — every ref
        #     it reads is a field of an already-gated `doc`, and it derives no new content, changes
        #     no item, and asks nothing. Re-prompting for the migration of a fact that was approved
        #     when it was written would be asking the same question twice.
        #   * IDEMPOTENT BY PREDICATE (E1), the DB.S2b/DB.S5 shape: `NOT EXISTS (an open edge
        #     already saying this)`. A second `team init` matches zero rows. `DISTINCT` collapses a
        #     doc that lists the same ref twice, which the partial unique index would otherwise
        #     refuse mid-migration.
        #   * SKIP-AND-REPORT ON A DANGLING REF (E7). The `EXISTS (… mokata_memory …)` clause is
        #     the skip: a `supersedes`/`depends_on` ref whose target item is not in the store yields
        #     NO edge rather than an edge into nothing, and the migration keeps going. The COUNT of
        #     what it skipped is reported separately (`dangling_edge_refs_sql`) and surfaced by
        #     `team init`, so a silent skip is impossible — this degrades clean exactly as
        #     `_backfill_lifecycle_columns` does, and never orphans an edge or fails the pass.
        #
        # `about_code` is deliberately NOT existence-checked: its dst is a code path/symbol that
        # lives in the repo, not a row in this table, so "no such item" is not a defect there and
        # counting it as one would report a dangling ref for every correctly-formed code anchor.
        *_edge_backfill_sql(),
        # M-1/R9 — fill the approval id DB.S7a had to leave NULL on edges it migrated before items
        # were stamped. AFTER the inserts above (the rows must exist to be filled), derived only
        # from an item that actually carries a stamp, and it only ever writes into a NULL — so it
        # never overwrites the id a live-projected edge already inherited from the flush.
        _edge_approval_backfill_sql(),
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
        #
        # DB.S2b — the backfill stamp rides the SAME row, and is set here, LAST, for the same
        # reason the version row has always been last: it is the claim that everything above it
        # ran. A pre-DB.S2b artifact already stamped `version=3` gets the flag flipped by this
        # `DO UPDATE` — and only after the backfill statement above has actually corrected its
        # rows. The runtime's scope pushdown activates on that flag, never on the version alone.
        f"INSERT INTO {SCHEMA_VERSION_TABLE} (version, {MIN_SUPPORTED_COLUMN}, "
        f"{SCOPE_BACKFILLED_COLUMN})"
        f"  VALUES ({TEAM_SCHEMA_VERSION}, {TEAM_SCHEMA_MIN_SUPPORTED}, TRUE)"
        f"  ON CONFLICT (version) DO UPDATE SET "
        f"{MIN_SUPPORTED_COLUMN} = EXCLUDED.{MIN_SUPPORTED_COLUMN}, "
        f"{SCOPE_BACKFILLED_COLUMN} = EXCLUDED.{SCOPE_BACKFILLED_COLUMN}",
    ]


# pgvector's schema — mokata-owned, but OPT-IN and OFF the golden path (ADR-54: vanilla Postgres,
# no extensions). It lives HERE, in the init path, because D1 admits no runtime DDL anywhere: the
# `PgVectorBackend` used to run `CREATE EXTENSION vector` on every connect — the single most
# privileged statement in the codebase, on a path meant for a DML-only role. It is provisioned by
# `provision_vector` (never by the default `team init` pass); the backend only VERIFIES the table.
VECTOR_TABLE = "mokata_memory_vectors"

# DB.S4 — the STAMP table. The binding (Jas 2026-07-14): the embedder's identity + dimension are
# recorded ON the index, and an embedder CHANGE forces a gated re-embed rather than a silent mix.
#
# This is not bookkeeping. Cosine between vectors from two different embedders is arithmetic over
# unrelated coordinate systems: it returns a confident-looking number that means nothing, so a
# half-re-embedded index does not fail — it silently RANKS WRONG, forever, with no symptom a user
# could ever attribute to the cause. The stamp is what makes that state detectable, and the single
# row (`id=1`) is deliberately the whole table: one index, one embedder, no ambiguity.
VECTOR_STAMP_TABLE = "mokata_vector_stamp"


def vector_provision_sql(dim: int, embedder_id: str = "") -> "list[Any]":
    """The ordered, idempotent DDL for the OPT-IN pgvector tier (needs a role that may CREATE
    EXTENSION). Not part of `provision_sql` — the golden path stays extension-free.

    Entries are either a bare SQL string or a `(sql, params)` pair; the stamp UPSERT is the one
    pair, because the embedder id is a VALUE and values ride the driver's placeholders (an id
    interpolated into DDL would be the one string in this file built from non-constant input).

    HNSW (DB.S4) is provisioned here for the same reason DB.S3's GIN is: the query is CORRECT
    without it and merely SLOW — pgvector computes `<=>` per row — so the index is a performance
    artifact of the init path (D1/C4: no runtime DDL), never something a runtime connect conjures.
    `vector_cosine_ops` matches `semantic_search`'s `<=>` operator exactly; an index built for a
    different operator class is one the planner silently declines to use."""
    stmts: "list[Any]" = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"CREATE TABLE IF NOT EXISTS {VECTOR_TABLE} ("
        "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT, status TEXT,"
        f"  doc TEXT, embedding vector({dim}), seq BIGSERIAL, project TEXT)",
        f"ALTER TABLE {VECTOR_TABLE} ADD COLUMN IF NOT EXISTS project TEXT",
        f"CREATE INDEX IF NOT EXISTS {VECTOR_TABLE}_hnsw ON {VECTOR_TABLE}"
        f"  USING hnsw (embedding vector_cosine_ops)",
        f"CREATE TABLE IF NOT EXISTS {VECTOR_STAMP_TABLE} ("
        "  id INT PRIMARY KEY, embedder TEXT NOT NULL, dim INT NOT NULL)",
    ]
    if embedder_id:
        stmts.append((
            f"INSERT INTO {VECTOR_STAMP_TABLE} (id, embedder, dim) VALUES (1, %s, %s)"
            f"  ON CONFLICT (id) DO UPDATE SET embedder=EXCLUDED.embedder, dim=EXCLUDED.dim",
            (embedder_id, int(dim)),
        ))
    return stmts


def provision_vector(dsn: str, *, dim: int, embedder_id: str = "") -> ProvisionResult:
    """Provision the opt-in pgvector schema (the init path — never a runtime connect). Stamps
    `embedder_id` + `dim` onto the index when given, so the runtime can refuse a mismatch."""
    return _run_ddl(dsn, vector_provision_sql(dim, embedder_id),
                    [VECTOR_TABLE, VECTOR_STAMP_TABLE])


def _run_ddl(dsn: str, stmts: "list[Any]", tables: "list[str]",
             *, report_sql: str = "") -> ProvisionResult:
    """Execute an idempotent DDL pass. The ONLY function in mokata that writes schema — every
    caller is an init path, and the AST guard in the D1 test enforces that.

    An entry may be a bare SQL string or a `(sql, params)` pair (DB.S4's stamp UPSERT): a value
    that is not a mokata constant travels as a BOUND parameter, never interpolated into the text.

    `report_sql` (DB.S7a) is an optional single-scalar SELECT run LAST, in the same pass, whose
    value rides back on the result. It is inside the same `try` as the DDL on purpose: it reads a
    table this pass has just created, so a failure there is not a "degraded report" to swallow — it
    means the pass did not do what it claims, and the pass already fails closed and clean."""
    from .memory import _pg
    conn = _pg.get_connection(dsn, ProvisionError)
    skipped = 0
    try:
        for stmt in stmts:
            if isinstance(stmt, tuple):
                conn.execute(stmt[0], stmt[1])
            else:
                conn.execute(stmt)
        if report_sql:
            row = conn.execute(report_sql).fetchone()
            skipped = int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:                              # any DDL failure fails closed + clean
        raise ProvisionError(f"provisioning failed: {exc}") from exc
    reset_schema_cache()             # the schema just changed — the verify cache is now stale.
    return ProvisionResult(statements=stmts, version=TEAM_SCHEMA_VERSION, tables=tables,
                           skipped_dangling_edges=skipped)


def provision(dsn: str, *, project_id: Optional[str] = None) -> ProvisionResult:
    """Run the one idempotent provision pass on `dsn` (doc 48 E5). This is the ONLY DDL path in
    mokata — runtime connects (incl. the probe) never run these. Raises `ProvisionError` on a
    missing driver / unreachable DB / DDL failure (the caller surfaces the S2 fail-closed fixes)."""
    return _run_ddl(dsn, provision_sql(project_id),
                    [SCHEMA_VERSION_TABLE, MEMORY_TABLE, EDGES_TABLE, SESSION_TABLE, AUDIT_TABLE,
                     EVENTS_TABLE],
                    report_sql=dangling_edge_refs_sql())


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
