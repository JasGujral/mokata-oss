"""ID-VIS-SESSION · ID-SESSION-BROWSER · ID-SESSION-SWITCHER · ID-NEEDS-YOU (0.0.25 Lane ID3).

Doc 109 §18.3 / §18.4 / §16 #11, as DATA the page draws and the `ide_sessions` MCP tool returns:

  * `browser`   — every session (live + past): what it changed, the gates that fired for its run,
                  its token spend (estimated or measured, never blended), its ledger links, its
                  status, and what (if anything) it is waiting on a human for.
  * `needs_you` — the attention queue, sorted by RISK, not recency (doc 109 §16 #11).
  * `timelapse` — one session replayed from the snapshot timeline + the typed event store: every
                  capture and every gate decision, in order, with the lines each write changed.
  * `treemap`   — the repo sized by LOC, coloured by how much the session changed each file
                  (doc 109 §18.3), laid out HERE (squarified) so the page only draws rectangles.

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

import math
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import snapshots as S

# ------------------------------------------------------------------------------------- constants

LIVE_SECS = 15 * 60          # a session that wrote within this window and has no end signal is live
MAX_EVENTS = 5000            # typed-store rows read per view (newest)
MAX_FRAMES = 2000            # time-lapse steps returned (newest kept; `truncated` says so)
MAX_DELTAS = 2000            # writes whose line delta is computed (newest); older: writes only
MAX_TREEMAP_FILES = 3000     # leaves laid out; beyond it the largest + every changed file are kept
MAX_LOC_BYTES = 2 * 1024 * 1024
TREEMAP_W, TREEMAP_H = 1000.0, 600.0
SECRET_PAIR_SECS = 5.0       # a secret-scan hit this close to a block names the block a secret one

# Gate decisions that leave a session blocked until something changes. "declined" is a human
# saying NO — an answer, not a wait — so it is not here.
BLOCKING = frozenset({"block", "blocked", "deny", "denied", "refused", "fail", "failed",
                      "would-block"})

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

LIVE, ENDED, PAST = "live", "ended", "past"
READS_NOTE = ("reads are not recorded — mokata snapshots WRITES; a `before` is the file as the "
              "session found it, captured at its first write")


# ------------------------------------------------------------------------------------- helpers

def _epoch(ts: str) -> float:
    """An event store `ts` (ISO-8601, UTC, second resolution, `Z`) as epoch seconds; 0 when not."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


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
        if not isinstance(sid, str) or not sid or kind not in ("edit", "turn_end", "session_start",
                                                              "session_end"):
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
    rows = {r.id: r.facts() for r in SS.sessions(root)}
    signals = _bus_signals(bus_events)
    for sid, sig in signals.items():                 # a session the bus saw before its first write
        rows.setdefault(sid, {"id": sid, "short": sid[:8], "run": "", "files": [], "writes": 0,
                              "first": sig["at"], "last": sig["at"], "claim": "", "last_iso": ""})
    events = _events(root, (GATE_DECISION, SECRET_SCAN_HIT, TOKEN_SPEND))
    pending = _pending(root, now)
    live = _live_runs(root, (r["run"] for r in rows.values()))

    def mine(ev: Any, row: Dict[str, Any]) -> bool:
        return (bool(row["run"]) and ev.run_id == row["run"]) or ev.session_id == row["id"]

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
        if sig and sig["ended"]:
            status, basis = ENDED, "session_end hook"
        elif row["run"] and row["run"] in live:
            status, basis = LIVE, "live MCP process registered for its run"
        elif sig and sig["at"] and now - sig["at"] <= LIVE_SECS:
            status, basis = LIVE, f"bus event ({sig['last']}) within {LIVE_SECS // 60} min"
        elif last and now - last <= LIVE_SECS:
            status, basis = LIVE, f"a write within {LIVE_SECS // 60} min"
        else:
            status, basis = PAST, "no-signal"
        reasons: List[Dict[str, Any]] = []
        for p in pending:
            if row["run"] and p.run_id == row["run"]:
                claimed_proposals.add(p.proposal_id)
                reasons.append(_awaiting_item(p, now))
        if blocked and status != ENDED:
            b = blocked[-1]
            at = _epoch(b.ts)
            later_write = float(row.get("last") or 0.0) > at + 1.0
            later_pass = any(g.seq > b.seq for g in gates
                             if str(g.data.get("decision", "")).lower() not in BLOCKING)
            if not later_write and not later_pass:
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
        if sig and sig["last"] == "turn_end" and status != ENDED:
            reasons.append(_item(TURN_ENDED, sig["at"], now, {"basis": "Stop hook (bus)"}))
        reasons.sort(key=lambda r: (-r["risk"], r["since"]))
        out.append({**row, "status": status, "status_basis": basis, "last": last,
                    "gates": {"fired": len(gates), "blocked": len(blocked),
                              "by_gate": _count(str(g.data.get("gate", "")) for g in gates)},
                    "secret_hits": len(secrets_hit),
                    "tokens": _spend(spend),
                    "ledger": sorted({e.ledger_seq for e in evs if e.ledger_seq is not None})[-20:],
                    "needs_you": reasons, "risk": reasons[0]["risk"] if reasons else 0})
    out.sort(key=lambda r: (-r["risk"], -(r["last"] or 0.0)))
    unattributed = [_awaiting_item(p, now) for p in pending
                    if p.proposal_id not in claimed_proposals]
    queue = [dict(reason, session=r["id"], short=r["short"], run=r["run"])
             for r in out for reason in r["needs_you"]]
    queue += [dict(item, session="", short="", run=item["detail"].get("run", ""))
              for item in unattributed]
    queue.sort(key=lambda q: (-q["risk"], q["since"]))
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


