"""Stage 71 — team audit / shared activity log (shared OR local, conflict-free, NO telemetry).

The existing I3 `AuditLedger` (append-only local JSONL) stays each dev's default. This module lets
a TEAM optionally publish those SAME audit entries to the team's OWN managed Postgres (Stage 69's
bring-your-own DB — an env-var DSN) so everyone can see who-did-what across the governed brain —
WITHOUT anything ever being phoned home to mokata/Anthropic. The data is the team's, on the team's
storage, full stop.

Design (REUSES the ledger + the shared transport, never rebuilds):
  * READS the local ledger (`govern.ledger.AuditLedger`) + its `why_timeline` — unchanged.
  * The SHARED backend is a mokata-OWNED, namespaced Postgres table reached by an env-var DSN,
    mirroring `session_transport.PostgresTransport` / `memory._pg.connect_psycopg`. It is
    APPEND-ONLY (INSERT only — never UPDATE/DELETE), carries PER-ACTOR attribution, and is
    NAMESPACED per repo, so two teammates writing concurrently each get their own rows (a
    `BIGSERIAL id`) and NEVER clobber each other — conflict-free by construction.
  * Sharing is OPT-IN (`settings.audit.shared`) + LOCAL-FIRST (local is the default). Publishing
    — the only moment data leaves the machine — is HUMAN-GATED + SECRET-SCANNED through the
    universal `WriteGate` (kind `send`, the egress rule). The DSN secret is NEVER stored: only the
    env-var NAME lives in the manifest, exactly like `team_connect`.
  * Degrade-clean: no `psycopg` / no DSN → a clear `SharedAuditUnavailable`, stays local, never
    crashes, never silently downgrades.

NO TELEMETRY. There is NO mokata/Anthropic endpoint anywhere in this module — the ONLY network
target is the team's own DSN, read from the environment.

`psycopg` is an optional extra; the core stays dependency-free. Clean-room. Copyright 2026
MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from . import team_docs
from .govern.gate import WriteGate, WriteRequest
from .govern.ledger import AuditLedger
from .govern.secrets import Finding

# The env vars the shared audit log reads its DSN from (never inline in the committed manifest,
# exactly like the shared-memory + session backends). The audit-specific override var wins; then
# the CONFIGURED team DSN name (via the ONE resolver — so `team connect --dsn-env CUSTOM` reaches
# audit too, C-1); then the literal default (which `resolve_dsn_env` supplies). The default NAME
# lives only in `dsn.py`.
from .dsn import DEFAULT_DSN_ENV  # noqa: E402  (the single source of the default env-var name)
from .errors import DegradedCapability

# The audit subsystem's own override env var (wins over the team/default names when set).
AUDIT_OVERRIDE_ENV = "MOKATA_AUDIT_PG_DSN"
# Kept for the degrade-clean "needs a DSN in $X / $Y" hint (the two well-known names).
PG_DSN_ENVS = (AUDIT_OVERRIDE_ENV, DEFAULT_DSN_ENV)
PG_TABLE = "mokata_audit_log"                # mokata-OWNED, namespaced (never a generic name)

_AUDIT_SETTINGS_KEY = "audit"               # settings.audit.{shared, dsn_env}


class SharedAuditUnavailable(DegradedCapability):
    """Raised when the shared audit log can't be built — e.g. psycopg missing or no DSN. The
    caller degrades cleanly (a clear message, the log stays LOCAL) and NEVER silently downgrades."""


def honest_note() -> str:
    """The one honest sentence reused in output + docs: mokata phones NOTHING home."""
    return ("NO telemetry — mokata never phones your audit log home to mokata or Anthropic. A "
            "shared team log lives on YOUR team's own storage (your managed Postgres, just a "
            "DSN); mokata never stores the DSN secret, only the env-var name is recorded.")


def actor() -> str:
    """Best-effort per-actor attribution (who) from the environment — for the shared log's
    who-did-what. Never a machine path; a name only."""
    for var in ("MOKATA_ACTOR", "USER", "USERNAME", "LOGNAME"):
        val = os.environ.get(var)
        if val:
            return val
    return "unknown"


def namespace(root: str) -> str:
    """The project the shared log is NAMESPACED by. Stage 71a ALIGNS this with the ONE project key
    every shared backend uses (`project.derive_project_id`), so audit scopes consistently with
    memory / vector / sessions. Machine-path-free + deterministic (git remote if present, else the
    repo path, hashed). A `Surface` caller uses `project.project_id` to honor the configured
    `settings.project.id` override."""
    from .project import derive_project_id
    return derive_project_id(root)


# ------------------------------------------------------------------------------- settings helpers
def _settings(data: dict) -> dict:
    return dict(((data.get("settings") or {}).get(_AUDIT_SETTINGS_KEY) or {}))


def shared_enabled(data: dict) -> bool:
    """True when the team has OPTED IN to sharing (`settings.audit.shared`). Default: LOCAL."""
    return bool(_settings(data).get("shared"))


def dsn_env_name(data: Optional[dict] = None) -> str:
    """The env-var NAME the shared audit DSN is DISPLAYED as (never the secret itself): the
    audit-specific override (`settings.audit.dsn_env`) when set, else the CONFIGURED team name
    (via the ONE resolver), else the default. Aligns display with what `resolve_dsn` resolves."""
    from .dsn import resolve_dsn_env
    return _settings(data or {}).get("dsn_env") or resolve_dsn_env(data)


def resolve_dsn(dsn_env: Optional[str] = None, *, data: Optional[dict] = None,
                environ: Optional[dict] = None) -> Optional[str]:
    """The DSN VALUE for the shared audit log. Resolution order (C-1, matches memory/journal):
    an explicit per-call name / the audit-specific configured name (`settings.audit.dsn_env`) →
    the audit override var (`$MOKATA_AUDIT_PG_DSN`) → the CONFIGURED team DSN name (via
    `dsn.resolve_dsn_env`, which also supplies the literal default). None when none is set — the
    caller degrades clean. The DSN VALUE is only read here, never stored."""
    from .dsn import resolve_dsn_env
    env = os.environ if environ is None else environ
    explicit = dsn_env or _settings(data or {}).get("dsn_env")
    names = ([explicit] if explicit else []) + [AUDIT_OVERRIDE_ENV, resolve_dsn_env(data)]
    for name in names:
        val = env.get(name) if name else None
        if val:
            return val
    return None


# ----------------------------------------------------- standing audit-publish consent (C5/P-10)
# doc 48 C5/P-10 — the batched audit publish is deferred DURABILITY, never deferred CONSENT. An
# EXPLICIT, revocable standing consent captured ONCE at onboarding (human-gated + ledgered) stands
# in for the per-batch prompt WITHOUT weakening the gate: the per-publish secret-scan still hard-
# blocks (P2 intact). The flag lives in `settings.audit.standing_consent`; the who/when of every
# grant/revoke lives in the ledger (`audit_consent` events) — revocable any time.
STANDING_CONSENT_KEY = "standing_consent"


@dataclass
class ConsentResult:
    granted: bool
    changed: bool = False
    aborted: bool = False
    message: str = ""


@dataclass
class ConsentCapture:
    """The onboarding-capture verdict (mapped to a `team join` step by the caller)."""
    status: str          # wired | skipped | declined
    detail: str = ""


def has_standing_consent(data: dict) -> bool:
    """True when the team has granted the revocable standing audit-publish consent."""
    return bool(_settings(data).get(STANDING_CONSENT_KEY))


def _fresh_data(root: str) -> dict:
    """The manifest as committed on disk (so a just-written grant/revoke is seen even when the
    caller holds a stale surface)."""
    from .config import Surface
    return Surface.load(root).manifest.data


def grant_standing_consent(root: str, surface: Any, *, assume_yes: bool = False,
                           confirm: Optional[Callable[[str], bool]] = None,
                           out: Optional[Callable[[str], None]] = None,
                           ledger: Any = None) -> ConsentResult:
    """Grant the standing audit-publish consent — human-gated (via the universal `config set`
    WriteGate) + ledgered (an `audit_consent`/granted event). Idempotent: already-granted is a
    clean no-op."""
    from . import config_cmd
    emit = out or (lambda *_a: None)
    if has_standing_consent(_fresh_data(root)):
        msg = "standing audit-publish consent already granted (revoke: `mokata audit --consent revoke`)."
        emit(msg)
        return ConsentResult(True, False, False, msg)
    who = actor()
    res = config_cmd.config_set(root, f"settings.{_AUDIT_SETTINGS_KEY}.{STANDING_CONSENT_KEY}",
                                "true", assume_yes=assume_yes, confirm=confirm, out=emit,
                                ledger=ledger)
    if not getattr(res, "committed", False):
        return ConsentResult(False, False, getattr(res, "aborted", False),
                             getattr(res, "reason", "") or "standing consent not granted (declined).")
    led = ledger or AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("audit_consent", decision="granted", actor=who, scope="team-audit-publish")
    emit(f"standing audit-publish consent granted as {who} — batched publish won't re-prompt per "
         f"batch (the secret-scan gate stays). Revoke: `mokata audit --consent revoke`.")
    return ConsentResult(True, True, False, "granted")


def revoke_standing_consent(root: str, surface: Any, *, assume_yes: bool = False,
                            confirm: Optional[Callable[[str], bool]] = None,
                            out: Optional[Callable[[str], None]] = None,
                            ledger: Any = None) -> ConsentResult:
    """Revoke the standing consent — human-gated + ledgered (`audit_consent`/revoked). Publishing
    returns to per-batch human-gating. No-op (clean) when there is nothing to revoke."""
    from . import config_cmd
    emit = out or (lambda *_a: None)
    if not has_standing_consent(_fresh_data(root)):
        msg = "no standing audit-publish consent to revoke — per-batch human-gating is the default."
        emit(msg)
        return ConsentResult(False, False, False, msg)
    who = actor()
    res = config_cmd.config_set(root, f"settings.{_AUDIT_SETTINGS_KEY}.{STANDING_CONSENT_KEY}",
                                "false", assume_yes=assume_yes, confirm=confirm, out=emit,
                                ledger=ledger)
    if not getattr(res, "committed", False):
        return ConsentResult(True, False, getattr(res, "aborted", False),
                             getattr(res, "reason", "") or "revoke declined.")
    led = ledger or AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("audit_consent", decision="revoked", actor=who, scope="team-audit-publish")
    emit(f"standing audit-publish consent revoked as {who} — publishing returns to per-batch "
         f"human-gating.")
    return ConsentResult(False, True, False, "revoked")


def capture_standing_consent(root: str, surface: Any, dsn_env: str, environ: Optional[dict],
                             *, assume_yes: bool = False,
                             confirm: Optional[Callable[[str], bool]] = None,
                             out: Optional[Callable[[str], None]] = None,
                             ledger: Any = None) -> ConsentCapture:
    """Onboarding capture of the standing consent (doc 48 C5/P-10). Context-gated: offered only
    when a shared-audit context exists (a DSN is exported OR sharing is on); otherwise it points
    at how to grant later. Never a governance bypass — the per-publish secret-scan gate stays."""
    emit = out or (lambda *_a: None)
    env = os.environ if environ is None else environ
    data = _fresh_data(root)
    if has_standing_consent(data):
        return ConsentCapture("wired", team_docs.with_docs(
            "standing audit-publish consent already granted — revoke any time with "
            "`mokata audit --consent revoke`."))
    dsn_set = bool((env.get(dsn_env) or "").strip())
    if not (dsn_set or shared_enabled(data)):
        return ConsentCapture("skipped",
            "no shared-audit context yet — grant standing consent later with "
            "`mokata audit --consent grant` once team audit sharing is on.")
    res = grant_standing_consent(root, surface, assume_yes=assume_yes, confirm=confirm,
                                 out=emit, ledger=ledger)
    if res.granted and res.changed:
        return ConsentCapture("wired", team_docs.with_docs(
            "standing audit-publish consent granted (batched publish won't re-prompt per batch; "
            "the secret-scan gate stays). Revoke: `mokata audit --consent revoke`."))
    if res.granted:
        return ConsentCapture("wired", "standing audit-publish consent already granted.")
    if res.aborted:
        return ConsentCapture("declined",
            "standing consent declined — publishing stays per-batch human-gated; grant later "
            "with `mokata audit --consent grant`.")
    return ConsentCapture("skipped", res.message or "standing consent not captured.")


# --------------------------------------------------------------------------------- shared backend
class SharedAuditLog:
    """A shared, OWNED, namespaced audit-log table reached by an env-var DSN (mirrors the shared
    session/memory Postgres backends). APPEND-ONLY (INSERT only) with PER-ACTOR attribution and a
    per-repo NAMESPACE, so concurrent teammates never clobber each other — each write is a fresh
    `BIGSERIAL` row. `autocommit` so one client's publish is immediately visible to the others."""

    name = "postgres"
    TABLE = PG_TABLE

    def __init__(self, dsn: Optional[str] = None, client: Any = None) -> None:
        if client is not None:
            self._conn = client               # an injected connection: already provisioned.
            return
        if not dsn:
            raise SharedAuditUnavailable(
                "the shared audit log needs a DSN in $" + " / $".join(PG_DSN_ENVS)
                + " (never inline in the committed manifest)")
        # D1 — VERIFY-ONLY: `team init` (teamdb) owns this table. Its `id BIGSERIAL PRIMARY KEY`
        # is the conflict-free key — every writer's row is its OWN row, so concurrent appends never
        # clobber. An append-only runtime role (no UPDATE/DELETE grant) is exactly the point, and
        # this connect no longer demands CREATE on top of it.
        from .memory._pg import connect_psycopg
        self._conn = connect_psycopg(dsn, SharedAuditUnavailable)

    # Justification for the B608 suppressions below (bandit false positive): the SQL interpolates
    # ONLY the mokata-OWNED constant `self.TABLE`, never user input; every VALUE (namespace/actor/
    # entry/…) rides the driver's `%s` placeholders. Suppression markers only — no injection surface.
    def append(self, ns: str, who: str, entry: dict) -> None:
        """APPEND-ONLY — INSERT one entry. Never updates/deletes an existing row, so two actors
        publishing concurrently both survive."""
        self._conn.execute(
            f"INSERT INTO {self.TABLE} (namespace, actor, seq, kind, at, entry)"  # nosec B608
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (ns, who, int(entry.get("seq") or 0), str(entry.get("kind", "")),
             str(entry.get("at", "")), json.dumps(entry)))

    def max_seq(self, ns: str, who: str) -> int:
        """The highest local seq already published FOR THIS (namespace, actor) — so a re-publish
        only appends NEW entries (idempotent, per-actor; never re-sends or clobbers another's)."""
        row = self._conn.execute(
            f"SELECT MAX(seq) FROM {self.TABLE} WHERE namespace=%s AND actor=%s",  # nosec B608
            (ns, who)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def read(self, ns: Optional[str] = None) -> List[dict]:
        """Every shared entry (optionally scoped to one namespace), OLDEST first — spans ALL
        actors so the read is team-wide who-did-what. Each returned entry carries its actor +
        namespace attribution."""
        if ns:
            rows = self._conn.execute(
                f"SELECT namespace, actor, entry FROM {self.TABLE} WHERE namespace=%s"  # nosec B608
                " ORDER BY id", (ns,)).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT namespace, actor, entry FROM {self.TABLE} ORDER BY id").fetchall()  # nosec B608
        out: List[dict] = []
        for row_ns, row_actor, raw in rows:
            try:
                entry = json.loads(raw)
            except (ValueError, TypeError):
                entry = {"raw": raw}
            entry.setdefault("actor", row_actor)
            entry["namespace"] = row_ns
            out.append(entry)
        return out

    def list_projects(self) -> List[str]:
        """Distinct project keys (namespaces) with audit rows — for `audit --team --list-projects`.
        Stage 71a: the audit `namespace` column IS the shared project key (aligned)."""
        from .project import LEGACY_PROJECT
        rows = self._conn.execute(
            f"SELECT DISTINCT namespace FROM {self.TABLE}").fetchall()  # nosec B608
        return sorted({(r[0] if r and r[0] else LEGACY_PROJECT) for r in rows})


def make_shared_log(dsn_env: Optional[str] = None, *, data: Optional[dict] = None,
                    client: Any = None) -> SharedAuditLog:
    """Build the shared audit log. OPT-IN + degrade-clean: with no injected client and no DSN it
    raises `SharedAuditUnavailable` (a clear message) rather than crashing or silently
    downgrading. `data` (the manifest) lets the resolver honor the CONFIGURED team DSN name
    (C-1), so a `team connect --dsn-env CUSTOM` reaches audit without a separate audit var."""
    if client is not None:
        return SharedAuditLog(client=client)
    return SharedAuditLog(dsn=resolve_dsn(dsn_env, data=data))


def _is_publish_record(entry: dict) -> bool:
    """A publish's OWN WriteGate record (kind `write_gate`, target `team-audit:…`). Excluded from
    the shareable set so re-publishing is a true no-op (the bookkeeping of sharing doesn't trickle
    endlessly back into the shared log)."""
    return (entry.get("kind") == "write_gate"
            and str(entry.get("target", "")).startswith("team-audit:"))


def _fresh_entries(log: SharedAuditLog, local: AuditLedger, ns: str, who: str) -> List[dict]:
    """The local entries not yet published to the shared log for THIS (namespace, actor) — minus a
    publish's own bookkeeping record, so a re-publish with no real activity is a clean no-op."""
    since = log.max_seq(ns, who)
    return [e for e in local.entries()
            if int(e.get("seq") or 0) > since and not _is_publish_record(e)]


# --------------------------------------------------------------------------------- publish (write)
@dataclass
class ShareResult:
    committed: bool
    aborted: bool
    published: int
    pending: int = 0
    reason: str = ""
    findings: List[Finding] = field(default_factory=list)
    message: str = ""


def share_audit(root: str, surface: Any, *, assume_yes: bool = False, policy: Any = None,
                confirm: Optional[Callable[[str], bool]] = None,
                out: Optional[Callable[[str], None]] = None,
                ledger: Any = None, client: Any = None) -> ShareResult:
    """Publish this dev's NEW local audit entries to the team's shared log. The ONLY moment data
    leaves the machine — so it is SECRET-SCANNED + HUMAN-GATED through the universal WriteGate
    (kind `send`, the egress rule; a secret is a hard block approval can't override). Append-only,
    per-actor, namespaced. OPT-IN (`settings.audit.shared`); degrade-clean when the backend is
    absent (stays LOCAL). The DSN secret is never stored."""
    emit = out or (lambda *_a: None)
    data = surface.manifest.data
    emit(honest_note())

    if not shared_enabled(data):
        msg = ("team audit sharing is OFF — your log stays LOCAL (the default). Opt in with "
               "`mokata config set settings.audit.shared true`.")
        emit(msg)
        return ShareResult(False, False, 0, reason="not enabled", message=msg)

    dsn_env = dsn_env_name(data)
    try:
        log = make_shared_log(data=data, client=client)
    except SharedAuditUnavailable as exc:
        msg = (f"shared audit unavailable ({exc}) — your log stays LOCAL (degrade-clean). "
               f"Export ${dsn_env} and `pip install 'mokata[postgres]'` to publish.")
        emit(msg)
        return ShareResult(False, False, 0, reason="unavailable", message=msg)

    from .project import project_id
    local = ledger or AuditLedger.from_mokata_dir(surface.mokata_dir)
    ns, who = project_id(surface), actor()
    fresh = _fresh_entries(log, local, ns, who)
    if not fresh:
        msg = f"shared audit already in sync — nothing new to publish (as {who})."
        emit(msg)
        return ShareResult(True, False, 0, pending=0, reason="in sync", message=msg)

    # SECURITY: publishing is data LEAVING the machine (egress) — secret-scan the payload +
    # human-gate it, exactly like a session push. The WriteGate (kind `send`) hard-blocks a secret
    # even under approval, and records the decision in the LOCAL ledger.
    payload = "\n".join(json.dumps(e) for e in fresh)
    plural = "y" if len(fresh) == 1 else "ies"
    prompt = (f"mokata · approve publishing {len(fresh)} audit entr{plural} to your team's shared "
              f"log (${dsn_env}, as {who}; append-only, the DSN is never stored)?")

    # doc 48 C5/P-10 — an explicit STANDING consent (captured once at onboarding, revocable +
    # ledgered) stands in for the per-batch prompt. It does NOT weaken the gate: the secret-scan
    # below is a hard block a standing consent can't override (never a governance bypass).
    gate_yes = assume_yes
    if has_standing_consent(data) and not assume_yes:
        gate_yes = True
        emit("using your standing audit-publish consent (revoke: `mokata audit --consent revoke`).")
    box: dict = {}

    def _commit() -> None:
        for entry in fresh:
            log.append(ns, who, entry)
        box["n"] = len(fresh)

    from .govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                               policy_trust)
    gate = WriteGate(ledger=local, trust=policy_trust(policy))
    outcome = gate.submit(
        WriteRequest(kind="send", target=f"team-audit:{dsn_env}/{ns}", content=payload,
                     actor=who, tool=policy_tool(policy, "audit_share"),
                     surface=policy_surface(policy, CLI_SURFACE)),
        commit=_commit, confirm=confirm, assume_yes=gate_yes, prompt=prompt,
        human_approved=policy_approved(policy))
    if not outcome.committed:
        emit(outcome.reason)
        return ShareResult(False, outcome.aborted, 0, pending=len(fresh),
                           reason=outcome.reason, findings=outcome.findings,
                           message=outcome.reason)
    published = box.get("n", 0)
    msg = (f"published {published} audit entr{'y' if published == 1 else 'ies'} to the team's "
           f"shared log as {who} (append-only, per-actor).")
    emit(msg)
    return ShareResult(True, False, published, pending=len(fresh), reason="committed", message=msg)


