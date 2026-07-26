"""B-SKILLS — the skills-visibility diagnostic.

Live report (Jas 2026-07-17): opening a NEW session on the same repo, the ``/`` command menu does
not show mokata's skills, though the first session did. mokata's Agent Skills + slash commands are
PROJECT-scoped Claude Code surfaces written by ``mokata setup claude`` into
``<root>/.claude/skills/<name>/SKILL.md`` + ``.claude/commands/*.md``. They are read from disk per
session — NOT installed by any hook — so whether the SessionStart hook ran or not cannot make
on-disk skills appear or disappear.

The failure state this names: the CURRENT session's root lacks the curated ``.claude/skills/`` +
commands. That happens for a git WORKTREE of the repo (a separate root; ``mokata setup`` writes to
the literal root it runs in, never the canonical checkout — see ``harness_setup.resolve_targets``
→ ``harness_paths.scope_base``), a fresh/second checkout, or a project-scoped install viewed from a
root that was never set up. Whatever the trigger — and INCLUDING the pure Claude-Code-side case
where the files ARE present but the session cached an older list — mokata cannot install skills into
someone else's session; the fix mokata CAN give is LEGIBILITY: name why the skills aren't visible
and the one command that wires them, with the restart hint every loud finding carries (Claude Code
reads the skill/command list once per session, so a change needs a new session / restart).

Read-only diagnostic (doc 85 §3 ``*Finding``): LOUD only when something is wrong (a healthy repo is
QUIET on the write paths), and it NEVER raises — a weird / here-be-dragons root degrades to a single
"could not check" line, never a crash. Mirrors the B-VER version-parity finding shape
(``mcp_admin.VersionParityFinding`` / ``parity_lines``) so the two read alike.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def _curated_command_names() -> List[str]:
    """The native command filenames ``mokata setup claude`` writes into ``.claude/commands/`` —
    the shipped ``templates/commands/*.md`` basenames (claude's native format keeps the ``.md``
    name). Single-sourced to the shipped templates so the check can't drift from what setup writes.
    Degrade-clean: a missing templates dir yields an empty list (nothing to check)."""
    from . import package_data_root
    tdir = package_data_root() / "templates" / "commands"
    if not tdir.is_dir():
        return []
    return sorted(p.name for p in tdir.glob("*.md"))


def _present_missing_skills(skills_dir: Optional[Path], names) -> tuple:
    """`(present_count, missing_names)` for the curated Agent Skills at ``<skills_dir>/<name>/
    SKILL.md``. A None dir (harness with no skills surface) means every skill is missing."""
    if skills_dir is None:
        return 0, list(names)
    d = Path(skills_dir)
    present, missing = 0, []
    for name in names:
        if (d / name / "SKILL.md").is_file():
            present += 1
        else:
            missing.append(name)
    return present, missing


def _present_missing_commands(commands_dir: Optional[Path], names) -> tuple:
    """`(present_count, missing_names)` for the slash commands at ``<commands_dir>/<name>``."""
    if commands_dir is None:
        return 0, list(names)
    d = Path(commands_dir)
    present, missing = 0, []
    for name in names:
        if (d / name).is_file():
            present += 1
        else:
            missing.append(name)
    return present, missing


@dataclass
class SkillsVisibilityFinding:
    """The skills-visibility verdict for one root. ``status`` is one of ``present`` (quiet pass) ·
    ``missing`` (nothing wired here) · ``partial`` (an incomplete/stale set) · ``uncheckable``
    (the check itself could not run — degrade-clean, never a crash). A read-only diagnostic
    (doc 85 §3 ``*Finding``); ``render`` is the loud printable report + the restart hint."""

    status: str
    root: str = ""
    scope: str = "project"                # where a PRESENT set was found ("project" | "user")
    where_dir: Optional[Path] = None      # the dir a present set was found in (for the OK line)
    skills_dir: Optional[Path] = None
    commands_dir: Optional[Path] = None
    present_skills: int = 0
    total_skills: int = 0
    missing_skills: List[str] = field(default_factory=list)
    present_commands: int = 0
    total_commands: int = 0
    missing_commands: List[str] = field(default_factory=list)
    is_worktree: bool = False
    worktree_label: str = "main"
    plugin_present: bool = False
    plugin_root: Optional[Path] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "present"

    def render(self, quiet_when_ok: bool = True) -> List[str]:
        """The loud, human report. LOUD only when something is wrong; a clean PRESENT is silent on
        the write paths (``quiet_when_ok=True``) and a one-line OK + restart hint on the status/
        doctor surfaces (``quiet_when_ok=False``). The restart hint rides EVERY loud branch AND the
        OK line — it is the single answer to the pure Claude-Code-caching case (files present, old
        session): open a new session / restart Claude Code."""
        restart = ("restart Claude Code (it reads the skill/command list once per session, so a "
                   "new session picks up the change)")

        if self.status == "uncheckable":
            return [f"mokata skills: could not check visibility ({self.detail}) — if the `/` menu "
                    "is missing mokata's skills, run `mokata setup claude` in this repo."]

        if self.status == "present":
            if quiet_when_ok:
                return []
            lines = [f"mokata skills: visible ✓ — {self.present_skills} Agent Skills + "
                     f"{self.present_commands} commands wired ({self.scope} scope: {self.where_dir})."]
            lines.append(f"  If the `/` menu still doesn't list them, {restart}.")
            if self.plugin_present:
                lines.append(f"  Note: the mokata plugin is also installed ({self.plugin_root}) — "
                             "Claude Code may list each skill twice (`mokata:<name>` + `<name>`). "
                             "That's expected; reliably-current beats deduped-but-stale.")
            return lines

        # LOUD — missing / partial
        lines: List[str] = []
        if self.status == "missing":
            if self.is_worktree:
                lines.append(
                    "mokata skills: NOT VISIBLE ✗ — this root is a git worktree "
                    f"('{self.worktree_label}'), and `mokata setup claude` writes the skills + "
                    "commands into the root it runs in, not the main checkout, so none are wired "
                    "here.")
                lines.append(f"  expected at {self.skills_dir} — 0/{self.total_skills} skills, "
                             f"0/{self.total_commands} commands found.")
            else:
                lines.append(
                    "mokata skills: NOT VISIBLE ✗ — no mokata skills or commands are wired in this "
                    "root (a fresh/second checkout, or `mokata setup claude` was never run here).")
                lines.append(f"  expected {self.total_skills} skills at {self.skills_dir} and "
                             f"{self.total_commands} commands at {self.commands_dir} — none found.")
            lines.append(f"  Fix: run `mokata setup claude` in this repo, then {restart}.")
        else:  # partial
            lines.append(
                "mokata skills: PARTIAL/STALE ✗ — this root has an INCOMPLETE mokata skill set "
                "(an old or interrupted install).")
            if self.missing_skills:
                lines.append(f"  {self.present_skills}/{self.total_skills} skills present; "
                             f"missing: {', '.join(self.missing_skills)}")
            if self.missing_commands:
                lines.append(f"  {self.present_commands}/{self.total_commands} commands present; "
                             f"missing: {', '.join(self.missing_commands)}")
            lines.append(f"  Fix: re-run `mokata setup claude` (it refreshes the set), then "
                         f"{restart}.")
        if self.plugin_present:
            lines.append(
                f"  Also: the mokata plugin is installed ({self.plugin_root}) — it provides its OWN "
                "`mokata:<name>` skills that can go stale independently of pip; update it with "
                "`/plugin marketplace update` if you rely on it.")
        return lines


def _plugin_shadow(home: Optional[str]):
    """The B-VER plugin sweep, reused: a PluginShadowFinding when a mokata plugin is detectable,
    else None. Never raises."""
    try:
        from .mcp_admin import plugin_shadow
        return plugin_shadow(home=home)
    except Exception:
        return None


def skills_visibility(root: str = ".", home: Optional[str] = None) -> SkillsVisibilityFinding:
    """Build the skills-visibility finding for ``root``. Checks the curated Agent Skills
    (``installed_skill_names()``: pipeline + domain) and slash commands against the project-scope
    ``.claude/`` surface, falling back to the user scope so a ``--scope user`` install still reads
    as PRESENT. Classifies present / missing / partial and annotates worktree + plugin context.

    NEVER raises — any failure degrades to an ``uncheckable`` finding, never a crash (the doctor
    must survive a weird root)."""
    try:
        from .agent_skills import installed_skill_names
        from .harness_setup import resolve_targets
        from .repo_identity import worktree_label

        skill_names = list(installed_skill_names())
        cmd_names = _curated_command_names()
        total_skills, total_commands = len(skill_names), len(cmd_names)

        proj = resolve_targets("project", root, home, "claude")
        ps_present, ps_missing = _present_missing_skills(proj.skills_dir, skill_names)
        pc_present, pc_missing = _present_missing_commands(proj.commands_dir, cmd_names)

        label = worktree_label(root)
        is_wt = label != "main"
        shadow = _plugin_shadow(home)
        plugin_present = shadow is not None
        plugin_root = getattr(shadow, "plugin_root", None)

        base = dict(
            root=str(root), skills_dir=proj.skills_dir, commands_dir=proj.commands_dir,
            present_skills=ps_present, total_skills=total_skills, missing_skills=ps_missing,
            present_commands=pc_present, total_commands=total_commands, missing_commands=pc_missing,
            is_worktree=is_wt, worktree_label=label,
            plugin_present=plugin_present, plugin_root=plugin_root,
        )

        # All curated skills AND commands present here → visible via the project scope.
        if not ps_missing and not pc_missing and (total_skills or total_commands):
            return SkillsVisibilityFinding(status="present", scope="project",
                                           where_dir=proj.skills_dir, **base)

        # Nothing at all at the project scope → the user scope may still cover it globally.
        if ps_present == 0 and pc_present == 0:
            user = resolve_targets("user", root, home, "claude")
            us_present, us_missing = _present_missing_skills(user.skills_dir, skill_names)
            uc_present, uc_missing = _present_missing_commands(user.commands_dir, cmd_names)
            if not us_missing and not uc_missing and (total_skills or total_commands):
                return SkillsVisibilityFinding(
                    status="present", scope="user", where_dir=user.skills_dir,
                    present_skills=us_present, present_commands=uc_present, **{
                        k: v for k, v in base.items()
                        if k not in ("present_skills", "present_commands")})
            return SkillsVisibilityFinding(status="missing", **base)

        # Some present, some missing → an incomplete / stale install.
        return SkillsVisibilityFinding(status="partial", **base)
    except Exception as exc:                 # read-only diagnostic — NEVER raises
        return SkillsVisibilityFinding(status="uncheckable", root=str(root),
                                       detail=f"{type(exc).__name__}: {exc}")


def skills_visibility_lines(root: str = ".", home: Optional[str] = None, *,
                            quiet_when_ok: bool = True) -> List[str]:
    """The shared skills-visibility report block — the SAME finding rendered for ``doctor`` (and any
    other surface). Loud-only by default; the doctor passes ``quiet_when_ok=False`` to also show the
    OK + restart line. Never raises."""
    try:
        return skills_visibility(root, home).render(quiet_when_ok=quiet_when_ok)
    except Exception as exc:                 # informational path — never raise
        return [f"mokata skills: visibility check skipped ({exc})."]


def briefing_offer(root: str = ".", home: Optional[str] = None) -> Optional[str]:
    """A SHORT one-line offer for the SessionStart briefing (budget-conscious), or None when the
    skills ARE visible here. This is the WT.S1 detect-and-OFFER pattern applied to the new-session
    repro: a session that opens on a root without the wiring SAYS why the `/` menu is empty and
    names the one command that fixes it — human-gated (it only offers ``mokata setup claude``,
    never writes). Never raises."""
    try:
        f = skills_visibility(root, home)
    except Exception:
        return None
    if f.status not in ("missing", "partial"):
        return None
    if f.status == "missing" and f.is_worktree:
        why = f"this worktree ('{f.worktree_label}') has none of mokata's skills/commands wired"
    elif f.status == "missing":
        why = "mokata's skills/commands aren't wired in this root"
    else:
        why = "mokata's skills/commands are only partly wired here (stale/incomplete)"
    return (f"⚠ mokata: the `/` menu won't show mokata's skills — {why}. "
            "Run `mokata setup claude` here, then restart Claude Code.")
