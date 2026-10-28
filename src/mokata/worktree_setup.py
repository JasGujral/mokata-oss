"""P31 SESSION-WORKTREES — what `mokata setup claude` writes so Claude Code's NATIVE worktrees behave
the way mokata's sessions need (rulings by Jas, 2026-10-22). mokata builds no worktree system; it
sets three things Claude Code reads (code.claude.com/docs/en/worktrees):

  * `worktree.baseRef: "head"` in the project's `.claude/settings.json` — a session's worktree
    branches from the CURRENT HEAD (ruling 2), not the remote default branch, so an in-progress
    feature branch is what a new session builds on;
  * `.worktreeinclude` at the repo root — the gitignored files Claude Code copies into every new
    worktree: `.env`, `.env.*`, and `.claude/settings.local.json` (the machine-local hook token the
    IDE bridge / http gates use). NOT `.mokata/`: a worktree resolves the main checkout's ONE store
    (`repo_identity.state_root`), and a copy would fork it;
  * `.claude/worktrees/` in the repo's `.gitignore`, so worktree contents never show as untracked
    files in the main checkout.

Idempotent: a value already in place is reported `ok` and not rewritten. Every row is shown in the
setup plan BEFORE the human confirms (P2) and every write passes the WriteGate's destination layers.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

BASE_REF = "head"
WORKTREEINCLUDE = ".worktreeinclude"
INCLUDE_PATTERNS = (".env", ".env.*", ".claude/settings.local.json")
GITIGNORE_LINE = ".claude/worktrees/"

STATE_WRITE = "write"
STATE_OK = "ok"


@dataclass(frozen=True)
class SetupRow:
    path: str
    what: str
    state: str                      # write | ok


def _lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _base_ref(settings_path: Path) -> Optional[str]:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    wt = data.get("worktree") if isinstance(data, dict) else None
    return wt.get("baseRef") if isinstance(wt, dict) else None


def _missing_includes(root: Path) -> List[str]:
    have = {ln.strip() for ln in _lines(root / WORKTREEINCLUDE)}
    return [p for p in INCLUDE_PATTERNS if p not in have]


def _gitignored(root: Path) -> bool:
    have = {ln.strip().rstrip("/") for ln in _lines(root / ".gitignore")}
    return GITIGNORE_LINE.rstrip("/") in have or "/" + GITIGNORE_LINE.rstrip("/") in have


def plan_rows(root: str, settings_path: Path) -> List[SetupRow]:
    """The three rows, each `write` (will change) or `ok` (already so). Read-only."""
    r = Path(root).resolve()
    rows = [SetupRow(str(settings_path), f'worktree.baseRef = "{BASE_REF}" (session worktrees '
                     f'branch from the current HEAD)',
                     STATE_OK if _base_ref(settings_path) == BASE_REF else STATE_WRITE)]
    missing = _missing_includes(r)
    rows.append(SetupRow(str(r / WORKTREEINCLUDE),
                         "copy gitignored " + ", ".join(INCLUDE_PATTERNS) + " into new worktrees "
                         "(never .mokata/ — worktrees share the main checkout's)",
                         STATE_WRITE if missing else STATE_OK))
    rows.append(SetupRow(str(r / ".gitignore"), f"ignore {GITIGNORE_LINE}",
                         STATE_OK if _gitignored(r) else STATE_WRITE))
    return rows


def render_rows(rows: List[SetupRow]) -> List[str]:
    if not rows:
        return []
    out = ["", "Session worktrees (Claude Code's own; every session works in .claude/worktrees/<name>/):"]
    for row in rows:
        mark = "will set" if row.state == STATE_WRITE else "ok     "
        out.append(f"  [{mark}] {row.what}")
        out.append(f"             {row.path}")
    return out


def _append_lines(path: Path, lines: List[str]) -> None:
    from .atomicfile import atomic_write_text
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    atomic_write_text(str(path), existing + sep + "\n".join(lines) + "\n")


def apply_rows(root: str, settings_path: Path) -> List[str]:
    """Apply every `write` row; returns the touched paths. Each destination passes the WriteGate's
    destination layers first (`harness_setup._gate_config_destination`); the human's consent is the
    setup flow's own confirm, given after the plan showed these rows."""
    from .harness_setup import _gate_config_destination, _load_json, _write_json
    touched: List[str] = []
    r = Path(root).resolve()
    for row in plan_rows(root, settings_path):
        if row.state != STATE_WRITE:
            continue
        p = Path(row.path)
        if p == settings_path:
            data = _load_json(settings_path)
            wt = data.get("worktree") if isinstance(data.get("worktree"), dict) else {}
            data["worktree"] = dict(wt, baseRef=BASE_REF)
            _write_json(settings_path, data)
        elif p.name == WORKTREEINCLUDE:
            _gate_config_destination(p)
            missing = _missing_includes(r)
            head = [] if p.exists() else ["# Gitignored files Claude Code copies into each new "
                                          "session worktree (mokata setup)."]
            _append_lines(p, head + missing)
        else:
            _gate_config_destination(p)
            _append_lines(p, [GITIGNORE_LINE])
        touched.append(str(p))
    return touched


def strip_base_ref(data: dict) -> bool:
    """Unsetup: remove `worktree.baseRef` when it is exactly what setup wrote (`"head"`), and the
    `worktree` object if that leaves it empty. A user's other `worktree.*` keys survive."""
    wt = data.get("worktree")
    if not isinstance(wt, dict) or wt.get("baseRef") != BASE_REF:
        return False
    rest = {k: v for k, v in wt.items() if k != "baseRef"}
    if rest:
        data["worktree"] = rest
    else:
        data.pop("worktree", None)
    return True


__all__ = ["strip_base_ref", "BASE_REF", "WORKTREEINCLUDE", "INCLUDE_PATTERNS", "GITIGNORE_LINE", "SetupRow",
           "plan_rows", "render_rows", "apply_rows"]
