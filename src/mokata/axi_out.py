"""AXI-OUT (0.0.23 E2) — the agent-ergonomic output helpers, kept to ONE small module.

Items built here (doc 84 AXI-OUT row; the rest of the row stays open):

  (2)  pre-computed aggregates — `with_count` stamps the size of a list-shaped read on the result,
       so a consumer never has to count (or guess that a missing list means zero).
  (3)  empty-state honesty — `empty_state_findings` is the LINT: a list-shaped result must say how
       many it holds, and an EMPTY one must say WHY (a message/note/reason) or carry the degraded
       marker — "0 results" ≠ silence ≠ skipped-because-broken.
  (10) `remedyCommand` — `remedy_command` pulls the exact fixing command out of a remediation
       sentence, and `with_remedy` adds it as a machine-readable field to every non-success MCP
       result (and every degraded marker) that names one. Derived from the text the surface
       ALREADY says, so the prose and the field cannot disagree.

Pure stdlib, never raises. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# Statuses that are NOT a success: the ones a consumer needs a fix for.
NON_SUCCESS_STATUSES = frozenset({"degraded", "refused", "blocked", "timed_out", "error"})

# The text fields a payload says its remedy in, in the order a remedy is looked for.
_TEXT_FIELDS = ("remedy", "fix", "hint", "next", "message", "reason", "note", "notice", "detail")

# A backticked mokata command (`mokata doctor`, `mokata approve --list`) — the exact-command form
# every mokata remediation sentence already uses. The `/mokata:<name>` slash form counts too.
_CMD = re.compile(r"`((?:mokata(?:-[a-z]+)?|/mokata:)[^`\n]*)`")
_BARE = re.compile(r"(?<![\w/-])(mokata [a-z][a-z0-9-]*(?: [a-z][a-z0-9-]*)?(?: --?[a-z][\w-]*)*)")

# The keys an empty result may explain itself with.
_EMPTY_EXPLAINERS = ("message", "note", "reason", "hint", "empty_reason")


def remedy_command(text: Any) -> Optional[str]:
    """The first exact mokata command in `text` (backticked preferred), or None."""
    if not isinstance(text, str) or "mokata" not in text:
        return None
    m = _CMD.search(text)
    if m:
        cmd = m.group(1).strip().rstrip(".,;:")
        return cmd or None
    m = _BARE.search(text)
    return m.group(1).strip() if m else None


def _first_remedy(payload: Dict[str, Any], fields: Iterable[str] = _TEXT_FIELDS) -> Optional[str]:
    for key in fields:
        cmd = remedy_command(payload.get(key))
        if cmd:
            return cmd
    return None


def with_remedy(payload: Any) -> Any:
    """Add `remedyCommand` to a non-success result (and to its `degraded` marker) when the text it
    already carries names one. Never overwrites an explicit one; never adds a key to a success;
    returns the SAME object (mutated) or the input untouched when it is not a dict."""
    try:
        if not isinstance(payload, dict):
            return payload
        marker = payload.get("degraded")
        if isinstance(marker, dict) and "remedyCommand" not in marker:
            cmd = _first_remedy(marker)
            if cmd:
                marker["remedyCommand"] = cmd
        if payload.get("status") in NON_SUCCESS_STATUSES and "remedyCommand" not in payload:
            cmd = _first_remedy(payload)
            if cmd:
                payload["remedyCommand"] = cmd
        return payload
    except Exception:  # noqa: BLE001 — an ergonomics field must never cost a tool its answer
        return payload


def with_count(payload: Dict[str, Any], list_key: str, count_key: str = "count") -> Dict[str, Any]:
    """Stamp `count_key` = len(payload[list_key]) when it is absent (item 2)."""
    items = payload.get(list_key)
    if isinstance(items, list) and count_key not in payload:
        payload[count_key] = len(items)
    return payload


def empty_state_findings(payload: Any, list_key: str, count_key: str = "count") -> List[str]:
    """THE LINT (item 3). [] when the result is honest about its size; otherwise what is wrong."""
    if not isinstance(payload, dict):
        return [f"not a dict: {type(payload).__name__}"]
    out: List[str] = []
    items = payload.get(list_key)
    if not isinstance(items, list):
        return [f"`{list_key}` is not a list (silence is not an empty state)"]
    count = payload.get(count_key, payload.get("total"))
    if not isinstance(count, int):
        out.append(f"no `{count_key}` aggregate — a consumer would have to count `{list_key}`")
    elif count < len(items):
        out.append(f"`{count_key}`={count} is smaller than the {len(items)} items returned")
    if not items:
        explained = any(isinstance(payload.get(k), str) and payload.get(k).strip()
                        for k in _EMPTY_EXPLAINERS)
        if not explained and not payload.get("degraded") and payload.get("enabled") is not False:
            out.append(f"empty `{list_key}` with no message — reads exactly like a broken read")
    return out
