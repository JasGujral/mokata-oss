"""TM.S5 — ONE health surface (doc 48 P-11 / E2).

Team mode's connection state is a SINGLE cached probe verdict, rendered identically everywhere:
the statusline badge (⚠ on trouble), `mokata mode`/status, `mokata doctor`, and the in-chat
briefing all read the SAME cached `HealthVerdict`. A broken/unreachable connection is ALWAYS
highlighted — never silent — and every failure surfaces the explicit work-locally offer
(offline never blocks; `mokata sync` reconciles later).

The probe is bounded and LAZY: `check()` re-uses a cached verdict until it goes stale
(`CACHE_TTL_S`), then re-runs the ≤500ms TM.S2 probe once and re-caches. The hot path
(`cached_or_neutral`) NEVER probes — it reads the last cached verdict so the statusline can't
hang and can't fabricate a warning that hasn't been observed.

Local mode is untouched: `check()` short-circuits to a `local` verdict with NO probe and NO
network (zero-config stays byte-for-byte the default).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import TEMP_LOCAL_DIRNAME, run_mode as _rm, team_docs

# The health states. `local`/`healthy` are OK; `degraded`/`offline` are TROUBLE (always
# highlighted). `unknown` = team mode, but nothing probed yet this session (no fabricated ⚠).
LOCAL = "local"
HEALTHY = "healthy"
DEGRADED = "degraded"      # reachable, but the shared schema is absent/incompatible
OFFLINE = "offline"        # unreachable / timeout / driver-absent / no DSN
UNKNOWN = "unknown"        # team mode, not yet checked

_OK_STATES = (LOCAL, HEALTHY)
_TROUBLE_STATES = (DEGRADED, OFFLINE)

# doc 48 E2 — the verdict is cached and lazily re-checked. A cached verdict older than this is
# re-probed once before the next dependent op (bounded freshness, no per-op network cost).
CACHE_TTL_S = 30.0
CACHE_FILENAME = "team_health.json"

# The one-line label per state, shared by every surface so the wording never diverges.
_LABELS = {LOCAL: "LOCAL", HEALTHY: "HEALTHY", DEGRADED: "DEGRADED",
           OFFLINE: "OFFLINE", UNKNOWN: "UNKNOWN"}


@dataclass
class HealthVerdict:
    """One team-connection verdict — the single source every surface renders. `checked_at` is
    a wall-clock stamp (seconds) used only for cache-staleness, never for security."""

    state: str
    detail: str = ""
    elapsed_ms: float = 0.0
    schema_version: Optional[int] = None
    checked_at: float = 0.0

    @property
    def ok(self) -> bool:
        return self.state in _OK_STATES

    @property
    def trouble(self) -> bool:
        return self.state in _TROUBLE_STATES

    @property
    def label(self) -> str:
        return _LABELS.get(self.state, self.state.upper())

    def badge_suffix(self, *, ascii_only: bool = False) -> str:
        """The statusline suffix: a warning glyph ONLY on trouble, else empty. A never-silent
        signal that costs one glyph — and never a fabricated warning (unknown/ok → "")."""
        if not self.trouble:
            return ""
        return " [!]" if ascii_only else " ⚠"

    def to_dict(self) -> dict:
        return {"state": self.state, "detail": self.detail, "elapsed_ms": self.elapsed_ms,
                "schema_version": self.schema_version, "checked_at": self.checked_at}

    @classmethod
    def from_dict(cls, d: dict) -> "HealthVerdict":
        return cls(state=str(d.get("state", UNKNOWN)), detail=str(d.get("detail", "")),
                   elapsed_ms=float(d.get("elapsed_ms", 0.0) or 0.0),
                   schema_version=d.get("schema_version"),
                   checked_at=float(d.get("checked_at", 0.0) or 0.0))


# --------------------------------------------------------------------------- classification
def classify(res: Any) -> "tuple[str, str]":
    """Map one TM.S2 `ProbeResult` to (state, detail). Fail-closed: anything short of
    reachable-AND-compatible is TROUBLE (never silently treated as OK)."""
    if not getattr(res, "driver_present", True):
        return OFFLINE, getattr(res, "detail", "") or "the Postgres driver (psycopg) is not installed"
    if not getattr(res, "reachable", False):
        return OFFLINE, getattr(res, "detail", "") or "unreachable — could not connect / round-trip"
    if not getattr(res, "schema_present", False) or getattr(res, "schema_version", None) is None:
        return DEGRADED, getattr(res, "detail", "") or "reachable, but the shared schema is not provisioned"
    if not getattr(res, "compatible", False):
        return DEGRADED, getattr(res, "detail", "") or "reachable, but the shared schema version is incompatible"
    return HEALTHY, getattr(res, "detail", "") or f"reachable ({getattr(res, 'elapsed_ms', 0.0):.0f}ms)"


# ------------------------------------------------------------------------------ the cache
def _cache_path(surface: Any) -> Optional[str]:
    try:
        return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, CACHE_FILENAME)
    except Exception:  # pragma: no cover - a broken surface has no cache path
        return None


def load_cached(surface: Any) -> Optional[HealthVerdict]:
    """The last stored verdict, or None. Degrade-clean: a missing/corrupt cache reads as None,
    never an exception (the caller falls back to a fresh probe or a neutral verdict)."""
    path = _cache_path(surface)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return HealthVerdict.from_dict(json.load(fh))
    except Exception:
        return None


def store(surface: Any, verdict: HealthVerdict) -> None:
    """Persist `verdict` so every surface (including the separate statusline process) reads the
    SAME state. Best-effort: a write failure never breaks the caller (health is observability)."""
    path = _cache_path(surface)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(verdict.to_dict(), fh)
    except Exception:  # pragma: no cover - best-effort persistence
        pass


# --------------------------------------------------------------------------- the check
def _default_probe(dsn: str) -> Any:
    from .teamdb import probe as _probe
    return _probe(dsn)


def check(surface: Any, *, environ: Optional[dict] = None,
          probe: Optional[Callable[[str], Any]] = None,
          now: Optional[Callable[[], float]] = None,
          max_age_s: float = CACHE_TTL_S, force: bool = False) -> HealthVerdict:
    """The bounded, lazily-cached team-health check — the ONE call every dependent op makes.

    Local mode returns a `local` verdict and NEVER probes. Team mode reuses a fresh cached
    verdict (within `max_age_s`) or re-runs the ≤500ms TM.S2 probe once and re-caches. Any
    failure (no DSN, unreachable, probe error) is fail-closed to OFFLINE/DEGRADED with a
    detail — never silent. `probe`/`environ`/`now` are injectable for tests."""
    import time as _time
    clock = now or _time.time
    if _rm.read_mode(surface) != _rm.TEAM:
        return HealthVerdict(LOCAL, "local mode — zero-config; no shared connection",
                             checked_at=clock())

    t = clock()
    if not force:
        cached = load_cached(surface)
        if cached is not None and (t - cached.checked_at) < max_age_s:
            return cached

    env = os.environ if environ is None else environ
    dsn = (env.get(_rm.CREDENTIAL_ENV) or "").strip()
    if not dsn:
        v = HealthVerdict(OFFLINE,
                          f"${_rm.CREDENTIAL_ENV} is not set — team mode has no shared connection",
                          checked_at=t)
        store(surface, v)
        return v

    pf = probe or _default_probe
    try:
        res = pf(dsn)
    except Exception as exc:  # the probe is itself fail-closed, but never trust it to be
        v = HealthVerdict(OFFLINE, f"probe failed: {exc}", checked_at=t)
        store(surface, v)
        return v

    state, detail = classify(res)
    v = HealthVerdict(state, detail, elapsed_ms=float(getattr(res, "elapsed_ms", 0.0) or 0.0),
                      schema_version=getattr(res, "schema_version", None), checked_at=t)
    store(surface, v)
    return v


def cached_or_neutral(surface: Any) -> HealthVerdict:
    """The HOT-PATH verdict for the statusline badge — reads the last cached verdict and NEVER
    probes (so the badge can't hang). Local → `local`; team with a cache → that verdict (⚠ if
    troubled, persisting a real observation); team with no cache yet → `unknown` (no ⚠)."""
    try:
        if _rm.read_mode(surface) != _rm.TEAM:
            return HealthVerdict(LOCAL)
    except Exception:
        return HealthVerdict(LOCAL)
    cached = load_cached(surface)
    if cached is not None:
        return cached
    return HealthVerdict(UNKNOWN, "team — connection not yet checked this session")


# --------------------------------------------------------------------------- rendering
def work_locally_offer(*, ascii_only: bool = False) -> str:
    """The explicit work-locally offer shown on trouble — offline never blocks, and nothing is
    lost: writes journal locally and `mokata sync` reconciles (through the human gate) later."""
    return ("you can keep working locally — team writes are journaled and NOT lost; "
            "run `mokata sync` to flush + reconcile once the connection is healthy")


def summary_line(verdict: HealthVerdict, *, ascii_only: bool = False) -> str:
    """The ONE-LINE health summary shared by `mode`, doctor and the in-chat briefing."""
    glyph = ""
    if verdict.trouble:
        glyph = "[!] " if ascii_only else "⚠ "
    return f"{glyph}Team connection: {verdict.label} — {verdict.detail}".rstrip(" —")


def status_block(verdict: HealthVerdict, *, ascii_only: bool = False) -> str:
    """The multi-line health block for `mokata mode`/status, doctor, and in-chat. On trouble it
    ALWAYS appends the work-locally offer + the canonical setup & operations link, so a broken
    connection is never a dead end. In local mode it renders nothing team-specific."""
    if verdict.state == LOCAL:
        return "Team connection: n/a (local mode)"
    lines = [summary_line(verdict, ascii_only=ascii_only)]
    if verdict.trouble:
        lines.append(f"  → {work_locally_offer(ascii_only=ascii_only)}")
        lines.append(f"  → {team_docs.team_docs_hint()}")
    return "\n".join(lines)
