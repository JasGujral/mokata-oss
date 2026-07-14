"""Stage 35c — port memory between backends: `mokata memory migrate`.

Move the LIVE store from one database to another — local SQLite → a hosted Postgres for the
team, or → the Obsidian markdown vault, and back. Reads ALL items from the source and writes
them, WITH provenance, into the destination (resolved from the manifest's per-tool config).

Guarantees:
  - HUMAN-GATED — preview the item count + destination, then confirm (P2).
  - IDEMPOTENT — re-running upserts by id, so no duplicates.
  - NON-DESTRUCTIVE — the source is left intact unless an explicit, separately-gated
    `--drop-source`.
  - DEGRADE-CLEAN — if the destination can't be built (e.g. Postgres unreachable), report and
    write NOTHING; the source is never partially migrated then lost.

Unlike `build_backend` (which degrades a bad Postgres to the SQLite floor), migrate builds the
EXACT requested destination and FAILS LOUD if it can't — silently landing in a floor would
lose the team's data. Works across sqlite / obsidian / postgres via the MemoryBackend contract.
"""

from __future__ import annotations

from ..prompt import read_yes_no

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..govern import WriteGate, WriteRequest
from .backends import MemoryBackend, build_postgres_backend
from .item import downgrade_refusal
from .store import build_backend
from ..errors import MokataError

# Backends migrate can move between (each a MemoryBackend with file/db storage).
SUPPORTED = ("sqlite", "obsidian", "postgres")


class MigrateError(MokataError):
    """Raised when a source/destination backend can't be built for a migration."""


@dataclass
class MigrateResult:
    migrated: int = 0
    dropped: int = 0
    blocked: int = 0                 # items hard-blocked at the gate (secret) — not migrated
    refused: int = 0                 # SI.6 — items whose content DIVERGED from the approved batch
    pending: int = 0                 # SI.6 — journaled but not yet flushed (offline/contended)
    conflicts: int = 0               # SI.6 — CAS conflicts surfaced for `mokata sync`
    journaled: bool = False          # SI.6 — the write went journal-first (the team funnel)
    from_backend: str = ""
    to_backend: str = ""
    aborted: bool = False
    error: str = ""
    message: str = ""

    def render(self) -> str:
        if self.error:
            return (f"migrate: {self.error} — nothing migrated; source ({self.from_backend}) "
                    "left intact.")
        if self.aborted:
            return f"migrate aborted: {self.message}"
        line = (f"migrate: {self.migrated} item(s) {self.from_backend} -> "
                f"{self.to_backend} (idempotent upsert).")
        if self.blocked:
            line += f" {self.blocked} BLOCKED (secret detected — not migrated)."
        if self.refused:
            # SI.6 (content changed after the batch approval) + D6 (a doc a newer mokata wrote,
            # which this build must not re-serialize). Both mean: left intact in the source, not
            # migrated, reason named in the ledger.
            line += (f" {self.refused} REFUSED (not migrated — see the ledger for why; "
                     f"unchanged in the source).")
        if self.conflicts:
            line += (f" {self.conflicts} CONFLICT(s) surfaced — a concurrent writer changed "
                     f"the row; resolve with `mokata sync`.")
        if self.pending:
            line += (f" {self.pending} journaled but NOT yet applied (offline or another window "
                     f"is flushing) — `mokata sync` drains them; nothing is lost.")
        if self.dropped:
            line += f" Dropped {self.dropped} from the source."
        return line


# ------------------------------------------------------------------ batch-derivation guard (SI.6)
def item_digest(item: Any) -> str:
    """The hash of the exact content this migration was approved to write.

    A migration is ONE human decision covering MANY per-item writes (the batch shape it shares with
    `team join`'s vault pull). That decision is only meaningful if what actually lands is what was
    approved — so the batch is hashed at the gate, and every item is re-hashed at its write. Nothing
    may be injected, swapped or mutated in between; a divergence is REFUSED, not written."""
    return hashlib.sha256(json.dumps(
        {"id": item.id, "mtype": item.mtype, "subject": item.subject,
         "value": item.value, "status": item.status},
        sort_keys=True).encode("utf-8")).hexdigest()


