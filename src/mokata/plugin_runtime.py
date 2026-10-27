"""The plugin route's runtime (0.0.26 L8, `MP.S1` / G-1) — one pinned mokata for MCP, hooks and CLI.

A stranger with Claude Code and `uv` runs `/plugin marketplace add JasGujral/mokata-oss` and
`/plugin install mokata@mostack` and must get a WORKING mokata with no `pip` step. The plugin is a
static checkout, so everything it starts is pinned to the plugin's OWN version:

  * the MCP server — `plugin.json` spawns `uvx --from "mokata[full]==<plugin version>" mokata-mcp`
    (the version is a guarded field, `packaging.read_version_fields`, so the pin cannot drift);
  * the hooks — `hooks/mokata-hook-launch` runs, in order: the interpreter of that SAME pinned uvx
    environment (recorded by the MCP server, below), else any Python 3 running the plugin's own
    `src/` (identical code by construction), else `uvx --from "mokata==<version>" mokata-hook`;
  * the CLI (`/mokata:doctor`, `/mokata:review-window`'s fallback) — `hooks/mokata-launch`, the
    same pinned environment.

Why the hooks do not simply call `uvx` too: measured on the 0.0.26 build box, a warm `uvx --from
mokata==X python -c pass` costs ~150 ms more than a bare interpreter, and an observe hook's whole
budget is 50 ms (`perf.HOOK_BUDGET_TARGETS_MS`). So the MCP server — which runs INSIDE the pinned
environment for the whole session — records that environment's interpreter, and the hook shim
`exec`s it directly. No version is ever guessed: the record's file name carries the version, and
the shim only reads the record for its own version.

The records live in `${CLAUDE_PLUGIN_DATA}` — the per-plugin data directory Claude Code allocates,
keeps across updates and deletes on uninstall. They are a CACHE of where the pinned environment
is (no code, memory or config; never in the user's repository), so P2's human gate does not apply,
in the same way as `plugin_cache`'s `~/.mokata/plugin-root`. Deleting them costs one slower hook.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from . import __version__

#: The extra the plugin's uvx environment carries: everything `--mode full` uses, IDE included
#: (0.0.26 L1's `full`), so the plugin route reaches the CLI route's end state with no pip step.
PIN_EXTRA = "full"

#: `${CLAUDE_PLUGIN_DATA}` file names. The runtime record is per VERSION, so a shim of version X
#: can never pick up the interpreter of version Y.
RUNTIME_RECORD_PREFIX = "runtime-"
RUNTIME_RECORD_SUFFIX = ".txt"
MCP_STATUS_FILE = "mcp-server.json"
HOOK_STATUS_FILE = "hooks-seen.json"

#: The shims and the CLI launcher, relative to the plugin root.
HOOK_SHIM_REL = ("src", "mokata", "hooks", "mokata-hook-launch")
CLI_LAUNCHER_REL = ("src", "mokata", "hooks", "mokata-launch")

#: Measured at 0.0.26 L8 (Linux x86_64, a fresh uv cache, `uvx --from "mokata[full] @ file://…"`):
#: 98 packages, ~400 MB unpacked in uv's cache, 13 s on the build box's connection. Said once, on
#: the first session, so a slow first start is expected rather than mistaken for a broken plugin.
FIRST_RUN_COST = "about 100 packages (~400 MB in uv's cache; seconds on a fast link, minutes on a slow one)"

#: One line per OS — the official astral installers, plus each OS's package manager.
UV_INSTALL_LINES: Tuple[Tuple[str, str], ...] = (
    ("macOS / Linux", "curl -LsSf https://astral.sh/uv/install.sh | sh   (or: brew install uv)"),
    ("Windows", 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
                "   (or: winget install --id=astral-sh.uv -e)"),
)

#: Where the uv installers put `uvx` — a GUI-launched Claude Code's PATH often omits them.
_UV_DIRS_POSIX = ("~/.local/bin", "~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin")
_UV_DIRS_WINDOWS = ("~/.local/bin", "~/.cargo/bin", "~/AppData/Local/Microsoft/WinGet/Links")


#: The interpreter request every plugin `uvx` carries (0.0.26 cut). Without it uv runs its DEFAULT
#: Python — a uv-managed 3.10 or macOS's system 3.9 — and the 3.12+ package is "unsatisfiable", so
#: the plugin's MCP server never starts. Measured at the cut on the plugin-route CI leg's own shape.
UV_PYTHON = ">=3.12"


def pin_spec(version: str = __version__, extra: Optional[str] = PIN_EXTRA) -> str:
    """The requirement every plugin launch pins: `mokata[full]==<version>`."""
    return f"mokata[{extra}]=={version}" if extra else f"mokata=={version}"


def runtime_record_name(version: str = __version__) -> str:
    return f"{RUNTIME_RECORD_PREFIX}{version}{RUNTIME_RECORD_SUFFIX}"


def on_plugin_route(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is this process serving the plugin route? A plugin hook or MCP server gets
    `CLAUDE_PLUGIN_ROOT` from Claude Code; the plugin's CLI launcher exports `MOKATA_VIA_PLUGIN`."""
    env = os.environ if env is None else env
    return bool(env.get("CLAUDE_PLUGIN_ROOT") or env.get("MOKATA_VIA_PLUGIN"))


