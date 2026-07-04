"""Agent Skills surface — the model-invocable twin of mokata's slash commands.

Claude Code exposes TWO distinct surfaces: slash *commands* (`/mokata:<name>`) and *Agent
Skills* (`SKILL.md` files Claude auto-engages from their `description`). mokata already ships
commands; this module renders the matching Agent Skills so mokata's capabilities also appear
in — and auto-trigger from — the Agent Skills list.

Single source, no drift: a skill is rendered from the SAME `templates/commands/<name>.md`
template the command ships from. The skill's trigger text is the template's own
`description` (+ `when_to_use` when present); the skill's body is the template's protocol
body VERBATIM, behind a fixed banner. Nothing is hand-copied, so the two surfaces can't
diverge — a drift-guard test re-renders and compares, exactly like the command templates.

Curated: only capabilities a user would want Claude to engage on its own are surfaced (the
pipeline gates + knowledge/session capabilities) — not the pure utilities (version, tour,
setup, …). The allow-list is an explicit constant below; add/remove a name to tune it.

Precedence note (Claude Code): when a skill and a command share a name, the SKILL takes
precedence. That's why the body carries the full protocol inline and never tells Claude to
"go run the /<name> command" (which would loop) — it follows the protocol right here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# The curated allow-list: command names that are genuinely MODEL-INVOCABLE — Claude should be
# able to engage them on its own when the moment fits. Pipeline gates + knowledge/session
# capabilities. Deliberately EXCLUDES pure utilities/orchestration mechanics (version, tour,
# setup, reconfigure, upgrade, init, enter, exec, resume, watch, progress, chain, decompose,
# skill, stacks, team, vault). Keep alphabetical-by-intent groups for legibility.
CURATED_SKILLS: tuple = (
    # exploration → spec → TDD build → land
    "brainstorm",
    "spec",
    "test",
    "develop",
    "review",
    "refine",
    "debug",
    "bug",
    "optimize",
    "ship",
    # knowledge / governance / portability
    "onboard",
    "govern",
    "session",
    "playbook",
    # harness repair (Stage 3b.4) — auto-engages when the MCP server/tools aren't connecting
    "mcp",
)

# A stable marker every rendered SKILL.md carries (in the banner). unsetup uses it to identify
# mokata-authored skills for clean removal WITHOUT ever touching a user's own SKILL.md — the
# same ownership discipline the hook/statusline wiring uses.
SKILL_MARKER = "mokata Agent Skill."

# The banner every rendered SKILL.md carries above the protocol body. Fixed text (only the
# capability name is interpolated) so it stays driftless. It frames the skill as the
# auto-engaged twin of the command and reinforces mokata's human-gate — WITHOUT telling
# Claude to re-invoke the command (skills shadow commands, so that would loop).
_SKILL_BANNER = (
    "> **mokata Agent Skill.** This is mokata's `{name}` capability, surfaced so Claude can "
    "engage it\n"
    "> automatically when the moment fits. It runs the SAME protocol as the `/mokata:{name}` "
    "command,\n"
    "> from one shared source — follow that protocol directly here; do not hand off to a "
    "parallel\n"
    "> flow. mokata's non-negotiables still hold: durable writes are **human-gated** (preview, "
    "then\n"
    "> explicit approval), and this capability's own gate is never silently skipped."
)


class SkillSourceError(RuntimeError):
    """A curated skill's source template is missing or malformed."""


@dataclass(frozen=True)
class SkillSource:
    name: str
    description: str
    when_to_use: Optional[str]
    body: str                       # the template's protocol body, verbatim (no frontmatter)


def parse_frontmatter(md: str) -> Dict[str, str]:
    """Parse a template's leading `---` frontmatter into a flat dict of single-line values.

    mokata's command templates use simple `key: value` frontmatter (one line per key); this
    is a deliberately small parser for exactly that shape, not a general YAML loader.
    """
    if not md.startswith("---"):
        return {}
    end = md.find("\n---", 3)
    if end == -1:
        return {}
    block = md[3:end]
    fm: Dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fm[key.strip()] = value.strip()
    return fm


def _split_frontmatter(md: str) -> str:
    """Return the body of a template (everything after its `---` frontmatter), leading blank
    lines stripped. Falls back to the whole text when there is no frontmatter."""
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            after = md[end + 4:]           # skip the closing "\n---"
            nl = after.find("\n")
            after = after[nl + 1:] if nl != -1 else ""
            return after.lstrip("\n")
    return md


def load_skill_source(name: str, templates_dir: Path) -> SkillSource:
    """Read one curated command template and extract the skill source (frontmatter + body)."""
    path = templates_dir / f"{name}.md"
    if not path.is_file():
        raise SkillSourceError(f"no command template for curated skill '{name}' at {path}")
    md = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(md)
    description = fm.get("description", "").strip()
    if not description:
        raise SkillSourceError(f"template '{name}.md' has no frontmatter description")
    when = fm.get("when_to_use") or None
    return SkillSource(name=name, description=description, when_to_use=when,
                       body=_split_frontmatter(md))


