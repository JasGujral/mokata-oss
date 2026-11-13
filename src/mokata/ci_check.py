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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional

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
    from .knowledge.cancel import checkpoint, report
    total = len(files)
    for i, rel in enumerate(files):
        checkpoint()            # cancellable per file; a no-op outside a job
        # visible long work (0.0.28 F): true progress, one unit (files), done never past total
        report(done=i + 1, total=total, unit="files", note="reading the changed files")
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


#: Where the parts of the evidence a size limit left out can be found (G3-EVIDENCE).
COMMENT_POINTER = ("this job's step summary (the `--evidence-file` render), and the committed "
                   "record files under `.mokata/runs/` in this PR's diff")
FILE_POINTER = "the committed record files under `.mokata/runs/` in this PR's diff"


# ----------------------------------------------------------------- result model
#: The third leg outcome (SPEC-TRISTATE): nothing was there to check. Neither a pass nor a failure.
ABSENT = "absent"


@dataclass
class CheckLeg:
    name: str                       # "completeness" | "spec-awareness" | "spec-drift"
    #: SPEC-TRISTATE — exactly three outcomes, and the third is NOT a flavour of the first:
    #: "pass" (checked, clean), "block" (checked, failed) or "absent" (there was nothing to check,
    #: or part of it could not be graded). An absent leg is never counted, rendered or exported as a
    #: pass: `CICheckResult.overall`, the comment's icon and closing line, the count summary and the
    #: MCP response all read it from `CheckLeg.absent`.
    status: str
    summary: str
    detail: List[str] = field(default_factory=list)
    unblock: Optional[str] = None
    ungraded: int = 0               # ACs a passing leg could not attribute to a test
    degraded: bool = False          # D5 — a leg that could not READ what it was meant to check
    #: G3-EVIDENCE — the gate evidence THIS leg computed on the runner (`GateEvidence`), rendered
    #: under the verdicts. It is this check's own; a committed record never lands here.
    evidence: List[Any] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status == "block"

    @property
    def absent(self) -> bool:
        return self.status == ABSENT

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def verdict(self, *, ascii_only: bool = False) -> str:
        from .legibility import gate_verdict
        if self.absent:
            mark = "[absent]" if ascii_only else "•"
            line = f"{mark} {self.name} NOT CHECKED — {self.summary}"
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
    #: G3-EVIDENCE — the run records this PR committed (`run_record.RunRecords`) and the repo root
    #: their paths resolve against. They are rendered; they never feed `blocked` or `exit_code`.
    records: Any = None
    root: str = ""

    @property
    def blocked(self) -> bool:
        return any(leg.blocked for leg in self.legs)

    @property
    def degraded(self) -> bool:
        """D5 — any leg ran on an incomplete read. The check still PASSES (never a false block),
        but "PASSED" must not be the only word the reviewer sees."""
        return any(leg.degraded for leg in self.legs) or self.records_degraded

    @property
    def records_degraded(self) -> bool:
        """A committed run record this PR touched could not be rendered (unreadable/inconsistent).
        It never blocks (a committed record never grades), but it is not a clean pass either."""
        return bool(self.records is not None and self.records.degraded)

    @property
    def absent_legs(self) -> List[str]:
        """SPEC-TRISTATE — the legs that had nothing to check. Never folded into a pass."""
        return [leg.name for leg in self.legs if leg.absent]

    @property
    def checked(self) -> bool:
        """At least one leg actually graded something (passed or blocked). An uninitialized repo,
        or every leg absent, is NOT checked."""
        return self.initialized and any(leg.passed or leg.blocked for leg in self.legs)

    @property
    def exit_code(self) -> int:
        """1 only for a real block. An absent leg exits 0 (a PR gate never false-blocks on a repo
        that simply has nothing to check) -- but 0 here means "did not fail", and `overall` /
        `checked` / `absent_legs` say it did not PASS either. The merge gate (P21) does not read
        this leg in 0.0.27."""
        return 1 if self.blocked else 0

    @property
    def icon(self) -> str:
        """The comment headline's mark. The green check is reserved for a check that passed in
        full: absent and degraded each get their own, so neither borrows it."""
        if self.blocked:
            return "🛑"
        if self.degraded:
            return "⚠️"
        if not self.checked or self.absent_legs:
            return "➖"
        return "✅"

    @property
    def overall(self) -> str:
        if self.blocked:
            return "BLOCKED"
        tags = (["DEGRADED"] if self.degraded else []) + (
            ["PARTIAL: " + ", ".join(self.absent_legs) + " not checked"]
            if self.checked and self.absent_legs else [])
        head = "PASSED" if self.checked else "NOT CHECKED"
        return head + (" (" + "; ".join(tags) + ")" if tags else "")

    def render(self, *, ascii_only: bool = False) -> str:
        head = f"mokata PR check — {self.overall}"
        if not self.initialized:
            return head + ("\n  • mokata not initialized in this repo — nothing was checked "
                           "(absent, not a pass).")
        lines = [head]
        for leg in self.legs:
            for sub in leg.verdict(ascii_only=ascii_only).splitlines():
                lines.append(f"  {sub}")
            for d in leg.detail:
                lines.append(f"      {d}")
        from .ci_evidence import records_status
        if self.records is not None:
            lines.append("  evidence: " + " ".join(records_status(self.records)).replace("**", ""))
        return "\n".join(lines)

    def comment_body(self, link: Any = None) -> str:
        """The PR review COMMENT: the verdicts, then the evidence, under GitHub's comment limit."""
        from .ci_evidence import GITHUB_COMMENT_MAX
        return self._evidence_output(link, limit=GITHUB_COMMENT_MAX, measure=len,
                                     pointer=COMMENT_POINTER)

    def evidence_markdown(self, link: Any = None) -> str:
        """The `--evidence-file` (appended to the job's step summary): the same sections, budgeted
        by the same rule against GitHub's step-summary limit."""
        from .ci_evidence import STEP_SUMMARY_MAX
        return self._evidence_output(link, limit=STEP_SUMMARY_MAX,
                                     measure=lambda t: len(t.encode("utf-8")),
                                     pointer=FILE_POINTER)

    def _evidence_output(self, link: Any, *, limit: int, measure: Any, pointer: str) -> str:
        verdict = self._verdict_markdown()
        if not self.initialized:
            return verdict
        from .ci_evidence import EvidenceView, LinkContext, compose, verdict_counts
        view = EvidenceView(root=self.root or ".",
                            check_evidence=[e for leg in self.legs for e in leg.evidence],
                            check_blocked=self.blocked, records=self.records,
                            link=link if link is not None else LinkContext())
        return compose(verdict, verdict_counts(self, pointer), view, limit=limit,
                       measure=measure, pointer=pointer)

    def _verdict_markdown(self) -> str:
        """The PR review COMMENT (GitHub-flavoured markdown). mokata produces it; the workflow's
        own GITHUB_TOKEN posts it — never mokata, never from a user's machine."""
        lines = [f"## {self.icon} mokata PR check — **{self.overall}**", ""]
        if not self.initialized:
            lines.append("mokata isn't initialized in this repo, so **nothing was checked** "
                         "(no spec gate, no regression guard). This is an absent check, not a "
                         "pass; it does not fail the job.")
            lines.append("")
            lines.append("_Opt-in, local-first, degrade-clean — mokata only flags what it can "
                         "actually check._")
            return "\n".join(lines)
        # G3-EVIDENCE — a leg's summary and detail carry COMMITTED-spec text (titles, AC text, test
        # names), which the PR author wrote: each goes through the evidence render's code-span rule,
        # so no title can ping `@org/team`, autolink `#123` or close a `<details>`.
        from dataclasses import replace as _replace
        from .ci_evidence import code_span
        for leg in self.legs:
            safe = _replace(leg, summary=code_span(leg.summary))
            verdict = safe.verdict().replace(chr(10) + "  ", "  ")
            if verdict.startswith("• "):              # the bullet below is the marker
                verdict = verdict[2:]
            lines.append(f"- {verdict}")
            for d in leg.detail:
                lines.append(f"  - {code_span(d[2:] if d.startswith('- ') else d)}")
        lines.append("")
        if self.blocked:
            lines.append("Address the **to unblock** action(s) above, or confirm the change "
                         "through mokata's gates, then push again.")
            self._absent_note(lines)
        elif self.records_degraded and not any(leg.degraded for leg in self.legs):
            lines.append("⚠️ **A committed run record in this PR could not be rendered** (see "
                         "Evidence below). The verdicts above are complete; the record is not.")
            self._absent_note(lines)
        elif self.degraded:
            # D5 — never "Nothing to flag 🎉" on a read that failed: that sentence is the bug.
            lines.append("⚠️ **mokata could not complete this check** — a leg above ran on an "
                         "INCOMPLETE read, so this PR was NOT fully checked against the saved "
                         "specs/decisions. Treat this as *unchecked*, not *clean*: run "
                         "`mokata doctor` on the CI runner.")
            self._absent_note(lines)
        elif self.absent_legs or not self.checked:
            self._absent_note(lines)
        else:
            lines.append("Nothing for mokata to flag on this change. 🎉")
        lines.append("")
        lines.append("_Opt-in, local-first, degrade-clean — mokata reuses its own completeness "
                     "gate + spec-awareness guard and only flags what it can actually check._")
        return "\n".join(lines)


    def _absent_note(self, lines: List[str]) -> None:
        """SPEC-TRISTATE — say, in the comment itself, that an absent leg is not a clean bill."""
        if self.absent_legs:
            lines.append("➖ **Not checked:** " + ", ".join(f"`{n}`" for n in self.absent_legs)
                         + " had nothing to check. *Absent is not passing* — "
                         + ("only the other legs above were graded." if self.checked
                            else "nothing was graded."))


