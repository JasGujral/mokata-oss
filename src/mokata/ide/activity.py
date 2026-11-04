"""0.0.27 P12 `ID-ACTIVITY-PANEL` — what the agent is doing right now, and what it did, for every
session, in one panel (doc 113 §5b Lane P; doc 109 §4.1, §14 "Agent threads", §18.4; PRD §5.3).

Two reads, both DERIVED from stores mokata already keeps — nothing here writes, spawns or polls:

  * `live(root, …)`   the panel's opening picture: every LIVE session (plus the focused one and any
                      session waiting on a human) with its pipeline phase, what it last ran (the
                      tool, the file), its files, its RED/GREEN test record, its gate verdicts and
                      what it is waiting on; beside them the IDE's jobs (progress, Cancel) and the
                      risk-sorted needs-you queue. After this one read the page is PUSHED every
                      change over the SSE bus (`edit` / `tool` / `turn_end` / `event` / `job` /
                      `approval`) and never polls.
  * `history(root, session, …)`
                      one session's activity as a TIMELINE, newest first, PAGED on the server by a
                      keyset cursor (`before`) — never capped: a session with tens of thousands of
                      events is walked page by page. A resumed session's history is JOINED to the
                      sessions it continues (the O10 chain), so the work on both sides of the break
                      reads as one story.

THE ITEM — one typed, extensible shape for everything on the timeline (P46's change explainers
will be one more `type`; `register_type` is their door):

    {"id":      a stable id ("ev:<seq>", "snap:<id>", "resume:<session>:<t>")
     "t":       epoch seconds,
     "type":    one of TYPES (phase · gate · test · review · approval · secret · memory · tokens ·
                tool · edit · before · resume · subagent · explainer),
     "title":   one content-free line,
     "verdict": "" | ok | failed | passed | blocked | advisory | red | green | approved | rejected
                | hit | waiting,
     "session": the Claude session id, "run": the run id,
     "source":  events | snapshots | claims   (where the fact comes from — §7g),
     "ledger_seq": the audit-ledger row backing it (None = no integrity claim),
     "detail":  per type (a path, a gate name, a test id, a reason the ledger keeps, …)}

THE MOKATA EDGE (doc 109 §14, "Agent threads"): this is governed activity, not a raw tool log.
Beside every tool call sit the gate verdicts that let it through or stopped it, the approvals a
human still owes, the RED→GREEN of the run's tests and the pipeline phase the agent is in — none of
which px0 has, because px0 has no gates, no ledger, no spec and no run.

HONESTY: `activity.state` says what the bus has SEEN (`basis`), and a session the bus never heard
from says `unknown`, not `idle`. Secret-safety: a pending proposal is named by id, tool and the
command that resolves it, never by its summary or preview (`session_views`' rule).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import MokataError
from . import snapshots as S

PAGE = 200                   # events per history page (the cursor reaches every row)
PAGE_MAX = 2000
TITLE_MAX = 200
WHY_MAX = 300

# ------------------------------------------------------------------------------- the item types

#: type -> one line saying what it is. CLOSED for the page (an unknown type is drawn as "activity")
#: but OPEN for a stage: `register_type` adds one. P46 (change explainers) registers `explainer`.
TYPES: Dict[str, str] = {
    "phase": "a pipeline phase entered or finished (brainstorm · spec · test · develop · review · ship)",
    "gate": "a gate's verdict on a write, a stop, a plan or a spec",
    "test": "a test seen RED or GREEN by the TDD guard (the run's test record)",
    "review": "the review gate's verdict",
    "approval": "a human's yes or no (a minted approval, an override, a plan approved)",
    "secret": "a secret the scanner caught (its kind and where — never the text)",
    "memory": "a memory read/write/heal",
    "tokens": "token spend for one labelled unit of work (measured or estimated, never blended)",
    "tool": "a tool or command the agent ran (its name, never its input or output)",
    "edit": "a file the agent wrote (a snapshot the IDE can diff)",
    "before": "the file as the session found it, captured at its first write",
    "resume": "the conversation was resumed (a new session carrying this one's run)",
    "subagent": "a subagent finished (with or without its HandbackReport)",
    "explainer": "a change-explainer page (0.0.27 P46): the change set explained, every claim "
                 "cited — opens in the Review tab; marked stale once the code moved past it",
}
EVENT_TYPE_OF: Dict[str, Tuple[str, ...]] = {
    # item type -> the typed-store event types that produce it (for a `types=` filter)
    "phase": ("phase_transition",), "gate": ("gate_decision",), "test": ("gate_decision",),
    "review": ("gate_decision",), "subagent": ("gate_decision",),
    "approval": ("approval_decision",), "secret": ("secret_scan_hit",), "memory": ("memory_op",),
    "tokens": ("token_spend",), "tool": ("tool_call",),
}
BLOCKING = frozenset({"block", "blocked", "deny", "denied", "refused", "fail", "failed",
                      "would-block", "no-handback"})
PASSING = frozenset({"pass", "passed", "allow", "allowed", "ok", "approved", "handback"})


def register_type(name: str, description: str) -> None:
    """Add an item type (a later stage's door — P46 `explainer`). Re-registering is a no-op."""
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError("an item type is an identifier")
    TYPES.setdefault(name, str(description))


class ActivityRefused(MokataError, ValueError):
    """A bad question (no session, a malformed cursor) — a 400, never a 500."""


# ------------------------------------------------------------------------------- helpers

def _epoch(ts: Any) -> float:
    from .session_views import _epoch as e
    return e(ts)


def _clip(s: Any, n: int = TITLE_MAX) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _verdict(decision: str) -> str:
    d = (decision or "").lower()
    if d in BLOCKING:
        return "blocked"
    if d in PASSING:
        return "passed"
    if d in ("advisory", "would-pass"):
        return "advisory"
    return ""


def _item(id_: str, t: float, type_: str, title: str, *, verdict: str = "", session: str = "",
          run: str = "", source: str, ledger_seq: Optional[int] = None,
          detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"id": id_, "t": float(t), "type": type_, "title": _clip(title), "verdict": verdict,
            "session": session or "", "run": run or "", "source": source,
            "ledger_seq": ledger_seq, "detail": detail or {}}


# ------------------------------------------------------------------------------- events -> items

def item_from_event(ev: Any, ledger: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One typed-store row (+ the ledger entry it derives from, when the page fetched it) as an
    item. The ledger entry is what carries the parts the typed store refuses on purpose: the TDD
    guard's RED/GREEN and test id, a gate's human-readable WHY (bounded, as `why_timeline`)."""
    d = ev.data if isinstance(ev.data, dict) else {}
    led = ledger or {}
    base = dict(id_=f"ev:{ev.seq}", t=_epoch(ev.ts), session=ev.session_id, run=ev.run_id,
                source="events", ledger_seq=ev.ledger_seq)
    why = _clip(led.get("reason") or led.get("why") or "", WHY_MAX) if led else ""
    if ev.type == "gate_decision":
        gate, decision = str(d.get("gate", "")), str(d.get("decision", ""))
        subject = _clip(d.get("subject", ""))
        if gate == "tdd" or led.get("kind") == "tdd":
            ev_kind = str(led.get("event") or "")
            test = _clip(led.get("test") or "")
            verdict = ev_kind if ev_kind in ("red", "green") else _verdict(ev_kind or decision)
            title = {"red": f"test RED · {test}", "green": f"test GREEN · {test}",
                     "blocked": f"RED before GREEN · {test} has no failing run",
                     "allowed": f"implementation allowed · {test}"}.get(
                         ev_kind, f"tdd · {decision}" + (f" · {test}" if test else ""))
            return _item(type_="test", title=title, verdict=verdict,
                         detail={"test": test, "event": ev_kind, "decision": decision,
                                 "basis": "ledger (tdd)" if led else "typed store"}, **base)
        if gate == "review":
            v = _verdict(decision)
            return _item(type_="review", title=f"review {decision}" + (f" · {subject}" if subject
                                                                       else ""),
                         verdict=v or decision.lower(), detail={"decision": decision,
                                                                "stage": subject, "why": why},
                         **base)
        if gate == "subagent-contract":
            return _item(type_="subagent", title="subagent finished · " + decision,
                         verdict=_verdict(decision),
                         detail={"decision": decision, "stage": subject}, **base)
        v = _verdict(decision)
        title = f"{gate or 'gate'} · {decision or 'recorded'}" + (f" · {subject}" if subject else "")
        return _item(type_="gate", title=title, verdict=v,
                     detail={"gate": gate, "decision": decision, "subject": subject, "why": why},
                     **base)
    if ev.type == "phase_transition":
        phase, outcome = str(d.get("phase", "")), str(d.get("outcome", "enter"))
        title = (f"entered {phase}" if outcome == "enter" else
                 f"{phase} finished" if outcome == "finish" else f"{phase} · {outcome}")
        return _item(type_="phase", title=title, detail={"phase": phase, "outcome": outcome},
                     **base)
    if ev.type == "approval_decision":
        decision = str(d.get("decision", ""))
        dl = decision.lower()
        v = ("approved" if dl in ("approved", "approve", "accepted", "yes", "override")
             else "rejected" if dl in ("rejected", "reject", "declined", "no") else "")
        return _item(type_="approval", title=f"approval · {decision}" +
                     (f" · {_clip(d.get('subject', ''))}" if d.get("subject") else ""),
                     verdict=v, detail={"decision": decision, "subject": _clip(d.get("subject", "")),
                                        "actor": str(d.get("actor", "") or ev.actor),
                                        "approval_seq": d.get("approval_seq"), "why": why}, **base)
    if ev.type == "secret_scan_hit":
        return _item(type_="secret", title=f"secret caught · {d.get('kind', '')}" +
                     (f" · {_clip(d.get('where', ''))}" if d.get("where") else ""), verdict="hit",
                     detail={"kind": str(d.get("kind", "")), "where": _clip(d.get("where", "")),
                             "count": d.get("count", 1)}, **base)
    if ev.type == "memory_op":
        return _item(type_="memory", title=f"memory · {d.get('op', '')}" +
                     (f" · {d.get('mtype')}" if d.get("mtype") else ""),
                     detail={"op": str(d.get("op", "")), "mtype": str(d.get("mtype", "")),
                             "count": d.get("count", 1)}, **base)
    if ev.type == "token_spend":
        est = bool(d.get("estimated", True))
        return _item(type_="tokens", title=f"tokens · {d.get('label', '')} · "
                     f"{d.get('input_tokens', 0)} in / {d.get('output_tokens', 0)} out"
                     + (" (estimated)" if est else " (measured)"),
                     detail={"label": str(d.get("label", "")), "input": d.get("input_tokens", 0),
                             "output": d.get("output_tokens", 0), "estimated": est,
                             "model": str(d.get("model", ""))}, **base)
    if ev.type == "tool_call":
        ok = d.get("ok", True)
        return _item(type_="tool", title=f"{d.get('tool', 'tool')}" +
                     (f" · {d.get('surface')}" if d.get("surface") else "") +
                     ("" if ok else " · failed"), verdict="ok" if ok else "failed",
                     detail={"tool": str(d.get("tool", "")), "surface": str(d.get("surface", "")),
                             "ok": bool(ok)}, **base)
    return _item(type_="activity", title=f"{ev.type}", detail=dict(d), **base)


def _wrote(path: str, private: bool, hook: bool) -> str:
    """"wrote X", or for a private path the flag and the note (0.0.27 CASEFOLD-FIX r2): a hook
    planted in `.git/hooks/` must read as one, never as an ordinary edit."""
    if not private:
        return f"wrote {path}"
    return (f"wrote a git hook · {path}" if hook else f"wrote {path}") + f" ({S.PRIVATE_NOTE})"


def item_from_snapshot(s: S.Snapshot) -> Dict[str, Any]:
    private = s.state == S.PRIVATE
    flags = {"private": private, "git_hook": S.git_hook(s.path)} if private else {}
    if s.kind == S.AFTER:
        return _item(f"snap:{s.id}", s.t, "edit", _wrote(s.path, private, S.git_hook(s.path)) +
                     (" (too large to keep)" if s.state == S.TOO_LARGE else ""), session=s.session,
                     run=s.run, source="snapshots",
                     detail={"path": s.path, "snap": s.id, "state": s.state, "size": s.size,
                             **flags})
    return _item(f"snap:{s.id}", s.t, "before", f"first touch · {s.path}" +
                 (f" ({S.PRIVATE_NOTE})" if private else ""), session=s.session,
                 run=s.run, source="snapshots",
                 detail={"path": s.path, "snap": s.id, "state": s.state, **flags})


def item_from_bus(evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A bus event (what the SSE stream pushes) as an item — the SAME shape the history answers,
    so the page prepends live activity to the timeline it loaded. `event` (a typed-store row) is
    mapped from its `type`/`data`; `edit` and `tool` from the hook adapter. None for the rest."""
    kind = evt.get("kind")
    t = float(evt.get("ts") or time.time())
    sid = str(evt.get("session") or "")
    if kind == "edit" and isinstance(evt.get("path"), str):
        private, hook = bool(evt.get("private")), bool(evt.get("git_hook"))
        flags = {"private": True, "git_hook": hook} if private else {}
        return _item(f"bus:{evt.get('id', '')}", t, "edit", _wrote(evt["path"], private, hook),
                     session=sid, source="bus",
                     detail={"path": evt["path"], "tool": str(evt.get("tool", "")), **flags})
    if kind == "tool":
        seq = evt.get("seq")
        return _item(f"ev:{seq}" if isinstance(seq, int) else f"bus:{evt.get('id', '')}", t,
                     "tool", str(evt.get("tool", "tool")), verdict="ok", session=sid,
                     source="events" if isinstance(seq, int) else "bus",
                     detail={"tool": str(evt.get("tool", "")), "surface": "claude-code",
                             "durable": isinstance(seq, int)})
    if kind == "event":
        from ..events.store import StoredEvent
        seq = evt.get("seq")
        ev = StoredEvent(seq=int(seq) if isinstance(seq, int) else 0, event_id="",
                         ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), schema_version=1,
                         type=str(evt.get("type", "")), session_id=sid,
                         run_id=str(evt.get("run") or ""), actor="", duration_ms=None,
                         ledger_seq=None, data=evt.get("data") if isinstance(evt.get("data"), dict)
                         else {})
        return item_from_event(ev)
    return None


# ------------------------------------------------------------------------------- the live picture

#: Bus kinds that say a session is ACTIVE (its agent did something) vs its turn/session ended.
_ACTIVE_KINDS = ("edit", "tool")
_STATE_TEXT = {"working": "the agent is working (its last tool call is on record and its turn has "
                          "not ended)", "idle": "the agent ended its turn — it is your move",
               "ended": "the session ended", "unknown": "the bus has not heard from this session "
                                                        "since the IDE opened"}


def _bus_activity(bus_events: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Per session, from the bus ring (oldest first): the last tool, the last file, when, and the
    derived state. Only what the ring SAW since this server started — said in `basis`."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in bus_events or ():
        sid, kind = e.get("session"), e.get("kind")
        if not isinstance(sid, str) or not sid:
            continue
        if kind not in ("edit", "tool", "turn_end", "session_start", "session_end", "event"):
            continue
        a = out.setdefault(sid, {"state": "unknown", "last_tool": "", "last_path": "", "at": 0.0,
                                 "tools": 0, "basis": "bus ring (since this IDE server started)"})
        ts = float(e.get("ts") or 0.0)
        if kind in _ACTIVE_KINDS:
            a["state"], a["at"], a["tools"] = "working", ts, a["tools"] + 1
            a["last_tool"] = str(e.get("tool") or ("Write" if kind == "edit" else ""))
            if kind == "edit":
                a["last_path"] = str(e.get("path") or "")
        elif kind == "turn_end":
            a["state"], a["at"] = "idle", ts
        elif kind == "session_end":
            a["state"], a["at"] = "ended", ts
        elif kind == "session_start":
            a["state"], a["at"] = "working", ts
        elif kind == "event" and e.get("type") == "gate_decision":
            # a gate verdict is the agent doing something governed; it keeps the session "working"
            if a["state"] != "ended":
                a["state"], a["at"] = "working", ts
    for a in out.values():
        a["text"] = _STATE_TEXT[a["state"]]
    return out


def _phase(root: str, run: str) -> Optional[Dict[str, Any]]:
    if not run:
        return None
    try:
        from . import plans
        lc = plans.lifecycle(root, run)
    except Exception:  # noqa: BLE001 — a run with no readable lifecycle has no phase to show
        return None
    return {"current": lc.get("current"), "finished": bool(lc.get("finished")),
            "regressed": bool(lc.get("regressed")),
            "stages": [{"stage": s["stage"], "status": s["status"], "blocks": s.get("blocks", 0)}
                       for s in lc.get("stages", [])],
            "tests": dict(lc.get("test_record") or {"failing": 0, "passing": 0}),
            "basis": "run state (progress log + ledger + tdd record)"}


BRIDGE_OFF = ("live tool calls need the IDE bridge: run `mokata setup claude --ide-bridge` "
              "(observe-only http hooks, off by default — U7/U8: the gate hooks stay command "
              "hooks). Edits, gates, tests, approvals and phases still arrive from the record.")


def bridge_state(root: str) -> Dict[str, Any]:
    """Whether this repo's Claude Code settings carry the `--ide-bridge` observe hooks — the only
    source of live tool calls. Said plainly when they are off (P12 ruling)."""
    from pathlib import Path
    from ..harness_setup import LOCAL_SETTINGS, SetupError, ide_bridge_surfaces
    try:
        wired = bool(ide_bridge_surfaces(Path(root) / ".claude" / LOCAL_SETTINGS))
    except SetupError as exc:
        # An existing settings file that cannot be read/parsed (`_load_json` refuses it): whether
        # the bridge is wired is UNKNOWN — said with the file's own reason, never "run setup",
        # which would refuse to touch that file anyway (§7g: unknown is not off).
        return {"wired": False, "note": f"live tool calls: bridge wiring unknown — {exc}",
                "command": "mokata setup claude --ide-bridge"}
    return {"wired": wired, "note": "" if wired else BRIDGE_OFF,
            "command": "mokata setup claude --ide-bridge"}


def live(root: str, *, bus_events: Optional[Iterable[Dict[str, Any]]] = None,
         focus: Optional[str] = None, jobs: Optional[Dict[str, Any]] = None,
         now: Optional[float] = None) -> Dict[str, Any]:
    """The panel's opening picture (see the module note). Every LIVE session, plus the focused one
    and every session waiting on a human; ended sessions are counted and reachable by history."""
    from . import session_views as SV
    now = time.time() if now is None else now
    ring = list(bus_events or ())
    b = SV.browser(root, now=now, bus_events=ring)
    acts = _bus_activity(ring)
    rows: List[Dict[str, Any]] = []
    for r in b["sessions"]:
        chain = r.get("chain") or [r["id"]]
        shown = (r["status"] == SV.LIVE or bool(r["needs_you"])
                 or (focus and (focus == r["id"] or focus in chain)))
        if not shown:
            continue
        act = None
        for sid in chain:
            if sid in acts and (act is None or acts[sid]["at"] > act["at"]):
                act = acts[sid]
        act = dict(act) if act else {"state": "unknown", "last_tool": "", "last_path": "",
                                     "at": 0.0, "tools": 0, "text": _STATE_TEXT["unknown"],
                                     "basis": "bus ring (since this IDE server started)"}
        if r["status"] == SV.ENDED and act["state"] != "ended":
            act["state"], act["text"] = "ended", _STATE_TEXT["ended"]
        files = r.get("files") or []
        rows.append({"id": r["id"], "short": r["short"], "prompt": r.get("prompt", ""),
                     "status": r["status"], "status_basis": r["status_basis"], "run": r["run"],
                     "branch": r.get("branch", ""), "worktree": r.get("worktree", ""),
                     "chain": chain, "resumed_from": r.get("resumed_from", ""),
                     "last": r.get("last", 0.0), "started": r.get("started", 0.0),
                     "phase": _phase(root, r["run"]),
                     "activity": act,
                     "files": {"count": len(files), "last": act.get("last_path") or
                               (files[-1] if files else ""), "writes": r.get("writes", 0)},
                     "gates": r["gates"], "secret_hits": r.get("secret_hits", 0),
                     "needs_you": r["needs_you"], "risk": r.get("risk", 0),
                     "focused": bool(focus and (focus == r["id"] or focus in chain))})
    rows.sort(key=lambda x: (-x["risk"], -(x["last"] or 0.0)))
    from ..events import store_for_root
    # the typed store's high-water mark AT THIS READ: a `event` the bus pushes later with a seq at
    # or below it is already counted in these rows (the tail lags the write by up to a tick)
    store_seq = store_for_root(root).max_seq() or 0
    return {"sample": False, "sessions": rows, "shown": len(rows), "all": b["count"],
            "store_seq": store_seq, "bridge": bridge_state(root),
            "live": sum(1 for r in b["sessions"] if r["status"] == SV.LIVE),
            "needs_you": b["needs_you"], "waiting": b["waiting"],
            "risk_formula": b["risk_formula"],
            "jobs": jobs if jobs is not None else {"active": [], "recent": []},
            "bus": bool(ring), "focus": focus or "", "types": dict(TYPES),
            "edge": "governed activity: gates, approvals and the test record beside every tool "
                    "call — not a raw log"}


# ------------------------------------------------------------------------------- history

def _cursor(before: Any) -> Tuple[Optional[int], float, bool]:
    """`before` = "<seq>:<t_hi>[:x]" from the previous page's `next`; None for the first page.
    `seq` may be empty (the event side was already exhausted); a trailing `x` makes the time bound
    EXCLUSIVE (the previous page cut its snapshot side at exactly `t_hi` and took every row there).
    A non-finite bound is refused (F4): it would come back out of a JSON answer as a 500."""
    if before in (None, ""):
        return None, math.inf, False
    bad = ActivityRefused("before must be the `next` cursor of the previous page")
    if not isinstance(before, str) or len(before) > 80:
        raise bad
    parts = before.split(":")
    if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] != "x"):
        raise bad
    seq_s, t_s = parts[0], parts[1]
    try:
        seq = int(seq_s) if seq_s else None
        t_hi = float(t_s)
    except ValueError:
        raise bad from None
    if (seq is not None and seq < 0) or not math.isfinite(t_hi):
        raise bad
    return seq, t_hi, len(parts) == 3