def batch_digest(items: List[Any]) -> str:
    """The one hash naming the whole approved batch (order-independent, so a re-read that returns
    the same items in another order is still the same batch)."""
    return hashlib.sha256(
        "\n".join(sorted(item_digest(i) for i in items)).encode("utf-8")).hexdigest()


def build_named_backend(tool: str, root: str,
                        config: Optional[dict] = None,
                        clients: Optional[dict] = None,
                        project: Optional[str] = None) -> MemoryBackend:
    """Build the EXACT named backend (NON-degrading). Postgres that can't be reached raises
    `MigrateError` rather than falling to the SQLite floor — so a migration never silently
    lands somewhere it shouldn't. `project` (Stage 71a) scopes the shared Postgres to the current
    project so a migration reads/writes ONLY this project's rows on a shared DSN."""
    config = config or {}
    if tool == "postgres":
        be = build_postgres_backend(config, project=project)
        if be is None:
            raise MigrateError(
                "postgres backend is unreachable or unconfigured (set config.dsn_env, "
                "install the 'postgres' extra, and ensure the DB is up)")
        return be
    if tool in ("sqlite", "obsidian"):
        return build_backend(tool, root, clients=clients, config=config)
    raise MigrateError(f"unsupported migrate backend '{tool}'; choose one of {SUPPORTED}")


def _resolved_memory_tool(surface: Any) -> str:
    """The tool the memory_store currently resolves to (the default migrate source)."""
    try:
        res = surface.router.resolve("memory_store")
        if res is not None and res.available and res.tool in SUPPORTED:
            return res.tool
    except (ManifestError, AttributeError):
        # D5 — the real raisers of `router.resolve` (the SAME pair `select_memory_backend` already
        # names for this exact call): a broken manifest (ManifestError) and a duck-typed surface
        # with no `.router` (AttributeError). The SQLite floor is the documented default source.
        pass
    return "sqlite"


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Proceed with the migration?")


def _default_drop_confirm(text: str) -> bool:
    return read_yes_no(text, "DROP the source after migrating?")


