"""Stage 6p — the brainstorm PLAN FILE: durable, reviewable plan artifacts.

The brainstorm phase already produces a digestible design write-up (`design_writeup()`) and an
approved-approach hand-off in run-state. Stage 6p also saves that write-up as a FILE at approval —
so the design is a durable, editable artifact, not only chat + run-state.

Two locations, by intent:
  * `.mokata/temp_local/plans/<slug>.md` — the INTERNAL working copy, written as a byproduct of
    the user's own approval (see `brainstorm.save_approach_plan`). It's internal runtime data, so
    it lives under the gitignored `temp_local/` (Stage 24D), the same split as the memory store and
    audit ledger. mokata owns it.
  * `plans/<slug>.md` (project root) — the EXPORTED, committable copy the user keeps in their repo.
    Produced by the user-initiated `mokata plan export`.

This module is the pure, dependency-free file layer: slugging, listing, reading, and exporting.
It imports nothing from `brainstorm` (the dependency runs the other way), so it stays a small,
testable utility. Degrade-clean throughout — a missing dir is "no plans", not an error.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional
from .errors import MokataError

# Dir name shared by the internal working area (under `.mokata/temp_local/`) and the exported,
# committable copy (project root).
PLANS_DIRNAME = "plans"

# A slug is bounded so a long topic can't produce an unwieldy filename.
_MAX_SLUG = 60


class PlanError(MokataError):
    """A plan-file operation could not be completed (e.g. exporting a plan that isn't there)."""


def plan_slug(topic: str) -> str:
    """A safe, deterministic filename stem for a brainstorm topic.

    Lowercased, non-alphanumerics collapsed to single hyphens, trimmed, and length-bounded.
    Never empty — an all-punctuation topic degrades to `plan`."""
    s = re.sub(r"[^a-z0-9]+", "-", str(topic or "").lower()).strip("-")
    if len(s) > _MAX_SLUG:
        s = s[:_MAX_SLUG].rstrip("-")
    return s or "plan"


def plan_path(plans_dir: str, slug: str) -> str:
    return os.path.join(plans_dir, f"{slug}.md")


def write_plan_file(plans_dir: str, slug: str, content: str) -> Optional[str]:
    """Write `content` to `<plans_dir>/<slug>.md`, creating the dir. Returns the path, or None
    on any filesystem failure — the caller decides whether that's fatal (at approval it is NOT:
    the run-state hand-off remains the source of truth, so a plan-file miss must never break it)."""
    try:
        os.makedirs(plans_dir, exist_ok=True)
        path = plan_path(plans_dir, slug)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path
    except OSError:
        return None


def list_plans(plans_dir: str) -> List[str]:
    """The saved plan slugs (sorted), or `[]` when the dir is absent/unreadable (degrade-clean)."""
    try:
        names = os.listdir(plans_dir)
    except OSError:
        return []
    return sorted(n[:-3] for n in names if n.endswith(".md"))


def read_plan(plans_dir: str, slug: str) -> Optional[str]:
    """The saved plan's markdown, or None if there is no such plan (or it can't be read)."""
    try:
        with open(plan_path(plans_dir, slug), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


@dataclass
class PlanExport:
    """The outcome of exporting an internal plan to the project-visible `plans/` copy."""

    slug: str
    src: str
    dest: str
    written: bool
    existed: bool          # the destination already existed before this call
    reason: str = ""

    @property
    def overwritten(self) -> bool:
        return self.written and self.existed


def export_plan(plans_dir: str, dest_dir: str, slug: str, force: bool = False) -> PlanExport:
    """Copy the internal `<plans_dir>/<slug>.md` to `<dest_dir>/<slug>.md` (the committable copy).

    User-INITIATED (the user runs `mokata plan export`), so no separate gate — but it must never
    silently clobber the user's edits: when the destination already exists and `force` is not set,
    it reports (`written=False`) and leaves the file untouched. `force=True` overwrites."""
    src = plan_path(plans_dir, slug)
    if not os.path.exists(src):
        raise PlanError(f"no saved plan '{slug}' in {plans_dir}")
    dest = plan_path(dest_dir, slug)
    existed = os.path.exists(dest)
    if existed and not force:
        return PlanExport(slug, src, dest, written=False, existed=True,
                          reason=f"{dest} already exists — pass --force to overwrite it")
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copyfile(src, dest)
    return PlanExport(slug, src, dest, written=True, existed=existed,
                      reason="overwrote the existing copy" if existed else "created")


def resolve_slug(plans_dir: str, slug: Optional[str]) -> str:
    """Resolve the target slug for show/export: the given one, or — when omitted — the sole saved
    plan. Raises `PlanError` (with guidance) when there is none or the choice is ambiguous."""
    if slug:
        return slug
    saved = list_plans(plans_dir)
    if not saved:
        raise PlanError("no saved plans yet")
    if len(saved) > 1:
        raise PlanError(f"several plans saved — name one of: {', '.join(saved)}")
    return saved[0]
