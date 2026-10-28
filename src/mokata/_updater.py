"""The detached updater (0.0.26 L2, `MOK-UPDATE`): waits for mokata to exit, then upgrades it.

Windows locks the running `mokata.exe` (and the IDE server's interpreter), so an upgrade cannot
run from inside the environment it replaces. `mokata update` (or the IDE's "Update now") copies
THIS file out of the package, writes a job file, and starts it with the BASE Python (not the
environment's) in a new process group with no console window. It then exits. This script:

  1. waits until every pid in the job (the `mokata` command, or the IDE server) has exited;
  2. runs the installer command the human confirmed — exactly `job["argv"]`;
  3. on success runs the follow-up with the NEW mokata (`mokata update --finish`), which refreshes
     the vendored commands/skills and re-wires only if the wiring changed;
  4. writes `job["result"]` (JSON) for the next `mokata` / IDE start to report: the new version,
     or the error and the line to run by hand.

STDLIB ONLY and imports nothing from mokata: it runs while mokata's files are being replaced.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

WAIT_SECS = 600           # mokata (or the IDE) has this long to exit before the update gives up
STEP_SECS = 1800          # one installer run
_NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x00100000, False, int(pid))          # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102         # WAIT_TIMEOUT -> still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _run(argv):
    kw = {"creationflags": _NO_WINDOW} if os.name == "nt" else {}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=STEP_SECS,
                              check=False, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return done.returncode, ((done.stdout or "") + (done.stderr or ""))[-6000:]


def wait_for(pids, *, alive=_alive, sleep=time.sleep, timeout=WAIT_SECS, clock=time.time) -> bool:
    """True once none of `pids` is running; False if one is still running after `timeout`."""
    end = clock() + timeout
    while any(alive(p) for p in pids):
        if clock() >= end:
            return False
        sleep(0.5)
    return True


def run_job(job: dict, *, run=None, alive=None, sleep=time.sleep, clock=time.time) -> dict:
    """Steps 1-3. Returns the result record (also what `main` writes)."""
    run, alive = run or _run, alive or _alive
    res = {"from": job.get("from"), "command": job.get("display"), "manual": job.get("display"),
           "finished": None, "ok": False}
    if not wait_for(job.get("wait_pids") or [], alive=alive, sleep=sleep, clock=clock):
        res["error"] = ("mokata was still running after "
                        f"{WAIT_SECS // 60} min, so nothing was changed — close it and run the "
                        "line yourself")
        return res
    rc, out = run(list(job["argv"]))
    res["returncode"], res["output"] = rc, out
    if rc != 0:
        res["error"] = (f"the upgrade failed (exit {rc}). If it names a file in use, close every "
                        "Claude Code session and IDE using mokata (its MCP server holds mokata's "
                        "files), then run the line yourself")
        return res
    res["ok"] = True
    if job.get("finish_argv"):
        frc, fout = run(list(job["finish_argv"]))
        res["finish_returncode"], res["finish_output"] = frc, fout
    if job.get("version_argv"):
        vrc, vout = run(list(job["version_argv"]))
        res["to"] = vout.strip().splitlines()[-1] if vrc == 0 and vout.strip() else None
    res["finished"] = clock()
    return res


def write_result(path: str, res: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, indent=2)
    os.replace(tmp, path)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    with open(argv[0], encoding="utf-8") as fh:
        job = json.load(fh)
    try:
        res = run_job(job)
    except Exception as exc:  # noqa: BLE001 — whatever happened, the next start must hear of it
        res = {"ok": False, "error": f"the updater crashed: {type(exc).__name__}: {exc}",
               "manual": job.get("display"), "from": job.get("from")}
    write_result(job["result"], res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
