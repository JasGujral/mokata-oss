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
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..degrade import FAILURE_UNREACHABLE, note_degraded
from ..govern.deviation import (
    ACCEPTANCE_CRITERIA,
    DeviationGate,
    DeviationOutcome,
    DeviationRequest,
)
from .spec import Spec
from .spec_gate import read_emitted_spec

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
    degraded: bool = False      # True when the check did not run at full strength (see below)
    note: str = ""
    touch_set: List[str] = field(default_factory=list)
    # D5 — the INDEPENDENT ways this check can be weaker than it looks. `degraded` is the umbrella
    # (any one), kept as the field every existing caller/ledger row already reads.
    # GR.S3-FU — TWO distinct floor signals, mirroring `brainstorm_impact.ApproachImpact`:
    #   * `floor_degraded` — DISPLAY: the touch-set did not come from a REAL adopted graph (it came
    #     from the embedded AST floor or bare grep, or no layer). Drives the honesty caveat.
    #   * `graph_degraded` — QUERY-LEVEL: the structural answer was WITHHELD (no layer, a faulted
    #     query, or a `blast_radius` that fell to the grep floor). This is the signal the
    #     `graph.required` gate reads — AST answering WITH evidence is floor_degraded but NOT
    #     graph_degraded, so it is not refused (GR.S3 deviation 2 closed).
    graph_degraded: bool = False
    floor_degraded: bool = False   # the touch-set came from a floor (AST/grep), not a real graph
    touch_note: str = ""           # the floor's caveat (e.g. the AST note) — carried, never stripped
    skipped: int = 0               # saved specs/decisions that could NOT be loaded — NOT checked

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def render(self) -> str:
        if not self.checked:
            return f"spec-awareness: {self.note}"
        if self.graph_degraded:
            head = "mode: lexical/file overlap (no graph)"
        elif self.floor_degraded:
            # GR.S3-FU — the AST floor expanded the touch-set structurally: honest that it is a
            # floor (not the adopted graph), carrying its caveat so it is never overclaimed.
            head = "mode: AST-expanded touch-set"
            if self.touch_note:
                head += f" — {self.touch_note}"
        else:
            head = "mode: graph-expanded touch-set"
        if not self.conflicts:
            # D5 — an all-clear that SKIPPED its inputs is not an all-clear. "No saved spec is
            # affected" is a claim about the corpus; a corpus we could not read supports no claim.
            if self.skipped:
                return (f"spec-awareness: INCOMPLETE — {self.skipped} saved spec(s)/decision(s) "
                        f"could NOT be read and were NOT checked. This is NOT an all-clear: a spec "
                        f"you rely on may be affected and unseen. Run `mokata doctor`. ({head})")
            return f"spec-awareness: no saved spec or decision is affected. ({head})"
        lines = [f"spec-awareness: this change affects {len(self.conflicts)} saved "
                 f"spec(s)/decision(s) — confirm (amend/supersede) or re-plan. ({head})"]
        lines += [c.render() for c in self.conflicts]
        if self.skipped:
            lines.append(f"  ! {self.skipped} further saved spec(s)/decision(s) could NOT be read "
                         f"and were NOT checked — there may be more.")
        return "\n".join(lines)


# --------------------------------------------------------------------------- corpus loading
class _Loaded(list):
    """A plain `list` that also remembers how many entries it could NOT load.

    Why a list subclass and not a new return type: `load_spec_corpus` / `load_decisions` feed
    straight into `check_change` at every call site (ci_check, the knowledge CLI, the MCP tool), and
    a corpus that dropped half its entries has to be able to SAY so by the time the report is built.
    A subclass carries that fact through every one of those call sites with no signature change —
    `== []`, `not specs`, iteration and comprehension all behave exactly as before."""

    skipped: int = 0


def load_spec_corpus(store: Any) -> List[Spec]:
    """The saved-spec corpus from state: the emitted spec + any archived specs. Degrade-clean —
    an absent/unreadable source contributes nothing — but a DROPPED entry is COUNTED (`.skipped`),
    because a corpus that silently lost specs still answers "no saved spec is affected", which is
    the one answer it has no right to give."""
    specs = _Loaded()
    skipped = 0
    emitted, malformed = read_emitted_spec(store)
    if emitted is not None:
        specs.append(emitted)
    elif malformed:
        skipped += 1                    # the emitted spec IS there and could not be parsed
    if store is not None:
        try:
            arr = store.read(SPEC_CORPUS_KEY)
        except (OSError, AttributeError):
            # The corpus artifact could not be READ (IO/permissions, or a store double without
            # `read`). Nothing is known about it — count it as ONE unchecked source, not zero.
            arr = None
            skipped += 1
        if isinstance(arr, list):
            for d in arr:
                try:
                    specs.append(Spec.from_dict(d))
                except (KeyError, TypeError, ValueError, AttributeError):
                    skipped += 1        # a corrupt archived spec — dropped, but NOT in silence
                    continue
    specs.skipped = skipped
    return specs


