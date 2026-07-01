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

from .prompt import read_yes_no

import json
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .init import init_repo
from . import MOKATA_DIR, MANIFEST_FILENAME

# Stage 54b — where the original statusLine is stashed when mokata composes over a user's
# own (so `unsetup` can restore it verbatim). Claude Code ignores unknown statusLine keys.
_WRAPPED_KEY = "_mokataWrapped"

# Setup targets (Stage 52: claude/codex; Stage 63: cursor/copilot/windsurf/gemini/aider).
# `cowork` is registry/matrix-only (a plugin host, not a `mokata setup` target).
HARNESSES = ("claude", "codex", "cursor", "copilot", "windsurf", "gemini", "aider")
SCOPES = ("project", "user")

# Per-harness wiring map (Stage 52a, extended Stage 63). Each harness is a different mapping
# of the same portable artifacts; the capabilities a harness lacks are NOT wired (degrade
# clearly — never pretend).
#   dir             — the harness config dir under the scope base
#   commands_subdir — where prompt/slash/workflow commands live (the native surface)
#   command_format  — how a `<name>.md` template is materialized natively:
#                       "md"        → copy as-is (claude/codex/cursor/windsurf)
#                       "prompt.md" → `<name>.prompt.md` (copilot prompt files)
#                       "toml"      → `<name>.toml` with description + prompt (gemini)
#                       "reference" → copied as REFERENCE prompts (aider has no slash cmds)
#   mcp_auto        — auto-register the bundled MCP server where the agent's MCP config schema
#                     matches mokata's `mcpServers` merge (claude/cursor/gemini); else a
#                     documented MANUAL step (codex TOML, copilot VS Code schema, windsurf path)
_HARNESS_DIR = {"claude": ".claude", "codex": ".codex", "cursor": ".cursor",
                "copilot": ".github", "windsurf": ".windsurf", "gemini": ".gemini",
                "aider": ".aider"}
_HARNESS_COMMANDS_SUBDIR = {"claude": "commands", "codex": "prompts", "cursor": "commands",
                            "copilot": "prompts", "windsurf": "workflows",
                            "gemini": "commands", "aider": "mokata-commands"}
_HARNESS_COMMAND_FORMAT = {"claude": "md", "codex": "md", "cursor": "md",
                           "copilot": "prompt.md", "windsurf": "md", "gemini": "toml",
                           "aider": "reference"}
_HARNESS_MCP_AUTO = {"claude": True, "codex": False, "cursor": True, "copilot": False,
                     "windsurf": False, "gemini": True, "aider": False}

# A one-line description of each agent's NATIVE command surface (for the setup plan + docs).
_HARNESS_NATIVE_NOTE = {
    "claude": "Claude Code slash commands (.claude/commands/*.md)",
    "codex": "Codex prompts (.codex/prompts/*.md)",
    "cursor": "Cursor commands (.cursor/commands/*.md) + rules context; MCP via .cursor/mcp.json",
    "copilot": "Copilot prompt files (.github/prompts/*.prompt.md) + instructions context",
    "windsurf": "Windsurf workflows (.windsurf/workflows/*.md) + rules context",
    "gemini": "Gemini CLI commands (.gemini/commands/*.toml) + MCP via .gemini/settings.json",
    "aider": "Aider reference prompts (.aider/mokata-commands/) + conventions context "
             "— Aider has NO native slash-command files",
}

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
    """Resolved filesystem targets for a (scope, harness) choice."""
    base: Path                       # the directory the harness config dir lives under
    commands_dir: Path               # <base>/<hdir>/<commands subdir>
    settings_path: Optional[Path]    # <base>/.claude/settings.json (None when no hook wiring)
    mcp_path: Optional[Path]         # MCP config (None when the harness isn't auto-wired)
    skills_dir: Optional[Path] = None  # <base>/.claude/skills (Agent Skills — claude only)


