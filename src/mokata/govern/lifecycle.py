"""K6 — clean uninstall / state reset.

Remove mokata's state (`.mokata/` — memory, index, state, audit, and optionally the
committed config) without residue. Reversible-aware: `plan_reset` previews exactly what
will be removed (no side effects), the action is human-gated, and an optional `backup_dir`
moves the state aside so it can be restored instead of destroyed.
"""

from __future__ import annotations

from ..prompt import read_yes_no

import json
import os
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME

# State subdirectories removed by a reset that keeps the committed config. `temp_local/`
# holds the runtime split (Stage 24D — memory store, state, audit, caches, index); the
# bare memory/state/audit names are kept so a legacy pre-24D layout is also cleaned.
STATE_SUBDIRS = (TEMP_LOCAL_DIRNAME, "memory", "state", "audit")

# KB.S1 — the removal TOMBSTONE. The audit ledger lives under `.mokata/temp_local/audit/`, so a
# reset that removes .mokata deletes the very ledger that would record the delete (the UNSETUP hard
# part). The record must therefore live somewhere the removal cannot erase: the USER's own profile
# (`~/.mokata/`, outside every repo — the same home `knowledge/user_prefs` uses). It carries repo
# IDENTITY + when + actor only — never any repo content — so a wiped repo still leaves an auditable
# trace that mokata was here and was removed (P7/P22).
_TOMBSTONE_FILE = "removals.json"


@dataclass
class ResetPlan:
    root: str
    targets: List[str] = field(default_factory=list)
    keep_config: bool = False


@dataclass
class ResetResult:
    removed: List[str] = field(default_factory=list)
    aborted: bool = False
    message: str = ""


def plan_reset(root: str, keep_config: bool = False) -> ResetPlan:
    """List what a reset would remove. Pure — no side effects."""
    mdir = os.path.join(root, MOKATA_DIR)
    if keep_config:
        targets = [os.path.join(mdir, sub) for sub in STATE_SUBDIRS
                   if os.path.exists(os.path.join(mdir, sub))]
    else:
        targets = [mdir] if os.path.exists(mdir) else []
    return ResetPlan(root=root, targets=targets, keep_config=keep_config)


def _default_confirm(text: str) -> bool:
    return read_yes_no(text, "Proceed with removal?")


def _remove(path: str, backup_dir: Optional[str]) -> None:
    if backup_dir is not None:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _tombstone_path(user_home: Optional[str] = None) -> str:
    base = user_home or os.path.expanduser("~")
    return os.path.join(base, ".mokata", _TOMBSTONE_FILE)


def _write_tombstone(root: str, removed: List[str], keep_config: bool, actor: str,
                     backed_up: bool, user_home: Optional[str] = None) -> None:
    """Append a removal tombstone to the USER's profile (KB.S1) — the record that survives the
    deletion of the repo's own ledger. Repo IDENTITY (abspath) + when + actor + how much was
    removed; NO repo content. Best-effort: a tombstone that can't land never fails the reset (the
    `user_prefs.record_graph_decline` philosophy)."""
    entry = {
        "repo": os.path.abspath(root),
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "removed_count": len(removed),
        "keep_config": keep_config,
        "backed_up": backed_up,
    }
    path = _tombstone_path(user_home)
    try:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, list):
                data = []
        except (OSError, ValueError):
            data = []
        data.append(entry)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        # A tombstone that can't be written is non-fatal — the removal already happened; on a
        # broken/unwritable home we lose only the trace, never the reset itself.
        pass


def reset_state(root: str, keep_config: bool = False,
                confirm: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False,
                backup_dir: Optional[str] = None,
                actor: str = "cli",
                user_home: Optional[str] = None) -> ResetResult:
    """Remove mokata state (human-gated). With `backup_dir`, move it aside instead of
    deleting (reversible).

    KB.S1: the removal is recorded in a user-scoped tombstone (`~/.mokata/removals.json`) that
    SURVIVES the deletion of the repo's own audit ledger. `actor` names the surface (cli / mcp);
    `user_home` is injectable for tests. Consent is unchanged — the `read_yes_no` confirm below,
    fail-closed off a TTY."""
    plan = plan_reset(root, keep_config)
    if not plan.targets:
        return ResetResult(removed=[], aborted=False, message="nothing to remove")

    if not assume_yes:
        gate = confirm or _default_confirm
        preview = "mokata reset will remove:\n  " + "\n  ".join(plan.targets)
        if not gate(preview):
            return ResetResult(removed=[], aborted=True, message="aborted by user")

    removed: List[str] = []
    for target in plan.targets:
        _remove(target, backup_dir)
        removed.append(target)
    # Record the removal AFTER it has happened, in the user profile the removal cannot erase.
    _write_tombstone(root, removed, keep_config, actor,
                     backed_up=backup_dir is not None, user_home=user_home)
    return ResetResult(removed=removed, aborted=False, message="removed")