def pending_share(root: str, surface: Any, *, client: Any = None) -> Tuple[bool, int, str, str]:
    """Read-only preview for the propose path: (available, pending_count, dsn_env, message). Counts
    local entries not yet published for this (namespace, actor). Connects to the shared log to
    compare; writes NOTHING and never gates."""
    data = surface.manifest.data
    dsn_env = dsn_env_name(data)
    if not shared_enabled(data):
        return (False, 0, dsn_env, "team audit sharing is OFF (local-first default).")
    try:
        log = make_shared_log(data=data, client=client)
    except SharedAuditUnavailable as exc:
        return (False, 0, dsn_env, str(exc))
    from .project import project_id
    local = AuditLedger.from_mokata_dir(surface.mokata_dir)
    fresh = _fresh_entries(log, local, project_id(surface), actor())
    return (True, len(fresh), dsn_env, f"{len(fresh)} local entr"
            f"{'y' if len(fresh) == 1 else 'ies'} pending publish as {actor()}.")


# ----------------------------------------------------------------------------------- read (team)
@dataclass
class TeamAuditView:
    available: bool
    entries: List[dict] = field(default_factory=list)
    actors: List[str] = field(default_factory=list)
    message: str = ""


_PROJECT_CURRENT = object()      # Stage 71a — sentinel: scope to the current project (default)


