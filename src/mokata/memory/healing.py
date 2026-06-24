"""C5 — self-healing, SURFACING form only.

Detection is pure and read-only: it scans active items and returns *proposals*. It NEVER
writes. Resolution (apply) is human-gated and lives in the store. The whole design
defaults to no change unless the user explicitly approves or edits — memory is never
silently rewritten. Autonomous consolidation (C7) is deliberately out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .item import MemoryItem, now_iso

CONTRADICTION = "contradiction"
STALE = "stale"


@dataclass
class HealingProposal:
    kind: str                       # CONTRADICTION | STALE
    subject: str
    mtype: str
    old: MemoryItem                 # the item proposed to change
    new: Optional[MemoryItem]       # the winning fact (contradiction), or None (stale)
    rationale: str

    def diff(self) -> str:
        if self.kind == CONTRADICTION and self.new is not None:
            return f"{self.old.value!r} -> {self.new.value!r}"
        return f"{self.old.value!r} (active) -> stale"


def detect_issues(active_items: List[MemoryItem],
                  now: Optional[str] = None) -> List[HealingProposal]:
    """Return healing proposals for the given ACTIVE items. Read-only; writes nothing."""
    now = now or now_iso()
    proposals: List[HealingProposal] = []

    # Staleness: an item whose TTL has elapsed.
    for it in active_items:
        if it.expires_at and it.expires_at < now:
            proposals.append(HealingProposal(
                kind=STALE, subject=it.subject, mtype=it.mtype, old=it, new=None,
                rationale=f"valid_for elapsed (expired {it.expires_at})",
            ))

    # Contradiction: two+ active items with the same (type, subject) but different value.
    groups: dict = {}
    for it in active_items:
        groups.setdefault((it.mtype, it.subject), []).append(it)
    for (mtype, subject), grp in groups.items():
        if len({g.value for g in grp}) <= 1:
            continue
        ordered = sorted(grp, key=lambda g: (g.created_at, g.id))
        old, new = ordered[0], ordered[-1]
        proposals.append(HealingProposal(
            kind=CONTRADICTION, subject=subject, mtype=mtype, old=old, new=new,
            rationale="two active facts disagree; newest proposed to supersede oldest",
        ))
    return proposals


# Clean-room surface-and-approve prompt — show the change, default to NO change.
def render_proposal(p: HealingProposal) -> str:
    lines = [
        f"mokata · memory needs your decision ({p.kind})",
        f"  subject: [{p.mtype}] {p.subject}",
        f"  change:  {p.diff()}",
        f"  why:     {p.rationale}",
        "",
        "Nothing changes unless you act. Choose: approve / edit / reject.",
        "Default is REJECT — memory is never rewritten without your say-so.",
    ]
    return "\n".join(lines)
