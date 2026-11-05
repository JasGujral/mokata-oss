"""Is a Windows process alive? Answered from `tasklist`, and FAIL-CLOSED.

`tasklist /FI "PID eq N"` lists the process when it lives and prints a one-line notice (rc 0) when
it does not. A tasklist that failed, was killed or printed nothing says NOTHING about the
process; reading that silence as "dead" would turn a broken instrument into a green test. So:
dead only when tasklist succeeded, printed something, and no row names the pid. Anything else
is "alive" (unknown), and a test that waits for death then fails with the evidence in hand.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import csv
import io
import os
import subprocess


def tasklist_says_alive(pid, run=subprocess.run):
    """(alive, evidence). `alive` is True when a row names `pid` AND when tasklist's answer cannot
    be trusted. `run` is injectable so the rule is tested without a Windows box."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe")
    try:
        p = run([exe, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        return True, f"tasklist could not run: {exc}"
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        return True, f"tasklist rc={p.returncode} stdout={out!r} stderr={(p.stderr or '')[:200]!r}"
    for row in csv.reader(io.StringIO(out)):
        if len(row) >= 2 and row[1].strip() == str(pid):
            return True, out
    return False, out
