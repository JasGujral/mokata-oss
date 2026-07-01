"""Stage 66 — cross-platform helpers (Windows / macOS / Linux parity).

Small, dependency-free utilities for the POSIX-only assumptions that would otherwise bite on
Windows. Kept here so the fix is one place + one test target:

  * `basename_any` — a separator-agnostic basename. `os.path.basename` only splits on the
    HOST separator, so on a POSIX host (CI, most contributors) a Windows path `C:\\a\\b` would
    NOT collapse — breaking the machine-path-free bundle invariant for Windows paths.
  * `current_user` — the current username via `getpass.getuser()`, which consults `%USERNAME%`
    on Windows (not just `$USER`/`$LOGNAME` on POSIX). Never raises.

Core stays dependency-free; clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import getpass


def basename_any(path: str) -> str:
    """The last path component, splitting on BOTH `/` and `\\` regardless of host OS.

    `os.path.basename` is host-specific (POSIX never splits on `\\`), so a Windows absolute
    path scrubbed on a Linux/macOS box keeps its whole prefix. This splits on either
    separator so a Windows, POSIX, or mixed path all collapse correctly. Trailing
    separators are ignored; an all-separator string yields ""."""
    stripped = path.rstrip("/\\")
    if not stripped:
        return ""
    # take the tail after the last separator of either kind
    tail = stripped
    for sep in ("/", "\\"):
        tail = tail.rsplit(sep, 1)[-1]
    return tail


def current_user() -> str:
    """The current username, cross-platform. `getpass.getuser()` checks `LOGNAME`, `USER`,
    `LNAME` and `USERNAME` (the Windows variable) in order, so it resolves on every OS.
    Never raises — an unresolvable user degrades to "" (e.g. no env + no pwd entry)."""
    try:
        return getpass.getuser() or ""
    except Exception:
        return ""