def render_skill_md(src: SkillSource) -> str:
    """Render a SKILL.md from a skill source. Frontmatter carries the model-invocation trigger
    (`description` [+ `when_to_use`]); the body is the fixed banner + the command's protocol
    body verbatim. Deterministic — the drift guard depends on it."""
    when_line = f"when_to_use: {src.when_to_use}\n" if src.when_to_use else ""
    return (
        f"---\n"
        f"name: {src.name}\n"
        f"description: {src.description}\n"
        f"{when_line}"
        f"---\n\n"
        f"{_SKILL_BANNER.format(name=src.name)}\n\n"
        f"{src.body}"
    )


def skill_markdown(name: str, templates_dir: Path) -> str:
    """One-shot: render the SKILL.md content for a curated skill name from its template."""
    return render_skill_md(load_skill_source(name, templates_dir))


def generate_skill_files(templates_dir: Path,
                         names: Optional[tuple] = None) -> Dict[str, str]:
    """Return {name: SKILL.md content} for the curated set (or a supplied subset). This is the
    single generator both the plugin-root `skills/` tree and the `setup` path render from."""
    chosen = names if names is not None else CURATED_SKILLS
    return {name: skill_markdown(name, templates_dir) for name in chosen}


def skill_relpaths(names: Optional[tuple] = None) -> List[str]:
    """The on-disk layout Claude Code expects: `<name>/SKILL.md`, one dir per skill."""
    chosen = names if names is not None else CURATED_SKILLS
    return [f"{name}/SKILL.md" for name in chosen]


def write_skill_files(skills_dir: Path, files: Dict[str, str]) -> List[Path]:
    """Materialize {name: content} into `<skills_dir>/<name>/SKILL.md`. Returns written paths."""
    written: List[Path] = []
    for name, content in files.items():
        dst = skills_dir / name / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        written.append(dst)
    return written


def find_orphan_skills(skills_dir: Optional[Path], keep) -> List[Path]:
    """The mokata-AUTHORED (``SKILL_MARKER``-bearing) ``<skills_dir>/<name>/SKILL.md`` paths whose
    dir name is NOT in ``keep``. READ-ONLY — this is what the setup PLAN previews and what
    :func:`prune_orphan_skills` deletes, sharing ONE marker check so plan and apply can't diverge.
    A skill WITHOUT the marker (a user's own) or an unreadable file is skipped — never flagged."""
    keep = set(keep)
    found: List[Path] = []
    if skills_dir is None:
        return found
    d = Path(skills_dir)
    if not d.is_dir():
        return found
    for sk in sorted(d.glob("*/SKILL.md")):
        if sk.parent.name in keep:
            continue
        try:
            if SKILL_MARKER not in sk.read_text(encoding="utf-8"):
                continue                         # a user's own skill — never touch it
        except OSError:
            continue                             # unreadable → leave it alone, don't crash
        found.append(sk)
    return found


def prune_orphan_skills(skills_dir: Optional[Path], keep) -> List[Path]:
    """SYNC the on-disk skills tree to the current curated set: remove every mokata-authored
    (marker-bearing) skill dir whose name is NOT in ``keep`` — the counterpart to
    :func:`write_skill_files`, which only writes the current set and never removes a dropped one.
    Cleans the now-empty ``<name>/`` dir, and the ``skills/`` dir itself if it ends up empty (so
    ``unsetup``, which passes ``keep=()``, leaves no residue). NEVER removes a non-marker (user)
    skill; an unreadable/undeletable file is skipped (degrade-clean). Returns the removed paths."""
    removed: List[Path] = []
    for sk in find_orphan_skills(skills_dir, keep):
        try:
            sk.unlink()
            removed.append(sk)
            if not any(sk.parent.iterdir()):
                sk.parent.rmdir()
        except OSError:
            continue                             # can't delete it → leave it, don't crash
    try:
        d = Path(skills_dir) if skills_dir is not None else None
        if d is not None and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    except OSError:
        pass
    return removed


def _regenerate_plugin_skills() -> List[Path]:
    """Regenerate the shipped plugin-root `skills/` tree from the command templates. Run this
    (``python -m mokata.agent_skills``) whenever a curated command's frontmatter changes; the
    drift-guard test then goes GREEN. This module is the single source — never hand-edit a
    SKILL.md. It's a SYNC: writes the current curated set, then prunes any marker-bearing skill
    dir no longer curated, so a removed skill can't ship stale in the wheel/plugin."""
    from . import package_data_root
    root = package_data_root()                          # the mokata package dir (holds the data)
    templates_dir = root / "templates" / "commands"
    skills_dir = root / "skills"
    written = write_skill_files(skills_dir, generate_skill_files(templates_dir))
    prune_orphan_skills(skills_dir, CURATED_SKILLS)     # drop any skill dropped from the curated set
    return written


if __name__ == "__main__":                              # pragma: no cover
    for _p in _regenerate_plugin_skills():
        print(_p)
