"""DB.S1 — the `mokata doctor` DSN deep-check (doc 84 §7; ADR-54 H1, doc 85 §1).

Bare Claude Code hands you a psycopg stack trace when the team DB is misconfigured. This module
tells you WHICH layer failed — driver vs network vs auth vs pooler vs schema-version — each as a
NAMED, actionable finding with the specific fix, and NEVER echoes the DSN value, host, user, or
password (P16 legibility, secret-free).

It owns NO network machinery of its own. The connection signal comes from `teamdb.probe` (the
bounded, fail-closed ≤500ms preflight — reused, never re-implemented); the SHAPE signal (provider
+ transaction-mode-pooler) comes from `dsn_inspect` (DB.S0, pure string parsing, no network). The
DSN VALUE is resolved transiently through the ONE resolver (`dsn.resolve_dsn`, C-1) and passed only
to those two secret-free primitives — never persisted, never rendered (the DB.S0 invariant holds).

`classify()` maps one probe attempt to exactly ONE primary finding (the most fundamental failing
layer) plus, orthogonally, a POOLER co-report when a reachable connection is a transaction-mode
pooler. `deep_check()` gates on team mode + a set DSN (a local / no-DSN repo is silent — zero
probe, zero noise, P8) and drives the whole thing. The doctor wiring (`govern.doctor.diagnose`)
turns each finding into a `DoctorFinding`, so a hard failure flips doctor's exit through the SAME
`report.ok` it already derives from — never a second exit path.

The SCHEMA-VERSION axis is a DIFFERENT axis than the MCP-server version parity (B-VER,
`mcp_admin.version_parity`): one is the shared-DB schema, the other is which mokata binary Claude
Code launches. They are kept as distinct findings and never conflated.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from . import dsn_inspect, teamdb

# Severities line up 1:1 with `govern.doctor.DoctorFinding` so the wiring maps them with no
# translation table: "error" flips doctor's exit (a genuinely broken team connection), "warning"
# and "info" never do (report.ok = no errors).
SEV_ERROR = "error"
SEV_WARNING = "warning"
SEV_INFO = "info"

# The named axes (also the finding `code`s in doctor's table, prefixed so the DSN deep-check reads
# as one section). Each is one layer a team connection can fail at — plus POOLER (orthogonal) and
# OK (all green).
AXIS_DRIVER = "db-driver-absent"
AXIS_NETWORK = "db-network"
AXIS_AUTH = "db-auth"
AXIS_SCHEMA_ABSENT = "db-schema-absent"
AXIS_SCHEMA_OLD = "db-schema-old"          # the DB is below this build's floor → migrate the DB
AXIS_CLIENT_OLD = "db-client-old"          # the DB is ahead of this build → upgrade mokata
AXIS_SCHEMA_WARN = "db-schema-version"     # in-range but the versions differ → advisory only
AXIS_POOLER = "db-pooler"
AXIS_OK = "db-ok"


@dataclass
class DsnDeepCheckFinding:
    """One named, secret-free verdict along a single DSN axis (doc 85 §3 read-only diagnostic;
    the `*Finding` family DB.S0's `PoolerTrapFinding` established). Carries the env-var NAME, the
    provider LABEL, the bare port, and the schema version — NEVER the DSN value, host, user, or
    password.

    Two renderings, one truth: `summary` is the terse doctor-table cell (feeds `report.ok`);
    `detail` is the full actionable line — what failed + the concrete fix + the env-var NAME — for
    doctor's `database (team DSN)` section. Both are secret-free."""

    axis: str
    severity: str          # SEV_ERROR | SEV_WARNING | SEV_INFO
    summary: str           # the terse table cell (also what `report.ok` is derived over)
    detail: str            # the full human line: what failed + the concrete fix + the env-var NAME
    env_name: str = ""
    provider: str = ""     # a provider LABEL (e.g. "Supabase") — never a host
    port: Optional[int] = None
    schema_version: Optional[int] = None

    @property
    def code(self) -> str:
        """The doctor-table code for this axis."""
        return self.axis

    @property
    def is_error(self) -> bool:
        return self.severity == SEV_ERROR

    def render(self, *, ascii_only: bool = False) -> str:
        """The single secret-free line for the doctor section. A glyph carries the severity so a
        broken connection is never silent; the detail already names the fix + the env-var NAME."""
        glyph = {SEV_ERROR: "[x]" if ascii_only else "✗",
                 SEV_WARNING: "[!]" if ascii_only else "⚠",
                 SEV_INFO: "[ok]" if ascii_only else "✓"}.get(self.severity, "-")
        return f"  {glyph} {self.detail}"


@dataclass
class DsnDeepCheck:
    """The DSN deep-check verdict: the PRIMARY finding (the most fundamental failing layer, or OK)
    plus any orthogonal co-reports (today only the POOLER warning). `ok` is False iff a finding is
    an error — the SAME semantics doctor's `report.ok` uses, so the two can never disagree."""

    findings: List[DsnDeepCheckFinding] = field(default_factory=list)

    @property
    def primary(self) -> Optional[DsnDeepCheckFinding]:
        return self.findings[0] if self.findings else None

    @property
    def ok(self) -> bool:
        return not any(f.is_error for f in self.findings)

    def render_lines(self, *, ascii_only: bool = False) -> List[str]:
        """The `database (team DSN)` section: a header line + one line per finding."""
        if not self.findings:
            return []
        return ["database (team DSN):"] + [f.render(ascii_only=ascii_only) for f in self.findings]


# --------------------------------------------------------------------------- the fixes (secret-free)
def _pooler_detail(inspection: dsn_inspect.DsnInspection, env_name: str) -> str:
    """The POOLER fix line, reusing DB.S0's `PoolerTrapFinding` so the direct-string remediation
    can't drift from the connect-path one. Names the provider + env NAME + bare port only."""
    finding = dsn_inspect.pooler_trap_finding(inspection, env_name)
    label = inspection.provider_label
    port = f" (port {inspection.port})" if inspection.port else ""
    # PoolerTrapFinding.render() carries the full provider-specific direct-string fix; fold its
    # body (minus the glyph header) into one secret-free line for the doctor section.
    fix = ""
    if finding is not None:
        for line in finding.render().splitlines():
            s = line.strip()
            if s.startswith("Fix:"):
                fix = " " + s
    return (f"{label}: TRANSACTION-MODE POOLER{port} — LISTEN/NOTIFY (team push) + session "
            f"features break SILENTLY behind it; point ${env_name} at the DIRECT/SESSION "
            f"connection string, not the pooler.{fix}")


# --------------------------------------------------------------------------- the classifier
def classify(res: Any, inspection: dsn_inspect.DsnInspection,
             env_name: str) -> DsnDeepCheck:
    """Map ONE probe attempt to exactly one PRIMARY finding — the most fundamental failing layer,
    driver → network → auth → schema — and, orthogonally, a POOLER co-report when a REACHABLE
    connection is a transaction-mode pooler. Pure: no network, injected `res`/`inspection` doubles.

    Fail-closed direction: anything short of reachable-authed-and-in-range is an ERROR (flips
    doctor's exit); an in-range-but-different schema version and a pooler are WARNINGS (OK)."""
    label = inspection.provider_label if inspection is not None else "the team DB"
    port = inspection.port if inspection is not None else None

    def finding(axis: str, sev: str, summary: str, detail: str, **kw) -> DsnDeepCheckFinding:
        return DsnDeepCheckFinding(axis=axis, severity=sev, summary=summary, detail=detail,
                                   env_name=env_name, provider=label, port=port, **kw)

    conn = getattr(res, "conn_reason", "")

    # 1) DRIVER-ABSENT — nothing to probe with; the optional extra is not installed.
    if not getattr(res, "driver_present", True) or conn == teamdb.CONN_DRIVER_ABSENT:
        return DsnDeepCheck([finding(
            AXIS_DRIVER, SEV_ERROR,
            "the Postgres driver (psycopg) is not installed",
            f"the Postgres driver (psycopg) is not installed, so ${env_name} cannot be checked — "
            f"install the optional extra: `pip install 'mokata[postgres]'`")])

    # 2) NETWORK — the connect never reached a responding server (DNS / host down / port closed /
    #    timeout). Distinct from auth: no server ever answered.
    if conn == teamdb.CONN_TIMEOUT:
        ms = int(getattr(res, "elapsed_ms", 0)) or teamdb.PROBE_BUDGET_MS
        return DsnDeepCheck([finding(
            AXIS_NETWORK, SEV_ERROR,
            f"${env_name} is unreachable — no response within the probe budget",
            f"${env_name} is UNREACHABLE — no response within the probe budget ({ms}ms). Check "
            f"the host is up and reachable from here (network / DNS / firewall / port)")])
    if conn == teamdb.CONN_NETWORK_UNREACHABLE or (
            not getattr(res, "reachable", False) and conn != teamdb.CONN_AUTH_FAILED):
        return DsnDeepCheck([finding(
            AXIS_NETWORK, SEV_ERROR,
            f"${env_name} is unreachable (network / DNS / port closed)",
            f"${env_name} is UNREACHABLE — could not connect (DNS / host down / port closed). "
            f"Check the host and port, the firewall, and that the database is running")])

    # 3) AUTH — the host ANSWERED and rejected the credentials. Distinct from network: reachable
    #    server, bad role/password.
    if conn == teamdb.CONN_AUTH_FAILED:
        return DsnDeepCheck([finding(
            AXIS_AUTH, SEV_ERROR,
            f"authentication failed for ${env_name} (the host answered, creds rejected)",
            f"the host answered but AUTHENTICATION FAILED for ${env_name} — check the role and "
            f"password in that env var (the network is fine; the credentials were rejected)")])

    # From here the connection round-trip SUCCEEDED. A pooler is orthogonal and co-reports.
    pooler_co: List[DsnDeepCheckFinding] = []
    if inspection is not None and getattr(inspection, "pooled", False):
        pooler_co = [finding(
            AXIS_POOLER, SEV_WARNING,
            f"{label}: transaction-mode POOLER — LISTEN/NOTIFY + sessions break behind it",
            _pooler_detail(inspection, env_name))]

    # 4) SCHEMA-VERSION — reachable + authed. `compatible=False` is a HARD, exit-flipping break
    #    (absent / below-floor / ahead-of-client); an in-range version difference only WARNS.
    if not getattr(res, "compatible", False):
        reason = getattr(res, "reason", "")
        sv = getattr(res, "schema_version", None)
        if reason == teamdb.REASON_SCHEMA_TOO_OLD:
            primary = finding(
                AXIS_SCHEMA_OLD, SEV_ERROR,
                "connected, but the shared schema is older than this build serves",
                f"connected, but the shared schema is OLDER than this build can serve — migrate "
                f"it: run `mokata team init` to upgrade the shared schema", schema_version=sv)
        elif reason == teamdb.REASON_CLIENT_TOO_OLD:
            primary = finding(
                AXIS_CLIENT_OLD, SEV_ERROR,
                "connected, but the shared schema is newer than this build",
                f"connected, but the shared schema is NEWER than this build understands — upgrade "
                f"mokata: `pip install -U mokata` (the schema is ahead of this client)",
                schema_version=sv)
        else:  # REASON_SCHEMA_ABSENT (or an empty/unknown reason) → provision.
            primary = finding(
                AXIS_SCHEMA_ABSENT, SEV_ERROR,
                "connected, but the shared schema is not provisioned",
                f"connected + authenticated, but the shared schema is NOT PROVISIONED — run "
                f"`mokata team init` to create it", schema_version=sv)
        return DsnDeepCheck([primary] + pooler_co)

    # in-range but the versions differ (client-newer-than-schema, or schema-ahead-in-range) → WARN.
    warning = getattr(res, "warning", "")
    if warning:
        return DsnDeepCheck([finding(
            AXIS_SCHEMA_WARN, SEV_WARNING,
            "connected; the schema version differs but is in range",
            f"connected + schema in range, but versions differ: {warning}",
            schema_version=getattr(res, "schema_version", None))] + pooler_co)

    # 5) OK — all green. One concise healthy line naming the provider + schema version.
    ms = int(getattr(res, "elapsed_ms", 0) or 0)
    sv = getattr(res, "schema_version", None)
    sv_txt = f"schema v{sv}" if sv is not None else "schema present"
    return DsnDeepCheck([finding(
        AXIS_OK, SEV_INFO,
        f"team DB OK — {label}, {sv_txt}",
        f"team DB OK — {label}, {sv_txt}, reachable ({ms}ms) via ${env_name}",
        schema_version=sv)] + pooler_co)


# --------------------------------------------------------------------------- the driver
def deep_check(surface: Any, *, environ: Optional[dict] = None,
               probe: Optional[Callable[[str], Any]] = None,
               budget_ms: int = teamdb.PROBE_BUDGET_MS) -> Optional[DsnDeepCheck]:
    """Run the DSN deep-check for `surface`, or return None when there is nothing to check.

    Silent (None, zero probe) unless the repo is TEAM-connected: local / solo mode, or team mode
    with the DSN env var unset (which `team_health` already reports loudly), returns None so a
    no-DSN repo stays quiet (P8). Otherwise the DSN VALUE is resolved transiently, its SHAPE is
    inspected (no network), and the bounded `teamdb.probe` supplies the connection signal — then
    `classify` names the verdict. `probe` is injectable for tests; the default is the real bounded
    probe, so a hanging connect is wall-clock capped and doctor never hangs."""
    import os

    from . import run_mode as _rm
    from .dsn import resolve_dsn

    env = os.environ if environ is None else environ
    # `read_mode` degrades to local on any unreadable/invalid mode and NEVER raises (its contract),
    # so no guard is needed here — a broken mode reads as local and this stays silent (P8).
    if _rm.read_mode(surface) != _rm.TEAM:
        return None                              # local / solo — zero probe, zero noise (P8)
    res_dsn = resolve_dsn(surface, environ=env)
    if not res_dsn.is_set or not res_dsn.dsn:
        return None                              # team mode, $ENV unset — team_health owns that line
    env_name = res_dsn.env_name
    dsn_value = res_dsn.dsn                       # transient — passed only to secret-free primitives
    inspection = dsn_inspect.inspect_dsn(dsn_value)

    try:
        if probe is not None:
            res = probe(dsn_value)
        else:
            res = teamdb.probe(dsn_value, budget_ms=budget_ms)
    except Exception as exc:                      # the probe is fail-closed, but never trust it
        res = teamdb.ProbeResult(reachable=False, compatible=False,
                                 conn_reason=teamdb.CONN_NETWORK_UNREACHABLE, error=str(exc))
    return classify(res, inspection, env_name)


def deep_check_lines(surface: Any, *, environ: Optional[dict] = None,
                     probe: Optional[Callable[[str], Any]] = None,
                     ascii_only: bool = False) -> List[str]:
    """The doctor `database (team DSN)` section as rendered lines (empty on a non-team repo). A
    thin wrapper over `deep_check` for surfaces that print a section without touching `report.ok`."""
    check = deep_check(surface, environ=environ, probe=probe)
    if check is None:
        return []
    return check.render_lines(ascii_only=ascii_only)


__all__ = [
    "DsnDeepCheckFinding", "DsnDeepCheck", "classify", "deep_check", "deep_check_lines",
    "SEV_ERROR", "SEV_WARNING", "SEV_INFO",
    "AXIS_DRIVER", "AXIS_NETWORK", "AXIS_AUTH", "AXIS_SCHEMA_ABSENT", "AXIS_SCHEMA_OLD",
    "AXIS_CLIENT_OLD", "AXIS_SCHEMA_WARN", "AXIS_POOLER", "AXIS_OK",
]