# ----------------------------------------------------------------- the two legs
def _ac_test_map(spec: Any, tests: List[Any]) -> List[str]:
    """The AC -> test map, one line per acceptance criterion: the tests that cover it, or the plain
    statement that none does. Bounded by the spec's own criteria, never by a cap."""
    lines: List[str] = []
    for ac in spec.criteria:
        names = [t.name for t in tests if ac.id in t.ac_ids]
        lines.append(f"{ac.id} -> {', '.join(names) if names else '(no test)'}")
    return lines


REEMIT = "re-emit the spec (`mokata spec emit`) so it records its own tests"


def _completeness_one(root: str, spec: Any, store: Any, all_tests: List[Any]) -> CheckLeg:
    """Grade one spec. A COMMITTED spec is graded by its OWN declared tests only, so criteria that
    every spec numbers AC-1 never borrow another spec's test. A committed spec that declares none
    is not graded: it is listed as such, never passed."""
    from dataclasses import replace
    from .committed_specs import attributed_tests
    from .engine.completeness import run_completeness_gate
    if getattr(spec, "committed", False):
        if spec.tests is None:
            leg = CheckLeg("completeness", ABSENT,
                           f"{spec.title}: records no tests of its own — {REEMIT}",
                           detail=[f"{spec.title}: not graded — {REEMIT}"])
            leg.ungraded = len(spec.criteria)
            return leg
        tests = attributed_tests(spec, all_tests)
    else:
        tests = [t for t in all_tests if set(spec.ac_ids).intersection(t.ac_ids)]
    result = run_completeness_gate(replace(spec, tests=None), tests, store=store)
    detail = [f"{spec.title}: {line}" for line in _ac_test_map(spec, tests)]
    evidence = [result.evidence] if result.evidence is not None else []
    if result.passed:
        return CheckLeg("completeness", "pass", result.reason, detail=detail, evidence=evidence)
    from .legibility import unblock_hint
    if result.unmapped_ids:
        detail.append(f"{spec.title}: unmapped acceptance criteria: {', '.join(result.unmapped_ids)}")
    return CheckLeg("completeness", "block", result.reason, detail=detail,
                    unblock=unblock_hint(result.gate_id), evidence=evidence)


