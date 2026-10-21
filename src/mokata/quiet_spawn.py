"""No console window for a background spawn on Windows (0.0.25 `IDE-WIN-CMD-FLASH`).

The IDE server runs with no visible console. On Windows, every CONSOLE program it starts — `git`,
`rg`, `code-review-graph`, and any `.bat`/`.cmd` shim (pyenv-win's `python`/`mokata` shims always
run through `cmd.exe`) — gets a console of its own. Unless the spawn says CREATE_NO_WINDOW, that
console is a VISIBLE cmd window that opens and closes with the child: the IDE polls `/api/file`,
`/api/file` runs `git`, so the user sees cmd windows flash continuously (reported by Jas on Windows,
2026-10-19; mechanism read from the spawns a polling IDE makes — `git` ×10 per refresh round).

Two halves, either of which alone stops the flash:
  * `launch._detach_kwargs` gives the SERVER a hidden console (CREATE_NO_WINDOW, no longer
    DETACHED_PROCESS — Windows ignores CREATE_NO_WINDOW beside DETACHED_PROCESS, and a process
    with no console at all forces every console child to make a new, visible one);
  * every captured-output spawn in the IDE's reach passes `hidden()` — so a server started any
    other way (`--foreground` under pythonw, a service) still never flashes.

Only for spawns whose output is captured or discarded. Never for an interactive child the user is
meant to see (an editor, `claude` in a terminal): CREATE_NO_WINDOW gives it a console nobody sees.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict


def _windows() -> bool:
    return os.name == "nt"


def hidden() -> Dict[str, int]:
    """`subprocess` kwargs that suppress the console window on Windows; empty elsewhere."""
    if _windows():
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


__all__ = ["hidden"]
