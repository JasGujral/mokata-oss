"""A1 — `/mokata:menu`: the one-screen command palette.

Enumerate EVERY shipped `/mokata:` command and every bundled skill from their ACTUAL files —
the command templates (`templates/commands/*.md`: `name` + `description` from frontmatter, and
"gated" = a `## Gate` section exists) and the skills (`skills/*/SKILL.md`) — so a user sees the
whole surface at a glance. SINGLE SOURCE, no drift: adding or removing a command/skill file
changes the menu with no code edit (the Stage 57 lesson); there is no hand-maintained list.

Read-only: it reads files and formats them through the A4 legibility helpers
(`box`/`table` + the `_color_enabled` colour gate) — it never writes state. Degrade-clean:
colour + a Unicode box only on a real TTY (NO_COLOR unset); a piped / redirected / NO_COLOR
run renders a plain-ASCII palette with zero escape codes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import package_data_root
from .agent_skills import parse_frontmatter
from .legibility import _color_enabled, box, green, table

_GATE_HEADING = re.compile(r"(?m)^## Gate")


@dataclass(frozen=True)
class MenuEntry:
    name: str
    description: str
    gated: bool          # a `## Gate` section is present in the source file


def _has_gate(md: str) -> bool:
    """True when the template/skill body declares a `## Gate` section (the gate convention)."""
    return _GATE_HEADING.search(md) is not None


def _short_desc(description: str) -> str:
    """The one-line palette description: drop the redundant `mokata · ` prefix the command/skill
    frontmatter carries (every line already sits under a `mokata` header). Purely cosmetic — the
    text is still the file's own `description`, never a rewrite."""
    return description[len("mokata · "):] if description.startswith("mokata · ") else description


def _entry_from_md(default_name: str, md: str) -> MenuEntry:
    fm = parse_frontmatter(md)
    return MenuEntry(name=fm.get("name", default_name).strip() or default_name,
                     description=fm.get("description", "").strip(), gated=_has_gate(md))


def _commands_dir(root: Optional[Path] = None) -> Path:
    return (root or package_data_root()) / "templates" / "commands"


def _skills_dir(root: Optional[Path] = None) -> Path:
    return (root or package_data_root()) / "skills"


def list_commands(root: Optional[Path] = None) -> List[MenuEntry]:
    """Every `/mokata:` command, derived from `templates/commands/*.md` (sorted, deterministic)."""
    d = _commands_dir(root)
    if not d.is_dir():
        return []
    out: List[MenuEntry] = []
    for p in sorted(d.glob("*.md")):
        out.append(_entry_from_md(p.stem, p.read_text(encoding="utf-8")))
    return out


def list_skills(root: Optional[Path] = None) -> List[MenuEntry]:
    """Every bundled skill, derived from `skills/*/SKILL.md` (sorted, deterministic)."""
    d = _skills_dir(root)
    if not d.is_dir():
        return []
    out: List[MenuEntry] = []
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        skill_md = sub / "SKILL.md"
        if skill_md.is_file():
            out.append(_entry_from_md(sub.name, skill_md.read_text(encoding="utf-8")))
    return out


def _rows(entries: List[MenuEntry], mark: str, color: bool):
    """Table rows `(name, gate-marker | "", short-description)`. The gate marker is green when
    coloured — the padding stays aligned because legibility.table measures visible width."""
    return [(e.name, green(mark, color=color) if e.gated else "", _short_desc(e.description))
            for e in entries]


def render_menu(root: Optional[Path] = None, *, ascii_only: Optional[bool] = None,
                color: Optional[bool] = None, stream=None) -> str:
    """The one-screen palette: a header box + a Commands table + a Skills table, each row showing
    the name, a gate marker (gated only), and a one-line description. Rendered THROUGH the A4
    `box`/`table` helpers. Colour + Unicode box only when `_color_enabled()` (a real TTY, NO_COLOR
    unset, not ascii); otherwise plain ASCII, zero escapes. `ascii_only`/`color`/`stream` are
    overridable for tests; by default both are derived from the single colour gate."""
    if color is None:
        color = _color_enabled(ascii_only=bool(ascii_only), stream=stream)
    if ascii_only is None:
        ascii_only = not color

    commands = list_commands(root)
    skills = list_skills(root)
    mark = "[gate]" if ascii_only else "✓"

    header = box(
        [f"mokata · command palette — {len(commands)} commands, {len(skills)} skills",
         f"every /mokata: command and bundled skill; {mark} = has a gate"],
        ascii_only=ascii_only,
    )
    cmd_table = table(("command", "gate", "description"),
                      _rows(commands, mark, color), ascii_only=ascii_only)
    skill_table = table(("skill", "gate", "description"),
                        _rows(skills, mark, color), ascii_only=ascii_only)
    return "\n".join([
        header,
        "",
        "Commands  (run as /mokata:<name>, or `mokata <name>`):",
        cmd_table,
        "",
        "Skills  (Claude auto-engages these when the moment fits):",
        skill_table,
    ])
