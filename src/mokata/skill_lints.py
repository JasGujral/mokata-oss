"""SK.S4 — skill lints (validate the finished skill content SK.S1–S3 produced).

The last Phase-C step. SK.S1 gave every skill a ``## Contract`` + a ``⛭ … active — gate:``
activation line; SK.S2 gave it an anti-rationalization table + a Verification checkbox; and
proactive dispatch (this stage) gives it model-invocation triggers (frontmatter
``when_to_use``). These lints assert that anatomy is actually PRESENT and well-formed on every
shipped ``skills/<name>/SKILL.md``, and run as part of the test suite + CI so a skill can never
ship with a missing Contract, too-few triggers, or a dropped anatomy section.

Three lints:

* **contract-lint** — every curated skill carries a ``## Contract`` section AND the single-
  sourced ``⛭ mokata <skill> active — gate: …`` activation line (SK.S1's surface).
* **trigger-lint** — every curated skill declares ≥3 engage triggers and ≥1 negative trigger
  (when NOT to engage) in its ``when_to_use`` frontmatter, so it auto-fires legibly and never
  over-fires (proactive dispatch).
* **anatomy-lint** — every curated skill carries the anti-rationalization table
  (``## Rationalizations``) and the Verification checkbox (``## Verification`` with ``- [ ]``
  items) (SK.S2's anatomy).

**Expected set = ``CURATED_SKILLS``, never a hardcoded number.** The lints iterate the canonical
registry in :mod:`agent_skills`, so when a skill is added there (DK.S5 adds ``docsync`` — 15→16)
the expected set extends automatically; no count constant to bump.

**Validator-owned anti-self-exempt (the load-bearing rule).** Any exemption lives in
:data:`EXEMPTIONS` HERE, in the lint code — never in a skill's own frontmatter. A skill cannot
exempt itself: a planted ``lint_exempt:`` (or similar) key in a SKILL.md is IGNORED for exemption
purposes (the underlying defect is still reported) AND is itself flagged as a violation. So the
only way to exempt a skill from a lint is to edit this module — a reviewable, human-gated change.

These are TEST/CI checks over static content, not runtime gates: nothing here blocks execution.
The eight real gates stay exactly the backed enforcement points in the gate-integrity map.

Clean-room: mokata's own words and structure; no external framework imported.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from .agent_skills import CURATED_SKILLS, parse_frontmatter
from .progress import SKILL_GLYPH

# --------------------------------------------------------------- lint ids
LINT_CONTRACT = "contract-lint"
LINT_TRIGGER = "trigger-lint"
LINT_ANATOMY = "anatomy-lint"
LINT_CITATION = "citation-lint"
LINT_SELF_EXEMPT = "anti-self-exempt"
LINT_IDS: Tuple[str, ...] = (LINT_CONTRACT, LINT_TRIGGER, LINT_ANATOMY, LINT_CITATION)

# The minimum trigger anatomy every skill must declare (proactive dispatch).
MIN_TRIGGERS = 3
MIN_NEGATIVE_TRIGGERS = 1


# --------------------------------------------------------------- validator-owned exemptions
# THE ONLY place a skill may be exempted from a lint. A skill's own frontmatter can NEVER add to
# this (see :func:`_self_exempt_findings`) — exemptions are validator-owned so a check can only be
# waived by a reviewable edit here, never by the very file under test disabling its own check.
# Empty by design: every curated skill is expected to satisfy every lint.
EXEMPTIONS: Dict[str, FrozenSet[str]] = {
    LINT_CONTRACT: frozenset(),
    LINT_TRIGGER: frozenset(),
    LINT_ANATOMY: frozenset(),
    LINT_CITATION: frozenset(),
}

# Frontmatter keys a skill might plant to try to exempt ITSELF from a lint. Their presence is
# never honoured (exemptions are validator-owned) and is itself a violation, so a self-exempt
# attempt fails LOUD instead of silently switching a check off.
SELF_EXEMPT_KEYS: Tuple[str, ...] = (
    "lint_exempt", "lint-exempt", "skip_lint", "skip-lint", "no_lint", "no-lint",
    "mokata_lint_exempt", "exempt_lint", "disable_lint",
)

# Markers that begin a skill's negative-trigger clause (when NOT to engage).
NEGATIVE_MARKERS: Tuple[str, ...] = (
    "do not engage", "don't engage", "do not use this", "don't use this",
    "never engage", "not engage", "avoid engaging",
)


# --------------------------------------------------------------- findings
@dataclass(frozen=True)
class LintFinding:
    """One lint violation: which lint, which skill, and a human-readable reason."""

    lint: str
    skill: str
    message: str

    def render(self) -> str:
        return f"[{self.lint}] {self.skill}: {self.message}"


# --------------------------------------------------------------- loading shipped content
def expected_skill_names() -> Tuple[str, ...]:
    """The set the lints validate — the canonical ``CURATED_SKILLS`` registry, NEVER a hardcoded
    count. Adding a name there (DK.S5's ``docsync``) extends the expected set here for free."""
    return tuple(CURATED_SKILLS)


def _shipped_skills_dir() -> Path:
    from . import package_data_root
    return Path(package_data_root()) / "skills"


def load_shipped_skill_texts(
    names: Optional[Tuple[str, ...]] = None, skills_dir: Optional[Path] = None
) -> Dict[str, str]:
    """``{name: SKILL.md text}`` for the expected set. A missing file maps to ``""`` (which every
    lint then flags), so a curated skill without a shipped SKILL.md is a failure, never a silent
    skip. ``names`` defaults to :func:`expected_skill_names`; ``skills_dir`` to the package dir."""
    chosen = names if names is not None else expected_skill_names()
    root = Path(skills_dir) if skills_dir is not None else _shipped_skills_dir()
    out: Dict[str, str] = {}
    for name in chosen:
        path = root / name / "SKILL.md"
        try:
            out[name] = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            out[name] = ""
    return out


# --------------------------------------------------------------- trigger analysis
def _split_positive_negative(when_to_use: str) -> Tuple[str, str]:
    """Split ``when_to_use`` at the FIRST negative marker into (positive-triggers, negative-part).
    No negative marker → the whole text is positive and the negative part is empty."""
    low = when_to_use.lower()
    neg_pos: Optional[int] = None
    for marker in NEGATIVE_MARKERS:
        i = low.find(marker)
        if i != -1 and (neg_pos is None or i < neg_pos):
            neg_pos = i
    if neg_pos is None:
        return when_to_use, ""
    return when_to_use[:neg_pos], when_to_use[neg_pos:]


def _count_positive_triggers(positive: str) -> int:
    """Count the distinct engage-conditions in the positive portion of a ``when_to_use``.

    Conditions are the comma-separated clauses of the positive text, after dropping a lead-in
    verb ("Engage when …") and an ``i.e.`` restatement tail (a clarifier, not a new trigger — but
    an ``e.g.`` list IS kept, its examples are real triggers). A clause counts only if it carries
    ≥2 words (so a stray fragment isn't miscounted as a trigger)."""
    p = positive.strip()
    low = p.lower()
    # Drop an "i.e." summary/restatement tail (not additional triggers). Keep "e.g." examples.
    k = low.find("i.e.")
    if k != -1:
        p = p[:k].rstrip(" —–-,;:·")
        low = p.lower()
    for lead in ("engage when ", "engage on ", "engage for ", "use when ",
                 "use this skill when ", "use this when ", "engage if "):
        if low.startswith(lead):
            p = p[len(lead):]
            break
    clauses: List[str] = []
    for raw in p.split(","):
        c = raw.strip()
        cl = c.lower()
        for conj in ("or ", "and ", "nor ", "when "):
            if cl.startswith(conj):
                c = c[len(conj):].strip()
                break
        if len(c.split()) >= 2:
            clauses.append(c)
    return len(clauses)


def analyze_triggers(when_to_use: str) -> Tuple[int, bool]:
    """``(positive_trigger_count, has_negative_trigger)`` for a ``when_to_use`` string."""
    positive, negative = _split_positive_negative(when_to_use)
    return _count_positive_triggers(positive), bool(negative.strip())


# --------------------------------------------------------------- the three lints
def _activation_present(name: str, text: str) -> bool:
    """The single-sourced ``⛭ mokata <skill> … — gate: …`` activation line (SK.S1) is present."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(f"{SKILL_GLYPH} mokata {name} ") and "gate:" in s:
            return True
    return False


def _lint_contract(name: str, text: str) -> List[LintFinding]:
    findings: List[LintFinding] = []
    if "## Contract" not in text:
        findings.append(LintFinding(
            LINT_CONTRACT, name, "missing the `## Contract` section (SK.S1)"))
    if not _activation_present(name, text):
        findings.append(LintFinding(
            LINT_CONTRACT, name,
            f"missing the `{SKILL_GLYPH} mokata {name} active — gate: …` activation line (SK.S1)"))
    return findings


def _lint_triggers(name: str, text: str) -> List[LintFinding]:
    findings: List[LintFinding] = []
    fm = parse_frontmatter(text)
    when = (fm.get("when_to_use") or "").strip()
    if not when:
        findings.append(LintFinding(
            LINT_TRIGGER, name,
            "no `when_to_use` frontmatter — a skill must declare model-invocation triggers so it "
            "auto-fires (proactive dispatch)"))
        return findings
    count, has_negative = analyze_triggers(when)
    if count < MIN_TRIGGERS:
        findings.append(LintFinding(
            LINT_TRIGGER, name,
            f"declares only {count} engage trigger(s); ≥{MIN_TRIGGERS} required"))
    if not has_negative:
        findings.append(LintFinding(
            LINT_TRIGGER, name,
            "no negative trigger — must state when NOT to engage (e.g. \"Do NOT engage …\")"))
    return findings


def _lint_anatomy(name: str, text: str) -> List[LintFinding]:
    findings: List[LintFinding] = []
    if "## Rationalizations" not in text:
        findings.append(LintFinding(
            LINT_ANATOMY, name,
            "missing the anti-rationalization table (`## Rationalizations`) (SK.S2)"))
    if "## Verification" not in text:
        findings.append(LintFinding(
            LINT_ANATOMY, name,
            "missing the Verification checkbox (`## Verification`) (SK.S2)"))
    elif "- [ ]" not in text:
        findings.append(LintFinding(
            LINT_ANATOMY, name,
            "the Verification section has no `- [ ]` checkbox items (SK.S2)"))
    return findings


# --------------------------------------------------------------- citation-lint (DK.S0)
# The clean-room primary-source authoring standard, enforced on DOMAIN skills (DK.S1–S4) only:
# a domain skill that makes external claims must CITE a primary-source URL and carry the UNVERIFIED
# discipline. It is a NO-OP on the pipeline skills — it fires only when the skill name is a domain
# (`domains.is_domain_skill`), so the 15 pipeline skills are untouched until domain content lands.
_URL_MARKERS: Tuple[str, ...] = ("http://", "https://")


def domain_citation_findings(name: str, text: str) -> List[str]:
    """The messages the citation lint would raise for one skill's ``text`` — empty unless ``name``
    is a domain skill that fails the clean-room standard (missing a cited primary-source URL, or
    missing the UNVERIFIED discipline). A pure helper (no LintFinding wrapping) so callers and the
    DK.S0 tests can check the standard directly; :func:`_lint_citation` wraps it for the runner."""
    from .domains import is_domain_skill
    if not is_domain_skill(name):
        return []                               # not a domain skill → the lint does not apply
    low = text.lower()
    out: List[str] = []
    if not any(m in low for m in _URL_MARKERS):
        out.append("a domain skill must CITE a primary-source URL for its external claims "
                   "(clean-room authoring standard) — none found")
    if "unverified" not in low:
        out.append("a domain skill must carry the UNVERIFIED discipline (flag anything it could "
                   "not verify) — the marker is absent")
    return out


def _lint_citation(name: str, text: str) -> List[LintFinding]:
    return [LintFinding(LINT_CITATION, name, msg)
            for msg in domain_citation_findings(name, text)]


_LINTS = {
    LINT_CONTRACT: _lint_contract,
    LINT_TRIGGER: _lint_triggers,
    LINT_ANATOMY: _lint_anatomy,
    LINT_CITATION: _lint_citation,
}


# --------------------------------------------------------------- anti-self-exempt
def _self_exempt_findings(name: str, text: str) -> List[LintFinding]:
    """Reject any frontmatter key that tries to exempt this skill from a lint. Exemptions are
    validator-owned (:data:`EXEMPTIONS`); a skill can never switch off its own check."""
    findings: List[LintFinding] = []
    fm = parse_frontmatter(text)
    for key in fm:
        if key.strip().lower() in SELF_EXEMPT_KEYS:
            findings.append(LintFinding(
                LINT_SELF_EXEMPT, name,
                f"frontmatter key `{key.strip()}` attempts to exempt this skill from a lint; "
                "exemptions are validator-owned (in `skill_lints.EXEMPTIONS`) and cannot be set "
                "from a skill — ignored and rejected"))
    return findings


# --------------------------------------------------------------- runner
def run_lints(
    texts: Optional[Dict[str, str]] = None,
    exemptions: Optional[Dict[str, FrozenSet[str]]] = None,
) -> List[LintFinding]:
    """Run all three lints over ``texts`` (default: the shipped SKILL.md for
    :func:`expected_skill_names`). Returns every :class:`LintFinding`; empty == all green.

    A self-exempt attempt in a skill's frontmatter is reported AND ignored: the underlying lint
    still runs and still reports the real defect, so a skill can't disable a check by planting a
    key. The ``exemptions`` argument defaults to the validator-owned :data:`EXEMPTIONS` and is the
    ONLY channel that can waive a lint."""
    if texts is None:
        texts = load_shipped_skill_texts()
    ex = exemptions if exemptions is not None else EXEMPTIONS
    findings: List[LintFinding] = []
    for name, text in texts.items():
        # Self-exempt attempts are rejected outright — and never consulted below.
        findings.extend(_self_exempt_findings(name, text))
        for lint_id, fn in _LINTS.items():
            if name in ex.get(lint_id, frozenset()):
                continue                       # validator-owned exemption ONLY
            findings.extend(fn(name, text))
    return findings


def lint_report(findings: List[LintFinding]) -> str:
    """A human/CI-readable report. Green when there are no findings."""
    if not findings:
        n = len(expected_skill_names())
        return (f"skill lints OK — all {n} curated skill(s) carry a Contract + activation line, "
                f"≥{MIN_TRIGGERS} triggers + a negative, and the full anatomy.")
    lines = [f"skill lints FAILED ({len(findings)} finding(s)):"]
    lines.extend(f"  {f.render()}" for f in findings)
    return "\n".join(lines)
