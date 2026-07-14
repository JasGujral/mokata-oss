"""CM.S1 — the ONE DSN resolver (C-1).

Single source of truth for the shared-DB DSN env-var NAME. Before this, memory READS honored
the team-connect-configured `tools.postgres.config.dsn_env` while team HEALTH / FLUSH / SYNC /
preflight were hardwired to the literal default name — so a team on `team connect --dsn-env
CUSTOM` read fine, but health reported OFFLINE and the flush loop skipped every write forever
(C-1, silent data loss). Every subsystem now obtains the env-var NAME here, so reads and writes
can never resolve different vars again.

Contract (doc 85 §1/§6 regression guards):
  * DSN env-var-NAME ONLY — the DSN VALUE is never persisted or logged. This module reads the
    value from the environment at point of use and returns it in a transient result; it never
    stores it. `require_dsn` surfaces the env-var NAME (never the value) when the var is unset.
  * ONE persisted source of truth for the NAME: `settings.team.dsn_env` (the pointer `team
    connect` writes) is canonical, with `tools.postgres.config.dsn_env` (the memory tool-config
    wiring `team connect` keeps in lock-step, and older/adopted manifests) as the fallback, then
    the default. `resolve_dsn_env` is the ONLY reader of these fields for the purpose of naming.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from .errors import DegradedCapability
from .degrade import FAILURE_UNSET

# The default shared-DB DSN env-var NAME (doc 48 golden path). This is the ONLY definition of
# the literal in the whole codebase — every other module imports it from here so a custom name
# can never split one subsystem from another (C-1). NEVER re-inline this string elsewhere.
DEFAULT_DSN_ENV = "MOKATA_PG_DSN"

# Where the team-connect pointer lives (settings.team.dsn_env) — the canonical persisted NAME.
_TEAM_SETTINGS_KEY = "team"


class DsnUnset(DegradedCapability):
    """Raised when the resolved DSN env var is unset. Carries the env-var NAME (never a value)
    so a caller can surface an actionable, secret-free fix (per the MokataError direction)."""

    failure_class = FAILURE_UNSET

    def __init__(self, env_name: str) -> None:
        self.env_name = env_name
        super().__init__(f"${env_name} is not set")


@dataclass
class DsnResult:
    """The resolved DSN env-var NAME and (optionally) its VALUE read from the environment.
    `dsn` is None when the env var is unset — the caller degrades clean. The value is NEVER
    persisted or logged; it lives only in this transient result."""

    env_name: str
    dsn: Optional[str] = None

    @property
    def is_set(self) -> bool:
        return bool(self.dsn)


def _manifest_data(surface_or_data: Any) -> Dict[str, Any]:
    """The manifest dict from a Surface, a raw manifest dict, or None. Degrade-clean: any
    broken/absent surface reads as an empty manifest (→ the default env name), never raises."""
    if surface_or_data is None:
        return {}
    if isinstance(surface_or_data, dict):
        return surface_or_data
    try:
        return surface_or_data.manifest.data or {}
    except Exception:
        return {}


def _persisted_dsn_env(data: Dict[str, Any]) -> Optional[str]:
    """The persisted env-var NAME, canonical field first (settings.team.dsn_env), then the
    memory tool-config wiring (tools.postgres.config.dsn_env). None when neither is set."""
    settings = data.get("settings") or {}
    team = (settings.get(_TEAM_SETTINGS_KEY) or {}).get("dsn_env")
    if team:
        return team
    cfg = ((data.get("tools") or {}).get("postgres") or {}).get("config") or {}
    return cfg.get("dsn_env") or None


def resolve_dsn_env(surface_or_data: Any = None, *, override: Optional[str] = None) -> str:
    """The ONE env-var NAME the shared-DB DSN is read from. Precedence: an explicit per-call
    `override` (rare) → the team-connect-configured NAME persisted in the manifest → the
    default. Returns only the NAME (never a value) and never raises (degrade-clean → default)."""
    if override:
        return override
    return _persisted_dsn_env(_manifest_data(surface_or_data)) or DEFAULT_DSN_ENV


def resolve_dsn(surface_or_data: Any = None, *, override: Optional[str] = None,
                environ: Optional[Dict[str, str]] = None) -> DsnResult:
    """Resolve the shared-DB env-var NAME (via `resolve_dsn_env`) and read its VALUE from
    `environ` (the live process by default). The value is transient — never persisted or logged.
    `dsn` is None when unset; the caller degrades clean and surfaces `env_name` (never a value)."""
    env = os.environ if environ is None else environ
    name = resolve_dsn_env(surface_or_data, override=override)
    val = (env.get(name) or "").strip() or None
    return DsnResult(name, val)


def require_dsn(surface_or_data: Any = None, *, override: Optional[str] = None,
                environ: Optional[Dict[str, str]] = None) -> DsnResult:
    """Like `resolve_dsn`, but raises `DsnUnset(env_name)` when the env var is unset — for the
    fail-closed paths that must stop with the env-var NAME in the error (never the value)."""
    res = resolve_dsn(surface_or_data, override=override, environ=environ)
    if not res.is_set:
        raise DsnUnset(res.env_name)
    return res
