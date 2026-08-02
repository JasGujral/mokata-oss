"""HOOK-RESOLVE — is mokata's ENFORCEMENT PLANE actually launchable?  (read-only)

`mokata doctor` could report a clean bill of health while secret-guard and gate-guard were
DEAD: Claude Code silently drops a hook whose command does not resolve, so a wired-but-
unresolvable `mokata-hook` produced no error, no warning, and no gate — a passing doctor and
zero enforcement look identical from the outside. This module is the missing legibility: it
reads every surface a mokata hook can be wired on, extracts the program each wired command
would actually launch, and says whether that program resolves.

Surfaces read (all of them, because a user is on exactly one of these routes):
  * `.claude/settings.json` — PROJECT scope, then USER scope (what `mokata setup claude`
    writes, detected with `harness_setup._is_mokata_hook` so only mokata's own entries are
    inspected and a user's own hooks are never touched or judged);
  * the PLUGIN manifest — `<plugin-root>/.claude-plugin/plugin.json`, located via the
    `plugin_cache` root the SessionStart hook records. Its `hooks` key is followed to the
    packaged `hooks.json` (every entry there is mokata's by construction) and its
    `mcpServers` entry is read the same way.

READ-ONLY, LOCAL, and CHEAP: resolution is `mcp_admin._command_resolves` (a `shutil.which` /
path-exists probe, the same primitive `unreachable_registration` uses) — NO subprocess is
spawned, nothing is written, and nothing here can raise. A surface that cannot be read is
skipped rather than guessed at; the caller (`govern.doctor`) turns what IS found into
findings.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# The plugin-root placeholder Claude Code expands inside a plugin manifest's commands. We
# expand it the same way so the check tests the path that will REALLY be launched.
PLUGIN_ROOT_VAR = "CLAUDE_PLUGIN_ROOT"

# The one remedy, in one place: every finding that names a dead command names this.
SETUP_REMEDY = "mokata setup claude"

_SETTINGS_SURFACE = "settings.json"
_PLUGIN_SURFACE = "plugin manifest"


@dataclass
class WiredHook:
    """One hook command as it is actually wired, plus whether its program resolves."""
    surface: str          # "settings.json (project scope)" | "plugin manifest"
    where: str            # the file that carries the wiring
    event: str            # SessionStart | PreToolUse | PostToolUse | …
    command: str          # the full wired command, placeholders expanded
    program: str          # the program token that command would launch
    resolves: bool
    # HOOK-SHELL-AGNOSTIC — HOW it is launched, which decides whether the command string is
    # ever parsed by a shell at all. `args` present = EXEC form (spawned directly, no shell,
    # no quoting rule); `shell` is the harness's per-hook shell selector ("bash"|"powershell").
    args: Optional[List[str]] = None
    shell: Optional[str] = None
    # DOC-ONBOARD — the tool matcher the enclosing block declares (None when the event takes
    # none, e.g. SessionStart). Carried because a STALE matcher is the quietest kind of dead
    # gate: the hook fires, resolves, and simply never sees the tool call it was meant to stop.
    matcher: Optional[str] = None


@dataclass
class WiredServer:
    """The plugin manifest's MCP registration, same question asked of its command."""
    where: str
    name: str
    command: str
    resolves: bool


@dataclass
class HookWiringReport:
    """What mokata's enforcement plane is wired to, and whether it can launch.

    An EMPTY report is the honest answer for a repo with nothing wired (no settings.json
    hooks, no plugin) — it means "no mokata hooks are wired here", never "the wiring is
    fine". The caller reports only what is here, so an unwired repo stays silent.

    `unverifiable` is what keeps that reading honest when the check itself breaks: an empty
    report and a FAILED report would otherwise be the same object, and the reader would take
    "no findings" as a clean bill of health for gates nobody actually checked."""

    hooks: List[WiredHook] = field(default_factory=list)
    servers: List[WiredServer] = field(default_factory=list)
    unverifiable: Optional[str] = None

    @property
    def dead_hooks(self) -> List[WiredHook]:
        """The wired hooks whose command does NOT resolve — the gates that are not firing."""
        return [h for h in self.hooks if not h.resolves]

    @property
    def dead_servers(self) -> List[WiredServer]:
        return [s for s in self.servers if not s.resolves]


