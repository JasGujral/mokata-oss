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
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .harness_paths import (MCP_SERVER_NAME, MCP_TOOL_PERMISSION,
                            claude_mcp_config_path, claude_settings_path)
from .plugin_cache import read_plugin_root


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


# ======================================================================================
# B-VER — version-parity preventive check (the "did I upgrade the server Claude Code will
# actually launch?" question). The shared seam `mokata mcp status`, `mokata doctor`,
# `mokata setup claude`, and `mokata mcp install` all report through — and DB.S1 later folds
# `version_parity` into its `doctor` DSN deep-check (the B-VER stage).
#
# Setup already verifies the registered server is CONNECTED, but never that the command Claude
# Code launches serves the SAME VERSION as the CLI that wrote the registration. The 2026-07-14
# incident: `pip install -U mokata` + `setup claude` still ran the OLD server. Four undetected
# stale paths, each a NAMED finding here: a stale running process / multi-env drift (`version_
# parity`), scope shadowing (`scope_shadow`), plugin shadowing (`plugin_shadow`).
# ======================================================================================

# Env keys whose VALUES are secret-shaped (DSN/credentials). The probe launches
# `<command> --version`, which needs none of them — so the child gets a scrubbed environment
# and a finding can never carry a DSN (B-VER secret-safety).
_SECRET_KEY_RE = re.compile(
    r"(DSN|DATABASE_URL|PASSWORD|SECRET|TOKEN|API[_-]?KEY|_KEY|PRIVATE|CREDENTIAL)", re.I)

# A version-ish token: two-or-more dotted numeric groups, optional pre-release/build tail.
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+(?:[.\-+][0-9A-Za-z.]+)?")


def _scrubbed_env() -> dict:
    """A copy of the environment with secret-shaped keys removed — handed to the version probe
    so no DSN/credential is ever passed to the probed subprocess (which does not need one)."""
    return {k: v for k, v in os.environ.items() if not _SECRET_KEY_RE.search(k)}


def _parse_version(text: str) -> Optional[str]:
    """The version token from a `--version` reply (`mokata-mcp --version` prints the bare
    version, but stay lenient about surrounding text)."""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return m.group(0) if m else (text.strip() or None)


def _probe_version(command: str, args: Optional[List[str]],
                   timeout: float) -> Tuple[str, Optional[str], str]:
    """Launch `command args --version` and classify: ('ok', version, '') | ('mismatch' handled
    by the caller) | ('timeout', None, detail) | ('probe_failed', None, detail). Bounded and
    fail-open — it never raises; a hang is a TIMEOUT, a non-zero/absent command is PROBE-FAILED.
    The env is scrubbed of secrets; the child needs none to print a version."""
    argv = [command] + list(args or []) + ["--version"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=_scrubbed_env())
    except subprocess.TimeoutExpired:
        return ("timeout", None, f"no --version reply within {timeout:.0f}s")
    except FileNotFoundError:
        return ("probe_failed", None, f"the registered command '{command}' was not found")
    except OSError as exc:
        return ("probe_failed", None, f"could not launch '{command}': {exc}")
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
        return ("probe_failed", None, tail[-1] if tail else f"exited {proc.returncode}")
    ver = _parse_version(proc.stdout)
    if not ver:
        return ("probe_failed", None, "answered --version with no parseable version")
    return ("ok", ver, "")


@dataclass
class VersionParityFinding:
    """The version-parity verdict for the registered mokata server. `status` is one of
    ``match`` (quiet pass) · ``mismatch`` · ``probe_failed`` · ``timeout`` · ``unregistered``.
    A read-only diagnostic (doc 85 §3 `*Finding`); `render` is the loud printable report."""
    status: str
    cli_version: str
    registered_command: str
    registered_version: Optional[str] = None
    source: Optional[Path] = None
    detail: str = ""
    registered_args: List[str] = field(default_factory=list)

    @property
    def registered_invocation(self) -> str:
        """The full launched command line (command + args) — the which-env-owns-what identity."""
        return " ".join([self.registered_command, *self.registered_args]).strip()

    def render(self, quiet_when_ok: bool = True) -> List[str]:
        # A clean MATCH is a QUIET pass on the write paths (setup/install stay byte-identical);
        # the status/doctor surfaces pass quiet_when_ok=False to show the OK line. Unregistered is
        # always silent here — `status_lines`/`full_status` already report "NOT REGISTERED".
        if self.status == "unregistered":
            return []
        if self.status == "match":
            return [] if quiet_when_ok else [
                f"mokata-mcp: version parity OK ✓ ({self.cli_version})"]
        src = f"  (from {self.source})" if self.source else ""
        cli_env = f"  at {sys.executable}"
        if self.status == "mismatch":
            return [
                "mokata-mcp: VERSION MISMATCH ✗ — the registered server is a DIFFERENT version "
                "than this CLI.",
                f"  registered: {self.registered_version} at {self.registered_invocation}{src}",
                f"  this CLI:   {self.cli_version}{cli_env}",
                "  Cause: Claude Code keeps launching the stale server until you RESTART it — and "
                "the registered command must point at the environment you upgraded.",
                "  Fix:   restart Claude Code; if it persists, run `mokata mcp install` to "
                "re-point the registration at this environment.",
            ]
        if self.status == "probe_failed":
            return [
                "mokata-mcp: VERSION PROBE FAILED ✗ — the registered server did not answer "
                "`--version` (likely a STALE install predating this check).",
                f"  registered: {self.registered_invocation}{src}",
                f"  this CLI:   {self.cli_version}{cli_env}",
                f"  detail: {self.detail}" if self.detail else "  detail: (no output)",
                "  Fix: restart Claude Code; if it persists, reinstall mokata "
                "(`pip install -U mokata`) then `mokata mcp install`.",
            ]
        # timeout
        return [
            "mokata-mcp: VERSION PROBE TIMED OUT ✗ — no `--version` reply "
            "(setup still succeeded).",
            f"  registered: {self.registered_command}{src}",
            "  Fix: run `mokata mcp status` to diagnose, then restart Claude Code.",
        ]


