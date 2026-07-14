"""CM.S2 — LOUD team-mode degraded reads (C-2).

The bug this closes: in team mode, when the shared Postgres is unreachable/unconfigured, a
memory READ degraded to the local SQLite floor SILENTLY — the user believed they were reading
shared team memory but were not. Worse, "what mode am I in" and "which backend serves this
read" were computed from DIFFERENT inputs in DIFFERENT places (memory selection ran its own
connect; `team_health` ran its own probe), so they could DISAGREE (health CONNECTED while a
read served local, or vice versa).

This module is the ONE decision point: mode + read-backend derive from the SAME resolved input
— CM.S1's DSN resolver (`dsn.py`) for the env-var NAME and the SAME cached ≤500ms probe (E2)
`team_health.check` runs for the badge/doctor. `served_by_team` is TRUE iff the health verdict
is OK, so the probe that says CONNECTED is literally the same input that routes the read — the
two can never diverge. When a team-mode read is served from the fallback it is LOUDLY marked
degraded, naming the resolved env-var NAME (never its VALUE) and the failure class.

Scope (CM.S2 only): make the existing fallback HONEST. Reads themselves are unchanged (local-
first P8 still serves them); no second probe, no background process, no schema change.

D5 (0.0.13) — the taxonomy this module's CM.S2 docstring anticipated has now LANDED, here, because
this module already owned the failure-class vocabulary and a second vocabulary would have been the
bug wearing a different hat. Three things were added and nothing was taken away:

  * three more failure classes (`local-io`, `corrupt`, `engine-unavailable`) — each backed by a
    real raiser the sweep actually found, never a word invented for symmetry;
  * `CapabilityDegradeNotice` + `note_degraded()` — the same loud, once-per-subsystem notice for
    the degrades that are NOT about team memory (a code graph that fell to grep, a secret-scanner
    that could not import). It follows MS.S4's `WalDegradeNotice` precedent rather than bending
    the team notice into saying something untrue;
  * `emitted_notices()` — the notices are now REMEMBERED, not just printed, so `mokata doctor` can
    answer "what degraded this session?" after the line has scrolled away.

The companion is `errors.py`: `MokataError` + the `DegradedCapability` family, whose members carry
a `failure_class` drawn from THIS module's vocabulary — so an exception, a notice and a doctor line
all say the same word for the same failure.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

# The shared/team memory backend id — the only tool whose team-mode read can degrade to the
# LOCAL floor (SQLite/Obsidian are already per-repo, so there is nothing shared to fall back
# from). Kept as a name so a call site can't typo the literal.
SHARED_TOOL = "postgres"

# The failure classes a team-mode degrade falls into (doc 48 E2 / the health verdict). Named so
# the badge/doctor can say WHY (unset var vs unreachable vs schema) — never a bare "degraded".
FAILURE_UNSET = "unset"            # the resolved DSN env var is not set
FAILURE_UNREACHABLE = "unreachable"  # set, but the shared DB is unreachable / driver-absent
FAILURE_SCHEMA = "schema"          # reachable, but the shared schema is absent/incompatible

# MS.S4 — the first LOCAL-mode failure class: the filesystem under the local SQLite store cannot
# enable WAL (a network mount that can't back the -shm index), so the store falls back to its
# previous journal mode. Team mode is not involved and no DSN exists, so a notice in this class
# carries no `env_name`. It lives here because this module owns the failure-class vocabulary —
# one place, or the surfaces start inventing their own words for "degraded".
FAILURE_WAL = "wal-unavailable"

# D5 — the three classes the SWEEP found the rest of the codebase actually produces. Each is
# backed by a REAL raiser (nothing invented): a class with no raiser is a word doctor can print
# and no failure can ever earn.
#
#   LOCAL_IO   a local mokata artifact could not be READ at all — `OSError` opening the write
#              journal / `sqlite3.DatabaseError` on the SQLite floor (a locked or permission-
#              broken `.mokata/temp_local/`). Local-mode, like FAILURE_WAL: carries no env_name.
#   CORRUPT    the artifact is PRESENT but unparseable — `json.JSONDecodeError` on an audit-ledger
#              line or a persisted spec (a torn append, a version skew). Distinct from LOCAL_IO
#              because the remediation is different: restore/re-emit the file, not fix the disk.
#   ENGINE     mokata's OWN engine could not be imported — `ImportError` from a hook's lazy import
#              of `mokata.govern`. A half-installed mokata, whose remediation is a reinstall.
FAILURE_LOCAL_IO = "local-io"
FAILURE_CORRUPT = "corrupt"
FAILURE_ENGINE = "engine-unavailable"

# D6 — a mokata ARTIFACT (a memory doc) declares a schema version this build does not speak: a
# teammate on a NEWER mokata wrote it. Coined for the same reason D5 coined FAILURE_WAL — not for
# symmetry, but because the nearest existing class renders a FALSE sentence. FAILURE_SCHEMA is the
# SHARED-DB schema ("not provisioned / incompatible — run `mokata team init`"), and every word of
# that is wrong here: nothing is unprovisioned, the database is irrelevant (this fires on a purely
# local SQLite store too), and `team init` would never fix it. The only true remediation is to
# upgrade the CLIENT. Its raiser is `memory/item.py:MemoryDocTooNew`; the caller does NOT degrade
# to a floor — it REFUSES the write, because the only floor on offer ("write it anyway, minus the
# fields you didn't understand") is precisely the bug.
FAILURE_DOC_SCHEMA = "doc-schema"

_CLASS_LABEL = {
    FAILURE_UNSET: "env var not set",
    FAILURE_UNREACHABLE: "shared database unreachable",
    FAILURE_SCHEMA: "shared schema not provisioned / incompatible",
    FAILURE_WAL: "filesystem cannot enable SQLite WAL",
    FAILURE_LOCAL_IO: "local mokata state could not be read",
    FAILURE_CORRUPT: "local mokata state is present but unreadable/corrupt",
    FAILURE_ENGINE: "the mokata engine could not be loaded",
    FAILURE_DOC_SCHEMA: "written by a NEWER mokata than this one",
}


# Every degrade says the same reassuring thing first — a degraded write is journaled, not lost —
# and then the ONE command that fixes THIS failure. The default assumes the connection is the
# problem, so the writes simply wait for it. D1 adds the class where that advice is actively
# WRONG: a schema failure means the connection is perfectly healthy and `mokata sync` would do
# nothing, forever. A notice may carry an exact `fix` (teamdb.schema_fix names which way the
# version is out of range — provision, upgrade the schema, or upgrade mokata).
_JOURNALED = "Writes are journaled and NOT lost; "
_DEFAULT_FIX = _JOURNALED + "run `mokata sync` once the connection is healthy."
_SCHEMA_FIX = _JOURNALED + "run `mokata team init` to provision/upgrade the shared schema."
_CLASS_FIX = {FAILURE_SCHEMA: _SCHEMA_FIX}


@dataclass
class DegradeNotice:
    """One team-mode degrade event — a read served from the LOCAL fallback instead of shared
    team memory. Carries the resolved env-var NAME (never the DSN VALUE, per CM.S1) and the
    failure class, so every surface renders the SAME loud, actionable, secret-free notice."""

    subsystem: str                 # e.g. "memory" — the once-per-subsystem notice key
    env_name: str                  # the resolved DSN env-var NAME (never the value)
    failure_class: str             # one of FAILURE_UNSET / _UNREACHABLE / _SCHEMA
    detail: str = ""               # the underlying verdict detail (already secret-free)
    fix: str = ""                  # D1 — the exact remediation; "" = the class default

    @property
    def class_label(self) -> str:
        return _CLASS_LABEL.get(self.failure_class, self.failure_class)

    @property
    def remediation(self) -> str:
        """The command that actually fixes THIS failure. A schema degrade must never be told to
        `mokata sync` — the connection is healthy; the schema is the problem (D1/D2)."""
        if self.fix:
            return f"{_JOURNALED}{self.fix.rstrip('.')}."
        return _CLASS_FIX.get(self.failure_class, _DEFAULT_FIX)

    def render(self, *, ascii_only: bool = False) -> str:
        """The ONE loud notice string — shared by the CLI print, the MCP marker text, and
        doctor. Names the failure class + the env-var NAME; never the DSN VALUE."""
        glyph = "[!]" if ascii_only else "⚠"
        return (f"{glyph} team {self.subsystem}: DEGRADED — served from the LOCAL fallback, "
                f"NOT shared team memory ({self.class_label}: ${self.env_name}). "
                f"{self.remediation}")

    def to_dict(self) -> dict:
        """The structured marker MCP read responses carry (data, not a repeated print)."""
        return {"degraded": True, "subsystem": self.subsystem, "env_name": self.env_name,
                "failure_class": self.failure_class, "detail": self.detail,
                "fix": self.remediation, "notice": self.render()}


@dataclass
class ReadRoutingDecision:
    """The ONE routing decision: given a surface, is this read served by shared team memory or
    the local fallback, and is that a (loud) degrade? `verdict` is the SAME cached E2 health
    verdict the badge/doctor render — `served_by_team == verdict.ok` (for the shared tool), so
    the input that says CONNECTED is the same input that routes the read: they cannot diverge."""

    mode: str                      # run_mode.LOCAL | run_mode.TEAM
    tool: str                      # the resolved memory-store tool id
    served_by_team: bool           # True iff the read is served by the shared backend
    degraded: bool                 # True iff a team-mode read fell back to LOCAL (loud)
    verdict: Any                   # the team_health.HealthVerdict (the shared E2 probe verdict)
    notice: Optional[DegradeNotice] = None
    env_name: str = ""             # the resolved DSN env-var NAME (never the value)

    def mark_degraded_from_build_failure(self, failure_class: str = FAILURE_UNREACHABLE,
                                         detail: str = "shared backend unavailable at read time",
                                         fix: str = "") -> None:
        """The residual-race honesty hook: health said OK but the live backend build still
        failed, so the read actually fell back. Flip to degraded so the store's surfaces stay
        honest — never a silent team→local fall-through. Idempotent.

        D1 — the class is now an ARGUMENT, not an assumption. A denied-CREATE / unprovisioned
        schema used to be reported as UNREACHABLE ("run `mokata sync`"), which is a lie: the
        connection is healthy and syncing would never fix it. The caller passes the class the
        typed failure carried, so the notice names the real cause and the real command."""
        if self.degraded:
            return
        self.served_by_team = False
        self.degraded = True
        self.notice = DegradeNotice(subsystem="memory", env_name=self.env_name,
                                    failure_class=failure_class, detail=detail, fix=fix)


def _resolve_memory_tool(surface: Any) -> str:
    """The memory-store tool the router resolves to — the SAME logic `select_memory_backend`
    uses (router.resolve('memory_store'), SQLite floor when unavailable). Degrade-clean."""
    from .manifest import ManifestError
    try:
        res = surface.router.resolve("memory_store")
        if res is not None and res.available and res.tool:
            return res.tool
    except (ManifestError, AttributeError):
        # ManifestError: `router.resolve` on an unknown capability. AttributeError: a surface with
        # no `.router`. The SQLite floor below is the same answer `select_memory_backend` gives, so
        # the routing decision this feeds stays consistent with the backend actually selected.
        pass
    return "sqlite"


def _failure_class(env_set: bool, verdict: Any) -> str:
    """Map (is the env var set?) + the health verdict to a failure class. Uses the SAME inputs
    the routing decision does — never a second derivation."""
    from . import team_health
    if not env_set:
        return FAILURE_UNSET
    if getattr(verdict, "state", None) == team_health.DEGRADED:
        return FAILURE_SCHEMA
    return FAILURE_UNREACHABLE


def failure_class(surface: Any, verdict: Any, *, environ: Optional[dict] = None) -> str:
    """The CM.S2 failure-class vocabulary for a health verdict — the SAME mapping the read-routing
    notice uses (`_failure_class`), exposed so CM.S4's flush-liveness surfacing records the class
    from the SAME cached verdict (no second probe, no re-derivation). Names the class only; never
    a DSN value."""
    from .dsn import resolve_dsn
    res = resolve_dsn(surface, environ=environ)
    return _failure_class(res.is_set, verdict)


def resolve_read_routing(surface: Any, *, tool: Optional[str] = None,
                         environ: Optional[dict] = None,
                         probe: Optional[Callable[[str], Any]] = None,
                         now: Optional[Callable[[], float]] = None,
                         force: bool = False) -> ReadRoutingDecision:
    """The ONE read-routing decision. Mode comes from `run_mode.read_mode`; the shared-backend
    verdict comes from the SAME cached ≤500ms `team_health.check` probe (E2) — no second probe.

    LOCAL mode (the default) → served locally, NEVER degraded, and NEVER probes (zero-network
    hot path, byte-identical to today). TEAM mode with the shared tool → the read is served by
    team iff the verdict is OK; otherwise it falls back to LOCAL and is marked degraded, naming
    the resolved env-var NAME + the failure class. Degrade-clean: any error reads as a safe
    local, non-degraded decision (never raises on the read path).

    `tool`/`environ`/`probe`/`now`/`force` are injectable for tests; all default to the live
    resolution + the shared cached probe."""
    from . import run_mode as _rm, team_health
    from .manifest import ManifestError
    try:
        mode = _rm.read_mode(surface)
    except (ManifestError, AttributeError, OSError):
        # `read_mode` is in fact TOTAL — `stored_mode` swallows everything and returns None, which
        # reads back as LOCAL — so this handler is belt-and-braces on the hottest read path in
        # mokata. It is narrowed rather than deleted (unlike `team_health.cached_or_neutral`'s twin,
        # which is gone) because THIS one is defensible in kind: a manifest/IO failure while
        # resolving the mode genuinely should route the read locally. It must never catch a typo.
        mode = _rm.LOCAL

    if mode != _rm.TEAM:
        # zero-config local: no probe, no network, never degraded.
        return ReadRoutingDecision(mode=_rm.LOCAL, tool=tool or "sqlite",
                                   served_by_team=False, degraded=False,
                                   verdict=team_health.HealthVerdict(team_health.LOCAL))

    resolved_tool = tool or _resolve_memory_tool(surface)
    try:
        verdict = team_health.check(surface, environ=environ, probe=probe, now=now, force=force)
    except Exception:
        verdict = team_health.HealthVerdict(team_health.OFFLINE, "health check failed")

    # The resolved DSN env-var NAME (never the value), via the ONE resolver (CM.S1) — and
    # whether it is set, using the SAME environ the probe saw, so the class can't disagree.
    from .dsn import resolve_dsn
    res = resolve_dsn(surface, environ=environ)
    env_name = res.env_name

    is_shared = resolved_tool == SHARED_TOOL
    served_by_team = is_shared and verdict.ok           # ← same input as the badge: cannot diverge
    degraded = is_shared and verdict.trouble
    notice = None
    if degraded:
        notice = DegradeNotice(subsystem="memory", env_name=env_name,
                               failure_class=_failure_class(res.is_set, verdict),
                               detail=verdict.detail)
    return ReadRoutingDecision(mode=_rm.TEAM, tool=resolved_tool,
                               served_by_team=served_by_team, degraded=degraded,
                               verdict=verdict, notice=notice, env_name=env_name)


@dataclass
class CapabilityDegradeNotice(DegradeNotice):
    """D5 — a degrade that is NOT about team memory: a mokata CAPABILITY fell back to a documented
    floor (the code graph to grep, recall to lexical, a gate to not running at all).

    Why a subclass and not a wider `DegradeNotice`: the CM.S2 notice hard-words the team story —
    "served from the LOCAL fallback, NOT shared team memory (...: $VAR)" — and for a ledger that
    cannot be parsed or a secret-scanner that cannot import, that sentence is simply false. This is
    the MS.S4 `WalDegradeNotice` pattern (a local-capability degrade rendering its OWN truth in the
    CM.S2 SHAPE), generalized once so the other subsystems don't each invent a notice.

    `env_name` is empty by construction (no DSN is involved) and no PATH is ever interpolated —
    the notice names the subsystem, the fallback and the class, so it cannot leak a directory
    layout (the CM.S1 secret-safety rule).
    """

    fallback: str = ""              # what the caller fell back TO, in the user's words

    @property
    def class_label(self) -> str:
        """The class in NEUTRAL words. CM.S2's labels are team-flavoured — FAILURE_UNREACHABLE
        reads "shared database unreachable" — which is true for a memory read served from the local
        floor and FALSE for a code graph that fell back to grep (there is no shared database in
        that story at all). Rendering the team label on a capability degrade would reintroduce, in
        the notice itself, the exact bug D5 exists to kill: a loud message saying something untrue.
        Classes with no team flavour (`local-io`, `corrupt`, `engine-unavailable`) inherit the base
        label unchanged — there is only one honest way to say those."""
        return _CAPABILITY_LABEL.get(self.failure_class) or super().class_label

    @property
    def remediation(self) -> str:
        """The fix, with NO "writes are journaled and NOT lost" preamble. That reassurance is a
        claim about the TEAM WRITE PATH; on a corrupt ledger or an unimportable engine it is not
        merely irrelevant, it is untrue — and a false reassurance is exactly the D5 bug."""
        if self.fix:
            return f"{self.fix.rstrip('.')}."
        return _CAPABILITY_FIX.get(self.failure_class, "Run `mokata doctor`.")

    def render(self, *, ascii_only: bool = False) -> str:
        glyph = "[!]" if ascii_only else "⚠"
        fell_back = f" — {self.fallback.rstrip('.')}" if self.fallback else ""
        return (f"{glyph} {self.subsystem}: DEGRADED{fell_back} "
                f"({self.class_label}). {self.remediation}")


# The three team-flavoured classes, said NEUTRALLY — for a degrade that is not about team memory.
# Only these three need a re-wording; `local-io` / `corrupt` / `engine-unavailable` were coined by
# D5 for exactly these subsystems and already read true in both shapes.
_CAPABILITY_LABEL = {
    FAILURE_UNREACHABLE: "the backend is unreachable",
    FAILURE_UNSET: "not configured",
    FAILURE_SCHEMA: "schema not provisioned / incompatible",
}

# The class-default remediation for a capability degrade (used only when a site names no exact
# `fix`). Every D5 site SHOULD name its own — these are the honest floor, not an excuse.
_CAPABILITY_FIX = {
    FAILURE_LOCAL_IO: "Check permissions/disk under `.mokata/`, then run `mokata doctor`.",
    FAILURE_CORRUPT: "Restore or regenerate the file, then run `mokata doctor`.",
    FAILURE_ENGINE: "Reinstall mokata (`pip install -U mokata`), then run `mokata doctor`.",
    # D6 — the ONLY remediation: this build cannot be taught to read a doc it predates.
    FAILURE_DOC_SCHEMA: "Upgrade mokata (`pip install -U mokata`) to write it.",
}


# --------------------------------------------------------------------- once-per-subsystem notice
# Process-lifetime set of subsystems whose degrade notice has already been printed, so the CLI
# prints ONE loud notice per subsystem — not one per read call (a briefing/loop must not spam).
_EMITTED: Set[str] = set()

# D5 — the notices themselves, keyed by subsystem, for the session. `_EMITTED` only ever remembered
# that a subsystem HAD spoken, which is enough to suppress a repeat and useless to anyone asking
# "what degraded?". Doctor needs the notice, not the fact of it: a degrade the user scrolled past
# an hour ago is exactly the one they need on demand (P16 — a degrade you can't see is a lie in
# the proof; P17 — recovery starts with knowing something broke).
_NOTICES: Dict[str, DegradeNotice] = {}


def emit_degrade_notice(notice: DegradeNotice, out: Callable[[str], None] = print, *,
                        seen: Optional[Set[str]] = None, ascii_only: bool = False) -> bool:
    """Print `notice` LOUDLY, but only ONCE per subsystem for this process (keyed by
    `notice.subsystem`). Returns True when it actually printed, False when suppressed as a
    repeat. `seen` overrides the module set (for tests / an isolated subsystem group).

    D5: when the MODULE store is in play (`seen is None`) the notice is also RECORDED, so
    `emitted_notices()` can answer "what degraded this session?" long after the line scrolled
    away. An injected `seen` is an isolated group (a test, a subsystem-local set) and is NOT
    recorded — otherwise a test's forced failure would show up in a real doctor run."""
    store = _EMITTED if seen is None else seen
    if notice.subsystem in store:
        return False
    store.add(notice.subsystem)
    if seen is None:
        _NOTICES[notice.subsystem] = notice
    out(notice.render(ascii_only=ascii_only))
    return True