def _completeness_leg(root: str, store: Any) -> CheckLeg:
    """Verify the SAVED spec is still complete (every AC maps to a test). On a CI checkout there is
    no run state, so the COMMITTED specs (`.mokata/specs/`, opt-in) are checked instead — the same
    gate over each, by that spec's OWN declared tests. Degrade-clean: no spec at all → absent; a repo
    that doesn't tag tests with AC ids (zero coverage) → absent (never a false block); a partial
    mapping → BLOCK on the unmapped AC(s). A block in ANY spec blocks; a pass that left a spec
    ungraded is reported as absent, never as a pass."""
    from .committed_specs import load_committed
    from .engine.spec_gate import load_emitted_spec

    saved = load_emitted_spec(store)
    specs = [saved] if saved is not None and saved.criteria else []
    if not specs:
        specs = [sp for sp in load_committed(root).specs if sp.criteria]
    if not specs:
        return CheckLeg("completeness", ABSENT,
                        "no saved or committed spec in this repo — nothing to verify")
    # The AC-tag convention is a property of the REPO, decided once over every spec's ids: a repo
    # that tags any of them is held to it for all of them (one walk, not one per spec).
    from .engine.acmapper import scan_tests
    all_ids = list(dict.fromkeys(a for sp in specs for a in sp.ac_ids))
    all_tests = scan_tests(root, all_ids)
    if not {a for t in all_tests for a in t.ac_ids}:
        return CheckLeg("completeness", ABSENT,
                        "no AC-tagged tests found — completeness not enforced for this repo")
    from .knowledge.cancel import checkpoint
    legs = []
    for sp in specs:
        checkpoint()
        legs.append(_completeness_one(root, sp, store, all_tests))
    detail = [d for leg in legs for d in leg.detail]
    evidence = [e for leg in legs for e in leg.evidence]
    blocked = [leg for leg in legs if leg.blocked]
    if blocked:
        return CheckLeg("completeness", "block", "; ".join(leg.summary for leg in blocked),
                        detail=detail, unblock=blocked[0].unblock, evidence=evidence)
    graded = [leg for leg in legs if leg.status == "pass"]
    ungraded = sum(leg.ungraded for leg in legs)
    if ungraded or not graded:
        return CheckLeg("completeness", ABSENT,
                        f"{ungraded} acceptance criteria were NOT graded: their spec records no "
                        f"tests of its own — {REEMIT}. The rest passed.", detail=detail,
                        evidence=evidence)
    return CheckLeg("completeness", "pass",
                    "; ".join(dict.fromkeys(leg.summary for leg in graded)), detail=detail,
                    evidence=evidence)


