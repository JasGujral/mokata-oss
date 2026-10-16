"""Parse a Claude Code `--output-format stream-json --verbose` stream into a Trajectory.

The raw stream is ARCHIVED VERBATIM beside this parse (`stream.jsonl`) — the parse is derived, so
any number here can be re-derived and re-audited from the raw bytes.

A TURN is one model call: one distinct `message.id` among `assistant` events (Claude Code may split
one response's content blocks over several events sharing an id — counting events would inflate
turns). The CONTEXT a turn re-sends is `input_tokens + cache_creation_input_tokens +
cache_read_input_tokens` of that call's usage: the G2 methodology's key analytic is that every turn
re-sends the growing context, so per-turn context growth is reported, not only cost.

Lines that are not JSON, and event types this parser does not know, are COUNTED (`unparsed`) rather
than dropped silently; a stream whose shape drifted shows up as a number, not as a quieter result.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Turn:
    index: int                 # 1-based
    message_id: str
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0
    text: str = ""
    tool_uses: List[Dict[str, Any]] = field(default_factory=list)   # {"id","name","input"}

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


@dataclass
class Trajectory:
    turns: List[Turn] = field(default_factory=list)
    #: Ordered record of everything that entered the conversation: {"seq","turn","role","text"}.
    #: `turn` is the number of model turns completed BEFORE the entry (so an entry at turn t is
    #: in the context of turn t+1 onward, until a compaction boundary).
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)  # {"turn","tool_use_id","is_error","text"}
    compactions: List[int] = field(default_factory=list)              # turn index at each boundary
    init: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    unparsed: int = 0

    # --- derived -----------------------------------------------------------------------------
    @property
    def num_turns(self) -> int:
        return len(self.turns)

    @property
    def context_per_turn(self) -> List[int]:
        return [t.context_tokens for t in self.turns]

    @property
    def total_context_tokens(self) -> int:
        """Everything re-sent across the run — the cost the growing context imposes."""
        return sum(self.context_per_turn)

    @property
    def max_context_tokens(self) -> int:
        return max(self.context_per_turn, default=0)

    @property
    def first_turn_context_tokens(self) -> int:
        """System prompt + tool schemas + first user message: the floor every turn pays."""
        return self.turns[0].context_tokens if self.turns else 0

    @property
    def growth_per_turn(self) -> float:
        """Mean per-turn context growth, (last - first) / (turns - 1). 0.0 for a one-turn run."""
        ctx = self.context_per_turn
        return (ctx[-1] - ctx[0]) / (len(ctx) - 1) if len(ctx) > 1 else 0.0

    @property
    def output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def tool_names(self) -> List[str]:
        return [u["name"] for t in self.turns for u in t.tool_uses]

    @property
    def mcp_servers(self) -> List[Dict[str, Any]]:
        servers = self.init.get("mcp_servers") or []
        return [s if isinstance(s, dict) else {"name": str(s)} for s in servers]

    @property
    def tools_offered(self) -> List[str]:
        return [str(t) for t in (self.init.get("tools") or [])]

    def summary(self) -> Dict[str, Any]:
        return {
            "num_turns": self.num_turns,
            "context_per_turn": self.context_per_turn,
            "total_context_tokens": self.total_context_tokens,
            "max_context_tokens": self.max_context_tokens,
            "first_turn_context_tokens": self.first_turn_context_tokens,
            "growth_per_turn": round(self.growth_per_turn, 2),
            "output_tokens": self.output_tokens,
            "compactions": list(self.compactions),
            "tool_calls": len(self.tool_names),
            "tool_errors": sum(1 for r in self.tool_results if r.get("is_error")),
            # Claude Code reports an API-EQUIVALENT figure even on a subscription login, where
            # nothing is billed per token. Named "notional" so nobody reads it as an invoice.
            "notional_cost_usd": self.result.get("total_cost_usd"),
            "duration_ms": self.result.get("duration_ms"),
            "result_subtype": self.result.get("subtype"),
            "is_error": bool(self.result.get("is_error")) or not self.result,
            "reported_num_turns": self.result.get("num_turns"),
            "unparsed_lines": self.unparsed,
        }

    def to_json(self) -> Dict[str, Any]:
        return {"summary": self.summary(), "turns": [dict(asdict(t), context_tokens=t.context_tokens)
                                                     for t in self.turns],
                "timeline": self.timeline, "tool_results": self.tool_results,
                "compactions": self.compactions, "init": self.init, "result": self.result,
                "unparsed": self.unparsed}

    def transcript(self) -> str:
        """The FULL trajectory as plain text, for the judge. Nothing is truncated: a judge that
        grades a summary is grading the summarizer."""
        lines = []
        for e in self.timeline:
            lines.append(f"[{e['role']} @turn {e['turn']}] {e['text']}")
        return "\n".join(lines)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif "content" in block:
                    parts.append(_text_of(block["content"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _text_of(content.get("content") or content.get("text") or "")
    return ""


def _int(d: Dict[str, Any], key: str) -> int:
    try:
        return int(d.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def parse_stream(lines: Iterable[str]) -> Trajectory:
    tr = Trajectory()
    by_id: Dict[str, Turn] = {}
    seq = 0

    def note(role: str, text: str) -> None:
        nonlocal seq
        if text:
            seq += 1
            tr.timeline.append({"seq": seq, "turn": len(tr.turns), "role": role, "text": text})

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            tr.unparsed += 1
            continue
        if not isinstance(ev, dict):
            tr.unparsed += 1
            continue
        etype, sub = ev.get("type"), ev.get("subtype")
        if etype == "system" and sub == "init":
            tr.init = ev
        elif etype == "system" and sub == "compact_boundary":
            tr.compactions.append(len(tr.turns))
            note("compact_boundary", json.dumps(ev.get("compact_metadata") or {}, sort_keys=True)
                 or "compaction")
        elif etype == "system":
            # Hook output and other system notices enter the context; keep their text.
            note(f"system:{sub or 'other'}", _text_of(ev.get("content") or ev.get("message")
                                                      or ev.get("output") or ""))
        elif etype == "assistant":
            msg = ev.get("message") or {}
            mid = str(msg.get("id") or f"anon-{seq}-{len(tr.turns)}")
            turn = by_id.get(mid)
            if turn is None:
                turn = Turn(index=len(tr.turns) + 1, message_id=mid)
                by_id[mid] = turn
                tr.turns.append(turn)
            usage = msg.get("usage") or {}
            # Split events repeat the same usage; take the max, never the sum.
            turn.input_tokens = max(turn.input_tokens, _int(usage, "input_tokens"))
            turn.cache_creation_input_tokens = max(turn.cache_creation_input_tokens,
                                                   _int(usage, "cache_creation_input_tokens"))
            turn.cache_read_input_tokens = max(turn.cache_read_input_tokens,
                                               _int(usage, "cache_read_input_tokens"))
            turn.output_tokens = max(turn.output_tokens, _int(usage, "output_tokens"))
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    turn.text = (turn.text + "\n" + block["text"]).strip()
                    seq += 1
                    tr.timeline.append({"seq": seq, "turn": turn.index - 1, "role": "assistant",
                                        "text": block["text"]})
                elif block.get("type") == "tool_use":
                    use = {"id": block.get("id"), "name": str(block.get("name") or ""),
                           "input": block.get("input") or {}}
                    turn.tool_uses.append(use)
                    seq += 1
                    tr.timeline.append({"seq": seq, "turn": turn.index - 1, "role": "tool_use",
                                        "text": f"{use['name']} "
                                                + json.dumps(use["input"], sort_keys=True)})
        elif etype == "user":
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                 for b in content):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        text = _text_of(block.get("content"))
                        tr.tool_results.append({"turn": len(tr.turns),
                                                "tool_use_id": block.get("tool_use_id"),
                                                "is_error": bool(block.get("is_error")),
                                                "text": text})
                        note("tool_result", text)
            else:
                note("user", _text_of(content))
        elif etype == "result":
            tr.result = ev
        else:
            tr.unparsed += 1
    return tr


def load_trajectory_json(data: Dict[str, Any]) -> Trajectory:
    """Rebuild a Trajectory from its archived `trajectory.json`."""
    tr = Trajectory(timeline=list(data.get("timeline") or []),
                    tool_results=list(data.get("tool_results") or []),
                    compactions=list(data.get("compactions") or []),
                    init=dict(data.get("init") or {}), result=dict(data.get("result") or {}),
                    unparsed=int(data.get("unparsed") or 0))
    for t in data.get("turns") or []:
        t = {k: v for k, v in t.items() if k != "context_tokens"}
        tr.turns.append(Turn(**t))
    return tr