def _explainers(root: str, sessions: Sequence[str]) -> List[Dict[str, Any]]:
    """0.0.27 P46 — the explainer pages of these sessions (their sidecars), staleness computed now.
    Bounded by the store's per-session retention. A store that cannot be read lists nothing."""
    from ..explain import store as XS
    from ..explain.safedir import UnsafeStore
    out: List[Dict[str, Any]] = []
    for sid in sessions:
        try:
            out += XS.list_reviews(root, XS.check_sid(sid))["reviews"]
        except (UnsafeStore, OSError):
            continue
    return out


def history(root: str, session: str, *, before: Any = None, limit: int = PAGE,
            types: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """One page of a session's timeline, newest first (see the module note).

    PAGING over two sources without a cap or a gap: the typed-store rows page by `seq` (the keyset
    cursor); the snapshot writes and resumes of the same conversation are merged in by TIME, each
    page taking those in `(t_oldest_event_on_page, t_hi]` — the previous page's lower bound is this
    page's upper bound, so every write lands on exactly one page. The last page (fewer events than
    `limit`) takes everything older.

    BOTH SIDES ARE BOUNDED (F7): when the time window holds more than `limit` writes, the page
    keeps the newest `limit` (plus every write at exactly the cut time, so the cut is clean),
    drops the events older than the cut back to the next page, and the next cursor carries the cut
    time as an EXCLUSIVE bound. Totals are counted on the FIRST page only (`total` is null after):
    a count is a scan of the whole history, and a page's cost must not grow with it (F1)."""
    from . import sessions as SS
    from ..events import store_for_root
    from ..run_intent import resume_facts
    if not isinstance(session, str) or not session or len(session) > 200:
        raise ActivityRefused("a session id is required")
    limit = max(1, min(PAGE_MAX, int(limit or PAGE)))
    want = [t for t in (types or ()) if t in TYPES]
    before_seq, t_hi, excl = _cursor(before)
    first_page = before in (None, "")

    def inside(t: float, lo: float) -> bool:
        return lo < t and (t < t_hi if excl else t <= t_hi)

    joined = SS.chain(root, session)
    row = SS.get(root, session)
    run = row.run if row else ""
    for sid in (row.chain if row else []):
        if sid not in joined:
            joined.append(sid)
    store = store_for_root(root)
    ev_types: Optional[List[str]] = None
    if want:
        ev_types = sorted({et for w in want for et in EVENT_TYPE_OF.get(w, ())})
    events = (store.window(session_ids=joined, run_id=run, before_seq=before_seq, limit=limit,
                           types=ev_types)
              if (not want or ev_types) else [])
    exhausted = len(events) < limit
    t_lo = -math.inf if exhausted else _epoch(events[-1].ts)
    if not exhausted and t_lo > t_hi:
        t_lo = t_hi                          # clock skew between stores: never skip a write
    # the conversation's writes (+ first-touch captures), ONE scan of the cached timeline
    mine = [s for s in SS.timeline_cached(root) if s.session in joined or (run and s.run == run)]
    want_snaps = not want or "edit" in want or "before" in want
    snaps = [s for s in mine if inside(s.t, t_lo)] if want_snaps else []
    snaps.sort(key=lambda s: -s.t)
    next_cursor: Optional[str]
    if len(snaps) > limit:
        # F7 — cut the snapshot side: the newest `limit`, and every write at the cut time
        t_cut = snaps[limit - 1].t
        snaps = [s for s in snaps if s.t >= t_cut]
        keep = 0
        while keep < len(events) and _epoch(events[keep].ts) >= t_cut:
            keep += 1
        events = events[:keep]               # the rest goes to the next page (seq keeps them)
        t_lo, cut = t_cut, True
        seq_next = events[-1].seq if events else before_seq
        next_cursor = f"{'' if seq_next is None else seq_next}:{t_cut!r}:x"
    else:
        cut = False
        # an uncut page whose lower bound was clamped onto an EXCLUSIVE upper bound keeps the
        # exclusion, or the next page re-reads the writes at exactly that time (0.0.27 P12 verify R1)
        keep_x = ":x" if (excl and t_lo == t_hi) else ""
        next_cursor = None if exhausted else f"{events[-1].seq}:{t_lo!r}{keep_x}"

    def in_page(t: float) -> bool:
        return (t >= t_lo if cut else t > t_lo) and (t < t_hi if excl else t <= t_hi)
    # the ledger rows backing this page's events — a bisect per row, never a read of the file
    led: Dict[int, Dict[str, Any]] = {}
    seqs = [e.ledger_seq for e in events if e.ledger_seq is not None]
    if seqs:
        from .. import MOKATA_DIR
        from ..govern.ledger import AuditLedger
        try:
            led = AuditLedger.from_mokata_dir(os.path.join(root, MOKATA_DIR)).entries_by_seq(seqs)
        except (OSError, ValueError):
            led = {}
    items = [item_from_event(e, led.get(e.ledger_seq) if e.ledger_seq is not None else None)
             for e in events]
    items += [item_from_snapshot(s) for s in snaps
              if not want or (("edit" if s.kind == S.AFTER else "before") in want)]
    resumes = 0
    if not want or "resume" in want:
        for sid in joined:
            frm, times = resume_facts(root, sid)
            for k, t in enumerate(times):
                resumes += 1
                if in_page(t):
                    items.append(_item(f"resume:{sid}:{k}:{t:.3f}", t, "resume",
                                       f"resumed · {sid[:8]}" + (f" from {frm[:8]}" if frm and
                                                                 frm != sid else ""),
                                       session=sid, run=run, source="claims",
                                       detail={"from": frm or sid}))
    if not want or "explainer" in want:
        for row in _explainers(root, joined):
            t = float(row.get("generated_ts") or 0.0)
            if in_page(t):
                items.append(_item(f"expl:{row['id']}", t, "explainer",
                                   f"change explained · {row.get('kept', 0)} cited claim(s)"
                                   + (f" · {row['dropped']} dropped" if row.get("dropped") else "")
                                   + f" · {row.get('state', '')}",
                                   session=row.get("session_id") or "", run=row.get("run_id") or "",
                                   source="explainer sidecar",
                                   detail={"review": row["id"], "state": row.get("state"),
                                           "why": row.get("why"),
                                           "spec": (row.get("target") or {}).get("spec")}))
    if want:
        items = [i for i in items if i["type"] in want]
    items.sort(key=lambda i: (-i["t"], -(int(i["id"][3:]) if i["id"].startswith("ev:") else 0)))
    total = None
    if first_page:
        total = {"events": store.count_for(session_ids=joined, run_id=run),
                 "writes": len(mine), "resumes": resumes}
    return {"sample": False, "session": session, "joined": joined, "run": run,
            "items": items, "count": len(items), "next": next_cursor,
            "window": {"t_hi": None if t_hi == math.inf else t_hi,
                       "t_lo": None if t_lo == -math.inf else t_lo, "exclusive": excl},
            "total": total,
            "types": dict(TYPES), "sources": ["events", "snapshots", "claims", "ledger"],
            "reads": "reads are not recorded — mokata snapshots WRITES and the gates they pass"}


__all__ = ["TYPES", "PAGE", "PAGE_MAX", "ActivityRefused", "register_type", "item_from_event",
           "item_from_snapshot", "item_from_bus", "live", "history"]