def _spec_awareness_leg(surface: Any, changed_files: List[str],
                        changed_symbols: List[str]) -> CheckLeg:
    """Surface a regression: does this PR touch a previously saved spec/decision? Degrade-clean:
    no saved corpus → absent; a touch → BLOCK (a reviewer must confirm/amend); no overlap → pass.

    D5 — the degrade-clean contract cuts BOTH ways. A store that will not build must not false-block
    the PR (it doesn't), but the leg then checks the change against ZERO decisions and reports "no
    saved spec or decision is affected" — the exact sentence it would print if the corpus had loaded
    and genuinely cleared the change. A PR that contradicts a recorded decision merges GREEN, and
    nobody is told. The fallback still falls back; the leg is now marked DEGRADED so the pass cannot
    be read as a clean bill of health."""
    from sqlite3 import Error as SQLiteError

    # SPEC-TRISTATE H1 -- no change set, nothing to compare the corpus against. That is ABSENT, not
    # "no saved spec is affected": grading an empty touch-set printed PASSED over a PR (a bad or
    # unfetched --base, no --files, MCP files="") that the leg never looked at.
    if not changed_files and not changed_symbols:
        return CheckLeg("spec-awareness", ABSENT,
                        "no changed files supplied — the change was not compared with the saved "
                        "specs/decisions (pass --files or a resolvable --base)")

    from .engine.spec_awareness import ChangeSet, check_change, load_decisions, load_spec_corpus
    from .knowledge import KnowledgeLayer
    from .manifest import ManifestError
    from .memory import MemoryStore
    from .memory.store import MemoryError

    specs = list(load_spec_corpus(surface.state))
    # L7b — the COMMITTED specs join the corpus (on a CI checkout they are the whole corpus).
    from .committed_specs import load_committed
    titles = {sp.title for sp in specs}
    specs += [sp for sp in load_committed(surface.root).specs if sp.title not in titles]
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
        return CheckLeg("spec-awareness", ABSENT, note, detail=degraded, degraded=is_degraded)
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


def _committed_specs_leg(root: str) -> CheckLeg:
    """L7b (D1) — drift of the COMMITTED specs (`.mokata/specs/`, opt-in `settings.specs.commit`)
    against this checkout: each AC's code anchors must still resolve and, where the repo tags tests
    with AC ids, each AC must still have its test (`docsync.audit_spec`, the same audit `mokata
    docsync` runs). No committed specs → a visible SKIP naming the opt-in, never a silent pass; an
    unreadable committed spec → BLOCK (a spec nobody can read is not a spec that holds)."""
    from .committed_specs import load_committed
    from .docsync import audit_spec
    committed = load_committed(root)
    if not committed.present:
        return CheckLeg("spec-drift", ABSENT, committed.note)
    detail: List[str] = [f"{name}: present but unreadable — NOT checked"
                         for name in committed.skipped]
    from .docsync import spec_tests_ungraded
    ungraded = [sp.title for sp in committed.specs if spec_tests_ungraded(sp)]
    from .knowledge.cancel import checkpoint
    for spec in committed.specs:
        checkpoint()
        for f in audit_spec(spec, root):
            detail.append(f"{spec.title} · {f.section}: {f.message}")
    if detail:
        return CheckLeg("spec-drift", "block",
                        f"the committed specs and the code disagree ({committed.note})",
                        detail=detail,
                        unblock=("amend the spec (`mokata spec amend`, which updates the committed "
                                 "copy) or restore the code/test it points at"))
    if ungraded:
        return CheckLeg("spec-drift", ABSENT,
                        f"anchors match, but the tests of {len(ungraded)} spec(s) "
                        f"({', '.join(ungraded)}) were NOT graded: they record no tests — "
                        f"{REEMIT} ({committed.note})")
    return CheckLeg("spec-drift", "pass", f"committed specs match the code ({committed.note})")


