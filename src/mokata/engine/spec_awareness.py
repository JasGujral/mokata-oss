"""Stage 37 — spec-awareness / regression guard (don't break saved specs).

Before/at implementation, a change is cross-checked against the corpus of SAVED specs (the
Stage 32 emitted spec, plus any archived specs) and DECISION memory: does it TOUCH or
CONTRADICT something already specified or decided? Overlap is computed on the touch-set — the
symbols/files in play, EXPANDED through the knowledge graph (Stage 33 grounding) so a spec about
a caller of the changed code is caught too. On a hit the change is not silently let through: it
is surfaced and routed through the Stage 31 `DeviationGate` (human-gated, logged) — the human
confirms (amend/supersede the old spec) or re-plans.

Degrade-clean (P11): no saved specs/decisions ⇒ a no-op (no false alarm); no graph ⇒ the
touch-set is the literal symbols/files (lexical/file overlap) and the report SAYS SO; only the
touch-set is checked, never the whole corpus per run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..govern.deviation import (
    ACCEPTANCE_CRITERIA,
    DeviationGate,
    DeviationOutcome,
    DeviationRequest,
)
from .spec import Spec
from .spec_gate import load_emitted_spec

# Forward-compat: an optional archive of prior specs (a list of spec dicts) the regression guard
# also checks. Absent today -> the corpus is just the emitted spec; reusing Stage 32 state.
SPEC_CORPUS_KEY = "spec_corpus"

SPEC_CONFLICT_KIND = "spec_conflict"


# --------------------------------------------------------------------------- the change
@dataclass
class ChangeSet:
    """What a change touches: the symbols and files in play (+ an optional free-text note)."""
    symbols: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    text: str = ""


# --------------------------------------------------------------------------- findings
@dataclass
class SpecConflict:
    source_kind: str            # "spec" | "decision"
    ref: str                    # the spec title / decision subject
    where: List[str]            # the touched symbols/files that overlap it
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"source_kind": self.source_kind, "ref": self.ref,
                "where": list(self.where), "detail": self.detail}

    def render(self) -> str:
        return (f"  - affects {self.source_kind} '{self.ref}' "
                f"(via {', '.join(self.where)})"
                + (f" — {self.detail}" if self.detail else ""))


@dataclass
class SpecAwarenessReport:
    conflicts: List[SpecConflict] = field(default_factory=list)
    checked: bool = True        # False when there was nothing to check (no corpus) -> no-op
    degraded: bool = False      # True when the graph was absent (lexical/file overlap only)
    note: str = ""
    touch_set: List[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def render(self) -> str:
        if not self.checked:
            return f"spec-awareness: {self.note}"
        head = "mode: lexical/file overlap (no graph)" if self.degraded \
            else "mode: graph-expanded touch-set"
        if not self.conflicts:
            return f"spec-awareness: no saved spec or decision is affected. ({head})"
        lines = [f"spec-awareness: this change affects {len(self.conflicts)} saved "
                 f"spec(s)/decision(s) — confirm (amend/supersede) or re-plan. ({head})"]
        lines += [c.render() for c in self.conflicts]
        return "\n".join(lines)


# --------------------------------------------------------------------------- corpus loading
def load_spec_corpus(store: Any) -> List[Spec]:
    """The saved-spec corpus from state: the emitted spec + any archived specs. Degrade-clean —
    an absent/unreadable source contributes nothing."""
    specs: List[Spec] = []
    emitted = load_emitted_spec(store)
    if emitted is not None:
        specs.append(emitted)
    if store is not None:
        try:
            arr = store.read(SPEC_CORPUS_KEY)
        except Exception:
            arr = None
        if isinstance(arr, list):
            for d in arr:
                try:
                    specs.append(Spec.from_dict(d))
                except Exception:
                    continue
    return specs


def load_decisions(memory_store: Any) -> List[Any]:
    """Active decision-memory items, or [] when memory is absent/disabled (degrade-clean)."""
    if memory_store is None:
        return []
    try:
        from ..memory import DECISION
        return list(memory_store.all_active(mtype=DECISION))
    except Exception:
        return []


# --------------------------------------------------------------------------- touch-set
def expand_touch_set(layer: Any, symbols: List[str],
                     depth: int = 1) -> Tuple[Set[str], bool]:
    """Expand the changed symbols through the code graph (callers/callees/blast-radius) so a spec
    about impacted code is caught too. Returns (expanded_symbols, degraded). degraded=True when
    there's no real graph -> the set is just the literal symbols (lexical floor)."""
    expanded: Set[str] = {s for s in symbols if s}
    if layer is None or not getattr(layer, "uses_graph", False):
        return expanded, True
    for sym in list(expanded):
        for getter in ("callers", "callees"):
            try:
                res = getattr(layer, getter)(sym)
            except Exception:
                continue
            if res is not None and not res.degraded:
                expanded.update(r.symbol for r in res.references if r.symbol)
        try:
            res = layer.blast_radius(sym, depth=depth)
            if res is not None and not res.degraded:
                expanded.update(r.symbol for r in res.references if r.symbol)
        except Exception:
            pass
    return expanded, False


