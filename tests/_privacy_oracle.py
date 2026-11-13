"""An oracle for "only this user can open it" that does NOT use mokata's own `winacl.verify_private`.

A test that asks the code under test whether its file is private grades nothing (doc 85 §7e). So:
  * POSIX: `os.stat` - owner is this uid, no group/other bit;
  * macOS: also `ls -led` - no ACL entry line (ACLs are evaluated before the mode bits);
  * Windows: `icacls` - exactly one entry, for the user `whoami` names, and it is not inherited.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from typing import List, Tuple


def _run(argv: List[str]) -> str:
    from mokata.quiet_spawn import hidden
    p = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, text=True,
                       encoding="utf-8", errors="replace", **hidden())
    return p.stdout


def mac_acl_entries(path: str) -> List[str]:
    """`ls -led` ACL lines (` 0: group:everyone allow read`), [] when none."""
    out = _run(["/bin/ls", "-led", path])
    return [ln.strip() for ln in out.splitlines()[1:] if re.match(r"^\s*\d+:", ln)]


def mac_acl_entries_in(directory: str) -> List[Tuple[str, str]]:
    """Every ACL line of every entry directly in `directory`, with the entry it belongs to: ONE
    `ls -le` for a whole directory (10k files in one spawn)."""
    out = _run(["/bin/ls", "-lae", directory])
    got, last = [], None
    for ln in out.splitlines():
        if re.match(r"^\s*\d+:", ln):
            got.append((last or "?", ln.strip()))
        elif ln and ln[0] in "-dl":
            last = ln.split()[-1]
    return [(n, a) for n, a in got if n not in (".", "..")]


def _icacls(path: str) -> List[str]:
    icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
    out = _run([icacls, path])
    body = out.split("Successfully processed")[0]
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return []
    first = lines[0]
    lines[0] = first[len(path):] if first.startswith(path) else first
    return [ln.strip() for ln in lines if ln.strip()]


def whoami() -> str:
    return _run(["whoami"]).strip().lower()


def why_not_private(path: str) -> str:
    """"" when only this user can open `path`, else what is wrong (plain words)."""
    st = os.lstat(path)
    if os.name == "nt":
        aces = _icacls(path)
        me = whoami()
        if len(aces) != 1:
            return f"{len(aces)} ACL entries: {aces}"
        if not aces[0].lower().startswith(me + ":"):
            return f"the one entry is not {me}: {aces}"
        if "(I)" in aces[0]:
            return f"the one entry is INHERITED: {aces}"
        return ""
    if st.st_uid != os.geteuid():
        return f"owned by uid {st.st_uid}, not {os.geteuid()}"
    if stat.S_IMODE(st.st_mode) & 0o077:
        return f"mode {oct(stat.S_IMODE(st.st_mode))} grants group/other"
    if sys.platform == "darwin":
        acl = mac_acl_entries(path)
        if acl:
            return f"ACL entries {acl}"
    return ""


def grant_others_read(path: str) -> None:
    """Make `path` WEAK the way a careless copy would: group/other may read it."""
    if os.name == "nt":
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        subprocess.run([icacls, path, "/grant", "*S-1-5-32-545:(R)"], capture_output=True,
                       check=True)
    else:
        os.chmod(path, 0o644)


def give_dir_inheritable_acl(directory: str) -> bool:
    """macOS: `group:everyone allow read,file_inherit,directory_inherit` on `directory` (File
    Sharing / MDM do this); Windows: `BUILTIN\\Users` read, object+container inherit (what a
    profile or a shared drive carries). True when it took; elsewhere False."""
    if os.name == "nt":
        icacls = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "icacls.exe")
        p = subprocess.run([icacls, directory, "/grant", "*S-1-5-32-545:(OI)(CI)(R)"],
                           capture_output=True, text=True)
        return p.returncode == 0
    if sys.platform != "darwin":
        return False
    subprocess.run(["/bin/chmod", "+a", "group:everyone allow read,list,search,file_inherit,"
                    "directory_inherit", directory], capture_output=True, check=True)
    return bool(mac_acl_entries(directory))
