"""Where an executable a user chose is found — and never where the REPO could plant one (0.0.27).

Moved here from `ide/lsp.py` (P24's re-verification R2) at 0.0.27 P21, because the gated merge
needs the same answer for `git` from the CLI and MCP surfaces, which must not import the `[ide]`
extra. One implementation; `ide/lsp.py` imports it.

  * `path_dirs` — the user's PATH as ABSOLUTE directories, never the current directory: CPython's
    `shutil.which` (and Windows' CreateProcess) search the cwd first, and `mokata ide`'s cwd is the
    repo, so the repo could answer for a name the user chose.
  * `which_in` — a name as an executable file in exactly the given directories; never a
    `.cmd`/`.bat` shim; optionally never a hit inside a given directory, however it is spelled
    (`fs_identity.refuse_if_inside`, 0.0.28 C2).
  * `win_runnable` / `win_refusal` — 0.0.28 lane B rule R5, an ALLOW-list: a Windows program is a
    file whose name, read as the Win32 layer reads it (trailing dots and spaces stripped), ends
    `.exe`/`.com` — or has no extension and `name.exe`/`name.com` exists. Everything else (`.cmd`,
    `run.cmd.`, `run.bat `, `RUN~1.CMD`, `run.cmd::$DATA`, `.ps1`, a device name, a verbatim
    `?`-prefixed or UNC path) is refused before anything starts.

Pure functions; the platform and PATHEXT are injectable so a test can fake Windows.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from .fs_identity import refuse_if_inside, same_entry


def path_dirs(environ: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> List[str]:
    """The user's PATH as ABSOLUTE directories: an empty or relative entry and the current
    directory are dropped (re-verification R2: CPython's `shutil.which` on Windows searches the
    cwd first even with `path=`, and `mokata ide`'s cwd is the repo — so the repo could choose)."""
    env = os.environ if environ is None else environ
    here = cwd or os.getcwd()
    out = []
    for d in (env.get("PATH") or "").split(os.pathsep):
        d = d.strip('"')
        if not d or not os.path.isabs(d):
            continue
        if same_entry(d, here):                      # the cwd however it is spelled (0.0.28 C2)
            continue
        out.append(d)
    return out


#: The only Windows suffixes a program can be started from without a shell.
_RUNNABLE = (".exe", ".com")
#: What Windows starts through cmd.exe / a script host — found to SAY so, never run.
_BATCH = (".cmd", ".bat")
_SHIMS = (".cmd", ".bat", ".ps1")

#: CPython 3.13 `ntpath._isreservedname`'s sets, COPIED (the floor is 3.10, so it is not called):
#: DOS device names (incl. the superscript COM/LPT digits Windows also reserves) and the characters
#: no Windows file name may hold.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{c}" for c in "123456789\xb9\xb2\xb3"}
    | {f"LPT{c}" for c in "123456789\xb9\xb2\xb3"})
_RESERVED_CHARS = frozenset({chr(i) for i in range(32)} | set('"*:<>?|/\\'))


def win_reserved_name(name: str) -> bool:
    """CPython 3.13's `_isreservedname` rule for ONE path component: a trailing dot or space,
    a reserved character (incl. `:`, a stream), or a DOS device name however it is dressed —
    `nul .py`, `CON.txt`, `COM\xb9`. `.` and `..` are not reserved."""
    if name[-1:] in (".", " "):
        return name not in (".", "..")
    if _RESERVED_CHARS.intersection(name):
        return True
    return name.partition(".")[0].rstrip(" ").upper() in _RESERVED_NAMES


def win_name(name: str) -> str:
    """`name` as the Win32 path layer reads it: trailing dots and spaces stripped from the final
    component (`run.cmd.` and `run.cmd ` ARE `run.cmd` to CreateProcess). Separators are kept."""
    cut = max(name.rfind("/"), name.rfind("\\")) + 1
    tail = name[cut:].rstrip(". ")
    return name[:cut] + (tail or name[cut:])


def win_refusal(name: str) -> "Optional[Tuple[str, str]]":
    """`(kind, why)` when Windows must not be asked to start `name` as a program, else None (the
    name, read as Win32 reads it, ends `.exe`/`.com` or has no extension). `kind` is one of
    `verbatim`, `unc`, `drive-relative`, `stream`, `device`, `batch` (cmd.exe), `script`
    (PowerShell, a script host, anything else). Only the NAME is judged — no file is touched."""
    raw = name.replace("/", "\\")
    if raw.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return "verbatim", (f"`{name}` is a `\\\\?\\` / `\\\\.\\` path — Windows does not "
                            f"normalise it the way mokata checks names, so it is not started")
    if raw.startswith("\\\\"):
        return "unc", f"`{name}` is on a network share (UNC path) — mokata never starts a runner from one"
    drive = len(raw) > 1 and raw[1] == ":" and raw[0].isalpha()
    rest = raw[2:] if drive else raw
    if drive and not rest.startswith("\\"):
        return "drive-relative", (f"`{name}` is relative to the current directory of drive "
                                  f"{raw[:2]} — spell the runner's full path")
    if ":" in rest:
        return "stream", (f"`{name}` names an NTFS alternate data stream (a `:` after the file "
                          f"name), not a program file — set `settings.baseline.test_command` "
                          f"to the real executable's own path")
    base = win_name(raw).rpartition("\\")[2]
    if base and base.partition(".")[0].rstrip(" ").upper() in _RESERVED_NAMES:
        return "device", f"`{name}` is a Windows device name (`{base}`), not a program file"
    ext = os.path.splitext(base)[1].lower()
    if not ext or ext in _RUNNABLE:
        return None
    if ext in _BATCH:
        return "batch", f"`{name}` is a Windows batch file — cmd.exe would re-parse the command line"
    return "script", (f"`{name}` is not a Windows program (`{ext}`): only a real `.exe`/`.com` is "
                      f"started — PowerShell or a script host would parse the command line")


