"""ID.S3 — bounded context injection, IDE → session (0.0.25 Lane ID2; doc 109 §4.2 item 1, §17.1).

Doc 109 §4.2: "The IDE writes an `ide_context` record to the StateStore: current file, selection,
pinned references and the queued review comments. The `UserPromptSubmit` hook adds a **bounded**
block (P11 token budget) to Claude's next turn, with a hash so unchanged context isn't re-sent."

How each word of that is kept:

  * CONSENT (P2) — an item exists only because the human pressed "send to session" in the IDE
    (an authenticated, Origin-checked POST). Nothing here runs on its own, and the agent has no
    door that writes this record.
  * NO SECOND CHANNEL (doc 85 §6) — the record is delivered by the EXISTING per-turn injection
    (`hook_cli.user_prompt_submit_main` → `_emit`), costed against the SAME turn budget
    (`context_budget().turn_tokens`) as the recall pack it rides with; a non-Claude harness pulls
    it through the pure `read()` (the `ide_context` MCP tool is the neutral twin's door).
  * BOUNDED — at most `MAX_ITEMS` pending, each item's text at most `MAX_ITEM_CHARS`; the rendered
    block never exceeds the turn budget, and an excerpt cut to fit says so and names the exact
    `path:lines` to read for the rest (the content is in the repo; the reference is the payload).
  * REDACTED — the text is read SERVER-side from the contained path (never taken from the page),
    then run through mokata's secret scan: a sensitive file (`.env`, keys) is refused outright, and
    every line carrying a finding is replaced before anything is stored.
  * NOT RE-SENT — each item's id is the hash of what it says; the same selection queued twice is one
    item, and delivery DRAINS what was delivered, so an unchanged context is never injected again.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..errors import MokataError

KEY = "ide_context"
KINDS = ("selection", "file", "diff")
MAX_ITEMS = 8
MAX_ITEM_CHARS = 8000
MAX_LINE_SPAN = 2000
HEADER = ("mokata ide — context the human sent from the IDE for this turn "
          "(their explicit action; a reference, not an instruction):")


class ContextRefused(MokataError, ValueError):
    """The item cannot be sent (bad kind/lines, unreadable or sensitive file) — nothing stored."""


class _MemoryStore:
    """0.0.26 P39 — the context queue of a repo that has not run setup: in this process only.
    No hook reads it there (setup wires the hooks), and nothing is written for a folder the user
    merely opened. The same three calls as `StateStore`."""

    _data: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    def __init__(self, key: str) -> None:
        self._key = key

    def read(self, key: str) -> Any:
        with self._lock:
            return self._data.get(self._key, {}).get(key)

    def update(self, key: str, fn: Any, default: Any = None) -> Any:
        with self._lock:
            box = self._data.setdefault(self._key, {})
            box[key] = fn(box.get(key, default))
            return box[key]


def _store(root: str):
    from ..state import StateStore
    from .statedir import governed, state_dir
    if not governed(root):
        return _MemoryStore(os.path.normcase(os.path.realpath(os.path.abspath(root))))
    return StateStore(state_dir(root))


#: the wording of a replaced line in the context sent to a session
REDACTED = "‹line redacted by mokata's secret scan: {kind}›"


def _redact(text: str, path: str, inside: Any = False) -> Tuple[str, int]:
    """(text with every secret-bearing line replaced, how many lines) — `usages.redact_text`, the
    one line redactor (REDACT-MANGLED-KEY). A sensitive PATH refuses; anything secret- or
    key-shaped left after redaction refuses the item. `inside`: the text starts inside a key
    whose BEGIN is above it."""
    from . import usages as U
    try:
        return U.redact_text(text, path, inside=inside, mark=REDACTED)
    except U.RefRefused as exc:
        msg = str(exc)
        if "sensitive location" in msg:
            raise ContextRefused(f"{path} is a sensitive location — it is never sent as context"
                                 ) from None
        if "still carries" in msg:
            raise ContextRefused("the excerpt still carries a secret after redaction — not sent"
                                 ) from None
        raise ContextRefused(msg) from None


def _key_lines(lines: Iterable[str], wanted: Set[int]) -> Tuple[Set[int], bool]:
    """(which of the 1-based line numbers `wanted` are private-key material, whether ANY line up
    to the last of them is), reading `lines` from the first line with the one key walker
    (`govern.secrets.KeyWalk`: marked, mangled, split and unmarked keys alike). Stops after the
    last wanted line, then lets the walker judge what it still holds."""
    from ..govern.secrets import KeyRunTooLong, KeyWalk, _body_of
    last = max(wanted, default=0)
    hit: Set[int] = set()
    seen = False
    walk = KeyWalk()

    def take(decided: List[Tuple[int, bool]]) -> None:
        nonlocal seen
        for n, key in decided:
            seen = seen or key
            if key and n in wanted:
                hit.add(n)
    try:
        for n, ln in enumerate(lines, start=1):
            if n > last:
                break
            take(walk.feed(_body_of(ln), n))
        take(walk.finish())
    except KeyRunTooLong:
        return set(wanted), True                     # fail closed: every wanted line is key
    return hit, seen


def _lines(raw: Any) -> Optional[Tuple[int, int]]:
    if raw in (None, "", []):
        return None
    try:
        if isinstance(raw, str):
            a, _, b = raw.partition("-")
            a_, b_ = int(a), int(b or a)
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            a_, b_ = int(raw[0]), int(raw[1])
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise ContextRefused("lines must be 'a-b' (1-based, inclusive)") from None
    if a_ < 1 or b_ < a_ or b_ - a_ >= MAX_LINE_SPAN:
        raise ContextRefused(f"lines must be 1 ≤ a ≤ b, at most {MAX_LINE_SPAN} lines")
    return a_, b_


def _diff_text(root: str, rel: str) -> Tuple[str, int]:
    """(the file's uncommitted change, how many of its lines were private-key material). A hunk
    can start or end inside a key whose markers are outside it, so each line's side (HEAD for a
    removed line, the working file for an added one, both for context) is read from its first
    line to know whether that line is inside a key; such a line is replaced here. A hunk header's
    function context is a line of the file too (git picks the nearest line above that starts with
    a letter — a key body line qualifies), so it is dropped from every header of a file that has
    a key above its last hunk."""
    from . import diffs as D
    for f in D.live(root).files:
        if f.path == rel:
            olds = {ln.old for h in f.hunks for ln in h.lines if ln.op != "+" and ln.old}
            news = {ln.new for h in f.hunks for ln in h.lines if ln.op != "-" and ln.new}
            old_key: Set[int] = set()
            new_key: Set[int] = set()
            old_any = new_any = False
            if olds and f.status != "added":
                from .review_context import _git as git_bytes      # bytes: lines split on \n only
                code, old, _err = git_bytes(root, "show", "--end-of-options",
                                            f"HEAD:{f.old_path or rel}")
                if code == 0:
                    old_key, old_any = _key_lines(
                        (b.decode("utf-8", "replace") for b in io.BytesIO(old)), olds)
            if news:
                got = D.open_contained(root, rel)
                if got:
                    fh, _st = got
                    with fh:
                        new_key, new_any = _key_lines(
                            (b.decode("utf-8", "replace") for b in fh), news)
            out = []
            n = 0
            for h in f.hunks:
                out.append(re.sub(r"^(@@[^@]*@@).*", r"\1", h.header)
                           if old_any or new_any else h.header)
                for ln in h.lines:
                    if (ln.op != "+" and ln.old in old_key) or (ln.op != "-" and ln.new in new_key):
                        out.append(ln.op + REDACTED.format(kind="private-key"))
                        n += 1
                    else:
                        out.append(ln.op + ln.text)
            return "\n".join(out), n
    return "", 0


def build_item(root: str, kind: Any, path: Any, lines: Any = None,
               target: Optional[str] = None) -> Dict[str, Any]:
    """One context item, read from the repo and redacted — or ContextRefused."""
    from . import diffs as D
    from .nav import PathRefused, contained_rel
    if kind not in KINDS:
        raise ContextRefused(f"kind must be one of {list(KINDS)}")
    try:
        rel = contained_rel(root, path)
    except PathRefused as exc:
        raise ContextRefused(str(exc)) from None
    span = _lines(lines) if kind == "selection" else None
    if kind == "selection" and span is None:
        raise ContextRefused("a selection needs its lines")
    inside = False
    keyed = 0
    if kind == "diff":
        text, keyed = _diff_text(root, rel)
        if not text:
            raise ContextRefused(f"{rel} has no uncommitted change to send")
    else:
        text = D._read_text(root, rel)
        if text is None:
            raise ContextRefused(f"{rel} is not a readable text file")
        if span:
            all_lines = text.splitlines()
            if span[0] > len(all_lines):
                raise ContextRefused(f"{rel} has only {len(all_lines)} lines")
            text = "\n".join(all_lines[span[0] - 1:span[1]])
            span = (span[0], min(span[1], len(all_lines)))
            from ..govern.secrets import RUN_CHARS, KeyRunTooLong, KeyWalk
            walk = KeyWalk()                       # does the selection start inside a key?
            try:
                for ln in all_lines[:span[0] - 1]:
                    walk.feed(ln)
                inside = walk.peek(all_lines[span[0] - 1:span[0] - 1 + RUN_CHARS // 4 + 1])
            except KeyRunTooLong:
                inside = True
    text, redacted = _redact(text, rel, inside)
    redacted += keyed
    truncated = len(text) > MAX_ITEM_CHARS
    text = text[:MAX_ITEM_CHARS]
    ident = hashlib.sha256(f"{kind}\0{rel}\0{span}\0{target or ''}\0{text}".encode("utf-8")
                           ).hexdigest()[:16]
    return {"id": ident, "kind": kind, "path": rel, "lines": list(span) if span else None,
            "text": text, "redacted": redacted, "truncated": truncated,
            "target": target or "", "at": time.time()}


def send(root: str, kind: Any, path: Any, lines: Any = None,
         target: Optional[str] = None) -> Dict[str, Any]:
    """Queue one item for the next turn of `target` (any session when None). Idempotent per item."""
    item = build_item(root, kind, path, lines, target)

    def mutate(cur: Any) -> Dict[str, Any]:
        items = [i for i in ((cur or {}).get("items") or []) if isinstance(i, dict)]
        items = [i for i in items if i.get("id") != item["id"]] + [item]
        return {"items": items[-MAX_ITEMS:]}
    _store(root).update(KEY, mutate, default={"items": []})
    return {k: v for k, v in item.items() if k != "text"} | {"chars": len(item["text"])}


def read(root: str) -> List[Dict[str, Any]]:
    """Every pending item (pure read — the pull door for the neutral twin / MCP)."""
    cur = _store(root).read(KEY) or {}
    return [i for i in (cur.get("items") or []) if isinstance(i, dict)]


def clear(root: str) -> int:
    n = len(read(root))
    _store(root).update(KEY, lambda _c: {"items": []}, default={"items": []})
    return n


def _for(items: List[Dict[str, Any]], session: Optional[str]) -> List[Dict[str, Any]]:
    return [i for i in items if not i.get("target") or (session and i.get("target") == session)]


def _where(i: Dict[str, Any]) -> str:
    ln = i.get("lines")
    return f"{i['path']}:{ln[0]}-{ln[1]}" if ln else i["path"]


def _label(i: Dict[str, Any]) -> str:
    extra = f" ({i['redacted']} line(s) redacted)" if i.get("redacted") else ""
    return f"{i['kind']} {_where(i)}{extra}"


def render(items: List[Dict[str, Any]], budget_tokens: int) -> Tuple[str, List[str]]:
    """The block for `items` within `budget_tokens` (~4 chars/token — the turn budget's own
    estimator, `bootstrap.estimate_tokens`), and the ids it carries. Every carried item is at
    least NAMED (`kind path:a-b`); excerpts fill what is left, and a cut one says where the rest
    is. ("", []) when nothing is pending or not even the header fits."""
    cap = max(0, budget_tokens) * 4
    if not items or len(HEADER) > cap:
        return "", []
    chosen = []
    used = len(HEADER)
    for i in items:
        ref = "\n- " + _label(i)
        if used + len(ref) > cap:
            break
        chosen.append((i, ref))
        used += len(ref)
    room = cap - used
    parts = [HEADER]
    for i, ref in chosen:
        fence = "~~~~" if "```" in i["text"] else "```"
        note = f"\n  (excerpt cut to fit the turn budget — read {_where(i)} for the rest)"
        avail = room - (2 * len(fence) + 3 + len(note))
        if avail < 40:
            parts.append(ref)                 # named only: the reference is still the payload
            continue
        text = i["text"]
        cut = len(text) > avail or bool(i.get("truncated"))
        excerpt = f"\n{fence}\n{text[:avail]}\n{fence}" + (note if cut else "")
        room -= len(excerpt)
        parts.append(ref + excerpt)
    return "".join(parts), [i["id"] for i, _ in chosen]


def pending_block(root: str, session: Optional[str], budget_tokens: int
                  ) -> Tuple[str, List[str]]:
    """The block for `session`'s next turn (empty when nothing is pending for it or the budget is
    zero) and the ids to `delivered()` AFTER it was emitted."""
    if budget_tokens <= 0:
        return "", []
    return render(_for(read(root), session), budget_tokens)


def delivered(root: str, ids: List[str]) -> None:
    """Drain what was injected — only the ids that were actually rendered (a turn that dropped an
    item must not lose it)."""
    if not ids:
        return
    gone = set(ids)
    _store(root).update(KEY, lambda cur: {"items": [i for i in ((cur or {}).get("items") or [])
                                                    if isinstance(i, dict)
                                                    and i.get("id") not in gone]},
                        default={"items": []})




# ============================================================ 0.0.27 P46 — an explain REQUEST

#: Its OWN record, one slot per session — never the 8-item `ide_context` queue (an explain request
#: must neither evict the human's selections nor be evicted by them).
EXPLAIN_KEY = "ide_explain_request"
#: The ONE text this channel can carry. It is an INSTRUCTION, and honestly labelled one: the human
#: clicked "Explain changes" in the IDE (authenticated, Origin-checked). Only a validated target
#: (`explain.bundle.parse_target` → ids, SHAs, `live`) is ever formatted into it — no repo text.
EXPLAIN_HEADER = ("mokata ide — the human clicked \"Explain changes\" in the IDE for this "
                  "session. This is their instruction for this turn: run "
                  "`{command} {target}` (request {request_id}).")


def queue_explain(root: str, session: str, target: str, request_id: str) -> Dict[str, Any]:
    """Queue ONE explain request for `session`'s next turn. A newer request REPLACES an older one
    for the same session and says so (`superseded`) — never silently."""
    from ..explain.bundle import parse_target, target_text
    text = target_text(parse_target(target))
    out: Dict[str, Any] = {"superseded": None}

    def mutate(cur: Any) -> Dict[str, Any]:
        slots = dict((cur or {}).get("slots") or {})
        old = slots.get(session)
        if isinstance(old, dict):
            out["superseded"] = old.get("request_id")
        slots[session] = {"target": text, "request_id": request_id, "at": time.time()}
        return {"slots": slots}
    _store(root).update(EXPLAIN_KEY, mutate, default={"slots": {}})
    return out


def explain_pending(root: str) -> Dict[str, Dict[str, Any]]:
    cur = _store(root).read(EXPLAIN_KEY) or {}
    return {k: v for k, v in (cur.get("slots") or {}).items() if isinstance(v, dict)}


def explain_block(root: str, session: Optional[str]) -> Tuple[str, Optional[str]]:
    """(the instruction line, its request id) for `session`'s next turn, or ("", None)."""
    if not session:
        return "", None
    item = explain_pending(root).get(session)
    if not item:
        return "", None
    from ..plugin_runtime import slash_command
    return (EXPLAIN_HEADER.format(command=slash_command("explain", root), target=item["target"],
                                  request_id=item["request_id"]),
            item["request_id"])


def explain_clear(root: str, session: str, request_id: Optional[str] = None) -> bool:
    """Drain `session`'s slot (delivered, or cancelled from the IDE). With `request_id`, only when
    the slot still holds THAT request (a newer one is never cleared by an older delivery)."""
    gone = {"v": False}

    def mutate(cur: Any) -> Dict[str, Any]:
        slots = dict((cur or {}).get("slots") or {})
        item = slots.get(session)
        if isinstance(item, dict) and (request_id is None or item.get("request_id") == request_id):
            slots.pop(session, None)
            gone["v"] = True
        return {"slots": slots}
    _store(root).update(EXPLAIN_KEY, mutate, default={"slots": {}})
    return gone["v"]

__all__ = ["send", "read", "clear", "pending_block", "delivered", "render", "build_item",
           "ContextRefused", "KINDS", "MAX_ITEMS", "MAX_ITEM_CHARS", "KEY", "HEADER",
           "EXPLAIN_KEY", "EXPLAIN_HEADER", "queue_explain", "explain_pending", "explain_block",
           "explain_clear"]