# ------------------------------------------------------------------------------------- deltas

class _Deltas:
    """Lines added/removed by each AFTER against the previous capture of the same file. Bounded:
    only the newest `MAX_DELTAS` writes are measured; a capture whose content was not kept (too
    large / binary) has an UNMEASURED delta (`None`), never a zero."""

    def __init__(self, root: str, snaps: Sequence[S.Snapshot]):
        self.root = root
        self.by_id: Dict[str, Optional[Tuple[int, int]]] = {}
        afters = [s for s in snaps if s.kind == S.AFTER]
        measured = {s.id for s in afters[-MAX_DELTAS:]}
        prev: Dict[str, S.Snapshot] = {}
        for s in snaps:
            p = prev.get(s.path)
            if s.kind == S.AFTER and s.id in measured:
                self.by_id[s.id] = self._delta(p, s)
            prev[s.path] = s

    def _delta(self, a: Optional[S.Snapshot], b: S.Snapshot) -> Optional[Tuple[int, int]]:
        from .diffs import text_diff
        old = "" if a is None else S.content_of(self.root, a)
        new = S.content_of(self.root, b)
        if old is None or new is None:
            return None
        if a is not None and a.sha and a.sha == b.sha:
            return (0, 0)
        fd = text_diff(b.path, old, new)
        return (fd.added, fd.removed)


# ------------------------------------------------------------------------------------- time-lapse

def timelapse(root: str, session: str) -> Dict[str, Any]:
    """ID-VIS-SESSION time-lapse: one session replayed — each capture and each gate decision for
    its run, in time order, with the lines each write changed. The page's scrubber steps it."""
    from . import sessions as SS
    from ..events.schema import GATE_DECISION
    from .nav import PathRefused
    if not isinstance(session, str) or not session or len(session) > 200:
        raise PathRefused("a session id is required")
    snaps = S.timeline(root, session=session)
    row = SS.get(root, session)
    run = row.run if row else ""
    deltas = _Deltas(root, snaps)
    steps: List[Dict[str, Any]] = []
    for s in snaps:
        d = deltas.by_id.get(s.id) if s.kind == S.AFTER else None
        steps.append({"t": s.t, "kind": "write" if s.kind == S.AFTER else "before", "path": s.path,
                      "snap": s.id, "state": s.state,
                      "added": d[0] if d else None, "removed": d[1] if d else None,
                      "measured": d is not None or s.kind != S.AFTER})
    gates = [e for e in _events(root, (GATE_DECISION,))
             if (run and e.run_id == run) or e.session_id == session]
    for e in gates:
        decision = str(e.data.get("decision", ""))
        steps.append({"t": _epoch(e.ts), "kind": "gate", "gate": str(e.data.get("gate", "")),
                      "decision": decision, "blocked": decision.lower() in BLOCKING,
                      "subject": str(e.data.get("subject", ""))[:300], "ledger_seq": e.ledger_seq})
    steps.sort(key=lambda x: (x["t"], 0 if x["kind"] == "before" else 1))
    truncated = len(steps) > MAX_FRAMES
    steps = steps[-MAX_FRAMES:]
    files: List[str] = []
    for i, st in enumerate(steps):
        st["i"] = i
        if st.get("path") and st["path"] not in files:
            files.append(st["path"])
    return {"sample": False, "session": session, "run": run, "steps": steps, "files": files,
            "count": len(steps), "truncated": truncated, "reads": READS_NOTE,
            "unmeasured": sum(1 for s in steps if s["kind"] == "write" and not s["measured"])}


# ------------------------------------------------------------------------------------- treemap

_LOC_CACHE: Dict[Tuple[str, int, int], Optional[int]] = {}