def load_decisions(memory_store: Any) -> List[Any]:
    """Active decision-memory items, or [] when memory is absent/disabled (degrade-clean).

    D5 — a memory store that is WIRED but broken returned zero decisions, and zero decisions from a
    broken store is indistinguishable, downstream, from "this project has recorded no decisions".
    The `[]` fallback stays; `.skipped` now marks that the answer is unknown, not empty."""
    if memory_store is None:
        return _Loaded()                # memory genuinely absent/disabled — an honest empty
    out = _Loaded()
    try:
        from ..memory import DECISION
        out.extend(memory_store.all_active(mtype=DECISION))
    except (OSError, ImportError, sqlite3.Error):
        out.skipped = 1                 # the decision corpus is UNKNOWN, not empty
    return out


# --------------------------------------------------------------------------- touch-set
def expand_touch_set(layer: Any, symbols: List[str],
                     depth: int = 1) -> Tuple[Set[str], bool, bool, str]:
    """Expand the changed symbols through the code graph OR the embedded AST floor (callers /
    callees / blast-radius) so a spec about impacted code is caught too. Returns
    ``(expanded, floor_degraded, graph_degraded, note)``.

    GR.S3-FU — the expansion no longer sits behind the LAYER-level ``uses_graph`` gate, so the AST
    floor's real structural evidence expands the touch-set out of the box (P22). Two INDEPENDENT
    signals, mirroring ``brainstorm_impact.compute_impact`` (Lens-1) so the three decision consumers
    key on the SAME query-level evidence:

      * ``floor_degraded`` (DISPLAY) — the touch-set did not come from a REAL adopted graph: no
        layer, or the layer is a floor (``uses_graph`` False → AST or bare grep). The honesty caveat
        (GR.S1): an AST-expanded touch-set is still flagged a floor and carries the AST note.
      * ``graph_degraded`` (QUERY-LEVEL) — the signal the ``graph.required`` gate reads: True only
        when a structural answer was ATTEMPTED and WITHHELD — no layer (with symbols to expand), a
        query that FAULTED, or a ``blast_radius`` that reached the lexical floor (``qr.degraded``).
        The AST floor answering structurally keeps ``floor_degraded=True`` but
        ``graph_degraded=False`` — so it EXPANDS and is NOT refused. This mirrors Lens-1 exactly:
        ``blast_radius`` is the canonical query the signal keys on.

        D2 — "answering structurally" INCLUDES a verified ZERO. This read "the grep / empty-AST
        fallthrough" and "empty-AST evidence still refuses (the GR.S1 hand-off)", which is the
        defect stated as a contract: a LEAF is not absent evidence. The distinction is made once,
        at the backend, so this consumer needed no change — which is the point, since three
        consumers each carrying their own copy of the rule is how they drifted apart before.

    D5 — a graph wired but FAULTING forces ``graph_degraded=True`` and speaks once via
    ``note_degraded('code-graph')``: a degraded flag that lies is worse than no flag."""
    expanded: Set[str] = {s for s in symbols if s}
    if layer is None:
        # No layer: display-degraded, and query-degraded only if there were symbols whose blast
        # radius went un-answered (an empty change has none — mirrors Lens-1's ``bool(tgts)``).
        return expanded, True, bool(expanded), ""
    # DISPLAY: a floor (AST/grep, ``uses_graph`` False) is degraded for the caveat; a real graph
    # is not. An object without ``uses_graph`` is assumed a real graph (matches Lens-1).
    floor_degraded = getattr(layer, "uses_graph", True) is False
    graph_degraded = False
    faulted = False
    note = ""
    for sym in list(expanded):
        # ``blast_radius`` is the CANONICAL query the query-level signal keys on — exactly the query
        # Lens-1 uses. A degraded (lexical-floor) answer withholds the radius; a structurally verified
        # ZERO does not — see D2 in the docstring above.
        try:
            res = layer.blast_radius(sym, depth=depth)
        except Exception:               # noqa: BLE001 — see the note below
            faulted = True
            res = None
        if res is not None:
            if getattr(res, "degraded", False):
                graph_degraded = True
            else:
                expanded.update(r.symbol for r in res.references if r.symbol)
                if not note and getattr(res, "note", ""):
                    note = res.note     # carry the floor's caveat (the AST note) — never stripped
        # callers/callees WIDEN the expansion; a fault degrades, a floor answer is simply not used.
        for getter in ("callers", "callees"):
            try:
                res = getattr(layer, getter)(sym)
            except Exception:           # noqa: BLE001
                # BROAD ON PURPOSE: a pluggable optional graph backend's error types are not
                # nameable at module scope (neo4j, a custom adapter). No longer silent — the fault
                # forces the lexical-floor flag and is announced once, below.
                faulted = True
                continue
            if res is not None and not res.degraded:
                expanded.update(r.symbol for r in res.references if r.symbol)
                if not note and getattr(res, "note", ""):
                    note = res.note
    if faulted:
        graph_degraded = True           # graph-backed answer withheld: the floor is what ran
        note_degraded(
            "code-graph", FAILURE_UNREACHABLE,
            fallback=("the touch-set fell back to the literal symbols (the lexical floor) — the "
                      "graph did NOT expand it, so a spec about a CALLER of your change may go "
                      "unseen"),
            fix="run `mokata doctor` to check the code-graph backend")
    return expanded, floor_degraded, graph_degraded, note


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
    """Cross-check a change against saved specs + decisions. Pure: no I/O, no gate, no logging.

    D5 — the loaders (`load_spec_corpus` / `load_decisions`) carry a `.skipped` count for inputs
    they could NOT read. It is read here, not re-derived, and it changes what the report is ALLOWED
    to claim: a guard that never saw the corpus cannot report a clean one."""
    skipped = getattr(specs, "skipped", 0) + getattr(decisions, "skipped", 0)

    if not specs and not decisions:
        if skipped:
            # NOT "nothing to guard": there was something to guard and we could not read it.
            return SpecAwarenessReport(
                checked=False, degraded=True, skipped=skipped,
                note=(f"{skipped} saved spec(s)/decision(s) could NOT be read — the regression "
                      f"guard DID NOT RUN. This is NOT an all-clear; a saved spec may be broken by "
                      f"this change and nothing would have caught it. Run `mokata doctor`."))
        return SpecAwarenessReport(
            checked=False,
            note="no saved specs or decisions yet — nothing to guard (skipped).")

    touch_symbols, floor_degraded, graph_degraded, touch_note = expand_touch_set(
        layer, change.symbols, depth)
    # The umbrella `degraded` = ANY weakness vs a full-strength real-graph check: a floor answered
    # (AST/grep), a structural answer was withheld (query-level), or inputs were skipped.
    degraded = floor_degraded or graph_degraded or bool(skipped)
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

    if graph_degraded:
        note = "graph absent — checked by lexical/file overlap only"
    elif floor_degraded:
        note = "checked against the AST-expanded touch-set" + (f" ({touch_note})" if touch_note
                                                               else "")
    else:
        note = "checked against the graph-expanded touch-set"
    if skipped:
        note += (f"; {skipped} saved spec(s)/decision(s) could NOT be read and were NOT checked "
                 f"(this result is INCOMPLETE)")
    return SpecAwarenessReport(
        conflicts=conflicts, checked=True, degraded=degraded, note=note,
        touch_set=sorted(touch_symbols | set(file_terms)),
        graph_degraded=graph_degraded, floor_degraded=floor_degraded, touch_note=touch_note,
        skipped=skipped)