def plugin_data_dir(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`${CLAUDE_PLUGIN_DATA}` — exported by Claude Code to a plugin's MCP servers and hooks only."""
    value = (os.environ if env is None else env).get("CLAUDE_PLUGIN_DATA") or ""
    return value or None


def _portable(path: str) -> str:
    # Git Bash runs the hook shim on Windows; it accepts `C:/…` but not every `C:\…` spelling.
    # An ABSOLUTE host path (an interpreter), not a repo NAME — so not `repo_paths`: the stdlib's
    # Windows path type owns the respelling, drive and UNC (`//server/share`) prefixes included.
    if os.name != "nt":
        return path
    from pathlib import PureWindowsPath
    return PureWindowsPath(path).as_posix()


def _write(path: str, text: str) -> None:
    # LF on every OS: the hook shim reads the runtime record with `read -r`, which keeps a `\r` —
    # Windows' text-mode CRLF made the recorded interpreter `python.exe\r`, never executable, and
    # every plugin hook fell off the pinned rung (0.0.26 cut, plugin route · windows-latest).
    from .atomicfile import atomic_write_text
    atomic_write_text(path, text, newline="\n")


def record_runtime(env: Optional[Mapping[str, str]] = None, *, version: str = __version__,
                   executable: Optional[str] = None, pid: Optional[int] = None,
                   now: Optional[float] = None) -> Optional[str]:
    """Called by the plugin-launched MCP server at startup: record THIS interpreter (the pinned uvx
    environment's) for the hook shim and CLI launcher, and the server's own status for doctor.

    Returns the runtime record's path, or None when not on the plugin route (no
    `CLAUDE_PLUGIN_DATA`) or the write failed. Never raises: the server must start regardless."""
    data = plugin_data_dir(env)
    if not data:
        return None
    exe = _portable(executable or sys.executable)
    record = os.path.join(data, runtime_record_name(version))
    try:
        current = None
        if os.path.isfile(record):
            with open(record, encoding="utf-8") as fh:
                current = fh.read().strip()
        if current != exe:
            _write(record, exe + "\n")
        # A record of another version is dead weight after an update — and the shim of that
        # version is gone with it, so nothing can read it.
        for name in os.listdir(data):
            if (name.startswith(RUNTIME_RECORD_PREFIX) and name.endswith(RUNTIME_RECORD_SUFFIX)
                    and name != os.path.basename(record)):
                try:
                    os.unlink(os.path.join(data, name))
                except OSError:
                    pass  # D5 — a stale cache entry that will not delete is harmless
        _write(os.path.join(data, MCP_STATUS_FILE), json.dumps({
            "version": version, "python": exe, "pid": os.getpid() if pid is None else pid,
            "started_at": time.time() if now is None else now}, indent=1) + "\n")
        return record
    except (OSError, ValueError):
        # D5 — a cache that cannot be written costs the hooks their fast rung (they fall back to the
        # plugin's own source or uvx); the server itself is unaffected, so it must still start.
        return None


def record_hook_seen(subcommand: str, env: Optional[Mapping[str, str]] = None, *,
                     now: Optional[float] = None) -> None:
    """A plugin-route hook fired: stamp it for `doctor --plugin` ("hooks firing"). Called from the
    plugin's SessionStart only (one small write per session, never per tool call). Never raises."""
    data = plugin_data_dir(env)
    if not data:
        return
    try:
        _write(os.path.join(data, HOOK_STATUS_FILE), json.dumps({
            "subcommand": subcommand, "version": __version__, "python": _portable(sys.executable),
            "at": time.time() if now is None else now}, indent=1) + "\n")
    except (OSError, ValueError):
        pass  # D5 — observability for doctor; a missed stamp is reported as "not seen", never lost work


def _read_json(path: str) -> Optional[Dict[str, object]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_runtime(data_dir: Optional[str], version: str = __version__) -> Optional[str]:
    """The recorded pinned interpreter for `version`, if it is still there (uv may prune its cache)."""
    if not data_dir:
        return None
    try:
        with open(os.path.join(data_dir, runtime_record_name(version)), encoding="utf-8") as fh:
            exe = fh.read().strip()
    except OSError:
        return None
    return exe if exe and os.path.isfile(exe) else None


def find_uvx(env: Optional[Mapping[str, str]] = None, *, windows: Optional[bool] = None
             ) -> Optional[str]:
    """`uvx` on PATH, else where the uv installers put it (the same list the hook shim searches)."""
    import shutil
    env = os.environ if env is None else env
    windows = (os.name == "nt") if windows is None else windows
    hit = shutil.which("uvx", path=env.get("PATH"))
    if hit:
        return hit
    dirs = _UV_DIRS_WINDOWS if windows else _UV_DIRS_POSIX
    home = env.get("USERPROFILE" if windows else "HOME") or os.path.expanduser("~")
    for d in dirs:
        d = os.path.join(home, d[2:]) if d.startswith("~/") else d
        found = shutil.which("uvx", path=d)
        if found:
            return found
    return None


def uv_install_hint() -> str:
    """ONE line: how to get uv, per OS — what a plugin user without uv needs, and nothing else."""
    per_os = " · ".join(f"{os_name}: `{cmd}`" for os_name, cmd in UV_INSTALL_LINES)
    return f"install uv — {per_os} — then restart Claude Code"


def first_session_line(*, env: Optional[Mapping[str, str]] = None, version: str = __version__,
                       uvx: Optional[str] = "<probe>") -> Optional[str]:
    """I-04 on the plugin route: the one line the FIRST plugin session needs, else None.

      * no `uv` → the MCP server cannot start at all: say exactly how to get uv, one line per OS;
      * uv present but this version's pinned environment never started → disclose the first-run
        cost, so a slow first start reads as expected rather than broken;
      * the pinned environment has started before → nothing (silence is the healthy state)."""
    data = plugin_data_dir(env)
    if uvx == "<probe>":
        uvx = find_uvx(env)
    if not uvx:
        return (f"⚠ mokata (plugin): `uv` was not found, so mokata's MCP server cannot start — "
                f"{uv_install_hint()}. Hooks still run on any Python 3.")
    if data and read_runtime(data, version) is None:
        return (f"mokata (plugin): first start — uv is fetching {pin_spec(version)}: "
                f"{FIRST_RUN_COST}, once. If `/mcp` shows mokata failed meanwhile, reconnect it "
                f"there when the download finishes. Check the route any time with /mokata:doctor.")
    return None


# ------------------------------------------------------------------------------------------------
# doctor --plugin
# ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteCheck:
    ok: bool
    label: str
    detail: str
    fix: Optional[str] = None


@dataclass
class RouteReport:
    checks: List[RouteCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def lines(self, *, ascii_only: bool = False) -> List[str]:
        good, bad = ("OK", "!!") if ascii_only else ("✓", "✗")
        out = ["mokata plugin route"]
        for c in self.checks:
            out.append(f"  {good if c.ok else bad} {c.label}: {c.detail}")
            if not c.ok and c.fix:
                out.append(f"      fix: {c.fix}")
        out.append("  -> healthy" if self.ok else f"  -> NOT healthy: fix the first {bad} above")
        return out


def _plugin_manifest(plugin_root: str) -> Dict[str, object]:
    data = _read_json(os.path.join(plugin_root, ".claude-plugin", "plugin.json"))
    return data or {}


def _manifest_pin(manifest: Mapping[str, object]) -> Optional[str]:
    try:
        args = manifest["mcpServers"]["mokata"]["args"]  # type: ignore[index]
    except (KeyError, TypeError):
        return None
    for a in args or ():
        if isinstance(a, str) and a.startswith("mokata") and "==" in a:
            return a
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes  # no subprocess, no console: OpenProcess on a dead pid returns NULL
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ago(ts: object, now: float) -> str:
    try:
        secs = max(0, int(now - float(ts)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "at an unknown time"
    if secs < 120:
        return f"{secs}s ago"
    if secs < 7200:
        return f"{secs // 60} min ago"
    return f"{secs // 3600} h ago"


def plugin_route_report(plugin_root: Optional[str], *, env: Optional[Mapping[str, str]] = None,
                        data_dir: Optional[str] = None, version: str = __version__,
                        uvx: Optional[str] = "<probe>", now: Optional[float] = None) -> RouteReport:
    """Five questions, each answered from evidence, never assumed:

      1. uv found — the MCP server's launcher (`uvx`) resolves;
      2. package resolved — this process IS the pinned environment, or its record names one;
      3. version match — plugin.json version == its MCP pin == the running package;
      4. MCP server running — it recorded itself at startup and that process is alive;
      5. hooks firing — the plugin's SessionStart stamped the data dir this session."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    report = RouteReport()
    if uvx == "<probe>":
        uvx = find_uvx(env)
    report.checks.append(RouteCheck(
        bool(uvx), "uv found", uvx or "no `uvx` on PATH or in the usual install dirs",
        fix=None if uvx else uv_install_hint()))

    manifest = _plugin_manifest(plugin_root) if plugin_root else {}
    plugin_version = manifest.get("version") if isinstance(manifest.get("version"), str) else None
    pin = _manifest_pin(manifest)
    data = data_dir or plugin_data_dir(env)
    if data is None and plugin_root:
        data = _guess_data_dir(env)

    want = plugin_version or version
    recorded = read_runtime(data, want) if data else None
    resolved = recorded or None
    report.checks.append(RouteCheck(
        bool(resolved), "package resolved",
        (f"{pin or pin_spec(want)} → {resolved}" if resolved else
         f"no pinned environment recorded for {want} yet (the MCP server records it on its first "
         f"start)"),
        fix=None if resolved else "start a Claude Code session and wait for the first-run "
                                  "download; `/mcp` shows mokata connected when it is done"))

    expected_pin = pin_spec(plugin_version) if plugin_version else None
    match = bool(plugin_version) and pin == expected_pin and plugin_version == version
    detail = (f"plugin {plugin_version or '?'} · MCP pin {pin or '?'} · package {version}")
    report.checks.append(RouteCheck(
        match, "version match", detail,
        fix=None if match else (
            "the plugin checkout is inconsistent — `/plugin update mokata@mostack`, then restart"
            if plugin_version and pin != expected_pin else
            f"this launcher ran mokata {version}, the plugin is {plugin_version}: "
            f"`/plugin update mokata@mostack` and restart Claude Code")))

    status = _read_json(os.path.join(data, MCP_STATUS_FILE)) if data else None
    alive = bool(status) and _pid_alive(int(status.get("pid") or 0))  # type: ignore[union-attr]
    if status and status.get("version") != want:
        alive = False
    report.checks.append(RouteCheck(
        alive, "MCP server running",
        (f"mokata {status.get('version')} pid {status.get('pid')}, started "
         f"{_ago(status.get('started_at'), now)}" if status else "never started on this machine")
        + ("" if alive or not status else " — not running now"),
        fix=None if alive else "open `/mcp` in Claude Code and reconnect mokata; if it fails, "
                               "run `uvx --python \"" + UV_PYTHON + "\" --from \"" + pin_spec(want) + "\" mokata-mcp --version` in "
                               "a terminal to see uv's error"))

    seen = _read_json(os.path.join(data, HOOK_STATUS_FILE)) if data else None
    fired = bool(seen) and seen.get("version") == want  # type: ignore[union-attr]
    report.checks.append(RouteCheck(
        fired, "hooks firing",
        (f"SessionStart ran {_ago(seen.get('at'), now)} on {seen.get('python')}" if seen else
         "no plugin hook has run yet")
        + ("" if fired or not seen else f" (that was mokata {seen.get('version')})"),
        fix=None if fired else "start a new Claude Code session; if this check still fails, the "
                               "hooks found neither a Python 3 nor uv — " + uv_install_hint()))
    return report


def _guess_data_dir(env: Mapping[str, str]) -> Optional[str]:
    """Outside a hook/MCP process (a Bash-tool run of the launcher) `CLAUDE_PLUGIN_DATA` is not
    exported, so find Claude Code's per-plugin data dir: `~/.claude/plugins/data/<id>/` with every
    character of `mokata@<marketplace>` other than [A-Za-z0-9_-] replaced by `-`."""
    base = os.path.join(env.get("CLAUDE_CONFIG_DIR") or os.path.join(
        env.get("USERPROFILE" if os.name == "nt" else "HOME") or os.path.expanduser("~"),
        ".claude"), "plugins", "data")
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("mokata-"))
    except OSError:
        return None
    return os.path.join(base, names[0]) if names else None


__all__ = [
    "UV_PYTHON",
    "FIRST_RUN_COST", "PIN_EXTRA", "RouteCheck", "RouteReport", "UV_INSTALL_LINES",
    "find_uvx", "first_session_line", "on_plugin_route", "pin_spec", "plugin_data_dir", "plugin_route_report",
    "read_runtime", "record_hook_seen", "record_runtime", "runtime_record_name", "uv_install_hint",
]
