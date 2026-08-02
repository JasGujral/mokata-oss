"""DB.S5 — memory decay & lifecycle: usage signals, bi-temporal validity, size budgets.

THE ONE definition of the v4 lifecycle semantics. Every consumer — the two SQL backends, the
fusion in `tiered.py`, the archival sweep, the store — reads its column names, its scoring
functions and its budgets from HERE, so no two of them can disagree about what "cold" means or
what a closed validity window looks like.

Two policy invariants govern everything in this module, and they are not negotiable:

  * **NEVER DELETE.** An item leaving the working set CLOSES a validity window (`valid_to` is
    set); it is not row-deleted. History is retained, and a closed item is still readable,
    auditable and re-openable. This is Graphiti's bi-temporal model applied to mokata's store:
    `valid_from`/`valid_to` say WHEN THE FACT WAS TRUE, alongside the `created_at` provenance
    that already says when we LEARNED it. `expires_at` (the pre-existing one-sided TTL) is a
    different axis and is left exactly as it was — it drives C5 staleness detection, not
    validity.
  * **PROPOSE, DON'T ACT.** A budget sweep PROPOSES; it never evicts. The proposal rides the
    existing consolidation propose/review path (`consolidation.ARCHIVE` → `store.apply_consolidation`
    → the WriteGate), so archival is human-gated exactly like every other durable memory change
    (P2). There is no second proposal system and no autonomous archival path.

The ONE thing that writes without a proposal is the usage telemetry (`hit_count` /
`last_recalled_at`) — transient RUN-STATE per the D5 policy, on the read path, and therefore
degrade-clean by construction: see `store.record_usage`, which swallows any telemetry failure so
a recall never fails because its bookkeeping did.

WHY THE TWO HALVES ARE STORED DIFFERENTLY, because it is the load-bearing design decision here:

  * **validity** (`valid_from`/`valid_to`) is ITEM SEMANTICS. It lives on the DOC (like `scope`,
    `review`, `about_code`) and is PROJECTED into columns by the write path, so it survives an
    Obsidian vault, the native client, an export and a teammate's read — and so closing a window
    is a governed, gated, journaled doc write like any other.
  * **usage** (`hit_count`/`last_recalled_at`) is COLUMN-ONLY and never touches the doc. Putting
    it on the doc would make every recall rewrite an approved memory: it would collide with the
    D6 write-refusal, enter the WriteGate, and journal a team CAS write — turning a read into a
    governed write that can fail the read. A bare `UPDATE … SET hit_count = hit_count + 1` on the
    row touches no approved content and can be dropped on the floor when it fails. A backend with
    no columns (Obsidian, native) simply has no usage signal, and the fusion falls back to its
    three original terms — which is exactly the back-compat floor.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .item import EPISODIC, now_iso

# ------------------------------------------------------------------ the v4 columns
# The SCHEMA v4 column names, in one place — the SQLite DDL, the SQLite idempotent ADD COLUMN,
# `teamdb.provision_sql`'s ALTER … IF NOT EXISTS block and every read of them all name the column
# through these constants. (The team-side twins in `teamdb` alias these, so a rename here cannot
# leave one engine on the old name.)
LAST_RECALLED_AT_COLUMN = "last_recalled_at"
HIT_COUNT_COLUMN = "hit_count"
VALID_FROM_COLUMN = "valid_from"
VALID_TO_COLUMN = "valid_to"

# The doc keys the validity half round-trips through (the usage half has NO doc key — see above).
VALID_FROM_KEY = "valid_from"
VALID_TO_KEY = "valid_to"


@dataclass(frozen=True)
class UsageSignal:
    """One item's transient usage telemetry, as read back from the store.

    `hits == 0` is the FRESH state (a never-recalled item, a row mid-migration, or any backend
    with no usage columns at all) and it is the one the back-compat bar is written against: with
    hits at zero BOTH new fusion terms are exactly 0.0, so the fused score is arithmetically
    identical to the pre-DB.S5 three-term score. See `recency_score`/`usage_score`.
    """

    hits: int = 0
    last_recalled_at: Optional[str] = None


# ------------------------------------------------------------------ the decay curve
# The recency half-life, in days: an item recalled HALF_LIFE_DAYS ago scores 0.5, twice that ago
# 0.25, and so on. A half-life (rather than a cliff or a linear ramp) because decay should be
# gradual and never reach zero — an old-but-real memory keeps a small, honest contribution instead
# of falling off an edge on an arbitrary birthday. 30 days is one working month: work touched this
# month ranks visibly above work touched last quarter, which is the distinction a developer
# actually makes.
RECENCY_HALF_LIFE_DAYS = 30.0

# The usage saturation constant, in hits. `hits / (hits + K)` is monotone, bounded in [0, 1) and
# has SHARPLY diminishing returns: 1 hit scores 0.17, 5 hits 0.5, 20 hits 0.8. That shape is the
# point — the interesting signal is "has this ever been useful", not "has it been useful 500
# times". An unbounded count would let one hot item's usage term dwarf every content signal in the
# fusion, which is precisely how a ranking stops being about the query.
USAGE_SATURATION_HITS = 5.0

_SECONDS_PER_DAY = 86400.0


def parse_iso(value: Any) -> Optional[datetime]:
    """An ISO-8601 timestamp as an aware datetime, or None for anything unparseable.

    Never raises, and that is deliberate rather than lazy: these values come from a DB column, a
    hand-editable doc and a teammate's client, so a malformed one must degrade to "no signal"
    (score 0.0 — the back-compat floor) instead of failing a recall. A naive timestamp is read as
    UTC, matching `item.now_iso`, so mixed-offset stores compare correctly.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def recency_score(signal: UsageSignal, created_at: str = "",
                  now: Optional[str] = None) -> float:
    """The RECENCY term in [0, 1] — how recently this item was USED.

    Measured off `last_recalled_at`, falling back to `created_at` when the item has hits but no
    recall timestamp (a store whose telemetry write half-landed, or a signal supplied by a
    backend that counts without stamping). The fallback is why `created_at` is a parameter at all;
    it is never the PRIMARY reference.

    **0.0 for an item with no hits, unconditionally, and that is the back-compat contract**, not
    an implementation detail. Scoring a never-recalled item off `created_at` would give EVERY row
    in a fresh v4 store a non-zero recency term, which would re-order results that the pre-DB.S5
    fusion ranked purely on content — the exact regression the "byte-identical with the new
    signals absent" bar forbids. A brand-new v4 store therefore ranks precisely as it did before
    this stage, and the recency term switches on for an item only once that item has actually been
    recalled.

    A future timestamp (clock skew across a team) clamps to 1.0 rather than exceeding it, so the
    term can never escape the range the weight assumes.
    """
    if signal.hits <= 0:
        return 0.0
    ref = parse_iso(signal.last_recalled_at) or parse_iso(created_at)
    ts = parse_iso(now) or datetime.now(timezone.utc)
    if ref is None:
        return 0.0
    age_days = (ts - ref).total_seconds() / _SECONDS_PER_DAY
    if age_days <= 0:
        return 1.0
    return float(0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))


