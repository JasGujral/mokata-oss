"""Stage 3b.2 — client-side admin for the bundled mokata MCP server.

Two concerns, both CLIENT-side (distinct from ``mcp_server.py``, which IS the server):

* ``resolve_registered`` — read the Claude Code registration (project ``.mcp.json`` else
  user ``~/.claude.json`` — the same paths ``harness_setup`` writes) and return the
  command mokata is registered under.
* ``handshake`` — spawn that command over stdio, send a REAL MCP ``initialize`` JSON-RPC
  request, read the response bounded by a timeout, and classify the outcome. This speaks
  MCP's newline-delimited JSON-RPC directly and needs NO client SDK, so ``mokata mcp
  status`` works even against a broken server install — it must be able to diagnose an
  SDK-absent server that can't even start (the classic silent-dead-server case). It fails
  CLOSED: any doubt is a specific failure + a one-line fix, never a false CONNECTED and
  never an escaping traceback.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .harness_paths import (MCP_SERVER_NAME, MCP_TOOL_PERMISSION,
                            claude_mcp_config_path, claude_settings_path)


# --------------------------------------------------------------------------------------
# Registration lookup
# --------------------------------------------------------------------------------------
@dataclass
class Registration:
    command: str
    args: List[str]
    source: Path            # the config file the registration was read from


def _read_server(path: Path) -> Optional[Registration]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    entry = (data.get("mcpServers") or {}).get(MCP_SERVER_NAME)
    if not isinstance(entry, dict) or not entry.get("command"):
        return None
    args = entry.get("args") or []
    if not isinstance(args, list):
        args = []
    return Registration(command=str(entry["command"]),
                        args=[str(a) for a in args], source=path)


def resolve_registered(root: str = ".", home: Optional[str] = None) -> Optional[Registration]:
    """Return the mokata MCP registration, searching project scope then user scope — the
    same order (and paths) `harness_setup` writes. None when unregistered anywhere."""
    project = claude_mcp_config_path("project", root, home)
    user = claude_mcp_config_path("user", root, home)
    for candidate in (project, user):
        if candidate is None:
            continue
        reg = _read_server(Path(candidate))
        if reg is not None:
            return reg
    return None


# --------------------------------------------------------------------------------------
# initialize handshake (dependency-free MCP client)
# --------------------------------------------------------------------------------------
@dataclass
class HandshakeResult:
    ok: bool
    code: str                 # connected | command_not_found | sdk_absent | timeout | error
    detail: str = ""
    fix: str = ""


_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "mokata-mcp-status", "version": __version__},
    },
}


def _terminate(proc: subprocess.Popen) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def handshake(command: str, args: Optional[List[str]] = None,
              timeout: float = 10.0) -> HandshakeResult:
    """Spawn `command args` on stdio, send an MCP `initialize`, and classify the result.
    Fail-closed: every non-connected path returns a specific code + one-line fix; no
    exception escapes."""
    argv = [command] + list(args or [])
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return HandshakeResult(
            False, "command_not_found",
            f"the registered command '{command}' was not found on PATH",
            "reinstall mokata (`pip install -U mokata`), then run `mokata mcp install`")
    except OSError as exc:
        return HandshakeResult(
            False, "error", f"could not launch '{command}': {exc}",
            "check the registered command in your .mcp.json (`mokata mcp install` to repair)")

    holder: dict = {}

    def _reader() -> None:
        try:
            holder["line"] = proc.stdout.readline()
        except Exception as exc:            # pragma: no cover - stream torn down mid-read
            holder["exc"] = exc

    try:
        proc.stdin.write(json.dumps(_INIT_REQUEST) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass                                 # server already gone; classified below

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    reader.join(timeout)

    if reader.is_alive():
        _terminate(proc)
        return HandshakeResult(
            False, "timeout",
            f"no MCP initialize response within {timeout:.0f}s",
            "the server launched but isn't answering — run `mokata mcp start` to see why")

    line = holder.get("line") or ""
    if not line.strip():
        # stdout closed with no response: the server died. Classify from its stderr —
        # read it BEFORE terminating (terminate closes the pipe).
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        try:
            err = (proc.stderr.read() or "").strip()
        except Exception:
            err = ""
        _terminate(proc)
        if "No module named 'mcp'" in err or "MCP SDK is not installed" in err:
            return HandshakeResult(
                False, "sdk_absent",
                "the server exited: the MCP SDK is not installed (a required dependency)",
                "reinstall mokata — `pip install -U mokata` — to restore the SDK")
        tail = err.splitlines()[-1] if err else ""
        return HandshakeResult(
            False, "error",
            "the server exited without an MCP response" + (f": {tail}" if tail else ""),
            "run `mokata mcp start` to see the server's own error output")

    _terminate(proc)
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return HandshakeResult(
            False, "error", "the server's first output was not valid JSON-RPC",
            "run `mokata mcp start` to inspect the server output")
    if isinstance(msg, dict) and "result" in msg:
        return HandshakeResult(True, "connected")
    if isinstance(msg, dict) and "error" in msg:
        return HandshakeResult(
            False, "error", f"the server rejected initialize: {msg.get('error')}",
            "update mokata (`pip install -U mokata`)")
    return HandshakeResult(
        False, "error", "unexpected MCP response shape",
        "run `mokata mcp start` to inspect the server output")


# --------------------------------------------------------------------------------------
# Shared reporting helpers (reused by `mokata mcp status`, `setup`, and SessionStart)
# --------------------------------------------------------------------------------------
def status_lines(root: str = ".", home: Optional[str] = None,
                 timeout: float = 10.0) -> Tuple[bool, List[str]]:
    """Resolve the registration, run the `initialize` handshake, and return
    ``(connected, report_lines)`` — the printable CONNECTED/failure report. The single
    source of the status report, shared by `mokata mcp status` and `setup`'s CONNECTED
    verification so their wording can't drift. Never raises."""
    try:
        reg = resolve_registered(root, home)
        if reg is None:
            return False, ["mokata-mcp: NOT REGISTERED ✗",
                           "  Fix: run `mokata mcp install` to register the server."]
        res = handshake(reg.command, reg.args, timeout=timeout)
        if res.ok:
            return True, ["mokata-mcp: CONNECTED ✓"]
        return False, [f"mokata-mcp: NOT CONNECTED ✗ ({res.code})",
                       f"  Cause: {res.detail}",
                       f"  Fix:   {res.fix}"]
    except Exception as exc:                 # informational path — never raise
        return False, [f"mokata-mcp: status check skipped ({exc})."]


