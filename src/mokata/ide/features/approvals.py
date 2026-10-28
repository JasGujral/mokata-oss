"""0.0.26 P38 `APPROVE-IN-IDE` — the approval card's routes (PRD G6 / R29).

Everything goes through mokata's EXISTING approval channel (`mokata/approval.py`): the IDE never
proposes, never redeems and never writes a proposal record itself — it calls `approval.approve` /
`approval.reject` (the same functions `mokata approve` calls), with `actor="ide-tty"` and the audit
ledger, and only after the human typed back a one-time code the server printed to its OWN terminal
(`ide/approve_codes.py` — the decision, the threat model and why the pairing token is not enough).

  GET  /api/approvals                  pending proposals (in full) + whether Approve-here is on
                                       (and why not); `?id=` also answers that one proposal in any
                                       state, with its ledger rows (the card after a decision)
  POST /api/approvals/code   {id, decision}         print a one-time code to the server's terminal
  POST /api/approvals/decide {id, decision, code}   approve / reject with that code

No route answer ever carries a code. Without a terminal (`mokata ide` launched detached, or by an
agent) `code` answers 409 with the reason and the CLI twin; the card shows them (option D).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from ... import approval
from ...awaiting import APPROVE_CMD
from .. import approve_codes as AC

ACTOR = "ide-tty"                 # on the ledger: decided in the window, code from its terminal
REJECT_CMD = "mokata approve --reject {proposal_id}"
LEDGER_TAIL_BYTES = 256 * 1024    # the card's ledger rows come from the ledger's tail, never a scan


def card(p: approval.Proposal, now: Optional[float] = None) -> Dict[str, Any]:
    """One proposal as the card shows it — the write IN FULL (`approval.render`, or the group render)
    plus the commands that decide it in a terminal."""
    now = time.time() if now is None else now
    expired = p.expired(now)
    state = p.status if p.status in (approval.STATUS_USED, approval.STATUS_REJECTED) else (
        "expired" if expired else p.status)
    group = p.tool.endswith("_group") or "would write ALL" in p.preview
    return {
        "proposal_id": p.proposal_id, "tool": p.tool, "target": p.target, "summary": p.summary,
        "run": p.run_id, "status": state, "decided_by": p.approved_by,
        "decided_at": p.approved_at, "created_at": p.created_at,
        "expires_in": 0 if expired else p.expires_in(now),
        "render": approval.render_group(p, now) if group else approval.render(p, now),
        "command": APPROVE_CMD.format(proposal_id=p.proposal_id),
        "reject_command": REJECT_CMD.format(proposal_id=p.proposal_id),
        "decidable": state in (approval.STATUS_PROPOSED, approval.STATUS_APPROVED),
    }


def ledger_rows(root: str, proposal_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """This proposal's `write_approval` ledger rows (approved / rejected / redeemed), newest last —
    read from the ledger's TAIL only (bounded; the chain itself is `mokata audit`'s job)."""
    from ... import MOKATA_DIR
    from ...govern.ledger import AuditLedger
    path = AuditLedger.path_for(os.path.join(root, MOKATA_DIR))
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - LEDGER_TAIL_BYTES))
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue                                   # the first line of a tail may be partial
        if isinstance(e, dict) and e.get("kind") == approval.LEDGER_KIND \
                and e.get("proposal") == proposal_id:
            out.append({k: e.get(k) for k in ("seq", "at", "decision", "actor", "approved_by",
                                              "tool", "target", "content_hash", "run", "committed",
                                              "entry_hash")})
    return out[-limit:]


def routes(reg, ctx) -> None:
    import asyncio
    from starlette.responses import JSONResponse

    state = ctx.state

    def _tty() -> Dict[str, Any]:
        ok, why = state.approve_tty
        return {"available": bool(ok), "reason": why}

    def _ledger():
        from ... import MOKATA_DIR
        from ...govern.ledger import AuditLedger
        return AuditLedger.from_mokata_dir(os.path.join(state.root, MOKATA_DIR))

    def _with_ledger(c: Dict[str, Any]) -> Dict[str, Any]:
        # an APPROVED card (e.g. approved in a terminal) shows the ledger row that says so
        return {**c, "ledger": ledger_rows(state.root, c["proposal_id"])} if c["status"] == "approved" else c

    def read(request) -> Dict[str, Any]:
        if state.demo:
            return {"sample": True, "pending": [], "approve_here": _tty(), "one": None}
        now = time.time()
        out: Dict[str, Any] = {"sample": False, "approve_here": _tty(),
                               "pending": [_with_ledger(card(p, now)) for p in approval.pending(state.root, now)],
                               "one": None}
        pid = ctx.q(request, "id")
        if pid:
            p = approval.load(state.root, pid)
            out["one"] = None if p is None else {**card(p, now),
                                                 "ledger": ledger_rows(state.root, pid)}
        return out

    def _bad(status: int, error: str, note: str, **extra) -> Any:
        return JSONResponse({"error": error, "note": note, **extra}, status_code=status)

    def _decidable(pid: str):
        p = approval.load(state.root, pid)
        if p is None:
            return None, _bad(409, "UnknownProposal", "no such proposal — it never existed, expired "
                                                      "or was pruned")
        c = card(p)
        if not c["decidable"]:
            return None, _bad(409, "NotDecidable", f"this proposal is {c['status']} — nothing to "
                                                   "decide", proposal=c)
        return p, None

    async def code(request):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        pid, decision = str(b.get("id") or ""), str(b.get("decision") or "")
        if not pid or decision not in AC.DECISIONS:
            return _bad(400, "BadRequest", "id and decision (approve|reject) are required")
        if state.demo:
            return _bad(409, "Sample", AC.SAMPLE)
        ok, why = state.approve_tty
        if not ok:
            return _bad(409, "NoTerminal", why, command=APPROVE_CMD.format(proposal_id=pid),
                        reject_command=REJECT_CMD.format(proposal_id=pid))
        p, err = await asyncio.to_thread(_decidable, pid)
        if err is not None:
            return err
        minted, refusal, retry = state.codes.mint(pid, decision)
        if minted is None:
            return _bad(429, "RateLimited", refusal, retry_in=round(retry, 1))
        shown = f"{minted[:4]}-{minted[4:]}"
        verb = "APPROVE" if decision == "approve" else "REJECT"
        printed = AC.say(f"\n  mokata ide · to {verb} {pid} ({p.tool} → {p.target or '-'}) type "
                         f"this code in the window:  {shown}   (one use, {int(AC.CODE_TTL)} s)\n\n")
        if not printed:                      # the terminal went away: the code is void at once
            state.codes.void(pid)
            return _bad(409, "NoTerminal", AC.NO_TTY, command=APPROVE_CMD.format(proposal_id=pid))
        return JSONResponse({"printed": True, "expires_in": int(AC.CODE_TTL),
                             "tries": AC.MAX_TRIES,
                             "note": "a one-time code was printed in the terminal running "
                                     "`mokata ide --foreground` — type it here"})

    async def decide(request):
        b = await ctx.body(request)
        b = b if isinstance(b, dict) else {}
        pid, decision = str(b.get("id") or ""), str(b.get("decision") or "")
        if not pid or decision not in AC.DECISIONS:
            return _bad(400, "BadRequest", "id and decision (approve|reject) are required")
        if state.demo:
            return _bad(409, "Sample", AC.SAMPLE)
        ok, why = state.approve_tty
        if not ok:                                   # no terminal → no code could exist
            return _bad(409, "NoTerminal", why, command=APPROVE_CMD.format(proposal_id=pid))
        good, why = state.codes.check(pid, decision, b.get("code"))
        if not good:
            return _bad(403, "BadCode", why)

        def apply() -> Dict[str, Any]:
            fn = approval.approve if decision == "approve" else approval.reject
            res = fn(state.root, pid, actor=ACTOR, ledger=_ledger())
            p = res.proposal or approval.load(state.root, pid)
            return {"ok": res.ok, "code": res.code, "message": res.message, "decision": decision,
                    "proposal": card(p) if p else None, "ledger": ledger_rows(state.root, pid)}
        out = await asyncio.to_thread(apply)
        return JSONResponse(out, status_code=200 if out["ok"] else 409)

    reg.register("/api/approvals", read, kind="json", feature="approvals")
    reg.register("/api/approvals/code", code, kind="action", feature="approvals")
    reg.register("/api/approvals/decide", decide, kind="action", feature="approvals")
