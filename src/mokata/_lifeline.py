"""The POSIX lifeline shim (0.0.29 lane B, R-B6): nothing the MCP server started outlives it.

Run BY PATH as `python -I _lifeline.py <lifeline fd> <status fd> -- <command…>` by the process door
(`quiet_spawn.spawn_jobbed`) once the server called `quiet_spawn.enable_lifeline()`. It is the
tree's session leader; the command is its child in the same process group.

  * The command starts: the status fd is closed with nothing written. It cannot start: the errno,
    its text and the file name are written there (NUL-separated) and the shim exits 127 — the door
    raises the same `OSError` Popen would have raised.
  * The command exits: the shim exits with its code; a death by signal N is re-raised on the shim
    itself, so the door sees the same `-N`.
  * INT/TERM/HUP sent to the shim are passed on to the command.
  * EOF on the lifeline fd — the server's write end closed, however it died (SIGKILL, OOM, a
    crash) — SIGKILLs the shim's own process group: the command and everything it started. The one
    raw group signal outside `quiet_spawn`, and only ever to its own group
    (`tests/test_signals_never_broadcast.py`).

STDLIB ONLY and imports nothing from mokata: it runs under `-I` beside a server that may be gone.
A `setsid` descendant leaves the group and is not reached (§10 L1, disclosed).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import select
import signal
import subprocess
import sys
import threading


def main(argv: list) -> int:
    life, status = int(argv[1]), int(argv[2])
    cmd = argv[argv.index("--") + 1:]
    try:
        child = subprocess.Popen(cmd, close_fds=True)
    except OSError as exc:
        msg = f"{exc.errno or 0}\0{exc.strerror or exc}\0{exc.filename or ''}"
        try:
            os.write(status, msg.encode("utf-8", "replace"))
        finally:
            os.close(status)
        return 127
    os.close(status)

    def forward(signum: int, _frame: object) -> None:
        try:
            child.send_signal(signum)
        except OSError:
            pass
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)

    done_r, done_w = os.pipe()

    def reap() -> None:
        child.wait()
        os.write(done_w, b"x")
    threading.Thread(target=reap, daemon=True).start()
    while True:
        try:
            ready, _, _ = select.select([life, done_r], [], [])
        except InterruptedError:
            continue
        if done_r in ready:
            break
        if life in ready and not os.read(life, 1):
            os.killpg(0, signal.SIGKILL)      # our own group only: the server is gone
    rc = child.returncode
    if rc is not None and rc < 0:
        try:
            signal.signal(-rc, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
        os.kill(os.getpid(), -rc)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