def resolve_targets(scope: str, root: str, home: Optional[str] = None,
                    harness: str = "claude") -> Targets:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope '{scope}'; choose one of {SCOPES}")
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness '{harness}'; choose one of {HARNESSES}")
    base = Path(root).resolve() if scope == "project" else (
        Path(home).resolve() if home else Path.home())
    hdir = _HARNESS_DIR[harness]
    commands_dir = base / hdir / _HARNESS_COMMANDS_SUBDIR[harness]
    settings_path = base / hdir / "settings.json"
    if _HARNESS_MCP_AUTO[harness]:
        if harness == "claude":
            # Claude Code's user-scoped MCP config lives in ~/.claude.json (where
            # `claude mcp add --scope user` writes); project scope uses .mcp.json.
            mcp_path = (base / ".mcp.json") if scope == "project" else (base / ".claude.json")
        elif harness == "cursor":
            mcp_path = base / ".cursor" / "mcp.json"        # `mcpServers` schema
        elif harness == "gemini":
            mcp_path = base / ".gemini" / "settings.json"   # top-level `mcpServers` (merged)
        else:                                               # generic mcpServers fallback
            mcp_path = base / hdir / "mcp.json"
    else:
        mcp_path = None              # harness auto-MCP not supported — manual step
    # Agent Skills are a Claude Code surface (`.claude/skills/<name>/SKILL.md`); only claude
    # gets a skills target. Other harnesses leave it None (nothing written / nothing to undo).
    skills_dir = (base / hdir / "skills") if harness == "claude" else None
    return Targets(base=base, commands_dir=commands_dir,
                   settings_path=settings_path, mcp_path=mcp_path, skills_dir=skills_dir)


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
    mcp_auto: bool = True             # auto-register the MCP server (claude); else manual
    unsupported: List[str] = field(default_factory=list)  # capabilities this harness lacks
    skill_names: List[str] = field(default_factory=list)  # Agent Skills to install (claude)
    plugin_provides_skills: bool = False  # plugin detected → skills suppressed to avoid dupes


# The hook scripts (kept as standalone shims) map to `mokata-hook` subcommands.
_HOOK_SUBCOMMAND = {"session_start.py": "session-start", "secret_guard.py": "secret-guard"}


def _hook_command(script: str) -> str:
    # Stage 53b: wire hooks as the `mokata-hook` console entry point — the SAME mechanism
    # the bundled `mokata-mcp` server already uses reliably — instead of a bare `python3`
    # (which a minimal PATH or Windows wouldn't resolve) or the `sh launch.sh` chain.
    # `mokata setup` runs from the installed package, so `mokata-hook` is a guaranteed
    # sibling console script; resolve it to an absolute path for zero PATH dependency in
    # the user's later sessions, falling back to the bare name.
    exe = shutil.which("mokata-hook") or "mokata-hook"
    sub = _HOOK_SUBCOMMAND[script]
    cmd = f'"{exe}" {sub}'
    if sub == "session-start":
        # Forward the clone root so /mokata:init can locate the bundled engine (manual
        # setup has no ${CLAUDE_PLUGIN_ROOT}).
        cmd += f' --plugin-root "{repo_root()}"'
    return cmd


def _statusline_command(wrap_command: Optional[str] = None) -> str:
    # Stage 54b: the Claude Code statusLine command — the SAME PATH-resolved `mokata-hook`
    # console entry point the hooks use. With `wrap_command` set, mokata COMPOSES over a
    # user's existing statusLine (runs theirs, then appends mokata's) instead of clobbering.
    exe = shutil.which("mokata-hook") or "mokata-hook"
    cmd = f'"{exe}" statusline'
    if wrap_command:
        cmd += f" --wrap {shlex.quote(wrap_command)}"
    return cmd


def _is_mokata_statusline(block: Dict) -> bool:
    """True if a settings.json statusLine block is one mokata wired (so re-setup is
    idempotent and unsetup only touches our own)."""
    cmd = str(block.get("command", "")) if isinstance(block, dict) else ""
    return "mokata-hook" in cmd and "statusline" in cmd


