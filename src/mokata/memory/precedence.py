"""TM.S6 — the precedence / merge engine (doc 62 §3). A PURE, deterministic function.

Input  : the UNION items across a read path (each carrying `scope_level`, a `kind`, a `subject`
         used as the conflict KEY, a `value`, an optional `pin` flag, an optional integer
         `priority`, and an `id`).
Output : a `Resolution` — the resolved view for every conflicting key.

Two philosophies, chosen by the item's precedence CLASS (derived from its kind + enforcement):

  * PREFERENCE class  (facts + soft/advisory rules) — "narrowest scope wins".
  * SAFETY class      (HARD rules)                  — "most-restrictive wins, regardless of depth".

TM.S7 makes the class ENFORCEMENT-DRIVEN (doc 62 §4): a governance `rule` is SAFETY iff its
effective enforcement is `hard`, else PREFERENCE; facts and formulae are always PREFERENCE. This
replaces TM.S6's placeholder mapping (which put every RULE/GUARDRAIL in SAFETY). The resolution
ladder below is unchanged — only the class INPUT moved from raw kind to kind × enforcement.

════════════════════════════════════════════════════════════════════════════════════════════
THE RESOLUTION LADDER (the documented, cycle-proof order — applied per conflict KEY)
════════════════════════════════════════════════════════════════════════════════════════════
Depth comes from the linear scope chain (`scope.scope_depth`): global=0 (broadest) …
personal=4 (narrowest). Ordering NEVER relies on dict/list insertion order.

1. SAFETY first. If the key has ANY safety-class (hard) item, that key is AUTHORITATIVE and
   blocks: the most-restrictive item wins REGARDLESS of depth, and no narrower scope can loosen
   it. Every safety item is retained + marked `authoritative`. (The actual in-run BLOCK is
   TM.S8; here we only mark it.) A safety key's resolved value takes precedence over any
   preference item sharing that key.

2. PREFERENCE otherwise, per key:
   a. PIN FLOOR. If any item for the key is pinned, the resolved value is the BROADEST pin's
      value (smallest depth), frozen as an un-overridable floor — narrower scopes cannot
      override it. Several pins → broadest wins (ties broken by step 4).
   b. NARROWEST-WINS MERGE. With no pin, fold the per-depth representatives from BROADEST to
      NARROWEST:
        - object-shaped (dict) values MERGE KEY-BY-KEY, recursing (narrower keys override
          per-key, broader keys are retained);
        - scalar values are OVERRIDDEN WHOLESALE by the narrower scope.

3. Two items at the SAME depth for one key are first reduced to a single representative by the
   tiebreak (step 4) before the depth-fold.

4. TIEBREAK (deterministic, total order — used everywhere a single winner is needed):
        higher integer `priority`  →  DENY-OVERRIDES (the more-restrictive item: a pin in the
        preference class; every safety item is already a deny)  →  stable by smallest `id`.

Determinism: every grouping/selection uses these total orders, so the same input set yields the
same output irrespective of input ordering (property-tested).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .item import HARD, RULE, effective_enforcement, governance_kind
from .scope import DEFAULT_SCOPE_LEVEL, scope_depth

# ---------------------------------------------------------------- precedence classes
PREFERENCE = "preference"   # facts / soft+advisory rules → narrowest-wins + pin + key-by-key merge
SAFETY = "safety"           # HARD rules → most-restrictive-wins, authoritative at any depth


def precedence_class(item: Any) -> str:
    """The precedence class for `item` (TM.S7 — enforcement-driven, doc 62 §4): a governance
    `rule` is SAFETY only when its effective enforcement is `hard`; an advisory/soft rule and
    every fact/formula are PREFERENCE. Duck-typed over `kind` / `enforcement`."""
    if governance_kind(getattr(item, "kind", "")) == RULE:
        return SAFETY if effective_enforcement(item) == HARD else PREFERENCE
    return PREFERENCE


# ---------------------------------------------------------------- normalized input row
@dataclass
class _Row:
    key: str
    value: Any
    level: str
    scope_id: str
    pclass: str
    pin: bool
    priority: int
    id: str
    depth: int

    @classmethod
    def of(cls, item: Any) -> "_Row":
        level = getattr(item, "scope_level", "") or DEFAULT_SCOPE_LEVEL
        return cls(
            key=item.subject,
            value=item.value,
            level=level,
            scope_id=getattr(item, "scope_id", "") or "",
            pclass=precedence_class(item),
            pin=bool(getattr(item, "pin", False)),
            priority=int(getattr(item, "priority", 0) or 0),
            id=item.id,
            depth=scope_depth(level),
        )


def _winner(rows: Sequence[_Row]) -> _Row:
    """Pick a single winner among competitors by the tiebreak ladder (step 4): highest integer
    `priority` → DENY-OVERRIDES (the more-restrictive / pinned item) → stable by smallest `id`.
    A total order, so the choice is deterministic regardless of input ordering."""
    best_priority = max(r.priority for r in rows)
    cand = [r for r in rows if r.priority == best_priority]
    if len(cand) > 1:
        best_pin = max(1 if r.pin else 0 for r in cand)
        cand = [r for r in cand if (1 if r.pin else 0) == best_pin]
    return min(cand, key=lambda r: r.id)


# ---------------------------------------------------------------- resolved view
@dataclass
class ResolvedEntry:
    key: str
    value: Any
    scope_level: str
    scope_id: str
    pclass: str
    priority: int = 0
    authoritative: bool = False   # a safety/hard item in force (TM.S8 blocks on it)
    pinned: bool = False          # value is a broader-scope pinned floor
    merged: bool = False          # value was object-merged across scopes
    source_ids: List[str] = field(default_factory=list)


@dataclass
class Resolution:
    """The resolved view. `preferences` maps key→entry (narrowest-wins/pin/merge); `safety`
    maps key→the authoritative representative; `safety_items` is every authoritative hard entry
    (nothing dropped)."""

    preferences: Dict[str, ResolvedEntry] = field(default_factory=dict)
    safety: Dict[str, ResolvedEntry] = field(default_factory=dict)
    safety_items: List[ResolvedEntry] = field(default_factory=list)

    def get(self, key: str) -> Optional[ResolvedEntry]:
        """The effective entry for `key`: the authoritative safety entry when one exists (a hard
        rule cannot be loosened), else the resolved preference entry, else None."""
        if key in self.safety:
            return self.safety[key]
        return self.preferences.get(key)

    def keys(self) -> List[str]:
        return sorted(set(self.preferences) | set(self.safety))

    def is_authoritative(self, key: str) -> bool:
        return key in self.safety


# ---------------------------------------------------------------- object merge (key-by-key)
def merge_values(broad: Any, narrow: Any) -> Any:
    """Merge a broader value under a narrower one (doc 62 §3, finding 3): object-shaped (dict)
    values merge KEY-BY-KEY recursively (narrower overrides per-key, broader keys retained);
    anything else (scalar, list, type mismatch) is overridden WHOLESALE by the narrower value."""
    if isinstance(broad, dict) and isinstance(narrow, dict):
        out = dict(broad)
        for k, v in narrow.items():
            out[k] = merge_values(out[k], v) if k in out else v
        return out
    return narrow


def _is_object_merge(values: Sequence[Any]) -> bool:
    return sum(1 for v in values if isinstance(v, dict)) >= 2


# ---------------------------------------------------------------- the engine
def _resolve_preference(rows: List[_Row]) -> ResolvedEntry:
    key = rows[0].key
    pins = [r for r in rows if r.pin]
    if pins:
        # PIN FLOOR — broadest pin (smallest depth) wins; ties by the standard tiebreak.
        broadest = min(r.depth for r in pins)
        floor = _winner([r for r in pins if r.depth == broadest])
        return ResolvedEntry(key=key, value=floor.value, scope_level=floor.level,
                             scope_id=floor.scope_id, pclass=PREFERENCE, priority=floor.priority,
                             pinned=True, source_ids=[floor.id])

    # NARROWEST-WINS: one representative per depth (tiebreak), then fold broad→narrow.
    depths = sorted({r.depth for r in rows})
    reps = [_winner([r for r in rows if r.depth == d]) for d in depths]
    object_merge = _is_object_merge([r.value for r in reps])
    value: Any = None
    for i, rep in enumerate(reps):
        value = rep.value if i == 0 else merge_values(value, rep.value)
    narrowest = reps[-1]
    return ResolvedEntry(key=key, value=value, scope_level=narrowest.level,
                         scope_id=narrowest.scope_id, pclass=PREFERENCE,
                         priority=narrowest.priority, merged=object_merge and len(reps) > 1,
                         source_ids=[r.id for r in reps])


def _resolve_safety(rows: List[_Row]) -> "tuple[ResolvedEntry, List[ResolvedEntry]]":
    key = rows[0].key
    # MOST-RESTRICTIVE-WINS regardless of depth: every hard item is authoritative + retained.
    # Representative for the resolved view = the tiebreak winner (deny-overrides / priority / id).
    items = [ResolvedEntry(key=key, value=r.value, scope_level=r.level, scope_id=r.scope_id,
                           pclass=SAFETY, priority=r.priority, authoritative=True,
                           source_ids=[r.id])
             for r in sorted(rows, key=lambda r: (r.depth, r.id))]
    rep_row = _winner(rows)
    rep = next(e for e in items if e.source_ids == [rep_row.id])
    return rep, items


def resolve(items: Sequence[Any]) -> Resolution:
    """Resolve the union `items` into the deterministic view described by THE RESOLUTION LADDER
    (module docstring). Pure: no I/O, no dependence on input ordering."""
    rows = [_Row.of(it) for it in items]

    by_key: Dict[str, List[_Row]] = {}
    for r in rows:
        by_key.setdefault(r.key, []).append(r)

    res = Resolution()
    for key in sorted(by_key):                       # deterministic key iteration
        group = by_key[key]
        safety_rows = [r for r in group if r.pclass == SAFETY]
        pref_rows = [r for r in group if r.pclass == PREFERENCE]
        if safety_rows:
            rep, items_out = _resolve_safety(safety_rows)
            res.safety[key] = rep
            res.safety_items.extend(items_out)
        if pref_rows:
            res.preferences[key] = _resolve_preference(pref_rows)
    return res