# --------------------------------------------------------------------------- the guard
@dataclass
class GuardOutcome:
    proceeded: bool             # True == safe to continue (no conflict, or human-confirmed)
    blocked: bool               # True == a conflict the human did not confirm
    report: SpecAwarenessReport
    deviation: Optional[DeviationOutcome] = None
    graph_refusal: Optional[str] = None    # GR.S3 — set when a DEGRADED touch-set was refused

    def render(self) -> str:
        if self.graph_refusal is not None:
            # GR.S3 — a degraded touch-set is not a decision input: the refusal IS the output.
            return self.graph_refusal
        out = self.report.render()
        if self.deviation is not None:
            out += "\n" + ("[CONFIRMED] " if self.proceeded else "[BLOCKED] ") \
                + self.deviation.reason
        return out


def guard_change(change: ChangeSet, *, specs: List[Spec], decisions: List[Any],
                 layer: Any = None, ledger: Any = None, phase: str = "develop",
                 confirm=None, assume_yes: bool = False,
                 depth: int = 1, graph_required: bool = False, graph_overridden: bool = False,
                 graph_backend: Any = None, notice: Any = None) -> GuardOutcome:
    """Run the corpus check; on a conflict surface it and route through the DeviationGate
    (human-gated, logged). No conflict / no corpus → proceed cleanly. Never breaks a saved spec
    without surfacing it: a conflict BLOCKS until the human confirms (amend/supersede) or
    re-plans, and both the conflict and the resolution are recorded in the audit ledger.

    GR.S3 — when `graph_required` is on and the touch-set fell to the lexical floor
    (`report.graph_degraded`) with no ledgered override, the whole result is REFUSED as a decision
    input FIRST: a degraded regression guard cannot vouch for a clean corpus. The escape is the
    session-scoped `--allow-degraded` (`graph_overridden`); default-off keeps today's behavior
    byte-identical."""
    report = check_change(change, specs, decisions, layer=layer, depth=depth)
    if graph_required and report.graph_degraded and not graph_overridden:
        from ..govern.graph_required import check_graph_required
        gate = check_graph_required(
            degraded=True, required=True, overridden=False,
            consumer="spec-check (regression guard)", backend=graph_backend,
            mentions=len(report.touch_set), files=len(change.files),
            targets=change.symbols, notice=notice)
        if gate.refused:
            return GuardOutcome(proceeded=False, blocked=True, report=report,
                                graph_refusal=gate.render())
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