def note_degraded(subsystem: str, failure_class: str, *, fallback: str = "", fix: str = "",
                  detail: str = "", out: Optional[Callable[[str], None]] = None,
                  seen: Optional[Set[str]] = None) -> bool:
    """THE D5 one-liner: a capability degraded — say so ONCE, loudly, in the CM.S2 shape, and
    record it for doctor. Returns True when it actually printed.

    This is the ONLY thing a swallowing site needs to add. It does not change what the caller does
    next: the fallback still falls back. It just stops being a secret."""
    notice = CapabilityDegradeNotice(subsystem=subsystem, env_name="",
                                     failure_class=failure_class, detail=detail, fix=fix,
                                     fallback=fallback)
    return emit_degrade_notice(notice, out or _stderr, seen=seen)


def _stderr(message: str) -> None:
    """Degrade notices go to STDERR: they are diagnostics, and a notice printed to stdout would
    corrupt the JSON/-quiet output of any command a script is parsing."""
    print(message, file=sys.stderr)


def emitted_notices() -> List[DegradeNotice]:
    """Every degrade notice this process has emitted, one per subsystem, in emission order.

    THE ANSWER TO "what silently degraded?" — which, before D5, had no answer at all: the notice
    printed once into a scrollback and was gone. Doctor renders this (`govern/doctor.py`), so the
    question has ONE place to be asked."""
    return list(_NOTICES.values())


def reset_degrade_notices(seen: Optional[Set[str]] = None) -> None:
    """Clear the once-per-subsystem memory (test hook; a fresh process starts empty anyway)."""
    (seen if seen is not None else _EMITTED).clear()
    if seen is None:
        _NOTICES.clear()
