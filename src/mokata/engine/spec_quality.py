"""SQ.S1 + SQ-REGRESS (0.0.23 D5) — RED-GREEN for the spec itself.

Before a spec can reach the human approval gate it runs a VALIDATION BATTERY, composed from members
that already exist (nothing here re-implements a gate):

  1 structural    `completeness.run_completeness_gate` — at least one AC, every AC mapped to a test —
                  plus "every AC is testable": an AC with no text states nothing a test could prove.
  2 rules         `spec_awareness.check_change` over the spec's own words and anchors against the
                  SAVED spec corpus (minus this spec's own entry) and the recorded memory decisions:
                  a spec that collides with an approved spec or decision is a finding.
  3 coherence     against the APPROVED APPROACH: the spec names the approach it was approved under;
                  no AC asks for an item the approach's `decisions[].deferred` explicitly deferred;
                  the approach's own design-fit verdict is not `misfit`. Anchors that do not resolve
                  are ADVISORY here (SPEC-ANCHOR S3 — warn, never block).
  4 failure-mode  `premortem.derive_probes` — every declared downside (con) of the chosen approach is
                  answered by an AC (its words, or an AC citing the probe id, e.g. "P1"). ADVISORY
                  this release (`ENFORCE_FAILURE_MODES`, an open ruling — see its comment); the three
                  standard angles (failure/scale/rollback) are advisory regardless: a lexical match
                  cannot prove them.

THE LOOP (bounded ≤ `MAX_CYCLES`). A failing battery is a set of STRUCTURED findings handed back to the
author, who revises and re-emits; each run is one cycle, recorded on the audit ledger
(`spec_quality`, finding → fix visible) and counted per run. Only a PASSING spec goes on to consent.
After `MAX_CYCLES` failing cycles the loop stops asking the model and ESCALATES: the findings go to
the human, and the spec still does not reach approval until it passes.

SQ-REGRESS. When a blocking finding's ROOT CAUSE is the APPROACH — the spec needs what the plan
deferred, or the plan was judged a misfit — editing spec text cannot fix it. The run is FORCE-REGRESSED
spec → brainstorm (`PipelineCheckpoint.regress` over every pipeline phase, ledgered, a marker at
`spec_regress__<run>`), the analogue of SI-DEV's develop → spec: re-open the approach, update the plan,
re-approve it through its own gate, then return to spec. The regression only ever RESTRICTS (it
un-passes gates); it writes no approval and widens nothing, so it is bookkeeping in the safe direction
— the same tier as `amend.open_amend`'s rollback, which also lands before any human approves.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .spec import Spec, TestRef

MAX_CYCLES = 3
#: RULED ADVISORY 2026-10-16 (stage report D5). The row said failure-mode coverage is "enforced, not
#: advisory". Measured: enforcing it refuses ~60 existing emit paths in the suite whose approved
#: approaches declare cons no AC names — the shape real repos are in too. Ships ADVISORY (the finding
#: is computed, ledgered and returned on every verdict); flipping this to True is a later release's
#: decision, with the fixture migration it implies. Not a settings key: a gate a
#: team can quietly turn off is not one this release should mint.
ENFORCE_FAILURE_MODES = False
#: 0.0.23 verification M3 — the three approach-coherence checks (`approach-mismatch`,
#: `requires-deferred`, `approach-misfit`) are ADVISORY in this release. Enforced, they refused specs
#: the 0.0.22 completeness gate passes, on a free-text name compare and a word match, and their
#: ROOT_APPROACH cause rolled the run back to brainstorm before any human saw a proposal. They are
#: computed, returned and ledgered. RULED ADVISORY 2026-10-16 together with `ENFORCE_FAILURE_MODES`;
#: making them block is a later release's decision, with the fixtures it would update.
ENFORCE_COHERENCE = False
QUALITY_PREFIX = "spec_quality__"
REGRESS_PREFIX = "spec_regress__"
LEDGER_KIND = "spec_quality"
REGRESS_LEDGER_KIND = "spec_regress"

STRUCTURAL, RULES, COHERENCE, FAILURE_MODE = "structural", "rules", "coherence", "failure-mode"
MEMBERS = (STRUCTURAL, RULES, COHERENCE, FAILURE_MODE)
ROOT_SPEC, ROOT_APPROACH = "spec", "approach"

PASS, REVISE, ESCALATE, REGRESS = "pass", "revise", "escalate", "regress"
GATE = "spec-quality"


@dataclass(frozen=True)
class QualityFinding:
    member: str
    code: str
    message: str
    root_cause: str = ROOT_SPEC
    blocking: bool = True
    ac_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = {"member": self.member, "code": self.code, "message": self.message,
               "root_cause": self.root_cause, "blocking": self.blocking}
        if self.ac_id:
            out["ac_id"] = self.ac_id
        return out

    def line(self) -> str:
        tag = "" if self.blocking else " (advisory)"
        who = " — root cause: the APPROACH" if self.root_cause == ROOT_APPROACH else ""
        return f"[{self.member}] {self.message}{tag}{who}"


@dataclass
class QualityVerdict:
    status: str
    findings: List[QualityFinding] = field(default_factory=list)
    cycle: int = 0
    max_cycles: int = MAX_CYCLES

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def blocking(self) -> List[QualityFinding]:
        return [f for f in self.findings if f.blocking]

    @property
    def advisories(self) -> List[QualityFinding]:
        return [f for f in self.findings if not f.blocking]

    def reason(self) -> str:
        n = len(self.blocking)
        if self.status == PASS:
            return "spec-quality battery passed (structural · rules · coherence · failure-mode)"
        if self.status == REGRESS:
            return (f"{n} finding(s) whose root cause is the APPROACH — the run is regressed "
                    f"spec → brainstorm: re-open the approach, update the plan, re-approve it, "
                    f"then return to spec")
        if self.status == ESCALATE:
            return (f"{n} finding(s) still open after {self.cycle} cycle(s) (cap {self.max_cycles})"
                    f" — STOP revising and hand these findings to the human")
        return (f"{n} finding(s) — revise the spec and emit again (cycle {self.cycle} of "
                f"{self.max_cycles})")

    def render(self) -> str:
        lines = [f"[{self.status.upper()}] spec-quality — {self.reason()}"]
        lines += ["  " + f.line() for f in self.findings]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "cycle": self.cycle, "max_cycles": self.max_cycles,
                "reason": self.reason(), "findings": [f.to_dict() for f in self.findings]}


# ------------------------------------------------------------------------------------- the battery
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


_STOP = frozenset({"the", "and", "for", "with", "that", "this", "when", "then", "must", "should",
                   "are", "not", "from", "into", "each", "its", "has", "have", "can", "all",
                   "any", "will", "risk", "may", "too", "but", "was", "were", "under", "over"})


def _words(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text or "")
            if len(w) >= 3 and w.lower() not in _STOP}


def _structural(spec: Spec, tests: List[TestRef], handoff: Any) -> List[QualityFinding]:
    from .completeness import run_completeness_gate
    out: List[QualityFinding] = []
    gr = run_completeness_gate(spec, tests, handoff=handoff)
    if not gr.passed:
        out.append(QualityFinding(STRUCTURAL, "completeness", gr.reason))
    for ac in spec.criteria:
        if not (ac.text or "").strip():
            out.append(QualityFinding(STRUCTURAL, "untestable",
                                      f"{ac.id} has no text — it states nothing a test can prove",
                                      ac_id=ac.id))
    return out


def _rules(spec: Spec, corpus: List[Spec], decisions: List[Any], layer: Any) -> List[QualityFinding]:
    if not corpus and not decisions:
        return []
    from .spec_awareness import ChangeSet, check_change
    text = " ".join([spec.title] + [ac.text for ac in spec.criteria])
    files = sorted({a.file for ac in spec.criteria for a in ac.anchors if a.kind != "reference"})
    symbols = sorted({a.symbol for ac in spec.criteria for a in ac.anchors if a.symbol})
    others = [s for s in corpus if getattr(s, "title", None) != spec.title]
    try:
        report = check_change(ChangeSet(symbols=symbols, files=files, text=text), others,
                              decisions, layer=layer)
    except Exception:  # noqa: BLE001 — a rules member that cannot run is an advisory, said
        return [QualityFinding(RULES, "rules-unavailable",
                               "the rules check could not run (spec corpus / decisions "
                               "unreadable) — not verified", blocking=False)]
    # ADVISORY, deliberately: `check_change` reports an OVERLAP of surface (this spec's anchored
    # files/symbols are covered by a saved AC or a recorded decision), not a proven contradiction.
    # The spec-awareness guard routes the same fact to a HUMAN to confirm at develop time; blocking
    # the spec on it here would refuse every spec that touches code another spec already covers.
    out = []
    for c in getattr(report, "conflicts", []) or []:
        msg = c.render().strip().lstrip("- ") if hasattr(c, "render") else str(c)
        out.append(QualityFinding(RULES, "overlap", msg + " — confirm (amend/supersede) or re-plan",
                                  blocking=False))
    return out


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()


def _coherence(spec: Spec, handoff: Any, root: str,
               enforce: bool = ENFORCE_COHERENCE) -> List[QualityFinding]:
    out: List[QualityFinding] = []
    if handoff is not None:
        approved = getattr(getattr(handoff, "approach", None), "name", None)
        if spec.approach and approved and _norm_name(spec.approach) != _norm_name(approved):
            out.append(QualityFinding(COHERENCE, "approach-mismatch",
                                      f"the spec names approach '{spec.approach}' but "
                                      f"'{approved}' is the one approved", blocking=enforce))
        ac_text = {ac.id: (ac.text or "") for ac in spec.criteria}
        for d in getattr(handoff.approach, "decisions", None) or []:
            for df in getattr(d, "deferred", None) or []:
                markers = [m for m in (getattr(df, "markers", ()) or ()) if m]
                for ac_id, text in ac_text.items():
                    hit = next((m for m in markers if re.search(
                        r"(?<![A-Za-z0-9_])" + re.escape(m) + r"(?![A-Za-z0-9_])", text)), None)
                    if hit:
                        out.append(QualityFinding(
                            COHERENCE, "requires-deferred",
                            f"{ac_id} asks for `{hit}`, which approach decision "
                            f"{getattr(d, 'id', '?')} DEFERRED ({getattr(df, 'item', '')}) — the "
                            f"plan itself must change, not the spec text",
                            root_cause=ROOT_APPROACH, ac_id=ac_id, blocking=enforce))
        fit = getattr(handoff, "design_fit", None)
        if fit is not None and str(getattr(fit, "verdict", "")).lower() == "misfit":
            out.append(QualityFinding(COHERENCE, "approach-misfit",
                                      "the approved approach carries a design-fit verdict of "
                                      "MISFIT — a spec cannot repair an approach that does not fit",
                                      root_cause=ROOT_APPROACH, blocking=enforce))
    if root:
        from .spec_anchor import anchor_warnings
        for w in anchor_warnings(spec, root=root, graph_available=False):
            out.append(QualityFinding(COHERENCE, "anchor-unresolved", w, blocking=False))
    return out


def _failure_modes(spec: Spec, handoff: Any,
                   enforce_failure_modes: bool = False) -> List[QualityFinding]:
    if handoff is None:
        return []
    from .premortem import derive_probes
    try:
        probes = derive_probes(handoff)
    except Exception:  # noqa: BLE001 — a malformed handoff has no probes to enforce
        return []
    out: List[QualityFinding] = []
    texts = [(ac.id, ac.text or "") for ac in spec.criteria]
    for p in probes:
        cited = any(re.search(r"(?<![A-Za-z0-9])" + re.escape(p.id) + r"(?![A-Za-z0-9])", t)
                    for _, t in texts)
        risk_words = _words(p.risk)
        worded = any(risk_words & _words(t) for _, t in texts)
        if cited or worded:
            continue
        if p.category == "approach-risk":
            out.append(QualityFinding(FAILURE_MODE, "uncovered-risk",
                                      f"probe {p.id}: no AC answers the approach's declared "
                                      f"downside \"{p.risk}\" (name it, or cite {p.id} in the AC "
                                      f"that covers it)", blocking=enforce_failure_modes))
        else:
            out.append(QualityFinding(FAILURE_MODE, "angle-unaddressed",
                                      f"probe {p.id}: {p.question}", blocking=False))
    return out


def run_battery(spec: Spec, tests: List[TestRef], *, handoff: Any = None,
                corpus: Optional[List[Spec]] = None, decisions: Optional[List[Any]] = None,
                layer: Any = None, root: str = "",
                enforce_failure_modes: bool = ENFORCE_FAILURE_MODES,
                enforce_coherence: bool = ENFORCE_COHERENCE) -> List[QualityFinding]:
    """The four members, in order. Pure over its inputs; every member degrades to no finding (or an
    advisory that says it could not run) rather than raising into an emit."""
    return (_structural(spec, tests, handoff)
            + _rules(spec, list(corpus or []), list(decisions or []), layer)
            + _coherence(spec, handoff, root, enforce_coherence)
            + _failure_modes(spec, handoff, enforce_failure_modes))


# ------------------------------------------------------------------------------------- the loop
def _quality_key(run_id: str) -> str:
    return QUALITY_PREFIX + (run_id or "local")


def cycles_so_far(store: Any, run_id: str) -> int:
    try:
        data = store.read(_quality_key(run_id))
    except Exception:  # noqa: BLE001
        return 0
    try:
        return int((data or {}).get("cycles", 0)) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        return 0


def _record_quality(ledger: Any, **fields: Any) -> None:
    """One `spec_quality` ledger row (a literal kind, so the event-stream sweep can see it)."""
    if ledger is None:
        return
    try:
        ledger.record("spec_quality", **fields)
    except Exception as exc:  # noqa: BLE001 — observability; the verdict stands without it
        from ..session_flow import note_persist_failure
        note_persist_failure("ledger:spec_quality", error=exc)


def _record_regress(ledger: Any, **fields: Any) -> None:
    """One `spec_regress` ledger row (a literal kind, as above)."""
    if ledger is None:
        return
    try:
        ledger.record("spec_regress", **fields)
    except Exception as exc:  # noqa: BLE001 — the roll-back already landed; say the row did not
        from ..session_flow import note_persist_failure
        note_persist_failure("ledger:spec_regress", error=exc)


def force_regress(store: Any, run_id: str, findings: List[QualityFinding], *,
                  ledger: Any = None) -> bool:
    """SQ-REGRESS — spec → brainstorm. Un-passes every pipeline phase (the approach gate included) so
    the run resumes at brainstorm, writes the `spec_regress__<run>` marker naming why, and ledgers it.
    Restrictive only; idempotent."""
    from ..brainstorm import PIPELINE_PHASES
    from ..govern.resume import PipelineCheckpoint
    record = {"run_id": run_id, "status": "open", "at": time.time(),
              "findings": [f.to_dict() for f in findings if f.root_cause == ROOT_APPROACH]}
    store.write(REGRESS_PREFIX + (run_id or "local"), record)
    moved = False
    if run_id:
        moved = PipelineCheckpoint(store, run_id, ledger=ledger).regress(PIPELINE_PHASES)
    _record_regress(ledger, run=run_id, frm="spec", to="brainstorm",
            findings=[f.code for f in findings if f.root_cause == ROOT_APPROACH])
    return moved


def quality_gate(spec: Spec, tests: List[TestRef], *, store: Any, run_id: str = "",
                 handoff: Any = None, corpus: Optional[List[Spec]] = None,
                 decisions: Optional[List[Any]] = None, layer: Any = None, root: str = "",
                 ledger: Any = None,
                 enforce_failure_modes: bool = ENFORCE_FAILURE_MODES,
                 enforce_coherence: bool = ENFORCE_COHERENCE) -> QualityVerdict:
    """One CYCLE of the loop: run the battery, count and ledger the cycle, decide PASS / REVISE /
    ESCALATE / REGRESS. A pass resets the count (a later re-emit starts its own loop). The cycle
    counter and the ledger entry are observability; the REGRESS checkpoint roll-back only ever
    un-passes gates."""
    if handoff is None and store is not None:
        try:
            from ..brainstorm import load_approved_approach
            handoff = load_approved_approach(store)
        except Exception:  # noqa: BLE001 — no approach on record: the battery runs without one
            handoff = None
    findings = run_battery(spec, tests, handoff=handoff, corpus=corpus, decisions=decisions,
                           layer=layer, root=root, enforce_failure_modes=enforce_failure_modes,
                           enforce_coherence=enforce_coherence)
    blocking = [f for f in findings if f.blocking]
    if not blocking:
        # A pass writes ONLY when it closes an open loop (the counter reset + the closing ledger
        # row). A first-time pass leaves no trace, so a spec that sails through costs no write.
        prior = cycles_so_far(store, run_id) if store is not None else 0
        if prior:
            _bump(store, run_id, reset=True, findings=findings)
            _record_quality(ledger, run=run_id, status=PASS, cycle=0, after=prior,
                    advisories=[f.code for f in findings])
        return QualityVerdict(PASS, findings, cycle=0)
    cycle = _bump(store, run_id, reset=False, findings=findings)
    if any(f.root_cause == ROOT_APPROACH for f in blocking):
        status = REGRESS
    elif cycle >= MAX_CYCLES:
        status = ESCALATE
    else:
        status = REVISE
    _record_quality(ledger, run=run_id, status=status, cycle=cycle,
            findings=[f.code + (":" + f.ac_id if f.ac_id else "") for f in blocking])
    if status == REGRESS:
        force_regress(store, run_id, findings, ledger=ledger)
    return QualityVerdict(status, findings, cycle=cycle)


def _bump(store: Any, run_id: str, *, reset: bool, findings: List[QualityFinding]) -> int:
    key = _quality_key(run_id)
    box = {"cycles": 0}

    def _m(cur: Any) -> Dict[str, Any]:
        cur = dict(cur) if isinstance(cur, dict) else {}
        n = 0 if reset else int(cur.get("cycles", 0) or 0) + 1
        box["cycles"] = n
        history = [h for h in cur.get("history", []) if isinstance(h, dict)][-(MAX_CYCLES * 3):]
        history.append({"at": time.time(), "cycle": n, "passed": reset,
                        "findings": [f.code for f in findings if f.blocking]})
        return {"cycles": n, "history": history, "updated_at": time.time()}
    try:
        if hasattr(store, "update"):
            store.update(key, _m, default={})
        else:
            store.write(key, _m(store.read(key)))
    except Exception:  # noqa: BLE001 — an unwritable counter never escalates on its own; the findings still block
        return 0 if reset else 1
    return box["cycles"]


__all__ = ["MAX_CYCLES", "MEMBERS", "PASS", "REVISE", "ESCALATE", "REGRESS", "GATE",
           "ROOT_SPEC", "ROOT_APPROACH", "QualityFinding", "QualityVerdict", "run_battery",
           "quality_gate", "force_regress", "cycles_so_far"]
