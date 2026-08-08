"""SIMP.S2 — one-time, human-gated migration of a DEPRECATED channel into the canonical shape.

`mokata migrate <channel>` moves a deprecated channel's data into the two-modes-one-shape store,
reusing the EXISTING gated write path — never a second one (P2):

  * obsidian / native-memory  → `memory.migrate_memory` (backend → the canonical memory store):
    every item lands via the WriteGate with provenance, secrets hard-blocked, idempotent upsert.
  * memory-share.json         → `memory.import_memory` (the gated merge into the resolved store).
  * vault (session bundles)   → re-homed from the deprecated `vault` transport onto the mode-
    derived canonical transport (SIMP.S1), gated + idempotent + non-destructive.

Every migration is NON-DESTRUCTIVE (the source is left in place — deletion is the human's choice,
at 0.0.17), PREVIEWABLE (counts + a sample, no writes), and ONE-TIME (a state marker makes a
re-run a no-op: "already migrated"). The design-artifact vault (brainstorm/spec markdown) has no
canonical memory store to fold into; it is guarded by the DB.S9 read-shim and left for the human.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from . import MOKATA_DIR, TEMP_LOCAL_DIRNAME
from .deprecation import DEPRECATED_MEMORY_TOOLS

CHANNELS = ("obsidian", "native-memory", "memory-share", "vault")
_SAMPLE = 5                                    # how many keys/tags a preview shows
_MARKER_DIRNAME = "deprecations"


# ------------------------------------------------------------------------- the one-time marker
def _marker_path(mokata_dir: str, channel: str) -> str:
    return os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, _MARKER_DIRNAME,
                        f"migrated-{channel}.json")


def _read_marker(mokata_dir: str, channel: str) -> Optional[dict]:
    try:
        with open(_marker_path(mokata_dir, channel), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_marker(mokata_dir: str, channel: str, count: int) -> None:
    p = _marker_path(mokata_dir, channel)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"channel": channel, "count": count,
                       "migrated_at": datetime.now(timezone.utc).isoformat()}, fh)
    except OSError:
        pass                                   # the migration succeeded; the marker is best-effort


# ------------------------------------------------------------------------------ canonical target
def _canonical_memory_tool(surface: Any) -> str:
    """The non-deprecated memory tool this repo migrates INTO — the first `sqlite`/`postgres` in
    the committed chain, defaulting to the guaranteed SQLite floor. Never a deprecated backend."""
    from .manifest import ManifestError
    try:
        chain = surface.router.manifest.fallback_order("memory_store")
    except (ManifestError, AttributeError):
        # The honest raisers: a broken/unknown-capability manifest (ManifestError) or a duck-typed
        # surface with no `.router`/`.manifest` (AttributeError). SQLite is the guaranteed canonical
        # floor — always a valid, non-deprecated target — so an unreadable chain degrades to it.
        chain = []
    for tool in chain:
        if tool in ("sqlite", "postgres") and tool not in DEPRECATED_MEMORY_TOOLS:
            return tool
    return "sqlite"


# --------------------------------------------------------------------------------- source reads
def _obsidian_backend(surface: Any):
    from .memory.backends import ObsidianBackend
    cfg = surface.manifest.tool_config("obsidian")
    vault = cfg.get("vault")
    if not vault:
        vault = os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, "memory", "vault")
    return ObsidianBackend(os.path.expanduser(vault))


def _memory_share_path(surface: Any, file: str) -> str:
    from .memory import MEMORY_SHARE_FILENAME
    return file or os.path.join(surface.root, MOKATA_DIR, MEMORY_SHARE_FILENAME)


def _source_subjects(surface: Any, channel: str, client: Any, file: str) -> List[str]:
    """The subjects/tags present in the deprecated channel (READ-ONLY — for the preview)."""
    if channel == "obsidian":
        return [i.subject for i in _obsidian_backend(surface).all()]
    if channel == "native-memory":
        if client is None:
            return []
        from .memory.backends import NativeMemoryBackend
        return [i.subject for i in NativeMemoryBackend(client).all()]
    if channel == "memory-share":
        from .memory import load_memory_share
        path = _memory_share_path(surface, file)
        if not os.path.exists(path):
            return []
        data = load_memory_share(path)
        return [d.get("subject", "") for d in data.get("items", [])]
    if channel == "vault":
        return _vault_tags(surface)
    return []


def _vault_tags(surface: Any) -> List[str]:
    from .session_transport import VaultTransport
    try:
        return VaultTransport(surface.root).list_tags()
    except OSError:
        return []


# ------------------------------------------------------------------------------------ the plan
@dataclass
class ChannelMigratePlan:
    """Previewed migration (no side effects, `*Plan` per doc 85 §3): what WOULD move, and where."""

    channel: str
    count: int
    sample: List[str] = field(default_factory=list)      # up to _SAMPLE keys/tags (NEVER values)
    target: str = ""
    already_migrated: bool = False

    def render(self, *, ascii_only: bool = False) -> str:
        if self.already_migrated:
            return (f"migrate {self.channel}: already migrated for this repo — nothing to do "
                    f"(re-run with --force to migrate again).")
        if self.count == 0:
            return f"migrate {self.channel}: nothing to migrate (the channel is empty)."
        shown = ", ".join(self.sample)
        more = "" if self.count <= len(self.sample) else f", … (+{self.count - len(self.sample)})"
        return (f"migrate {self.channel}: {self.count} item(s) → {self.target} "
                f"(human-gated, non-destructive). e.g. {shown}{more}")


def plan_channel_migration(surface: Any, channel: str, *, client: Any = None,
                           file: str = "") -> ChannelMigratePlan:
    """Preview a channel migration: counts + a sample of KEYS (never values — secret-safe), and
    whether it has already run for this repo. READ-ONLY: writes nothing."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown migration channel '{channel}' (one of {CHANNELS})")
    subjects = _source_subjects(surface, channel, client, file)
    target = "the canonical transport" if channel == "vault" else _canonical_memory_tool(surface)
    return ChannelMigratePlan(
        channel=channel, count=len(subjects), sample=subjects[:_SAMPLE], target=target,
        already_migrated=_read_marker(surface.mokata_dir, channel) is not None)


