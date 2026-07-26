"""MCP-R.D1c · pagination — the ONE place a list tool's slice + cursor math lives.

Before this stage the list tools returned their WHOLE list in a single call, and `audit` was the
poster child: `limit=0` was its DEFAULT, i.e. "return the entire append-only ledger". On a
long-lived repo that is an unbounded token payload in one read (P14) and the client had no way to
learn that more existed (P16). This module is the single mechanism that fixes it: a tool hands its
list to `paginate(...)` and gets back the page under its own list-key plus an honest cursor
(`total`/`has_more`/`next_offset`), with a BOUNDED default limit. No tool hand-rolls the arithmetic.

Three properties make this shared rather than per-tool copy-paste:
  * BOUNDED BY DEFAULT — `limit` defaults to `DEFAULT_PAGE_LIMIT`; only an EXPLICIT `limit=ALL` (0)
    opts out. A negative limit falls back to the bounded default rather than becoming unbounded.
  * ORDER-PRESERVING — the caller states its orientation once (`from_end`) and the helper never
    reorders the sequence it was given, so no tool's ordering is silently flipped.
  * HONEST ABOUT `total` — `total` is a real `len()` when the source is a materialized list, and
    `None` (never a fabricated number) when a source's full length is genuinely unknown; `has_more`
    stays correct either way.

`count` means the PAGE length everywhere; `total` is the full length. The returned key order is
`count, <list-key>, total, has_more, next_offset` — the pre-D1c leading shape (`{count, <list>}`)
is preserved and the cursor fields follow it.

Pure stdlib — never imports the optional MCP SDK (same discipline as `registry`/`response_format`/
`tool_annotations`), so the slice/cursor decision is unit-testable without the SDK. Typed VALIDATION
of `limit`/`offset` (loud errors on a negative or non-integer) is deliberately deferred to D1d; this
stage only guarantees that a bad value can never widen the payload.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

# The bounded default every list tool ships with — a page an agent can actually read, not a ledger.
DEFAULT_PAGE_LIMIT = 50

# The EXPLICIT all-opt-out. `limit=ALL` is opt-in only; it is never a tool's default (that is the
# whole point of D1c — `audit` used to default to it).
ALL = 0

# Passed as `total=` when a source's full length is genuinely unknown (a streamed/remote list). The
# result then carries `total: null` — never a guess (P16).
UNKNOWN_TOTAL = None

# Sentinel for the DEFAULT mode: `items` is the FULL sequence, so the helper slices it and derives
# `total` from its length. Distinct from `total=None`, which means "honestly unknown".
_FULL: Any = object()


def _bounds(limit: int, offset: int) -> tuple:
    """Normalize the two inputs so a bad value can never WIDEN the payload. A negative limit falls
    back to the bounded default (never to `ALL`, which would be an unbounded response from a typo);
    a negative offset clamps to the first page. Loud typed validation is D1d's job."""
    if limit < 0:
        limit = DEFAULT_PAGE_LIMIT
    if offset < 0:
        offset = 0
    return limit, offset


def paginate(items: Sequence[Any], *, key: str, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0,
             total: Any = _FULL, from_end: bool = False) -> Dict[str, Any]:
    """Page `items` and return `{count, <key>, total, has_more, next_offset}` for a tool to merge
    into its result. The SOLE site of the slice + cursor arithmetic (MCP-R.D1c).

    `key` is the tool's own list-key (`entries`/`sessions`/`windows`/`bundles`/`hits`/`stacks`), so
    the paged result keeps the shape callers already know.

    Two modes, chosen by `total`:
      * DEFAULT (`total` omitted) — `items` is the FULL sequence: the helper slices it and `total`
        is its real `len()`. This is what all eight list tools use; every one of their sources is a
        materialized in-memory list, so the length is both known and cheap.
      * WINDOW (`total` given) — `items` IS the page a streamed/remote source already produced.
        Pass `total=UNKNOWN_TOTAL` when the full length is genuinely unknown (`total` comes back
        null and `has_more` is derived from whether a FULL page was returned), or an int when a
        cheap count is available. Never pass a guess.

    `from_end=True` orients `offset` at the NEWEST end of a chronological, append-ordered sequence:
    `offset=0` is the most recent `limit` items and paging forward walks BACK in time, while each
    page stays in the sequence's own order internally. `audit` uses it — it preserves the pre-D1c
    `entries[-limit:]` tail semantics exactly, so the ledger's order is never silently flipped.
    Every other list tool pages from the START of its own natural order (name-sorted, rank-sorted,
    registry order), which offset-from-start preserves.
    """
    limit, offset = _bounds(limit, offset)

    if total is _FULL:
        full = list(items)
        known: Optional[int] = len(full)
        if from_end:
            end = max(0, known - offset)
            start = 0 if limit == ALL else max(0, end - limit)
            page = full[start:end]
            has_more = bool(page) and start > 0
        else:
            page = full[offset:] if limit == ALL else full[offset:offset + limit]
            has_more = offset + len(page) < known
    else:
        # WINDOW mode — the source already sliced; trust it and only compute the cursor.
        page = list(items)
        known = total
        if known is None:
            has_more = limit != ALL and len(page) == limit
        else:
            has_more = offset + len(page) < known

    return {"count": len(page), key: page, "total": known,
            "has_more": has_more, "next_offset": offset + len(page) if has_more else None}
