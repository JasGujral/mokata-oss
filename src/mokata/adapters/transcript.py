"""H-2 · the read-only TRANSCRIPT adapter — real token counts, behind a boundary.

doc 85 §7l (STANDING, Jas 2026-10-02): mokata integrates with Claude Code through documented
surfaces only, and `transcript_path` JSONL is *"read-only and adapter-wrapped, and hooks are
primary"*. This module IS that wrapper. It exists because of what H-2 cannot do without it:
**every** token budget in this tree — the 2000-token bootstrap (P11), the handback caps, the
savings ledger, the parallel cost estimates — is enforced against `bootstrap.estimate_tokens`,
a deliberately tokenizer-free `chars/4` rule, and `govern.tokens` has carried a calibration
record since R11 that logs **the estimate alone**. The margin ("estimate >= actual") was
therefore ASSERTED and never MEASURED, which is the defect P16 exists to forbid — and
`doctor`'s `calibration-drift` finding could not fire in the field at all, because no
production caller ever had a real `actual` to hand it.

⛔ **`usage` IS PER API RESPONSE, NOT PER RECORD — AND THE FIRST REAL TRANSCRIPT IS WHAT TAUGHT
US THAT.** The first version of this module classified one JSONL line at a time, and on a real
Claude Code session it reported a chars/4 ratio of **7.48**, which is impossible for text. The
cause: Claude Code writes **one record per content BLOCK** (there is an `apiBlockIndex` field),
and **every one of those records repeats the whole response's `usage`.** Measured on that
session: **415 distinct API responses, 237 of them written across more than one record.** So a
32-character text record — *"Now the calibration loop module."*, estimate 8 tokens — carried
`output_tokens: 5666`, because the response it belonged to also contained the tool call that
wrote a 300-line file. ⭐ **The module was comparing a whole to a part**, and the module's own
docstring had warned about the adjacent version of this mistake while being blind to this one.

So the unit is the RESPONSE, keyed by `message.id`, and the scope limit is declared rather than
implied (§7j): a response is calibratable only when **every block across all of its records is
text**, because `output_tokens` covers blocks whose characters are not in any text. Thinking
tokens are **subtracted** rather than used to exclude the response (`output_tokens_details.
thinking_tokens`) — they are output tokens whose text is not present, so removing them leaves
exactly the text's own count. ⚠ On the one real transcript available the two rules agreed,
because every calibratable response there had zero thinking tokens; subtraction is kept because
it is the rule that stays correct when they stop agreeing.

⚠ **THE YIELD IS SMALL AND THAT IS A PROPERTY OF AGENTIC SESSIONS, NOT A BUG.** Of those 415
responses, **5 were calibratable** — almost every response in a working session carries a
`tool_use` block. The instrument accumulates slowly, and a row that reports a ratio over a
handful of responses must say so.

⛔ **AND THE INPUT SIDE IS NOT MEASURABLE HERE, which is the honest answer rather than a gap.**
`usage.input_tokens` covers the system prompt, the tool schemas, every prior message and the
cache — none of which is a text mokata holds. So this adapter calibrates the `chars/4` rule
itself, on output, and says so; it does not claim to measure what a session spent. The rule is
general (it is applied to arbitrary text everywhere), so a constant measured on output is the
constant that governs the budgets.

⭐ **COUNTS ONLY. NEVER TEXT.** A sample carries a model name and two integers. The characters
are counted and the text dropped in the same expression — nothing this module returns, logs or
persists can carry a fragment of the user's conversation, and `test_a38` asserts that of the
dataclass rather than trusting this paragraph.

⚠ **POSITION IS NOT A STATE — READ THE ID, NOT THE OFFSET.** Reading the whole JSONL on every
hook call would make an async observability lane cost O(session), so the read is incremental
from a byte offset. A byte offset alone is the shape this project has already been bitten by: a
rotated, replaced or truncated file leaves it pointing at the middle of someone else's content.
So a cursor is `(session_id, offset)` and the **id validates the offset** — a different
`session_id` (from the ENVELOPE, which §7l makes primary) or a file now SHORTER than the offset
restarts the read at zero. Only COMPLETE lines are consumed, so a transcript caught mid-append
leaves its partial last line for the next call instead of parsing a truncated record.

⭐ **A SCHEMA CHANGE FAILS LOUD (§7e).** The JSONL shape is not a public contract, so
`records` and `usable` are both reported: *"nothing was written"* and *"everything was written
in a shape this adapter no longer understands"* are different facts, and `schema_blind` is the
second one. Without that pair a Claude Code release that renames `usage` would present as a
calibration that quietly measures nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..bootstrap import estimate_tokens

# How much of a transcript ONE call may read. The incremental cursor means the steady-state read
# is proportional to what was appended since the last call; this cap bounds the COLD read (a long
# session seen for the first time) so an async hook can never become the slowest thing in a turn.
# A capped read is not a lost read: the cursor advances by what was consumed and the next call
# continues from there.
TRANSCRIPT_READ_CAP_BYTES = 512 * 1024

# One pathological line must not become one pathological allocation. A line longer than this is
# DROPPED (and counted in `records`, so it cannot hide), not truncated and parsed: half a JSON
# record is not a record, and parsing one would be the "an ABSENT answer and a REAL answer share
# a representation" failure (§7g) at the byte layer.
TRANSCRIPT_LINE_CAP_BYTES = 256 * 1024

# The content block types whose characters ARE the message's output. Anything else in a message's
# content means `output_tokens` covers tokens whose text is not present, so the message is skipped.
_TEXT_BLOCK = "text"


@dataclass(frozen=True)
class TokenSample:
    """One estimate-vs-actual observation: a model name and two integers, and that is the whole
    type. There is deliberately no field a transcript fragment could land in."""
    model: str
    estimate: int          # estimate_tokens(the message's text) — the chars/4 rule's answer
    actual: int            # usage.output_tokens — the harness's real count for that message


@dataclass(frozen=True)
class TranscriptCursor:
    """Where the next read starts, and the identity that makes the offset meaningful.

    `session_id` comes from the hook ENVELOPE (§7l: hooks are primary), never from the file."""
    session_id: str = ""
    offset: int = 0


@dataclass
class TranscriptReading:
    """The outcome of one read. Four facts that must not collapse into each other (§7g):
    how many records were seen, how many were usable, how far the cursor moved, and — when the
    read could not happen at all — WHY, as a reason rather than an empty result."""
    samples: List[TokenSample] = field(default_factory=list)
    records: int = 0               # complete lines that parsed as a JSON object
    assistant: int = 0             # ... recognized as an assistant message record
    responses: int = 0             # distinct API responses CLOSED in this read
    priced: int = 0                # ... carrying a usable output-token count
    usable: int = 0                # ... and all-text, so a sample came out
    interleaved: int = 0           # records that returned to an EARLIER response's id
    consumed: int = 0              # bytes of COMPLETE lines consumed by this read
    cursor: TranscriptCursor = field(default_factory=TranscriptCursor)
    truncated: bool = False        # the read hit TRANSCRIPT_READ_CAP_BYTES, more remains
    reason: Optional[str] = None   # non-None == the read did not happen; never paired with samples

    @property
    def readable(self) -> bool:
        return self.reason is None

    @property
    def schema_blind(self) -> bool:
        """Assistant messages went by and NOT ONE of them carried a token count this adapter
        could read — the signal a Claude Code schema change produces BEFORE the calibration
        silently starts measuring nothing (§7e).

        ⛔ **IT IS KEYED ON `priced`, NOT ON `usable`, AND MY FIRST DRAFT GOT THAT WRONG.**
        `records > 0 and usable == 0` fires on a window of user turns, and on a window whose
        assistant messages all carry `tool_use` blocks — both of which are an ordinary minute in
        an ordinary session. A detector whose output is almost all false positives teaches its
        reader that the rule means nothing, which is the stage-07 lesson arriving one stage
        later. `usage.output_tokens` is on EVERY assistant message whatever its blocks, so its
        total absence across a window is about the SCHEMA and nothing else.

        ⚠ **The scope limit, declared (§7j):** a window with no assistant message at all is NOT
        reported as blind. It is a quiet window, and a transcript of nothing but user turns is a
        thing that happens.

        ⚠ **AND IT KEYS ON `responses`, NOT ON `assistant`** — the stage report said `assistant`
        and the code has always said `responses` (review F9). They differ exactly where it
        matters: a window whose assistant records are all still OPEN has `assistant > 0` and
        `responses == 0`, and **nothing in it has been judged yet**. Calling that a moved schema
        would be the same false positive this docstring is about."""
        return self.reason is None and self.responses > 0 and self.priced == 0


def _message_of(record: Any) -> Optional[Dict[str, Any]]:
    """The assistant message inside one transcript record, or None.

    TWO shapes are accepted and no more: Claude Code's envelope (`{"type": "assistant",
    "message": {...}}`) and a bare API message (`{"role": "assistant", ...}`). Anything else is
    not understood — and being not understood is REPORTED (`records` without `usable`) rather
    than treated as an absence, because the JSONL schema is not a contract mokata owns."""
    if not isinstance(record, dict):
        return None
    inner = record.get("message")
    if isinstance(inner, dict):
        if record.get("type") not in (None, "assistant"):
            return None
        return inner if inner.get("role") in (None, "assistant") else None
    if record.get("role") == "assistant":
        return record
    return None


def response_of(record: Any) -> Optional[Dict[str, Any]]:
    """The API-response facts one transcript record carries, or None.

    ONE place in this tree knows the transcript's shape, and this is it. Returns
    `{id, model, usage, blocks}` where `blocks` is a list of `(type, text_or_None)` for the
    blocks THIS record holds — the caller groups records by `id` to reassemble the response,
    because `usage` describes the response and not the record (see the module docstring)."""
    message = _message_of(record)
    if message is None:
        return None
    rid = message.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None
    content = message.get("content")
    blocks: List[Tuple[str, Optional[str]]] = []
    if isinstance(content, str):
        blocks.append((_TEXT_BLOCK, content))
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                blocks.append(("<malformed>", None))
                continue
            btype = b.get("type")
            text = b.get("text") if btype == _TEXT_BLOCK else None
            if btype == _TEXT_BLOCK and not isinstance(text, str):
                blocks.append(("<malformed>", None))
                continue
            blocks.append((str(btype), text))
    else:
        return None
    usage = message.get("usage")
    return {"id": rid.strip(), "model": message.get("model"),
            "usage": usage if isinstance(usage, dict) else None, "blocks": blocks}


def _priced(usage: Optional[Dict[str, Any]]) -> Optional[int]:
    """The response's real OUTPUT TEXT token count, or None.

    `output_tokens` minus `output_tokens_details.thinking_tokens`: thinking is billed as output
    and its characters are not in the content, so leaving it in would inflate the ratio by
    whatever the model thought about.

    ⚠ §7f (two redundant defences are untestable): an earlier draft rejected `out <= 0` HERE as
    well as at the return, and the mutant that relaxed the first one SURVIVED a 68-test suite —
    not because the suite was thin but because the two guards were the same guard, so no input
    could reach one without the other. The positivity test happens ONCE, at the return, after
    the subtraction, where it also covers a response whose thinking consumed its whole output.
    This guard now does only the thing the other cannot: reject a non-integer and a bool."""
    if not isinstance(usage, dict):
        return None
    out = usage.get("output_tokens")
    if not isinstance(out, int) or isinstance(out, bool):
        return None
    details = usage.get("output_tokens_details")
    think = (details or {}).get("thinking_tokens") if isinstance(details, dict) else 0
    if isinstance(think, int) and not isinstance(think, bool) and think > 0:
        out -= think
    return out if out > 0 else None


def sample_of_response(group: Dict[str, Any]) -> Optional[TokenSample]:
    """The counts-only sample for ONE fully-collected API response, or None.

    None when the response carries any non-text block, has no readable model, or reports no
    usable output count. `actual = 0` is never a sample: against a real estimate it would read
    as a catastrophic under-count by the harness rather than as the absence of a number (§7g)."""
    blocks = group.get("blocks") or []
    if not blocks or any(t != _TEXT_BLOCK for t, _x in blocks):
        return None
    actual = _priced(group.get("usage"))
    if actual is None:
        return None
    model = group.get("model")
    if not isinstance(model, str) or not model.strip():
        return None
    # The ONE expression where the text exists: its length is taken and the string is not kept.
    estimate = estimate_tokens("".join(t for _k, t in blocks if t))
    if estimate <= 0:
        return None
    return TokenSample(model=model.strip(), estimate=int(estimate), actual=int(actual))


def read_samples(transcript_path: Optional[str], *,
                 session_id: str = "",
                 cursor: Optional[TranscriptCursor] = None,
                 cap: int = TRANSCRIPT_READ_CAP_BYTES) -> TranscriptReading:
    """Read the transcript's new bytes and return counts-only samples. Never raises.

    The cursor is honoured only when its `session_id` matches the envelope's and the file is at
    least as long as its offset; otherwise the read restarts at zero, because an offset whose
    identity does not check out is a guess about someone else's file."""
    start = TranscriptCursor(session_id=session_id, offset=0)
    if not transcript_path or not isinstance(transcript_path, str):
        return TranscriptReading(cursor=start, reason="no transcript path in the hook envelope")
    try:
        size = os.path.getsize(transcript_path)
    except OSError as exc:
        return TranscriptReading(cursor=start,
                                 reason=f"transcript not readable ({type(exc).__name__})")

    offset = 0
    if cursor is not None and cursor.session_id == session_id and cursor.offset <= size:
        offset = max(0, int(cursor.offset))
    if offset >= size:
        return TranscriptReading(cursor=TranscriptCursor(session_id, size))

    want = size - offset
    truncated = want > cap
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(offset)
            blob = fh.read(min(want, cap))
    except OSError as exc:
        return TranscriptReading(cursor=start,
                                 reason=f"transcript not readable ({type(exc).__name__})")

    # Only COMPLETE lines are consumed. `rpartition` leaves a partial trailing line behind for
    # the next call; when the read was capped there is almost always one.
    head, newline, _tail = blob.rpartition(b"\n")
    if not newline:
        # No complete line in this window. Consuming nothing is correct — and a window that is
        # ALL one oversized partial line would otherwise wedge here forever, so the line cap
        # below is what makes progress guaranteed rather than hoped for.
        if truncated:
            return TranscriptReading(cursor=TranscriptCursor(session_id, offset + len(blob)),
                                     consumed=len(blob), truncated=True,
                                     reason="no complete record inside the read cap")
        return TranscriptReading(cursor=TranscriptCursor(session_id, offset))

    # ⭐ A GROUP-BY KEYED ON THE `message.id`, AND NOTHING IS JUDGED UNTIL THE WINDOW ENDS.
    # `usage` describes an API response and Claude Code writes one record per content block, so a
    # response is judgeable only once ALL of its records have been seen. A group is marked CLOSED
    # when a non-assistant record proves the response finished; the groups with no such proof stay
    # OPEN and the cursor is left at the EARLIEST open group's first byte, so the next read
    # re-reads and closes them. Advancing past an open group would drop roughly one response per
    # hook call, which on a hook that fires per file-mutating tool call is most of them.
    #
    # ⛔ **THE FIRST VERSION CLOSED A GROUP WHEN THE NEXT RECORD CARRIED A DIFFERENT `id`, AND
    # THAT RECREATES THE DEFECT THE WHOLE STAGE IS NAMED FOR.** Records of two responses can
    # INTERLEAVE — a sidechain `Task` does it by construction — so A, B, A closed A on B's
    # arrival, judged it from its first blocks only, and then treated A's remaining blocks as a
    # third response. Fewer chars against the same token count is an UNDER-estimate of
    # chars/token, which is 7.48 arriving by a new route. Found by the stage's independent review
    # (F1).
    #
    # ⭐ So the id is a KEY, not a transition: every record of an id lands in ONE group wherever it
    # appears in the window, and a closed id that comes BACK simply re-opens — nothing has been
    # emitted yet, so nothing is double-counted and nothing partial is ever judged. The return is
    # COUNTED (`interleaved`) rather than assumed absent, because "transcripts do not
    # interleave" is exactly the premise that was wrong (P16).
    reading = TranscriptReading(truncated=truncated)
    groups: "Dict[str, Dict[str, Any]]" = {}   # id -> group, insertion-ordered (3.7+)
    closed: "set" = set()                      # ids a non-assistant record has proved finished
    last_touched: Optional[str] = None         # the id of the previous assistant record
    consumed_to = offset                   # byte offset just past the last ALL-CLOSED point
    at = offset

    def _close(group: Optional[Dict[str, Any]]) -> None:
        if group is None:
            return
        reading.responses += 1
        if _priced(group.get("usage")) is not None:
            reading.priced += 1
        sample = sample_of_response(group)
        if sample is not None:
            reading.usable += 1
            reading.samples.append(sample)

    for raw in head.split(b"\n"):
        line_bytes = len(raw) + 1          # the newline this line consumed
        line_at = at
        at += line_bytes
        if not raw.strip():
            continue
        if len(raw) > TRANSCRIPT_LINE_CAP_BYTES:
            reading.records += 1           # counted, so an oversized record cannot hide
            continue
        try:
            record = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            reading.records += 1
            continue
        if not isinstance(record, dict):
            reading.records += 1
            continue
        reading.records += 1
        info = response_of(record)
        if info is None:
            # ⭐ A NON-ASSISTANT RECORD CLOSES EVERY OPEN RESPONSE, and that is not a convenience.
            # A user turn, a tool result or a system record appearing after an assistant response
            # is proof that the response finished — nothing more will be appended to it. Without
            # this, the newest response in every window stayed open and the instrument ran one
            # response behind forever, which on a hook that fires per tool call is most of them.
            #
            # ⚠ EVERY open group, not just the newest: with interleaving, "the newest" is not a
            # thing. A record that proves one response finished proves it of every response whose
            # records precede it, which is all of them.
            if len(closed) != len(groups):
                closed.update(groups)
                consumed_to = at           # everything before this point is accounted for
            continue
        reading.assistant += 1
        gid = info["id"]
        group = groups.get(gid)
        if group is None:
            groups[gid] = {"id": gid, "model": info["model"], "usage": info["usage"],
                           "blocks": list(info["blocks"]), "first_at": line_at}
            last_touched = gid
            continue
        # ⭐ WHAT `interleaved` MEASURES: a record joining a response that is NOT the one the
        # previous record joined, or rejoining one a non-assistant record had already PROVED
        # finished. Both are the same phenomenon — a response's records are not contiguous — and
        # both are what the first version mis-grouped. The withdrawal below is silent repair; the
        # counter is how anyone finds out it happened.
        if (last_touched is not None and last_touched != gid) or gid in closed:
            reading.interleaved += 1
        closed.discard(gid)
        group["blocks"].extend(info["blocks"])
        # ⛔ `_priced(...) is None`, NOT `.get("usage") is None` (review F3). A record can carry a
        # `usage` DICT that holds no readable output-token count — a partial envelope, or a
        # schema move that renamed the field — and the first version then treated the group as
        # already having its usage and never took a LATER record's real one. The response reads
        # as unpriced, `priced` stays 0, and `schema_blind` fires: **a false "the JSONL shape has
        # moved" over a transcript whose shape is fine.** The predicate must be *can I read a
        # number out of this*, which is what `_priced` answers and what every other consumer of
        # `usage` in this module already asks.
        if _priced(group.get("usage")) is None:
            group["usage"] = info["usage"]
        last_touched = gid

    open_groups = [g for gid, g in groups.items() if gid not in closed]

    # ⚠ PROGRESS IS GUARANTEED, not hoped for. Normally the cursor stops at the EARLIEST open
    # group so every open group can be closed next time. But a single group larger than the whole
    # read cap would leave the offset where it started and the reader would loop on it forever, so
    # a TRUNCATED window that closed nothing advances past itself and drops those groups instead.
    if open_groups and not (truncated and consumed_to == offset):
        new_offset = max(offset, min(g["first_at"] for g in open_groups))
    else:
        for g in open_groups:
            closed.add(g["id"])
        new_offset = offset + len(head) + len(newline)
    for gid, g in groups.items():
        if gid in closed:
            _close(g)
    reading.consumed = new_offset - offset
    reading.cursor = TranscriptCursor(session_id, new_offset)
    return reading