# ---------------------------------------------------------------------------------- the result
@dataclass
class ChannelMigrateResult:
    channel: str
    migrated: int = 0
    already_migrated: bool = False
    aborted: bool = False
    source_intact: bool = True                 # SIMP.S2 is NEVER destructive
    message: str = ""

    def render(self) -> str:
        return self.message or (f"migrate {self.channel}: {self.migrated} item(s) migrated.")


def run_channel_migration(surface: Any, channel: str, *,
                          confirm: Optional[Callable[[str], bool]] = None,
                          assume_yes: bool = False, client: Any = None, file: str = "",
                          force: bool = False, ledger: Any = None,
                          out: Optional[Callable[[str], None]] = None) -> ChannelMigrateResult:
    """Run the one-time gated migration for `channel`. Idempotent (a marker makes a re-run a
    no-op), non-destructive, and routed through the EXISTING gated write path per channel."""
    emit = out or print
    if channel not in CHANNELS:
        return ChannelMigrateResult(channel=channel, aborted=True,
                                    message=f"unknown migration channel '{channel}'")
    if not force and _read_marker(surface.mokata_dir, channel) is not None:
        marker = _read_marker(surface.mokata_dir, channel) or {}
        return ChannelMigrateResult(
            channel=channel, already_migrated=True, migrated=0,
            message=(f"migrate {channel}: already migrated ({marker.get('count', 0)} item(s) on "
                     f"{marker.get('migrated_at', '?')}) — nothing to do. --force to re-run."))

    if channel in ("obsidian", "native-memory"):
        res = _migrate_memory_channel(surface, channel, confirm=confirm, assume_yes=assume_yes,
                                      client=client, ledger=ledger, emit=emit)
    elif channel == "memory-share":
        res = _migrate_memory_share(surface, confirm=confirm, assume_yes=assume_yes,
                                    file=file, ledger=ledger, emit=emit)
    else:  # vault
        res = _migrate_vault(surface, confirm=confirm, assume_yes=assume_yes,
                             force=force, ledger=ledger, emit=emit)

    if not res.aborted:
        _write_marker(surface.mokata_dir, channel, res.migrated)
    return res


# ------------------------------------------------------------------------ per-channel migrators
def _migrate_memory_channel(surface: Any, channel: str, *, confirm, assume_yes, client, ledger,
                            emit) -> ChannelMigrateResult:
    from .memory import migrate_memory
    if channel == "native-memory" and client is None:
        return ChannelMigrateResult(
            channel=channel, aborted=True, migrated=0,
            message=("migrate native-memory: no wired client — nothing to read. (native-memory is "
                     "an external store; without its client there is no data to migrate, and "
                     "reading the local floor would migrate the wrong data.)"))
    target = _canonical_memory_tool(surface)
    clients = {"native-memory": client} if client is not None else None
    mres = migrate_memory(surface, to_backend=target, from_backend=channel,
                          confirm=confirm, assume_yes=assume_yes, ledger=ledger,
                          clients=clients, out=emit)
    if mres.aborted:
        return ChannelMigrateResult(channel=channel, aborted=True, migrated=0,
                                    message=mres.render())
    return ChannelMigrateResult(channel=channel, migrated=mres.migrated, message=mres.render())


