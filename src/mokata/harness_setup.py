"""`mokata setup <harness>` — one-command wiring for using mokata WITHOUT the plugin.

The Claude Code plugin is just a convenient bundle of three portable artifacts mokata
already ships: the prompt templates (``templates/commands/*.md``), the bundled MCP server
(``mokata-mcp``), and the hook scripts (``hooks/``). This module wires those three pieces
into a harness directly, so a user gets the full plugin experience without installing from a
marketplace. The harness still supplies the LLM ("brain"); mokata supplies the structure.

Currently the only harness is ``claude`` (Claude Code). The design leaves room for others
(Gemini CLI, Codex) — each is a different mapping of the same three artifacts.

Everything here is **human-gated** (P2): ``setup`` shows exactly what it will write/merge,
then waits for confirmation (``--yes`` is the non-interactive escape hatch). Writes are
**idempotent** (re-running converges) and **reversible** (``unsetup`` removes what was
wired). JSON files are *merged*, never clobbered — an existing ``.mcp.json`` or
``settings.json`` keeps its other entries.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .init import init_repo
from . import MOKATA_DIR, MANIFEST_FILENAME

HARNESSES = ("claude",)
SCOPES = ("project", "user")

# The MCP server key + command (mirrors .claude-plugin/plugin.json's mcpServers entry).
MCP_SERVER_NAME = "mokata"
MCP_COMMAND = "mokata-mcp"

# PreToolUse matcher (mirrors hooks/hooks.json).
HOOK_PRETOOL_MATCHER = "Write|Edit|MultiEdit|Bash"


def repo_root() -> Path:
    """The mokata checkout / plugin root that holds templates/ and hooks/.

    For the documented no-plugin install (``pip install -e .`` from a clone), the package
    imports from ``<clone>/src/mokata``, so the artifacts live two parents up.
    """
    return Path(__file__).resolve().parents[2]


def _templates_dir() -> Path:
    return repo_root() / "templates" / "commands"


def _hooks_dir() -> Path:
    return repo_root() / "hooks"


class SetupError(Exception):
    """Raised when the artifacts mokata needs to wire can't be found."""


# --------------------------------------------------------------------------------------
# Scope → target paths
# --------------------------------------------------------------------------------------

@dataclass
class Targets:
    """Resolved filesystem targets for a (scope) choice."""
    base: Path                 # the directory .claude/ lives under
    commands_dir: Path         # <base>/.claude/commands
    settings_path: Path        # <base>/.claude/settings.json
    mcp_path: Path             # project: <root>/.mcp.json ; user: ~/.claude.json


def resolve_targets(scope: str, root: str, home: Optional[str] = None) -> Targets:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope '{scope}'; choose one of {SCOPES}")
    if scope == "project":
        base = Path(root).resolve()
        mcp_path = base / ".mcp.json"
    else:  # user
        base = Path(home).resolve() if home else Path.home()
        # Claude Code's user-scoped MCP config lives in ~/.claude.json (where
        # `claude mcp add --scope user` writes); project scope uses .mcp.json.
        mcp_path = base / ".claude.json"
    return Targets(
        base=base,
        commands_dir=base / ".claude" / "commands",
        settings_path=base / ".claude" / "settings.json",
        mcp_path=mcp_path,
    )


# --------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------

@dataclass
class SetupPlan:
    harness: str
    scope: str
    profile: str
    root: str
    with_hooks: bool
    targets: Targets
    command_files: List[str]          # basenames to copy
    needs_init: bool                  # .mokata/manifest.json absent
    hook_commands: Dict[str, str] = field(default_factory=dict)  # event -> command string


def _hook_command(script: str) -> str:
    # Absolute path to the checkout's hook script. Manual setup has no
    # ${CLAUDE_PLUGIN_ROOT}, so we point straight at the clone.
    return f'python3 "{_hooks_dir() / script}"'


