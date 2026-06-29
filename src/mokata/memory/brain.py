"""Stage 36 — the project "brain": surface typed memory by category, frugally.

Typed memory (the `kind` parts on `MemoryItem`) is only useful if it's surfaced the right way:
  - **always-on** (`rule` / `guardrail`) → injected into the SessionStart briefing / rules
    surface EVERY run, but within a hard line budget — if there are more than fit, the most
    relevant are kept and the rest are flagged, NEVER blowing the budget (P11);
  - **JIT** (`context` / `reference` / `best-practice`) → pulled into a skill ONLY when relevant
    to the task in play (top-k by relevance), never the whole corpus (P11).

This module is pure functions over a `MemoryStore`/item list — no I/O, no LLM, dependency-free.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from .episodic import lexical_score
from .item import ALWAYS_ON_KINDS, DEFAULT_TOP_K, JIT_KINDS, MEMORY_KINDS, MemoryItem

# Accept a few natural spellings for a kind so capture (LLM/user) is forgiving; canonical out.
_KIND_ALIASES = {
    "rule": "rule", "rules": "rule",
    "guardrail": "guardrail", "guard-rail": "guardrail", "guard rail": "guardrail",
    "guardrails": "guardrail",
    "best-practice": "best-practice", "best practice": "best-practice",
    "best_practice": "best-practice", "bestpractice": "best-practice",
    "convention": "best-practice", "conventions": "best-practice",
    "context": "context", "domain": "context", "formula": "context",
    "reference": "reference", "ref": "reference", "doc": "reference", "document": "reference",
    "decision": "decision", "episodic": "episodic",
}


def normalize_kind(kind: Optional[str]) -> str:
    """Map a free-form kind to its canonical taxonomy value, or '' if unrecognised."""
    if not kind:
        return ""
    return _KIND_ALIASES.get(kind.strip().lower(), "")


def _text(item: MemoryItem) -> str:
    return f"{item.subject} {item.value}"


def group_by_kind(items: List[MemoryItem]) -> "OrderedDict[str, List[MemoryItem]]":
    """Group items by their effective kind, in taxonomy display order (only non-empty groups).
    Within a group, items are stable-sorted by created_at then subject."""
    buckets: Dict[str, List[MemoryItem]] = {}
    for it in items:
        buckets.setdefault(it.effective_kind, []).append(it)
    ordered: "OrderedDict[str, List[MemoryItem]]" = OrderedDict()
    # known kinds first, in declared order; then any unexpected kinds, name-sorted (defensive)
    for k in MEMORY_KINDS:
        if k in buckets:
            ordered[k] = sorted(buckets.pop(k), key=lambda i: (i.created_at, i.subject))
    for k in sorted(buckets):
        ordered[k] = sorted(buckets[k], key=lambda i: (i.created_at, i.subject))
    return ordered


def _line(item: MemoryItem) -> str:
    return f"- [{item.effective_kind}] {item.subject}: {item.value}"


def always_on_items(store: Any) -> List[MemoryItem]:
    """Active rule/guardrail items (the always-on set), regardless of relevance. Surfacing
    the always-on rules (briefing, rules view, governance dashboard) is automatic injection,
    not a user recall — read via the NON-counting path so it never moves the read counter or
    mutates durable stats. (peek_active falls back to all_active for any non-store double.)"""
    peek = getattr(store, "peek_active", None) or store.all_active
    return [i for i in peek() if i.effective_kind in ALWAYS_ON_KINDS]


def always_on_lines(store: Any, max_lines: int,
                    query: Optional[str] = None) -> Tuple[List[str], int]:
    """Render the always-on rules/guardrails as terse lines, capped at `max_lines` — the budget
    is NEVER exceeded (P11). When more entries exist than fit, the most relevant are kept (ranked
    by `query` if given, else rule-before-guardrail then oldest-first) and the last slot becomes
    a `(+N more …)` notice. Returns (lines, overflow_count)."""
    items = always_on_items(store)
    if max_lines <= 0 or not items:
        return [], len(items)

    if query:
        items.sort(key=lambda i: (-lexical_score(query, _text(i)),
                                  ALWAYS_ON_KINDS.index(i.effective_kind), i.created_at))
    else:
        items.sort(key=lambda i: (ALWAYS_ON_KINDS.index(i.effective_kind), i.created_at,
                                  i.subject))

    if len(items) <= max_lines:
        return [_line(i) for i in items], 0

    # Too many to fit: keep (max_lines - 1) and spend the last line on an honest notice.
    keep = max(max_lines - 1, 0)
    shown = [_line(i) for i in items[:keep]]
    overflow = len(items) - keep
    shown.append(f"- (+{overflow} more project rule(s)/guardrail(s) not shown — over the "
                 f"always-on budget; run `mokata memory --kind rule` / prioritise)")
    return shown, overflow


def jit_recall(store: Any, query: str, top_k: int = DEFAULT_TOP_K,
               kinds: Tuple[str, ...] = JIT_KINDS) -> List[MemoryItem]:
    """Just-in-time retrieval of the context/reference/best-practice items RELEVANT to `query`
    — top-k by relevance, never a corpus dump (P11). Returns at most `top_k` items, only those
    with a non-zero relevance signal; an empty query retrieves nothing. Lexical floor (zero-dep,
    deterministic); the wider tiered retrieval keys the same relevance when wired."""
    if not query or top_k <= 0:
        return []
    candidates = [i for i in store.all_active() if i.effective_kind in kinds]
    scored = [(lexical_score(query, _text(i)), i) for i in candidates]
    scored = [(s, i) for s, i in scored if s > 0.0]
    scored.sort(key=lambda si: (-si[0], si[1].created_at, si[1].subject))
    return [i for _s, i in scored[:top_k]]