# --------------------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------------------
def _program(command: str) -> str:
    """The program token a wired command would launch — the first shell word, unquoted.

    `posix=False` keeps a Windows path's backslashes intact (a POSIX split would eat them),
    so the quotes are stripped afterwards. An unparseable command degrades to a plain split
    rather than raising."""
    if not command:
        return ""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if not parts:
        return ""
    token = parts[0].strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1]
    return token


def _resolves(program: str) -> bool:
    """True if `program` names something launchable — the SAME local probe
    `mcp_admin.unreachable_registration` uses (`shutil.which` for a bare name, an executable
    file for a path). No subprocess.

    HOOK-SHELL-AGNOSTIC — this used to try `.cmd`/`.bat`/`.exe` completions on Windows, on the
    stated premise that cmd.exe completes the extension-less shim path. That premise is FALSE:
    cmd.exe is never a hook shell (see `shell_fragile_hooks` for the real matrix), so the
    completions described a mechanism that never runs — and, worse, they made doctor find
    `mokata-hook-launch.cmd` and certify the hook healthy on the ONE platform where it is dead.
    A resolvability probe now asks only the question it can honestly answer: does THIS path
    name something launchable. Whether the shell that will run it can launch it is a separate
    question, asked separately by `shell_fragile_hooks`."""
    if not program:
        return False
    try:
        from .mcp_admin import _command_resolves
        return bool(_command_resolves(program))
    except (ImportError, OSError, ValueError):
        # The whole of what a which / path-exists / access probe can raise (plus the lazy
        # import). NARROW on purpose: "could not probe" must not be quietly indistinguishable
        # from "resolves fine", and a real bug here has to surface rather than hide inside a
        # security check. False here means the caller reports the hook as NOT firing — the
        # loud direction, which is the correct way for this check to be wrong.
        return False


def _expand_plugin_root(command: str, plugin_root: Optional[str]) -> str:
    """Expand `${CLAUDE_PLUGIN_ROOT}` exactly as Claude Code does before launching, so the
    resolvability question is asked of the REAL path. Left as-is when the root is unknown."""
    if not plugin_root:
        return command
    return (command
            .replace("${%s}" % PLUGIN_ROOT_VAR, plugin_root)
            .replace("$" + PLUGIN_ROOT_VAR, plugin_root))


# --------------------------------------------------------------------------------------
# HOOK-SHELL-AGNOSTIC — which shell will run a wired hook, and can it?
# --------------------------------------------------------------------------------------
# READ FROM THE SHIPPED HARNESS (Claude Code 2.1.220), not assumed. A hook `command` is
# spawned by exactly one of three routes:
#
#   args present            -> spawn(command, args)                    NO shell at all
#   shell === "powershell"  -> spawn(pwsh, [...flags, "-Command", cmd])
#   otherwise               -> spawn(cmd, [], {shell: gitBash | true})
#
# and the default `shell` is bash — Git Bash on Windows, or PowerShell when Git Bash is not
# installed. cmd.exe is NEVER a hook shell, which is why the extension-completion premise this
# module used to encode was wrong.
def powershell_executes(command: str) -> bool:
    """Would `pwsh -Command <command>` actually EXECUTE this, or just parse it as a string?

    THE RULE that falsified the old premise. PowerShell chooses COMMAND mode or EXPRESSION mode
    from the first character of a statement. A leading quote opens EXPRESSION mode, so

        "C:\\...\\mokata-hook.exe" secret-guard

    is a string literal followed by a bare word — a parse error, and nothing runs. The harness
    passes the command string to `-Command` RAW (its PowerShell pre-pass rewrites `${VAR}` to
    `${env:VAR}` and nothing else), so it does NOT insert the `&` call operator that would make
    a quoted path execute. A bare (unquoted) program name opens COMMAND mode and runs normally.

    READ FROM THE IMPLEMENTATION, not observed — there is no PowerShell on a POSIX dev box. If
    the harness ever begins prepending `&`, this is the first thing to re-check."""
    text = (command or "").strip()
    if not text:
        return False
    if text[0] in ("&", "."):        # the call operator / dot-source: explicit COMMAND mode
        return True
    return text[0] not in ("'", '"')


