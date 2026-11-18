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

Everything wire writes into a FILE THE USER MAY ALSO OWN is marked, so `strip_rows` (unsetup) removes
exactly what mokata wrote and never a user's line: `.worktreeinclude` carries `INCLUDE_HEADER` above
mokata's patterns (the header is written whether mokata creates the file or appends to the user's),
and `.gitignore` carries `GITIGNORE_MARK` directly above `.claude/worktrees/`. When git already
ignores `.claude/worktrees/` (a user's own line, or `.claude/` ignored) nothing is appended, so
there is nothing to remove.

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
#: The marks. Exact text: a user's comment that merely resembles them is not removed.
INCLUDE_HEADER = "# Gitignored files Claude Code copies into each new session worktree (mokata setup)."
GITIGNORE_MARK = "# mokata: session worktrees (removed by unsetup)"
#: 0.0.29 C1 verify L1/L2 — what wire did to the FILE, carried on its own mark so unwire can undo
#: exactly that: `NEW_FILE` = wire created it (unwire deletes it once mokata's block is gone);
#: `EOL_ADDED` = the file had no final newline and wire added one before its block (unwire takes
#: it back). A bare mark = appended to a file that already ended cleanly (an empty file included).
NEW_FILE = " [file created by mokata setup]"
EOL_ADDED = " [mokata setup added the newline above]"

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
    """Does git already ignore `.claude/worktrees/`? Asked of git itself (`check-ignore`), so a
    `.claude/` ignore, `.git/info/exclude` or a global excludes file count. Only when git cannot
    answer (not a repo, no git) does it fall back to comparing the literal `.gitignore` line."""
    from .session_worktrees import _git
    code, _out = _git(str(root), "check-ignore", "-q", GITIGNORE_LINE + "x")
    if code in (0, 1):
        return code == 0
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


def _append_lines(path: Path, mark: str, lines: List[str]) -> None:
    """Append `mark` + `lines` keeping the user's bytes exactly: read as UTF-8 WITHOUT newline
    translation, write back WITHOUT it, and end the new lines with the file's own line ending (CRLF
    if the file uses CRLF). What wire did to the file rides on the mark (`NEW_FILE`, `EOL_ADDED`),
    so `strip_rows` hands back the exact bytes: an absent file absent, an empty file empty, a file
    with no final newline without one."""
    from .atomicfile import atomic_write_text
    try:
        existing = path.read_bytes().decode("utf-8")
        created = False
    except FileNotFoundError:
        existing, created = "", True
    eol = "\r\n" if "\r\n" in existing else "\n"
    sep = "" if not existing or existing.endswith("\n") else eol
    mark += NEW_FILE if created else (EOL_ADDED if sep else "")
    atomic_write_text(str(path), existing + sep + eol.join([mark] + lines) + eol, newline="")


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
            _append_lines(p, INCLUDE_HEADER, missing)     # the header IS the mark, always
        else:
            _gate_config_destination(p)
            _append_lines(p, GITIGNORE_MARK, [GITIGNORE_LINE])
        touched.append(str(p))
    return touched


def _eol_free(line: bytes) -> str:
    return line.rstrip(b"\r\n").decode("utf-8", errors="replace")


def _mark_flags(line: bytes, mark: str) -> Optional[str]:
    """The suffix (`""`, `NEW_FILE`, `EOL_ADDED`) when `line` is exactly one of `mark`'s forms,
    else None. Exact text: a user's comment that merely resembles a mark is not one."""
    text = _eol_free(line)
    for suffix in ("", NEW_FILE, EOL_ADDED):
        if text == mark + suffix:
            return suffix
    return None


@dataclass
class _Stripped:
    data: bytes
    created: bool = False           # wire created the file (a NEW_FILE mark was removed)


def _strip_blocks(data: bytes, mark: str, is_ours) -> _Stripped:
    """Drop every mokata block — a mark line and the lines right below it that `is_ours(i, text)`
    claims — and undo what the mark says wire did: a newline wire added to the line above is taken
    back when the block was the end of the file (with lines after it, they need that newline)."""
    out: List[bytes] = []
    lines = data.splitlines(keepends=True)
    created = False
    i = 0
    while i < len(lines):
        flag = _mark_flags(lines[i], mark)
        if flag is None:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and is_ours(j - i - 1, _eol_free(lines[j])):
            j += 1
        if j == i + 1 and mark == GITIGNORE_MARK:      # a bare mark with no line: not ours
            out.append(lines[i])
            i += 1
            continue
        created = created or flag == NEW_FILE
        if flag == EOL_ADDED and j == len(lines) and out:
            last = out[-1]
            out[-1] = last[:-2] if last.endswith(b"\r\n") else last.rstrip(b"\n")
        i = j
    return _Stripped(b"".join(out), created)


def _without_include_block(data: bytes) -> _Stripped:
    """Drop every mokata block: the header line and the contiguous lines right below it that are
    mokata's own patterns. A user's lines (above, below, or between blocks) survive."""
    return _strip_blocks(data, INCLUDE_HEADER, lambda _k, text: text in INCLUDE_PATTERNS)


def _without_gitignore_block(data: bytes) -> _Stripped:
    """Drop the marker line plus the `.claude/worktrees/` line DIRECTLY below it. An unmarked
    `.claude/worktrees/` line (the user's own) is never touched."""
    return _strip_blocks(data, GITIGNORE_MARK, lambda k, text: k == 0 and text == GITIGNORE_LINE)


def strip_rows(root: str, *, apply: bool = True) -> List[str]:
    """Unsetup: remove ONLY what `apply_rows` marked in `.worktreeinclude` and `.gitignore`, working
    on bytes so CRLF and every other user byte survive exactly. A file wire CREATED (its mark says
    so) is deleted once nothing but mokata's block was in it; any other file is rewritten without
    the block — an empty file the user had stays an empty file. Returns the paths changed (`apply=False`: the paths that WOULD change, for
    the unsetup plan the human approves). A symlinked file, or one that is not UTF-8, is left alone.
    Each destination passes the WriteGate's destination layers first."""
    from .atomicfile import atomic_write_text
    from .harness_setup import _gate_config_destination
    r = Path(root).resolve()
    changed: List[str] = []
    for name, strip in ((WORKTREEINCLUDE, _without_include_block),
                        (".gitignore", _without_gitignore_block)):
        p = r / name
        if p.is_symlink() or not p.is_file():
            continue
        try:
            before = p.read_bytes()
            stripped = strip(before)
            after = stripped.data
            text = after.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if after == before:
            continue
        changed.append(str(p))
        if not apply:
            continue
        _gate_config_destination(p)
        if not after and stripped.created:
            p.unlink()                  # wire created it; an empty file the user had stays
        else:
            atomic_write_text(str(p), text, newline="")
    return changed


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


__all__ = ["strip_base_ref", "strip_rows", "INCLUDE_HEADER", "GITIGNORE_MARK", "NEW_FILE", "EOL_ADDED", "BASE_REF", "WORKTREEINCLUDE", "INCLUDE_PATTERNS", "GITIGNORE_LINE", "SetupRow",
           "plan_rows", "render_rows", "apply_rows"]