def usage_score(signal: UsageSignal) -> float:
    """The USAGE term in [0, 1) — how often this item has been recalled, saturating.

    0.0 at zero hits (the back-compat floor, see `recency_score`), and bounded above by 1 for any
    count, so a pathologically hot item contributes a bounded boost rather than swamping the
    content tiers. A negative count (a corrupt column) reads as zero, never as a negative boost.
    """
    hits = max(0, int(signal.hits or 0))
    if hits <= 0:
        return 0.0
    return hits / (hits + USAGE_SATURATION_HITS)


# ------------------------------------------------------------------ bi-temporal validity
def is_open(item: Any) -> bool:
    """True when `item`'s validity window is still OPEN (no `valid_to`).

    Duck-typed, and absence reads as OPEN: every item written before v4 has no `valid_to` at all,
    and those items are unambiguously still valid — reading a missing window as closed would
    retire the entire pre-v4 corpus on upgrade.
    """
    return not (getattr(item, VALID_TO_KEY, None) or "")


def open_window(item: Any) -> str:
    """The `valid_from` an item should carry: its own, else its `created_at` provenance.

    The fallback is what makes the v4 migration free — a pre-v4 item's window opened when the item
    was created, so `created_at` IS its `valid_from` and no write is needed to say so. The backfill
    projects the same value into the column.
    """
    return (getattr(item, VALID_FROM_KEY, None) or "") or getattr(item, "created_at", "") or ""


def close_window(item: Any, now: Optional[str] = None) -> Any:
    """CLOSE `item`'s validity window in place — the ONLY retirement operation in this module.

    Sets `valid_to` (and `valid_from`, if the item never carried one, so the closed window is a
    complete interval rather than a half-open one). It does NOT delete, does not clear the value,
    and does not touch provenance: everything the human approved survives, and the item stays
    readable and re-openable. Re-closing an already-closed window is a no-op — the FIRST close is
    the true one, and overwriting it would rewrite history to say the fact stopped being true later
    than it did.

    The caller is responsible for persisting the item through the gated write path; this function
    is pure mutation on the object and writes nothing itself.
    """
    if not is_open(item):
        return item
    stamp = now or now_iso()
    if not (getattr(item, VALID_FROM_KEY, None) or ""):
        setattr(item, VALID_FROM_KEY, open_window(item))
    setattr(item, VALID_TO_KEY, stamp)
    return item


# ------------------------------------------------------------------ size budgets (doc 62)
# Per-SCOPE × per-TYPE working-set budgets: the number of ACTIVE items of one memory type, at one
# scope level, beyond which a sweep starts PROPOSING that the coldest be archived. Not a hard cap
# and not enforced at write time — nothing is ever refused or evicted because of a budget; it is
# purely the trigger for a human-gated proposal.
#
# Shaped per scope because the scopes mean different things: `global`/`team` memory is small,
# curated and shared (a large team corpus is a governance problem long before it is a size one),
# while `personal` memory is the working scratchpad and is expected to grow. Shaped per type
# because EPISODIC turns arrive at conversational volume while persistent facts and decisions
# arrive at human-decision volume — one budget for both would either strangle the facts or never
# fire on the turns.
_EPISODIC_BUDGETS: Dict[str, int] = {
    "global": 200, "team": 400, "project": 1000, "category": 1000, "personal": 2000,
}
_DURABLE_BUDGETS: Dict[str, int] = {
    "global": 100, "team": 200, "project": 500, "category": 500, "personal": 1000,
}

