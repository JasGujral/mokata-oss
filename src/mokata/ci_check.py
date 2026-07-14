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
    degraded: bool = False          # D5 — a leg that could not READ what it was meant to check

    @property
    def blocked(self) -> bool:
        return self.status == "block"

    def verdict(self, *, ascii_only: bool = False) -> str:
        from .legibility import gate_verdict
        if self.status == "skip":
            mark = "[skip]" if ascii_only else "•"
            line = f"{mark} {self.name} skipped — {self.summary}"
        else:
            line = gate_verdict(self.name, self.status == "pass", self.summary,
                                action=self.unblock, ascii_only=ascii_only)
        # D5 — a PASS that could not read its corpus is not a pass, it is an unchecked PR; and a
        # SKIP ("nothing to guard") is the same lie wearing a friendlier word, because the corpus it
        # found nothing in is the one it failed to read. The leg stays non-blocking (a PR gate must
        # never FALSE-BLOCK on mokata's own broken read — that is the degrade-clean contract), but
        # neither verdict may be read as "nothing is affected".
        if self.degraded:
            mark = "[!]" if ascii_only else "⚠"
            line += f"\n  {mark} DEGRADED — this leg ran on an INCOMPLETE read; see the detail."
        return line


@dataclass
class CICheckResult:
    legs: List[CheckLeg] = field(default_factory=list)
    initialized: bool = True

    @property
    def blocked(self) -> bool:
        return any(leg.blocked for leg in self.legs)

    @property
    def degraded(self) -> bool:
        """D5 — any leg ran on an incomplete read. The check still PASSES (never a false block),
        but "PASSED" must not be the only word the reviewer sees."""
        return any(leg.degraded for leg in self.legs)

    @property
    def exit_code(self) -> int:
        return 1 if self.blocked else 0

    @property
    def overall(self) -> str:
        if self.blocked:
            return "BLOCKED"
        return "PASSED (DEGRADED)" if self.degraded else "PASSED"

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
        icon = "🛑" if self.blocked else ("⚠️" if self.degraded else "✅")
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
        elif self.degraded:
            # D5 — never "Nothing to flag 🎉" on a read that failed: that sentence is the bug.
            lines.append("⚠️ **mokata could not complete this check** — a leg above ran on an "
                         "INCOMPLETE read, so this PR was NOT fully checked against the saved "
                         "specs/decisions. Treat this as *unchecked*, not *clean*: run "
                         "`mokata doctor` on the CI runner.")
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
    no saved corpus → skip; a touch → BLOCK (a reviewer must confirm/amend); no overlap → pass.

    D5 — the degrade-clean contract cuts BOTH ways. A store that will not build must not false-block
    the PR (it doesn't), but the leg then checks the change against ZERO decisions and reports "no
    saved spec or decision is affected" — the exact sentence it would print if the corpus had loaded
    and genuinely cleared the change. A PR that contradicts a recorded decision merges GREEN, and
    nobody is told. The fallback still falls back; the leg is now marked DEGRADED so the pass cannot
    be read as a clean bill of health."""
    from sqlite3 import Error as SQLiteError

    from .engine.spec_awareness import ChangeSet, check_change, load_decisions, load_spec_corpus
    from .knowledge import KnowledgeLayer
    from .manifest import ManifestError
    from .memory import MemoryStore
    from .memory.store import MemoryError

    specs = load_spec_corpus(surface.state)
    degraded: List[str] = []
    try:
        store = MemoryStore.from_surface(surface)
        decisions = load_decisions(store)
    except (MemoryError, OSError, ImportError, ManifestError, SQLiteError) as exc:
        from .degrade import FAILURE_UNREACHABLE, note_degraded
        decisions = []
        degraded.append("the DECISION CORPUS could not be read — this PR was NOT checked "
                        "against saved decisions")
        note_degraded("memory", FAILURE_UNREACHABLE,
                      fallback="this PR was NOT checked against saved decisions",
                      fix="run `mokata doctor`", detail=str(exc))
    try:
        layer = KnowledgeLayer.from_surface(surface)
    except (ManifestError, AttributeError):
        # The knowledge layer is an ENRICHMENT here (it widens the touch-set); its absence narrows
        # the check but does not blind it, and `select_backends` already reports a graph that fell
        # to the floor. The corpus above is the leg's actual evidence — that one is the degrade.
        layer = None

    change = ChangeSet(symbols=list(changed_symbols), files=list(changed_files))
    report = check_change(change, specs, decisions, layer=layer)
    is_degraded = bool(degraded)
    if not report.checked:
        # "nothing to guard" is TRUE only when there was nothing to read. When the read FAILED, the
        # empty corpus is an artefact of the failure, and that sentence becomes the lie.
        note = ("mokata could not READ the corpus — this is not 'nothing to guard'" if is_degraded
                else (report.note or "nothing to guard"))
        return CheckLeg("spec-awareness", "skip", note, detail=degraded, degraded=is_degraded)
    if not report.has_conflicts:
        summary = (f"no saved spec or decision is affected ({report.note})" if not is_degraded
                   else f"no conflict found in what could be READ ({report.note})")
        return CheckLeg("spec-awareness", "pass", summary, detail=degraded,
                        degraded=is_degraded)
    detail = [c.render().strip() for c in report.conflicts] + degraded
    return CheckLeg(
        "spec-awareness", "block",
        f"this change affects {len(report.conflicts)} saved spec(s)/decision(s)",
        detail=detail, degraded=is_degraded,
        unblock=("confirm (amend/supersede) the affected spec(s)/decision(s) through the "
                 "deviation gate (`mokata spec-check`), or re-plan so they aren't broken"))


# ----------------------------------------------------------------- the check
def run_ci_check(root: str, changed_files: List[str],
                 changed_symbols: Optional[List[str]] = None) -> CICheckResult:
    """Run the completeness + spec-awareness legs over a PR's changed files. Pure of git/network
    (the caller supplies the changed-file list). NEVER raises — an uninitialized/unreadable repo
    degrades to a clean PASS (nothing to check)."""
    from .config import ConfigError, Surface
    changed_files = list(changed_files or [])
    if not Surface.is_initialized(root):
        return CICheckResult(legs=[], initialized=False)
    try:
        surface = Surface.load(root)
    except (ConfigError, OSError):
        # `Surface.load` raises ConfigError for an absent/invalid manifest (it re-wraps
        # ManifestError) and OSError for an unreadable constitution. An UNLOADABLE surface is
        # reported as uninitialized — the same friendly PASS a repo with no mokata gets.
        return CICheckResult(legs=[], initialized=False)

    symbols = changed_symbols if changed_symbols is not None \
        else symbols_in_files(root, changed_files)

    legs = [
        _completeness_leg(root, surface.state),
        _spec_awareness_leg(surface, changed_files, symbols),
    ]
    return CICheckResult(legs=legs, initialized=True)
