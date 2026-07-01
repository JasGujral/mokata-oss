"""Stage 58 — mokata as a CI / PR check.

Run mokata's two PR-relevant gates over a pull request's CHANGED FILES and report PASS/BLOCK as a
check result (non-zero exit on a real block) plus a review-comment body. It COMPOSES the existing
engines — it rebuilds none of them:

  * **completeness** (`engine.completeness.run_completeness_gate` + `engine.acmapper.scan_tests`):
    does the repo's SAVED spec still have every acceptance criterion mapped to a test?
  * **spec-awareness** (`engine.spec_awareness.check_change`): does this PR TOUCH a previously
    saved spec/decision — a regression a reviewer must confirm?

DEGRADE-CLEAN is the whole point of a PR gate: it must never FALSE-BLOCK. So when there's nothing
to check it PASSES — an uninitialized repo, no saved spec, no spec corpus, or a repo that doesn't
tag its tests with AC ids all SKIP rather than block. The check is READ-ONLY: it SURFACES blocks
for the reviewer and PRODUCES the comment body; it never posts to GitHub itself (the workflow's
own `GITHUB_TOKEN` posts it — mokata never acts on a user's behalf outside their CI).

Core stays dependency-free; clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

# ----------------------------------------------------------------- changed-file → touch-set
def symbols_in_files(root: str, files: List[str]) -> List[str]:
    """The defined symbols (functions, classes, types) in the changed source files — the touch-set
    the spec-awareness guard expands. Read-only, dependency-free, LANGUAGE-AWARE (Stage 65): per
    the lexical heuristics in `mokata.languages`, so Python/JS-TS/Go/Rust/Java all surface their
    definitions. A missing/unreadable file or an unknown language is simply skipped/handled
    generically (degrade-clean — never a crash)."""
    from . import languages
    out: List[str] = []
    seen = set()
    for rel in files:
        if not languages.is_source_file(rel):
            continue
        path = rel if os.path.isabs(rel) else os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for name in languages.language_for(rel).definition_names(text):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


# ----------------------------------------------------------------- result model
@dataclass
class CheckLeg:
    name: str                       # "completeness" | "spec-awareness"
    status: str                     # "pass" | "block" | "skip"
    summary: str
    detail: List[str] = field(default_factory=list)
    unblock: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.status == "block"

    def verdict(self, *, ascii_only: bool = False) -> str:
        from .legibility import gate_verdict
        if self.status == "skip":
            mark = "[skip]" if ascii_only else "•"
            return f"{mark} {self.name} skipped — {self.summary}"
        return gate_verdict(self.name, self.status == "pass", self.summary,
                            action=self.unblock, ascii_only=ascii_only)


@dataclass
class CICheckResult:
    legs: List[CheckLeg] = field(default_factory=list)
    initialized: bool = True

    @property
    def blocked(self) -> bool:
        return any(leg.blocked for leg in self.legs)

    @property
    def exit_code(self) -> int:
        return 1 if self.blocked else 0

    @property
    def overall(self) -> str:
        return "BLOCKED" if self.blocked else "PASSED"

    def render(self, *, ascii_only: bool = False) -> str:
        head = f"mokata PR check — {self.overall}"
        if not self.initialized:
            return head + "\n  • mokata not initialized in this repo — nothing to check (PASS)."
        lines = [head]
        for leg in self.legs:
            for sub in leg.verdict(ascii_only=ascii_only).splitlines():
                lines.append(f"  {sub}")
            for d in leg.detail:
                lines.append(f"      {d}")
        return "\n".join(lines)

    def comment_body(self) -> str:
        """The PR review COMMENT (GitHub-flavoured markdown). mokata produces it; the workflow's
        own GITHUB_TOKEN posts it — never mokata, never from a user's machine."""
        icon = "🛑" if self.blocked else "✅"
        lines = [f"## {icon} mokata PR check — **{self.overall}**", ""]
        if not self.initialized:
            lines.append("mokata isn't initialized in this repo, so there's nothing to check "
                         "(no spec gate, no regression guard). _Passing._")
            lines.append("")
            lines.append("_Opt-in, local-first, degrade-clean — mokata only flags what it can "
                         "actually check._")
            return "\n".join(lines)
        for leg in self.legs:
            lines.append(f"- {leg.verdict().replace(chr(10) + '  ', '  ')}")
            for d in leg.detail:
                lines.append(f"  - {d}")
        lines.append("")
        if self.blocked:
            lines.append("Address the **to unblock** action(s) above, or confirm the change "
                         "through mokata's gates, then push again.")
        else:
            lines.append("Nothing for mokata to flag on this change. 🎉")
        lines.append("")
        lines.append("_Opt-in, local-first, degrade-clean — mokata reuses its own completeness "
                     "gate + spec-awareness guard and only flags what it can actually check._")
        return "\n".join(lines)


