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
    # docs↔code reconciliation (DK.S5) — audits/reconciles docs against the code, auto-fires on
    # drift, and backs brainstorm's Lens 1 doc-freshness check + the pre-release doc gate.
    "docsync",
    # harness repair (Stage 3b.4) — auto-engages when the MCP server/tools aren't connecting
    # (named `mcp-repair`, not `mcp`, to avoid colliding with Claude Code's built-in /mcp and
    # the mcp-builder skill — its repair identity is distinctive).
    "mcp-repair",
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
    (`description` [+ `when_to_use`]); the body is the fixed banner + the SK.S1 activation line
    + the command's protocol body verbatim + the SK.S1 `## Contract` block. Deterministic — the
    drift guard depends on it.

    SK.S1 single-source: the `⛭ … active — gate:` line and the Contract are NOT written here per
    skill — they are pulled from `progress.active_skill_line` and `skill_contracts.render_contract_md`
    (one source each), so all 15 SKILL.md render from the same place and can't drift into 15 copies.
    """
    from .progress import active_skill_line
    from .skill_contracts import render_contract_md
    from .skill_anatomy import render_anatomy_md
    from .skills import expand_grounding
    when_line = f"when_to_use: {src.when_to_use}\n" if src.when_to_use else ""
    activation = active_skill_line(src.name)                    # single source (progress.py)
    # SK.S2 single-source: expand the grounding MARKER the template carries into the one
    # canonical block (skills.grounding_block) — no template holds a literal copy that can drift.
    body = expand_grounding(src.body.rstrip())
    anatomy = render_anatomy_md(src.name)                       # single source (skill_anatomy.py)
    contract = render_contract_md(src.name)                     # single source (skill_contracts.py)
    return (
        f"---\n"
        f"name: {src.name}\n"
        f"description: {src.description}\n"
        f"{when_line}"
        f"---\n\n"
        f"{_SKILL_BANNER.format(name=src.name)}\n\n"
        f"{activation}\n\n"
        f"{body}\n\n"
        f"{anatomy}\n\n"
        f"{contract}\n"
    )


def skill_markdown(name: str, templates_dir: Path) -> str:
    """One-shot: render the SKILL.md content for a curated skill name from its template."""
    return render_skill_md(load_skill_source(name, templates_dir))


# CAT.S1 — the COMPLETE curated catalog `mokata skills` shows. It is the whole of
# ``CURATED_SKILLS`` (so the count follows that single source and can't drift), split into two
# groups the user can tell apart:
#   * RUNNABLE pipeline skills — those that also live in the `skills._SKILLS` registry, so
#     `mokata run <name>` / `mokata <name>` drives them; their one-line summary is the registry's
#     own `summary` (single source).
#   * STANDALONE / auto-firing skills — the curated capabilities that are NOT in `_SKILLS`
#     (govern, session, playbook, mcp-repair, docsync): they auto-fire and/or have their own
#     command, never `mokata run`. Their summary is single-sourced from the command-template
#     frontmatter `description` (the SAME text the shipped SKILL.md carries), so it can't drift.
@dataclass(frozen=True)
class CatalogEntry:
    name: str
    summary: str            # one-line — single-sourced (registry OR template frontmatter)
    runnable: bool          # True → `mokata run <name>`; False → own command / auto-fires
    invocation: str         # how to invoke it


def curated_catalog(templates_dir: Path) -> List[CatalogEntry]:
    """The complete curated skill catalog — one entry per name in ``CURATED_SKILLS``, in order.

    The set and the count are single-sourced to ``CURATED_SKILLS``: add a name there and the
    catalog grows automatically. Each summary is single-sourced too — from the ``skills._SKILLS``
    registry for a runnable pipeline skill, and from the command template's frontmatter
    ``description`` for a standalone/auto-firing one — so nothing is hand-copied and nothing drifts.
    """
    from .skills import SKILLS
    entries: List[CatalogEntry] = []
    for name in CURATED_SKILLS:
        skill = SKILLS.get(name)
        if skill is not None:
            entries.append(CatalogEntry(name, skill.summary, True, f"mokata run {name}"))
        else:
            src = load_skill_source(name, templates_dir)
            entries.append(CatalogEntry(name, src.description, False, f"/mokata:{name}"))
    return entries


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


def copy_skill_references(src_skills_dir: Path, dst_skills_dir: Path,
                          names: Optional[tuple] = None) -> List[Path]:
    """Copy each curated skill's progressive-disclosure ``references/`` tree from the shipped
    package skills dir to a setup target, so the pip/setup install path delivers the same JIT
    detail files the plugin install ships. Skills without a ``references/`` dir are skipped.
    Returns the written destination paths. Degrade-clean: a missing source is simply skipped."""
    import shutil
    chosen = names if names is not None else CURATED_SKILLS
    written: List[Path] = []
    src_skills_dir = Path(src_skills_dir)
    dst_skills_dir = Path(dst_skills_dir)
    for name in chosen:
        src_refs = src_skills_dir / name / "references"
        if not src_refs.is_dir():
            continue
        for src in sorted(src_refs.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(src_skills_dir)
            dst = dst_skills_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            written.append(dst)
    return written


def installed_skill_names() -> tuple:
    """Every mokata-authored skill dir a setup/regen KEEPS on disk: the curated pipeline skills
    (template-rendered) PLUS the shipped domain skills (hand-authored, DK.S1+). The prune keep-set
    and orphan detection use THIS so a domain skill (marker-bearing, but not in ``CURATED_SKILLS``)
    is never mistaken for an orphan and deleted. Template GENERATION still uses ``CURATED_SKILLS``
    only — domain skills are copied, not rendered."""
    from .domains import shipped_domain_skills
    return tuple(CURATED_SKILLS) + shipped_domain_skills()


def install_domain_skills(src_skills_dir: Path, dst_skills_dir: Path,
                          names: Optional[tuple] = None) -> List[Path]:
    """Deliver each shipped DOMAIN skill's static ``SKILL.md`` + progressive-disclosure
    ``references/`` tree from the package skills dir to a setup target. Domain skills are
    hand-authored clean-room content (not rendered from a command template), so they are COPIED
    verbatim — the twin of :func:`generate_skill_files`+:func:`write_skill_files` for the pipeline
    skills. Degrade-clean: a missing source dir is skipped. Returns the written destination paths."""
    import shutil
    from .domains import shipped_domain_skills
    chosen = names if names is not None else shipped_domain_skills()
    src_skills_dir = Path(src_skills_dir)
    dst_skills_dir = Path(dst_skills_dir)
    written: List[Path] = []
    for name in chosen:
        src_dir = src_skills_dir / name
        if not (src_dir / "SKILL.md").is_file():
            continue
        for src in sorted(src_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(src_skills_dir)
            dst = dst_skills_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
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
    import shutil
    removed: List[Path] = []
    for sk in find_orphan_skills(skills_dir, keep):
        try:
            sk.unlink()
            removed.append(sk)
            # SK.S2 — a pruned mokata skill may carry a progressive-disclosure references/ tree;
            # remove it too so the marker-bearing skill leaves NO residue (the dir stays empty
            # only for skills without references). The skill is already confirmed mokata-authored.
            refs = sk.parent / "references"
            if refs.is_dir():
                shutil.rmtree(refs, ignore_errors=True)
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
    # Domain skills (DK.S1+) are hand-authored static content already on disk under skills/; the
    # regen must KEEP them (never render/prune them away), so the keep-set is curated + domain.
    prune_orphan_skills(skills_dir, installed_skill_names())
    return written


if __name__ == "__main__":                              # pragma: no cover
    for _p in _regenerate_plugin_skills():
        print(_p)
