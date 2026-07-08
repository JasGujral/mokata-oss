"""TM.S9 — retain-on-success growth (doc 62 §5, "growth-as-process" + mokata P2).

Accumulation is a GOVERNED process, not a new kind: after a procedure succeeds repeatedly,
mokata PROPOSES a new parameterized formula (template + trigger/applicability metadata) for the
human to approve — the CBR Retrieve→Reuse→Revise→Retain "Retain" step / Voyager skill library.
It reuses mokata's existing surface-and-approve engine (the same shape as `govern.learning`
rule-promotion PROPOSALS): count occurrences, and the first time a pattern crosses the threshold,
emit a proposal. It is PROPOSE-ONLY —

  * the tracker holds NO store/backend and CANNOT write; observing a success only counts +
    (at threshold) returns a proposal. There is NO autonomous write path;
  * applying a proposal routes through the store's universal WriteGate (P2) — a declined gate
    writes nothing;
  * HARD RULES NEVER auto-grow (doc 62 §5 — CoALA's "procedural writes are the riskiest"): only
    facts/formulae grow via propose; rules are authored/promoted EXPLICITLY. `assert_growable`
    enforces this, and the proposal builder only ever produces a `formula`, so a rule can never
    slip in through growth.

Pure logic (the store does the gating/ledgering). Clean-room; Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from .formula import make_formula, template_params
from .item import FACT, FORMULA, MemoryItem, governance_kind

# The ONLY kinds growth may auto-propose (doc 62 §5): facts (episodic→semantic distillation) and
# formulae (retain-on-success). A `rule` (incl. a mapped hard GUARDRAIL) is NEVER auto-grown.
GROWABLE_KINDS = (FACT, FORMULA)


def assert_growable(kind: str) -> None:
    """Guard the growth invariant (doc 62 §5): only a fact or a formula may be auto-proposed.
    A governance `rule` (a hard rule / mapped guardrail) is NEVER auto-grown — rules are authored
    or promoted through the explicit, human-gated path (`store.promote`), never grown. Raises
    AssertionError for a rule kind."""
    gk = governance_kind(kind)
    assert gk in GROWABLE_KINDS, (
        f"growth is propose-only for facts/formulae; a '{gk}' rule is never auto-grown — "
        "rules are authored/promoted explicitly (doc 62 §5)"
    )


# ---------------------------------------------------------------- the proposal
@dataclass
class FormulaProposal:
    """A PROPOSED formula (never yet written): the parameterized template + its trigger/topic
    applicability metadata + named params, plus why it was proposed (occurrences/rationale)."""

    key: str
    subject: str
    template: str
    triggers: List[str] = field(default_factory=list)
    params: List[str] = field(default_factory=list)
    topic: str = ""
    occurrences: int = 0
    rationale: str = ""

    def to_item(self, **kw: Any) -> MemoryItem:
        """Materialize the proposal as a formula `MemoryItem` (kind=formula) — the FORMULA kind
        is fixed here, so a proposal can never become a rule. Extra kwargs (scope_level/scope_id/
        id/author…) pass through to `make_formula`, so the approved formula lands in the right
        scope. Asserts the growable invariant defensively."""
        assert_growable(FORMULA)
        return make_formula(self.subject, self.template, triggers=self.triggers,
                            topic=self.topic,
                            params=self.params or template_params(self.template), **kw)

    def render(self) -> str:
        """The surface-and-approve line — show the proposed formula, default to NO write."""
        tail = f"  (params: {', '.join(self.params)})" if self.params else ""
        return (f"mokata · propose to RETAIN a formula [{self.subject}]: {self.template}{tail}\n"
                f"  why: {self.rationale}\n"
                f"Nothing is stored unless you approve.")


# ---------------------------------------------------------------- retain-on-success tracker
class RetainOnSuccess:
    """Counts successful procedures and PROPOSES a formula once a pattern crosses the threshold —
    propose-only (the same shape as `govern.learning.RulesLearner`). Holds no store/backend: it
    cannot write, so there is no autonomous procedural write path."""

    def __init__(self, threshold: int = 3, ledger: Any = None) -> None:
        self.threshold = threshold
        self._counts: Dict[str, int] = {}
        self._proposed: Set[str] = set()
        self.proposals: List[FormulaProposal] = []
        self._ledger = ledger

    def observe_success(self, key: str, *, subject: str, template: str,
                        triggers: Optional[Sequence[str]] = None,
                        topic: str = "",
                        params: Optional[Sequence[str]] = None
                        ) -> Optional[FormulaProposal]:
        """Record one SUCCESSFUL run of the procedure `key`. Returns a `FormulaProposal` the FIRST
        time `key` reaches the threshold (propose-only — it never writes a formula); `None`
        otherwise. Repeated calls after the proposal do not re-propose."""
        self._counts[key] = self._counts.get(key, 0) + 1
        n = self._counts[key]
        if n < self.threshold or key in self._proposed:
            return None
        self._proposed.add(key)
        proposal = FormulaProposal(
            key=key, subject=subject, template=template,
            triggers=[str(t) for t in (triggers or []) if str(t).strip()],
            params=list(params) if params is not None else template_params(template),
            topic=topic or "",
            occurrences=n,
            rationale=f"procedure succeeded {n} times (>= threshold {self.threshold}) — "
                      f"propose retaining it as a reusable formula",
        )
        self.proposals.append(proposal)
        if self._ledger is not None:
            self._ledger.record("formula_proposed", pattern=key, occurrences=n,
                                subject=subject, template=template)
        return proposal


# ---------------------------------------------------------------- apply through the gate (P2)
def apply_formula_proposal(store: Any, proposal: FormulaProposal, decision: str,
                           confirm: Optional[Callable[[str], bool]] = None,
                           assume_yes: bool = False, **item_kw: Any) -> Any:
    """Apply a formula proposal through the store's universal WriteGate (P2) — the ONLY write path.
    `reject`/`defer` write nothing and never open the gate; `approve`/`edit` build the formula item
    and route it through `store.remember` (secret-scan + human gate + audit ledger). A declined
    gate returns a not-committed `WriteResult` — nothing is written. Extra kwargs pass to
    `to_item` (scope/id/author). No autonomous path: the formula reaches the backend ONLY here,
    behind the gate."""
    from .store import WriteResult
    if decision in ("reject", "defer"):
        if getattr(store, "_ledger", None) is not None:
            store._ledger.record("formula_decision", pattern=proposal.key,
                                 decision=decision, added=False)
        return WriteResult(None, committed=False, aborted=True,
                           message=f"{decision}: no change")
    if decision not in ("approve", "edit"):
        raise ValueError(f"unknown decision '{decision}'")
    item = proposal.to_item(**item_kw)
    return store.remember(item, confirm=confirm, assume_yes=assume_yes)
