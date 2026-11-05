"""ID-SESSION-BROWSER · ID-SESSION-SWITCHER · ID-NEEDS-YOU (0.0.25 Lane ID3).

Doc 109 §18.3 / §18.4 / §16 #11, as DATA the page draws and the `ide_sessions` MCP tool returns:

  * `browser`   — every session (live + past): what it changed, the gates that fired for its run,
                  its token spend (estimated or measured, never blended), its ledger links, its
                  status, and what (if anything) it is waiting on a human for.
  * `needs_you` — the attention queue, sorted by RISK, not recency (doc 109 §16 #11).

Everything is DERIVED from state mokata already keeps — the snapshot timeline, the C1 run claims,
the live-session registry, the approval store, the typed event store and (inside the IDE server
only) the event bus ring. Nothing here writes, spawns, polls an external service or calls a model.

SECRET-SAFETY (the `awaiting` rule): a pending proposal is surfaced by its id, its tool and the
command that resolves it — never by its `summary` or `preview`, which are the human's to read at
the terminal with `mokata approve <id>`.

HONESTY (doc 85 §7g): every derived status carries the `basis` it was derived from, and what the
stores cannot know is said, not guessed — reads are not recorded (snapshots record writes), a
session's status without a live signal is `past` with basis `no-signal`, and a write whose content
was too large to keep has an unmeasured delta (`None`), never a zero.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ------------------------------------------------------------------------------------- constants

LIVE_SECS = 15 * 60          # a session that wrote within this window and has no end signal is live
MAX_EVENTS = 5000            # typed-store rows read per view (newest)
MAX_DELTAS = 2000            # writes whose line delta is computed (newest); older: writes only
SECRET_PAIR_SECS = 5.0       # a secret-scan hit this close to a block names the block a secret one

# Gate decisions that leave a session blocked until something changes — ONE definition, shared
# with the next-step hints (`mokata.gate_blocks`, 0.0.27 P14).
from ..gate_blocks import BLOCKING, in_chain as _in_chain, pending_block as _pending_block  # noqa: E402

# The needs-you RISK score (doc 109 §16 #11: sorted by risk, not recency). Deterministic and shown:
# the payload carries `RISK_FORMULA`, so the order is never a mystery.
SECRET_BLOCK, AWAITING, GATE_BLOCK, TURN_ENDED = (
    "secret-blocked", "awaiting-approval", "blocked-at-gate", "turn-ended")
RISK = {SECRET_BLOCK: 90, AWAITING: 70, GATE_BLOCK: 60, TURN_ENDED: 10}
RISK_FORMULA = ("risk = reason weight (secret-blocked 90 · awaiting-approval 70 · "
                "blocked-at-gate 60 · turn-ended 10) + min(9, minutes waiting // 10); "
                "ties: longest waiting first")
REASON_TEXT = {
    SECRET_BLOCK: "a secret was blocked — the write cannot land until the secret is removed",
    AWAITING: "AWAITING APPROVAL — mokata is waiting on a human (this is not an error)",
    GATE_BLOCK: "blocked at a gate, and nothing has been written since",
    TURN_ENDED: "the agent ended its turn — it is your move",
}

#: 0.0.27 P46 — outside the risk ranking (see `_explain_item`).
EXPLAIN_QUEUED = "explain-queued"
EXPLAIN_TEXT = ("an \"Explain changes\" request is queued for this session's next turn — it runs "
                "when you next send a message there")

LIVE, ENDED, PAST = "live", "ended", "inactive"     # P33: the index's statuses (sessions.py)
READS_NOTE = ("reads are not recorded — mokata snapshots WRITES; a `before` is the file as the "
              "session found it, captured at its first write")


# ------------------------------------------------------------------------------------- helpers

def _epoch(ts: str) -> float:
    """An event store `ts` (ISO-8601, UTC, second resolution, `Z`) as epoch seconds; 0 when not."""
    from ..gate_blocks import epoch_of
    return epoch_of(ts)


def _events(root: str, types: Sequence[str]) -> List[Any]:
    """The newest `MAX_EVENTS` typed-store rows of `types` (oldest first). Never raises."""
    from ..events import query
    return query(root, types=list(types), limit=MAX_EVENTS)


def _pending(root: str, now: float) -> List[Any]:
    """Live proposals NOT yet approved — the ones a human must act on. An approved-but-unredeemed
    proposal is waiting on the AGENT's re-call, not on a person, so it is not a needs-you."""
    from .. import approval
    return [p for p in approval.pending(root, now) if not p.approved]


