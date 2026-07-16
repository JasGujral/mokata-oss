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
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .init import init_repo
from . import MOKATA_DIR, MANIFEST_FILENAME, package_data_root
# Stage 3d.1: the shared MCP name + Claude config-path resolution live in the neutral
# `harness_paths` leaf so `mcp_admin` can depend on THEM instead of on this module (which
# broke the mcp_admin ↔ harness_setup cycle). Re-exported below for back-compat importers.
from .harness_paths import (MCP_APPROVE_TOOL_ASK, MCP_SERVER_NAME,
                            MCP_TOOL_PERMISSION, claude_mcp_config_path,
                            scope_base)
# Stage 3d.1: cycle gone → the `mcp_admin` import that used to be lazy (below, in
# `setup_harness`) is now a plain module-level import.
from .mcp_admin import parity_lines, status_lines
from .errors import MokataError

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

# The MCP server command (mirrors .claude-plugin/plugin.json's mcpServers entry). The
# companion `MCP_SERVER_NAME` now lives in `harness_paths` and is re-exported at the top.
MCP_COMMAND = "mokata-mcp"

# The hook console entry point (mirrors hooks/hooks.json + pyproject `mokata-hook`).
HOOK_COMMAND = "mokata-hook"

# PreToolUse matchers (mirror hooks/hooks.json). TWO independent hooks share this event:
#   secret-guard — the SECURITY block (G4/I1). Also matches Bash: a secret can leak through a
#                  shell command (`echo $KEY >> ...`, a curl with a token), not just a file write.
#   gate-guard   — the SI.1 RUN-STATE GATE block. File-mutation tools ONLY: it decides from a target
#                  PATH, and a Bash command has none to decide on (shelling out to `sed -i` is a
#                  known, documented hole — closing it needs command parsing, not a wider matcher).
HOOK_PRETOOL_MATCHER = "Write|Edit|MultiEdit|Bash"
HOOK_GATE_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"
#   dirty-track  — GR.S4 PostToolUse ASYNC OBSERVABILITY hook. File-mutation tools only (it
#                  records the written PATH into the graph dirty-set). NEVER blocks (exit 0
#                  always) — it is not a security lane; the sync PreToolUse blockers are untouched.
HOOK_DIRTY_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"


def repo_root() -> Path:
    """The directory that holds the packaged artifacts — ``templates/`` and ``hooks/``.

    Stage 3: these live INSIDE the package, so this resolves to the ``mokata`` package
    directory for BOTH a pip-installed wheel (site-packages/mokata) and a clone/editable
    install (``<clone>/src/mokata``) — no manual clone required. It is a real directory
    containing the data in either case, which is what the ``--plugin-root`` embedding and
    the SessionStart hook need to locate the bundled engine."""
    return package_data_root()


def _templates_dir() -> Path:
    return repo_root() / "templates" / "commands"


def _hooks_dir() -> Path:
    return repo_root() / "hooks"


class SetupError(MokataError):
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
    base = scope_base(scope, root, home)
    hdir = _HARNESS_DIR[harness]
    commands_dir = base / hdir / _HARNESS_COMMANDS_SUBDIR[harness]
    settings_path = base / hdir / "settings.json"
    if _HARNESS_MCP_AUTO[harness]:
        if harness == "claude":
            # Claude Code's user-scoped MCP config lives in ~/.claude.json (where
            # `claude mcp add --scope user` writes); project scope uses .mcp.json.
            # Delegated to the neutral leaf so mcp_admin resolves the SAME path.
            mcp_path = claude_mcp_config_path(scope, root, home)
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
    # SI.1: event -> the LIST of hook entries mokata wires on it, each `{"command", "matcher"?}`.
    # It was one command per event until PreToolUse had to carry BOTH the secret-guard (security)
    # and the gate-guard (run-state) hooks, with DIFFERENT matchers — so the value is a list and the
    # matcher travels with its own command instead of being assumed per event.
    hook_commands: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    mcp_auto: bool = True             # auto-register the MCP server (claude); else manual
    grant: bool = False               # grant CC permission for mokata's MCP tools (claude)
    unsupported: List[str] = field(default_factory=list)  # capabilities this harness lacks
    skill_names: List[str] = field(default_factory=list)  # Agent Skills to install (claude)
    skill_orphans: List[str] = field(default_factory=list)  # stale mokata skills to PRUNE (sync)