def _migrate_memory_share(surface: Any, *, confirm, assume_yes, file, ledger,
                          emit) -> ChannelMigrateResult:
    from .memory import MemoryStore, import_memory, load_memory_share
    path = _memory_share_path(surface, file)
    if not os.path.exists(path):
        return ChannelMigrateResult(channel="memory-share", aborted=True, migrated=0,
                                    message=f"migrate memory-share: no share file at {path}")
    try:
        data = load_memory_share(path)
    except (OSError, ValueError) as exc:
        return ChannelMigrateResult(channel="memory-share", aborted=True, migrated=0,
                                    message=f"migrate memory-share: cannot read {path}: {exc}")
    store = MemoryStore.from_surface(surface)
    # Never fold data INTO a store that is itself deprecated — switch the resolved store to
    # sqlite/postgres first (P16 honesty; the canonical shape is the whole point of migrating).
    if store.backend.name in DEPRECATED_MEMORY_TOOLS:
        store.close()
        return ChannelMigrateResult(
            channel="memory-share", aborted=True, migrated=0,
            message=(f"migrate memory-share: the resolved memory store is '{store.backend.name}', "
                     f"itself deprecated. Point memory_store at sqlite/postgres, then migrate."))
    ires = import_memory(store, data, confirm=confirm, assume_yes=assume_yes, ledger=ledger)
    store.close()
    migrated = len(ires.added) + len(ires.resolved)
    aborted = ires.aborted or (migrated == 0 and bool(ires.declined))
    return ChannelMigrateResult(channel="memory-share", migrated=migrated, aborted=aborted,
                                message=ires.render())


