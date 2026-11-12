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
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__, quiet_spawn
from .bounded_io import read_bounded, read_line_bounded
from .harness_paths import (MCP_APPROVE_TOOL_ASK, MCP_SERVER_NAME,
                            MCP_TOOL_PERMISSION, claude_mcp_config_path,
                            claude_settings_path)
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


def _terminate(tree: "quiet_spawn.Jobbed") -> None:
    """End the whole tree through the door (the only kill), THEN close our ends of the pipes.

    The order is the fix (0.0.28 I1): a timed-out bounded read leaves its reader thread blocked in
    `read()` holding the stream's lock, and a grandchild that inherited the server's stdout keeps
    that read from ever seeing EOF. Closing the stream first then blocked on the lock forever — a
    `mokata mcp status` that never returned. Ending the tree first gives the reader its EOF."""
    tree.close()
    proc = tree.proc
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass


def handshake(command: str, args: Optional[List[str]] = None,
              timeout: float = 10.0) -> HandshakeResult:
    """Spawn `command args` on stdio, send an MCP `initialize`, and classify the result.
    Fail-closed: every non-connected path returns a specific code + one-line fix; no
    exception escapes."""
    argv = [command] + list(args or [])
    try:
        # 0.0.28 I1 — through the process door: the server under test is a TREE (uvx → python),
        # and every exit path below ends all of it (`_terminate` → `Jobbed.close`).
        tree = quiet_spawn.spawn_jobbed(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
    proc = tree.proc

    try:
        proc.stdin.write(json.dumps(_INIT_REQUEST) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass                                 # server already gone; classified below

    # A1 (0.0.19) — the bounded reader that used to be written out here in full now lives in
    # `bounded_io`, because it was the ONLY bound in `src/` and the CRG stdio transport had none:
    # one unbounded `readline()` there wedged the whole MCP server (#45, #46, #53). Extracting it
    # is what let both clients share one Windows-proven shape instead of growing a third.
    # BEHAVIOUR IS UNCHANGED HERE: a read that ERRORS or reaches EOF yields empty text and falls
    # into the same death-classification block below that `holder.get("line") or ""` fed before.
    read = read_line_bounded(proc.stdout, timeout)
    if read.timed_out:
        _terminate(tree)
        return HandshakeResult(
            False, "timeout",
            f"no MCP initialize response within {timeout:.0f}s",
            "the server launched but isn't answering — run `mokata mcp start` to see why")

    line = read.text
    if not line.strip():
        # stdout closed with no response: the server died. Classify from its stderr —
        # read it BEFORE terminating (terminate closes the pipe).
        try:
            tree.wait_exit(2)
        except subprocess.TimeoutExpired:
            pass
        # A1 (0.0.19) — BOUNDED, for the same reason the read above is. `wait(timeout=2)` is
        # best-effort: when it expires the process is still alive and still holding stderr open,
        # so `stderr.read()` — which returns only at EOF — was the SECOND unbounded pipe read in
        # this function. It could only fire on the path where the server is already misbehaving,
        # which is precisely when a hang is least welcome. Non-hanging behaviour is unchanged:
        # a dead process EOFs immediately and yields exactly the same text.
        err = (read_bounded(proc.stderr.read, 2.0).text or "").strip()
        _terminate(tree)
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

    _terminate(tree)
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
    # MCP-R.D2 (UX-STUCK grant legibility): mcp__mokata__approve in permissions.ask — the
    # INTENTIONAL consent exception (AP-MCP, doc 85 §5 D26). `_merge_grant` writes it in the same
    # breath as the allow-wildcard precisely because the wildcard already MATCHES the approve tool;
    # without the explicit ask, the auto-grant would silently cover the one call that must always
    # reach a human. So its ABSENCE alongside a present wildcard is not a missing convenience —
    # it is SI.3's hole re-opened, and doctor has to be able to say so.
    approve_asked: bool = False
    approve_asked_source: Optional[Path] = None


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
    enabled = permitted = approve_asked = False
    en_src = pm_src = ask_src = None
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
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else None
        if not permitted:
            allow = perms.get("allow") if perms else None
            if isinstance(allow, list) and (
                    MCP_TOOL_PERMISSION in allow or server_wide in allow):
                permitted, pm_src = True, path
        # MCP-R.D2: the AP-MCP consent exception. A grant in EITHER scope counts, same as above
        # (Claude Code merges them), and the whole-server `mcp__mokata` ask form counts too — it
        # covers the approve tool just as the explicit entry does.
        if not approve_asked:
            ask = perms.get("ask") if perms else None
            if isinstance(ask, list) and (
                    MCP_APPROVE_TOOL_ASK in ask or server_wide in ask):
                approve_asked, ask_src = True, path
    return GrantStatus(enabled, permitted, en_src, pm_src, approve_asked, ask_src)


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
        # MCP-R.D2 (UX-STUCK grant legibility): the consent EXCEPTION, reported as its own axis so
        # a reader can tell an auto-granted surface from an ungoverned one. Present is the SHIPPED,
        # CORRECT state — say so, because "why does approve still prompt me?" is a question the
        # silent version of this line was answering nowhere.
        if g.approve_asked:
            lines.append(f"  approve-ask ✓ ({MCP_APPROVE_TOOL_ASK} in permissions.ask — the "
                         f"in-chat approve tool always prompts a human; intentional, SI.3)")
        elif g.permitted:
            # The dangerous combination, and the whole reason this axis is reported: the wildcard
            # MATCHES the approve tool, so an allow-grant without the ask lets the model's own
            # approve call sail through un-prompted.
            lines.append("  approve-ask ✗ the mcp__mokata__* allow-grant currently COVERS "
                         "mcp__mokata__approve — approvals may not prompt a human")
            lines.append(f"    Fix: run `{_INSTALL_FIX}` "
                         f"(restores {MCP_APPROVE_TOOL_ASK} to permissions.ask).")
        else:
            lines.append("  approve-ask — n/a (no allow-grant, so every call already prompts)")

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
        # 0.0.28 I1 — through the process door: an empty stdin (`MCP-STDIN-INHERITED`: under the MCP
        # server the caller's input is the JSON-RPC stream), temp-file output no grandchild can
        # hold open, and the whole tree ended on timeout.
        rc, out, err = quiet_spawn.run_jobbed(argv, timeout=timeout, env=_scrubbed_env())
    except subprocess.TimeoutExpired:
        return ("timeout", None, f"no --version reply within {timeout:.0f}s")
    except FileNotFoundError:
        return ("probe_failed", None, f"the registered command '{command}' was not found")
    except OSError as exc:
        return ("probe_failed", None, f"could not launch '{command}': {exc}")
    if rc != 0:
        tail = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
        return ("probe_failed", None, tail[-1] if tail else f"exited {rc}")
    ver = _parse_version(out)
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


# ======================================================================================
# 0.0.28 I2 — `mokata mcp docs`: the MCP tool reference, RENDERED from the live server
# ======================================================================================
# `docs/reference/mcp-tools.md` is this render, byte for byte (tests/test_i2_mcp_docs_derived).
# One source: the registry's tools as the server builds them (the same docstrings and input schemas
# `tools/list` sends), plus each tool's CLI twin from `parity.SURFACE_MATRIX`. A hand-written tool
# reference drifted from the schemas the first time either changed; a render cannot.

_DOCS_HEAD = """\
# Reference: MCP tools

<!-- GENERATED by `mokata mcp docs` from the live tool schemas. Do not edit by hand:
     regenerate with `mokata mcp docs > docs/reference/mcp-tools.md`. -->

Every tool below is what the mokata MCP server advertises in `tools/list`: the same description,
the same parameters. A parameter marked **required** must be sent; every other one has the
default shown. A closed set of values is listed under *Allowed*; any other value is refused.

## How a call can come back

- **A result.** The tool's own fields, as its *Returns* line describes.
- **`refused` with `reason_code: "invalid_input"`.** The call itself was wrong: an argument name
  the tool does not have (`did_you_mean` and `accepted` name the right ones), a required argument
  missing, a value outside its allowed set, or a boolean given as anything but `true`/`false`.
  `field` names the argument and `retry_with` gives the corrected call's shape. Nothing ran. The
  value you sent is never echoed back.
- **`refused` with `reason_code: "not-initialized"`.** mokata is not set up in that repo. The
  refusal's `remedyCommand` is `mokata init`; run it in the repo root, then call again.
- **`error`.** A real fault on mokata's side, with a hint naming its fix.

Arrays and objects may be sent as a JSON string of the same value; integers and numbers as their
decimal text; booleans as `"true"`/`"false"`.

## Gated writes: the consent flow

A tool marked *write* never writes on its first call. It returns `status: "proposed"` with a
`proposal_id` and a preview, and nothing is written. A human approves it in their own terminal
with `mokata approve <id>`; the tool is then called again with the same arguments plus that
`proposal_id`, and the write happens once. There is no argument a model can set to approve its
own write.

## First start under the plugin

Installed as the Claude Code plugin, the server starts through `uvx` with the pinned
`mokata[full]` release, so the very first start downloads it and can take a minute. Until it
answers, Claude Code shows the server as connecting. `mokata doctor --plugin` checks the route,
including whether the pinned release resolves.

"""


def _doc_type(prop: dict, defs: dict) -> str:
    """A short, human type for one schema property."""
    ref = prop.get("$ref")
    if isinstance(ref, str):
        return ref.split("/")[-1]
    if "anyOf" in prop:
        kinds = [_doc_type(a, defs) for a in prop["anyOf"] if a.get("type") != "null"]
        return " or ".join(kinds) or "null"
    if "enum" in prop:
        return "string"
    t = prop.get("type", "any")
    if t == "array":
        return f"array of {_doc_type(prop.get('items') or {}, defs)}"
    return str(t)


def _doc_enum(prop: dict, defs: dict) -> List[str]:
    for alt in prop.get("anyOf") or [prop]:
        ref = alt.get("$ref")
        alt = defs.get(ref.split("/")[-1], {}) if isinstance(ref, str) else alt
        if "enum" in alt:
            return [str(v) for v in alt["enum"]]
    return []


def _doc_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _doc_example(name: str, schema: dict) -> str:
    props = schema.get("properties") or {}
    defs = schema.get("$defs") or {}
    args = {}
    for req in schema.get("required") or []:
        allowed = _doc_enum(props.get(req, {}), defs)
        typ = _doc_type(props.get(req, {}), defs)
        args[req] = allowed[0] if allowed else f"<{typ}>"
    return f"{name}({json.dumps(args, ensure_ascii=False)})"


def _cli_twins() -> dict:
    from .parity import SURFACE_MATRIX
    twins: dict = {}
    for command, surface in sorted(SURFACE_MATRIX.items()):
        for tool in surface.mcp:
            twins.setdefault(tool, []).append(f"mokata {command}")
    return twins


def render_tool_docs() -> str:
    """The MCP tool reference as Markdown — `docs/reference/mcp-tools.md` is exactly this."""
    from .mcp.registry import TOOLS
    from .mcp.server import build_server
    kinds = {t.name: t.kind for t in TOOLS}
    tools = sorted(build_server()._tool_manager.list_tools(), key=lambda t: t.name)
    twins = _cli_twins()
    out = [_DOCS_HEAD, "## Tools", ""]
    out.append(", ".join(f"[`{t.name}`](#{t.name.replace('_', '-')})" for t in tools))
    out.append("")
    for t in tools:
        schema = t.parameters or {}
        props = schema.get("properties") or {}
        defs = schema.get("$defs") or {}
        required = set(schema.get("required") or [])
        out += [f"### {t.name.replace('_', '-')}", "",
                f"`{t.name}` · {kinds.get(t.name, 'read')}"
                + (f" · CLI twin: {', '.join(f'`{c}`' for c in twins[t.name])}"
                   if t.name in twins else ""), ""]
        out += [line.rstrip() for line in t.description.strip().splitlines()]
        out += ["", "| Parameter | Type | Required | Default | Allowed | Meaning |",
                "|---|---|---|---|---|---|"]
        for pname, prop in props.items():
            default = "" if pname in required else json.dumps(prop.get("default"),
                                                                ensure_ascii=False)
            out.append("| " + " | ".join(_doc_cell(c) for c in (
                f"`{pname}`", _doc_type(prop, defs), "yes" if pname in required else "",
                default, ", ".join(_doc_enum(prop, defs)), prop.get("description", ""))) + " |")
        out += ["", "Example: `" + _doc_example(t.name, schema).replace("`", "'") + "`", ""]
    return "\n".join(out).rstrip("\n") + "\n"
