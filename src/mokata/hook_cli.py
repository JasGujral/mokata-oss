"""`mokata-hook` — the console entry point for mokata's Claude Code hooks (Stage 53b).

mokata's hooks used to be launched by a static plugin ``hooks.json`` that ran
``sh launch.sh`` to resolve *a* ``python3`` / ``python`` / ``py`` at run time. That chain
is fragile: Windows has no ``sh``, a GUI-launched macOS Claude Code runs with a minimal
PATH, and any stale config that baked a bare ``python3`` never re-resolves — so test users
hit ``python3: command not found`` on the PreToolUse hook.

The bundled MCP server never had this problem: it's launched as the ``mokata-mcp`` **console
entry point**, resolved exactly like any other installed script. This module gives the hooks
the *same* mechanism: a single ``mokata-hook`` entry point with two subcommands —

    mokata-hook session-start [--plugin-root <dir>]
    mokata-hook secret-guard  [--text … --path … --send]

No bare ``python3``, no ``sh``, no PATH guessing. If ``mokata-mcp`` resolves for a user (it
must, for the MCP server to work), ``mokata-hook`` resolves identically.

This module is the **single source of truth** for the hook runtime. The standalone
``hooks/session_start.py`` / ``hooks/secret_guard.py`` scripts are thin shims that import
from here, so the legacy ``launch.sh`` fallback (and any direct invocation) still works.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

# Security-block exit code (PreToolUse): a non-zero exit blocks the tool call.
BLOCK_EXIT = 2

# Keys inside tool_input that name the target path (for path-based detection, e.g. .env).
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")


# ======================================================================================
# secret-guard (PreToolUse, sync SECURITY hook — G4 + I1)
# ======================================================================================

def _iter_strings(obj):
    """Yield every string value nested anywhere inside obj (dict/list/scalar)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _find_path(obj):
    """First path-like string found in a tool_input mapping (for path-based detection)."""
    if isinstance(obj, dict):
        for k in _PATH_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            found = _find_path(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_path(v)
            if found:
                return found
    return None


def _from_envelope(raw):
    """If raw stdin is a PreToolUse JSON envelope, return (content_to_scan, target_path)
    drawn ONLY from tool_input. Return None if it isn't such an envelope (so the caller
    falls back to raw-text scanning). The envelope metadata is never scanned."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "tool_input" not in data:
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, (dict, list)):
        return None
    content = "\n".join(_iter_strings(tool_input))   # command / content / edit strings only
    path = _find_path(tool_input)
    return content, path


def secret_guard_main(argv: Optional[List[str]] = None) -> int:
    """Scan the tool's CONTENT (and its target path) for secrets; BLOCK (exit 2) if any
    are found. Sync hooks block only for security; this is the canonical example.

    Input resolution (priority): ``--text`` (+ optional ``--path``); else stdin as a
    PreToolUse JSON envelope (scan only ``tool_input``); else stdin as raw text."""
    parser = argparse.ArgumentParser(description="mokata secret guard (sync security hook)")
    parser.add_argument("--text", default=None, help="content to scan")
    parser.add_argument("--path", default=None, help="target path being written/sent")
    parser.add_argument("--send", action="store_true", help="content is leaving the machine")
    args = parser.parse_args(argv)

    # Imported lazily so a half-installed environment degrades to a clean no-op (exit 0)
    # rather than crashing a hook — a missing engine must never block a session/tool call.
    try:
        from .govern import scan
    except Exception:
        return 0

    if args.text is not None:
        text, path = args.text, args.path
    else:
        raw = sys.stdin.read()
        envelope = _from_envelope(raw)
        if envelope is not None:
            text, path = envelope
            path = path or args.path        # explicit --path still honored if given
        else:
            text, path = raw, args.path     # raw-text fallback (non-envelope callers)

    findings = scan(text=text or "", path=path, for_send=args.send)
    if findings:
        for f in findings:
            sys.stderr.write(f"BLOCKED [{f.layer}/{f.kind}] {f.detail}\n")
        sys.stderr.write("mokata: secret detected — write/commit/send blocked.\n")
        return BLOCK_EXIT
    return 0


# ======================================================================================
# session-start (SessionStart, async/observability hook — A4 bootstrap + Stage 23 offer)
# ======================================================================================

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


def _record_plugin_root(plugin_root: Optional[str]) -> None:
    """Cache the plugin root so the /mokata:init command can find the bundled engine.

    Unlike the old in-tree hook, this module lives inside the installed package, so its
    ``__file__`` is NOT the plugin root — the root is passed explicitly (the plugin
    ``hooks.json`` forwards ``${CLAUDE_PLUGIN_ROOT}``; ``mokata setup`` forwards the clone
    root) or read from the ``CLAUDE_PLUGIN_ROOT`` env. Never raises."""
    try:
        from .plugin_cache import record_plugin_root
        root = plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT")
        if root:
            record_plugin_root(root)
    except Exception:
        pass


def session_start_main(argv: Optional[List[str]] = None) -> int:
    """Inject mokata's sub-2k-token bootstrap (A4). Async/observability hook: only adds
    context and ALWAYS exits 0 — a broken config degrades to a one-line note, never blocks
    the session."""
    parser = argparse.ArgumentParser(description="mokata SessionStart briefing hook")
    parser.add_argument("--plugin-root", default=None,
                        help="the plugin/clone root holding the bundled engine")
    args, _unknown = parser.parse_known_args(argv)

    cwd = _read_cwd_from_stdin()
    try:
        from .bootstrap import build_bootstrap, build_setup_offer
        from .config import ConfigError, Surface, find_project_root
    except ImportError:
        # mokata engine not available — say nothing, break nothing.
        return 0

    _record_plugin_root(args.plugin_root)

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

    _emit(build_bootstrap(surface).text)

    # Stage 60 — advance the "since last session" baseline at the session boundary. The briefing
    # above already DERIVED its diff against the OLD snapshot (read-only); now capture the new
    # baseline so the next session compares against this one. Guarded: a transient write to
    # gitignored temp_local/, read-only on the governed state, never raises, never blocks.
    try:
        from .visibility import capture_session_snapshot
        capture_session_snapshot(surface)
    except Exception:
        pass
    return 0


# ======================================================================================
# statusline (Claude Code statusLine command — Stage 54b)
# ======================================================================================
# Claude Code's statusLine is a command that receives the session JSON on stdin and prints
# ONE line to stdout (re-run, debounced, as state changes). This subcommand reads that
# payload, builds mokata's pipeline-stage mode-badge from the run-state, and prints it.
# It is READ-ONLY, ALWAYS exits 0, never blocks, and prints NOTHING when the badge is
# disabled (settings.ux.statusline=false) or mokata isn't initialized here — so it's pure,
# safe surfacing that can never get in the way of a session.

# How long we let a wrapped (pre-existing user) statusline command run before giving up —
# a statusline must never block the harness, so composition degrades to mokata's part alone.
_WRAP_TIMEOUT_SECS = 5


def _read_statusline_stdin() -> str:
    """The raw statusLine JSON on stdin; "" when there's nothing/at a TTY (never blocks)."""
    if sys.stdin is None:
        return ""
    try:
        if sys.stdin.isatty():
            return ""
    except Exception:
        pass
    try:
        return sys.stdin.read()
    except Exception:
        return ""


def _cwd_and_session_name(raw: str):
    """(cwd, session_name) from the statusLine payload. Honour `workspace.current_dir`
    (then top-level `cwd`), and the optional `session_name` Claude Code passes (the Stage-55
    badge slot). Degrade to the process cwd / no name on any parse problem."""
    cwd, name = os.getcwd(), None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return cwd, name
    if isinstance(payload, dict):
        ws = payload.get("workspace")
        if isinstance(ws, dict) and ws.get("current_dir"):
            cwd = ws["current_dir"]
        elif payload.get("cwd"):
            cwd = payload["cwd"]
        sn = payload.get("session_name")
        if isinstance(sn, str) and sn.strip():
            name = sn
    return cwd, name


def _mokata_segment(cwd: str, session_name: Optional[str]) -> str:
    """mokata's badge for `cwd` — "" when mokata isn't initialized here, the badge is
    disabled, or the engine is unavailable. Never raises."""
    try:
        from .config import Surface, find_project_root
        from .progress import build_stage_badge, statusline_enabled
    except Exception:
        return ""
    try:
        root = find_project_root(cwd)
        if not Surface.is_initialized(root):
            return ""
        surface = Surface.load(root)
        if not statusline_enabled(surface):
            return ""
        return build_stage_badge(surface, session_name=session_name)
    except Exception:
        return ""


def _run_wrapped(command: str, raw: str) -> str:
    """Run a pre-existing user statusLine command (merge-safe composition), feeding it the
    SAME payload, and return its first stdout line. Best-effort + bounded: any failure /
    timeout degrades to "" so mokata's badge still renders and the harness never blocks."""
    if not command:
        return ""
    try:
        import subprocess
        # Justification for the B602 suppression: `command` is the user's OWN pre-existing
        # statusLine command (from their Claude Code settings), composed here so mokata's badge
        # doesn't clobber it. It must run through a shell to match how the harness itself runs a
        # statusLine (pipes, $VARS); a shlex.split arg-list would silently break those — a
        # behaviour change. Not attacker input; bounded by `timeout`, best-effort (failure → "").
        proc = subprocess.run(command, shell=True, input=raw,  # nosec B602
                              capture_output=True, text=True, timeout=_WRAP_TIMEOUT_SECS)
        first = (proc.stdout or "").splitlines()
        return first[0].strip() if first else ""
    except Exception:
        return ""


def statusline_main(argv: Optional[List[str]] = None) -> int:
    """Print mokata's pipeline-stage badge for the Claude Code statusLine. With ``--wrap``
    it first runs the user's pre-existing statusLine command and prepends its output
    (compose, don't clobber). ALWAYS exits 0 and prints at most ONE line; prints nothing
    when there's nothing to show."""
    parser = argparse.ArgumentParser(description="mokata statusLine badge")
    parser.add_argument("--wrap", default=None,
                        help="a pre-existing statusLine command to run + compose with")
    args, _unknown = parser.parse_known_args(argv)

    raw = _read_statusline_stdin()
    cwd, session_name = _cwd_and_session_name(raw)

    their = _run_wrapped(args.wrap, raw) if args.wrap else ""
    mine = _mokata_segment(cwd, session_name)

    line = "  ".join(part for part in (their, mine) if part)
    if line:
        sys.stdout.write(line + "\n")
    return 0


# ======================================================================================
# dispatcher
# ======================================================================================

_SUBCOMMANDS = {
    "session-start": session_start_main,
    "secret-guard": secret_guard_main,
    "statusline": statusline_main,
}


def main(argv: Optional[List[str]] = None) -> int:
    """`mokata-hook <subcommand> [args…]`. An unknown/missing subcommand degrades to a
    clean no-op (exit 0) — a hook must never block a session on a routing mistake."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(
            "mokata-hook: missing subcommand (session-start | secret-guard | statusline)\n")
        return 0
    sub, rest = argv[0], argv[1:]
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        sys.stderr.write(
            f"mokata-hook: unknown subcommand {sub!r} "
            f"(expected session-start | secret-guard | statusline)\n")
        return 0
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