def team_audit_view(root: str, surface: Any, *, client: Any = None,
                    project: Any = _PROJECT_CURRENT) -> TeamAuditView:
    """The team-wide who-did-what/why view — reads the SHARED log (spans ALL actors) for this
    repo's project by default. Read-only. Stage 71a: pass `project=None` to span ALL projects
    (`--all`) or a specific project id (`--project`). Degrade-clean: sharing off / backend absent
    → available=False with a clear message and the LOCAL log unaffected."""
    data = surface.manifest.data
    if not shared_enabled(data):
        return TeamAuditView(False, message=(
            "team audit sharing is OFF (local-first default). Opt in with "
            "`mokata config set settings.audit.shared true`, then `mokata audit --team`."))
    dsn_env = dsn_env_name(data)
    try:
        log = make_shared_log(data=data, client=client)
    except SharedAuditUnavailable as exc:
        return TeamAuditView(False, message=(
            f"shared audit unavailable ({exc}) — nothing team-wide to show; your LOCAL log is "
            f"unaffected (degrade-clean)."))
    from .project import project_id
    ns = project_id(surface) if project is _PROJECT_CURRENT else project  # None → span all projects
    entries = log.read(ns)
    actors = sorted({str(e.get("actor") or "unknown") for e in entries})
    return TeamAuditView(True, entries=entries, actors=actors,
                         message=(f"{len(entries)} shared entr"
                                  f"{'y' if len(entries) == 1 else 'ies'} across "
                                  f"{len(actors)} actor(s)."))


def render_team_timeline(view: TeamAuditView, tail: Optional[int] = None) -> List[str]:
    """The who-did-what/why lines over the SHARED log — each entry's `why_timeline` line prefixed
    with its actor. Reuses `govern.ledger.why_timeline` (no rebuild)."""
    from .govern.ledger import WHY_TIMELINE_TAIL, why_timeline
    tail = tail if tail else WHY_TIMELINE_TAIL
    rows = view.entries[-tail:] if tail and tail > 0 else list(view.entries)
    lines = why_timeline(rows, tail=0)      # tail=0 → no further truncation
    return [f"[{str(e.get('actor') or 'unknown'):<10}] {line}"
            for e, line in zip(rows, lines)]
