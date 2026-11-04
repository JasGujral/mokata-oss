"""Read a Windows ACL back with `icacls` — the Windows half of every "owner-only" test.

POSIX tests read `st_mode & 0o777`; Windows has no such bits, so the same promise is read from the
ACL itself: exactly one entry, the current user's, and nothing inherited from the folder.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
from typing import List


def acl_entries(path: str) -> List[str]:
    """The ACE lines `icacls` prints for `path` (`NAME:(flags)`), the path column stripped."""
    out = subprocess.run(["icacls", path], capture_output=True, text=True, check=True).stdout
    entries = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(path):
            line = line[len(path):].strip()
        if ":(" in line:
            entries.append(line)
    return entries


def assert_owner_only(case, path: str) -> None:
    """`path` grants the current user and NOBODY else, and inherits nothing."""
    if os.name != "nt":
        return
    entries = acl_entries(path)
    case.assertEqual(1, len(entries), f"{path} ACL: {entries}")
    user = os.environ.get("USERNAME", "").lower()
    case.assertIn(user, entries[0].lower(), entries)
    case.assertNotIn("(I)", entries[0], "an owner-only file inherits nothing from its folder")
    for other in ("users", "everyone", "authenticated users", "system", "administrators"):
        case.assertNotIn(other + ":", entries[0].lower().replace("\\", " "), entries)