def _loc(root: str, rel: str) -> Tuple[Optional[int], int]:
    """(lines, bytes) of a file; lines is None for a binary / unreadable file. Cached by mtime."""
    ab = os.path.join(root, *rel.split("/"))
    try:
        st = os.stat(ab)
    except OSError:
        return None, 0
    key = (ab, st.st_size, st.st_mtime_ns)
    if key not in _LOC_CACHE:
        try:
            with open(ab, "rb") as fh:
                data = fh.read(MAX_LOC_BYTES)
            if b"\0" in data[:8192]:
                _LOC_CACHE[key] = None
            else:
                n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
                if st.st_size > MAX_LOC_BYTES:            # extrapolate past the read cap
                    n = int(n * st.st_size / max(1, len(data)))
                _LOC_CACHE[key] = n
        except OSError:
            _LOC_CACHE[key] = None
        if len(_LOC_CACHE) > 4 * MAX_TREEMAP_FILES:
            _LOC_CACHE.clear()
    return _LOC_CACHE.get(key), st.st_size


def squarify(weights: Sequence[float], x: float, y: float, w: float,
             h: float) -> List[Tuple[float, float, float, float]]:
    """The squarified layout (Bruls, Huizing, van Wijk 2000): rectangles for `weights` (sorted
    DESCENDING by the caller), tiling (x, y, w, h) exactly, each as close to square as the greedy
    row rule allows. Pure; areas are proportional to the weights."""
    total = float(sum(weights))
    if total <= 0 or w <= 0 or h <= 0 or not weights:
        return [(x, y, 0.0, 0.0) for _ in weights]
    areas = [wt * w * h / total for wt in weights]
    out: List[Tuple[float, float, float, float]] = []

    def worst(row: List[float], side: float) -> float:
        s = sum(row)
        if s <= 0 or side <= 0:
            return math.inf
        return max(side * side * max(row) / (s * s), (s * s) / (side * side * max(min(row), 1e-12)))

    i, n = 0, len(areas)
    while i < n:
        side = min(w, h)
        row = [areas[i]]
        i += 1
        while i < n and worst(row + [areas[i]], side) <= worst(row, side):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if i >= n:                                 # the last row fills what is left exactly
            if w >= h:
                cw = w
                yy = y
                for a in row:
                    rh = a / cw if cw else 0.0
                    out.append((x, yy, cw, rh))
                    yy += rh
            else:
                rh = h
                xx = x
                for a in row:
                    cw = a / rh if rh else 0.0
                    out.append((xx, y, cw, rh))
                    xx += cw
            break
        if w >= h:                                  # a column along the left edge
            cw = s / h
            yy = y
            for a in row:
                rh = a / cw
                out.append((x, yy, cw, rh))
                yy += rh
            x, w = x + cw, w - cw
        else:                                       # a row along the top edge
            rh = s / w
            xx = x
            for a in row:
                cw = a / rh
                out.append((xx, y, cw, rh))
                xx += cw
            y, h = y + rh, h - rh
    return out


