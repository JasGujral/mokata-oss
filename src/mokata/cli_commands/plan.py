"""plan — the saved brainstorm PLAN FILES (Stage 6p): list / show / export.

The brainstorm phase saves the approved design to `.mokata/temp_local/plans/<slug>.md` at
approval. This command group is the shell surface over those internal plans:
  * `plan list`             — the saved internal plans;
  * `plan show [<slug>]`    — print one (defaults to the sole plan);
  * `plan export [<slug>] [--to <dir>] [--force]` — copy the internal plan into a PROJECT-VISIBLE
    `plans/<slug>.md` (committable). User-initiated durable write — no gate, but never a silent
    clobber (reports + needs --force to overwrite an existing file).
"""
from __future__ import annotations

import argparse
import os
import sys

from ._common import _load_surface
from .. import plans as P


def cmd_plan(args: argparse.Namespace) -> int:
    action = getattr(args, "action", None) or "list"
    if action == "list":
        return _cmd_plan_list(args)
    if action == "show":
        return _cmd_plan_show(args)
    if action == "export":
        return _cmd_plan_export(args)
    print(f"plan: unknown action '{action}' (use list | show | export).", file=sys.stderr)
    return 2


def _cmd_plan_list(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    slugs = P.list_plans(surface.plans_dir)
    if not slugs:
        print("plan: no saved plans yet — approve a brainstorm approach to save one "
              "(.mokata/temp_local/plans/<slug>.md).")
        return 0
    print(f"plan: {len(slugs)} saved plan(s) in .mokata/temp_local/plans/")
    for slug in slugs:
        print(f"  {slug}")
    print("  show one with `mokata plan show <slug>`; keep an editable repo copy with "
          "`mokata plan export <slug>`.")
    return 0


def _cmd_plan_show(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    try:
        slug = P.resolve_slug(surface.plans_dir, args.slug)
    except P.PlanError as exc:
        print(f"plan show: {exc}", file=sys.stderr)
        return 1
    content = P.read_plan(surface.plans_dir, slug)
    if content is None:
        print(f"plan show: no saved plan '{slug}'.", file=sys.stderr)
        return 1
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_plan_export(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    # Default destination: `plans/` at the repo root (committable copy). An explicit --to is used
    # as given (relative to the invoking cwd).
    dest_dir = args.to or os.path.join(surface.root, P.PLANS_DIRNAME)
    try:
        slug = P.resolve_slug(surface.plans_dir, args.slug)
        res = P.export_plan(surface.plans_dir, dest_dir, slug, force=args.force)
    except P.PlanError as exc:
        print(f"plan export: {exc}", file=sys.stderr)
        return 1
    if not res.written:
        # existing file, no --force → report (to stdout: it's guidance, not a crash), don't clobber.
        print(f"plan export: {res.reason}")
        return 1
    verb = "overwrote" if res.overwritten else "wrote"
    print(f"plan export: {verb} {res.dest} — an editable copy you can commit.")
    return 0


def register(sub, common):
    p_plan = sub.add_parser(
        "plan", parents=[common],
        help="saved brainstorm plan files: list · show · export a committable copy (Stage 6p)",
    )
    p_plan.add_argument(
        "action", nargs="?", default="list", choices=("list", "show", "export"),
        help="list (default): saved internal plans · show [<slug>]: print one · export "
             "[<slug>]: copy it to a project-visible plans/<slug>.md you can commit")
    p_plan.add_argument("slug", nargs="?", default=None,
                        help="which plan (defaults to the sole saved plan)")
    p_plan.add_argument("--to", default=None,
                        help="export destination dir (default: plans/ at the repo root)")
    p_plan.add_argument("--force", action="store_true",
                        help="on export, overwrite an existing project copy (default: refuse "
                             "+ report, never a silent clobber)")
    p_plan.set_defaults(func=cmd_plan)


__all__ = [
    "cmd_plan",
    "_cmd_plan_list",
    "_cmd_plan_show",
    "_cmd_plan_export",
]