def _hit(token: str, text: str) -> bool:
    """A whole-word/identifier match of `token` in `text` (case-insensitive) — precise, so a
    generic word doesn't raise a false alarm."""
    if not token:
        return False
    return re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE) is not None


def _file_tokens(path: str) -> List[str]:
    """The path itself + its basename and stem, so 'src/pay.py' matches a spec naming 'pay'."""
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    return [t for t in {path, base, stem} if t]


# --------------------------------------------------------------------------- the check
def check_change(change: ChangeSet, specs: List[Spec], decisions: List[Any],
                 layer: Any = None, depth: int = 1) -> SpecAwarenessReport:
    """Cross-check a change against saved specs + decisions. Pure: no I/O, no gate, no logging."""
    if not specs and not decisions:
        return SpecAwarenessReport(
            checked=False,
            note="no saved specs or decisions yet — nothing to guard (skipped).")

    touch_symbols, degraded = expand_touch_set(layer, change.symbols, depth)
    file_terms: List[str] = []
    for f in change.files:
        file_terms.extend(_file_tokens(f))

    conflicts: List[SpecConflict] = []

    for spec in specs:
        text = (spec.title or "") + "\n" + "\n".join(c.text for c in spec.criteria)
        where = sorted({s for s in touch_symbols if _hit(s, text)}
                       | {f for f in change.files if any(_hit(t, text)
                                                         for t in _file_tokens(f))})
        if where:
            conflicts.append(SpecConflict(
                "spec", spec.title or spec.source or "(untitled spec)", where,
                detail="a saved acceptance criterion covers this surface"))

    for d in decisions:
        text = f"{d.subject}\n{d.value}"
        where = sorted({s for s in touch_symbols if _hit(s, text)}
                       | {f for f in change.files if any(_hit(t, text)
                                                         for t in _file_tokens(f))})
        if where:
            conflicts.append(SpecConflict(
                "decision", d.subject, where,
                detail=f"recorded decision: {d.value}"))

    note = ("graph absent — checked by lexical/file overlap only"
            if degraded else "checked against the graph-expanded touch-set")
    return SpecAwarenessReport(
        conflicts=conflicts, checked=True, degraded=degraded, note=note,
        touch_set=sorted(touch_symbols | set(file_terms)))


# --------------------------------------------------------------------------- the guard
@dataclass
class GuardOutcome:
    proceeded: bool             # True == safe to continue (no conflict, or human-confirmed)
    blocked: bool               # True == a conflict the human did not confirm
    report: SpecAwarenessReport
    deviation: Optional[DeviationOutcome] = None

    def render(self) -> str:
        out = self.report.render()
        if self.deviation is not None:
            out += "\n" + ("[CONFIRMED] " if self.proceeded else "[BLOCKED] ") \
                + self.deviation.reason
        return out


def guard_change(change: ChangeSet, *, specs: List[Spec], decisions: List[Any],
                 layer: Any = None, ledger: Any = None, phase: str = "develop",
                 confirm=None, assume_yes: bool = False,
                 depth: int = 1) -> GuardOutcome:
    """Run the corpus check; on a conflict surface it and route through the DeviationGate
    (human-gated, logged). No conflict / no corpus → proceed cleanly. Never breaks a saved spec
    without surfacing it: a conflict BLOCKS until the human confirms (amend/supersede) or
    re-plans, and both the conflict and the resolution are recorded in the audit ledger."""
    report = check_change(change, specs, decisions, layer=layer, depth=depth)
    if not report.checked or not report.has_conflicts:
        return GuardOutcome(proceeded=True, blocked=False, report=report)

    if ledger is not None:
        # A human-readable WHY (Stage 49): which saved spec(s)/decision(s) this change
        # affects, and where (the overlapping symbols/files) — pulled from the real conflicts.
        reason = "affects " + "; ".join(
            f"{c.source_kind} '{c.ref}' (via {', '.join(c.where)})"
            for c in report.conflicts)
        ledger.record(SPEC_CONFLICT_KIND, phase=phase, degraded=report.degraded,
                      reason=reason, conflicts=[c.to_dict() for c in report.conflicts],
                      touch_set=report.touch_set)

    refs = ", ".join(f"{c.source_kind} '{c.ref}'" for c in report.conflicts)
    req = DeviationRequest(
        what=f"this change affects saved {refs}",
        why=("the touched surface is already specified/decided; proceeding would change "
             "previously-approved behaviour"),
        options=["confirm the change and amend/supersede the affected spec(s)/decision(s)",
                 "re-plan so the change does not break them"],
        target=ACCEPTANCE_CRITERIA, phase=phase)
    outcome = DeviationGate(ledger).submit(req, confirm=confirm, assume_yes=assume_yes)
    return GuardOutcome(proceeded=outcome.approved, blocked=not outcome.approved,
                        report=report, deviation=outcome)