# ----------------------------------------------------------------- the check
#: The legs, in the order they run. `legs=` selects a subset by these names.
LEG_NAMES = ("completeness", "spec-awareness", "spec-drift")


def run_ci_check(root: str, changed_files: List[str],
                 changed_symbols: Optional[List[str]] = None,
                 legs: Optional[tuple] = None) -> CICheckResult:
    """Run the completeness + spec-awareness legs over a PR's changed files. Pure of git/network
    (the caller supplies the changed-file list). NEVER raises — an uninitialized/unreadable repo
    degrades to a clean PASS (nothing to check).

    `legs` (0.0.28 F) selects which legs run, by `LEG_NAMES`; None runs them all (the CLI and MCP
    check). The merge gate's spec leg asks for spec-awareness + spec-drift only: completeness is
    P3's question, answered there with the run's own evidence."""
    want = LEG_NAMES if legs is None else tuple(legs)
    unknown_legs = [n for n in want if n not in LEG_NAMES]
    if unknown_legs:
        raise ValueError(f"unknown ci_check leg(s): {', '.join(unknown_legs)}")
    from .config import ConfigError, Surface
    changed_files = list(changed_files or [])
    # A linked worktree holds no `.mokata/` of its own (the manifest is never committed): it is
    # initialized when the repo is (0.0.28 F — the merge's spec leg runs IN the session worktree).
    from .repo_identity import resolve_mokata_root
    if not Surface.is_initialized(root) and resolve_mokata_root(root).root is None:
        return CICheckResult(legs=[], initialized=False)
    try:
        surface = Surface.load(root)
    except (ConfigError, OSError):
        # `Surface.load` raises ConfigError for an absent/invalid manifest (it re-wraps
        # ManifestError) and OSError for an unreadable constitution. An UNLOADABLE surface is
        # reported as uninitialized — the same friendly PASS a repo with no mokata gets.
        return CICheckResult(legs=[], initialized=False)

    symbols = changed_symbols if changed_symbols is not None else (
        symbols_in_files(root, changed_files) if "spec-awareness" in want else [])

    # CANCEL, WITHOUT A TIMER: the same `knowledge.cancel` flag the IDE jobs, the merge suite and
    # the graph walks arm. Between legs, per spec, per changed file and per file of the test walk
    # a checkpoint raises `Cancelled` (a ControlSignal, deliberately not swallowed here: a
    # cancelled check has no verdict, and returning a half-built result would be the partial read
    # shown as complete). Outside a job every checkpoint is a no-op, so the CLI is unchanged.
    from .knowledge.cancel import checkpoint
    run = {"completeness": lambda: _completeness_leg(root, surface.state),
           "spec-awareness": lambda: _spec_awareness_leg(surface, changed_files, symbols),
           "spec-drift": lambda: _committed_specs_leg(root)}
    done = []
    for name in LEG_NAMES:
        if name not in want:
            continue
        checkpoint()
        done.append(run[name]())
    checkpoint()
    from .run_record import load_for_pr
    return CICheckResult(legs=done, initialized=True, records=load_for_pr(root, changed_files),
                         root=root)


# ----------------------------------------------------------------- Ctrl-C in the CLI
@contextmanager
def cancel_on_sigint() -> Iterator[Any]:
    """Arm `knowledge.cancel` for a CLI command (0.0.28 F): Ctrl-C — SIGINT, or CTRL_C_EVENT on
    Windows, which Python delivers as SIGINT — SETS the flag instead of raising mid-write, and the
    next checkpoint raises `Cancelled`, so a half-done check records nothing. The handler is
    installed only on the main thread (where Python allows one) and the previous one is restored."""
    import signal
    import threading
    from .knowledge import cancel as C
    flag = threading.Event()
    on_main = threading.current_thread() is threading.main_thread()
    prev = signal.getsignal(signal.SIGINT) if on_main else None
    if on_main:
        signal.signal(signal.SIGINT, lambda _signum, _frame: flag.set())
    try:
        with C.scope(flag):
            yield flag
    finally:
        if on_main:
            signal.signal(signal.SIGINT, prev if prev is not None else signal.default_int_handler)


#: What a cancelled CLI check prints; the exit status is 2, "did not run" (as `REFUSED_EXIT`).
CANCELLED_TEXT = "cancelled — nothing recorded"
CANCELLED_EXIT = 2
