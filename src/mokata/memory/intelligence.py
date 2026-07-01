"""Stage 59 — memory intelligence (the retention moat): explainable retrieval + health nudge.

The longer you use mokata, the smarter and more trustworthy its memory should feel. This
module adds two READ-ONLY, deterministic touches over the existing memory primitives — it
rebuilds NONE of them:

  * EXPLAINABLE RETRIEVAL — `why_surfaced(query, item)` (and the `explain_recall` pairing /
    the `RetrievalHit.explain` method) gives every recall hit a short, frugal "why it
    surfaced" phrase: which query token matched, whether a graph anchor or a semantic
    neighbour pulled it in, and its kind. Pure + deterministic (no LLM, no wall-clock); ONE
    short phrase per hit, so the JIT frugality bound (top-k, no corpus dump) still holds.

  * MEMORY-HEALTH NUDGE — `assess_health` / `MemoryHealth.nudge()` turns the existing
    self-healing detection (stale / contradictory, from `detect_issues`) and the C8
    read/write ratio (the UNUSED-memory signal) into ONE actionable line that points at the
    GATED review path (`mokata memory` / `mokata govern`). It SURFACES only — it NEVER edits
    or prunes memory (that stays the human-gated `apply_proposal` path). Degrade-clean: a
    healthy store nudges nothing (the line is empty).

The auto-proposed GUARDRAILS (recurring corrections → rule promotion PROPOSALS) are already
the `govern.learning.learn_from_ledger` primitive; Stage 59 only SURFACES those proposals on
the onboard / rules surfaces (proposal-only, human-gated — never auto-added).

Inviolables: read-only + deterministic for retrieval-explain + nudge derivation (no stat
bumps beyond the existing recall instrumentation); proposal-only + human-gated for any memory
edit or rule promotion; frugal/bounded; degrade-clean; core dependency-free; clean-room;
Apache-2.0.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from .healing import CONTRADICTION, STALE
from .item import ALWAYS_ON_KINDS

# A dependency-free word tokenizer matching the lexical-score tokenizer (clean-room copy so
# this module doesn't reach into episodic's privates) — so the matched token we name is
# exactly the token the lexical tier scored on.
_WORD = re.compile(r"[a-z0-9]+")

# How many matched tokens to name in a "why" phrase — frugal: one short phrase, never a list.
_MAX_NAMED_TOKENS = 2


def _tokens(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def _text(item: Any) -> str:
    return f"{item.subject} {item.value}"


# ----------------------------------------------------------------- explainable retrieval
def why_surfaced(query: str, item: Any, *, tiers: Optional[dict] = None) -> str:
    """A short, deterministic "why it surfaced" phrase for one recall hit — frugal (ONE
    phrase). Names the strongest signal that pulled the item in, plus its kind:

        [context] matched "auth"               (lexical keyword overlap — the JIT floor)
        [reference] semantically near          (an embedding neighbour — semantic tier)
        [context] graph-anchored "load_config" (a code-graph anchor — graph tier)
        [guardrail] always-on (project rule)   (an always-on rule/guardrail, no query signal)

    `tiers` is the optional per-tier score dict from a `RetrievalHit` (lexical/graph/semantic);
    without it (a bare `jit_recall` MemoryItem) the phrase falls back to the matched token.
    Read-only; computes nothing durable."""
    kind = getattr(item, "effective_kind", "") or "memory"
    matched = sorted(_tokens(query) & _tokens(_text(item)))

    if tiers:
        sem = float(tiers.get("semantic", 0.0) or 0.0)
        grp = float(tiers.get("graph", 0.0) or 0.0)
        lex = float(tiers.get("lexical", 0.0) or 0.0)
        # Name the dominant tier (semantic strongest, then graph), lexical as the floor.
        if sem > 0.0 and sem >= grp and sem >= lex:
            return f'[{kind}] semantically near your query'
        if grp > 0.0 and grp >= lex:
            anchor = f' "{matched[0]}"' if matched else ""
            return f'[{kind}] graph-anchored{anchor}'

    if matched:
        named = '" / "'.join(matched[:_MAX_NAMED_TOKENS])
        return f'[{kind}] matched "{named}"'
    # No query signal at all (e.g. an always-on rule injected, not recalled by a query).
    if kind in ALWAYS_ON_KINDS:
        return f'[{kind}] always-on (project {kind})'
    return f'[{kind}] relevant to your query'


@dataclass
class RecallExplanation:
    """One recall hit paired with its short, deterministic "why it surfaced" phrase."""
    item: Any
    why: str

    def line(self) -> str:
        """`- <subject>: <value>  ↳ <why>` — the hit rendered WITH its reason (frugal)."""
        return f"- {self.item.subject}: {self.item.value}  ↳ {self.why}"


def explain_recall(query: str, results: List[Any]) -> List[RecallExplanation]:
    """Pair each recall result with its "why it surfaced" phrase. Accepts BOTH a list of
    `RetrievalHit` (from `recall_relevant` — uses its per-tier scores) and a list of bare
    `MemoryItem` (from `jit_recall` — lexical floor). Read-only + deterministic; preserves
    the input order/bound (no extra items — the JIT frugality bound is the caller's top-k)."""
    out: List[RecallExplanation] = []
    for r in results:
        if hasattr(r, "item") and hasattr(r, "tiers"):       # a RetrievalHit
            item, tiers = r.item, r.tiers()
        else:                                                # a bare MemoryItem
            item, tiers = r, None
        out.append(RecallExplanation(item=item, why=why_surfaced(query, item, tiers=tiers)))
    return out


# ----------------------------------------------------------------- memory-health nudge
# The read/write ratio is the UNUSED-memory signal (C8): when more is captured than is ever
# recalled (reads < writes), the surplus writes are memory that isn't earning its keep. This
# is a NUDGE, not a precise per-item count — derived purely from the existing C8 counters.
UNUSED_FLOOR_RATIO = 1.0


def _unused_count(reads: int, writes: int) -> int:
    """The UNUSED-memory count from the C8 read/write ratio: writes not yet balanced by a
    recall (writes - reads), or 0 when memory is being read at least as often as written.
    Deterministic; derived only from the existing counters (no new instrumentation)."""
    if writes > 0 and reads < writes:
        return writes - reads
    return 0


@dataclass
class MemoryHealth:
    """A read-only health read of the memory store: the self-healing backlog (stale /
    contradictory) plus the UNUSED-memory count from the C8 ratio. PROPOSAL-ONLY — it points
    at the gated review path; it carries NO power to edit or prune memory."""
    stale: int
    contradictory: int
    unused: int
    reads: int
    writes: int

    @property
    def total_issues(self) -> int:
        return self.stale + self.contradictory + self.unused

    @property
    def healthy(self) -> bool:
        return self.total_issues == 0

    def nudge(self, *, ascii_only: bool = False) -> str:
        """The ONE actionable line — `N stale · M contradictory · K unused — review with
        \\`mokata memory\\` / \\`mokata govern\\`` — or `""` when healthy (degrade-clean / silent).
        It points ONLY at the gated review path; it never edits or prunes memory itself."""
        if self.healthy:
            return ""
        dot = " - " if ascii_only else " · "
        counts = dot.join([
            f"{self.stale} stale",
            f"{self.contradictory} contradictory",
            f"{self.unused} unused",
        ])
        return (f"mokata · memory health: {counts} — review with `mokata memory` (gated) / "
                f"`mokata govern`; nothing changes until you approve.")


def memory_health(proposals: List[Any], reads: int, writes: int) -> MemoryHealth:
    """Derive a `MemoryHealth` from ALREADY-COMPUTED self-healing proposals + the C8 counters
    — no extra store reads (so a caller that already has both, e.g. the govern view, reuses
    them). Read-only + deterministic."""
    stale = sum(1 for p in proposals if getattr(p, "kind", "") == STALE)
    contradictory = sum(1 for p in proposals if getattr(p, "kind", "") == CONTRADICTION)
    return MemoryHealth(stale=stale, contradictory=contradictory,
                        unused=_unused_count(reads, writes), reads=reads, writes=writes)


def assess_health(store: Any, now: Optional[str] = None) -> MemoryHealth:
    """Assess memory health over a live store — `detect_issues` (read-only; no read-counter
    bump) for the stale/contradictory backlog, plus the C8 `stats` for the unused signal.
    NEVER writes or auto-resolves anything. Degrade-clean: a store with memory off / no items
    yields a healthy (all-zero) read."""
    try:
        proposals = store.detect_issues(now=now)
    except Exception:
        proposals = []
    stats = getattr(store, "stats", None)
    reads = int(getattr(stats, "reads", 0) or 0)
    writes = int(getattr(stats, "writes", 0) or 0)
    return memory_health(proposals, reads, writes)