def _bus_signals(bus_events: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Per Claude session, from the IDE server's bus ring (oldest first): the last lifecycle kind
    and when, and whether it ended. Empty outside the server (the MCP twin has no bus)."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in bus_events or ():
        sid = e.get("session")
        kind = e.get("kind")
        # 0.0.27 P12 — a `tool` event (a Bash/Read/… call the hook adapter reports) is activity too.
        if not isinstance(sid, str) or not sid or kind not in ("edit", "tool", "turn_end",
                                                              "session_start", "session_end"):
            continue
        sig = out.setdefault(sid, {"last": "", "at": 0.0, "ended": False, "started": False})
        sig["last"], sig["at"] = kind, float(e.get("ts") or 0.0)
        if kind == "session_end":
            sig["ended"] = True
        elif kind == "session_start":
            sig["started"], sig["ended"] = True, False
        else:
            sig["ended"] = False
    return out


def _live_runs(root: str, runs: Iterable[str]) -> set:
    from ..run_resolver import live_runs
    cands = {r for r in runs if r}
    return set(live_runs(root, cands)) if cands else set()


# ------------------------------------------------------------------------------------- browser

def browser(root: str, *, now: Optional[float] = None,
            bus_events: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """ID-SESSION-BROWSER + ID-NEEDS-YOU: every session with its status, changes, gates, spend,
    ledger links and needs-you reasons, plus the risk-sorted queue. Read-only."""
    from . import sessions as SS
    from ..events.schema import GATE_DECISION, SECRET_SCAN_HIT, TOKEN_SPEND
    now = time.time() if now is None else now
    # P33 — ONE session source: the durable index ∪ snapshots ∪ claims ∪ worktrees (`sessions`);
    # status comes from it (SessionEnd marker / the Claude process / heartbeat), not the bus ring.
    rows = {r.id: r.facts() for r in SS.sessions(root, now)}
    signals = _bus_signals(bus_events)
    # O10 — a resumed session is JOINED to the one it continues: same run, and each names the other.
    for row in rows.values():
        row.setdefault("resumed_by", [])
    for row in rows.values():
        frm = row.get("resumed_from")
        if frm and frm in rows:
            rows[frm]["resumed_by"].append(row["id"])
    events = _events(root, (GATE_DECISION, SECRET_SCAN_HIT, TOKEN_SPEND))
    pending = _pending(root, now)
    live = _live_runs(root, (r["run"] for r in rows.values()))

    def mine(ev: Any, row: Dict[str, Any]) -> bool:
        return _in_chain(row["run"], row["chain"], ev.run_id, ev.session_id)

    try:
        from .context import explain_pending
        asks = explain_pending(root)
    except (OSError, ValueError):
        asks = {}
    claimed_proposals = set()
    out: List[Dict[str, Any]] = []
    for row in rows.values():
        evs = [e for e in events if mine(e, row)]
        gates = [e for e in evs if e.type == GATE_DECISION]
        blocked = [e for e in gates if str(e.data.get("decision", "")).lower() in BLOCKING]
        secrets_hit = [e for e in evs if e.type == SECRET_SCAN_HIT]
        spend = [e for e in evs if e.type == TOKEN_SPEND]
        sig = signals.get(row["id"])
        last = max(float(row.get("last") or 0.0), sig["at"] if sig else 0.0)
        status, basis = row["status"], row["status_basis"]
        if status != ENDED and sig and sig["ended"]:
            status, basis = ENDED, "session_end hook (bus)"
        elif status != LIVE and status != ENDED and row["run"] and row["run"] in live:
            status, basis = LIVE, "live MCP process registered for its run"
        reasons: List[Dict[str, Any]] = []
        for p in pending:
            if row["run"] and p.run_id == row["run"]:
                claimed_proposals.add(p.proposal_id)
                reasons.append(_awaiting_item(p, now))
        # G15 — only a LIVE session can be waiting on a gate or a turn: an ended (or silent)
        # session's old block ages out of needs-you instead of heading the queue for days.
        if blocked and status == LIVE:
            b = _pending_block(gates, float(row.get("last") or 0.0))
            if b is not None:
                at = _epoch(b.ts)
                gate = str(b.data.get("gate", ""))
                # a secret block: the gate says so, or a secret-scan hit landed with the block
                # (same run, within SECRET_PAIR_SECS — the store's ts is second-resolution)
                secret = "secret" in gate.lower() or any(
                    abs(_epoch(s.ts) - at) <= SECRET_PAIR_SECS for s in secrets_hit)
                reasons.append(_item(SECRET_BLOCK if secret else GATE_BLOCK, at, now,
                                     {"gate": gate, "decision": str(b.data.get("decision", "")),
                                      "subject": str(b.data.get("subject", ""))[:300],
                                      "ledger_seq": b.ledger_seq,
                                      "basis": "typed event store (gate_decision)"}))
        if sig and sig["last"] == "turn_end" and status == LIVE:
            reasons.append(_item(TURN_ENDED, sig["at"], now, {"basis": "Stop hook (bus)"}))
        reasons.sort(key=lambda r: (-r["risk"], r["since"]))
        risk = reasons[0]["risk"] if reasons else 0
        if row["id"] in asks:
            reasons.append(_explain_item(asks[row["id"]], now))
        out.append({**row, "status": status, "status_basis": basis, "last": last,
                    "gates": {"fired": len(gates), "blocked": len(blocked),
                              "by_gate": _count(str(g.data.get("gate", "")) for g in gates)},
                    "secret_hits": len(secrets_hit),
                    "tokens": _spend(spend),
                    "ledger": sorted({e.ledger_seq for e in evs if e.ledger_seq is not None})[-20:],
                    "needs_you": reasons, "risk": risk})
    out.sort(key=lambda r: (-r["risk"], -(r["last"] or 0.0)))
    unattributed = [_awaiting_item(p, now) for p in pending
                    if p.proposal_id not in claimed_proposals]
    queue = [dict(reason, session=r["id"], short=r["short"], run=r["run"])
             for r in out for reason in r["needs_you"]]
    queue += [dict(item, session="", short="", run=item["detail"].get("run", ""))
              for item in unattributed]
    queue.sort(key=lambda q: (not q.get("ranked", True), -q["risk"], q["since"]))
    return {"sample": False, "sessions": out, "needs_you": queue, "count": len(out),
            "waiting": len(queue), "risk_formula": RISK_FORMULA, "reads": READS_NOTE,
            "bus": bool(signals), "live_window_secs": LIVE_SECS}


def _count(names: Iterable[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for n in names:
        out[n or "gate"] = out.get(n or "gate", 0) + 1
    return out


def _spend(events: Sequence[Any]) -> Dict[str, Any]:
    """Token spend, measured and estimated kept APART (`TokenSpend.estimated` is the honesty bit)."""
    def total(est: bool) -> Dict[str, int]:
        sel = [e for e in events if bool(e.data.get("estimated", True)) is est]
        return {"input": sum(int(e.data.get("input_tokens") or 0) for e in sel),
                "output": sum(int(e.data.get("output_tokens") or 0) for e in sel),
                "events": len(sel)}
    return {"measured": total(False), "estimated": total(True)}


def _item(reason: str, since: float, now: float, detail: Dict[str, Any]) -> Dict[str, Any]:
    waited = max(0.0, now - since) if since else 0.0
    return {"reason": reason, "text": REASON_TEXT[reason], "since": since,
            "waiting_secs": int(waited),
            "risk": RISK[reason] + min(9, int(waited // 600)), "detail": detail}


def _explain_item(ask: Dict[str, Any], now: float) -> Dict[str, Any]:
    """0.0.27 P46 — an "Explain changes" request queued for this session's next turn. NOT a risk:
    it is listed after every ranked reason (`ranked: False`) and never changes a row's risk. It
    leaves the queue when the session's next turn delivers it, or when the IDE cancels it."""
    at = float(ask.get("at") or 0.0)
    return {"reason": EXPLAIN_QUEUED, "text": EXPLAIN_TEXT, "since": at,
            "waiting_secs": int(max(0.0, now - at)) if at else 0, "risk": 0, "ranked": False,
            "detail": {"target": ask.get("target"), "request_id": ask.get("request_id"),
                       "basis": "IDE explain request (one slot per session)"}}


def _awaiting_item(p: Any, now: float) -> Dict[str, Any]:
    from ..awaiting import APPROVE_CMD, LIST_CMD
    return _item(AWAITING, p.created_at, now,
                 {"proposal_id": p.proposal_id, "tool": p.tool, "run": p.run_id,
                  "command": APPROVE_CMD.format(proposal_id=p.proposal_id), "list": LIST_CMD,
                  "expires_in": p.expires_in(now), "basis": "approval store (pending)"})


def needs_you(root: str, **kw: Any) -> Dict[str, Any]:
    b = browser(root, **kw)
    return {"sample": False, "needs_you": b["needs_you"], "waiting": b["waiting"],
            "risk_formula": b["risk_formula"]}


# ------------------------------------------------------------------------------------- SAMPLE

def sample_browser(sessions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The `--demo` server's browser: its invented sessions, one marked as waiting, all SAMPLE."""
    now = time.time()
    out = []
    for i, s in enumerate(sessions):
        reasons = [] if i else [dict(_item(AWAITING, now - 420, now,
                                           {"proposal_id": "p-sample000000", "tool": "spec_emit",
                                            "run": s["run"], "command": "mokata approve p-sample000000",
                                            "list": "mokata approve --list", "expires_in": 3000,
                                            "basis": "SAMPLE"}))]
        out.append({**s, "status": LIVE if i == 0 else PAST, "status_basis": "SAMPLE",
                    "gates": {"fired": 3 - i, "blocked": 0, "by_gate": {"tdd": 3 - i}},
                    "secret_hits": 0, "tokens": _spend([]), "ledger": [], "needs_you": reasons,
                    "risk": reasons[0]["risk"] if reasons else 0})
    queue = [dict(r, session=s["id"], short=s["short"], run=s["run"])
             for s in out for r in s["needs_you"]]
    return {"sample": True, "sessions": out, "needs_you": queue, "count": len(out),
            "waiting": len(queue), "risk_formula": RISK_FORMULA, "reads": READS_NOTE,
            "bus": False, "live_window_secs": LIVE_SECS}


__all__ = ["browser", "needs_you",
           "sample_browser", "RISK", "RISK_FORMULA", "BLOCKING", "LIVE", "ENDED", "PAST",
           "AWAITING", "GATE_BLOCK", "SECRET_BLOCK", "TURN_ENDED",
           "LIVE_SECS"]