# The floor for a scope level nobody anticipated (a future level, or a corrupt column). The
# NARROWEST budget would be the wrong default — a bigger budget proposes LESS, so an unrecognised
# scope errs toward proposing nothing rather than toward proposing the retirement of items whose
# scope we could not even read. Unknown is not permission, in the archival direction too.
DEFAULT_BUDGET = 2000


def budget_for(scope_level: str, mtype: str) -> int:
    """The working-set budget for one (scope level, memory type) bucket — see the tables above."""
    table = _EPISODIC_BUDGETS if mtype == EPISODIC else _DURABLE_BUDGETS
    return table.get(scope_level, DEFAULT_BUDGET)


def coldness_key(item: Any, signal: UsageSignal, now: Optional[str] = None) -> Tuple:
    """The total, deterministic order in which items are considered for archival — COLDEST FIRST.

    Cold is decided by exactly the two signals DB.S5 introduces, in the order that matters:
    fewest hits first (never-used before rarely-used), then least-recently-used, and only then the
    creation stamp + id as tiebreaks. The last two are not cosmetic: without them two items with
    identical usage would order arbitrarily, and a sweep would propose a DIFFERENT set on every
    run over an unchanged store — the one property that would make a human-gated proposal
    impossible to review.

    PINNED ITEMS ARE NOT SORTED HERE — they are excluded before the sort (`propose_archival`),
    because a pin is a floor the precedence engine treats as un-overridable and archiving one
    would silently remove a floor the human deliberately set.
    """
    return (usage_score(signal),
            recency_score(signal, getattr(item, "created_at", ""), now),
            getattr(item, "created_at", "") or "",
            getattr(item, "id", "") or "")


@dataclass(frozen=True)
class BudgetOverage:
    """One (scope, type) bucket that is over its budget, and by how much."""

    scope_level: str
    mtype: str
    count: int
    budget: int

    @property
    def excess(self) -> int:
        return max(0, self.count - self.budget)


def bucket_of(item: Any) -> Tuple[str, str]:
    """The (scope level, memory type) bucket an item is budgeted in."""
    from .scope import DEFAULT_SCOPE_LEVEL
    return (getattr(item, "scope_level", "") or DEFAULT_SCOPE_LEVEL,
            getattr(item, "mtype", "") or "")


def find_overages(items: Sequence[Any]) -> List[BudgetOverage]:
    """The buckets over budget among `items`, deterministically ordered (largest excess first).

    Counts only what is passed in: the caller supplies the ACTIVE, still-open working set, so a
    superseded, stale or already-archived item is not counted against a budget it has already left.
    """
    counts: Dict[Tuple[str, str], int] = {}
    for it in items:
        key = bucket_of(it)
        counts[key] = counts.get(key, 0) + 1
    over = [BudgetOverage(scope, mtype, n, budget_for(scope, mtype))
            for (scope, mtype), n in counts.items()
            if n > budget_for(scope, mtype)]
    over.sort(key=lambda o: (-o.excess, o.scope_level, o.mtype))
    return over


def coldest_over_budget(items: Sequence[Any],
                        usage: Optional[Dict[str, UsageSignal]] = None,
                        now: Optional[str] = None
                        ) -> List[Tuple[BudgetOverage, List[Any]]]:
    """For every over-budget bucket, the COLDEST items that would bring it back to budget.

    Pure and read-only — it selects; it proposes nothing and writes nothing. The caller
    (`consolidation.propose_archival`) turns each selection into a human-gated proposal.

    An item whose window is already CLOSED is neither counted nor selected — it has already left
    the working set, so it consumes no budget and cannot be archived twice.

    A PINNED item is COUNTED but never SELECTED, and the asymmetry is deliberate. It is counted
    because it genuinely occupies the working set, and pretending otherwise would let a bucket
    full of pins sit silently over budget forever. It is never selected because a pin is a
    deliberate un-overridable floor (doc 62 §3): retiring one would revoke a decision the human
    made explicitly, in the name of a size heuristic. A bucket whose excess is made up entirely of
    pins therefore proposes nothing — the honest outcome, since there is nothing this sweep may
    offer to do about it.
    """
    signals = usage or {}
    live = [it for it in items if is_open(it)]
    by_bucket: Dict[Tuple[str, str], List[Any]] = {}
    for it in live:
        if not getattr(it, "pin", False):
            by_bucket.setdefault(bucket_of(it), []).append(it)

    out: List[Tuple[BudgetOverage, List[Any]]] = []
    for overage in find_overages(live):
        pool = by_bucket.get((overage.scope_level, overage.mtype), [])
        pool = sorted(pool, key=lambda it: coldness_key(
            it, signals.get(getattr(it, "id", ""), UsageSignal()), now))
        cold = pool[:overage.excess]
        if cold:
            out.append((overage, cold))
    return out
