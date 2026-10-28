"""O4 (0.0.26) — the IDE bridge CLIENT: is an IDE live for this repo, and forward one hook to it.

Why this exists. `mokata setup claude --ide-bridge` wires observe-only hooks that post to the open
IDE's `/api/bus`. On POSIX they are Claude Code `type: "http"` hooks: a refused localhost connect
fails in well under a millisecond. On WINDOWS a refused localhost connect is retried by the TCP
stack for ~1 s, so every hook cost ~1 s while the IDE was closed (0.0.25 cut, windows-latest p95
1011 ms). Claude Code's own http client does the connect, so nothing in mokata could check first.

The fix is to never attempt the connect when no IDE can be listening:

  * `live_ide(root)` — the CHEAP check. The server already writes its record
    (`temp_local/ide/server.json`: port, pid, …) on start and removes it on a clean exit. Live =
    the record exists, is a real-repo (not SAMPLE) server, AND its pid is alive (a stale record
    left by a crash or a hard kill names a dead pid and reads as closed). One small file read and
    one pid probe; no socket is touched. Stdlib only.
  * `forward(root, body)` — `live_ide` first; only then a loopback POST with a SHORT connect
    timeout, so even a recycled pid (record alive, nothing listening) costs at most CONNECT_SECS.
    It authenticates with the bridge record's HOOK token (least privilege: `/api/bus` and nothing
    else), and posts to the server's ACTUAL port from its record — so it also reaches a server
    whose stable bridge port was taken.
  * `main()` — the `type: "command"` hook `--ide-bridge` wires on Windows
    (`python -m mokata.ide.bridge_client --root <repo>`): stdin = the Claude Code hook payload,
    forwarded as-is (the same body the http hook sends). Always exit 0, never prints: observe-only,
    it can never block or slow a session.

Both functions are deliberately small and stdlib-only so the in-repo observe emitter (stage O3) can
call `forward` from a hook it already runs instead of a second spawn.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from typing import Any, Dict, Optional

# The IDE's records live in `ide.statedir.ide_dir(root)` (stdlib-only, no heavy state imports).
SERVER_RECORD = "server.json"
BRIDGE_RECORD = "bridge.json"
BUS_PATH = "/api/bus"
HOOK_HEADER = "X-Mokata-Hook"
CONNECT_SECS = 0.25       # loopback: a live server accepts in < 1 ms; this only bounds a stale pid
MAX_BODY = 8 * 1024 * 1024


def _ide_dir(root: str) -> str:
    # 0.0.26 P29 — the same rule the server writes by: an un-set-up repo's records live in the
    # user's profile, never in the repo (see `ide.statedir`).
    from .statedir import ide_dir
    return ide_dir(root)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def pid_alive(pid: Any) -> bool:
    """A look-only pid probe (stdlib). Windows: OpenProcess + GetExitCodeProcess — never
    `os.kill`, which is TerminateProcess there. Same contract as `session_registry.pid_alive`."""
    from ..session_registry import pid_alive as _probe
    return _probe(pid)


def live_ide(root: str) -> Optional[Dict[str, Any]]:
    """`{"port", "pid"}` of the IDE serving `root` RIGHT NOW, or None — decided without a socket."""
    rec = _read_json(os.path.join(_ide_dir(root), SERVER_RECORD))
    if not rec or rec.get("demo"):
        return None
    try:
        port, pid = int(rec.get("port") or 0), int(rec.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if not (0 < port < 65536) or not pid_alive(pid):
        return None
    return {"port": port, "pid": pid}


def _hook_token(root: str) -> str:
    rec = _read_json(os.path.join(_ide_dir(root), BRIDGE_RECORD))
    tok = rec.get("token") if rec else None
    return tok if isinstance(tok, str) else ""


def forward(root: str, body: bytes, *, timeout: float = CONNECT_SECS) -> bool:
    """POST one hook payload to the live IDE's bus. False (and NO connect attempted) when no IDE is
    live or the repo has no bridge token; False on any network error. Never raises."""
    ide = live_ide(root)
    if ide is None or not body or len(body) > MAX_BODY:
        return False
    token = _hook_token(root)
    if not token:
        return False
    conn = http.client.HTTPConnection("127.0.0.1", ide["port"], timeout=timeout)
    try:
        conn.request("POST", BUS_PATH, body=body,
                     headers={"Content-Type": "application/json", HOOK_HEADER: token})
        return 200 <= conn.getresponse().status < 300
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[args.index("--root") + 1] if "--root" in args[:-1] else os.getcwd()
    try:
        body = sys.stdin.buffer.read(MAX_BODY + 1)
    except (OSError, ValueError, AttributeError):
        return 0
    forward(root, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
