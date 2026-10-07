"""CM.S5 — publishing the typed event stream to the team's OWN Postgres, and waking its readers.

doc 77 §6 CM.S5 (deferred to the events release): *"Populate `mokata_events` + LISTEN/NOTIFY
subscription (ADR-54 R5 finally cashed in) for live invalidation. Until then pull-based freshness
is acceptable BECAUSE stores are per-call — document that invariant so nobody introduces a
long-lived store."*

⛔ **THREE THINGS WERE RE-DERIVED FROM THE TREE BEFORE A LINE OF THIS WAS WRITTEN, because doc 84
marked CM.S5 ✅ SHIPPED IN 0.0.17 and it was false in both halves.**

  1. **`mokata_events` had never been written to.** `team init` provisioned it from v1 and its own
     DDL comment said *"provisioned only; local-first population until a later UI"*. Zero inserts,
     zero readers, in every build. ⭐ doc 77 §6 C-5 recorded exactly this in **2026-07** — *"zero
     inserts, zero LISTEN/NOTIFY subscribers anywhere in src/"* — so **the row's own source
     document contained the measurement that disproved the row's tick.**
  2. **There was no LISTEN and no NOTIFY anywhere in `src/`.** Every match for those words is a
     WARNING that they die behind a transaction-mode pooler (`dsn_inspect`, `run_mode`), which is
     a different thing from using them.
  3. ⭐ **AND THE THING CM.S5 EXISTED TO INVALIDATE WAS DELETED BY CM.S3.** The read-through
     cache is gone — doc 77's own CM.S3 asked for it to be dropped outright rather than re-wired,
     and it was. ⚠ Its class name is deliberately NOT written here: `test_cm_s3_read_your_writes`
     asserts that identifier appears nowhere in `src/`, which is how it proves the class is
     retired, and prose about the deletion would defeat the guard that records it. So "live invalidation" has no cache to invalidate, and the
     SUBSCRIBER half of this row has no consumer in this tree at all.

**WHICH IS WHY THIS MODULE IS THE PUBLISHER AND NOT THE SUBSCRIBER** (Jas, 2026-10-04). A LISTEN
loop with nothing to invalidate would be §7i — a mechanism nothing runs — which is the class this
release has filed more than any other. The publisher is the half that survives the cache's
deletion: ID.S3's event bus (**0.0.24**, doc 86's IDE-TRACK map) is the first real consumer, and it
subscribes to what this writes. The invariant doc 77 asked for is a GUARD now rather than prose
(`tests/test_a40_…`), and it is load-bearing precisely BECAUSE the cache is gone: pull-based
freshness is the whole story, so a long-lived store would be the defect.

⭐ **PUBLISHING IS EGRESS, SO IT IS HUMAN-GATED AND SECRET-SCANNED — not a background insert.**
This is `team_audit.SharedAuditLog`'s model, and it is deliberate: the moment data leaves the
machine is the moment P2 applies. Every publish goes through the universal `WriteGate` as kind
`send` with the egress rule, and the secret scan is a HARD BLOCK that a standing consent cannot
override. Sharing is OPT-IN (`settings.events.shared`) and LOCAL-FIRST — the default publishes
nothing, ever. ⚠ Stage 10 **proved** events carry no content (`test_a39`'s planted-marker test
walks a marker through all seven payload types and finds it in none), so the scan's finding set is
expected to be empty; the gate is here anyway, because "the payloads carry no content" is a
property of the seven types that exist today and a future type is exactly what would break it.

⚠ **THE NOTIFY SIDE HAS TWO HARD LIMITS AND BOTH SHAPE THE DESIGN RATHER THAN BEING CHECKED FOR.**
A `NOTIFY` payload is capped at **8000 bytes** and a channel name is an IDENTIFIER capped at 63 —
so the channel is a CONSTANT and the namespace travels in the payload, and the payload carries
**identifiers only** (namespace, actor, how many, the high-water mark). It is a doorbell, not a
delivery: a woken reader goes and SELECTs. That also means a listener that misses a notification
loses nothing, which is the property that lets this fail open safely.

⛔ **AND LISTEN/NOTIFY DIES BEHIND A TRANSACTION-MODE POOLER (doc 48 H1 / ADR-54 / doc 85 §1).**
`dsn_inspect` already detects that shape and `team init` already warns about it. A publisher that
silently sent notifications nobody could ever receive would be a false green (§7e), so
`notify_support()` DECLARES the answer — supported, unsupported-behind-a-pooler, or unknown — and
the publish result carries it. Three states, never two: *"nobody was listening"* and *"nothing
could have been listening"* are different facts.

NO TELEMETRY. The only network target in this module is the team's own DSN, read from the
environment and never stored.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from .dsn import DEFAULT_DSN_ENV
from .errors import DegradedCapability
from .events import EVENTS_SETTINGS_KEY
from .govern.gate import WriteGate, WriteRequest
from .govern.secrets import Finding
from .teamdb import EVENTS_TABLE

# The events subsystem's own DSN override (wins over the team/default names when set) — the
# `MOKATA_AUDIT_PG_DSN` shape, for the same reason: one subsystem may point elsewhere.
EVENTS_OVERRIDE_ENV = "MOKATA_EVENTS_PG_DSN"
PG_DSN_ENVS = (EVENTS_OVERRIDE_ENV, DEFAULT_DSN_ENV)

# ⭐ ONE CONSTANT CHANNEL. A Postgres channel name is an identifier (63 bytes), so it cannot carry
# a namespace — a per-project channel would silently truncate two long project ids into one name,
# which is the worst possible failure for a routing key. The namespace travels in the payload and
# every listener filters on it.
NOTIFY_CHANNEL = "mokata_events"

# Postgres caps a NOTIFY payload at 8000 bytes. The payload is identifiers only and will never
# approach it — but `actor` comes from the environment and nothing bounds what a user puts in
# `$USER`, so the cap is enforced here rather than discovered at the server.
NOTIFY_PAYLOAD_CAP_BYTES = 8000

# How many events ONE publish moves. A publish is a human-gated batch, so the bound is about the
# prompt a person reads and the statement a server executes, not about throughput: the tail that
# does not fit is published by the next run, which resumes exactly where this one stopped.
PUBLISH_BATCH_CAP = 500

STANDING_CONSENT_KEY = "standing_consent"

# ⭐ THE EGRESS TARGET'S PREFIX, DECLARED ONCE AND USED BY BOTH SIDES — the `WriteRequest.target`
# this module writes, and the predicate that recognises it coming back. Found by running the thing:
# a publish is a governed `send`, so the gate records it in the ledger, the ledger PROJECTS it into
# the local event store, and that event is then unpublished — so **every publish leaves exactly one
# new event behind, which is its own governance record.** The loop is bounded (one row per publish,
# never a cascade) and the row is worth keeping: the one write that moves data off this machine is
# the last write a shared audit surface should be blind to. But it makes `nothing-new` UNREACHABLE
# in practice, and a state that cannot be observed is the §7g smell — so the result distinguishes
# "nothing at all" from "only my own publish trail", and `published 1 event` stops looking like a
# bug to whoever reads it every time.
PUBLISH_TARGET_PREFIX = "team-events:"


class SharedEventsUnavailable(DegradedCapability):
    """The shared event log can't be built — no `psycopg`, no DSN, or an unprovisioned schema.
    The caller degrades cleanly and says so; it NEVER silently stops publishing."""


# -------------------------------------------------------------------- the pooler question, DECLARED
NOTIFY_SUPPORTED = "supported"
NOTIFY_POOLED = "unsupported-transaction-pooler"
NOTIFY_UNKNOWN = "unknown"


def notify_support(dsn: Optional[str]) -> Tuple[str, str]:
    """`(state, why)` for whether LISTEN/NOTIFY can work over this DSN at all.

    ⛔ Three states on purpose (§7g). *"Nobody was listening"* is a fact about readers; *"nothing
    could have been listening"* is a fact about the CONNECTION, and a transaction-mode pooler
    makes the second one true while looking exactly like the first from the publisher's side.
    `dsn_inspect` already knows this shape, and this READS ITS `verdict` rather than re-deriving
    it. ⚠ **The first draft read `.pooled` instead and that was §7j** — a derivation that types its
    own scope. `.pooled` is one axis of a three-state answer: an UNPARSEABLE connection string has
    `pooled=False` and `classified=False`, so reading the bool alone reported a string it could not
    even parse as *"notifications are deliverable"*. Found by running it against the literal text
    `not a dsn at all`, which came back `supported`. The three states live in ONE place and this is
    not that place."""
    if not dsn:
        return (NOTIFY_UNKNOWN, "no DSN resolved, so the connection shape is unknown")
    try:
        from .dsn_inspect import inspect_dsn
        verdict = inspect_dsn(dsn).verdict
    except Exception:  # noqa: BLE001 — an uninspectable DSN is UNKNOWN, never "supported"
        return (NOTIFY_UNKNOWN, "the DSN could not be inspected, so pooling is unknown")
    if verdict == "pooled":
        return (NOTIFY_POOLED,
                "this is a transaction-mode pooler, where LISTEN/NOTIFY does not survive: "
                "notifications are sent and can never be received. Use the direct/session "
                "connection string for the IDE and any other live reader.")
    if verdict == "direct":
        return (NOTIFY_SUPPORTED, "a direct/session connection — notifications are deliverable")
    return (NOTIFY_UNKNOWN,
            "the connection-string shape could not be classified, so whether a notification can "
            "be received is unknown — and unknown is not permission (the `min_supported` rule)")


# ------------------------------------------------------------------------------- settings helpers
def _settings(data: dict) -> dict:
    return dict(((data.get("settings") or {}).get(EVENTS_SETTINGS_KEY) or {}))


def shared_enabled(data: dict) -> bool:
    """True when the team OPTED IN to publishing (`settings.events.shared`). Default: LOCAL.

    ⚠ This is a DIFFERENT question from `settings.events.enabled`, which is whether the LOCAL
    typed store records anything at all. Local-on + shared-off is the default and is the whole
    point: the store is an observability lane on your own machine until you say otherwise."""
    return bool(_settings(data).get("shared"))


def dsn_env_name(data: Optional[dict] = None) -> str:
    """The env-var NAME the shared DSN is DISPLAYED as — never the secret."""
    from .dsn import resolve_dsn_env
    return _settings(data or {}).get("dsn_env") or resolve_dsn_env(data)


def resolve_dsn(dsn_env: Optional[str] = None, *, data: Optional[dict] = None,
                environ: Optional[dict] = None) -> Optional[str]:
    """The DSN VALUE, resolved through the ONE ladder (C-1): an explicit per-call name / the
    events-specific configured name → `$MOKATA_EVENTS_PG_DSN` → the CONFIGURED team name. The
    value is read here and never stored."""
    from .dsn import resolve_dsn_env
    env = os.environ if environ is None else environ
    explicit = dsn_env or _settings(data or {}).get("dsn_env")
    names = ([explicit] if explicit else []) + [EVENTS_OVERRIDE_ENV, resolve_dsn_env(data)]
    for name in names:
        val = env.get(name) if name else None
        if val:
            return val
    return None


def has_standing_consent(data: dict) -> bool:
    """An explicit, revocable standing publish consent (doc 48 C5/P-10) stands in for the
    per-batch prompt. It does NOT weaken the gate: the secret scan still hard-blocks."""
    return bool(_settings(data).get(STANDING_CONSENT_KEY))


def pointer_is_ahead(mark: int, local_high: int) -> bool:
    """The contradiction both the publisher and the preview refuse on, in ONE place.

    The resume pointer is `MAX(local_seq)` for this (namespace, actor) in the TEAM's table — a
    borrowed memory. When it sits ABOVE this machine's newest local event, either another machine
    publishes under the same name or this store was rebuilt, and in both cases publishing from
    that mark skips everything local AND reports success (review F2).

    ⭐ `local_high > 0` is not slack: a machine with NO local events and a non-zero mark is a
    publisher that is simply up to date, which is the ordinary case and must not be refused
    (§7f — a guard that convicts the healthy case is a guard somebody deletes).

    ⚠ ONE function because two surfaces ask it. Two copies of this comparison is how the preview
    comes to print a confident count over a state the publisher refuses."""
    return local_high > 0 and mark > local_high


def namespace(root: str) -> str:
    """The project key this publishes under — `project.derive_project_id`, the SAME key memory,
    sessions and audit use, so one repo scopes identically across every shared table."""
    from .project import derive_project_id
    return derive_project_id(root)


def actor() -> str:
    """Best-effort attribution for the shared rows. A name, never a machine path."""
    for var in ("MOKATA_ACTOR", "USER", "USERNAME", "LOGNAME"):
        val = os.environ.get(var)
        if val:
            return val
    return "unknown"


# --------------------------------------------------------------------------------- shared backend
class SharedEventLog:
    """The team's `mokata_events` table, APPEND-ONLY and namespaced per project.

    `team init` owns the DDL (D1/C4 — no runtime DDL anywhere), so this VERIFIES and writes. Rows
    are conflict-free by construction: every insert is its own `BIGSERIAL`, and the v6 unique index
    on `(namespace, event_id)` makes a re-publish a no-op rather than a duplicate."""

    name = "postgres"
    TABLE = EVENTS_TABLE

    def __init__(self, dsn: Optional[str] = None, client: Any = None) -> None:
        if client is not None:
            self._conn = client
        elif not dsn:
            raise SharedEventsUnavailable(
                "publishing events needs a DSN in $" + " / $".join(PG_DSN_ENVS)
                + " (never inline in the committed manifest)")
        else:
            from .memory._pg import connect_psycopg
            self._conn = connect_psycopg(dsn, SharedEventsUnavailable)
        self._require_schema()

    def _require_schema(self) -> None:
        """Refuse a store whose `mokata_events` predates v6 — BY NAME, and before any statement
        touches the table.

        ⛔ This is the degrade `teamdb.EVENTS_SCHEMA_MIN` is named for, and it is here because the
        stage that wrote that claim did not write this method: a v5 team got `UndefinedColumn` on
        `local_seq` as an uncaught traceback instead (review F1). A missing column is a true fact
        stated in a vocabulary the user cannot act on.

        ⭐ IN THE CONSTRUCTOR, AND ON BOTH PATHS, which is why the injected-client branch now falls
        through instead of returning. A check the test doubles can skip is a check the next double
        skips silently (§7e) — so a double that cannot answer *"what schema are you?"* no longer
        constructs, and that is the intended cost.
        """
        # ⭐ ONE reader of "what version is this store", deliberately the private one: a second
        # reader here would be a second definition of the same question (§7f).
        from .teamdb import EVENTS_SCHEMA_MIN, _read_schema_version
        try:
            present, version, _unused_min = _read_schema_version(self._conn)
        except Exception as exc:
            raise SharedEventsUnavailable(
                "the shared schema version could not be read (%s) — run `mokata team init`. "
                "Nothing was published; the local event store is untouched."
                % type(exc).__name__)
        if not present or version is None:
            raise SharedEventsUnavailable(
                "the shared schema is not provisioned — run `mokata team init`. Nothing was "
                "published; the local event store is untouched.")
        if version < EVENTS_SCHEMA_MIN:
            raise SharedEventsUnavailable(
                "publishing events needs shared schema v%d and this team's is v%d — run "
                "`mokata team init` to upgrade it (it is idempotent, and it refuses rather than "
                "drops if the old events table carries rows). Nothing was published; the local "
                "event store is untouched." % (EVENTS_SCHEMA_MIN, version))

    # The B608 suppressions below interpolate ONLY the mokata-owned `self.TABLE`; every value
    # rides the driver's `%s` placeholders. Suppression markers only — no injection surface.
    def max_local_seq(self, ns: str, who: str) -> int:
        """Where this (namespace, actor) got to. See the v6 note in `teamdb`: this is the
        PUBLISHER'S OWN append index and says nothing about another actor's."""
        row = self._conn.execute(
            f"SELECT MAX(local_seq) FROM {self.TABLE} "          # nosec B608
            "WHERE namespace=%s AND actor=%s", (ns, who)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def append_all(self, ns: str, who: str, events: List[Any], *, project: str = "") -> int:
        """INSERT a batch, oldest first, and return how many rows LANDED.

        ⭐ `ON CONFLICT DO NOTHING` is what makes a re-publish safe, and the return value is the
        rows that actually landed rather than the rows attempted — so a caller can tell *"I
        published 12"* from *"12 were already there"*, which are different facts about the team's
        store (§7g). ⚠ `duration_ms` and `ledger_seq` are passed through as `None`, never
        coalesced to 0: keeping absence distinguishable from a measured zero across the team
        boundary is the entire reason the v6 schema exists."""
        landed = 0
        for ev in events:
            cur = self._conn.execute(
                f"INSERT INTO {self.TABLE} (namespace, project, event_id, ts, "   # nosec B608
                "schema_version, type, session_id, run_id, actor, duration_ms, ledger_seq, "
                "data, local_seq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (namespace, event_id) DO NOTHING",
                (ns, project or "", ev.event_id, ev.ts, int(ev.schema_version), ev.type,
                 ev.session_id, ev.run_id, who,
                 None if ev.duration_ms is None else int(ev.duration_ms),
                 None if ev.ledger_seq is None else int(ev.ledger_seq),
                 json.dumps(ev.data, sort_keys=True), int(ev.seq)))
            landed += int(getattr(cur, "rowcount", 0) or 0)
        return landed

    def notify(self, ns: str, who: str, landed: int, high_water: int) -> str:
        """Ring the doorbell. Returns the payload actually sent, for the record.

        ⛔ **SENT AFTER THE INSERTS, AND THE ORDER IS THE WHOLE CORRECTNESS ARGUMENT.** Postgres
        delivers a notification at COMMIT; the connection is autocommit, so the rows are already
        durable when this runs. Sent before them, a woken reader would SELECT and find nothing —
        a doorbell for an empty doorstep, which is worse than no doorbell.

        The payload is identifiers only and is capped: if `actor` is long enough to threaten the
        8000-byte limit it is dropped rather than truncated, because half a name is a wrong name,
        and the namespace alone is still a complete instruction ("go read this project")."""
        payload = json.dumps({"ns": ns, "actor": who, "n": int(landed),
                              "to": int(high_water)}, sort_keys=True)
        if len(payload.encode("utf-8")) > NOTIFY_PAYLOAD_CAP_BYTES:
            payload = json.dumps({"ns": ns, "n": int(landed), "to": int(high_water)},
                                 sort_keys=True)
        if len(payload.encode("utf-8")) > NOTIFY_PAYLOAD_CAP_BYTES:
            payload = ""                 # a bare wake-up is still a complete instruction
        self._conn.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))
        return payload

    def read(self, ns: Optional[str] = None, *, limit: int = 1000) -> List[dict]:
        """Shared rows, OLDEST first, across ALL actors. `duration_ms` / `ledger_seq` come back as
        `None` when absent — the §7g distinction survives the round trip or the schema failed."""
        cols = ("namespace, project, event_id, ts, schema_version, type, session_id, run_id, "
                "actor, duration_ms, ledger_seq, data, local_seq")
        if ns:
            rows = self._conn.execute(
                f"SELECT {cols} FROM {self.TABLE} WHERE namespace=%s "        # nosec B608
                "ORDER BY id LIMIT %s", (ns, int(limit))).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {cols} FROM {self.TABLE} ORDER BY id LIMIT %s",      # nosec B608
                (int(limit),)).fetchall()
        out: List[dict] = []
        for r in rows:
            try:
                data = json.loads(r[11]) if r[11] else {}
            except (ValueError, TypeError):
                data = {}
            out.append({"namespace": r[0], "project": r[1], "event_id": r[2], "ts": r[3],
                        "schema_version": r[4], "type": r[5], "session_id": r[6], "run_id": r[7],
                        "actor": r[8], "duration_ms": r[9], "ledger_seq": r[10],
                        "data": data if isinstance(data, dict) else {}, "local_seq": r[12]})
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 — closing a broken connection is not a caller's problem
            return


def make_shared_log(*, data: Optional[dict] = None, dsn_env: Optional[str] = None,
                    client: Any = None) -> "SharedEventLog":
    """Build the shared log, or raise `SharedEventsUnavailable` with a message naming the fix."""
    if client is not None:
        return SharedEventLog(client=client)
    return SharedEventLog(resolve_dsn(dsn_env, data=data or {}))


# ------------------------------------------------------------------------------------ the publish
@dataclass
class PublishResult:
    """What one publish did. Every field is a fact a caller might need to tell them apart (§7g)."""
    ok: bool = False
    aborted: bool = False
    published: int = 0                 # rows that LANDED
    attempted: int = 0                 # rows this batch tried
    already_there: int = 0             # attempted - published: a re-publish, not a failure
    own_trail: int = 0                 # of `attempted`, how many are this publisher's OWN records
    high_water: int = 0                # the local seq this (namespace, actor) has now reached
    remaining: int = 0                 # local events still unpublished after this batch
    notify_state: str = NOTIFY_UNKNOWN
    notify_why: str = ""
    reason: str = ""
    message: str = ""
    findings: List[Finding] = field(default_factory=list)


def _emit_noop(_msg: str) -> None:
    return


def is_own_publish_trail(event: Any) -> bool:
    """True when this event is a record of a PUBLISH rather than of the work being published.

    Keyed on the `subject` the publisher itself writes, through the ONE constant both sides read,
    so the two cannot drift. Not a name match on a tool string: `tool` is policy-substitutable
    (`policy_tool`), and a predicate that keys on a substitutable value grades nothing."""
    if getattr(event, "type", "") != "gate_decision":
        return False
    data = getattr(event, "data", None) or {}
    return str(data.get("subject", "")).startswith(PUBLISH_TARGET_PREFIX)


def pending_publish(root: str, surface: Any, *, client: Any = None
                    ) -> Tuple[bool, int, str, str]:
    """Read-only preview: `(available, pending_count, dsn_env, message)`. Writes NOTHING and never
    gates — the propose path calls it to say what a publish WOULD do."""
    data = surface.manifest.data
    env_name = dsn_env_name(data)
    if not shared_enabled(data):
        return (False, 0, env_name, "publishing events is OFF (local-first default). Turn it on "
                                    "with `mokata config set settings.events.shared true`.")
    try:
        log = make_shared_log(data=data, client=client)
    except SharedEventsUnavailable as exc:
        return (False, 0, env_name, str(exc))
    try:
        ns, who = namespace(root), actor()
        from .events.store import store_for_root
        store = store_for_root(root)
        mark = log.max_local_seq(ns, who)
        local_high = store.max_seq()
        if local_high is None:
            return (False, 0, env_name,
                    "the local event store exists and could not be read, so the pending count is "
                    "unknown — NOT zero. Check `.mokata/temp_local/events/events.db`.")
        if pointer_is_ahead(mark, local_high):
            # The same contradiction `publish` refuses on, reported here rather than printed as
            # a confident "0 event(s) not yet published" (review F2/F4).
            return (False, 0, env_name,
                    f"your team's store records local sequence {mark} for '{who}', ahead of this "
                    f"machine's newest event ({local_high}) — the pending count cannot be "
                    f"derived. Set $MOKATA_ACTOR to a name unique to this machine.")
        # ⭐ The EXACT backlog from the store's own gapless append index, not `len()` of a CAPPED
        # fetch: this printed "501 event(s) not yet published" as a fact with any larger number
        # queued (review F4).
        n = max(0, local_high - mark)
        more = "" if n <= PUBLISH_BATCH_CAP else f" (this batch would send {PUBLISH_BATCH_CAP})"
        return (True, n, env_name,
                f"{n} event(s) not yet published to ${env_name} as {who}{more}.")
    finally:
        if client is None:
            log.close()


def publish(root: str, surface: Any, *, assume_yes: bool = False,
            confirm: Optional[Callable[[str], bool]] = None,
            out: Optional[Callable[[str], None]] = None,
            client: Any = None, policy: Any = None,
            ledger: Any = None) -> PublishResult:
    """Publish this repo's unpublished local events to the team's shared table, human-gated.

    The shape is `team_audit.share`'s, deliberately: batched DURABILITY, never batched CONSENT.
    A standing consent replaces the per-batch prompt and cannot touch the secret scan."""
    emit = out or _emit_noop
    data = surface.manifest.data
    env_name = dsn_env_name(data)
    if not shared_enabled(data):
        msg = ("publishing events is OFF (local-first default). Turn it on with "
               "`mokata config set settings.events.shared true`.")
        emit(msg)
        return PublishResult(reason="shared-off", message=msg)

    dsn = resolve_dsn(data=data)
    state, why = notify_support(dsn)
    try:
        log = make_shared_log(data=data, client=client)
    except SharedEventsUnavailable as exc:
        emit(str(exc))
        return PublishResult(reason="unavailable", message=str(exc),
                             notify_state=state, notify_why=why)

    ns, who = namespace(root), actor()
    try:
        from .events.store import store_for_root
        store = store_for_root(root)

        # ---- the RESUME POINTER, and the two ways it lies ------------------------------------
        # ⛔ The pointer is `MAX(local_seq)` for THIS (namespace, actor) in the TEAM's table, and
        # that is a borrowed memory: it assumes one producer per actor name and a local store
        # whose seqs only ever go up. CM.S5's review (F2) broke both assumptions with a live
        # server, and in each case the publisher answered `ok=True, "nothing new to publish"`,
        # MCP `status: "in_sync"`, exit 0 — while every local event stayed unpublished. ⭐ A
        # pointer that is WRONG is survivable; a pointer that is wrong and reports SUCCESS is the
        # failure class this release is named for. Each contradiction below is now NAMED, and the
        # remedy named with it is one that exists (`$MOKATA_ACTOR`).
        #
        # ⚠ THIS IS A REFUSAL, NOT THE FIX. The fix is a LOCAL high-water mark per
        # (namespace, DSN) — the publisher's own bookkeeping, not an inference from somebody
        # else's rows — and that is a stage, filed as `PUBLISH-POINTER-IS-BORROWED-MEMORY`
        # (doc 84). Until then these three outcomes are honest and the silent one is gone.
        mark = log.max_local_seq(ns, who)
        local_high = store.max_seq()
        if local_high is None:
            msg = ("the local event store exists and could not be read, so there is nothing to "
                   "compare against your team's. NOTHING was published and no high-water mark "
                   "was recorded. Check `.mokata/temp_local/events/events.db`, then retry.")
            emit(msg)
            return PublishResult(reason="local-unreadable", message=msg,
                                 notify_state=state, notify_why=why)
        if pointer_is_ahead(mark, local_high):
            msg = (f"REFUSING: your team's store already records local sequence {mark} for "
                   f"'{who}', which is AHEAD of this machine's newest event ({local_high}). "
                   f"Either another machine publishes under the same name, or this machine's "
                   f"event store was rebuilt. Publishing now would skip every local event and "
                   f"report success. Set $MOKATA_ACTOR to a name unique to this machine and "
                   f"retry; nothing was published.")
            emit(msg)
            return PublishResult(reason="pointer-ahead", message=msg, high_water=mark,
                                 notify_state=state, notify_why=why)

        fetched = store.after_seq(mark, limit=PUBLISH_BATCH_CAP + 1)
        batch = fetched[:PUBLISH_BATCH_CAP]
        if not batch:
            msg = f"nothing new to publish (${env_name}, as {who})."
            emit(msg)
            return PublishResult(ok=True, reason="nothing-new", message=msg,
                                 notify_state=state, notify_why=why)
        # ⭐ The EXACT backlog, not a flag. `fetched` is capped at `PUBLISH_BATCH_CAP + 1`, so
        # `len(fetched) - len(batch)` is 0 or 1 and NOTHING ELSE — it was printed as "1 more
        # queued for the next publish" with eleven thousand queued (review F4). `seq` is the
        # store's own gapless append index, so the subtraction below is a count.
        remaining_after = max(0, local_high - int(batch[-1].seq))
        own = sum(1 for e in batch if is_own_publish_trail(e))

        # ⚠ The gate's `content` is what the SECRET SCAN reads, so it must be the bytes that are
        # actually about to leave — not a summary of them. Stage 10 proved the payloads carry no
        # content; this is what would notice if a future payload type stopped being true to that.
        payload = "\n".join(json.dumps(
            {"event_id": e.event_id, "ts": e.ts, "type": e.type, "session_id": e.session_id,
             "run_id": e.run_id, "duration_ms": e.duration_ms, "ledger_seq": e.ledger_seq,
             "data": e.data}, sort_keys=True) for e in batch)
        plural = "" if len(batch) == 1 else "s"
        prompt = (f"mokata · approve publishing {len(batch)} typed event{plural} to your team's "
                  f"shared store (${env_name}, as {who}; append-only, counts only, the DSN is "
                  f"never stored)?")
        gate_yes = assume_yes
        if has_standing_consent(data) and not assume_yes:
            gate_yes = True
            emit("using your standing event-publish consent "
                 "(revoke: `mokata config set settings.events.standing_consent false`).")

        box: dict = {}

        def _commit() -> None:
            landed = log.append_all(ns, who, batch, project=ns)
            box["landed"] = landed
            # ⛔ ONLY WHEN SOMETHING LANDED. `notify`'s own docstring argues the ORDER precisely
            # so a woken reader never finds an empty doorstep — and then rang unconditionally, so
            # a batch whose every row conflicted woke every listener in the team to SELECT nothing
            # (review F6). The order was right and the condition was missing.
            box["sent"] = (log.notify(ns, who, landed, int(batch[-1].seq)) if landed else "")

        from .govern.ledger import AuditLedger
        from .govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                                   policy_trust)
        local = ledger if ledger is not None else AuditLedger.from_mokata_dir(
            os.path.join(root, ".mokata"))
        gate = WriteGate(ledger=local, trust=policy_trust(policy))
        outcome = gate.submit(
            WriteRequest(kind="send", target=f"{PUBLISH_TARGET_PREFIX}{env_name}/{ns}",
                         content=payload,
                         actor=who, tool=policy_tool(policy, "events_publish"),
                         surface=policy_surface(policy, CLI_SURFACE)),
            commit=_commit, confirm=confirm, assume_yes=gate_yes, prompt=prompt,
            human_approved=policy_approved(policy))
        if not outcome.committed:
            emit(outcome.reason)
            return PublishResult(aborted=outcome.aborted, attempted=len(batch),
                                 reason=outcome.reason, message=outcome.reason,
                                 findings=list(outcome.findings),
                                 remaining=len(fetched), notify_state=state, notify_why=why)

        landed = int(box.get("landed", 0))
        already = max(0, len(batch) - landed)
        if landed == 0 and remaining_after:
            # ⛔ The whole head was already there under a DIFFERENT actor name, so `MAX(local_seq)`
            # for THIS name did not move — and the next run will fetch this same head again, for
            # ever, while the tail past it is never published (review F2, scenario b). Reporting
            # `committed` here is a green over a stalled publisher.
            msg = (f"REFUSING to report success: all {len(batch)} event(s) in this batch were "
                   f"already in your team's store under another name, so the resume pointer for "
                   f"'{who}' did not move and the {remaining_after} behind them can never be "
                   f"reached. Set $MOKATA_ACTOR to the name those rows were published under and "
                   f"retry. The rows in your team's store are unchanged.")
            emit(msg)
            return PublishResult(attempted=len(batch), already_there=already, own_trail=own,
                                 high_water=mark, remaining=remaining_after,
                                 reason="pointer-stuck", message=msg,
                                 notify_state=state, notify_why=why)
        msg = (f"published {landed} event{'' if landed == 1 else 's'} to the team's shared store "
               f"as {who} (append-only, counts only)")
        if own and own == len(batch):
            msg += " — all of them records of earlier publishes, not of new work"
        elif own:
            msg += f" ({own} of them records of earlier publishes)"
        if already:
            msg += f"; {already} were already there"
        if remaining_after:
            msg += f"; {remaining_after} more queued for the next publish"
        if state == NOTIFY_POOLED:
            msg += (". ⚠ the notification was sent but CANNOT be received over this DSN — " + why)
        emit(msg + ".")
        return PublishResult(ok=True, published=landed, attempted=len(batch),
                             already_there=already, own_trail=own,
                             high_water=int(batch[-1].seq),
                             remaining=remaining_after, notify_state=state, notify_why=why,
                             reason="only-own-trail" if own == len(batch) else "committed",
                             message=msg)
    finally:
        if client is None:
            log.close()
