"""C7 — consolidation pass (PROPOSAL-ONLY).

A periodic pass that *proposes* consolidations over memory — merge duplicates, summarize
episodic clusters, prune already-stale items — and **never commits**. Each proposal is
surfaced as an old → new diff for the human to approve / edit / reject, exactly like the
C5 self-healing flow; the default is no change. This preserves P2 (no autonomous writes);
the riskier autonomous form stays out — application is always human-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from .item import EPISODIC, PERSISTENT, MemoryItem

MERGE = "merge"
SUMMARIZE = "summarize"
PRUNE = "prune"


@dataclass
class ConsolidationProposal:
    kind: str                       # MERGE | SUMMARIZE | PRUNE
    mtype: str
    subject: str
    olds: List[MemoryItem] = field(default_factory=list)
    new: Optional[MemoryItem] = None
    rationale: str = ""

    def diff(self) -> str:
        if self.kind == MERGE:
            return f"{len(self.olds)} identical items -> 1 ({self.olds[0].value!r})"
        if self.kind == SUMMARIZE and self.new is not None:
            return f"{len(self.olds)} turns -> summary {self.new.value!r}"
        if self.kind == PRUNE:
            return f"prune {len(self.olds)} stale item(s) -> removed"
        return f"{self.kind}: {len(self.olds)} item(s)"


def propose_consolidations(active_items: List[MemoryItem],
                           stale_items: Optional[List[MemoryItem]] = None
                           ) -> List[ConsolidationProposal]:
    """Build proposals from the current memory. Pure: reads only, writes nothing."""
    proposals: List[ConsolidationProposal] = []

    # Merge: identical active items (same type + subject + value).
    groups: dict = {}
    for it in active_items:
        groups.setdefault((it.mtype, it.subject, it.value), []).append(it)
    for (mtype, subject, _value), grp in groups.items():
        if len(grp) > 1:
            ordered = sorted(grp, key=lambda g: (g.created_at, g.id))
            proposals.append(ConsolidationProposal(
                MERGE, mtype, subject, olds=ordered, new=ordered[-1],
                rationale=f"{len(grp)} identical active items"))

    # Summarize: a cluster of episodic turns in one session.
    sessions: dict = {}
    for it in active_items:
        if it.mtype == EPISODIC:
            sessions.setdefault(it.subject, []).append(it)
    for session, turns in sessions.items():
        if len(turns) >= 3:
            summary = MemoryItem.create(
                subject=f"summary:{session}",
                value=f"summary of {len(turns)} episodic turns in '{session}'",
                mtype=PERSISTENT, source="consolidation")
            proposals.append(ConsolidationProposal(
                SUMMARIZE, EPISODIC, session, olds=list(turns), new=summary,
                rationale=f"{len(turns)} turns can be summarized into one fact"))

    # Prune: items already marked stale (e.g. by C5) are eligible for removal.
    if stale_items:
        proposals.append(ConsolidationProposal(
            PRUNE, mtype="*", subject="(stale)", olds=list(stale_items), new=None,
            rationale=f"{len(stale_items)} stale item(s) eligible for pruning"))

    return proposals


def render_consolidation(p: ConsolidationProposal) -> str:
    lines = [
        f"mokata · memory consolidation proposed ({p.kind})",
        f"  subject: [{p.mtype}] {p.subject}",
        f"  change:  {p.diff()}",
        f"  why:     {p.rationale}",
        "",
        "Nothing changes unless you act. Choose: approve / edit / reject.",
        "Default is REJECT — consolidation never rewrites memory on its own.",
    ]
    return "\n".join(lines)
