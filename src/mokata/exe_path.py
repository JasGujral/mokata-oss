"""Where an executable a user chose is found — and never where the REPO could plant one (0.0.27).

Moved here from `ide/lsp.py` (P24's re-verification R2) at 0.0.27 P21, because the gated merge
needs the same answer for `git` from the CLI and MCP surfaces, which must not import the `[ide]`
extra. One implementation; `ide/lsp.py` imports it.

  * `path_dirs` — the user's PATH as ABSOLUTE directories, never the current directory: CPython's
    `shutil.which` (and Windows' CreateProcess) search the cwd first, and `mokata ide`'s cwd is the
    repo, so the repo could answer for a name the user chose.
  * `which_in` — a name as an executable file in exactly the given directories; never a
    `.cmd`/`.bat` shim; optionally never a hit whose realpath lies inside a given directory.
  * `is_batch` / `shim_in` — what Windows runs through cmd.exe (a `.cmd`/`.bat`, however it is
    spelled: `run.cmd.` and `run.cmd ` are the same file to Windows), and finding one to say so.

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
    here = cwd or os.getcwd()
    out = []
    for d in (env.get("PATH") or "").split(os.pathsep):
        d = d.strip('"')
        if not d or not os.path.isabs(d):
            continue
        if same_place(d, here):
            continue
        out.append(d)
    return out


def same_place(a: str, b: str) -> bool:
    """`a` and `b` name the same directory — by identity, not by spelling. A string compare of
    realpaths is wrong on a case-INSENSITIVE filesystem (macOS APFS, Windows): `/Repo/tools` and
    `/repo/tools` are one directory, and realpath/normcase fold case only on Windows (0.0.27
    LB5-1: a case-flipped PATH entry into the repo passed `refuse_under`). So: the folded strings
    first (cheap, and right for paths that do not exist), then `os.path.samestat` of the two."""
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    if os.path.normcase(ra) == os.path.normcase(rb):
        return True
    try:
        return _same_file(os.stat(ra), os.stat(rb))
    except OSError:
        return False


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    """`os.path.samestat`, but only when the filesystem REALLY reports file ids. Some volumes (SMB
    redirectors, WebDAV, VM shared folders) answer `st_ino == 0` or `st_dev == 0` for everything,
    and then every directory on them would be "the same" as every other (0.0.27 LB-L3) — a
    PATH entry dropped as the cwd, a runner read as "inside the repo". With no ids the answer is
    left to the folded-string compare the callers already made."""
    if not (a.st_ino and a.st_dev and b.st_ino and b.st_dev):
        return False
    return os.path.samestat(a, b)


def _under(path: str, base: str) -> bool:
    """`path` is `base` or inside it, compared by IDENTITY (`same_place`) up the path's ancestors,
    so a differently-cased spelling of the repo is still the repo on a case-insensitive FS."""
    cur = os.path.realpath(path)
    b = os.path.realpath(base)
    try:
        base_stat = os.stat(b)
    except OSError:
        base_stat = None
    nb = os.path.normcase(b)
    while True:
        if os.path.normcase(cur) == nb:
            return True
        if base_stat is not None:
            try:
                if _same_file(os.stat(cur), base_stat):
                    return True
            except OSError:
                pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def is_batch(name: str) -> bool:
    """A file Windows runs through cmd.exe: `.cmd`/`.bat`, and the spellings Windows treats as the
    same file — trailing dots and spaces are stripped by the Win32 path layer (`run.cmd.`,
    `run.cmd..`, `run.bat `), so `CreateProcess` starts cmd.exe for them (0.0.27 LB4-1)."""
    return name.rstrip(". ").lower().endswith((".cmd", ".bat"))


def is_stream(name: str) -> bool:
    """A Windows file name that carries an NTFS alternate-data-stream suffix (`run.cmd::$DATA`,
    `x.exe:s`): a `:` anywhere but the drive prefix. `CreateProcess` cannot start one (WinError
    193), and `run.cmd::$DATA` is the default stream of `run.cmd` — a batch file spelled so it
    escapes the `.cmd` check — so it is refused up front, not discovered at run time."""
    rest = name[2:] if len(name) > 1 and name[1] == ":" and name[0].isalpha() else name
    return ":" in rest


#: Wrappers Windows starts through a shell/host: what `shim_in` reports.
_SHIM_EXTS = (".cmd", ".bat", ".ps1")


def shim_in(name: str, dirs: Sequence[str]) -> Optional[str]:
    """The `name.cmd` / `.bat` / `.ps1` wrapper in these directories, if one exists — a thing
    mokata finds but never runs (cmd.exe / PowerShell would parse the command line). Used only to
    SAY so, never to run it."""
    for d in dirs:
        if not d or not os.path.isabs(d):
            continue
        for ext in _SHIM_EXTS:
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                return cand
    return None


#: The only Windows suffixes a program can be started from without a shell.
_RUNNABLE = (".exe", ".com")


def which_in(name: str, dirs: Sequence[str], nt: Optional[bool] = None,
             pathext: Optional[str] = None, refuse_under: Optional[str] = None) -> Optional[str]:
    """`name` as an executable file in exactly these directories, in order — never the cwd, never
    a shell shim. On Windows the PATHEXT suffixes are tried (and the name itself when it already
    has an extension); only `.exe`/`.com` count — a `.cmd`/`.bat` (cmd.exe), `.vbs`/`.js` (a script host) are never an answer; the current
    directory is skipped even when listed. `refuse_under`
    skips any hit whose REALPATH lies inside that directory (re-verification R2: a PATH entry such
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
        # that lacks them (a user's own, `.PY;.BAT`) must not make every real program invisible.
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
        if same_place(d, here):
            continue                                    # the cwd, however it was spelled
        for n in names:
            cand = os.path.join(d, n)
            if not os.path.isfile(cand):
                continue
            if is_batch(cand):
                continue
            if not is_nt and not os.access(cand, os.X_OK):
                continue
            if refuse_under is not None and _under(cand, refuse_under):
                continue
            return _as_found(cand) if is_nt else cand
    return None


def _as_found(path: str) -> str:
    """`path` spelled as the directory spells the file. PATHEXT is upper-case (`.EXE`), so a hit
    came back as `node.EXE` where the disk says `node.exe` — and that is what the trust prompt
    showed the user (0.0.27 round-2 Low). Windows folds case, so only the SHOWN spelling changes."""
    d, n = os.path.split(path)
    try:
        for entry in os.listdir(d):
            if entry.lower() == n.lower():
                return os.path.join(d, entry)
    except OSError:
        pass
    return path


__all__ = ["is_batch", "path_dirs", "same_place", "shim_in", "which_in"]
