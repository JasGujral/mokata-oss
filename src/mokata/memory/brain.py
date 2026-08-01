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

from .episodic import lexical_score, shares_content_term
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


def render_item_line(item: MemoryItem) -> str:
    """THE one-line rendering of a memory item for an injected surface — `- [kind] subject: value`.

    Public because H-1a's per-turn pack renders its JIT half itself (`always_on_lines` renders the
    always-on half) and the two halves land in the SAME emitted block. Two renderers would have
    made one block speak in two voices for no reason anyone could see from either side."""
    return f"- [{item.effective_kind}] {item.subject}: {item.value}"


def _injectable_active(store: Any) -> List[MemoryItem]:
    """THE read both auto-injection halves use: the active items this identity may SEE, read
    WITHOUT counting a read. Two properties, one call, and both were filed as H-1a blockers
    (doc 84 §4) because this module sits on the warm path of every turn once H-1a wires it:

      * NON-COUNTING (JIT-RECALL-COUNTS-A-READ). Auto-injection is not a user recall. `all_active`
        bumps `memory_stats.reads` and persists it to DISK, so a per-turn injection would write on
        every turn and turn the read/write ratio `/mokata:govern` surfaces into a count of TURNS.
        The always-on half already avoided this; `jit_recall` did not.

      * VISIBLE (JIT-RECALL-UNSCOPED). `all_active`/`peek_active` are the RAW reads — they apply
        neither the TM.S6 scope union, nor the M-A2 precedence winner, nor the TM.S10 readability
        filter. `peek_visible_active` is the store's one visibility rule on a non-counting read.
        Byte-identical in local/zero-config mode (no scope context, no enforcing policy), so this
        only ever changes what a TEAM seat sees — where it is DB.S8's S-2 "zero cross-seat leaks".

    Split the INSTRUMENTATION, never the rule (DB.S7c1). Duck-typed and degrading, so a minimal
    store or a test double that predates the seam still works: `peek_visible_active` →
    `peek_active` → `all_active`."""
    read = (getattr(store, "peek_visible_active", None)
            or getattr(store, "peek_active", None)
            or store.all_active)
    return list(read())


def always_on_items(store: Any) -> List[MemoryItem]:
    """Active rule/guardrail items (the always-on set), regardless of relevance. Surfacing
    the always-on rules (briefing, rules view, governance dashboard) is automatic injection,
    not a user recall — so it reads through `_injectable_active`: non-counting AND scope/
    readability filtered."""
    return [i for i in _injectable_active(store) if i.effective_kind in ALWAYS_ON_KINDS]


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
        return [render_item_line(i) for i in items], 0

    # Too many to fit: keep (max_lines - 1) and spend the last line on an honest notice.
    keep = max(max_lines - 1, 0)
    shown = [render_item_line(i) for i in items[:keep]]
    overflow = len(items) - keep
    shown.append(f"- (+{overflow} more project rule(s)/guardrail(s) not shown — over the "
                 f"always-on budget; run `mokata memory --kind rule` / prioritise)")
    return shown, overflow


def jit_recall(store: Any, query: str, top_k: int = DEFAULT_TOP_K,
               kinds: Tuple[str, ...] = JIT_KINDS,
               exclude_ids: Optional[Any] = None) -> List[MemoryItem]:
    """Just-in-time retrieval of the context/reference/best-practice items RELEVANT to `query`
    — top-k by relevance, never a corpus dump (P11). Returns at most `top_k` items, only those
    sharing a CONTENT term with the query; an empty query retrieves nothing. Lexical floor
    (zero-dep, deterministic); the wider tiered retrieval keys the same relevance when wired.

    ADMISSION vs RANKING are two different questions here, and only the first changed. Admission
    asks "is there evidence this item is about the turn?" and answers it with `shares_content_term`
    — function words are excluded, so a query and an item sharing only `the` are NOT a match (doc
    84 JIT-LEXICAL-FUNCTION-WORD-FLOOR). Ranking is still raw `lexical_score`, because it is the
    shared v1 ranker DB.S3's FTS parity compares against; H-4's BM25 is where ranking gets fixed,
    and its IDF subsumes this list rather than deleting it. Note the admission test SUBSUMES the
    old `score > 0` filter: a shared content token implies a non-zero Jaccard, never the reverse.

    Reads through `_injectable_active` — non-counting and visibility-filtered. This is automatic
    injection, not a recall the user asked for, and on a team seat the candidate set must never
    include an item the reader may not see.

    `exclude_ids` (H-1a S4) is dropped from the CANDIDATE set, before scoring and before the top-k
    cut — never from the result. Filtering afterwards looks equivalent and is not: with the
    excluded items still occupying the top k, a caller that has already seen them gets back an
    EMPTY list while genuinely relevant items sit at rank k+1, unreached. The dedup would then
    silence the caller instead of promoting what it has not seen yet, which is the opposite of
    what it is for. Default None ⇒ byte-identical for every existing caller."""
    if not query or top_k <= 0:
        return []
    exclude = set(exclude_ids or ())
    candidates = [i for i in _injectable_active(store)
                  if i.effective_kind in kinds and i.id not in exclude
                  and shares_content_term(query, _text(i))]
    scored = [(lexical_score(query, _text(i)), i) for i in candidates]
    scored.sort(key=lambda si: (-si[0], si[1].created_at, si[1].subject))
    return [i for _s, i in scored[:top_k]]
