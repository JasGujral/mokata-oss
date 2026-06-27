#!/usr/bin/env python3
"""SessionStart hook — injects mokata's sub-2k-token bootstrap (A4) into the session.

Wire this in Claude Code settings (.claude/settings.json). `mokata setup claude`
writes this for you, embedding the absolute interpreter (sys.executable) so it
works regardless of how Python is named or whether it's on PATH (Stage 28):

    {
      "hooks": {
        "SessionStart": [
          { "hooks": [ { "type": "command",
                         "command": "/abs/python3 /abs/hooks/session_start.py" } ] }
        ]
      }
    }

The shipped plugin hooks.json can't bake in your interpreter, so it instead calls
`hooks/launch.sh`, which resolves a Python 3 (python3 / python / py -3) at run time.

This is an *async/observability* hook (G4): it only adds context and always exits 0.
It never blocks a session — if the config is broken it degrades to a one-line note
instead of failing. (Security blocking, exit-2, is a separate concern, not this hook.)

Two Stage 23 jobs beyond the briefing:
  - record the plugin root to ~/.mokata/plugin-root so the `/mokata:init` command can
    find the bundled engine (${CLAUDE_PLUGIN_ROOT} isn't expanded in command bodies);
  - when the project isn't set up yet, inject a one-line OFFER so Claude proactively asks
    to initialize it — gone for good the instant `.mokata/manifest.json` exists.
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


def _record_plugin_root() -> None:
    """Cache the plugin root so the /mokata:init command can find the bundled engine.
    The plugin root is this file's grandparent (<root>/hooks/session_start.py); honor an
    explicit CLAUDE_PLUGIN_ROOT when Claude Code passes one. Never raises."""
    try:
        from mokata.plugin_cache import record_plugin_root
        root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        record_plugin_root(root)
    except Exception:
        pass


def main() -> int:
    cwd = _read_cwd_from_stdin()
    _ensure_importable()
    try:
        from mokata.bootstrap import build_bootstrap, build_setup_offer
        from mokata.config import ConfigError, Surface, find_project_root
    except ImportError:
        # mokata not available at all — say nothing, break nothing.
        return 0

    _record_plugin_root()

    # Key off the REAL project root so an already-initialized repo is recognized from a
    # subdirectory and never re-offered init (Stage 23 — the "asks every time" bug).
    root = find_project_root(cwd)

    if not Surface.is_initialized(root):
        # Proactive, one-time offer. Disappears for good once .mokata/ exists.
        _emit(build_setup_offer().text)
        return 0

    try:
        surface = Surface.load(root)
    except ConfigError:
        _emit(build_setup_offer().text)
        return 0
    except Exception as exc:  # never let a config problem break a session
        _emit(f"mokata: bootstrap skipped ({exc}).")
        return 0

    result = build_bootstrap(surface)
    _emit(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
