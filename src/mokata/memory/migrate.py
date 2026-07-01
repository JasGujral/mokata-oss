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

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..govern import WriteGate, WriteRequest
from .backends import MemoryBackend, build_postgres_backend
from .store import build_backend

# Backends migrate can move between (each a MemoryBackend with file/db storage).
SUPPORTED = ("sqlite", "obsidian", "postgres")


class MigrateError(Exception):
    """Raised when a source/destination backend can't be built for a migration."""


@dataclass
class MigrateResult:
    migrated: int = 0
    dropped: int = 0
    blocked: int = 0                 # items hard-blocked at the gate (secret) — not migrated
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
        if self.dropped:
            line += f" Dropped {self.dropped} from the source."
        return line


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
    except Exception:
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
                   ledger: Any = None,
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

    # Stage 37R (H1): the source content is UNTRUSTED (an external Obsidian/Postgres store), so
    # every per-item write goes through the universal WriteGate — secrets in subject/value are
    # HARD-BLOCKED (not migrated) and each commit is recorded in the audit ledger when provided.
    gate = WriteGate(ledger=ledger)
    migrated_items: List[Any] = []
    blocked = 0
    for it in items:
        outcome = gate.submit(
            WriteRequest("memory", f"memory:{it.subject}",
                         content=f"{it.subject}\n{it.value}", actor="migrate"),
            commit=lambda it=it: dest.put(it),   # upsert by id -> idempotent, provenance kept
            assume_yes=True)                     # migration already human-approved above
        if outcome.committed:
            migrated_items.append(it)
        elif outcome.findings:
            blocked += 1                         # secret — hard-blocked, left in the source

    dropped = 0
    if drop_source:
        if same_store:
            emit("migrate: refusing --drop-source on a self-migration (would delete the "
                 "just-written data).")
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
                         from_backend=src_tool, to_backend=to_backend, message="ok")
