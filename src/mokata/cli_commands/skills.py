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
    render_skill,
)


def _templates_dir():
    """The command-template dir — the single source for a standalone skill's frontmatter."""
    from .. import package_data_root
    return package_data_root() / "templates" / "commands"


def _catalog() -> List:
    """CAT.S1 — the COMPLETE curated catalog (all of CURATED_SKILLS), single-sourced."""
    from ..agent_skills import curated_catalog
    return curated_catalog(_templates_dir())


def _search_skills(query: str) -> List[tuple]:
    """Filter the COMPLETE curated catalog by keyword over name + one-line summary (Stage 70,
    CAT.S1 — now the full 16, not just the runnable 12). Returns `(name, summary)` tuples (a
    stable contract other callers unpack). Read-only, deterministic (catalog order preserved)."""
    q = query.lower()
    return [(e.name, e.summary) for e in _catalog()
            if q in e.name.lower() or q in e.summary.lower()]


def _utility_runnable_names() -> List[str]:
    """Runnable skills (`mokata run <name>`) that are NOT in the curated catalog — pure utilities
    (e.g. `version`). Derived from the two single sources, never hardcoded, so nothing is silently
    dropped from the list even though they sit outside the curated set."""
    from ..agent_skills import CURATED_SKILLS
    return [n for n in SKILL_NAMES if n not in set(CURATED_SKILLS)]


def _skills_detail_non_runnable(name: str) -> int:
    """Detail for a curated skill that is standalone/auto-firing (not in `_SKILLS`): show its
    SKILL.md-derived summary + how to invoke, rather than erroring."""
    from ..agent_skills import load_skill_source, SkillSourceError
    try:
        src = load_skill_source(name, _templates_dir())
    except SkillSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"/{src.name} — {src.description}")
    print(f"  invoke: /mokata:{src.name} "
          f"(auto-fires / own command — not `mokata run`)")
    if src.when_to_use:
        print(f"  when to use: {src.when_to_use}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    # L4 — catalog with progressive disclosure: bare list is cheap; a name reveals detail.
    # Stage 70 — a discoverable skill catalog: `mokata skills search <query>` filters by keyword.
    # CAT.S1 — the bare list is the COMPLETE curated catalog (all 16), grouped so the user can
    # tell the runnable pipeline skills from the auto-firing / own-command ones.
    from ..agent_skills import CURATED_SKILLS  # noqa: F401  (single source for the count note)
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
            print(f"  /{name:11} {summary}")
        return 0
    if args.name:
        try:
            skill = get_skill(args.name)
        except SkillNotFound:
            # Not a runnable `_SKILLS` skill — it may still be a curated standalone/auto-firing
            # skill (govern/session/playbook/mcp-repair/docsync). Show its detail, not an error.
            if args.name in set(CURATED_SKILLS):
                return _skills_detail_non_runnable(args.name)
            print(f"error: no skill '{args.name}'; available: "
                  f"{', '.join(e.name for e in _catalog())}", file=sys.stderr)
            return 1
        print(f"/{skill.name} — {skill.summary}")
        print(f"  gate: {skill.gate.id} ({skill.gate.kind}) — {skill.gate.description}")
        if skill.phase:
            print(f"  pipeline phase: {skill.phase}")
        if skill.scaffold:
            print("  (scaffold — deeper engine in a later stage)")
        print(f"  invoke: mokata run {skill.name}")
        print("\n" + skill.prompt)
        return 0

    catalog = _catalog()
    runnable = [e for e in catalog if e.runnable]
    standalone = [e for e in catalog if not e.runnable]
    print(f"mokata skills — the curated catalog ({len(catalog)} skills; "
          f"run `mokata skills <name>` for detail):")
    print("\nRunnable pipeline skills (run `mokata run <name>` or `/mokata:<name>`):")
    for e in runnable:
        print(f"  /{e.name:11} {e.summary}")
    print("\nStandalone / auto-firing skills (their own command or fire on their own — "
          "not `mokata run`):")
    for e in standalone:
        print(f"  /{e.name:11} {e.summary}")
    utility = _utility_runnable_names()
    if utility:
        print("\nUtility skills (runnable, outside the curated catalog): "
              + ", ".join(f"/{n}" for n in utility))
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
    # SK.S2 single-source: expand the grounding marker so a self-authored skill is written
    # self-contained (the marker never reaches a user's command file unexpanded).
    from ..skills import expand_grounding
    rendered = expand_grounding(command_markdown(skill))
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
        # CAT.S1 — a curated but NON-runnable skill (govern/session/playbook/mcp-repair/docsync)
        # is listed by `mokata skills` but isn't a `mokata run` target; point at how to invoke it
        # rather than the generic "no skill" error.
        from ..agent_skills import CURATED_SKILLS
        if args.name in set(CURATED_SKILLS):
            print(f"'{args.name}' isn't runnable via `mokata run` — it's an auto-firing / "
                  f"own-command skill. Invoke it with `/mokata:{args.name}` "
                  f"(or `mokata skills {args.name}` for detail).", file=sys.stderr)
            return 2
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
    # No argparse `choices` here: a curated but non-runnable skill (govern/session/…) must reach
    # cmd_run so it gets a CLEAR message pointing at `/mokata:<name>` (CAT.S1), not an argparse
    # "invalid choice" crash. cmd_run validates the name against the runnable registry itself.
    p_run.add_argument("name", metavar="name",
                       help=f"the skill to run (one of: {', '.join(SKILL_NAMES)})")
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
