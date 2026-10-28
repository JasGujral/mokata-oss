"""`GET /api/palette/commands` — every `/mokata:*` command and skill the shipped plugin carries, for
the IDE's universal palette (0.0.26 P3, `ID-PALETTE-FULL`).

SINGLE SOURCE: the list is `mokata.menu`'s — read from the very directories `.claude-plugin/
plugin.json` ships (`templates/commands/*.md` and `skills/*/SKILL.md`), name + description from
their own frontmatter and `gated` from a `## Gate` section. A new command or skill file is in the
palette with no code edit; there is no hand-maintained list.

A light read (≈70 small files, on request only — nothing at idle). It answers the same in SAMPLE
mode: the list is mokata's own surface, not the repository's. Selecting one in the page COPIES the
slash command for the human to run in their Claude Code session — the IDE never types into a
session on its own (P2): `/api/context` carries references, not instructions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def slash_commands(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """[{name, slash, description, gated, kind: "command"|"skill"}] — commands first, then the
    skills not already listed as a command (a skill and a command may share a name, e.g. `spec`;
    the palette shows it once, as the command, which is what the human types). `root` = the
    package data directory (tests point it at a copy)."""
    from ...menu import _short_desc, list_commands, list_skills

    out: List[Dict[str, Any]] = []
    seen = set()
    for kind, entries in (("command", list_commands(root)), ("skill", list_skills(root))):
        for e in entries:
            if e.name in seen:
                continue
            seen.add(e.name)
            out.append({"name": e.name, "slash": "/mokata:" + e.name,
                        "description": _short_desc(e.description), "gated": e.gated,
                        "kind": kind})
    return out


def routes(reg, ctx) -> None:
    def commands(_request) -> Dict[str, Any]:
        rows = slash_commands()
        return {"commands": rows, "count": len(rows),
                "source": "the shipped plugin: templates/commands/*.md + skills/*/SKILL.md",
                "how": "paste it into your Claude Code session's prompt (the IDE copies it; it "
                       "never sends a command to a session by itself)",
                "joined": ctx.state.joined}

    reg.register("/api/palette/commands", commands, kind="json", feature="palette")