def migrate_memory(surface: Any, to_backend: str, from_backend: Optional[str] = None,
                   *, confirm: Optional[Callable[[str], bool]] = None,
                   assume_yes: bool = False, drop_source: bool = False,
                   drop_confirm: Optional[Callable[[str], bool]] = None,
                   ledger: Any = None, policy: Any = None,
                   out: Optional[Callable[[str], None]] = None) -> MigrateResult:
    """Migrate all memory items from `from_backend` (default: the resolved store) to
    `to_backend`, with provenance, human-gated, idempotent, and non-destructive."""
    emit = out or print
    root = surface.mokata_dir
    manifest = surface.manifest
    src_tool = from_backend or _resolved_memory_tool(surface)
    from ..project import project_id
    project = project_id(surface)      # Stage 71a — migrate ONLY the current project's rows

    if to_backend not in SUPPORTED:
        return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,
                             error=f"unsupported destination '{to_backend}' "
                                   f"(one of {SUPPORTED})")

    # Build the source (its config from the manifest).
    try:
        source = build_named_backend(src_tool, root, manifest.tool_config(src_tool),
                                     project=project)
    except MigrateError as exc:
        return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,
                             error=f"source '{src_tool}' unavailable: {exc}")

    # Build the destination NON-degrading — a failure here writes NOTHING (degrade-clean).
    try:
        dest = build_named_backend(to_backend, root, manifest.tool_config(to_backend),
                                   project=project)
    except MigrateError as exc:
        source.close()
        return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,
                             error=str(exc))

    # Self-migrate guard: same tool + same config would copy onto itself; a --drop-source
    # then would wipe the data we just wrote. Refuse the destructive case.
    same_store = (src_tool == to_backend
                  and manifest.tool_config(src_tool) == manifest.tool_config(to_backend))

    items: List[Any] = source.all()      # ALL items (every status) — full-fidelity move

    # SI.6 — the approved batch, hashed BEFORE the human is asked. Every per-item write below must
    # derive from exactly this; see `item_digest`.
    approved_digests: Dict[str, str] = {it.id: item_digest(it) for it in items}
    batch = batch_digest(items)

    # SI.6 (74 C3) — WHERE the writes go. A `postgres` destination is the SHARED team memory table
    # (`teamdb.MEMORY_TABLE`), which the journal + single-flusher + CAS funnel OWNS. Writing it with
    # `dest.put` (a bare `ON CONFLICT DO UPDATE` that does NOT bump `revision`) bypassed that funnel
    # in BOTH directions: it silently clobbered a teammate's concurrent change, AND it left the row's
    # revision stale, so a later flush's CAS would compare against a revision that no longer
    # described the doc and overwrite the migrated value in turn. Journal-first is therefore not a
    # nicety here — a direct write actively corrupts the compare-and-set invariant every other
    # memory write depends on. sqlite/obsidian destinations are local files with no shared-row
    # concurrency and no funnel; they keep the direct backend write (byte-identical).
    to_funnel = (to_backend == "postgres")
    # The CAS bases: the destination's CURRENT revision per id, read once. Present → a
    # revision-guarded UPDATE (the idempotent upsert, made safe); absent → an INSERT of a believed-
    # new row. Either way a concurrent writer SURFACES as a conflict, never a silent lost update.
    base_revisions: Dict[str, Any] = {}
    if to_funnel:
        try:
            base_revisions = {i.id: getattr(i, "_revision", None) for i in dest.all()}
        except Exception as exc:                 # unreachable mid-migration → write NOTHING
            source.close()
            dest.close()
            return MigrateResult(from_backend=src_tool, to_backend=to_backend, aborted=True,
                                 error=f"destination '{to_backend}' unreadable: {exc}")

    emit(f"migrate: {len(items)} item(s) {src_tool} -> {to_backend}"
         + (" (same store — copy is a no-op)" if same_store else ""))
    if not assume_yes:
        gate = confirm or _default_confirm
        if not gate(f"Migrate {len(items)} memory item(s) from '{src_tool}' to "
                    f"'{to_backend}'? The source is left intact."):
            source.close()
            dest.close()
            return MigrateResult(from_backend=src_tool, to_backend=to_backend,
                                 aborted=True, message="declined at the human gate")
    if ledger is not None:
        # The batch decision itself, on the ledger — the anchor every per-item write inherits.
        ledger.record("migrate_batch", op="migrate", subject=f"{src_tool}->{to_backend}",
                      items=len(items), batch_digest=batch, journaled=to_funnel)

    # Stage 37R (H1): the source content is UNTRUSTED (an external Obsidian/Postgres store), so
    # every per-item write goes through the universal WriteGate — secrets in subject/value are
    # HARD-BLOCKED (not migrated) and each commit is recorded in the audit ledger when provided.
    from .. import team_journal, teamdb
    from ..govern.trust import (CLI_SURFACE, policy_surface, policy_tool, policy_trust)
    try:
        from ..team_audit import actor as _actor
        who = _actor()
    except Exception:
        # D5 — deliberately left BROAD, with no narrow class to name: `team_audit.actor`'s own
        # contract is never-raise, so there is no honest class to enumerate. "user" is the same
        # placeholder author the rest of the write path falls back to.
        who = "user"
    journal = team_journal.TeamJournal.for_surface(surface) if to_funnel else None
    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    migrated_items: List[Any] = []
    blocked = 0
    refused = 0

    def _journal_it(it: Any) -> None:
        """Journal-first (doc 48 E3), with approval inheritance. `ledger_id` is the seq the gate's
        `approved` record lands at — predicted as `len+1` from INSIDE the commit, which the gate runs
        under its exclusive ledger hold (B2/MS.S3), so the prediction is provably exact. The deferred
        flush re-records it: the durable write carries the human decision that licensed it (C5/P2)."""
        ledger_id = (len(ledger) + 1) if ledger is not None else None
        base = base_revisions.get(it.id)
        journal.append(team_journal.JournalEntry(
            id=uuid.uuid4().hex,
            op=(team_journal.OP_UPDATE if base is not None else team_journal.OP_PUT),
            table=teamdb.MEMORY_TABLE, key=it.id,
            payload={"id": it.id, "mtype": it.mtype, "subject": it.subject,
                     "status": it.status, "doc": json.dumps(it.to_doc()), "project": project},
            ledger_id=ledger_id, project=project, actor=who, base_revision=base))

    for it in items:
        # D6 — a migration is a write-back across backends: it READS a doc from the source store and
        # WRITES it to the destination. A doc a newer mokata wrote would be re-serialized by THIS
        # build, i.e. stripped, and land in the destination missing fields the human approved. Refuse
        # the item (it stays intact in the source), count it, and carry on with the rest — the same
        # per-item refusal shape as the batch-derivation guard below.
        refusal = downgrade_refusal(it)
        if refusal is not None:
            refused += 1
            if ledger is not None:
                ledger.record("write_gate", write_kind="memory", target=f"memory:{it.subject}",
                              actor="migrate", decision="blocked", reason=refusal)
            continue
        # SI.6 — the batch-derivation guard. The human approved THIS content; if the item diverges
        # from the hash taken at the gate, the approval does not cover what we are about to write.
        # Refuse it. (The loop iterates only the snapshot taken before the gate, so nothing NEW can
        # enter either — the two halves together are what make the batch approval mean something.)
        if item_digest(it) != approved_digests.get(it.id):
            refused += 1
            if ledger is not None:
                ledger.record("write_gate", write_kind="memory", target=f"memory:{it.subject}",
                              actor="migrate", decision="blocked",
                              reason="content changed after the batch approval")
            continue
        outcome = gate.submit(
            WriteRequest("memory", f"memory:{it.subject}",
                         content=f"{it.subject}\n{it.value}", actor="migrate",
                         tool=policy_tool(policy, "memory_migrate"),
                         surface=policy_surface(policy, CLI_SURFACE)),
            # journal-first into the shared funnel, or the direct backend write for a local file
            # destination. Idempotent either way: upsert by id, provenance kept.
            commit=((lambda it=it: _journal_it(it)) if to_funnel else (lambda it=it: dest.put(it))),
            # The human approved the MIGRATION as a batch, above — that decision covers every item
            # in it, so each per-item write carries it (and a read-only dial still blocks the lot).
            human_approved=True)
        if outcome.committed:
            migrated_items.append(it)
        elif outcome.findings:
            blocked += 1                         # secret — hard-blocked, left in the source

    # SI.6 — drain the journal through the SINGLE FLUSHER (MS.S5): each entry is secret-scanned at
    # egress strength, applied under CAS, and marked flushed with the inherited approval id. An
    # unhealthy/contended flush leaves the entries PENDING (never lost) — `mokata sync` drains them.
    pending = conflicts = 0
    if to_funnel and migrated_items:
        fres = team_journal.flush(surface, ledger=ledger, out=out)
        pending, conflicts = fres.pending, fres.conflicts
        if fres.skipped:
            emit(f"migrate: {fres.reason} — {len(migrated_items)} write(s) are journaled and will "
                 f"be applied by `mokata sync`; nothing is lost.")

    dropped = 0
    if drop_source:
        if same_store:
            emit("migrate: refusing --drop-source on a self-migration (would delete the "
                 "just-written data).")
        elif pending or conflicts:
            # SI.6 — journal-first makes "migrated" mean "journaled (+ applied when the flush was
            # healthy)". Dropping the source while writes are still pending, or while a CAS conflict
            # is awaiting a human, would be exactly the "partially migrated then lost" case this
            # command promises never to cause. Refuse; `mokata sync` first, then drop.
            emit(f"migrate: refusing --drop-source — {pending} write(s) are still journaled and "
                 f"{conflicts} conflict(s) unresolved. Run `mokata sync`, then drop.")
        else:
            approved = assume_yes
            if not approved:
                dgate = drop_confirm or _default_drop_confirm
                approved = dgate(f"Migration done. DROP all {len(migrated_items)} item(s) from "
                                 f"the source '{src_tool}'? This is destructive.")
            if approved:
                # drop only what actually migrated — a blocked item stays in the source.
                for it in migrated_items:
                    source.delete(it.id)
                dropped = len(migrated_items)

    dest.close()
    source.close()
    return MigrateResult(migrated=len(migrated_items), dropped=dropped, blocked=blocked,
                         refused=refused, pending=pending, conflicts=conflicts,
                         journaled=to_funnel,
                         from_backend=src_tool, to_backend=to_backend, message="ok")
