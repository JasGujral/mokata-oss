"""`mokata-hook` — the console entry point for mokata's Claude Code hooks (Stage 53b).

mokata's hooks used to be launched by a static plugin ``hooks.json`` that ran
``sh launch.sh`` to resolve *a* ``python3`` / ``python`` / ``py`` at run time. That chain
is fragile: Windows has no ``sh``, a GUI-launched macOS Claude Code runs with a minimal
PATH, and any stale config that baked a bare ``python3`` never re-resolves — so test users
hit ``python3: command not found`` on the PreToolUse hook.

The bundled MCP server never had this problem: it's launched as the ``mokata-mcp`` **console
entry point**, resolved exactly like any other installed script. This module gives the hooks
the *same* mechanism: a single ``mokata-hook`` entry point with its subcommands —

    mokata-hook session-start [--plugin-root <dir>]
    mokata-hook secret-guard  [--text … --path … --send]
    mokata-hook gate-guard    [--path … --cwd …]          (SI.1 — the run-state gates)
    mokata-hook statusline    [--wrap <command>]

No bare ``python3``, no ``sh``, no PATH guessing. If ``mokata-mcp`` resolves for a user (it
must, for the MCP server to work), ``mokata-hook`` resolves identically.

Two of these are PreToolUse hooks and BOTH may block with exit 2, for the two sanctioned reasons:
``secret-guard`` is the SECURITY block (G4/I1 — never overridable), and ``gate-guard`` is the
run-state GATE block (SI.1 — a methodology gate, overridable under P14's discipline). They are
independent entries on the same event; neither knows about the other.

This module is the **single source of truth** for the hook runtime. The standalone
``hooks/session_start.py`` / ``hooks/secret_guard.py`` / ``hooks/gate_guard.py`` scripts are thin
shims that import from here, so the legacy ``launch.sh`` fallback (and any direct invocation) still
works.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from typing import List, Optional

# Stage 3d.1: with the mcp_admin ↔ harness_setup cycle broken (shared pieces extracted to
# harness_paths), this import — previously lazy inside `_mcp_reachability_warning` — is top-level.
from .mcp_admin import unreachable_registration

# D5 — the degrade notice. TOP-LEVEL on purpose, even though both users are inside an `except`:
# an import that lives in the handler for "the engine could not be imported" is an import that can
# fail exactly when it is needed, turning a swallow into a traceback. `degrade` is stdlib-only at
# module scope (its `team_health`/`dsn` imports are lazy), so it costs the SI.2 hot path nothing —
# and if it cannot import, `hook_cli` itself never loaded and the hook was already dead.
from .degrade import FAILURE_ENGINE, note_degraded

# Security-block exit code (PreToolUse): a non-zero exit blocks the tool call.
BLOCK_EXIT = 2

# Keys inside tool_input that name the target path (for path-based detection, e.g. .env).
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file")

# How long a hook waits for stdin before giving up and using its default. Claude Code's real
# invocation writes its JSON payload and closes stdin, so the read returns instantly at EOF —
# this is pure headroom, not truncation. It only bites when stdin is an OPEN non-TTY pipe with
# no writer, where a bare read() would block forever and hang the session.
_STDIN_READ_TIMEOUT_SECS = 2.0


def _read_stdin_bounded(timeout: float = _STDIN_READ_TIMEOUT_SECS) -> Optional[str]:
    """Read all of stdin, but never block a session longer than ``timeout`` seconds.

    A bare ``sys.stdin.read()`` blocks FOREVER when stdin is an OPEN non-TTY pipe with no
    writer (a mis-wired launcher, a shell that leaves the write end open) — violating the
    "hooks never block a session" contract. So we read on a daemon thread and give up after
    ``timeout``, returning None (the caller falls back to its own default). Reuses the
    ``mcp_admin.handshake`` bounded-reader pattern: cross-platform (no SIGALRM), Windows-safe.
    Never raises. The normal path (payload + EOF) returns instantly, well inside ``timeout``."""
    stream = sys.stdin
    if stream is None:
        return None
    holder: dict = {}

    def _reader() -> None:
        try:
            holder["raw"] = stream.read()
        except Exception:            # torn-down/errored stream → treat as no input
            holder["raw"] = None

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None                  # timed out: an open pipe with no writer — don't block
    return holder.get("raw")


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
    #
    # D5 — but NEVER a SILENT no-op. This is a SECURITY control: if `govern` cannot be imported,
    # every Write/Edit/Bash from here on proceeds UNSCANNED FOR SECRETS, forever, and the user has
    # no way to know. A disabled scanner that says nothing is worse than no scanner at all, because
    # the user believes they are protected. Exit 0 stays (a hook must never wedge the session); the
    # silence does not. Narrowed to ImportError — the class a half-installed engine actually raises
    # (the same catch `session_start_main` already uses for this lazy-import pattern) — so a real
    # BUG in `govern` surfaces as a crash instead of hiding inside the security no-op.
    #
    # A hook is a short-lived process handling ONE tool call, so once-per-process == one warning per
    # unscanned write. That is not spam: it is the correct loudness for a security control that is
    # not running.
    try:
        from .govern import scan
    except ImportError as exc:
        note_degraded("secret-guard", FAILURE_ENGINE,
                      fallback="writes are NOT being scanned for secrets",
                      fix="Reinstall mokata (`pip install -U mokata`)", detail=str(exc))
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
# gate-guard (PreToolUse, sync RUN-STATE GATE hook — SI.1 / A1)
# ======================================================================================
# The lock on the door doc 74 named: mokata's gates all fired INSIDE its MCP write tools, so the
# model could bypass every one of them by using its NATIVE Write/Edit. This hook runs on those
# native tools and blocks (exit 2) a write that violates a run-state gate — enforcement stops being
# advice the model may ignore and becomes an exit code it cannot.
#
# It is the I/O only; `gate_hook.check_write` is the decision (and is unit-tested without a
# subprocess). Sync + exit-2 is sanctioned here: this IS the gate class G4 reserves the hard block
# for. The overriding contract is FAIL-OPEN — a hook that wedges the editor would be uninstalled,
# and an uninstalled gate enforces nothing — so EVERY uncertainty (no mokata repo, no run,
# ambiguous window, unreadable state, a bug in our own code) exits 0.


def gate_guard_main(argv: Optional[List[str]] = None) -> int:
    """Block a native file-mutation tool call that violates a persisted run-state gate.

    Reads the PreToolUse envelope on stdin (`tool_input` for the target path, `cwd` for the repo,
    `session_id` for the once-per-window notice). Exit 2 + a one-line actionable reason on a
    violation; exit 0 otherwise. Never raises: the outermost `except` is the fail-open floor."""
    parser = argparse.ArgumentParser(description="mokata run-state gate (sync gate hook)")
    parser.add_argument("--path", default=None, help="target path (bypasses stdin; for testing)")
    parser.add_argument("--cwd", default=None, help="repo dir (bypasses stdin; for testing)")
    args, _unknown = parser.parse_known_args(argv)

    try:
        # Imported lazily (like secret-guard's engine import) so a half-installed environment
        # degrades to a clean no-op instead of crashing a hook. The module's own import surface is
        # SI.2-cheap: stdlib + state + tdd_state — no engine, no govern, no config.
        from .gate_hook import (check_write, find_mokata_root, notice_once, target_content,
                                target_path)

        cwd, session_id, path = args.cwd or os.getcwd(), "", args.path
        content = ""
        if args.path is None:
            raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
            payload = {}
            if raw and raw.strip():
                try:
                    loaded = json.loads(raw)
                    payload = loaded if isinstance(loaded, dict) else {}
                except (ValueError, TypeError):
                    payload = {}                  # unparseable envelope -> nothing to gate on
            cwd = args.cwd or payload.get("cwd") or os.getcwd()
            sid = payload.get("session_id")
            session_id = sid if isinstance(sid, str) else ""
            tool_input = payload.get("tool_input")
            path = target_path(tool_input)
            # SI-DEV — the envelope carries the TEXT about to be written, and this hook used to
            # throw it away. It is what lets the scope gate see a deferred feature being added to an
            # otherwise-authorized file (the incident's actual shape). Matched against literal
            # markers only; never interpreted, never sent anywhere.
            content = target_content(tool_input)

        if not path:
            return 0                              # no file target (or no envelope) -> nothing to do

        root = find_mokata_root(cwd)
        if root is None:
            return 0                              # not a mokata project -> zero cost, instant allow

        outcome = check_write(root, path, content=content)

        if outcome.notice:
            once = notice_once(root, session_id, outcome.notice)
            if once:
                sys.stderr.write(once + "\n")

        if outcome.allowed:
            return 0
        sys.stderr.write(f"BLOCKED [{outcome.gate}] {outcome.reason}\n")
        return BLOCK_EXIT
    except Exception as exc:
        # FAIL OPEN. A gate that breaks the harness is worse than a gate that misses one write:
        # the user can always re-run the tool, but they cannot edit through a crashed hook. The
        # `return 0` and the BROAD catch are both correct and both stay: this IS the fail-open
        # floor, and a floor that only catches the failures we predicted is not a floor.
        #
        # D5 — what was wrong was the SILENCE. Every EXPECTED case (no file target, not a mokata
        # repo, an unparseable envelope) already returned 0 explicitly ABOVE, so reaching here means
        # something genuinely unexpected: a bug in `check_write`, a corrupt state file, an
        # ImportError from a half-installed engine. A gate that is persistently failing open
        # enforces NOTHING while the statusline badge and the docs tell the user governance is on —
        # a lie in the proof (P16). It still allows the write; it just says so.
        #
        # SUPPRESSION: `note_degraded` (once per PROCESS), deliberately NOT `gate_hook.notice_once`
        # (once per SESSION), even though the latter's cross-process marker is the tighter squelch:
        #   * its marker is keyed on the session_id ALONE (`.gate_notice__<sid>`), not on the
        #     message — so a gate notice already emitted this session would SILENCE this degrade,
        #     and emitting this degrade would silence a later legitimate gate notice. Re-introducing
        #     the D5 bug to save a repeated line is a bad trade;
        #   * it needs `root` and a non-empty `session_id`, and neither is guaranteed HERE — an
        #     ImportError of `gate_hook` leaves both `notice_once` and `root` unbound, and `--path`
        #     mode never parses an envelope, so `session_id` is "".
        # A hook is a one-tool-call process, so once-per-process == one line per write that went
        # ungated. For a gate enforcing nothing, that is the right loudness.
        note_degraded("gate-guard", FAILURE_ENGINE,
                      fallback="the run-state gate is FAILING OPEN and enforcing nothing",
                      fix="Run `mokata doctor`", detail=str(exc))
        return 0


# ======================================================================================
# session-start (SessionStart, async/observability hook — A4 bootstrap + Stage 23 offer)
# ======================================================================================

def _read_cwd_from_stdin() -> str:
    """Claude Code passes a JSON payload on stdin; honour its cwd if present."""
    if sys.stdin is None or sys.stdin.isatty():
        return os.getcwd()
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    if not raw or not raw.strip():
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
        # D5 — broad + silent ON PURPOSE: this is a best-effort CACHE of a hint. Nothing degrades
        # when it fails (`/mokata:init` re-derives the root), so there is no fallback to announce
        # and a notice here would be noise on a SessionStart that is working perfectly.
        pass


def _mcp_reachability_warning(root: str) -> Optional[str]:
    """One-line SessionStart warning when mokata's MCP server is registered but its command
    can't be resolved. Fast local lookup (no subprocess); None (silent) on reachable/absent
    or any error — never blocks or breaks the session."""
    try:
        reg = unreachable_registration(root=root)
        if reg is None:
            return None
        return (f"⚠ mokata-mcp: registered but its command isn't reachable ({reg.command}) — "
                "run `mokata mcp install` to repair, then restart Claude Code.")
    except Exception:
        return None


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

    briefing = build_bootstrap(surface)
    context = briefing.text
    # R11 — log the briefing's tokenizer-free chars/4 estimate so its safety margin is MEASURED.
    # Claude Code reports no real token count at inject time, so we log the ESTIMATE alone and
    # wire the comparison point for when an actual arrives (never fabricate one). Fully guarded:
    # observability that never blocks, slows, or raises in the SessionStart path.
    try:
        from .govern import log_bootstrap_calibration
        log_bootstrap_calibration(surface, briefing.token_estimate)
    except Exception:
        pass
    # Stage 3b.3 — minimal MCP reachability warning. If mokata's MCP server is registered but
    # its command can't be resolved (a broken auto-start), append ONE warning line + the fix.
    # A fast LOCAL lookup only (no handshake subprocess): SessionStart is async/observability
    # and must never block or slow the session. Degrade-clean: any doubt → say nothing.
    warning = _mcp_reachability_warning(root)
    if warning:
        context = context + "\n\n" + warning
    _emit(context)

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
    except (ValueError, OSError):
        # D5 — the two classes `isatty()` actually raises: ValueError on a CLOSED stream ("I/O
        # operation on closed file") and OSError on a broken/errored one. Either way there is no
        # TTY to detect, so fall through to the bounded read. Nothing else is swallowed.
        pass
    raw = _read_stdin_bounded(_STDIN_READ_TIMEOUT_SECS)
    return raw if raw is not None else ""


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
        from .progress import statusline_badge, statusline_enabled
    except Exception:
        return ""
    try:
        root = find_project_root(cwd)
        if not Surface.is_initialized(root):
            return ""
        surface = Surface.load(root)
        if not statusline_enabled(surface):
            return ""
        # TM.S1 — the statusline segment prefixes the run mode (local|team) so a session is
        # never ambiguous about which mode it's in, then the pipeline-stage badge.
        return statusline_badge(surface, session_name=session_name)
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
    "gate-guard": gate_guard_main,
    "statusline": statusline_main,
}


def main(argv: Optional[List[str]] = None) -> int:
    """`mokata-hook <subcommand> [args…]`. A missing or unknown subcommand is a
    MISCONFIGURATION (a mis-wired hook command), so it fails VISIBLY with exit 1 rather than
    a silent no-op — otherwise a mis-wired hook looks successful to Claude Code and scripts.
    It must NOT use exit 2: exit 2 is mokata's reserved SECURITY-BLOCK code, and only exit 2
    blocks a PreToolUse tool call — so exit 1 still honors "hooks never block a session on a
    routing mistake" (it fails loud without tripping the security-block path)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(
            "mokata-hook: missing subcommand "
            "(session-start | secret-guard | gate-guard | statusline)\n")
        return 1  # misconfiguration → visible exit 1 (NOT 0=silent, NOT 2=security-block)
    sub, rest = argv[0], argv[1:]
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        sys.stderr.write(
            f"mokata-hook: unknown subcommand {sub!r} "
            f"(expected session-start | secret-guard | gate-guard | statusline)\n")
        return 1  # misconfiguration → visible exit 1 (NOT 0=silent, NOT 2=security-block)
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