def _mokata_plugin_installed(home: Optional[str] = None) -> bool:
    """True if the mokata Claude Code PLUGIN appears installed. The plugin already provides the
    Agent Skills (as ``mokata:<name>``), so the no-plugin ``mokata setup`` path must NOT also
    write a project-scope copy — otherwise Claude Code lists every mokata skill TWICE. Detected
    by a ``plugin.json`` named ``mokata`` anywhere under ``~/.claude/plugins/``. Best-effort and
    quiet: any error → False (we simply don't suppress)."""
    base = Path(home) if home is not None else Path.home()
    plugins = base / ".claude" / "plugins"
    try:
        if not plugins.is_dir():
            return False
        for pj in plugins.rglob("plugin.json"):
            try:
                if json.loads(pj.read_text(encoding="utf-8")).get("name") == "mokata":
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


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

    # Capability-aware (Stage 52a): wire only what this harness actually supports; a
    # capability it lacks is NOT wired (the engine degrades clearly when it needs it).
    from .harness import HARNESS_CAPABILITIES, get_harness
    h = get_harness(harness)
    unsupported = [c for c in HARNESS_CAPABILITIES if not h.supports(c)]
    hooks_ok = h.supports("hooks")
    with_hooks = with_hooks and hooks_ok
    mcp_auto = _HARNESS_MCP_AUTO[harness]

    if with_hooks and not _hooks_dir().is_dir():
        raise SetupError(f"hook scripts not found at {_hooks_dir()}.")

    targets = resolve_targets(scope, root, home, harness)
    manifest_path = Path(root).resolve() / MOKATA_DIR / MANIFEST_FILENAME
    needs_init = not manifest_path.exists()

    hook_commands: Dict[str, str] = {}
    if with_hooks:
        hook_commands = {
            "SessionStart": _hook_command("session_start.py"),
            "PreToolUse": _hook_command("secret_guard.py"),
        }

    # Agent Skills — the model-invocable twin of the slash commands. Only where the harness
    # has that surface (claude); degrade-clean (empty list) everywhere else. The curated set
    # renders from the SAME command templates, so it can't drift from the commands.
    from .agent_skills import CURATED_SKILLS
    skill_names = list(CURATED_SKILLS) if targets.skills_dir is not None else []
    plugin_provides_skills = False
    if skill_names and _mokata_plugin_installed(home=home):
        # The plugin already ships these skills (as `mokata:<name>`); a project-scope copy would
        # make Claude Code list each skill twice. Suppress the write and say so in the plan.
        skill_names = []
        plugin_provides_skills = True

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
        mcp_auto=mcp_auto,
        unsupported=unsupported,
        skill_names=skill_names,
        plugin_provides_skills=plugin_provides_skills,
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
    lines.append(f"Will write {len(plan.command_files)} commands -> {t.commands_dir}:")
    lines.append("  /" + "  /".join(Path(f).stem for f in plan.command_files))
    lines.append(f"  (native surface: {_HARNESS_NATIVE_NOTE[plan.harness]})")
    lines.append("")
    if plan.skill_names and t.skills_dir is not None:
        lines.append(f"Will write {len(plan.skill_names)} Agent Skills -> {t.skills_dir}"
                     f"/<name>/SKILL.md:")
        lines.append("  " + "  ".join(plan.skill_names))
        lines.append("  (model-invocable twin of the commands; Claude may auto-engage these)")
        lines.append("")
    elif plan.plugin_provides_skills and t.skills_dir is not None:
        lines.append("Agent Skills: SKIPPED — the mokata plugin is installed and already "
                     "provides them; a project copy would duplicate every skill in Claude Code.")
        lines.append("")
    if plan.mcp_auto:
        lines.append(f"Will register the MCP server '{MCP_SERVER_NAME}' (command: "
                     f"{MCP_COMMAND}) in:")
        lines.append(f"  {t.mcp_path}")
    else:
        lines.append(f"MCP: register the '{MCP_SERVER_NAME}' server (command: {MCP_COMMAND}) "
                     f"with {plan.harness} yourself — automated MCP wiring is claude-only.")
    if plan.with_hooks:
        lines.append("")
        lines.append("Will wire hooks (SessionStart briefing + secret-guard) in:")
        lines.append(f"  {t.settings_path}")
    if (plan.with_hooks and plan.harness == "claude" and t.settings_path is not None
            and _statusline_setting_on(plan.root)):
        lines.append("")
        lines.append("Will wire the pipeline-stage badge as a statusLine (default-on; "
                     "opt out with `mokata config set settings.ux.statusline false`):")
        lines.append(f"  {t.settings_path}")
        lines.append("  (any existing statusLine is composed/wrapped, not overwritten.)")
    if plan.unsupported:
        # Degrade CLEARLY — never pretend a capability exists or silently skip it.
        lines.append("")
        lines.append(f"NOTE — '{plan.harness}' lacks: {', '.join(plan.unsupported)}. "
                     f"Those are NOT wired (e.g. no PreToolUse secret-guard / no native "
                     f"subagent fan-out); the engine degrades clearly when it needs them.")
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
    """True if a hook block is one mokata wired. Matches the Stage-53b `mokata-hook`
    console command AND the legacy hooks-dir path (so unsetup still cleans entries written
    by an older mokata)."""
    hooks_marker = str(_hooks_dir())
    for h in entry.get("hooks", []):
        if not isinstance(h, dict):
            continue
        cmd = str(h.get("command", ""))
        if "mokata-hook" in cmd or hooks_marker in cmd:
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