def change_volume(root: str, session: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Per file: writes, lines added/removed summed over every write (churn), and whether any
    write's delta could not be measured. One session, or every session when `session` is None."""
    snaps = S.timeline(root, session=session) if session else S.timeline(root)
    out: Dict[str, Dict[str, Any]] = {}
    # deltas are per session: a file's previous capture is the previous capture BY THAT SESSION
    by_session: Dict[str, List[S.Snapshot]] = {}
    for s in snaps:
        by_session.setdefault(s.session, []).append(s)
    for group in by_session.values():
        deltas = _Deltas(root, group)
        for s in group:
            if s.kind != S.AFTER:
                continue
            f = out.setdefault(s.path, {"writes": 0, "added": 0, "removed": 0, "unmeasured": 0})
            f["writes"] += 1
            d = deltas.by_id.get(s.id)
            if d is None:
                f["unmeasured"] += 1
            else:
                f["added"] += d[0]
                f["removed"] += d[1]
    return out


def treemap(root: str, session: Optional[str] = None, size: str = "loc",
            width: float = TREEMAP_W, height: float = TREEMAP_H) -> Dict[str, Any]:
    """ID-VIS-SESSION change treemap: the repo sized by LOC (doc 109 §18.3) — or, with
    `size="change"`, only the changed files sized by lines changed — coloured by what the session
    (or every session) changed. Laid out here; the page draws the rectangles as SVG."""
    from .nav import walk_files
    if size not in ("loc", "change"):
        from .server import BadRequest
        raise BadRequest("size must be loc or change")
    changes = change_volume(root, session)
    files = walk_files(root) if size == "loc" else sorted(changes)
    on_disk = set(files) if size == "loc" else None
    leaves: List[Dict[str, Any]] = []
    for rel in files:
        c = changes.get(rel)
        if size == "loc":
            loc, nbytes = _loc(root, rel)
            if loc is None:
                continue
            weight = max(1, loc)
        else:
            loc, nbytes = _loc(root, rel)
            weight = max(1, c["added"] + c["removed"]) if c else 1
        leaves.append({"path": rel, "loc": loc, "bytes": nbytes, "weight": weight,
                       "writes": c["writes"] if c else 0, "added": c["added"] if c else 0,
                       "removed": c["removed"] if c else 0,
                       "unmeasured": c["unmeasured"] if c else 0})
    total_files = len(leaves)
    omitted = 0
    if len(leaves) > MAX_TREEMAP_FILES:
        changed = [lf for lf in leaves if lf["writes"]]
        rest = sorted((lf for lf in leaves if not lf["writes"]), key=lambda lf: -lf["weight"])
        keep = changed + rest[:max(0, MAX_TREEMAP_FILES - len(changed))]
        omitted = len(leaves) - len(keep)
        leaves = keep
    dirs = layout(leaves, width, height)
    gone = sorted(p for p in changes if on_disk is not None and p not in on_disk)
    return {"sample": False, "session": session or "", "size": size, "width": width,
            "height": height, "files": leaves, "dirs": dirs, "count": len(leaves),
            "total_files": total_files, "omitted": omitted,
            "changed": sum(1 for lf in leaves if lf["writes"]),
            "not_on_disk": gone[:200],
            "heat_scale": "0 unchanged · 1–4 lines changed, log-scaled to the busiest file",
            "sized_by": "lines of code" if size == "loc" else "lines changed (added + removed)"}


def layout(leaves: List[Dict[str, Any]], width: float = TREEMAP_W,
           height: float = TREEMAP_H) -> List[Dict[str, Any]]:
    """Lay `leaves` (each: path, weight, writes, added, removed) out as a nested squarified
    treemap: sets each leaf's x/y/w/h and `heat` (0 unchanged, 1-4 log-scaled lines changed) and
    returns the directory frames."""
    vols = [lf["added"] + lf["removed"] for lf in leaves if lf["writes"]]
    top = max(vols) if vols else 0
    for lf in leaves:
        v = lf["added"] + lf["removed"]
        lf["heat"] = 0 if not lf["writes"] else (
            1 + min(3, int(3 * math.log1p(v) / math.log1p(top))) if top else 1)
    dirs: List[Dict[str, Any]] = []
    _layout(_tree(leaves), "", 0.0, 0.0, float(width), float(height), dirs, 0)
    return dirs


def _tree(leaves: List[Dict[str, Any]]) -> Dict[str, Any]:
    root: Dict[str, Any] = {"dirs": {}, "files": [], "weight": 0}
    for lf in leaves:
        node = root
        node["weight"] += lf["weight"]
        parts = lf["path"].split("/")
        for d in parts[:-1]:
            node = node["dirs"].setdefault(d, {"dirs": {}, "files": [], "weight": 0})
            node["weight"] += lf["weight"]
        node["files"].append(lf)
    return root


def _layout(node: Dict[str, Any], prefix: str, x: float, y: float, w: float, h: float,
            dirs: List[Dict[str, Any]], depth: int) -> None:
    kids: List[Tuple[float, str, Any]] = [(d["weight"], name, d) for name, d in node["dirs"].items()]
    kids += [(lf["weight"], lf["path"], lf) for lf in node["files"]]
    kids.sort(key=lambda k: (-k[0], k[1]))
    rects = squarify([k[0] for k in kids], x, y, w, h)
    for (weight, name, item), (rx, ry, rw, rh) in zip(kids, rects):
        if "dirs" in item:
            path = prefix + name
            dirs.append({"path": path, "x": round(rx, 2), "y": round(ry, 2), "w": round(rw, 2),
                         "h": round(rh, 2), "depth": depth, "weight": weight})
            _layout(item, path + "/", rx, ry, rw, rh, dirs, depth + 1)
        else:
            item.update(x=round(rx, 2), y=round(ry, 2), w=round(rw, 2), h=round(rh, 2))


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


__all__ = ["browser", "needs_you", "timelapse", "treemap", "squarify", "layout", "change_volume",
           "sample_browser", "RISK", "RISK_FORMULA", "BLOCKING", "LIVE", "ENDED", "PAST",
           "AWAITING", "GATE_BLOCK", "SECRET_BLOCK", "TURN_ENDED", "MAX_TREEMAP_FILES",
           "MAX_FRAMES", "LIVE_SECS"]
