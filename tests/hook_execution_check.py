"""HOOK-SHELL-AGNOSTIC — EXECUTE mokata's hooks and prove the gate FIRES, per platform x shell.

Not a unit test and deliberately not discoverable (the discover pattern is `test*.py`): CI
invokes this explicitly, once per platform x shell leg. Everything else in the suite asserts
what mokata WIRES; this asserts that what it wires actually RUNS and BLOCKS.

Why it exists. String assertions are exactly what let the falsified extension-search premise
survive — a test can confirm a command is wired, and the gate still be off, because whether the
shell can launch it is a different question. So this leg plants a real secret canary, runs the
hook the way it is actually wired, and demands exit 2. A leg that cannot block is a red.

WHAT IT CHECKS, per leg:
  1. EXEC form (the `mokata setup claude` route) — read straight out of the settings.json that
     setup just wrote, spawned directly with no shell, exactly as the harness spawns it.
     Canary -> exit 2 (BLOCK).  Clean write -> exit 0.
  2. SHELL form (the plugin route) — the shipped `hooks/mokata-hook-launch` shim run under THIS
     leg's shell, which is what `"shell": "bash"` in hooks.json selects. Skipped where no POSIX
     shell exists (the PowerShell leg), and that skip is REPORTED, never silent.

Usage:  python tests/hook_execution_check.py [--shell <name>]

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "src" / "mokata" / "hooks" / "mokata-hook-launch"

BLOCK_EXIT = 2      # hook_cli.BLOCK_EXIT — a real security block
ALLOW_EXIT = 0

# Both the canary and the assignment around it are assembled at RUNTIME. mokata's own
# secret-guard scans assigned VALUES (SECRET-VALUE-SCAN) and blocks a tracked file that
# contains either the canary literal or a `NAME = '<secret>'` shape — including inside an
# f-string. Keeping both out of the source text is what lets this file be written at all.
CANARY = "AKIA" + "IOSFODNN7" + "EXAMPLE"
_KEY_NAME = "AWS_" + "SECRET"

_failures = []
_skips = []


def _leak_content():
    """A file body carrying a real, scannable secret assignment — built, never written out."""
    return "%s = %r\n" % (_KEY_NAME, CANARY)


def _say(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        _failures.append(f"{label}{(' — ' + detail) if detail else ''}")


def _payload(content):
    return json.dumps({"tool_name": "Write",
                       "tool_input": {"file_path": "app/config.py", "content": content}})


def _run(argv, payload, shell=None):
    """Run a hook and return (exit code, stderr). `shell` runs a command STRING under a shell."""
    kwargs = dict(input=payload, capture_output=True, text=True, cwd=str(ROOT),
                  env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    if shell:
        proc = subprocess.run([shell, "-c", argv], **kwargs)
    else:
        proc = subprocess.run(argv, **kwargs)
    return proc.returncode, (proc.stderr or "").strip()


def check_exec_form():
    """The setup route, spawned with NO shell — the fix, proven end to end."""
    print("EXEC form (the `mokata setup claude` route — spawned directly, no shell)")
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "repo"
        (repo / ".git").mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, "-m", "mokata.cli", "setup", "claude",
             "--path", str(repo), "--scope", "project", "--yes", "--no-grant"],
            capture_output=True, text=True, cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "HOME": d})
        settings = repo / ".claude" / "settings.json"
        if not settings.is_file():
            _say(False, "setup wrote settings.json",
                 f"absent at {settings}; setup exit={proc.returncode}; "
                 f"{(proc.stderr or proc.stdout or '').strip()[:300]}")
            return
        data = json.loads(settings.read_text(encoding="utf-8"))

        wired = None
        for entry in data.get("hooks", {}).get("PreToolUse", []):
            for h in entry.get("hooks", []):
                if "secret-guard" in " ".join(h.get("args") or []):
                    wired = h
        if wired is None:
            _say(False, "secret-guard is wired in EXEC form",
                 "no PreToolUse hook carries secret-guard in args")
            return
        _say(True, "secret-guard is wired in EXEC form", f"args={wired.get('args')}")
        _say("shell" not in wired, "the wired hook names no shell (exec form takes none)")

        argv = [wired["command"]] + list(wired.get("args") or [])
        if not (Path(argv[0]).is_file() or shutil.which(argv[0])):
            _say(False, "the wired executable exists", f"{argv[0]!r} not found")
            return

        code, err = _run(argv, _payload(_leak_content()))
        _say(code == BLOCK_EXIT, "planted canary BLOCKS (exit 2)", f"got exit {code}; {err[:160]}")

        code, _ = _run(argv, _payload("TIMEOUT = 30\n"))
        _say(code == ALLOW_EXIT, "clean write ALLOWS (exit 0)", f"got exit {code}")


def check_shell_form(shell_name):
    """The plugin route: the shipped sh shim, run under THIS leg's shell."""
    print(f"SHELL form (the plugin shim under {shell_name!r})")
    if shell_name == "none":
        # The PowerShell leg. windows-latest SHIPS Git Bash and CI cannot uninstall it, so this
        # half cannot be exercised there honestly — the shim would run under a bash that a real
        # Git-Bash-less user does not have, and the leg would report a pass it did not earn.
        # The EXEC-form half above is what must hold on this leg, and it is the actual fix.
        _skips.append("shell form — this leg represents a box with NO POSIX shell; the plugin "
                      "shim is unrunnable there BY DESIGN and `shell: \"bash\"` makes Claude "
                      "Code say so. Exec form (checked above) is the route that must work.")
        print("  SKIP  no POSIX shell on this leg by design — REPORTED, not silently passed")
        return
    sh = shutil.which(shell_name)
    if not sh:
        _skips.append(f"shell form under {shell_name!r} — no such shell on this runner")
        print(f"  SKIP  no {shell_name!r} on PATH — REPORTED, not silently passed")
        return
    if not SHIM.is_file():
        _say(False, "the POSIX shim ships", f"absent at {SHIM}")
        return

    command = f'"{SHIM}" secret-guard'
    code, err = _run(command, _payload(_leak_content()), shell=sh)
    _say(code == BLOCK_EXIT, "planted canary BLOCKS (exit 2)", f"got exit {code}; {err[:160]}")

    code, _ = _run(command, _payload("TIMEOUT = 30\n"), shell=sh)
    _say(code == ALLOW_EXIT, "clean write ALLOWS (exit 0)", f"got exit {code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shell", default="sh",
                    help="the POSIX shell this leg represents (bash|zsh|sh); "
                         "the PowerShell leg passes one that will not be found, and the skip "
                         "is reported")
    args = ap.parse_args()

    print(f"mokata hook execution check — platform={sys.platform} shell={args.shell!r}\n")
    check_exec_form()
    print()
    check_shell_form(args.shell)

    print()
    for s in _skips:
        print(f"SKIPPED: {s}")
    if _failures:
        print(f"\nRED — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\nGREEN — the gate FIRES on this platform x shell "
          f"({len(_skips)} reported skip(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