# ----------------------------------------------------------------- the two legs
def _completeness_leg(root: str, store: Any) -> CheckLeg:
    """Verify the SAVED spec is still complete (every AC maps to a test). Degrade-clean: no saved
    spec → skip; a repo that doesn't tag tests with AC ids (zero coverage) → skip (never a false
    block); a partial mapping → BLOCK on the unmapped AC(s)."""
    from .engine.acmapper import scan_tests
    from .engine.completeness import run_completeness_gate
    from .engine.spec_gate import load_emitted_spec

    spec = load_emitted_spec(store)
    if spec is None or not spec.criteria:
        return CheckLeg("completeness", "skip",
                        "no saved spec in this repo — nothing to verify")
    tests = scan_tests(root, spec.ac_ids)
    covered = {aid for t in tests for aid in t.ac_ids}
    if not covered:
        # No AC-id-tagged tests anywhere → this repo doesn't use the convention; don't false-block.
        return CheckLeg("completeness", "skip",
                        "no AC-tagged tests found — completeness not enforced for this repo")
    result = run_completeness_gate(spec, tests, store=store)
    if result.passed:
        return CheckLeg("completeness", "pass", result.reason)
    from .legibility import unblock_hint
    detail = [f"unmapped acceptance criteria: {', '.join(result.unmapped_ids)}"] \
        if result.unmapped_ids else []
    return CheckLeg("completeness", "block", result.reason, detail=detail,
                    unblock=unblock_hint(result.gate_id))


def _spec_awareness_leg(surface: Any, changed_files: List[str],
                        changed_symbols: List[str]) -> CheckLeg:
    """Surface a regression: does this PR touch a previously saved spec/decision? Degrade-clean:
    no saved corpus → skip; a touch → BLOCK (a reviewer must confirm/amend); no overlap → pass."""
    from .engine.spec_awareness import ChangeSet, check_change, load_decisions, load_spec_corpus
    from .knowledge import KnowledgeLayer
    from .memory import MemoryStore

    specs = load_spec_corpus(surface.state)
    try:
        store = MemoryStore.from_surface(surface)
        decisions = load_decisions(store)
    except Exception:
        decisions = []
    try:
        layer = KnowledgeLayer.from_surface(surface)
    except Exception:
        layer = None

    change = ChangeSet(symbols=list(changed_symbols), files=list(changed_files))
    report = check_change(change, specs, decisions, layer=layer)
    if not report.checked:
        return CheckLeg("spec-awareness", "skip", report.note or "nothing to guard")
    if not report.has_conflicts:
        return CheckLeg("spec-awareness", "pass",
                        f"no saved spec or decision is affected ({report.note})")
    detail = [c.render().strip() for c in report.conflicts]
    return CheckLeg(
        "spec-awareness", "block",
        f"this change affects {len(report.conflicts)} saved spec(s)/decision(s)",
        detail=detail,
        unblock=("confirm (amend/supersede) the affected spec(s)/decision(s) through the "
                 "deviation gate (`mokata spec-check`), or re-plan so they aren't broken"))


# ----------------------------------------------------------------- the check
def run_ci_check(root: str, changed_files: List[str],
                 changed_symbols: Optional[List[str]] = None) -> CICheckResult:
    """Run the completeness + spec-awareness legs over a PR's changed files. Pure of git/network
    (the caller supplies the changed-file list). NEVER raises — an uninitialized/unreadable repo
    degrades to a clean PASS (nothing to check)."""
    from .config import Surface
    changed_files = list(changed_files or [])
    if not Surface.is_initialized(root):
        return CICheckResult(legs=[], initialized=False)
    try:
        surface = Surface.load(root)
    except Exception:
        return CICheckResult(legs=[], initialized=False)

    symbols = changed_symbols if changed_symbols is not None \
        else symbols_in_files(root, changed_files)

    legs = [
        _completeness_leg(root, surface.state),
        _spec_awareness_leg(surface, changed_files, symbols),
    ]
    return CICheckResult(legs=legs, initialized=True)