def plan_setup(
    harness: str,
    root: str = ".",
    scope: str = "project",
    profile: str = "standard",
    with_hooks: bool = True,
    home: Optional[str] = None,
) -> SetupPlan:
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness '{harness}'; choose one of {HARNESSES}")

    tdir = _templates_dir()
    if not tdir.is_dir():
        raise SetupError(
            f"command templates not found at {tdir}. Install mokata from a clone with "
            f"`pip install -e .` (the no-plugin path), or use the Claude Code plugin."
        )
    command_files = sorted(p.name for p in tdir.glob("*.md"))
    if not command_files:
        raise SetupError(f"no command templates (*.md) found in {tdir}.")

    if with_hooks and not _hooks_dir().is_dir():
        raise SetupError(f"hook scripts not found at {_hooks_dir()}.")

    targets = resolve_targets(scope, root, home)
    manifest_path = Path(root).resolve() / MOKATA_DIR / MANIFEST_FILENAME
    needs_init = not manifest_path.exists()

    hook_commands: Dict[str, str] = {}
    if with_hooks:
        hook_commands = {
            "SessionStart": _hook_command("session_start.py"),
            "PreToolUse": _hook_command("secret_guard.py"),
        }

    return SetupPlan(
        harness=harness,
        scope=scope,
        profile=profile,
        root=root,
        with_hooks=with_hooks,
        targets=targets,
        command_files=command_files,
        needs_init=needs_init,
        hook_commands=hook_commands,
    )