def version_parity_for(command: str, args: Optional[List[str]] = None,
                       source: Optional[object] = None, *,
                       cli_version: Optional[str] = None,
                       timeout: float = 5.0) -> VersionParityFinding:
    """Probe an EXPLICIT registered command (the exact string the write path just wrote) and
    classify it against the running CLI's version. This is the shared probe the write paths call
    on the just-written registration; `version_parity` wraps it for the resolve-then-probe path."""
    cli_version = cli_version or __version__
    code, ver, detail = _probe_version(command, args, timeout)
    status = ("match" if ver == cli_version else "mismatch") if code == "ok" else code
    return VersionParityFinding(
        status=status, cli_version=cli_version, registered_command=command,
        registered_version=ver, source=Path(source) if source else None, detail=detail,
        registered_args=[str(a) for a in (args or [])])


def version_parity(root: str = ".", home: Optional[str] = None, *,
                   timeout: float = 5.0,
                   cli_version: Optional[str] = None) -> VersionParityFinding:
    """Resolve the registered mokata server (project then user scope — `resolve_registered`) and
    return its version-parity finding. The single shared probe `mokata mcp status` and
    `mokata doctor` call (DB.S1 folds this into its doctor deep-check). Never raises."""
    try:
        reg = resolve_registered(root, home)
    except Exception:
        reg = None
    if reg is None:
        return VersionParityFinding(status="unregistered",
                                    cli_version=cli_version or __version__,
                                    registered_command="", registered_version=None)
    return version_parity_for(reg.command, reg.args, reg.source,
                              cli_version=cli_version, timeout=timeout)


@dataclass
class ScopeShadowFinding:
    """Both-scope sweep: is mokata registered in BOTH project `.mcp.json` and user
    `~/.claude.json`? If so, Claude Code uses the project one (resolve_registered precedence) and
    the user one is a stale-shadow risk. Read-only; `render` names the winner and the loser path."""
    shadowed: bool
    winner_scope: str = ""
    winner_path: Optional[Path] = None
    loser_scope: str = ""
    loser_path: Optional[Path] = None

    def render(self) -> List[str]:
        if not self.shadowed:
            return []
        return [
            "mokata-mcp: SCOPE SHADOW ✗ — mokata is registered in BOTH scopes; Claude Code uses "
            f"the {self.winner_scope} one.",
            f"  active (wins): {self.winner_path}  [{self.winner_scope} scope]",
            f"  shadowed:      {self.loser_path}  [{self.loser_scope} scope]  "
            "← stale-shadow risk; remove mokata's entry here to avoid confusion.",
        ]


def scope_shadow(root: str = ".", home: Optional[str] = None) -> ScopeShadowFinding:
    """Detect a mokata registration present in BOTH scopes. Project wins (the order/paths
    `resolve_registered` and `harness_setup` use), so the user-scope copy is flagged as the
    stale-shadow risk with its exact path to clean. Read-only. Never raises."""
    try:
        proj = Path(claude_mcp_config_path("project", root, home))
        user = Path(claude_mcp_config_path("user", root, home))
        has_proj = _read_server(proj) is not None
        has_user = _read_server(user) is not None
    except Exception:
        return ScopeShadowFinding(False)
    if has_proj and has_user:
        return ScopeShadowFinding(True, "project", proj, "user", user)
    return ScopeShadowFinding(False)


@dataclass
class PluginShadowFinding:
    """A mokata Claude Code plugin is installed: it launches its OWN bundled server regardless of
    pip upgrades, so a `pip install -U mokata` won't change the plugin's server."""
    plugin_root: Path

    def render(self) -> List[str]:
        return [
            f"mokata-mcp: PLUGIN PRESENT ⚠ — the mokata Claude Code plugin is installed "
            f"({self.plugin_root}).",
            "  It launches its OWN bundled server regardless of pip — `pip install -U mokata` "
            "won't change the plugin's server. Update the plugin too "
            "(`/plugin marketplace update`).",
        ]


def plugin_shadow(home: Optional[str] = None) -> Optional[PluginShadowFinding]:
    """Return a finding IFF a mokata plugin registration is detectable — the recorded plugin root
    (`plugin_cache.read_plugin_root`, written by the SessionStart hook) carries a
    `.claude-plugin/plugin.json` that declares the mokata MCP server. None otherwise (silent).
    Never raises."""
    try:
        root = read_plugin_root(home=home)
        if not root:
            return None
        manifest = Path(root) / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
            return PluginShadowFinding(Path(root))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def parity_lines(root: str = ".", home: Optional[str] = None, *,
                 timeout: float = 5.0, quiet_when_ok: bool = True) -> List[str]:
    """The shared version-parity report block — version parity + scope shadow + plugin shadow —
    printed by `setup`, `mcp install`, `mcp status`, and `doctor` so their wording can't drift.
    Loud-only by default (a clean run emits nothing, keeping the write paths byte-identical);
    the status/doctor surfaces pass `quiet_when_ok=False` to also show the OK line. Never raises."""
    lines: List[str] = []
    try:
        lines.extend(version_parity(root, home, timeout=timeout).render(
            quiet_when_ok=quiet_when_ok))
        lines.extend(scope_shadow(root, home).render())
        ps = plugin_shadow(home)
        if ps is not None:
            lines.extend(ps.render())
    except Exception as exc:                 # informational path — never raise
        return [f"mokata-mcp: version-parity check skipped ({exc})."]
    return lines
