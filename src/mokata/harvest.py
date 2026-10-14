"""H-1c (+ H-1b's episodic half) — the session harvest: what a session did, proposed as a memory.

THE PROBLEM
-----------
A session ends — by `/clear`, by closing the terminal, by a compaction that summarises it away — and
what it learned leaves with it. mokata's memory only gains what someone explicitly remembers, so the
episodic layer (and the consolidation that summarises it) is fed by hand or not at all.

THE FIX
-------
Three hook points — `Stop` (every turn end), `PreCompact` and `SessionEnd` — each REWRITE one DRAFT
note for the session (`harvest__<session>`). The note is built from mokata's RECORD of the run, not
from the transcript, so no model is called inside a hook:

    the run and the stage it reached · the files it touched (D2's WorkingSet) · what the gates
    caught in the loop · its subagents' handbacks (D4) · the review verdict · its notes · and, from
    `Stop`, the model's own last message, truncated

Rewriting on every `Stop` is what makes a killed terminal (no `SessionEnd`) still leave its latest
draft. The note is bounded (`MAX_NOTE_CHARS`) — D1's budget discipline applied to memory.

⛔ A DRAFT IS NOT A MEMORY (P2: human-gate durable writes). Nothing here writes to the memory store.
The draft is working state under `temp_local/`; it becomes an `episodic` memory only when a person
accepts it (`mokata memory harvest --accept <id>`), which goes through the store's own human-gated,
secret-scanned `remember`. A hook has no human to ask, so it never pretends to have one. A decided
draft (accepted or rejected) is not proposed again unless the session did more since.

Only a session IN a pipeline (its C1 claim is bound) is harvested — the positive trigger every gate
keeps. Parallel runs: one draft per session, each carrying its own run.

Never raises: a harvest that cannot read writes nothing, and a hook that calls it always exits 0.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .errors import MokataError
from .state import StateStore
from .tdd_state import state_dir

HARVEST_PREFIX = "harvest__"
MAX_NOTE_CHARS = 900
LAST_MESSAGE_CHARS = 240
PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"

_SID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@dataclass(frozen=True)
class Draft:
    id: str                    # the session id the draft belongs to
    run_id: str
    subject: str
    value: str
    state: str                 # pending | accepted | rejected
    at: float

    def line(self) -> str:
        first = self.value.splitlines()[0] if self.value else ""
        return f"{self.id} · {self.state} · {first}"


def _key(session_id: str) -> str:
    return HARVEST_PREFIX + session_id


def _valid(session_id: Any) -> Optional[str]:
    if isinstance(session_id, str) and _SID.match(session_id.strip()):
        return session_id.strip()
    return None


def build_note(root: str, run_id: str, last_message: Optional[str] = None) -> str:
    """The episodic note for `run_id`, from the record. Bounded to `MAX_NOTE_CHARS`."""
    lines: List[str] = []
    try:
        pos = _position_for_run(root, run_id)
        if pos is not None:
            where = f"stage '{pos.stage}'" if pos.stage else "no stage entered"
            lines.append(f"- reached {where} · approach "
                         f"{'approved' if pos.approach else 'not approved'} · spec "
                         f"{'emitted' if pos.spec else 'not emitted'} · {pos.red} failing test(s)")
    except Exception:  # noqa: BLE001 — each fact is optional; the note is what can be read
        pass
    try:
        from .working_set import working_set
        ws = working_set(root, run_id)
        if ws.touched_files:
            shown = ws.touched_files[-8:]
            more = len(ws.touched_files) - len(shown) + ws.touched_dropped
            lines.append("- files: " + ", ".join(shown) + (f" (+{more} more)" if more else ""))
        if ws.notes:
            lines.append("- notes: " + " | ".join(ws.notes[-3:]))
    except Exception:  # noqa: BLE001
        pass
    sd = state_dir(root)
    try:
        from .progress_events import GATE_BLOCK, ProgressLog
        gates: Dict[str, int] = {}
        for e in ProgressLog.from_state_dir(sd).read_events_backward():
            if e.get("type") == GATE_BLOCK and e.get("run_id") == run_id:
                g = str((e.get("data") or {}).get("gate") or "gate")
                gates[g] = gates.get(g, 0) + 1
        if gates:
            lines.append("- caught in the loop: " + ", ".join(
                f"{g} ×{n}" for g, n in sorted(gates.items())))
    except Exception:  # noqa: BLE001
        pass
    try:
        from .progress_events import subagents_finished
        subs = subagents_finished(sd, run_id)
        if subs:
            kinds: Dict[str, int] = {}
            for e in subs:
                hb = (e.get("data") or {}).get("handback") or {}
                k = hb.get("status", "no handback") if isinstance(hb, dict) else "no handback"
                kinds[k] = kinds.get(k, 0) + 1
            lines.append(f"- subagents: {len(subs)} finished (" + ", ".join(
                f"{k} ×{n}" for k, n in sorted(kinds.items())) + ")")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .config import Surface
        from .progress_events import ship_review_gate
        gate = ship_review_gate(Surface.load(root), run_id=run_id)
        if gate.present:
            lines.append(f"- review: {gate.message}")
    except Exception:  # noqa: BLE001
        pass
    if isinstance(last_message, str) and last_message.strip():
        msg = " ".join(last_message.split())
        if len(msg) > LAST_MESSAGE_CHARS:
            msg = msg[:LAST_MESSAGE_CHARS - 1] + "…"
        lines.append(f"- last word: {msg}")
    note = "\n".join(lines)
    if len(note) > MAX_NOTE_CHARS:
        note = note[:MAX_NOTE_CHARS - 1] + "…"
    return note


def _position_for_run(root: str, run_id: str) -> Any:
    """`reanchor.Position` for a run id (the re-anchor reads by session; the harvest by run)."""
    from .reanchor import Position
    from .gate_hook import read_override
    from .progress_events import RUN_FINISHED, STAGE_ENTER, ProgressLog
    from .run_resolver import APPROACH_PREFIX, SPEC_PREFIX
    from .tdd_state import load
    sd = state_dir(root)
    store = StateStore(sd)
    red, _green = load(store, run_id)
    stage, finished = None, False
    for e in ProgressLog.from_state_dir(sd).read_events_backward():
        if e.get("run_id") != run_id:
            continue
        if e.get("type") == RUN_FINISHED:
            finished, stage = True, "ship"
            break
        if e.get("type") == STAGE_ENTER:
            stage = e.get("stage")
            break
    return Position("bound", run_id, stage, store.exists(APPROACH_PREFIX + run_id),
                    store.exists(SPEC_PREFIX + run_id), len(red), finished,
                    tuple(sorted(read_override(root, run_id))))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def harvest(root: str, session_id: Any, event: str,
            last_message: Optional[str] = None) -> Optional[Draft]:
    """Rewrite this session's draft from the record. None when the session is in no run, the record
    says nothing, or the draft was already decided with this same content."""
    sid = _valid(session_id)
    if not sid:
        return None
    try:
        from .run_intent import CLAIM_BOUND, session_claim
        claim, rid = session_claim(root, sid)
        if claim != CLAIM_BOUND or not rid:
            return None
        store = StateStore(state_dir(root))
        prior = store.read(_key(sid)) or {}
        if last_message is None and isinstance(prior, dict):
            last_message = prior.get("last_message")      # PreCompact/SessionEnd carry none
        note = build_note(root, rid, last_message)
        if not note:
            return None
        # The digest is over the RECORD, never the chat: the last message changes every turn, so
        # keying on it re-proposed a decided draft after a mere "thanks" (0.0.22 verification).
        digest = _digest(build_note(root, rid, None) or note)
        if isinstance(prior, dict) and prior.get("state") in (ACCEPTED, REJECTED) \
                and prior.get("decided_digest") == digest:
            return None                                   # decided; nothing new since
        subject = f"session {time.strftime('%Y-%m-%d', time.gmtime())} · run {rid}"
        record = {"session_id": sid, "run_id": rid, "subject": subject, "value": note,
                  "digest": digest, "state": PENDING, "event": event, "at": time.time(),
                  "last_message": last_message if isinstance(last_message, str) else None}
        if isinstance(prior, dict) and prior.get("decided_digest"):
            record["decided_digest"] = prior["decided_digest"]
        store.write(_key(sid), record)
        return Draft(sid, rid, subject, note, PENDING, record["at"])
    except Exception as exc:  # noqa: BLE001 — a harvest never costs the human their turn
        _say_degraded(exc)
        return None


def drafts(root: str, state: Optional[str] = PENDING) -> List[Draft]:
    """Every draft in this repo (newest first), optionally only one state. Never raises."""
    out: List[Draft] = []
    try:
        sd = state_dir(root)
        names = [n[:-5] for n in os.listdir(sd) if n.startswith(HARVEST_PREFIX)
                 and n.endswith(".json")]
    except OSError:
        return out
    store = StateStore(sd)
    for name in names:
        d = store.read(name)
        if not isinstance(d, dict):
            continue
        if state is not None and d.get("state") != state:
            continue
        out.append(Draft(str(d.get("session_id")), str(d.get("run_id")), str(d.get("subject")),
                         str(d.get("value")), str(d.get("state")), float(d.get("at") or 0)))
    out.sort(key=lambda x: x.at, reverse=True)
    return out


class HarvestError(MokataError):
    """An unknown or already-decided draft named on the CLI — a usage error, it propagates."""


def decide(root: str, draft_id: str, accept: bool, *, confirm: Any = None,
           assume_yes: bool = False) -> str:
    """Accept (through the memory store's human-gated `remember`) or reject one pending draft.
    Returns the message to print; raises `HarvestError` for an unknown or already-decided draft."""
    sid = _valid(draft_id)
    store = StateStore(state_dir(root))
    rec = store.read(_key(sid)) if sid else None
    if not isinstance(rec, dict) or rec.get("state") != PENDING:
        raise HarvestError(f"no pending session note `{draft_id}` — `mokata memory harvest` "
                           f"lists them")
    if accept:
        from .config import Surface
        from .memory.item import EPISODIC, MemoryItem
        from .memory.store import MemoryStore
        mem = MemoryStore.from_surface(Surface.load(root))
        item = MemoryItem(subject=rec["subject"], value=rec["value"], mtype=EPISODIC,
                          provenance={"source": "harvest", "session": sid,
                                      "run_id": rec.get("run_id")})
        res = mem.remember(item, confirm=confirm, assume_yes=assume_yes)
        if not res.committed:
            return f"not saved: {res.message}" if getattr(res, "message", "") else "not saved"
        state, msg = ACCEPTED, f"saved as episodic memory: {rec['subject']}"
    else:
        state, msg = REJECTED, f"dropped the note for {rec['subject']}"
    rec.update({"state": state, "decided_digest": rec.get("digest"), "decided_at": time.time()})
    store.write(_key(sid), rec)
    try:
        from .govern.ledger import AuditLedger
        AuditLedger.from_mokata_dir(os.path.join(root, ".mokata")).record(
            "harvest_decision", decision=state, subject=rec["subject"], run_id=rec.get("run_id"))
    except Exception:  # noqa: BLE001 — the decision stands whether or not it is recorded
        pass
    return msg


def briefing_line(root: str) -> Optional[str]:
    """The one SessionStart line when drafts are waiting, else None."""
    n = len(drafts(root))
    if not n:
        return None
    return (f"mokata: {n} session note(s) from earlier sessions are waiting for your review — "
            f"`mokata memory harvest` (accept saves one as episodic memory; nothing is saved "
            f"until you do)")


def _say_degraded(exc: BaseException) -> None:
    try:
        from .degrade import FAILURE_LOCAL_IO, note_degraded
        note_degraded("session-harvest", FAILURE_LOCAL_IO,
                      fallback="this session's note was not drafted; nothing else is affected",
                      detail=str(exc), fix="check permissions/disk under `.mokata/temp_local/`")
    except Exception:  # noqa: BLE001
        pass


__all__ = ["HARVEST_PREFIX", "MAX_NOTE_CHARS", "PENDING", "ACCEPTED", "REJECTED", "Draft",
           "build_note", "harvest", "drafts", "decide", "HarvestError", "briefing_line"]
