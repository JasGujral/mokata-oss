"""skills / skill / run / enter — the skill catalog, authoring, and standalone or pipeline-entry execution."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List

from ._common import (
    ground,
    ConfigError,
    Surface,
    MOKATA_DIR,
    PIPELINE_PHASES,
    ManifestError,
    ENTRY_PHASES,
    PhaseError,
    plan_entry,
    render_entry,
    SKILL_NAMES,
    SkillNotFound,
    get_skill,
    list_skills,
    render_skill,
)


def _search_skills(query: str) -> List[tuple]:
    """Filter the skill catalog by keyword over name + one-line summary (Stage 70). Read-only,
    deterministic (catalog order preserved)."""
    q = query.lower()
    return [(n, s) for n, s in list_skills() if q in n.lower() or q in s.lower()]


def cmd_skills(args: argparse.Namespace) -> int:
    # L4 — catalog with progressive disclosure: bare list is cheap; a name reveals detail.
    # Stage 70 — a discoverable skill catalog: `mokata skills search <query>` filters by keyword.
    if args.name == "search":
        query = getattr(args, "query", None)
        if not query:
            print("error: `skills search <query>` requires a query", file=sys.stderr)
            return 2
        hits = _search_skills(query)
        if not hits:
            print(f"skills: no matches for {query!r}")
            return 0
        print(f"skills: {len(hits)} match(es) for {query!r}:")
        for name, summary in hits:
            print(f"  /{name:10} {summary}")
        return 0
    if args.name:
        try:
            skill = get_skill(args.name)
        except SkillNotFound as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"/{skill.name} — {skill.summary}")
        print(f"  gate: {skill.gate.id} ({skill.gate.kind}) — {skill.gate.description}")
        if skill.phase:
            print(f"  pipeline phase: {skill.phase}")
        if skill.scaffold:
            print("  (scaffold — deeper engine in a later stage)")
        print("\n" + skill.prompt)
        return 0
    print("mokata skills (run `mokata skills <name>` for detail):")
    for name, summary in list_skills():
        print(f"  /{name:10} {summary}")
    print("\nAuthor your own (G6, RED-GREEN-for-docs; human-gated write):")
    print("  mokata skill author <name> --summary <s> --require <doc>:<must-contain> "
          "--content-file <f>")
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    if args.action == "author":
        return _skill_author(args)
    print(f"error: unknown skill action '{args.action}'", file=sys.stderr)
    return 2


def _skill_author(args: argparse.Namespace) -> int:
    # G6 — draft a skill test-first (declare doc requirements -> content must satisfy them:
    # RED before GREEN), then HUMAN-GATE the write of the rendered command template. A RED
    # draft writes nothing; degrade-clean.
    from ..govern import AuditLedger, SkillDraft, WriteGate, WriteRequest
    from ..skills import Gate, command_markdown
    draft = SkillDraft(args.name)
    for spec in (args.require or []):
        rname, sep, must = spec.partition(":")
        if not sep or not rname or not must:
            print(f"error: --require must be name:must-contain (got {spec!r})",
                  file=sys.stderr)
            return 2
        draft.require(rname, must)
    if not draft.requirements:
        print("error: declare at least one --require name:must-contain "
              "(the doc tests, RED-GREEN-for-docs)", file=sys.stderr)
        return 2
    if not args.content_file:
        print("error: --content-file <path> is required (the drafted skill content)",
              file=sys.stderr)
        return 2
    try:
        with open(args.content_file, encoding="utf-8") as fh:
            draft.write(fh.read())
    except OSError as exc:
        print(f"error: cannot read {args.content_file}: {exc}", file=sys.stderr)
        return 1

    result = draft.check()
    if not result.passed:
        # RED — report the failing doc requirements and write NOTHING.
        print(f"skill '{args.name}' is RED — doc requirement(s) unmet: "
              f"{', '.join(result.failures)}")
        print("Revise the content until every requirement passes (RED -> GREEN), "
              "then re-run.")
        return 1

    # GREEN — promote to a Skill and human-gate the write of the rendered command.
    gate = Gate(f"{args.name}-approval",
                args.gate_desc or "Human-gated self-authored skill.", "human")
    skill = draft.to_skill(args.summary or f"mokata · {args.name}", gate)
    rendered = command_markdown(skill)
    dest = args.out or os.path.join(args.path, MOKATA_DIR, "skills", f"{args.name}.md")
    ledger = AuditLedger.from_mokata_dir(os.path.join(args.path, MOKATA_DIR))

    def commit() -> None:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(rendered)

    outcome = WriteGate(ledger=ledger).submit(
        WriteRequest("config", dest, content=rendered, actor="cli"),
        commit=commit, assume_yes=args.yes)
    if not outcome.committed:
        print(f"skill author: {outcome.reason} — nothing written.")
        return 1
    print(f"skill '{args.name}' authored (GREEN) and written to {dest}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # L1/L3 — run a skill standalone. No init and no upstream phase are required; if the
    # repo is initialized we add live grounding, otherwise we degrade cleanly.
    try:
        skill = get_skill(args.name)
    except SkillNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    surface = None
    if Surface.is_initialized(args.path):
        try:
            surface = Surface.load(args.path)
        except (ConfigError, ManifestError):
            surface = None

    # Stage 32 — implementation entry points (develop/test) require a persisted, complete
    # spec before code/tests; the block (and pass) is an audited gate decision.
    if skill.requires_spec:
        from ..engine import check_spec_persisted
        from ..govern import AuditLedger
        store = surface.state if surface is not None else None
        ledger = (AuditLedger.from_mokata_dir(surface.mokata_dir)
                  if surface is not None else None)
        res = check_spec_persisted(store, ledger=ledger, phase=skill.name)
        if not res.passed:
            print(f"[BLOCKED] {res.gate_id} — {res.reason}", file=sys.stderr)
            return 1

    grounding = ground(surface.router) if surface is not None else ground(None)
    sys.stdout.write(render_skill(skill, grounding))
    return 0


def cmd_enter(args: argparse.Namespace) -> int:
    # L2 — enter the pipeline at a phase; only the run phases' gates apply.
    try:
        plan = plan_entry(args.phase, stop=args.to)
    except PhaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(render_entry(plan))
    return 0


def register(sub, common):
    p_skills = sub.add_parser(
        "skills", parents=[common],
        help="list the skill/command catalog (add a name for detail)",
    )
    p_skills.add_argument("name", nargs="?",
                          help="reveal detail for one skill (or `search` to filter, Stage 70)")
    p_skills.add_argument("query", nargs="?",
                          help="with `skills search <query>`: filter the catalog by keyword")
    p_skills.set_defaults(func=cmd_skills)

    p_skill = sub.add_parser(
        "skill", parents=[common],
        help="author a new skill (RED-GREEN-for-docs; human-gated write)",
    )
    p_skill.add_argument("action", choices=("author",), help="author a skill")
    p_skill.add_argument("name", help="skill name (the /<name> command)")
    p_skill.add_argument("--summary", default=None, help="one-line catalog summary")
    p_skill.add_argument("--require", action="append", metavar="DOC:MUST-CONTAIN",
                         help="a doc requirement the content must satisfy (repeatable)")
    p_skill.add_argument("--content-file", default=None,
                         help="path to the drafted skill content (markdown)")
    p_skill.add_argument("--gate-desc", default=None,
                         help="the human gate's description for the authored skill")
    p_skill.add_argument("--out", default=None,
                         help="destination (default: .mokata/skills/<name>.md)")
    p_skill.add_argument("--yes", action="store_true",
                         help="approve the human-gated write non-interactively")
    p_skill.set_defaults(func=cmd_skill)

    p_run = sub.add_parser(
        "run", parents=[common],
        help="run a skill/command standalone (no pipeline prerequisite)",
    )
    p_run.add_argument("name", choices=SKILL_NAMES, help="the skill to run")
    p_run.set_defaults(func=cmd_run)

    p_enter = sub.add_parser(
        "enter", parents=[common],
        help="enter the pipeline at a phase (applies only that phase's gates)",
    )
    p_enter.add_argument("phase", choices=ENTRY_PHASES, help="phase to start at")
    p_enter.add_argument("--to", choices=PIPELINE_PHASES, default=None,
                         help="optional phase to stop after (default: just the start)")
    p_enter.set_defaults(func=cmd_enter)


__all__ = [
    "_search_skills",
    "cmd_skills",
    "cmd_skill",
    "_skill_author",
    "cmd_run",
    "cmd_enter",
]
