#!/usr/bin/env python3
"""SessionStart hook — injects mokata's sub-2k-token bootstrap (A4) into the session.

Wire this in Claude Code settings (.claude/settings.json):

    {
      "hooks": {
        "SessionStart": [
          { "hooks": [ { "type": "command",
                         "command": "python3 hooks/session_start.py" } ] }
        ]
      }
    }

This is an *async/observability* hook (G4): it only adds context and always exits 0.
It never blocks a session — if mokata is uninitialized or the config is broken, it
degrades to a one-line note instead of failing. (Security blocking, exit-2, is a
separate concern and not what this hook is for.)
"""

import json
import os
import sys


def _ensure_importable() -> None:
    """Make `mokata` importable whether installed or run from a source checkout."""
    try:
        import mokata  # noqa: F401
        return
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(os.path.dirname(here), "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)


def _read_cwd_from_stdin() -> str:
    """Claude Code passes a JSON payload on stdin; honour its cwd if present."""
    if sys.stdin is None or sys.stdin.isatty():
        return os.getcwd()
    try:
        raw = sys.stdin.read()
    except Exception:
        return os.getcwd()
    if not raw.strip():
        return os.getcwd()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return os.getcwd()
    return payload.get("cwd") or os.getcwd()


def _emit(context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


def main() -> int:
    cwd = _read_cwd_from_stdin()
    _ensure_importable()
    try:
        from mokata.bootstrap import build_bootstrap
        from mokata.config import ConfigError, Surface
    except ImportError:
        # mokata not available at all — say nothing, break nothing.
        return 0

    try:
        surface = Surface.load(cwd)
    except ConfigError:
        _emit("mokata: not initialized in this repo (run `mokata init`).")
        return 0
    except Exception as exc:  # never let a config problem break a session
        _emit(f"mokata: bootstrap skipped ({exc}).")
        return 0

    result = build_bootstrap(surface)
    _emit(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
