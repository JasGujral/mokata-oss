"""TM.S1 — run mode: `local` (default, zero-config) vs `team` (shared DBs).

Run mode is a first-class, VISIBLE property of every session. `local` is the default and
requires nothing — byte-for-byte zero-config, today's behaviour, everything under
`.mokata/`. `team` points the memory/knowledge/event stores at shared databases and
requires prerequisites that are checked FAIL-CLOSED before activation.

This module is the SINGLE SOURCE OF TRUTH every surface reads from — the statusline badge,
the SessionStart bootstrap line, the CLI banner, `mokata doctor`, and every harness — so a
session is never ambiguous about which mode it's in.

Scope (TM.S1 = mode PLUMBING + SURFACE only): the shared-DB connection manager and the real
≤500ms DB probe land in **TM.S2**. Until then `team_preflight` FAILS CLOSED — it always
refuses activation, naming the missing connection manager (TM.S2) as a blocker, while still
reporting the S1-checkable prereqs (a usable run identity + `$MOKATA_PG_DSN` credential
presence). Team mode is NEVER half-activated.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import team_docs

# The two run modes. `local` is the default; anything else the setting could hold is NOT
# team (fail-closed) and reads back as local.
LOCAL = "local"
TEAM = "team"
MODES = (LOCAL, TEAM)

# The manifest settings key. `settings.mode` — an open-ended settings entry, so an absent
# key keeps a Stage-1 (zero-config) manifest byte-for-byte unchanged and reads as local.
MODE_SETTING = "mode"

# The env-var NAME carrying the shared-DB DSN on the golden path (doc 48). The secret DSN
# is never stored — only its presence is checked here.
CREDENTIAL_ENV = "MOKATA_PG_DSN"


def is_valid_mode(value: Any) -> bool:
    """True only for a recognised run mode string (`local`/`team`)."""
    return value in MODES


def stored_mode(surface: Any) -> Optional[str]:
    """The RAW `settings.mode` value as committed, or None when absent — so `doctor` can
    SEE (and flag) a hand-edited invalid value while `read_mode` stays safe. Degrade-clean:
    a broken surface reads as absent (None), never an exception."""
    try:
        val = surface.manifest.setting(MODE_SETTING, None)
    except Exception:
        return None
    return val if isinstance(val, str) and val else None


def read_mode(surface: Any) -> str:
    """The effective run mode. Absent/broken/unrecognised → `local` (local-first + fail-
    closed: an unknown value is NEVER team). Never raises."""
    val = stored_mode(surface)
    return val if is_valid_mode(val) else LOCAL


# ------------------------------------------------------------------ the team preflight (S1)
@dataclass
class PreflightCheck:
    """One prerequisite for activating team mode. `fix` is the actionable next step, present
    on every blocker (fail-closed: a named fix, never a silent refusal)."""

    name: str
    ok: bool
    detail: str
    fix: str = ""


@dataclass
class PreflightReport:
    """The result of the team-mode preflight. `activatable` is True only when NO check
    blocks — at TM.S1 the connection-manager check always blocks, so it is always False."""

    checks: List[PreflightCheck] = field(default_factory=list)

    @property
    def blockers(self) -> List[PreflightCheck]:
        return [c for c in self.checks if not c.ok]

    @property
    def activatable(self) -> bool:
        return not self.blockers

    def render(self, *, ascii_only: bool = False) -> str:
        ok_glyph = "[ok]" if ascii_only else "✓"
        no_glyph = "[!]" if ascii_only else "✗"
        head = ("team mode: READY" if self.activatable
                else "team mode: NOT READY — activation refused (fail-closed)")
        lines = [head]
        for c in self.checks:
            g = ok_glyph if c.ok else no_glyph
            line = f"  {g} {c.name}: {c.detail}"
            if not c.ok and c.fix:
                line += f"  → {c.fix}"
            lines.append(line)
        # Fail-closed: every refused preflight links the canonical setup & operations page, so a
        # stuck user always has one place to go (the same link every team-mode error carries).
        if self.blockers:
            lines.append(f"  → {team_docs.team_docs_hint()}")
        return "\n".join(lines)


def _resolve_identity(surface: Any, identity: Optional[str]) -> str:
    """The run identity used to attribute team writes. TM.S1 reuses `session_bundle.py`'s
    existing run identity (the current user) until full session identity lands (0.0.13).
    An explicit `identity` overrides (for tests); degrade-clean to "" on any error."""
    if identity is not None:
        return identity
    try:
        from .session_bundle import _default_author
        return _default_author() or ""
    except Exception:
        return ""


def team_preflight(surface: Any, *, environ: Optional[Dict[str, str]] = None,
                   identity: Optional[str] = None, probe: Optional[Any] = None
                   ) -> PreflightReport:
    """The REAL fail-closed team-mode preflight (TM.S2).

    Team activates ONLY when every prerequisite passes: a usable run identity (reused from
    `session_bundle.py`), `$MOKATA_PG_DSN` present (doc 48 golden path), the shared DB
    reachable within the ≤500ms probe, AND its `mokata_schema_version` compatible. Each
    failure mode is fail-closed with its own NAMED fix; team mode is never half-activated,
    never a silent divergence.

    The DB probe is READ-ONLY (no DDL — provisioning is `mokata team init`, TM.S3).
    `environ`/`identity`/`probe` are injectable for tests; all default to the live process.
    """
    env = os.environ if environ is None else environ
    checks: List[PreflightCheck] = []

    # 1) run identity — reuse session_bundle's existing run identity (upgrades to full
    #    session identity in 0.0.13; don't block on it here).
    ident = _resolve_identity(surface, identity)
    checks.append(PreflightCheck(
        "run-identity", bool(ident),
        f"attributing team writes to '{ident}'" if ident
        else "no usable run identity resolved",
        fix="" if ident else "set $USER / $LOGNAME so writes carry a real author"))

    # 2) credentials — the env-var NAME must be present (the DSN secret is never stored).
    dsn = (env.get(CREDENTIAL_ENV) or "").strip()
    has_cred = bool(dsn)
    checks.append(PreflightCheck(
        "credentials", has_cred,
        f"${CREDENTIAL_ENV} is set" if has_cred else f"${CREDENTIAL_ENV} is not set",
        fix="" if has_cred
        else f"export {CREDENTIAL_ENV}=postgres://…  (the DSN secret is never stored)"))

    # 3) the shared DB — reachability + schema compatibility (the real TM.S2 probe). Only
    #    runs with a DSN to probe; without one, credentials (above) is the blocker.
    if has_cred:
        checks.extend(_db_checks(dsn, probe))

    return PreflightReport(checks)


# The pooler trap (doc 48 H1 / ADR-54): LISTEN/NOTIFY dies behind transaction-mode poolers, so
# an unreachable/handshake failure names it as the likely cause + the fix.
_POOLER_HINT = ("check the host is reachable and the DSN is correct; if it points at a "
                "transaction-mode POOLER (Supabase/Neon pooled port), LISTEN/NOTIFY dies there "
                "— use a DIRECT/session connection string")


def _db_checks(dsn: str, probe: Optional[Any]) -> List[PreflightCheck]:
    """Translate one shared-DB probe into the reachability + schema preflight checks. Every
    non-OK verdict names its own fix. Fail-closed: any probe error reads as not-activatable."""
    if probe is None:
        from .teamdb import probe as probe
    try:
        res = probe(dsn)
    except Exception as exc:  # pragma: no cover - the probe is itself fail-closed
        return [PreflightCheck("shared-database", False,
                               f"probe failed: {exc}",
                               fix=_POOLER_HINT)]

    # driver absent — team mode needs the optional Postgres extra.
    if not getattr(res, "driver_present", True):
        return [PreflightCheck(
            "shared-database", False,
            "the Postgres driver (psycopg) is not installed",
            fix="install the driver: `pip install 'mokata[postgres]'`")]

    # unreachable / timeout — name reachability + the pooler trap.
    if not res.reachable:
        return [PreflightCheck("shared-database", False,
                               res.detail or "unreachable", fix=_POOLER_HINT)]

    db_ok = PreflightCheck("shared-database", True,
                           f"reachable ({res.elapsed_ms:.0f}ms round-trip)")

    # reachable, but the schema isn't provisioned → run `team init` (TM.S3).
    if not res.schema_present or res.schema_version is None:
        return [db_ok, PreflightCheck(
            "team-schema", False,
            res.detail or "shared schema not provisioned",
            fix="run `mokata team init` to provision the shared schema (TM.S3)")]

    # reachable + provisioned, but an incompatible version.
    if not res.compatible:
        from .teamdb import TEAM_SCHEMA_VERSION
        return [db_ok, PreflightCheck(
            "team-schema", False,
            f"schema version {res.schema_version} is incompatible "
            f"(this mokata speaks v{TEAM_SCHEMA_VERSION})",
            fix="upgrade mokata, or re-run `mokata team init` to migrate the shared schema")]

    return [db_ok, PreflightCheck(
        "team-schema", True, f"schema v{res.schema_version} compatible")]


# --------------------------------------------------------------- activation (reach CONNECTED)
@dataclass
class ActivateResult:
    """The verdict of trying to reach CONNECTED + activate team mode. `connected` is the DB
    verdict (reachable + compatible + a usable identity — the TM.S2 preflight passed); `activated`
    is True only once the durable `settings.mode=team` write commits. A fail-closed refusal has
    both False and left the manifest untouched."""

    connected: bool
    activated: bool
    aborted: bool
    report: PreflightReport
    message: str = ""


def activate_team(root: str, surface: Any, *, environ: Optional[Dict[str, str]] = None,
                  assume_yes: bool = False, confirm: Optional[Callable[[str], bool]] = None,
                  identity: Optional[str] = None, out: Optional[Callable[[str], None]] = None,
                  ledger: Any = None, probe: Optional[Any] = None) -> ActivateResult:
    """Reach CONNECTED then activate team mode — the ONE fail-closed primitive `mokata mode set
    team` and the joiner (`team join`) share.

    Runs the TM.S2 preflight. On a reachable + compatible DB it persists `settings.mode=team`
    through the human-gated `config set` path (secret-scan + approval + audit). On ANY fail-closed
    verdict (missing identity/DSN, unreachable/pooler-trap, schema absent, incompatible version) it
    writes NOTHING and surfaces the S2 named fix — never a half-activation, never a silent write.
    Every refusal links the canonical setup & operations page. The probe is injectable for tests.
    """
    emit = out or (lambda *_a: None)
    report = team_preflight(surface, environ=environ, identity=identity, probe=probe)
    if not report.activatable:
        msg = team_docs.with_docs("team mode: NOT CONNECTED — activation refused (fail-closed); "
                                  "team prerequisites are not satisfied.")
        emit(msg)
        emit(report.render())
        return ActivateResult(False, False, False, report, msg)

    # CONNECTED — the DB is reachable + compatible. Persist the mode switch (human-gated).
    from . import config_cmd
    res = config_cmd.config_set(root, f"settings.{MODE_SETTING}", TEAM, assume_yes=assume_yes,
                                confirm=confirm, ledger=ledger, out=emit)
    if res.committed:
        msg = ("team mode: CONNECTED + activated — shared memory/knowledge/events now use the "
               "shared database.")
        emit(msg)
        return ActivateResult(True, True, False, report, msg)
    # Green DB, but the human declined (or the gate blocked) the durable mode write.
    msg = team_docs.with_docs(
        getattr(res, "reason", None) or "team mode not activated (mode write declined).")
    emit(msg)
    return ActivateResult(True, False, getattr(res, "aborted", False), report, msg)


# --------------------------------------------------------------------------- the surface
def mode_badge(surface: Any, *, ascii_only: bool = False) -> str:
    """The compact statusline fragment for the current mode, e.g. `local` / `team` / `team ⚠`.

    TM.S5 (doc 48 P-11): in team mode the badge appends the health warning glyph whenever the
    LAST CACHED verdict is troubled (unreachable/degraded) — a broken connection is never silent.
    Read-only + hot-path safe: it reads the cached verdict and NEVER probes (the badge can't
    hang and can't fabricate a warning that wasn't observed). Degrade-clean → `local`."""
    mode = read_mode(surface)
    try:
        from . import team_health
        return mode + team_health.cached_or_neutral(surface).badge_suffix(ascii_only=ascii_only)
    except Exception:
        return mode


def mode_line(surface: Any) -> str:
    """The ONE-LINE mode surface for the SessionStart bootstrap, the CLI banner, and doctor
    — never more than a line (bootstrap token budget). Names the mode + a terse descriptor.
    Degrade-clean: any error falls back to the local default, never raises."""
    mode = read_mode(surface)
    if mode == TEAM:
        return "Run mode: team — shared memory/knowledge/events on the shared database"
    return "Run mode: local — zero-config; everything under .mokata/ (the default)"
