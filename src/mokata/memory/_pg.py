"""Shared psycopg connect path (Stage 39 / M3 dedup).

Both the Postgres memory backend and the pgvector backend lazy-import `psycopg`, connect with
autocommit, run their owned-schema setup SQL, and degrade by raising a typed `*Unavailable`
exception. That boilerplate lived in two places; it lives here now. `psycopg` is an optional
extra — imported lazily so the core stays dependency-free.
"""

from __future__ import annotations

from typing import Any, Iterable, Type


def connect_psycopg(dsn: str, unavailable: Type[Exception],
                    setup_sql: Iterable[str] = ()) -> Any:
    """Lazy-import psycopg, connect (autocommit), run each `setup_sql` statement, and return the
    connection. A missing driver OR any connect/setup failure raises `unavailable(...)` so the
    caller degrades cleanly (never a hard failure)."""
    try:
        import psycopg  # optional extra; lazy so the core stays dependency-free
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise unavailable(
            "psycopg is not installed (optional extra 'postgres')") from exc
    try:
        conn = psycopg.connect(dsn, autocommit=True)
        for stmt in setup_sql:
            conn.execute(stmt)
        return conn
    except Exception as exc:  # any connect/extension/setup failure degrades cleanly
        raise unavailable(f"database unavailable: {exc}") from exc