# The hook scripts (kept as standalone shims) map to `mokata-hook` subcommands.
_HOOK_SUBCOMMAND = {"session_start.py": "session-start", "secret_guard.py": "secret-guard",
                    "gate_guard.py": "gate-guard", "dirty_track.py": "dirty-track"}


def _hook_command(script: str) -> str:
    # Stage 53b / B1: wire hooks as the `mokata-hook` console entry point — the SAME mechanism
    # (and now the SAME resolver, `resolved_console_script`) the bundled `mokata-mcp` server
    # uses — instead of a bare `python3` (which a minimal PATH or Windows wouldn't resolve) or
    # the `sh launch.sh` chain. `mokata setup` runs from the installed package, so `mokata-hook`
    # is a guaranteed sibling console script; B1 resolves it to an ABSOLUTE path (via the
    # interpreter-sibling fallback `which` alone misses) so hooks fire under the GUI-launched
    # minimal PATH; the plugin's `launch.sh` stays the documented fallback if nothing resolves.
    exe = resolved_console_script(HOOK_COMMAND)
    sub = _HOOK_SUBCOMMAND[script]
    cmd = f'"{exe}" {sub}'
    if sub == "session-start":
        # Forward the clone root so /mokata:init can locate the bundled engine (manual
        # setup has no ${CLAUDE_PLUGIN_ROOT}).
        cmd += f' --plugin-root "{repo_root()}"'
    return cmd


def _statusline_command(wrap_command: Optional[str] = None) -> str:
    # Stage 54b / B1: the Claude Code statusLine command — the SAME absolute-path-resolved
    # `mokata-hook` console entry point the hooks use (shared `resolved_console_script`, so it
    # also survives the GUI-launched minimal PATH). With `wrap_command` set, mokata COMPOSES
    # over a user's existing statusLine (runs theirs, then appends mokata's) instead of clobbering.
    exe = resolved_console_script(HOOK_COMMAND)
    cmd = f'"{exe}" statusline'
    if wrap_command:
        cmd += f" --wrap {shlex.quote(wrap_command)}"
    return cmd


def _is_mokata_statusline(block: Dict) -> bool:
    """True if a settings.json statusLine block is one mokata wired (so re-setup is
    idempotent and unsetup only touches our own)."""
    cmd = str(block.get("command", "")) if isinstance(block, dict) else ""
    return "mokata-hook" in cmd and "statusline" in cmd