# --------------------------------------------------------------------------------------
# Grant status — did setup enable + permit mokata's MCP tools? (the "stuck-loop" gate)
# --------------------------------------------------------------------------------------
@dataclass
class GrantStatus:
    enabled: bool                       # server trusted (enabledMcpjsonServers / enableAll…)
    permitted: bool                     # mcp__mokata__* in permissions.allow
    enabled_source: Optional[Path] = None
    permitted_source: Optional[Path] = None


def _read_settings(path: Path) -> Optional[dict]:
    """Read a Claude `settings.json` as a dict, or None on any problem (missing / unparseable
    / not an object). Read-only diagnosis — never raises, never a false positive."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def grant_status(root: str = ".", home: Optional[str] = None) -> GrantStatus:
    """Read `.claude/settings.json` (project then user — the same scopes setup writes) and
    report whether the mokata MCP server is ENABLED (`enabledMcpjsonServers` contains
    `mokata`, or `enableAllProjectMcpServers` is true) and whether its tools are PERMITTED
    (`mcp__mokata__*` — or the whole-server `mcp__mokata` — in `permissions.allow`). A grant in
    EITHER scope counts (Claude Code merges them). Never raises."""
    enabled = permitted = False
    en_src = pm_src = None
    server_wide = f"mcp__{MCP_SERVER_NAME}"          # allow-all-tools form of the grant
    for scope in ("project", "user"):
        try:
            path = claude_settings_path(scope, root, home)
        except Exception:
            continue
        data = _read_settings(path)
        if not data:
            continue
        if not enabled:
            servers = data.get("enabledMcpjsonServers")
            if data.get("enableAllProjectMcpServers") is True or (
                    isinstance(servers, list) and MCP_SERVER_NAME in servers):
                enabled, en_src = True, path
        if not permitted:
            perms = data.get("permissions")
            allow = perms.get("allow") if isinstance(perms, dict) else None
            if isinstance(allow, list) and (
                    MCP_TOOL_PERMISSION in allow or server_wide in allow):
                permitted, pm_src = True, path
    return GrantStatus(enabled, permitted, en_src, pm_src)


# --------------------------------------------------------------------------------------
# Full status — the shared reporter for `mokata mcp status` AND `mokata doctor`
# --------------------------------------------------------------------------------------
@dataclass
class FullStatus:
    registered: bool
    enabled: bool
    permitted: bool
    connected: bool
    lines: List[str]

    @property
    def ok(self) -> bool:
        return self.registered and self.enabled and self.permitted and self.connected


_INSTALL_FIX = "mokata mcp install"


def full_status(root: str = ".", home: Optional[str] = None,
                timeout: float = 10.0) -> FullStatus:
    """The complete MCP wiring check — registered? enabled? permitted? CONNECTED? — each with
    the one-line fix when missing. The SINGLE source shared by `mokata mcp status` and
    `mokata doctor` so the two can't drift. Fail-closed and never raises."""
    lines: List[str] = []
    try:
        reg = resolve_registered(root, home)
        registered = reg is not None
        if registered:
            lines.append("mokata-mcp: REGISTERED ✓")
        else:
            lines.append("mokata-mcp: NOT REGISTERED ✗")
            lines.append(f"  Fix: run `{_INSTALL_FIX}` to register the server.")

        g = grant_status(root, home)
        if g.enabled:
            lines.append("  enabled ✓   (mokata trusted in enabledMcpjsonServers)")
        else:
            lines.append("  enabled ✗   Claude Code will prompt to trust the server")
            lines.append(f"    Fix: run `{_INSTALL_FIX}` "
                         f"(adds enabledMcpjsonServers: [\"{MCP_SERVER_NAME}\"]).")
        if g.permitted:
            lines.append(f"  permitted ✓ ({MCP_TOOL_PERMISSION} in permissions.allow)")
        else:
            lines.append("  permitted ✗ Claude Code will gate each mcp__mokata__* call "
                         "(the stuck-loop)")
            lines.append(f"    Fix: run `{_INSTALL_FIX}` "
                         f"(adds {MCP_TOOL_PERMISSION} to permissions.allow).")

        connected = False
        if registered:
            res = handshake(reg.command, reg.args, timeout=timeout)
            if res.ok:
                connected = True
                lines.append("  connected ✓")
            else:
                lines.append(f"  connected ✗ ({res.code}) — {res.detail}")
                lines.append(f"    Fix: {res.fix}")
        return FullStatus(registered, g.enabled, g.permitted, connected, lines)
    except Exception as exc:                 # informational path — never raise
        return FullStatus(False, False, False, False,
                          [f"mokata-mcp: status check skipped ({exc})."])


def _command_resolves(command: str) -> bool:
    """True if `command` names a launchable program — on PATH (bare name) OR an existing
    executable file (absolute path). Fast + local: no subprocess."""
    if not command:
        return False
    if shutil.which(command):
        return True
    p = Path(command)
    return p.is_file() and os.access(str(p), os.X_OK)


def unreachable_registration(root: str = ".",
                             home: Optional[str] = None) -> Optional[Registration]:
    """SessionStart's lightweight reachability probe: return the mokata registration IFF it
    exists but its command can't be resolved (a broken auto-start worth one warning line).
    Returns None when not registered OR when the command resolves — the caller stays silent.
    A pure local lookup (`shutil.which` / path-exists) — NO handshake subprocess, so it can
    never block or slow a session. Never raises."""
    try:
        reg = resolve_registered(root, home)
        if reg is None or _command_resolves(reg.command):
            return None
        return reg
    except Exception:
        return None
