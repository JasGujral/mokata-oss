"""0.0.27 P11 — `path:line:col` locators, one parser for every door into the IDE.

`mokata ide src/a.py:42:7`, the palette (a pasted `src/a.py:42:7`), and the in-IDE cites that P7 and
P46 print all mean the same thing, so they share this one rule:

  * `path`, `path:LINE` and `path:LINE:COL` (1-based; 0 reads as 1, a missing line is line 1);
  * a Windows drive letter is part of the path, not a separator: `C:\\x\\y.py:3:1` is the file
    `C:\\x\\y.py`, line 3, column 1 — and `C:\\x\\y.py` alone is a path with no line;
  * a trailing `:` (compiler style `a.py:3:1:`) is dropped;
  * a path that is not inside the workspace is REFUSED (`PathRefused`), never opened: an absolute
    path must resolve inside the workspace root, a relative one is read from the caller's cwd, and a
    drive-letter path on a machine whose paths have no drives is outside every workspace.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from typing import NamedTuple, Optional

from .nav import PathRefused

_LOC = re.compile(r"^(?P<path>.+?)(?::(?P<line>\d{1,9})(?::(?P<col>\d{1,9}))?)?$", re.DOTALL)
_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")


class Locator(NamedTuple):
    path: str
    line: int
    col: Optional[int]


def parse(text: str) -> Locator:
    """`path[:line[:col]]` -> Locator. Never touches the disk."""
    if not isinstance(text, str):
        raise PathRefused("no path")
    s = text.strip()
    if s.endswith(":") and len(s) > 2:
        s = s.rstrip(":")
    if not s or "\0" in s:
        raise PathRefused("no path")
    m = _LOC.match(s)
    if not m:
        raise PathRefused("no path")
    line = int(m.group("line")) if m.group("line") else 1
    col = int(m.group("col")) if m.group("col") else None
    return Locator(m.group("path"), max(1, line), max(1, col) if col is not None else None)


def resolve(root: str, text: str, cwd: Optional[str] = None) -> Locator:
    """Parse `text` and return it with `path` as the workspace-relative '/' name, or raise
    PathRefused: outside the workspace, or not a file there. `cwd` is where a relative path is read
    from (the CLI's cwd; default: the root)."""
    from .snapshots import rel_path
    loc = parse(text)
    path = loc.path
    if _DRIVE.match(path) and os.name != "nt":
        raise PathRefused(f"{path} is a Windows drive path — outside this workspace")
    if re.match(r"^[\\/]{2}", path) and os.name != "nt":
        raise PathRefused(f"{path} is outside this workspace")
    rel = rel_path(root, path, cwd)
    if rel is None:
        # 0.0.27 SEC-HARDEN — the words come from the one rule that refused: git's store, mokata's
        # record, an alias of either, else "outside" (P11 named only the `.mokata` case and called
        # `.git/config` "outside this workspace").
        from .snapshots import refusal
        raise PathRefused(refusal(root, path, f"{path} is outside this workspace ({root}) — "
                                              "mokata ide opens files inside the workspace only",
                                  cwd))
    if not os.path.isfile(os.path.join(root, *rel.split("/"))):
        raise PathRefused(f"{rel} is not a file in this workspace{_bad_suffix_hint(root, rel)}")
    return Locator(rel, loc.line, loc.col)


def _bad_suffix_hint(root: str, rel: str) -> str:
    """`a.py:3:x` / `a.py:-3` are not locators, so the whole text is read as a path and is not a
    file. When the text before some `:` IS a file INSIDE the workspace, say what the suffix should
    have been. A head that resolves outside (a symlink out) earns no hint: saying "that file exists"
    about a target beyond the workspace would be an oracle."""
    from .snapshots import rel_path
    head = rel
    while ":" in head:
        head, _sep, _tail = head.rpartition(":")
        full = os.path.join(root, *head.split("/")) if head else ""
        if head and os.path.isfile(full) and rel_path(root, full) is not None:
            rest = rel[len(head):]
            if any(seg.isdigit() and len(seg) > 9 for seg in rest.split(":")[1:]):
                return (f" — but {head} is; line and column numbers have at most 9 digits "
                        f"(got '{rest}')")
            return (f" — but {head} is; a location is path:LINE or path:LINE:COL with whole "
                    f"numbers (got '{rest}')")
    return ""


__all__ = ["Locator", "parse", "resolve"]