def plan_setup(
    harness: str,
    root: str = ".",
    scope: str = "project",
    profile: str = "standard",
    with_hooks: bool = True,
    home: Optional[str] = None,
    grant: bool = True,
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
    # The MCP tool grant is a Claude Code concept and shares settings.json with the hooks, so
    # it rides on `with_hooks` (—no-hooks = "leave my Claude settings alone" → no grant either)
    # and only where mokata auto-registers the server (claude). Default ON; `--no-grant` opts out.
    grant = grant and harness == "claude" and mcp_auto and with_hooks

    # NOTE (Stage 3): do NOT hard-fail on a missing source ``hooks/`` dir. The hooks RUN via
    # the ``mokata-hook`` console entry point (pip-installed, Stage 53b) — the standalone
    # ``hooks/*.py`` shims + ``launch.sh`` are only the legacy pure-plugin fallback. So an
    # installed wheel wires working hooks from the entry point whether or not the source dir
    # is present; the dir requirement was a spurious pip-blocker and is gone.

    targets = resolve_targets(scope, root, home, harness)
    manifest_path = Path(root).resolve() / MOKATA_DIR / MANIFEST_FILENAME
    needs_init = not manifest_path.exists()

    hook_commands: Dict[str, List[Dict[str, str]]] = {}
    if with_hooks:
        hook_commands = {
            "SessionStart": [{"command": _hook_command("session_start.py")}],
            "PreToolUse": [
                {"command": _hook_command("secret_guard.py"),
                 "matcher": HOOK_PRETOOL_MATCHER},
                {"command": _hook_command("gate_guard.py"),
                 "matcher": HOOK_GATE_MATCHER},
            ],
            # GR.S4 — the ASYNC OBSERVABILITY lane: record touched paths for the read-time graph
            # freshness contract. Exits 0 always; the sync PreToolUse security blockers above are
            # untouched.
            "PostToolUse": [
                {"command": _hook_command("dirty_track.py"),
                 "matcher": HOOK_DIRTY_MATCHER},
            ],
        }

    # Agent Skills — the model-invocable twin of the slash commands. Only where the harness has
    # that surface (claude); degrade-clean (empty list) everywhere else. The curated set renders
    # from the SAME command templates, so it can't drift from the commands. mokata setup is a
    # REPLACE (Stage 5): it ALWAYS (over)writes the current curated skills into the project — the
    # project copy is authoritative — with NO plugin-based suppression. A reinstall therefore always
    # delivers the current, non-stale set. (If the mokata plugin is ALSO installed, Claude Code may
    # list a skill twice — `mokata:<name>` + `<name>` — which is accepted: reliably current beats
    # deduped-but-stale, and plugin detection was never dependable.)
    from .agent_skills import CURATED_SKILLS, find_orphan_skills, installed_skill_names
    # The template-rendered pipeline skills are the set setup GENERATES (skill_names); the
    # hand-authored domain skills (DK.S1+) are COPIED alongside them at apply-time. The keep-set
    # for orphan detection/prune is BOTH (installed_skill_names) so a domain skill is delivered and
    # never pruned as an orphan; template GENERATION stays curated-only (domain skills have no
    # command template to render from).
    skill_names = list(CURATED_SKILLS) if targets.skills_dir is not None else []
    keep_names = list(installed_skill_names()) if targets.skills_dir is not None else []

    # Stage 5 — SYNC: after (re)writing the current set, setup PRUNES any mokata-authored skill
    # DROPPED from it, so `.claude/skills/` matches the current set exactly and users stop seeing
    # old/removed skills after an update. Compute the orphans now (read-only) so the human-gated
    # plan can show the removal BEFORE approval — pruning is a durable delete. A user's own
    # (non-marker) skill is never an orphan; a shipped domain skill is in the keep-set, so kept.
    skill_orphans = [p.parent.name for p in
                     find_orphan_skills(targets.skills_dir, keep_names)]

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
        grant=grant,
        unsupported=unsupported,
        skill_names=skill_names,
        skill_orphans=skill_orphans,
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
    if plan.skill_orphans and t.skills_dir is not None:
        # A durable delete → it MUST be visible before approval (P2). Only mokata-authored
        # (marker-bearing) skills no longer in the curated set; user skills are never touched.
        lines.append(f"Removing {len(plan.skill_orphans)} stale mokata skill(s) no longer "
                     f"curated -> {t.skills_dir}/<name>/ (your own skills are left untouched):")
        lines.append("  " + "  ".join(plan.skill_orphans))
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
        lines.append("Will wire hooks (SessionStart briefing + secret-guard + run-state "
                     "gate-guard) in:")
        lines.append(f"  {t.settings_path}")
    if plan.grant and t.settings_path is not None:
        lines.append("")
        lines.append("Will grant Claude Code permission for mokata's tools + enable the "
                     "mokata MCP server (so Claude Code doesn't gate each mcp__mokata__* "
                     "call) in:")
        lines.append(f"  {t.settings_path}")
        lines.append(f"  (enabledMcpjsonServers += {MCP_SERVER_NAME}; "
                     f"permissions.allow += {MCP_TOOL_PERMISSION}; opt out with --no-grant)")
        lines.append(f"  (permissions.ask += {MCP_APPROVE_TOOL_ASK} — the in-chat approve tool is "
                     "NEVER auto-granted; Claude Code prompts you on every approve call even with "
                     "the wildcard above. AP-MCP, doc 85 D26 amendment.)")
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
    """Read a JSON-object config for merging. FAIL-CLOSED (P2): a file that doesn't exist is
    fresh (`{}` — we'll create it), but a file that EXISTS and can't be understood is NEVER
    silently treated as empty — that would let `_write_json` overwrite it and destroy the
    user's other servers/keys. So an existing-but-unparseable / unreadable / non-object file
    RAISES `SetupError` naming the file and the fix. Merge-safety only holds for files that
    parse; anything else we refuse to touch."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise SetupError(
            f"{path} exists but isn't valid JSON ({exc}) — mokata won't overwrite a config it "
            f"can't parse (it would destroy your other entries). Fix or remove the file, then "
            f"re-run."
        ) from exc
    except OSError as exc:
        raise SetupError(
            f"{path} exists but can't be read ({exc}) — mokata won't overwrite a config it "
            f"can't read. Fix the permissions or remove the file, then re-run."
        ) from exc
    if not isinstance(data, dict):
        raise SetupError(
            f"{path} exists but isn't a JSON object (found {type(data).__name__}) — mokata "
            f"merges into an object and won't overwrite this. Fix or remove the file, then "
            f"re-run."
        )
    return data


def _write_json(path: Path, data: Dict) -> None:
    # ATOMIC write (P2): render to a temp file in the SAME directory, then `os.replace` it onto
    # the target — a crash mid-write leaves the old config intact rather than a truncated/
    # half-written one. Same-dir temp keeps the rename atomic (no cross-filesystem move).
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        # D5 — `BaseException` is CORRECT here and is deliberately NOT narrowed. This handler does
        # not swallow anything: it cleans up the temp file and RE-RAISES. A KeyboardInterrupt or a
        # SystemExit landing mid-write must also delete the stray temp file, and those are exactly
        # the two that `except Exception` would miss. Nothing is hidden; nothing degrades.
        # Never leave a stray temp file behind if the write/rename fails.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_or_remove_json(path: Path, data: Dict) -> None:
    """Persist a merged config, or DELETE the file when nothing's left. If stripping mokata's
    entries emptied `data` (`not data`), the file is removed for a byte-clean reversal rather
    than left as an empty `{}` husk. This is safe by construction: any surviving non-mokata key
    keeps `data` non-empty, so the file is written, never deleted — user content is preserved."""
    if data:
        _write_json(path, data)
    else:
        path.unlink(missing_ok=True)


def resolved_console_script(name: str) -> str:
    """Resolve a mokata console entry point (`mokata-mcp`, `mokata-hook`) to an ABSOLUTE path
    so it survives the GUI-launched minimal PATH — the classic silent killer: a GUI-launched
    macOS app inherits a PATH WITHOUT the console-script dir, so a bare name never resolves and
    the server/hook silently never runs (the same failure class as the old `python3: command
    not found`). Resolution order, SHARED so `mokata-mcp` and `mokata-hook` resolve identically:
    (1) `shutil.which` on the current PATH; (2) a console-script sibling of the running
    interpreter (`<sys.executable dir>/<name>[.exe]`) — this covers a venv/install whose bin
    dir isn't on PATH, which is precisely the silent-killer case `which` can't see; (3) the bare
    name as a last resort (e.g. `-e` source runs), which the plugin's `launch.sh` still backs."""
    found = shutil.which(name)
    if found:
        return found
    bindir = Path(sys.executable).parent
    for candidate in (bindir / name, bindir / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return name


def resolved_mcp_command() -> str:
    """The `mokata-mcp` command an installed registration should launch — resolved to an
    ABSOLUTE path so auto-start survives the GUI-launched PATH (the classic silent killer),
    via the shared `resolved_console_script` (identical rule to how the hooks resolve
    `mokata-hook`)."""
    return resolved_console_script(MCP_COMMAND)


def _merge_mcp(path: Path, command: str = MCP_COMMAND) -> None:
    # Single source of MCP-merge truth: NEVER clobber other servers or unrelated keys.
    # `command` is a parameter so `mcp install` can opt into the resolved absolute path
    # (see `resolved_mcp_command`) while the default keeps `setup`'s existing behaviour —
    # one merge implementation, no fork that can drift.
    data = _load_json(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[MCP_SERVER_NAME] = {"command": command, "args": []}
    data["mcpServers"] = servers
    _write_json(path, data)


def mcp_install(scope: str, root: str = ".", home: Optional[str] = None,
                grant: bool = True) -> Path:
    """Write/repair the Claude Code registration for the bundled mokata MCP server,
    resolving `mokata-mcp` to an absolute path. Reuses the shared `_merge_mcp` (merge-safe,
    idempotent). Returns the registration file path. Same scopes as the `setup` path:
    project → `<root>/.mcp.json`, user → `<home>/.claude.json`.

    With `grant` (default), it ALSO grants Claude Code permission for mokata's MCP tools +
    enables the server in `.claude/settings.json` (the "stuck-loop" fix — see `_merge_grant`),
    so a repair install fixes BOTH the registration and the permission gate. `--no-grant`
    (grant=False) leaves settings.json untouched (for teams that manage permissions)."""
    targets = resolve_targets(scope, root, home, "claude")
    if targets.mcp_path is None:                       # pragma: no cover - claude always auto
        raise SetupError("the claude harness has no auto-wired MCP config path.")
    _merge_mcp(targets.mcp_path, resolved_mcp_command())
    touched = [str(targets.mcp_path)]
    if grant and targets.settings_path is not None:
        _merge_grant(targets.settings_path)
        touched.append(str(targets.settings_path))
    # KB.S1 — the repair-install writes .mcp.json / settings.json through `_write_json` too; record
    # it on the repo ledger like the setup flow (P7).
    _record_setup_ledger(root, "mcp_install", touched, scope=scope)
    return targets.mcp_path


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


def _merge_hooks(path: Path, hook_commands: Dict[str, List[Dict[str, str]]]) -> None:
    """Wire mokata's hook entries into settings.json — MERGE-SAFE and idempotent.

    Per event: drop EVERY prior mokata-wired entry (`_is_mokata_hook`), then append the current
    set fresh. So re-setup / update REFRESHES the wiring (the skills-sync pattern — an old command
    path or a dropped hook can never linger), a hook we ADD lands on the next `setup`, and the
    user's own entries on the same event are never touched. Each entry carries its OWN matcher, so
    the two PreToolUse hooks (secret-guard, gate-guard) can match different tool sets."""
    data = _load_json(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    for event, entries_to_wire in hook_commands.items():
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
        # Idempotent: drop any prior mokata-wired entry for this event, then add fresh.
        entries = [e for e in entries if not (isinstance(e, dict) and _is_mokata_hook(e))]
        for spec in entries_to_wire:
            block: Dict = {"hooks": [{"type": "command", "command": spec["command"]}]}
            matcher = spec.get("matcher")
            if matcher:
                block["matcher"] = matcher
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


def _merge_grant(path: Path) -> None:
    """Grant Claude Code permission for mokata's MCP tools + enable the mokata project server
    — MERGE-SAFE (never clobber; dedup arrays). This is the fix for the "MCP tools get stuck"
    hang: without these two keys Claude Code GATES every ``mcp__mokata__*`` call (approval
    loop). Two keys in ``settings.json``:

      * ``enabledMcpjsonServers`` += ``"mokata"`` — surgically trust mokata's project server
        (NOT ``enableAllProjectMcpServers``, which would trust every project's server);
      * ``permissions.allow`` += ``"mcp__mokata__*"`` — unioned into any existing allow list;
      * ``permissions.ask``   += ``"mcp__mokata__approve"`` — AP-MCP (doc 85 §5 D26 amendment):
        the in-chat approve tool must NEVER ride the allow-wildcard. The wildcard already MATCHES
        ``mcp__mokata__approve``; Claude Code evaluates deny → ask → allow, so this explicit ``ask``
        entry overrides it and forces a human PROMPT on every approve call, even when the tool is
        opted on. Written UNCONDITIONALLY alongside the allow grant so the wildcard can never
        silently cover the approve tool (the two are inseparable — see the grant-exclusion test).

    Idempotent (re-running converges) and reversible (``_strip_grant`` removes exactly these)."""
    data = _load_json(path)

    enabled = data.get("enabledMcpjsonServers")
    if not isinstance(enabled, list):
        enabled = []
    if MCP_SERVER_NAME not in enabled:
        enabled.append(MCP_SERVER_NAME)
    data["enabledMcpjsonServers"] = enabled

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []
    if MCP_TOOL_PERMISSION not in allow:
        allow.append(MCP_TOOL_PERMISSION)
    perms["allow"] = allow
    # AP-MCP grant-exclusion: the explicit prompt on the approve tool, written whenever the
    # allow-wildcard is (they are inseparable — the wildcard covers approve without this).
    ask = perms.get("ask")
    if not isinstance(ask, list):
        ask = []
    if MCP_APPROVE_TOOL_ASK not in ask:
        ask.append(MCP_APPROVE_TOOL_ASK)
    perms["ask"] = ask
    data["permissions"] = perms

    _write_json(path, data)


def _strip_grant(data: Dict) -> bool:
    """Remove EXACTLY the keys `_merge_grant` added — mokata from `enabledMcpjsonServers` and
    `mcp__mokata__*` from `permissions.allow` — preserving the user's own entries. Empties are
    dropped for a byte-clean reversal. Returns True if anything changed. In-place on `data`."""
    changed = False

    enabled = data.get("enabledMcpjsonServers")
    if isinstance(enabled, list) and MCP_SERVER_NAME in enabled:
        kept = [s for s in enabled if s != MCP_SERVER_NAME]
        if kept:
            data["enabledMcpjsonServers"] = kept
        else:
            data.pop("enabledMcpjsonServers", None)
        changed = True

    perms = data.get("permissions")
    if isinstance(perms, dict):
        perms_changed = False
        allow = perms.get("allow")
        if isinstance(allow, list) and MCP_TOOL_PERMISSION in allow:
            kept = [a for a in allow if a != MCP_TOOL_PERMISSION]
            if kept:
                perms["allow"] = kept
            else:
                perms.pop("allow", None)
            perms_changed = True
        # AP-MCP: remove the explicit approve prompt symmetrically (the mirror of `_merge_grant`).
        ask = perms.get("ask")
        if isinstance(ask, list) and MCP_APPROVE_TOOL_ASK in ask:
            kept_ask = [a for a in ask if a != MCP_APPROVE_TOOL_ASK]
            if kept_ask:
                perms["ask"] = kept_ask
            else:
                perms.pop("ask", None)
            perms_changed = True
        if perms_changed:
            if perms:
                data["permissions"] = perms
            else:
                data.pop("permissions", None)
            changed = True

    return changed


def _statusline_setting_on(root: str) -> bool:
    """settings.ux.statusline from the committed manifest (default-on / opt-out). Read the
    file directly so apply_setup needn't construct a full Surface."""
    try:
        data = _load_json(Path(root).resolve() / MOKATA_DIR / MANIFEST_FILENAME)
        ux = data.get("settings", {}).get("ux", {})
        return bool(ux.get("statusline", True)) if isinstance(ux, dict) else True
    except (SetupError, OSError, AttributeError):
        # D5 — the real raisers: `_load_json` converts an unparseable/unreadable/non-object
        # manifest into `SetupError`; `Path(root).resolve()` can still raise `OSError`; and a
        # manifest whose `settings` is null/a list makes the chained `.get` raise `AttributeError`
        # (so AttributeError is added to the named set — narrowing without it would turn today's
        # swallow into a CRASH). Default-ON is the documented opt-out default, so True is unchanged.
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
    from .skills import expand_grounding
    fmt = _HARNESS_COMMAND_FORMAT[harness]
    dst = commands_dir / _command_target_name(harness, name)
    # SK.S2 single-source: expand the grounding MARKER the template carries into the one canonical
    # block at write time, so the materialized command never ships a marker and every command
    # surface derives from `skills.GROUNDING_DISCIPLINE` (no per-template copy to drift).
    content = expand_grounding(src.read_text(encoding="utf-8"))
    if fmt == "toml":
        dst.write_text(_to_gemini_toml(content), encoding="utf-8")
    else:                                # md / prompt.md / reference all carry the md content
        dst.write_text(content, encoding="utf-8")
    return str(dst)


# --------------------------------------------------------------------------------------
# Audit-ledger record (KB.S1)
# --------------------------------------------------------------------------------------

def _record_setup_ledger(root: str, action: str, files, *, actor: str = "cli",
                         removed=None, **extra) -> None:
    """Record a setup/unsetup durable write on the REPO's audit ledger (KB.S1 / P7: every durable
    write leaves a record). These flows keep their existing bespoke human-at-TTY consent; this adds
    only the record — the raw committers (`_write_json`, `_write_command_file`, `write_skill_files`,
    `prune_orphan_skills`) are recorded here, at the flow that drives them, on the repo ledger.

    Names the touched PATHS only — never their contents (mokata's own scaffolding: command
    templates, SKILL.md, .claude/settings.json, .mcp.json — not user content, so not content
    review). Best-effort: a ledger IO failure never breaks the wiring."""
    try:
        from .govern.ledger import AuditLedger
        mokata_dir = os.path.join(root, MOKATA_DIR)
        rec: Dict = {"action": action,
                     "files": sorted(str(f) for f in files), "actor": actor}
        if removed:
            rec["removed"] = sorted(str(f) for f in removed)
        rec.update(extra)
        AuditLedger.from_mokata_dir(mokata_dir).record("setup", **rec)
    except OSError:
        # Non-fatal (the `user_prefs` philosophy): the wiring is already on disk; a ledger that
        # can't be written on a broken FS never blocks setup/unsetup.
        pass


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
        from .agent_skills import (copy_skill_references, generate_skill_files,
                                   write_skill_files)
        files = generate_skill_files(_templates_dir(), names=tuple(plan.skill_names))
        for p in write_skill_files(t.skills_dir, files):
            touched.append(str(p))
        # SK.S2 — deliver each skill's progressive-disclosure references/ from the shipped tree,
        # so the setup path matches the plugin install (both carry the JIT detail files).
        src_skills = _templates_dir().parent.parent / "skills"
        for p in copy_skill_references(src_skills, t.skills_dir, names=tuple(plan.skill_names)):
            touched.append(str(p))
        # DK.S1 — deliver the hand-authored DOMAIN skills (api/security/…) verbatim alongside the
        # pipeline skills: static SKILL.md + references, copied (not template-rendered), so they
        # auto-engage exactly like a native skill.
        from .agent_skills import install_domain_skills
        for p in install_domain_skills(src_skills, t.skills_dir):
            touched.append(str(p))

    # 2c. SYNC (Stage 5) — after writing the current set, PRUNE any mokata-authored skill dropped
    # from it, so the tree matches the curated set exactly (users stop seeing old/removed skills
    # after an update). Marker-guarded: a user's own skill is NEVER removed. `plan.skill_names`
    # is the set we keep (the intended project set — empty when the plugin is the sole source).
    pruned: List[str] = []
    if t.skills_dir is not None:
        from .agent_skills import installed_skill_names, prune_orphan_skills
        # Keep BOTH the pipeline skills just written AND the shipped domain skills just copied — a
        # domain skill is marker-bearing but not curated, so it must be in the keep-set or the sync
        # would delete it right after install.
        for p in prune_orphan_skills(t.skills_dir, installed_skill_names()):
            touched.append(str(p))
            pruned.append(str(p))

    # 3. MCP server (auto-registered only where the harness supports it; else manual).
    # Stage 3b.3: claude registers the RESOLVED absolute command (the SAME resolver as
    # `mokata mcp install` — one registration path, no fork) so auto-start survives the
    # GUI-launched PATH; other harnesses keep the bare command.
    if plan.mcp_auto and t.mcp_path is not None:
        command = resolved_mcp_command() if plan.harness == "claude" else MCP_COMMAND
        _merge_mcp(t.mcp_path, command)
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

    # 6. MCP tool grant (the "stuck-loop" fix) — default-ON, merge-safe. Grants Claude Code
    # permission for mokata's MCP tools + enables the server so it doesn't gate each call.
    # Claude-only, shares settings.json (so `--no-hooks` skips it), opt out with `--no-grant`.
    if plan.grant and t.settings_path is not None:
        _merge_grant(t.settings_path)
        if str(t.settings_path) not in touched:
            touched.append(str(t.settings_path))

    # KB.S1 — one batched record of the whole apply on the repo ledger (commands, skills, MCP /
    # settings merges, prunes). Names the paths, never their contents.
    _record_setup_ledger(plan.root, "setup", touched, removed=pruned,
                         harness=plan.harness, scope=plan.scope, profile=plan.profile)

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
    grant: bool = True,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
) -> SetupResult:
    """Plan + human-gate + apply the harness wiring end to end."""
    emit = out or print
    plan = plan_setup(harness, root, scope, profile, with_hooks, home, grant=grant)

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
        if plan.grant:
            emit("✓ Granted Claude Code permission for mokata's MCP tools + enabled the "
                 "server.")
        emit("Restart Claude Code, then try /brainstorm or ask it to \"run mokata doctor\".")
        # Stage 3b.3 — CONNECTED verification. Run the SAME `initialize` handshake as
        # `mokata mcp status` and report the result so the user knows the server is actually
        # reachable (not just registered). INFORMATIONAL ONLY: bounded, never raises, and
        # never gates setup success — a broken server prints its specific fix, setup still ok.
        if plan.mcp_auto and plan.targets.mcp_path is not None:
            # D5 — the guard that used to wrap this call was REDUNDANT and has been deleted.
            # `mcp_admin.status_lines` is contractually never-raise ("Never raises"): it catches its
            # own failures and RETURNS them as a "status check skipped (…)" report line. The
            # `except Exception: pass` could therefore only ever have swallowed a bug in this loop,
            # while hiding the very report the call exists to print.
            _connected, lines = status_lines(root=root, home=home)
            emit("")
            for line in lines:
                emit(line)
            # B-VER — version-parity preventive check. CONNECTED above proves the server is
            # REACHABLE; this proves it serves the SAME VERSION as the CLI that just wrote the
            # registration (probing the exact command in the file just written), and sweeps for
            # scope/plugin shadowing. Loud-only: a clean MATCH emits nothing (setup stays
            # byte-identical). Additive + fail-open — the probe NEVER gates setup success.
            for line in parity_lines(root=root, home=home):
                emit(line)
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
        lines.append(f"  the mokata MCP tool grant in {t.settings_path} "
                     f"(enabledMcpjsonServers / permissions.allow; your own entries kept)")
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
        # keep=() → every mokata-authored (marker-bearing) skill is an orphan here; the same
        # shared, marker-guarded prune setup uses, so a user's own skills still survive unsetup
        # and no mokata residue is left even if the curated set drifted.
        from .agent_skills import prune_orphan_skills
        for p in prune_orphan_skills(t.skills_dir, ()):
            removed.append(str(p))

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
            _write_or_remove_json(t.mcp_path, data)
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

        # MCP tool grant: remove exactly mokata's enabledMcpjsonServers + permissions.allow
        # entries (the user's own entries survive).
        if _strip_grant(data):
            changed = True

        if changed:
            _write_or_remove_json(t.settings_path, data)
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
    # KB.S1 — record the unsetup removal on the repo ledger (P7). This is where the setup/unsetup
    # path's `prune_orphan_skills` + `_write_or_remove_json` deletions are audited; names the
    # removed paths only. The repo's .mokata config is left intact by unsetup (only the harness
    # wiring is removed), so the ledger survives to carry the record.
    _record_setup_ledger(root, "unsetup", [], removed=removed, actor="cli",
                         harness=harness, scope=scope)
    emit("")
    emit(f"removed mokata wiring for {harness} ({scope} scope).")
    return UnsetupResult(removed=removed, plan=plan, message="ok")