def win_runnable(path: str) -> "Tuple[Optional[str], str]":
    """`(path to spawn, "")` for an EXPLICIT Windows runner path, else `(None, why)` (R5). The
    path is read as Win32 reads it; `.exe`/`.com` is started as named; NO extension is accepted
    only when `name.exe` (else `name.com`) exists on disk, and that file is what is spawned — never
    relying on CreateProcess to append anything (it does not, for a name that holds a path or
    ends in a dot). `x` beside only `x.bat` is refused."""
    bad = win_refusal(path)
    if bad is not None:
        return None, bad[1]
    p = win_name(path)
    if os.path.splitext(p)[1].lower() in _RUNNABLE:
        return p, ""
    for e in _RUNNABLE:
        if os.path.isfile(p + e):
            return _as_found(p + e), ""
    shim = next((p + e for e in _SHIMS if os.path.isfile(p + e)), None)
    if shim is not None:
        return None, (f"`{path}` has no extension and only {os.path.basename(shim)} is beside it "
                      f"— mokata never runs a .cmd/.bat/.ps1 (cmd.exe / PowerShell would parse the "
                      f"command line)")
    return None, (f"`{path}` has no extension and no `{os.path.basename(p)}.exe` is beside it — "
                  f"name the runner's real executable")


def _as_found(path: str) -> str:
    """`path` spelled as its directory spells the file (PATHEXT is upper-case, so a hit came back
    as `node.EXE` where the disk says `node.exe`; Windows folds case, only the SHOWN spelling
    changes)."""
    d, n = os.path.split(path)
    try:
        for entry in os.listdir(d):
            if entry.lower() == n.lower():
                return os.path.join(d, entry)
    except OSError:
        pass
    return path


def which_in(name: str, dirs: Sequence[str], nt: Optional[bool] = None,
             pathext: Optional[str] = None, refuse_under: Optional[str] = None) -> Optional[str]:
    """`name` as an executable file in exactly these directories, in order — never the cwd, never
    a shell shim. On Windows the PATHEXT suffixes are tried (and the name itself when it already
    has an extension); only `.exe`/`.com` count — a `.cmd`/`.bat` (cmd.exe), `.vbs`/`.js` (a script host) are never an answer; the current
    directory is skipped even when listed. `refuse_under`
    skips any hit that is, on this disk or by any spelling, inside that directory (0.0.28 C2:
    `fs_identity.refuse_if_inside`; re-verification R2: a PATH entry such
    as `<repo>/tools`, or a symlink into the repo, must not let the repo answer for a name the
    USER chose, nor for `node`). Explicit, so a test can inject the platform and the extensions."""
    is_nt = (os.name == "nt") if nt is None else nt
    if is_nt:
        exts = [e for e in (pathext if pathext is not None
                            else os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]
        # Only what CreateProcess can run by itself: a PATHEXT of `.VBS;.JS;.WSF;.MSC` (the
        # default) must never make a script the answer, nor shadow a later real `.exe`.
        exts = [e for e in exts if e.lower() in _RUNNABLE]
        # CreateProcess does not consult PATHEXT: `.exe`/`.com` are always runnable, so a PATHEXT
        # that lacks them (a user's own `.PY;.BAT`) must not make every real program invisible.
        have = {e.lower() for e in exts}
        exts += [e.upper() for e in _RUNNABLE if e not in have]
        names = ([name] if os.path.splitext(name)[1].lower() in _RUNNABLE else []) \
            + [name + e for e in exts]
    else:
        names = [name]
    here = os.getcwd()
    for d in dirs:
        if not d or not os.path.isabs(d) or not os.path.isdir(d):
            continue
        if same_entry(d, here):
            continue                                    # the cwd, however it was spelled
        for n in names:
            cand = os.path.join(d, n)
            if not os.path.isfile(cand):
                continue
            if cand.rstrip(". ").lower().endswith(_BATCH):
                continue
            if not is_nt and not os.access(cand, os.X_OK):
                continue
            if refuse_under is not None and refuse_if_inside(cand, refuse_under):
                continue
            return _as_found(cand) if is_nt else cand
    return None


__all__ = ["path_dirs", "which_in", "win_name", "win_refusal", "win_reserved_name",
           "win_runnable"]
