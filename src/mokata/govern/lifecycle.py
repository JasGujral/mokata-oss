"""K6 — clean uninstall / state reset.

Remove mokata's state (`.mokata/` — memory, index, state, audit, and optionally the
committed config) without residue. Reversible-aware: `plan_reset` previews exactly what
will be removed (no side effects), the action is human-gated, and an optional `backup_dir`
moves the state aside so it can be restored instead of destroyed.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .. import MOKATA_DIR

# State subdirectories removed by a reset that keeps the committed config.
STATE_SUBDIRS = ("memory", "state", "audit")


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
    try:
        return input(text + "\nProceed with removal? [y/N] ").strip().lower() \
            in ("y", "yes")
    except EOFError:
        return False


def _remove(path: str, backup_dir: Optional[str]) -> None:
    if backup_dir is not None:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.move(path, os.path.join(backup_dir, os.path.basename(path)))
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def reset_state(root: str, keep_config: bool = False,
                confirm: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False,
                backup_dir: Optional[str] = None) -> ResetResult:
    """Remove mokata state (human-gated). With `backup_dir`, move it aside instead of
    deleting (reversible)."""
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
    return ResetResult(removed=removed, aborted=False, message="removed")
