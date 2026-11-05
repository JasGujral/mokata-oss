"""No console window for a background spawn on Windows (0.0.25 `IDE-WIN-CMD-FLASH`).

The IDE server runs with no visible console. On Windows, every CONSOLE program it starts — `git`,
`rg`, `code-review-graph`, and any `.bat`/`.cmd` shim (pyenv-win's `python`/`mokata` shims always
run through `cmd.exe`) — gets a console of its own. Unless the spawn says CREATE_NO_WINDOW, that
console is a VISIBLE cmd window that opens and closes with the child: the IDE polls `/api/file`,
`/api/file` runs `git`, so the user sees cmd windows flash continuously (reported by Jas on Windows,
2026-10-19; mechanism read from the spawns a polling IDE makes — `git` ×10 per refresh round).

Two halves, either of which alone stops the flash:
  * `detached()` (the IDE server start, the hook drainer) gives the CHILD a hidden console (CREATE_NO_WINDOW, no longer
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

TYPE_CHECKING = False        # `typing` costs a hook ~3 ms and is only for readers here
if TYPE_CHECKING:
    from typing import Dict, Optional


def _windows() -> bool:
    return os.name == "nt"


def hidden() -> Dict[str, int]:
    """`subprocess` kwargs that suppress the console window on Windows; empty elsewhere."""
    if _windows():
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def detached(suspended: bool = False, nt: "Optional[bool]" = None) -> "Dict[str, object]":
    """`subprocess` kwargs for a background child that must OUTLIVE its parent and show no window:
    a new session on POSIX; on Windows a new process group with its own HIDDEN console.

    0.0.25 `IDE-WIN-CMD-FLASH` (B-01): NOT `DETACHED_PROCESS`. Windows ignores CREATE_NO_WINDOW beside
    it, so the child had no console at all — and a console-less process makes every console child it
    starts (`git`, `rg`, a pyenv-win `.bat` shim through `cmd.exe`) open a new VISIBLE window. A hidden
    console is inherited by those children instead, and CREATE_NEW_PROCESS_GROUP keeps the child out
    of the launching console's Ctrl-C/close group, so it is not taken down with its parent
    (`start_new_session` is POSIX-only and silently ignored on Windows). Used by the IDE server start
    (`ide/launch.py`), the hook drainer (`mokata_emit`, 0.0.26 O3) and the code-graph index
    builder (0.0.26 O13).

    `suspended` adds CREATE_SUSPENDED on Windows (nothing elsewhere): the process is created but
    does not run until the caller resumes it — `ide.lsp.spawn_jobbed` puts it in its Job Object
    first. `nt` injects the platform for the tests."""
    if _windows() if nt is None else nt:
        return {"creationflags": (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                                  | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                  | (0x00000004 if suspended else 0))}
    return {"start_new_session": True}


def _signalable(n: object, *own: int) -> bool:
    """A real process number we may signal: a plain `int` (not `bool`, not a test double whose
    `__index__` is 1), greater than 1, and none of `own`. 0, 1 and negatives are never a target:
    POSIX `killpg(1, sig)` is `kill(-1, sig)` on Linux — EVERY process the user owns (it took
    down the CI runner, 0.0.27) — and `killpg(0)` / `kill(0)` is the caller's own group."""
    return type(n) is int and n > 1 and n not in own


def signal_group(pgid: object, sig: int) -> bool:
    """`os.killpg(pgid, sig)` — only for a group that is not 0, 1, negative, a non-int, or OUR OWN
    group or our own pid (POSIX only; there is no process group to signal on Windows). Returns False, sending
    nothing, when refused; OS errors (the group is gone, not ours) propagate as from `killpg`.
    The ONE door for a group signal in mokata (`tests/test_signals_never_broadcast.py`)."""
    if _windows() or not _signalable(pgid, os.getpgrp(), os.getpid()):
        return False
    os.killpg(pgid, sig)  # type: ignore[arg-type]
    return True


def signal_pid(pid: object, sig: int) -> bool:
    """`os.kill(pid, sig)` — only for a pid that is not 0, 1, negative, a non-int, or OUR OWN
    process. Returns False, sending nothing, when refused; OS errors propagate as from `os.kill`.
    The ONE door for a non-probe signal to a pid in mokata."""
    if not _signalable(pid, os.getpid()):
        return False
    os.kill(pid, sig)  # type: ignore[arg-type]
    return True


__all__ = ["detached", "hidden", "signal_group", "signal_pid"]