def _merge_statusline(path: Path) -> None:
    """Wire mokata's statusLine badge into settings.json — MERGE-SAFE (Stage 54b).

    No existing statusLine -> set ours. A user's own statusLine -> COMPOSE over it (run
    theirs, then mokata's) and stash the original under ``_mokataWrapped`` so unsetup can
    restore it verbatim. Already ours -> refresh, preserving any wrapped original
    (idempotent; never double-wraps)."""
    data = _load_json(path)
    existing = data.get("statusLine")

    wrapped = None
    if isinstance(existing, dict) and _is_mokata_statusline(existing):
        # already mokata's — keep any original we previously composed over
        prev = existing.get(_WRAPPED_KEY)
        wrapped = prev if isinstance(prev, dict) else None
    elif isinstance(existing, dict) and existing.get("command"):
        # a user's own statusLine — compose over it, preserving the original
        wrapped = existing

    inner = wrapped.get("command") if isinstance(wrapped, dict) else None
    block: Dict = {"type": "command", "command": _statusline_command(inner)}
    if isinstance(wrapped, dict):
        block[_WRAPPED_KEY] = wrapped
    data["statusLine"] = block
    _write_json(path, data)


def _statusline_setting_on(root: str) -> bool:
    """settings.ux.statusline from the committed manifest (default-on / opt-out). Read the
    file directly so apply_setup needn't construct a full Surface."""
    try:
        data = _load_json(Path(root).resolve() / MOKATA_DIR / MANIFEST_FILENAME)
        ux = data.get("settings", {}).get("ux", {})
        return bool(ux.get("statusline", True)) if isinstance(ux, dict) else True
    except Exception:
        return True


# --------------------------------------------------------------------------------------
# Command-file materialization (per-harness native format — Stage 63)
# --------------------------------------------------------------------------------------

def _command_target_name(harness: str, name: str) -> str:
    """The native filename a `<name>.md` template materializes to for this harness, so setup
    and unsetup agree on exactly which file to write/remove (no residue)."""
    fmt = _HARNESS_COMMAND_FORMAT[harness]
    stem = name[:-3] if name.endswith(".md") else name
    if fmt == "prompt.md":
        return f"{stem}.prompt.md"
    if fmt == "toml":
        return f"{stem}.toml"
    return name                          # "md" / "reference" keep the .md filename


