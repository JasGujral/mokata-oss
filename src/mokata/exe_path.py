"""Where an executable a user chose is found — and never where the REPO could plant one (0.0.27).

Moved here from `ide/lsp.py` (P24's re-verification R2) at 0.0.27 P21, because the gated merge
needs the same answer for `git` from the CLI and MCP surfaces, which must not import the `[ide]`
extra. One implementation; `ide/lsp.py` imports it.

  * `path_dirs` — the user's PATH as ABSOLUTE directories, never the current directory: CPython's
    `shutil.which` (and Windows' CreateProcess) search the cwd first, and `mokata ide`'s cwd is the
    repo, so the repo could answer for a name the user chose.
  * `which_in` — a name as an executable file in exactly the given directories; never a
    `.cmd`/`.bat` shim; optionally never a hit whose realpath lies inside a given directory.

Pure functions; the platform and PATHEXT are injectable so a test can fake Windows.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence


def path_dirs(environ: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> List[str]:
    """The user's PATH as ABSOLUTE directories: an empty or relative entry and the current
    directory are dropped (re-verification R2: CPython's `shutil.which` on Windows searches the
    cwd first even with `path=`, and `mokata ide`'s cwd is the repo — so the repo could choose)."""
    env = os.environ if environ is None else environ
    here = os.path.normcase(os.path.realpath(cwd or os.getcwd()))
    out = []
    for d in (env.get("PATH") or "").split(os.pathsep):
        d = d.strip('"')
        if not d or not os.path.isabs(d):
            continue
        if os.path.normcase(os.path.realpath(d)) == here:
            continue
        out.append(d)
    return out


def _under(path: str, base: str) -> bool:
    """`path` (realpath'd) is `base` or inside it (realpath'd, case-folded where the FS folds)."""
    p = os.path.normcase(os.path.realpath(path))
    b = os.path.normcase(os.path.realpath(base))
    try:
        return os.path.commonpath([p, b]) == b
    except ValueError:                                  # another drive on Windows
        return False


def which_in(name: str, dirs: Sequence[str], nt: Optional[bool] = None,
             pathext: Optional[str] = None, refuse_under: Optional[str] = None) -> Optional[str]:
    """`name` as an executable file in exactly these directories, in order — never the cwd, never
    a shell shim. On Windows the PATHEXT suffixes are tried (and the name itself when it already
    has an extension); a `.cmd`/`.bat` hit is SKIPPED (it would need cmd.exe); the current
    directory is skipped even when listed. `refuse_under`
    skips any hit whose REALPATH lies inside that directory (re-verification R2: a PATH entry such
    as `<repo>/tools`, or a symlink into the repo, must not let the repo answer for a name the
    USER chose, nor for `node`). Explicit, so a test can inject the platform and the extensions."""
    is_nt = (os.name == "nt") if nt is None else nt
    if is_nt:
        exts = [e for e in (pathext if pathext is not None
                            else os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]
        names = ([name] if os.path.splitext(name)[1] else []) + [name + e for e in exts]
    else:
        names = [name]
    here = os.path.normcase(os.path.realpath(os.getcwd()))
    for d in dirs:
        if not d or not os.path.isabs(d) or not os.path.isdir(d):
            continue
        if os.path.normcase(os.path.realpath(d)) == here:
            continue                                    # the cwd, however it was spelled
        for n in names:
            cand = os.path.join(d, n)
            if not os.path.isfile(cand):
                continue
            if cand.lower().endswith((".cmd", ".bat")):
                continue
            if not is_nt and not os.access(cand, os.X_OK):
                continue
            if refuse_under is not None and _under(cand, refuse_under):
                continue
            return cand
    return None


__all__ = ["path_dirs", "which_in"]
