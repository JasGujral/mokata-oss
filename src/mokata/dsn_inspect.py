"""DB.S0 — DSN provider onboarding + the pooler-trap check (doc 84 §7; ADR-54 H1, doc 85 §1).

A **read-only, secret-free** inspector over the SHAPE of a resolved DSN VALUE. It reads the value
transiently (the caller passes it in — never persisted here) and returns a `DsnInspection`: the
detected provider (Supabase / Neon / RDS / generic) and whether the string is a **transaction-mode
pooler**. It makes NO network connection — pure string/URL parsing — and it NEVER echoes the DSN
value, host, user, or password in any field it returns (only the provider name + the bare port
number, neither of which is a credential).

The binding hedge H1 (doc 85 §1): LISTEN/NOTIFY (team push, CM.S5) and session features die behind
a transaction-mode pooler, so a pooled connection string is the #1 silent team-DB failure. This
module cashes H1 in as a preflight: `pooler_trap_finding` turns a pooled inspection into a LOUD
`PoolerTrapFinding` naming the provider, the direct-string fix, and the env-var NAME — the connect
path (`team.team_connect`) surfaces it and human-gates the write, and DB.S1's `doctor` reuses the
same two primitives (a leaf module — no upward import, LAYER-LINT-clean).

The ACTUAL reachability/auth probe is DB.S1's `doctor` deep-check — deliberately NOT built here.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit

# Provider host markers (grounded, doc 84 §7 / doc 85 §1 H1). Detection is host-substring based;
# the pooled verdict layers a per-provider transaction-mode signal on top.
_SUPABASE_HOST = ("supabase.co", "supabase.com")
_SUPABASE_POOLER_HOST = "pooler.supabase.com"     # the pooler endpoint; :6543 = transaction mode
_SUPABASE_SESSION_PORT = 5432                     # :5432 on the pooler host = session mode (OK)
_NEON_HOST = "neon.tech"
_NEON_POOLER_MARK = "-pooler"                     # Neon's pooled endpoint host segment
_RDS_HOST = "rds.amazonaws.com"
_RDS_PROXY_MARK = ".proxy-"                       # an Amazon RDS Proxy endpoint
# A generic (unrecognised-provider) DSN can still betray a pooler by an explicit pooler token in
# the host, or a well-known transaction-pooler port. Kept conservative (never wrong-confident):
# only these signals flip a generic DSN to pooled.
_GENERIC_POOLER_TOKENS = ("pgbouncer", "pooler")
_GENERIC_POOLER_PORTS = (6543, 6432)

_PROVIDER_LABEL = {
    "supabase": "Supabase",
    "neon": "Neon",
    "rds": "Amazon RDS",
    "generic": "a generic Postgres provider",
}

# The provider-specific "how to get a direct/session string" fix (secret-free; names shapes, not
# the user's own host). Surfaced by the finding + the connect legibility line.
_DIRECT_FIX = {
    "supabase": ("in Supabase → Project Settings → Database, copy the Session/Direct connection "
                 "string (host `db.<ref>.supabase.co`, port 5432), NOT the Transaction pooler "
                 "(`*.pooler.supabase.com:6543`)."),
    "neon": ("in Neon → Connection Details, use the DIRECT endpoint — the host WITHOUT the "
             "`-pooler` segment — not the pooled endpoint."),
    "rds": ("point at the RDS instance/cluster endpoint directly, NOT the RDS Proxy endpoint "
            "(`*.proxy-*`), for LISTEN/NOTIFY + session features."),
    "generic": ("use your provider's DIRECT/SESSION connection string (a non-pooler host / port "
                "5432), not the transaction-mode pooler."),
}


@dataclass
class DsnInspection:
    """The read-only verdict over a DSN's SHAPE (doc 85 §3 ``*Report``-family diagnostic). Carries
    NO secret: only the provider name, the pooled bool, whether it could be classified, the bare
    port number, and a secret-free reason. Never the DSN value, host, user, or password."""

    provider: str            # "supabase" | "neon" | "rds" | "generic"
    pooled: bool             # a transaction-mode pooler was detected (LISTEN/NOTIFY + sessions break)
    classified: bool         # False → the shape could not be parsed/recognised (degrade-clean)
    port: Optional[int] = None
    reason: str = ""

    @property
    def verdict(self) -> str:
        """A one-word posture: ``pooled`` · ``direct`` (direct/session OK) · ``unknown`` (could
        not classify)."""
        if not self.classified:
            return "unknown"
        return "pooled" if self.pooled else "direct"

    @property
    def provider_label(self) -> str:
        return _PROVIDER_LABEL.get(self.provider, _PROVIDER_LABEL["generic"])

    def summary(self, env_name: str) -> str:
        """A single secret-free legibility line for `team connect` — what mokata thinks it is
        connecting to. Names the env-var NAME, never the value."""
        if not self.classified:
            return (f"mokata · ${env_name} → could not classify the connection-string shape — "
                    f"{self.reason}")
        if self.pooled:
            return (f"mokata · ${env_name} → detected {self.provider_label}: "
                    f"TRANSACTION-MODE POOLER (pooled — warned).")
        return (f"mokata · ${env_name} → detected {self.provider_label}: direct/session "
                f"connection (LISTEN/NOTIFY + sessions OK).")


@dataclass
class PoolerTrapFinding:
    """A LOUD, blocking-by-default finding fired when the resolved DSN is a transaction-mode pooler
    (doc 85 §1 H1). ``*Finding`` per doc 85 §3 (read-only diagnostic the connect path acts on). It
    names the provider, the direct-string fix, and the env-var NAME — NEVER the value/credential."""

    provider: str
    env_name: str
    port: Optional[int] = None

    @property
    def provider_label(self) -> str:
        return _PROVIDER_LABEL.get(self.provider, _PROVIDER_LABEL["generic"])

    def render(self) -> str:
        port = f" (port {self.port})" if self.port else ""
        fix = _DIRECT_FIX.get(self.provider, _DIRECT_FIX["generic"])
        return "\n".join([
            f"⚠ POOLED-ENDPOINT DETECTED — ${self.env_name} looks like a {self.provider_label} "
            f"TRANSACTION-MODE POOLER{port}.",
            "  LISTEN/NOTIFY (team push) and session features need a DIRECT/SESSION connection "
            "string; they break SILENTLY behind a transaction-mode pooler.",
            f"  Fix: {fix}",
            "  mokata never rewrites your DSN — update it yourself, then re-run `mokata team "
            "connect`.",
        ])


def _host_port(value: str) -> Tuple[Optional[str], Optional[int], bool]:
    """Extract (host, port, ok) from a DSN VALUE — URL form (``postgres://…``) or libpq keyword
    form (``host=… port=…``). Pure parsing; ``ok`` is False for an unrecognisable shape. The host
    is returned only for transient marker-matching by the caller and is NEVER stored on a result."""
    v = (value or "").strip()
    if not v:
        return None, None, False
    if "://" in v:
        try:
            parts = urlsplit(v)
        except ValueError:
            return None, None, False
        if parts.scheme.lower() not in ("postgres", "postgresql"):
            return None, None, False
        try:
            port = parts.port
        except ValueError:
            port = None
        host = parts.hostname
        if not host:
            return None, None, False
        return host, port, True
    # libpq keyword form: whitespace-separated key=value pairs.
    if "=" in v:
        kv = {}
        for tok in v.split():
            if "=" in tok:
                k, _, val = tok.partition("=")
                kv[k.strip().lower()] = val.strip()
        host = kv.get("host") or kv.get("hostaddr")
        raw_port = kv.get("port")
        try:
            port = int(raw_port) if raw_port else None
        except ValueError:
            port = None
        if host:
            return host, port, True
        return None, None, False
    return None, None, False


def inspect_dsn(value: str) -> DsnInspection:
    """Inspect a resolved DSN VALUE's SHAPE and return a secret-free `DsnInspection`. Detects the
    provider by host markers and whether it is a transaction-mode pooler by host+port markers;
    an unrecognisable shape degrades clean to ``generic`` + ``classified=False`` (never
    wrong-confident). Makes NO network connection and echoes NO secret."""
    host, port, ok = _host_port(value)
    if not ok or not host:
        return DsnInspection(
            provider="generic", pooled=False, classified=False, port=port,
            reason="cannot classify the connection-string shape (not a recognisable Postgres DSN)")
    h = host.lower()

    # --- Supabase ---------------------------------------------------------------------------
    if any(m in h for m in _SUPABASE_HOST):
        # The pooler host is transaction mode on :6543; :5432 on the same host is session mode (OK).
        pooled = (_SUPABASE_POOLER_HOST in h and port != _SUPABASE_SESSION_PORT)
        reason = (f"Supabase transaction-mode pooler detected (pooler host"
                  f"{f', port {port}' if port else ''}); LISTEN/NOTIFY + session features do not "
                  f"work behind it." if pooled else
                  "Supabase direct/session connection (LISTEN/NOTIFY + sessions OK).")
        return DsnInspection("supabase", pooled, True, port, reason)

    # --- Neon -------------------------------------------------------------------------------
    if _NEON_HOST in h:
        pooled = _NEON_POOLER_MARK in h
        reason = ("Neon pooled endpoint detected (the '-pooler' host segment) — a transaction-mode "
                  "PgBouncer pooler where LISTEN/NOTIFY + sessions break." if pooled else
                  "Neon direct endpoint (no '-pooler' segment) — LISTEN/NOTIFY + sessions OK.")
        return DsnInspection("neon", pooled, True, port, reason)

    # --- Amazon RDS -------------------------------------------------------------------------
    if _RDS_HOST in h:
        pooled = _RDS_PROXY_MARK in h
        reason = ("Amazon RDS Proxy endpoint detected (the '.proxy-' host segment); RDS Proxy does "
                  "not support LISTEN/NOTIFY + some session features." if pooled else
                  "Amazon RDS instance/cluster endpoint — LISTEN/NOTIFY + sessions OK.")
        return DsnInspection("rds", pooled, True, port, reason)

    # --- generic (recognised as a Postgres DSN, provider unknown) ---------------------------
    pooled = any(tok in h for tok in _GENERIC_POOLER_TOKENS) or (port in _GENERIC_POOLER_PORTS)
    reason = (f"a transaction-mode pooler{f' (port {port})' if port else ''} — LISTEN/NOTIFY + "
              f"sessions break behind it." if pooled else
              "generic Postgres DSN (provider not recognised; assuming a direct/session "
              "connection).")
    return DsnInspection("generic", pooled, True, port, reason)


def pooler_trap_finding(inspection: DsnInspection, env_name: str) -> Optional[PoolerTrapFinding]:
    """The H1 preflight verdict: a `PoolerTrapFinding` when the inspection is a transaction-mode
    pooler, else None. Names the env-var NAME (never the value)."""
    if not inspection.pooled:
        return None
    return PoolerTrapFinding(provider=inspection.provider, env_name=env_name,
                             port=inspection.port)


__all__ = ["DsnInspection", "PoolerTrapFinding", "inspect_dsn", "pooler_trap_finding"]