def _frontmatter_description(md: str) -> str:
    """Best-effort pull of the `description:` line from a template's frontmatter."""
    if md.startswith("---"):
        end = md.find("\n---", 3)
        block = md[3:end] if end != -1 else md
        for line in block.splitlines():
            s = line.strip()
            if s.lower().startswith("description:"):
                return s.split(":", 1)[1].strip()
    return ""


def _to_gemini_toml(md: str) -> str:
    """A Gemini CLI custom-command TOML from a markdown template: a `description` + the full
    prompt as a multi-line LITERAL string. Deterministic; never escapes wrongly (templates
    carry no triple single-quote)."""
    desc = _frontmatter_description(md).replace("\\", "\\\\").replace('"', '\\"')
    body = md.replace("'''", "’’’") if "'''" in md else md
    return f"description = \"{desc}\"\nprompt = '''\n{body}'''\n"


def _write_command_file(harness: str, src: Path, commands_dir: Path, name: str) -> str:
    """Materialize one command template into the harness's NATIVE command surface (md copy,
    `*.prompt.md`, a Gemini `*.toml`, or a reference copy). Returns the written path. The
    claude/codex path stays a byte-identical `copyfile` (no regression)."""
    fmt = _HARNESS_COMMAND_FORMAT[harness]
    dst = commands_dir / _command_target_name(harness, name)
    if fmt == "toml":
        dst.write_text(_to_gemini_toml(src.read_text(encoding="utf-8")), encoding="utf-8")
    else:                                # md / prompt.md / reference all carry the md content
        shutil.copyfile(src, dst)
    return str(dst)


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

    # 2. slash/prompt/workflow commands — materialized in the harness's NATIVE format.
    t = plan.targets
    t.commands_dir.mkdir(parents=True, exist_ok=True)
    for name in plan.command_files:
        touched.append(_write_command_file(plan.harness, _templates_dir() / name,
                                            t.commands_dir, name))

    # 2b. Agent Skills (claude only) — the model-invocable twin of the commands, rendered from
    # the SAME command templates so the two surfaces can't drift. One dir per skill:
    # <skills_dir>/<name>/SKILL.md.
    if plan.skill_names and t.skills_dir is not None:
        from .agent_skills import generate_skill_files, write_skill_files
        files = generate_skill_files(_templates_dir(), names=tuple(plan.skill_names))
        for p in write_skill_files(t.skills_dir, files):
            touched.append(str(p))

    # 3. MCP server (auto-registered only where the harness supports it; else manual)
    if plan.mcp_auto and t.mcp_path is not None:
        _merge_mcp(t.mcp_path)
        touched.append(str(t.mcp_path))

    # 4. hooks (only if the harness supports them — capability-gated in plan_setup)
    if plan.with_hooks and t.settings_path is not None:
        _merge_hooks(t.settings_path, plan.hook_commands)
        touched.append(str(t.settings_path))

    # 5. statusLine badge (Stage 54b) — default-ON (opt-out), merge-safe. Claude-only (it's
    # a Claude Code feature); shares settings.json with the hooks, so `--no-hooks` (leave my
    # Claude settings alone) skips it too; skipped when settings.ux.statusline=false.
    if (plan.with_hooks and plan.harness == "claude" and t.settings_path is not None
            and _statusline_setting_on(plan.root)):
        _merge_statusline(t.settings_path)
        if str(t.settings_path) not in touched:
            touched.append(str(t.settings_path))

    return touched


def _default_confirm(prompt: str) -> bool:
    return read_yes_no(prompt)


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
    if harness == "claude":
        if plan.skill_names:
            emit(f"Installed {len(plan.skill_names)} Agent Skills "
                 f"({', '.join(plan.skill_names)}) alongside the /commands — Claude can now "
                 f"auto-engage them, and they show in the Agent Skills list.")
        emit("Restart Claude Code, then try /brainstorm or ask it to \"run mokata doctor\".")
    else:
        emit(f"Native surface: {_HARNESS_NATIVE_NOTE[harness]}.")
        emit(f"Point {harness} at the commands in {plan.targets.commands_dir}.")
        if not plan.mcp_auto:
            emit(f"MCP: register the '{MCP_SERVER_NAME}' server ({MCP_COMMAND}) with "
                 f"{harness} yourself (automated MCP wiring isn't available for it).")
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
    has_skills: bool = False          # an Agent Skills surface to remove (claude)


