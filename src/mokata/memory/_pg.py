"""Shared psycopg connect path + per-process connection manager (Stage 39 / M3 dedup; TM.S2).

Both the Postgres memory backend and the pgvector backend (plus the session transport and
`team.py`) lazy-import `psycopg`, connect with autocommit, run their owned-schema setup SQL,
and degrade by raising a typed `*Unavailable` exception. That boilerplate lives here.

TM.S2 adds the doc-48 **connection manager** (E4) + **timeouts** (E2):
  * ONE named managed connection per DSN, shared per-process by every Postgres consumer
    (they used to each open their own `psycopg.connect` — doc 48 finding 2). A dead cached
    connection is transparently replaced.
  * EVERY connect carries a `connect_timeout` AND a server-side `statement_timeout` — no
    unbounded hangs (doc 48 finding 4). Local mode is untouched; `psycopg` stays an optional
    extra, imported lazily so the core stays dependency-free.

This module NEVER owns DDL — and after D1 (0.0.13) it cannot carry any either: `connect_psycopg`
lost its `setup_sql` parameter, so a runtime connect VERIFIES the schema (`teamdb.ensure_schema`,
one cached read-only probe) and can no longer write it. `team init` (teamdb) is the sole owner.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple, Type

# doc 48 E2 — timeouts on every connect. `connect_timeout` is libpq seconds (its floor is a
# couple of seconds); `statement_timeout` is a server-side GUC in milliseconds set via the
# connection `options`. Both are backstops against a hung DB — the tight ≤500ms preflight
# bound is enforced wall-clock by `teamdb.probe`, on top of these.
CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 30_000


# The per-process connection cache: {dsn -> live connection}. mokata's CLI/MCP process is
# single-threaded per run, so one shared connection per DSN is correct (and what E4 wants).
_MANAGER: Dict[str, Any] = {}


def reset_manager() -> None:
    """Drop every cached connection (best-effort close). For tests + a clean re-probe.

    AUTHORITATIVE since PROBE-ORPHAN was fixed. It previously was not: a probe worker that had
    outlived its caller could publish into `_MANAGER` AFTER a teardown ran, so "reset" meant "reset
    unless a probe is in flight". Probe workers no longer publish (`open_unmanaged` + `adopt`), so
    when this returns the cache is empty and stays empty until someone still-waiting adopts.
    """
    for conn in list(_MANAGER.values()):
        close_quietly(conn)
    _MANAGER.clear()


def _is_live(conn: Any) -> bool:
    """A cached psycopg connection is reusable unless it reports closed."""
    try:
        return not getattr(conn, "closed", 0)
    except Exception:  # pragma: no cover - defensive
        # D5 — deliberately left BROAD, with no narrow class to name: `conn.closed` is a
        # THIRD-PARTY (psycopg) property whose getter may raise a driver class we cannot name
        # without a hard dependency on the optional extra. False = "not live" = reconnect, which is
        # the safe direction (a fresh connection, never a silently dead one).
        return False


def _raw_connect(dsn: str, *, connect_timeout: int, statement_timeout_ms: int) -> Any:
    """Lazy-import psycopg and open ONE autocommit connection with BOTH timeouts. Raises the
    original exception (ImportError / connect failure) — the manager wraps it in the caller's
    typed unavailable."""
    import psycopg  # optional extra; lazy so the core stays dependency-free
    return psycopg.connect(
        dsn,
        autocommit=True,
        connect_timeout=connect_timeout,
        options=f"-c statement_timeout={statement_timeout_ms}",
    )


def open_unmanaged(dsn: str, unavailable: Type[Exception], *,
                   connect_timeout: int = CONNECT_TIMEOUT_S,
                   statement_timeout_ms: int = STATEMENT_TIMEOUT_MS) -> Any:
    """A timeout-bounded connection that is deliberately NOT in `_MANAGER` — the caller owns it.

    PROBE-ORPHAN (doc 84). `teamdb.probe` runs its work in a daemon thread joined for a budget and
    WALKS AWAY on timeout; the thread lives on. While that worker connected through
    `get_connection`, a probe that had already reported UNREACHABLE could still install a live
    connection into the process-global `_MANAGER` with nobody waiting for it — so the next caller
    silently reused a connection whose probe said the database was down, and `reset_manager()` was
    not authoritative while a probe was in flight (a teardown that ran BEFORE the orphan landed
    was simply overwritten).

    The fix is structural rather than a flag: a worker that may be abandoned MUST NOT be able to
    publish. It connects through here, holds the connection privately, and only a caller that is
    STILL WAITING adopts it via `adopt`. Nothing that outlives its waiter can reach the cache.

    Raises `unavailable(...)` on any connect failure, exactly as `get_connection` does — the two
    differ ONLY in whether the result is published, never in how they fail.
    """
    try:
        return _raw_connect(dsn, connect_timeout=connect_timeout,
                            statement_timeout_ms=statement_timeout_ms)
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise unavailable("psycopg is not installed (optional extra 'postgres')") from exc
    except Exception as exc:
        raise unavailable(f"database unavailable: {exc}") from exc


def adopt(dsn: str, conn: Any) -> None:
    """Publish an `open_unmanaged` connection into the shared cache — the ONE way a privately
    opened connection becomes the process-wide one for `dsn`.

    Called only by a caller that is still waiting on the work that opened it (PROBE-ORPHAN).
    EXACTLY ONE of the two connections is closed, always: leaving both open leaks a socket, and
    the cache holds one connection per DSN by construction.

    ADOPT-LIVE-HANDLE (doc 84, fixed 2026-08-02) — WHICH of the two closes is the whole content of
    this function, and the first version chose backwards. It closed the CACHED connection to avoid
    "stranding a live socket", but the cached connection is precisely the one with OTHER REFERENTS:
    `PostgresBackend`, `PgVectorStore`, `PostgresTransport` and the shared audit ledger each take
    the managed connection ONCE in `__init__` and hold it as `self._conn` for their whole lifetime.
    The incoming connection has exactly one referent — the worker that just opened it. So closing
    the cached one does not strand a socket, it INVALIDATES live handles process-wide; every
    consumer built before the probe then raises `the connection is closed` on its next statement.

    A LIVE cached connection therefore WINS and the incoming one is closed as the redundancy it is.
    A cached connection that reports closed has nothing worth keeping and IS replaced, so the cache
    still heals itself — "never displace" would strand the process on a dead socket, which is the
    same class of bug pointing the other way.
    """
    previous = _MANAGER.get(dsn)
    if previous is conn:
        return
    if previous is not None and _is_live(previous):
        close_quietly(conn)                  # the cache already has a live, REFERENCED connection
        return
    if previous is not None:                 # a dead cached connection — nothing to preserve
        close_quietly(previous)
    _MANAGER[dsn] = conn


def close_quietly(conn: Any) -> None:
    """Best-effort close. THE one place that swallow is spelled, so the three call sites that need
    it (`reset_manager`, `adopt`, the abandoned probe worker) cannot drift apart on it."""
    try:
        conn.close()
    except Exception:
        # D5 — deliberately BROAD with no narrow class to name, and this is the reason
        # `reset_manager` already carried verbatim: `conn` is a THIRD-PARTY (psycopg) or injected
        # object whose `close()` raises classes mokata cannot import without a hard dependency on
        # the optional extra. Closing is a teardown that cannot fail in a way anyone can act on —
        # nothing to degrade, nothing to report; the connection is being dropped either way.
        pass


def get_connection(dsn: str, unavailable: Type[Exception], *,
                   connect_timeout: int = CONNECT_TIMEOUT_S,
                   statement_timeout_ms: int = STATEMENT_TIMEOUT_MS) -> Any:
    """The managed connection for `dsn` (doc 48 E4): return the shared cached one, or open +
    cache a fresh timeout-bounded connection. A dead cached connection is replaced. A missing
    driver OR any connect failure raises `unavailable(...)` so the caller degrades cleanly."""
    cached = _MANAGER.get(dsn)
    if cached is not None and _is_live(cached):
        return cached
    if cached is not None:                       # a dead connection — drop it, reconnect.
        _MANAGER.pop(dsn, None)
    try:
        conn = _raw_connect(dsn, connect_timeout=connect_timeout,
                            statement_timeout_ms=statement_timeout_ms)
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise unavailable(
            "psycopg is not installed (optional extra 'postgres')") from exc
    except Exception as exc:                     # any connect failure degrades cleanly
        raise unavailable(f"database unavailable: {exc}") from exc
    _MANAGER[dsn] = conn
    return conn


def connect_psycopg(dsn: str, unavailable: Type[Exception], *,
                    require_tables: Iterable[str] = ()) -> Any:
    """Return the shared managed connection for `dsn` (timeout-bounded), VERIFIED against the
    shared schema. The ONE seam every runtime Postgres consumer connects through.

    D1: this used to take a `setup_sql` list and execute it — that parameter is how four backends
    ran their own hand-mirrored `CREATE TABLE …` on EVERY connect, which a least-privilege
    DML-only role is denied (SQLSTATE 42501) even against a perfectly provisioned database. The
    parameter is GONE, so runtime DDL is now unrepresentable here rather than merely discouraged.
    What replaces it is a VERIFY: one cached, read-only schema probe (E2). A missing/out-of-range
    schema raises `unavailable(...)` carrying `failure_class` + the exact `mokata team init` fix,
    so the caller degrades LOUDLY and honestly — never silently to the local floor.

    `require_tables` verifies extra mokata-owned tables that sit OUTSIDE the version artifact
    (today only pgvector's opt-in table)."""
    from ..teamdb import ensure_schema
    ensure_schema(dsn, unavailable, require_tables=require_tables)
    return get_connection(dsn, unavailable)
