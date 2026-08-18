"""MIRROR-PIN-COVERS-FOUR-OF-* + MIRROR-ABSENCE-READS-AS-MATCH — grading the mirror chain.

`test_handoff_g1.test_shipped_skill_md_mirrors_are_regenerated` claimed in its docstring to pin
*"the single-source chain: `skills.py` -> `templates/commands/<n>.md` -> `skills/<n>/SKILL.md`"*.
Two defects, one per loop:

    for name in ("review", "develop", "test", "ship"):   <- link 1 graded for FOUR names
        ...                                                 a literal where a derivation belongs
    for name in CURATED_SKILLS:
        if not p.is_file(): continue                     <- an ABSENT mirror scored as a MATCH

⚠ IT IS NOT ONE CHAIN. That docstring is the third defect and the one that produced three wrong
denominators in a row. There are TWO links with DIFFERENT domains, and neither domain is the
other's:

    link 1   registry -> command template     domain = `skills.SKILL_NAMES`     (12)
    link 2   command template -> SKILL.md     domain = `agent_skills.CURATED_SKILLS` (16)

The sets overlap in 11 names and neither contains the other. `version` is a registry entry that is
deliberately NOT model-invocable, so it has a template and must never have a SKILL.md. `docsync`,
`govern`, `mcp-repair`, `playbook` and `session` are curated agent skills with NO registry entry at
all, so `get_skill` raises and **link 1 does not exist for them** — widening link 1 to
`CURATED_SKILLS` reds on five names that have no generator to drift from, which is the category
error the row's "5 ERRORS" measurement was reporting.

⚠⚠ AND LINK 1 IS NOT AN EQUALITY FOR EVERY NAME IN ITS DOMAIN. `brainstorm.md` is 220 lines on
disk against 135 rendered, and the extra 85 are load-bearing: the run-REGISTRATION protocol, the
auto-engage announcement, the declined-permission note. For that skill the TEMPLATE is the source
and the registry entry is the shorter standalone body. "Extended on purpose" and "stale" are
different facts that a byte-comparison cannot tell apart (doc 85 §7g), so the split is DECLARED —
`Skill.hand_authored_template` carries the reason — and this module grades the declaration against
the bytes in BOTH directions:

  * declared generated + template differs  -> DRIFT, the defect the pin exists to catch;
  * declared hand-authored + template IS identical -> the declaration is stale or the template was
    regenerated and its extra content is gone. Silence on that arm would let the very loss this
    pin protects against pass as a pass.

ABSENCE IS NEVER A SKIP, ON EITHER LINK. A missing template and a missing mirror each get their
own disposition and each is an offender. That is the whole of MIRROR-ABSENCE-READS-AS-MATCH: the
old `continue` gave "the mirror is gone" and "the mirror matches" one green.

PURE FUNCTIONS OVER A SUPPLIED CORPUS (doc 85 §7i). The real tree holds no offender — link 1 is
11 generated + 1 declared hand-authored, link 2 is 16/16 — so a grader that walked only the real
tree would pass whether or not it worked. Every function here takes its registry, its templates
dir and its skills dir as arguments; `tests/test_s4_skill_mirrors.py` feeds each disposition a
planted instance.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from pathlib import Path

# --- link 1: registry entry -> templates/commands/<name>.md --------------------------------
L1_GENERATED_MATCH = "L1_GENERATED_MATCH"          # declared generated, bytes agree — healthy
L1_GENERATED_DRIFT = "L1_GENERATED_DRIFT"          # declared generated, bytes differ — OFFENDER
L1_HAND_AUTHORED = "L1_HAND_AUTHORED"              # declared hand-authored, extends it — healthy
L1_HAND_AUTHORED_IDENTICAL = "L1_HAND_AUTHORED_IDENTICAL"   # declared, but nothing extra — OFFENDER
L1_DECLARATION_UNREASONED = "L1_DECLARATION_UNREASONED"     # declared with no reason — OFFENDER
L1_TEMPLATE_MISSING = "L1_TEMPLATE_MISSING"        # no template on disk — OFFENDER, never a skip
L1_NOT_IN_REGISTRY = "L1_NOT_IN_REGISTRY"          # no `_SKILLS` entry — link 1 does not exist

# --- link 2: templates/commands/<name>.md -> skills/<name>/SKILL.md -------------------------
L2_MATCH = "L2_MATCH"                              # shipped mirror == rendered — healthy
L2_DRIFT = "L2_DRIFT"                              # shipped mirror != rendered — OFFENDER
L2_MIRROR_MISSING = "L2_MIRROR_MISSING"            # no SKILL.md — OFFENDER (the named defect)
L2_TEMPLATE_MISSING = "L2_TEMPLATE_MISSING"        # cannot render at all — OFFENDER, and NOT the
                                                   # same fact as a missing mirror (§7g)
L2_NOT_CURATED = "L2_NOT_CURATED"                  # not an agent skill; must have NO mirror

# The dispositions a healthy tree is allowed to hold. Everything else is an offender. Stated as
# data so a test can assert the split rather than re-listing it, and so a mutant that moves a
# disposition across the line has something to move it in.
L1_HEALTHY = frozenset({L1_GENERATED_MATCH, L1_HAND_AUTHORED, L1_NOT_IN_REGISTRY})
L2_HEALTHY = frozenset({L2_MATCH, L2_NOT_CURATED})


def grade_link1(registry, templates_dir, names) -> dict:
    """Grade `registry -> templates/commands/<name>.md` for each of `names`.

    `registry` maps name -> Skill (supplied, so a planted registry can be graded). `names` is the
    DOMAIN and is supplied by the caller for the same reason — the production caller derives it
    from `SKILL_NAMES`, and a test can shrink it to prove the derivation is load-bearing.
    """
    from mokata.skills import command_markdown

    templates_dir = Path(templates_dir)
    out = {}
    for name in names:
        skill = registry.get(name)
        if skill is None:
            out[name] = L1_NOT_IN_REGISTRY       # link 1 is undefined here, not failing here
            continue
        declared = skill.hand_authored_template
        if declared is not None and not str(declared).strip():
            out[name] = L1_DECLARATION_UNREASONED
            continue
        p = templates_dir / f"{name}.md"
        if not p.is_file():
            out[name] = L1_TEMPLATE_MISSING      # NOT a skip — absence is its own verdict
            continue
        identical = p.read_text(encoding="utf-8") == command_markdown(skill)
        if declared is None:
            out[name] = L1_GENERATED_MATCH if identical else L1_GENERATED_DRIFT
        else:
            out[name] = L1_HAND_AUTHORED_IDENTICAL if identical else L1_HAND_AUTHORED
    return out


def grade_link2(curated, templates_dir, skills_dir, names) -> dict:
    """Grade `templates/commands/<name>.md -> skills/<name>/SKILL.md` for each of `names`.

    `curated` is the set that is SUPPOSED to have a mirror; a name outside it must have none, and
    that is checked rather than assumed — `version` is a registry skill deliberately excluded from
    the curated set, and "no mirror expected" must not be reachable by simply failing to look.
    """
    from mokata.agent_skills import skill_markdown

    templates_dir = Path(templates_dir)
    skills_dir = Path(skills_dir)
    curated = set(curated)
    out = {}
    for name in names:
        p = skills_dir / name / "SKILL.md"
        if name not in curated:
            # Not an agent skill. The healthy state is that NOTHING is shipped for it; a mirror
            # that exists anyway is a real finding (an uncurated skill reaching the agent).
            out[name] = L2_NOT_CURATED if not p.is_file() else L2_DRIFT
            continue
        if not (templates_dir / f"{name}.md").is_file():
            out[name] = L2_TEMPLATE_MISSING      # cannot render; distinct from a missing mirror
            continue
        if not p.is_file():
            out[name] = L2_MIRROR_MISSING        # NOT a skip — this is the named defect
            continue
        out[name] = (L2_MATCH if p.read_text(encoding="utf-8") == skill_markdown(name, templates_dir)
                     else L2_DRIFT)
    return out


def offenders(graded, healthy) -> dict:
    """The subset of `graded` whose disposition is not in `healthy` — what a pin must fail on."""
    return {n: d for n, d in graded.items() if d not in healthy}