def render_setup_plan(plan: SetupPlan) -> str:
    t = plan.targets
    lines: List[str] = []
    lines.append(f"mokata setup {plan.harness} — scope '{plan.scope}'")
    lines.append("")
    if plan.needs_init:
        lines.append(f"Will initialize mokata (profile '{plan.profile}') in {plan.root}:")
        lines.append(f"  {Path(plan.root).resolve() / MOKATA_DIR}/")
    else:
        lines.append(f"mokata already initialized in {plan.root} "
                     f"(leaving the existing profile/config untouched).")
    lines.append("")
    lines.append(f"Will copy {len(plan.command_files)} slash commands -> {t.commands_dir}:")
    lines.append("  /" + "  /".join(Path(f).stem for f in plan.command_files))
    lines.append("")
    lines.append(f"Will register the MCP server '{MCP_SERVER_NAME}' (command: "
                 f"{MCP_COMMAND}) in:")
    lines.append(f"  {t.mcp_path}")
    if plan.with_hooks:
        lines.append("")
        lines.append(f"Will wire hooks (SessionStart briefing + secret-guard) in:")
        lines.append(f"  {t.settings_path}")
    lines.append("")
    lines.append("Existing JSON files are merged, not overwritten. Reverse anytime with "
                 f"`mokata unsetup {plan.harness} --scope {plan.scope}`.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# JSON merge helpers (preserve unrelated keys)
# --------------------------------------------------------------------------------------

def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _merge_mcp(path: Path) -> None:
    data = _load_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[MCP_SERVER_NAME] = {"command": MCP_COMMAND, "args": []}
    data["mcpServers"] = servers
    _write_json(path, data)


def _is_mokata_hook(entry: Dict) -> bool:
    """True if a hook block is one mokata wired (its command points at our hooks dir)."""
    hooks_marker = str(_hooks_dir())
    for h in entry.get("hooks", []):
        if isinstance(h, dict) and hooks_marker in str(h.get("command", "")):
            return True
    return False


def _merge_hooks(path: Path, hook_commands: Dict[str, str]) -> None:
    data = _load_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    for event, command in hook_commands.items():
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
        # Idempotent: drop any prior mokata-wired entry for this event, then add fresh.
        entries = [e for e in entries if not (isinstance(e, dict) and _is_mokata_hook(e))]
        block: Dict = {"hooks": [{"type": "command", "command": command}]}
        if event == "PreToolUse":
            block["matcher"] = HOOK_PRETOOL_MATCHER
        entries.append(block)
        hooks[event] = entries

    data["hooks"] = hooks
    _write_json(path, data)


# --------------------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------------------

def apply_setup(plan: SetupPlan, *, assume_yes: bool = False, force: bool = False,
                out: Optional[Callable[[str], None]] = None) -> List[str]:
    """Write everything the plan describes. Returns the touched paths."""
    emit = out or print
    touched: List[str] = []

    # 1. init (reuse the human-gated init; we've already gated the whole setup).
    if plan.needs_init:
        res = init_repo(root=plan.root, profile=plan.profile, assume_yes=True,
                        force=force, out=emit)
        if res.aborted:
            raise SetupError(f"init failed: {res.message}")
        touched.extend(res.written)

    # 2. slash commands
    t = plan.targets
    t.commands_dir.mkdir(parents=True, exist_ok=True)
    for name in plan.command_files:
        dst = t.commands_dir / name
        shutil.copyfile(_templates_dir() / name, dst)
        touched.append(str(dst))

    # 3. MCP server
    _merge_mcp(t.mcp_path)
    touched.append(str(t.mcp_path))

    # 4. hooks
    if plan.with_hooks:
        _merge_hooks(t.settings_path, plan.hook_commands)
        touched.append(str(t.settings_path))

    return touched


def _default_confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


@dataclass
class SetupResult:
    touched: List[str]
    plan: SetupPlan
    aborted: bool = False
    message: str = ""


def setup_harness(
    harness: str,
    root: str = ".",
    scope: str = "project",
    profile: str = "standard",
    with_hooks: bool = True,
    assume_yes: bool = False,
    force: bool = False,
    home: Optional[str] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
) -> SetupResult:
    """Plan + human-gate + apply the harness wiring end to end."""
    emit = out or print
    plan = plan_setup(harness, root, scope, profile, with_hooks, home)

    emit(render_setup_plan(plan))

    if not assume_yes:
        gate = confirm or _default_confirm
        if not gate("\nWire this up? [y/N] "):
            return SetupResult(touched=[], plan=plan, aborted=True,
                               message="aborted by user")

    touched = apply_setup(plan, assume_yes=assume_yes, force=force, out=emit)

    emit("")
    emit(f"mokata is wired into {harness} ({scope} scope).")
    emit("Restart Claude Code, then try /brainstorm or ask it to \"run mokata doctor\".")
    return SetupResult(touched=touched, plan=plan, message="ok")


# --------------------------------------------------------------------------------------
# Unsetup (reverse)
# --------------------------------------------------------------------------------------

@dataclass
class UnsetupPlan:
    harness: str
    scope: str
    targets: Targets
    command_files: List[str]


def plan_unsetup(harness: str, root: str = ".", scope: str = "project",
                 home: Optional[str] = None) -> UnsetupPlan:
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness '{harness}'; choose one of {HARNESSES}")
    targets = resolve_targets(scope, root, home)
    tdir = _templates_dir()
    command_files = sorted(p.name for p in tdir.glob("*.md")) if tdir.is_dir() else []
    return UnsetupPlan(harness=harness, scope=scope, targets=targets,
                       command_files=command_files)


def render_unsetup_plan(plan: UnsetupPlan) -> str:
    t = plan.targets
    lines = [f"mokata unsetup {plan.harness} — scope '{plan.scope}'", ""]
    lines.append("Will remove:")
    lines.append(f"  copied slash commands in {t.commands_dir}")
    lines.append(f"  the '{MCP_SERVER_NAME}' MCP server entry in {t.mcp_path}")
    lines.append(f"  mokata hook entries in {t.settings_path}")
    lines.append("")
    lines.append("Your .mokata/ config is left untouched (use `mokata reset` for that).")
    return "\n".join(lines)


def apply_unsetup(plan: UnsetupPlan) -> List[str]:
    t = plan.targets
    removed: List[str] = []

    # 1. commands
    for name in plan.command_files:
        p = t.commands_dir / name
        if p.exists():
            p.unlink()
            removed.append(str(p))

    # 2. MCP server entry
    if t.mcp_path.exists():
        data = _load_json(t.mcp_path)
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and MCP_SERVER_NAME in servers:
            del servers[MCP_SERVER_NAME]
            if servers:
                data["mcpServers"] = servers
            else:
                data.pop("mcpServers", None)
            _write_json(t.mcp_path, data)
            removed.append(str(t.mcp_path))

    # 3. hook entries
    if t.settings_path.exists():
        data = _load_json(t.settings_path)
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            changed = False
            for event in list(hooks):
                entries = hooks.get(event)
                if not isinstance(entries, list):
                    continue
                kept = [e for e in entries
                        if not (isinstance(e, dict) and _is_mokata_hook(e))]
                if len(kept) != len(entries):
                    changed = True
                    if kept:
                        hooks[event] = kept
                    else:
                        del hooks[event]
            if changed:
                if hooks:
                    data["hooks"] = hooks
                else:
                    data.pop("hooks", None)
                _write_json(t.settings_path, data)
                removed.append(str(t.settings_path))

    return removed


@dataclass
class UnsetupResult:
    removed: List[str]
    plan: UnsetupPlan
    aborted: bool = False
    message: str = ""


def unsetup_harness(
    harness: str,
    root: str = ".",
    scope: str = "project",
    assume_yes: bool = False,
    home: Optional[str] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
) -> UnsetupResult:
    emit = out or print
    plan = plan_unsetup(harness, root, scope, home)
    emit(render_unsetup_plan(plan))
    if not assume_yes:
        gate = confirm or _default_confirm
        if not gate("\nRemove this wiring? [y/N] "):
            return UnsetupResult(removed=[], plan=plan, aborted=True,
                                 message="aborted by user")
    removed = apply_unsetup(plan)
    emit("")
    emit(f"removed mokata wiring for {harness} ({scope} scope).")
    return UnsetupResult(removed=removed, plan=plan, message="ok")