def shell_agnostic(hook: Any) -> bool:
    """True if this hook is spawned with NO shell — i.e. EXEC form (`args` present).

    Exec form is the only wiring no shell ever parses, so no quoting convention and no
    executable-extension search can reach it. Accepts either a WiredHook or a raw dict."""
    args = hook.get("args") if isinstance(hook, dict) else getattr(hook, "args", None)
    return args is not None


def _git_bash_present() -> bool:
    """Best-effort: is Git Bash installed (the shell the harness uses for `bash` on Windows)?"""
    import shutil
    if shutil.which("bash"):
        return True
    return any(Path(p).is_file() for p in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe"))


def shell_fragile_hooks(hooks: Any, *, windows: bool, git_bash: bool) -> List[Any]:
    """The wired hooks that the shell on THIS machine cannot actually run.

    Deliberately narrow, so the finding never cries wolf:
      * EXEC form           -> never fragile. No shell is involved on any platform.
      * not Windows         -> never fragile. `sh -c` runs every shape mokata wires.
      * Windows + Git Bash  -> fine: Git Bash runs the sh shim and any quoted absolute path.
      * Windows, no Git Bash, `shell: "bash"`  -> the harness THROWS a named error. Loud, but
        the gate is still OFF, and a user must be told the gates are not firing.
      * Windows, no Git Bash, default shell    -> falls through to PowerShell, where a quoted
        path with an argument is a parse error. This is the SILENT case and the reason the
        stage exists."""
    out: List[Any] = []
    if not windows:
        return out
    for h in hooks:
        if shell_agnostic(h):
            continue
        if git_bash:
            continue
        shell = h.get("shell") if isinstance(h, dict) else getattr(h, "shell", None)
        if shell == "powershell":
            command = h.get("command") if isinstance(h, dict) else getattr(h, "command", "")
            if powershell_executes(command):
                continue
        out.append(h)
    return out


def shell_findings(hooks: Any, *, windows: bool, git_bash: bool) -> List[tuple]:
    """[(level, code, detail)] for every hook the local shell cannot run.

    HARD ("error") like `hooks-not-firing`, and for the same reason: a gate that does not run
    is the one failure a guardrail may never keep quiet about (P14/P16). Grouped per (surface,
    shell) so one broken wiring is one finding naming its events, not four ways of saying it.
    Names the SAME single remedy HOOK-RESOLVE established, so the two stages tell one story."""
    grouped: Dict = {}
    for h in shell_fragile_hooks(hooks, windows=windows, git_bash=git_bash):
        surface = h.surface if not isinstance(h, dict) else h.get("surface", "")
        where = h.where if not isinstance(h, dict) else h.get("where", "")
        shell = h.shell if not isinstance(h, dict) else h.get("shell")
        grouped.setdefault((surface, where, shell), []).append(h)

    out: List[tuple] = []
    for (surface, where, shell), group in grouped.items():
        events = "/".join(sorted({str(h.event) for h in group}))
        if shell == "bash":
            why = ("this hook declares `shell: \"bash\"` and Git Bash is NOT installed, so "
                   "Claude Code refuses to launch it")
            fix = ("Install Git for Windows (https://git-scm.com/downloads/win), or run "
                   f"`{SETUP_REMEDY}` to rewire the hooks to the shell-free exec form")
        else:
            why = ("this hook names no shell, so a machine without Git Bash runs it under "
                   "PowerShell — where a quoted path followed by an argument is parsed as a "
                   "string, not a command, and the gate never runs")
            fix = (f"Run `{SETUP_REMEDY}` to rewire the hooks to the shell-free exec form "
                   "(`command` + `args`), which no shell parses")
        out.append((
            "error", "hooks-shell-unrunnable",
            f"{surface}: the wired {events} hook cannot be launched by the shell on this "
            f"machine — mokata's gates are NOT firing (secret-guard + gate-guard never run). "
            f"Why: {why}. Wired in {where}. Fix: {fix}"))
    return out


def _read_json(path: Any) -> Optional[Dict]:
    try:
        with open(str(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _hooks_from_config(data: Dict, *, surface: str, where: str,
                       plugin_root: Optional[str] = None,
                       mokata_only: bool = True) -> List[WiredHook]:
    """Walk a Claude Code hooks config (settings.json or a plugin hooks.json) into WiredHooks.

    `mokata_only` routes settings.json through `harness_setup._is_mokata_hook`, so a user's own
    hooks are never inspected or judged; a plugin's packaged hooks.json is mokata's by
    construction and needs no filter."""
    out: List[WiredHook] = []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return out
    is_mokata = None
    if mokata_only:
        try:
            from .harness_setup import _is_mokata_hook
            is_mokata = _is_mokata_hook
        except ImportError:
            return out
    for event, blocks in hooks.items():
        if not isinstance(blocks, list):
            continue
        for entry in blocks:
            if not isinstance(entry, dict):
                continue
            if is_mokata is not None and not is_mokata(entry):
                continue
            for h in entry.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                command = _expand_plugin_root(str(h.get("command", "")), plugin_root)
                if not command:
                    continue
                # EXEC form (`args` present) names the executable directly, so the command IS
                # the program — splitting it as a shell word would mangle a path with spaces
                # that no shell will ever see.
                raw_args = h.get("args")
                args = ([str(a) for a in raw_args] if isinstance(raw_args, list) else None)
                program = command if args is not None else _program(command)
                shell = h.get("shell")
                matcher = entry.get("matcher")
                out.append(WiredHook(surface=surface, where=where, event=str(event),
                                     command=command, program=program,
                                     resolves=_resolves(program),
                                     args=args,
                                     shell=str(shell) if shell else None,
                                     matcher=str(matcher) if matcher else None))
    return out


# --------------------------------------------------------------------------------------
# surfaces
# --------------------------------------------------------------------------------------
def _settings_hooks(root: str, home: Optional[str]) -> List[WiredHook]:
    """The hooks `mokata setup claude` wired, project scope then user scope."""
    out: List[WiredHook] = []
    try:
        from .harness_setup import resolve_targets
    except ImportError:
        return out
    for scope in ("project", "user"):
        try:
            targets = resolve_targets(scope, root, home, "claude")
        except (ValueError, OSError):
            continue
        path = getattr(targets, "settings_path", None)
        if path is None or not Path(str(path)).is_file():
            continue
        data = _read_json(path)
        if data is None:
            continue
        out.extend(_hooks_from_config(
            data, surface=f"{_SETTINGS_SURFACE} ({scope} scope)", where=str(path)))
    return out


def _plugin_surfaces(home: Optional[str]) -> tuple:
    """The PLUGIN route: the manifest's packaged hooks.json + its MCP registration.

    Located via the plugin root the SessionStart hook records (`plugin_cache`), so this only
    fires for a user who actually has the plugin installed. Returns ([], []) otherwise."""
    hooks: List[WiredHook] = []
    servers: List[WiredServer] = []
    try:
        from .plugin_cache import read_plugin_root
        plugin_root = read_plugin_root(home=home)
    except (ImportError, OSError):
        return hooks, servers
    if not plugin_root:
        return hooks, servers
    manifest_path = Path(plugin_root) / ".claude-plugin" / "plugin.json"
    manifest = _read_json(manifest_path)
    if manifest is None:
        return hooks, servers

    hooks_rel = manifest.get("hooks")
    if isinstance(hooks_rel, str) and hooks_rel:
        hooks_path = Path(plugin_root) / hooks_rel.lstrip("./")
        data = _read_json(hooks_path)
        if data is not None:
            hooks.extend(_hooks_from_config(
                data, surface=_PLUGIN_SURFACE, where=str(hooks_path),
                plugin_root=str(plugin_root), mokata_only=False))

    declared = manifest.get("mcpServers")
    if isinstance(declared, dict):
        for name, spec in declared.items():
            if not isinstance(spec, dict):
                continue
            command = _expand_plugin_root(str(spec.get("command", "")), str(plugin_root))
            if not command:
                continue
            servers.append(WiredServer(where=str(manifest_path), name=str(name),
                                       command=command, resolves=_resolves(_program(command))))
    return hooks, servers


def hook_wiring_report(root: str = ".", home: Optional[str] = None) -> HookWiringReport:
    """Build the wiring report for `root` — every surface a mokata hook can be wired on, and
    whether each wired command resolves. NEVER raises and NEVER writes; a surface that cannot
    be read is skipped, so the report says only what it actually saw."""
    try:
        hooks = _settings_hooks(root, home)
        plugin_hooks, servers = _plugin_surfaces(home)
        hooks.extend(plugin_hooks)
        return HookWiringReport(hooks=hooks, servers=servers)
    except Exception as exc:
        # D5 — the floor, and it is DELIBERATELY broad: this runs inside `mokata doctor`, the
        # command you reach for when things are already wrong, so a weird settings.json or an
        # exotic home must not crash the diagnosis. But it does NOT return a bare empty report:
        # empty means "nothing is wired", and handing that back for "the check blew up" would
        # tell the reader their gates are fine when nobody looked. The failure is carried out
        # as `unverifiable`, which `govern.doctor` reports as a LOUD `hooks-unverifiable` error.
        return HookWiringReport(unverifiable=f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------------------
# DOC-ONBOARD — IS THE WIRING STALE?  One function. Three surfaces.
# --------------------------------------------------------------------------------------
# The failure this exists for is the one `mokata upgrade` used to leave behind: pip installs the
# new code, and `.claude/settings.json` still carries the wiring the OLD mokata wrote. Nothing
# errors. The hooks that ARE wired still fire. The ones added since — and the matchers widened
# since — simply are not there, so a gate the release notes say you have never sees the tool call
# it was meant to stop. Wired has never implied CURRENT any more than it implied firing.
#
# Why one function and three renderings: the surface that can report this is decided by WHAT is
# broken. A dead bare name means mokata's own hooks never run, so the briefing cannot speak and
# only the MCP surface (or the CLI) can. A merely-stale wiring means the hooks DO run, so the
# briefing is the cheapest place to say so. `mokata doctor --wiring` covers the case where the
# user came looking. Three surfaces answering the same question from three copies of the rule is
# how they end up disagreeing — so they answer from here.
#
# The scope is DELIBERATELY settings.json only. `mokata setup claude` is the remedy this verdict
# names, and it is the remedy for exactly this surface: the plugin route's packaged hooks.json is
# refreshed by a plugin update, not by setup, so calling it stale here would hand a plugin user a
# fix that does nothing for them.

# What a wired hook can be wrong ABOUT. Ordered by how loudly it fails.
DRIFT_DEAD = "dead"                 # the command does not resolve — the gate never runs at all
DRIFT_SHELL_FORM = "shell-form"     # a command string, not exec form (pre-HOOK-SHELL-AGNOSTIC)
DRIFT_MISSING = "missing"           # an event/subcommand this mokata wires is not wired here
DRIFT_MATCHER = "matcher"           # wired, resolves, fires — against the wrong tool set


@dataclass
class WiringDrift:
    """The verdict: is the harness wiring on disk the wiring THIS mokata writes?

    `checked=False` is NOT a pass. It is "there was nothing here to judge" — no mokata hooks in
    any settings.json (an unwired repo, or `setup --no-hooks`), or the wiring report itself came
    back unverifiable. Both must stay SILENT: nagging a user who deliberately wired nothing is
    how a warning gets tuned out before the day it matters. The unverifiable case is already a
    LOUD `hooks-unverifiable` error from doctor, and saying it twice in two voices helps nobody."""

    items: List[tuple] = field(default_factory=list)   # [(code, reason)] — ordered, deduped
    where: str = ""                                    # the settings.json carrying it
    surface: str = ""                                  # "settings.json (project scope)" | …
    checked: bool = True

    @property
    def drifted(self) -> bool:
        return bool(self.items)

    @property
    def codes(self) -> List[str]:
        return [code for code, _reason in self.items]

    @property
    def reasons(self) -> List[str]:
        return [reason for _code, reason in self.items]

    @property
    def dead(self) -> bool:
        """The hooks are wired to something unlaunchable — mokata's own code never runs, so no
        hook-borne surface can report this. Whoever can still speak must."""
        return DRIFT_DEAD in self.codes


def _wired_subcommand(hook: WiredHook) -> str:
    """Which `mokata-hook` subcommand this wiring would actually invoke.

    EXEC form puts it in `args[0]`; the legacy shell form puts it after the program in the
    command string. Read rather than assumed, because the whole point is to judge wiring we
    did not write."""
    if hook.args:
        return str(hook.args[0])
    try:
        parts = shlex.split(hook.command, posix=False)
    except ValueError:
        parts = hook.command.split()
    if len(parts) < 2:
        return ""
    token = parts[1].strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        token = token[1:-1]
    return token


def _drift_items(hooks: List[WiredHook]) -> List[tuple]:
    """Judge ONE settings.json surface's mokata hooks against `expected_hook_wiring()`."""
    from .harness_setup import expected_hook_wiring

    items: List[tuple] = []
    if any(not h.resolves for h in hooks):
        dead = sorted({h.program for h in hooks if not h.resolves})
        items.append((DRIFT_DEAD,
                      f"the wired hook command does not resolve ({', '.join(dead)}) — the "
                      f"gates are NOT firing"))
    if any(h.args is None for h in hooks):
        items.append((DRIFT_SHELL_FORM,
                      "the hooks are wired as shell command strings, not the shell-free exec "
                      "form (wiring predates the cross-platform hook fix)"))

    have = {(h.event, _wired_subcommand(h)): h for h in hooks}
    missing: List[str] = []
    stale_matchers: List[str] = []
    for event, specs in expected_hook_wiring().items():
        for spec in specs:
            hook = have.get((event, spec["subcommand"]))
            if hook is None:
                missing.append(f"{event}/{spec['subcommand']}")
            elif hook.matcher != spec["matcher"]:
                stale_matchers.append(
                    f"{event}/{spec['subcommand']} (wired {hook.matcher or 'none'!r}, "
                    f"current {spec['matcher'] or 'none'!r})")
    if missing:
        items.append((DRIFT_MISSING,
                      f"this mokata wires hooks that are not here: {', '.join(missing)}"))
    if stale_matchers:
        items.append((DRIFT_MATCHER,
                      f"the wired tool matcher is out of date: {'; '.join(stale_matchers)}"))
    return items


def wiring_drift(root: str = ".", home: Optional[str] = None) -> WiringDrift:
    """THE single source of "is the harness wiring stale". READ-ONLY and never raises.

    Judges each settings.json surface (project scope, then user scope) against the shape a
    CURRENT `mokata setup claude` writes, and returns the first one that is wrong — one story,
    one remedy, rather than a pile of findings a user has to reconcile. Absolute executable
    paths are NOT compared: two healthy installs legitimately resolve `mokata-hook` to different
    paths, and a check that fires on that would nag every venv and virtualenv on the machine."""
    try:
        report = hook_wiring_report(root, home)
        if report.unverifiable:
            return WiringDrift(checked=False)
        surfaces: Dict[tuple, List[WiredHook]] = {}
        for hook in report.hooks:
            if not hook.surface.startswith(_SETTINGS_SURFACE):
                continue                      # plugin route — `setup` is not its remedy
            surfaces.setdefault((hook.surface, hook.where), []).append(hook)
        if not surfaces:
            return WiringDrift(checked=False)
        for (surface, where), hooks in surfaces.items():
            items = _drift_items(hooks)
            if items:
                return WiringDrift(items=items, where=where, surface=surface)
        return WiringDrift()
    except Exception:
        # D5 — DELIBERATELY broad, and it returns `checked=False` rather than a clean verdict.
        # This runs on the SessionStart path and inside every MCP `status` call: a weird
        # settings.json must not break a session, and "the check fell over" must never render
        # as "your wiring is current". The loud report for an unreadable wiring surface is
        # doctor's `hooks-unverifiable`, which owns that story.
        return WiringDrift(checked=False)


def wiring_drift_line(drift: WiringDrift, *, ascii_only: bool = False) -> Optional[str]:
    """The ONE-LINE rendering every surface prints, or None when there is nothing to say.

    One renderer for the same reason there is one verdict: the briefing, the MCP status note
    and the doctor detail must not describe the same broken wiring three different ways."""
    if not drift.drifted:
        return None
    glyph = "[!]" if ascii_only else "⚠"
    head = ("mokata's hooks are wired but NOT LAUNCHABLE — the gates are not firing"
            if drift.dead else
            f"mokata's harness wiring is STALE (older than mokata {_version()})")
    return (f"{glyph} {head}: {'; '.join(drift.reasons)}. "
            f"Fix: run `{SETUP_REMEDY}` (then restart Claude Code).")


def _version() -> str:
    from . import __version__
    return __version__