def plan_unsetup(harness: str, root: str = ".", scope: str = "project",
                 home: Optional[str] = None) -> UnsetupPlan:
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness '{harness}'; choose one of {HARNESSES}")
    targets = resolve_targets(scope, root, home, harness)
    tdir = _templates_dir()
    command_files = sorted(p.name for p in tdir.glob("*.md")) if tdir.is_dir() else []
    return UnsetupPlan(harness=harness, scope=scope, targets=targets,
                       command_files=command_files,
                       has_skills=targets.skills_dir is not None)


def render_unsetup_plan(plan: UnsetupPlan) -> str:
    t = plan.targets
    lines = [f"mokata unsetup {plan.harness} — scope '{plan.scope}'", ""]
    lines.append("Will remove:")
    lines.append(f"  copied commands in {t.commands_dir}")
    if plan.has_skills and t.skills_dir is not None:
        lines.append(f"  mokata Agent Skills in {t.skills_dir} (only mokata's own SKILL.md; "
                     f"your other skills are left untouched)")
    if t.mcp_path is not None:
        lines.append(f"  the '{MCP_SERVER_NAME}' MCP server entry in {t.mcp_path}")
    if t.settings_path is not None:
        lines.append(f"  mokata hook entries in {t.settings_path}")
        lines.append(f"  the mokata statusLine badge in {t.settings_path} "
                     f"(any composed-over statusLine is restored)")
    lines.append("")
    lines.append("Your .mokata/ config is left untouched (use `mokata reset` for that).")
    return "\n".join(lines)


def apply_unsetup(plan: UnsetupPlan) -> List[str]:
    t = plan.targets
    removed: List[str] = []

    # 1. commands (remove the harness's native filename — no residue across formats)
    for name in plan.command_files:
        p = t.commands_dir / _command_target_name(plan.harness, name)
        if p.exists():
            p.unlink()
            removed.append(str(p))

    # 1b. Agent Skills (claude) — remove ONLY mokata-authored SKILL.md (identified by the
    # banner marker), then their now-empty <name>/ dirs, and the skills/ dir if we emptied it.
    # Never touch a user's own skills; leave no mokata residue even if the curated set drifted.
    if plan.has_skills and t.skills_dir is not None and t.skills_dir.is_dir():
        from .agent_skills import SKILL_MARKER
        for sk in sorted(t.skills_dir.glob("*/SKILL.md")):
            try:
                if SKILL_MARKER not in sk.read_text(encoding="utf-8"):
                    continue
            except OSError:
                continue
            sk.unlink()
            removed.append(str(sk))
            if not any(sk.parent.iterdir()):
                sk.parent.rmdir()
        if t.skills_dir.exists() and not any(t.skills_dir.iterdir()):
            t.skills_dir.rmdir()

    # 2. MCP server entry
    if t.mcp_path is not None and t.mcp_path.exists():
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

    # 3. hook entries + the statusLine badge (Stage 54b) — one read/write of settings.json
    if t.settings_path is not None and t.settings_path.exists():
        data = _load_json(t.settings_path)
        changed = False

        hooks = data.get("hooks")
        if isinstance(hooks, dict):
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

        # statusLine: restore the user's original (if we composed over one), else drop ours.
        sl = data.get("statusLine")
        if isinstance(sl, dict) and _is_mokata_statusline(sl):
            wrapped = sl.get(_WRAPPED_KEY)
            if isinstance(wrapped, dict):
                data["statusLine"] = wrapped
            else:
                data.pop("statusLine", None)
            changed = True

        if changed:
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
