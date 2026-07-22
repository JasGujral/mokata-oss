"""SIMP.S2 — the deprecation-shim layer: warn ONCE per repo per channel; delete NOTHING.

bare Claude Code strands data in dead channels when tools change. mokata deprecates with a WARN
plus a one-command, human-approved migration that provably moves every item into the ONE
canonical shape — nothing silently dropped, nothing silently kept working-until-deleted (P22).

This module is the primitive every deprecated channel routes its first-use notice through. The
notice names WHAT is deprecated, the canonical REPLACEMENT, the one-time MIGRATION command, and
WHEN it disappears (0.0.17). It fires at most ONCE per repo per channel — a state marker (an
atomic `O_EXCL` file under `temp_local/`, the `graph_adopt.disclose_first_use` precedent), never a
nag on every call (doc 85 once-per-repo / failures-only discipline). It follows the `*Notice`
shape the `degrade.py` `DegradeNotice`/`note_degraded` family already established.

The DEPRECATED SET (removed at 0.0.17, SIMP.S3–S4 — this stage only WARNS + SHIMS + MIGRATES):
  * obsidian       — the Obsidian memory backend (`memory/backends.ObsidianBackend`)
  * native-memory  — the native-memory backend (`memory/backends.NativeMemoryBackend`)
  * vault          — the artifact vault + the vault session-transport kind
  * memory-share   — the `memory-share.json` export/import channel
  * neo4j          — the Neo4j code-graph backend (WARN-only; graph is derived data — re-index)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from . import TEMP_LOCAL_DIRNAME

# The release that REMOVES every channel in the DEPRECATED SET (doc 84:145/:189 CONFIRMED —
# not 0.0.15/0.0.14, which are stale pre-split labels). Named once so no notice can drift.
REMOVAL_RELEASE = "0.0.17"

# The canonical shape every deprecated MEMORY channel migrates into — the two-modes-one-shape
# store (doc 84 §10 FINALIZED): local SQLite, or the team's ONE Postgres DSN. Never a DSN VALUE.
_CANONICAL_MEMORY = ("The canonical memory store is local SQLite (or your team's one Postgres "
                     "DSN).")

# The ledger kind a first-use notice records when a ledger is supplied — an auditable "this repo
# touched a deprecated channel" event, hash-chained like every other ledger row.
DEPRECATION_LEDGER_KIND = "deprecation_notice"

# The once-per-repo marker directory (under temp_local — run-state, ungated, per-repo-ephemeral,
# exactly like `graph_adopt`'s first-use markers).
_MARKER_DIRNAME = "deprecations"


@dataclass(frozen=True)
class DeprecationNotice:
    """One deprecated channel's notice — WHAT is going, the canonical REPLACEMENT, the one-time
    MIGRATION command (empty for derived data that is re-indexed, not migrated), and WHEN it
    disappears. Static text only: it carries no item content and no DSN value, so it can never
    leak a secret (P23/CM.S1 secret-safety)."""

    channel: str            # the stable channel id (also the migration subcommand)
    what: str               # human name of the deprecated thing
    replacement: str        # the canonical shape that replaces it
    migration: str = ""     # the one-time migration command; "" ⇒ re-index (derived data)
    removal: str = REMOVAL_RELEASE

    def render(self, *, ascii_only: bool = False) -> str:
        glyph = "[deprecated]" if ascii_only else "⚠ deprecated"
        if self.migration:
            how = f"Migrate now with `{self.migration}` (one-time, human-gated)."
        else:
            how = ("No migration needed — the graph is derived data; re-index with your current "
                   "code-graph backend.")
        return (f"{glyph}: the {self.what} is deprecated and will be REMOVED in mokata "
                f"{self.removal}. {self.replacement} {how}")


# The registry — the ONE source of truth for what SIMP.S2 deprecates. The channel id IS the
# `mokata migrate <channel>` subcommand for the four migratable channels.
CHANNELS: Dict[str, DeprecationNotice] = {
    "obsidian": DeprecationNotice(
        channel="obsidian", what="Obsidian memory backend",
        replacement=_CANONICAL_MEMORY, migration="mokata migrate obsidian"),
    "native-memory": DeprecationNotice(
        channel="native-memory", what="native-memory backend",
        replacement=_CANONICAL_MEMORY, migration="mokata migrate native-memory"),
    "vault": DeprecationNotice(
        channel="vault", what="artifact/session vault channel",
        replacement=("Sessions travel over the mode-derived transport (local files, or your "
                     "team's one Postgres DSN)."),
        migration="mokata migrate vault"),
    "memory-share": DeprecationNotice(
        channel="memory-share", what="memory-share.json channel",
        replacement=_CANONICAL_MEMORY, migration="mokata migrate memory-share"),
    "neo4j": DeprecationNotice(
        channel="neo4j", what="Neo4j code-graph backend",
        replacement="The canonical code graph is the embedded AST floor / adopted CRG.",
        migration=""),
}


def deprecation_notice(channel: str) -> DeprecationNotice:
    """The notice for `channel` (always returns for a known channel; `from_`-style contract)."""
    return CHANNELS[channel]


def _marker_path(mokata_dir: str, channel: str) -> str:
    return os.path.join(mokata_dir, TEMP_LOCAL_DIRNAME, _MARKER_DIRNAME,
                        channel.replace("/", "_") + ".marker")


def _stderr(message: str) -> None:
    """Deprecation notices are diagnostics — STDERR, so they never corrupt the stdout/JSON a
    script is parsing (mirrors `degrade._stderr`)."""
    print(message, file=sys.stderr)


def warn_deprecated(channel: str, mokata_dir: str, *, out: Optional[Callable[[str], None]] = None,
                    ledger: Any = None, ascii_only: bool = False) -> bool:
    """Emit `channel`'s deprecation notice ONCE per repo, backed by an atomic `O_EXCL` state
    marker under `<mokata_dir>/temp_local/deprecations/`. Returns True on the firing use, False
    forever after (and on any degrade). `mokata_dir` is the `.mokata` directory.

    Degrade-clean (P8): a marker that can't be written (a broken/read-only temp_local) suppresses
    the notice rather than crashing the read path — a best-effort diagnostic, never a hard failure.
    When a `ledger` is supplied the firing use is recorded (`deprecation_notice`), so a repo's
    first touch of a deprecated channel is auditable long after the line scrolled away."""
    notice = CHANNELS.get(channel)
    if notice is None:
        return False
    marker = _marker_path(mokata_dir, channel)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return False                              # already warned for this repo (the state marker)
    except (OSError, ValueError):
        # degrade-clean: an unwritable/invalid temp_local path (OSError, or a ValueError for an
        # embedded-null path on Linux) suppresses the notice rather than crashing the read path.
        return False
    (out or _stderr)(notice.render(ascii_only=ascii_only))
    if ledger is not None:
        try:
            ledger.record(DEPRECATION_LEDGER_KIND, channel=channel, removal=notice.removal,
                          scope="repo")
        except Exception:                         # noqa: BLE001 — the notice already fired; the
            pass                                  # audit note is best-effort, never the guard
    return True


DEPRECATED_MEMORY_TOOLS = ("obsidian", "native-memory")
DEPRECATED_CHANNELS = tuple(CHANNELS)