def _migrate_vault(surface: Any, *, confirm, assume_yes, force, ledger,
                   emit) -> ChannelMigrateResult:
    """Re-home portable session bundles from the deprecated `vault` transport onto the mode-derived
    canonical transport (SIMP.S1). NON-destructive (the vault copies stay), idempotent (a tag
    already present in the target is skipped). Each bundle is integrity-verified before it is
    copied — a corrupt one is refused, never silently re-homed.

    GATED THROUGH THE UNIVERSAL WriteGate (0.0.17 stage 17, VAULT-CHANNEL-UNSCANNED). The bundle
    bytes come off a store nobody has re-read since they were captured, and until this stage they
    were copied with a raw `dst.write_bundle` — no secret scan on a durable write, the one thing
    I2 exists to make impossible. The gate is REUSED, never re-implemented (I2/P2).

    ONE HUMAN DECISION, ONE SCAN PER BUNDLE, and the split is deliberate. The scan is per-CONTENT,
    so the gate must see each bundle's own bytes; the DECISION is per-BATCH and is taken once at
    the consent below. Each per-bundle submit therefore carries that decision forward rather than
    re-prompting N times, which would be a UX regression dressed as governance — as `human_approved`
    when a human actually answered, and as `assume_yes` when `--yes` meant nobody was asked. A
    bundle the gate refuses is announced and skipped individually; the rest of the batch continues,
    matching the integrity refusal directly above it."""
    from . import session_bundle as SB
    from .govern import WriteGate, WriteRequest
    from .govern.trust import CLI_SURFACE
    from .session_transport import make_transport, transport_kind_for_mode, VaultTransport
    root = surface.root
    if ledger is None:
        # P7 — the record is UNCONDITIONAL, and this is how. It used to sit under
        # `if ledger is not None:`, so a caller with no ledger re-homed durable bundles leaving
        # nothing behind at all. That is a REACHABLE production state, not a test-only one: the one
        # production caller (`cli_commands/migrate.py:34`) passes `_ledger_for(path)`, which
        # degrades to None on an unreadable/uninitialized repo. Refuse LOUDLY rather than write
        # unrecorded — an unauditable durable write is worse than a migration that did not happen,
        # and the vault copies are still there to migrate once the ledger opens.
        return ChannelMigrateResult(
            channel="vault", aborted=True, migrated=0,
            message="migrate vault: no audit ledger for this repo — REFUSING to re-home session "
                    "bundles that would leave no record. Nothing was moved (the vault copies are "
                    "untouched). Fix .mokata/ so the ledger opens, then re-run.")
    src = VaultTransport(root)
    tags = src.list_tags()
    if not tags:
        return ChannelMigrateResult(channel="vault", migrated=0,
                                    message="migrate vault: no session bundles in the vault "
                                            "transport — nothing to re-home.")
    from .session_transport import SessionTransportUnavailable
    try:
        dst = make_transport(transport_kind_for_mode(root), root)
    except SessionTransportUnavailable as exc:
        # The typed degrade of BOTH `transport_kind_for_mode` (mode unknowable) and `make_transport`
        # (no DSN for the postgres leg). The refusal is ANNOUNCED in the returned message, never a
        # silent write-nothing: nothing is re-homed and the user is told why.
        return ChannelMigrateResult(channel="vault", aborted=True, migrated=0,
                                    message=f"migrate vault: cannot resolve the canonical "
                                            f"transport ({exc}) — nothing re-homed.")
    if dst.name == src.name:
        return ChannelMigrateResult(channel="vault", aborted=True, migrated=0,
                                    message="migrate vault: the canonical transport IS the vault "
                                            "here — pass an explicit target / switch mode first.")
    if not assume_yes:
        gate = confirm or (lambda _t: False)
        if not gate(f"Re-home {len(tags)} session bundle(s) from the vault transport to "
                    f"'{dst.name}'? The vault copies are left in place."):
            return ChannelMigrateResult(channel="vault", aborted=True, migrated=0,
                                        message="migrate vault: declined at the human gate.")
    # NOT named `gate`: that name is already the batch CONSENT callable four lines up, and the two
    # are different things — one asks the human once, this one governs every bundle's bytes.
    write_gate = WriteGate(ledger=ledger)
    migrated = 0
    for tag in tags:
        blob = src.read_bundle(tag)
        if blob is None:
            continue
        try:
            SB.verify_bundle(json.loads(blob))     # corrupt → refuse this one (never re-home it)
        except (ValueError, SB.SessionBundleError) as exc:
            emit(f"migrate vault: bundle '{tag}' failed its integrity check ({exc}) — REFUSED "
                 f"(not re-homed).")
            continue
        if dst.read_bundle(tag) is not None:
            continue                               # idempotent: already present in the target
        # The write now executes INSIDE the gate's commit callable — that is what makes this call
        # site GATED in the register's sense, and what expires the declared exception that named it
        # as `write_bundle`'s third, ungated caller. The secret scan runs per bundle and still
        # hard-blocks regardless of either flag below.
        #
        # THE TWO FLAGS ARE NOT INTERCHANGEABLE, and `gate.py:146` says so: `human_approved` means
        # "an explicit human decision on THIS write was obtained out-of-band"; `assume_yes` is a
        # STAND-IN ("nobody is here to ask; proceed"). Under `--yes` the batch consent above is
        # SKIPPED entirely — no human was asked anything — so claiming a human decision there is a
        # lie the gate is entitled to believe. It is inert today (this gate is built with no trust,
        # so the propose-only rung never engages), but `req.tool`/`req.surface` are already stamped:
        # the day the trust dial is threaded (SI.4-b), a hardcoded True would satisfy propose-only
        # with no human in the loop. So the batch decision rides in on `human_approved` only when a
        # human actually made one, and the stand-in rides in as itself.
        outcome = write_gate.submit(
            WriteRequest("config", dst.location(tag), content=blob, actor="migrate",
                         tool="migrate_vault", surface=CLI_SURFACE),
            commit=(lambda tag=tag, blob=blob: dst.write_bundle(tag, blob)),
            human_approved=not assume_yes, assume_yes=assume_yes)
        if not outcome.committed:
            # ANNOUNCED, never a silent skip — the same contract as the integrity refusal above.
            # `outcome.reason` is the gate's own shared block builder (`render_block`), the surface
            # the secret-guard hook prints, so the literal is never echoed back to the terminal.
            emit(f"migrate vault: bundle '{tag}' was REFUSED at the write gate — not re-homed. "
                 f"{outcome.reason}")
            continue
        migrated += 1
        ledger.record("migrate_channel", channel="vault", tag=tag, to=dst.name)
    return ChannelMigrateResult(channel="vault", migrated=migrated,
                                message=f"migrate vault: re-homed {migrated} session bundle(s) to "
                                        f"'{dst.name}' (vault copies left in place).")
