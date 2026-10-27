"""Every Claude Code session that ran in this repo — the ONE session source of the IDE (0.0.26
P33 SESSION-INDEX+SCREEN; first cut 0.0.22 E2).

A row is the UNION of every record mokata keeps about a session, keyed by Claude Code's session id
(parallel sessions are separate rows by construction):

    index        `session_index` — written at SessionStart / UserPromptSubmit / SessionEnd: the
                 first prompt (the row's label), segments (start, end, source, cwd, branch, the
                 Claude process), the heartbeat. Every session has one, whatever it did.
    snapshots    the files it wrote through Write/Edit (and how often)
    claims       its run (C1 / O10 — bound or open), and the session it was resumed from
    worktree     its Claude Code worktree (P31 binding): branch, path
    bash         paths its Bash calls changed outside a worktree (P31 fallback)
    transcript   Claude Code's own transcript of a session from BEFORE mokata kept an index
                 (read-only, bounded): its first prompt, start and last activity

Nothing is pruned and nothing is capped: the list is paginated (`page`). A resumed conversation
is ONE row (its chain folded in, R11); a row with no changes says so ("read-only session",
"brainstorm only") instead of being left out (R6).

Status (R12, G14): `ended` — a SessionEnd marker, or its Claude process is gone (pid + start token,
so a recycled pid is not "alive"); `live` — its process is running, or (when the process cannot be
checked) a heartbeat within `LIVE_SECS`; otherwise `inactive`. Every status carries its basis.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import snapshots as S

LIVE_SECS = 15 * 60
LIVE, ENDED, INACTIVE = "live", "ended", "inactive"
PAGE_MAX = 200
MAX_TRANSCRIPTS = 5000            # newest transcripts read (head only) per repo
TRANSCRIPT_HEAD_BYTES = 64 * 1024
FILTERS = ("all", "live", "needs", "worktree", "changed", "nochange")


@dataclass
class SessionRow:
    id: str
    run: str = ""
    files: List[str] = field(default_factory=list)
    writes: int = 0
    first: float = 0.0
    last: float = 0.0
    claim: str = ""                 # bound | open | "" (from C1)
    resumed_from: str = ""          # O10: the session this one continues (a new id on resume)
    resumes: List[float] = field(default_factory=list)   # O10: when it was resumed
    # P33 — the durable index
    prompt: str = ""
    started: float = 0.0
    ended: float = 0.0
    duration: float = 0.0
    status: str = INACTIVE
    status_basis: str = "no signal"
    branch: str = ""
    worktree: str = ""
    cwd: str = ""
    segments: int = 0
    sources: List[str] = field(default_factory=list)
    known_by: List[str] = field(default_factory=list)
    bash_files: List[str] = field(default_factory=list)
    too_large: List[str] = field(default_factory=list)
    chain: List[str] = field(default_factory=list)     # [this, the session it continues, ...]
    transcript: str = ""
    proc: Any = None
    end_reason: str = ""

    @property
    def kind(self) -> str:
        """What the session did, said for a row with no changes (never omitted, R6)."""
        if self.files or self.bash_files or self.worktree:
            return "changed"
        if self.prompt.lstrip().startswith(("/mokata:brainstorm", "/brainstorm")):
            return "brainstorm only"
        return "read-only session"

    def facts(self) -> Dict[str, Any]:
        files = list(dict.fromkeys(self.files + self.bash_files))
        return {"id": self.id, "short": self.id[:8], "run": self.run, "files": files,
                "writes": self.writes, "first": self.first, "last": self.last,
                "claim": self.claim, "resumed_from": self.resumed_from,
                "resumes": list(self.resumes),
                "last_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last))
                if self.last else "",
                "prompt": self.prompt, "started": self.started, "ended": self.ended,
                "duration": round(self.duration, 1), "status": self.status,
                "status_basis": self.status_basis, "branch": self.branch,
                "worktree": self.worktree, "segments": self.segments,
                "sources": list(self.sources), "known_by": list(self.known_by),
                "kind": self.kind, "too_large": list(self.too_large),
                "chain": list(self.chain or [self.id]), "end_reason": self.end_reason}


# ------------------------------------------------------------------------------------- sources

_TL: Dict[str, Tuple[Tuple[int, int], List[S.Snapshot]]] = {}


def timeline_cached(root: str) -> List[S.Snapshot]:
    """`snapshots.timeline(root)`, parsed once per change of the file (size, mtime)."""
    path = os.path.join(S.store_dir(root), "timeline.jsonl")
    try:
        st = os.stat(path)
        key = (st.st_size, st.st_mtime_ns)
    except OSError:
        return []
    hit = _TL.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    snaps = S.timeline(root)
    _TL[path] = (key, snaps)
    return snaps


def _claims(root: str):
    """(session, claim, run, at, resumed_from, resumes) for each C1 run-intent record."""
    from ..run_intent import INTENT_PREFIX, resume_facts, session_claim
    from ..state import StateStore
    from ..tdd_state import state_dir
    sd = state_dir(root)
    try:
        names = [n[:-5] for n in os.listdir(sd)
                 if n.startswith(INTENT_PREFIX) and n.endswith(".json")]
    except OSError:
        return
    store = StateStore(sd)
    for name in names:
        sid = name[len(INTENT_PREFIX):]
        claim, rid = session_claim(root, sid)
        data = store.read(name) or {}
        try:
            at = float(data.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        frm, resumes = resume_facts(root, sid)
        # P33 — a claim that reads "none" (expired / never bound) still PROVES the session ran.
        yield sid, ("" if claim == "none" else claim), rid or "", at, frm, resumes


def _attributions(root: str) -> Dict[str, List[str]]:
    """P31's Bash-window attribution, per session (sessions outside a worktree)."""
    from .. import session_worktrees as SW
    from ..state import StateStore
    from ..tdd_state import state_dir
    out: Dict[str, List[str]] = {}
    try:
        names = [n[:-5] for n in os.listdir(state_dir(root))
                 if n.startswith(SW.ATTRIBUTION_PREFIX) and n.endswith(".json")]
    except OSError:
        return out
    store = StateStore(state_dir(root))
    for name in names:
        data = store.read(name)
        if not isinstance(data, dict):
            continue
        sid = data.get("session") or name[len(SW.ATTRIBUTION_PREFIX):]
        files = data.get("files")
        if isinstance(files, dict) and files:
            out[str(sid)] = sorted(files)
    return out


# ---- Claude Code's own transcripts (sessions from before the index) --------------------------

def claude_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def project_slug(path: str) -> str:
    """Claude Code's transcript folder name for a working directory: every character that is not
    a letter or a digit becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))


_TX: Dict[str, Tuple[Tuple[int, float], Optional[Dict[str, Any]]]] = {}
_CMD = re.compile(r"<command-name>\s*([^<]+?)\s*</command-name>")
_ARGS = re.compile(r"<command-args>\s*([^<]*?)\s*</command-args>")


def _prompt_text(msg: Any) -> str:
    content = msg.get("content") if isinstance(msg, dict) else msg
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        content = " ".join(p for p in parts if isinstance(p, str))
    if not isinstance(content, str):
        return ""
    m = _CMD.search(content)
    if m:
        a = _ARGS.search(content)
        return (m.group(1) + (" " + a.group(1) if a and a.group(1) else "")).strip()
    if content.lstrip().startswith("<"):
        return ""                              # a harness wrapper (caveats, local output)
    return content


def _read_transcript(path: str, st: os.stat_result) -> Optional[Dict[str, Any]]:
    """The head of one transcript: first prompt, start, cwd, branch. Bounded; never raises."""
    key = (st.st_size, st.st_mtime)
    hit = _TX.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    out: Optional[Dict[str, Any]] = None
    try:
        with open(path, "rb") as fh:
            head = fh.read(TRANSCRIPT_HEAD_BYTES)
        start, cwd, branch, prompt = 0.0, "", "", ""
        for raw in head.split(b"\n"):
            try:
                d = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(d, dict):
                continue
            if not start and isinstance(d.get("timestamp"), str):
                from .session_views import _epoch
                start = _epoch(d["timestamp"])
            cwd = cwd or str(d.get("cwd") or "")
            branch = branch or str(d.get("gitBranch") or "")
            if not prompt and d.get("type") == "user" and not d.get("isMeta"):
                prompt = _prompt_text(d.get("message"))
            if start and prompt and cwd:
                break
        from ..session_index import label_of
        out = {"start": start or st.st_mtime, "last": st.st_mtime, "cwd": cwd,
               "branch": branch, "prompt": label_of(prompt), "path": path}
    except OSError:
        out = None
    _TX[path] = (key, out)
    return out


def _transcripts(root: str) -> Dict[str, Dict[str, Any]]:
    """`{session id: head facts}` for this repo's transcripts (its own folder and the folders of
    its Claude Code worktrees), newest `MAX_TRANSCRIPTS` first. Read-only."""
    base = claude_projects_dir()
    slug = project_slug(root)
    try:
        dirs = [e.path for e in os.scandir(base) if e.is_dir() and
                (e.name == slug or e.name.startswith(slug + "--claude-worktrees-"))]
    except OSError:
        return {}
    files: List[Tuple[float, str, os.stat_result]] = []
    for d in dirs:
        try:
            for e in os.scandir(d):
                if e.name.endswith(".jsonl") and e.is_file():
                    st = e.stat()
                    files.append((st.st_mtime, e.path, st))
        except OSError:
            continue
    files.sort(reverse=True)
    out: Dict[str, Dict[str, Any]] = {}
    for _mt, path, st in files[:MAX_TRANSCRIPTS]:
        facts = _read_transcript(path, st)
        if facts is not None:
            out[os.path.basename(path)[:-len(".jsonl")]] = facts
    return out


# ------------------------------------------------------------------------------------- the rows

def _status(row: SessionRow, rec: Optional[Dict[str, Any]], now: float, lookup: Any
            ) -> None:
    from .. import session_index as SI
    hb = float(rec.get("heartbeat") or 0.0) if rec else 0.0
    if rec and rec.get("segments"):
        seg = rec["segments"][-1]
        if seg.get("end"):
            row.status, row.ended = ENDED, float(seg["end"])
            row.end_reason = str(seg.get("end_reason") or "")
            row.status_basis = f"SessionEnd hook ({row.end_reason or 'no reason given'})"
            return
        alive = SI.process_alive(seg.get("proc"), lookup)
        if alive is True:
            row.status, row.status_basis = LIVE, "its Claude Code process is running"
            row.proc = seg.get("proc")
            return
        if alive is False:
            row.status, row.ended = ENDED, max(hb, row.last)
            row.end_reason = "process exited"
            row.status_basis = "its Claude Code process exited without a SessionEnd"
            return
    last = max(hb, row.last)
    if last and now - last <= LIVE_SECS:
        row.status = LIVE
        row.status_basis = (f"activity {int((now - last) // 60)} min ago (its process cannot be "
                            f"checked here)")
    elif row.transcript and not rec:
        row.status, row.status_basis = ENDED, "known from its Claude Code transcript only"
        row.ended = row.last
    else:
        row.status = INACTIVE
        row.status_basis = (f"no SessionEnd and no activity for "
                            f"{int((now - last) // 60) if last else '?'} min")


def sessions(root: str, now: Optional[float] = None, transcripts: bool = True
             ) -> List[SessionRow]:
    """EVERY session, one row per conversation chain, newest activity first (R6)."""
    from .. import session_index as SI
    from .. import session_worktrees as SW
    now = time.time() if now is None else now
    rows: Dict[str, SessionRow] = {}

    def row(sid: str, t: float = 0.0) -> SessionRow:
        r = rows.get(sid)
        if r is None:
            r = rows[sid] = SessionRow(sid, first=t, last=t)
        return r

    recs: Dict[str, Dict[str, Any]] = {}
    for rec in SI.read_all(root):
        sid = str(rec.get("id") or "")
        if not sid:
            continue
        recs[sid] = rec
        segs = rec.get("segments") or []
        start = float(segs[0].get("start") or 0.0) if segs else 0.0
        r = row(sid, start)
        r.known_by.append("index")
        r.prompt = str(rec.get("prompt") or "")
        r.started = start
        r.segments = len(segs)
        r.sources = [str(s.get("source") or "") for s in segs]
        r.resumed_from = str(rec.get("resumed_from") or "")
        r.transcript = str(rec.get("transcript") or "")
        if segs:
            r.cwd = str(segs[-1].get("cwd") or "")
            r.branch = str(segs[-1].get("branch") or "")
        r.last = max(r.last, float(rec.get("heartbeat") or 0.0))
        dur = 0.0
        for s in segs:
            st0 = float(s.get("start") or 0.0)
            end = s.get("end") or (float(rec.get("heartbeat") or st0) if s is segs[-1] else st0)
            dur += max(0.0, float(end) - st0)
        r.duration = dur
        r.resumes = [float(s.get("start") or 0) for s in segs[1:]
                     if s.get("source") in ("resume", "fork")]
    for s in timeline_cached(root):
        if not s.session:
            continue
        r = row(s.session, s.t)
        if "snapshots" not in r.known_by:
            r.known_by.append("snapshots")
        r.run = s.run or r.run
        if s.path not in r.files:
            r.files.append(s.path)
        if s.kind == S.AFTER:
            r.writes += 1
            if s.state == S.TOO_LARGE and s.path not in r.too_large:
                r.too_large.append(s.path)
        r.first = min(r.first, s.t) if r.first else s.t
        r.last = max(r.last, s.t)
    for sid, claim, rid, at, frm, resumes in _claims(root):
        r = row(sid, at)
        r.known_by.append("claim")
        r.claim = claim
        r.run = rid or r.run
        r.resumed_from = frm or r.resumed_from
        r.resumes = sorted(set(r.resumes) | set(resumes))
        r.last = max(r.last, at)
    for sid, b in SW.bindings(root).items():
        r = row(sid, float(b.get("last_seen") or 0.0))
        r.known_by.append("worktree")
        r.worktree = str(b.get("path") or "")
        r.branch = str(b.get("branch") or r.branch)
    for sid, files in _attributions(root).items():
        r = row(sid)
        r.known_by.append("bash")
        r.bash_files = files
    if transcripts:
        for sid, tx in _transcripts(root).items():
            r = rows.get(sid)
            if r is None:
                r = row(sid, tx["start"])
                r.started = tx["start"]
                r.duration = max(0.0, tx["last"] - tx["start"])
            r.known_by.append("transcript")
            r.transcript = r.transcript or tx["path"]
            r.prompt = r.prompt or tx["prompt"]
            r.branch = r.branch or tx["branch"]
            r.cwd = r.cwd or tx["cwd"]
            r.last = max(r.last, tx["last"]) if "index" not in r.known_by else r.last
    lookup = _lookup()
    for sid, r in rows.items():
        if not r.started:
            r.started = r.first
        if not r.first:
            r.first = r.started
        _status(r, recs.get(sid), now, lookup)
        if not r.duration and r.last and r.started:
            r.duration = max(0.0, (r.ended or r.last) - r.started)
    _one_live_per_process(rows)
    _fold_chains(rows)
    return sorted((r for r in rows.values() if r.chain), key=lambda r: r.last, reverse=True)


def _lookup():
    try:
        from ..session_ancestry import system_lookup
        return system_lookup()
    except Exception:  # noqa: BLE001
        return None


def _one_live_per_process(rows: Dict[str, SessionRow]) -> None:
    """`/resume` inside a running Claude switches its session: only the newest session a live
    process opened is live — the one it switched away from has ended."""
    by_proc: Dict[str, List[SessionRow]] = {}
    for r in rows.values():
        if r.status == LIVE and r.proc:
            by_proc.setdefault(json.dumps(r.proc), []).append(r)
    for group in by_proc.values():
        group.sort(key=lambda r: r.started)
        for old in group[:-1]:
            old.status, old.ended = ENDED, old.last
            old.end_reason = "switched"
            old.status_basis = (f"its Claude Code process switched to session "
                                f"{group[-1].id[:8]} (/resume)")


def _fold_chains(rows: Dict[str, SessionRow]) -> None:
    """A resumed conversation is ONE row (R11): each predecessor folds into the newest session
    that continues it — its files, writes, time span — and is not listed on its own."""
    succ: Dict[str, str] = {}
    for r in sorted(rows.values(), key=lambda r: r.started):
        if r.resumed_from and r.resumed_from in rows and r.resumed_from != r.id:
            succ[r.resumed_from] = r.id                  # the newest successor wins
    for r in rows.values():
        r.chain = [] if r.id in succ else [r.id]
    for head in [r for r in rows.values() if r.chain]:
        cur, seen = head.id, {head.id}
        preds = [k for k, v in succ.items() if v == cur]
        while preds and len(seen) < 64:
            p = preds[0]
            if p in seen:
                break
            seen.add(p)
            prev = rows[p]
            head.chain.append(p)
            head.files += [f for f in prev.files if f not in head.files]
            head.bash_files += [f for f in prev.bash_files if f not in head.bash_files]
            head.too_large += [f for f in prev.too_large if f not in head.too_large]
            head.writes += prev.writes
            head.started = min(x for x in (head.started, prev.started) if x) if prev.started \
                else head.started
            head.duration += prev.duration
            head.prompt = head.prompt or prev.prompt
            head.run = head.run or prev.run
            head.resumes = sorted(set(head.resumes) | {prev.last})
            preds = [k for k, v in succ.items() if v == p]


def chain(root: str, session_id: str, depth: int = 8) -> List[str]:
    """`[session_id, its predecessor, ...]` — a resumed session joined to the sessions it continues
    (O10 claims, and the index's transcript-named predecessor), newest first, cycle-safe."""
    from ..run_intent import resume_facts
    from .. import session_index as SI
    out = [session_id]
    cur = session_id
    while len(out) < depth:
        frm, _ = resume_facts(root, cur)
        if not frm:
            rec = SI.read(root, cur)
            frm = str((rec or {}).get("resumed_from") or "")
        if not frm or frm in out:
            break
        out.append(frm)
        cur = frm
    return out


def get(root: str, session_id: Optional[str]) -> Optional[SessionRow]:
    if not session_id:
        return None
    for r in sessions(root):
        if r.id == session_id or session_id in r.chain:
            return r
    return None


# ------------------------------------------------------------------------------------- the page

def _matches(r: SessionRow, words: List[str]) -> bool:
    hay = " ".join([r.id, r.prompt, r.branch, r.run, r.status, r.kind, r.worktree,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(r.started or r.last or 0)),
                    " ".join(r.files), " ".join(r.bash_files)]).lower()
    return all(w in hay for w in words)


def page(root: str, offset: int = 0, limit: int = 50, q: str = "", filt: str = "all",
         now: Optional[float] = None, needs: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """One page of the list: newest first, filtered by text (prompt, file, branch, run, date) and
    by `filt` (all | live | needs | worktree | changed | nochange). Never capped — paginated."""
    rows = sessions(root, now)
    words = [w for w in (q or "").lower().split() if w]
    need_set = set(needs or ())
    out = []
    for r in rows:
        if filt == "live" and r.status != LIVE:
            continue
        if filt == "needs" and not (need_set & set(r.chain)):
            continue
        if filt == "worktree" and not (r.worktree and os.path.isdir(r.worktree)):
            continue
        if filt == "changed" and r.kind != "changed":
            continue
        if filt == "nochange" and r.kind == "changed":
            continue
        if words and not _matches(r, words):
            continue
        out.append(r)
    limit = max(1, min(PAGE_MAX, int(limit)))
    offset = max(0, int(offset))
    shown = out[offset:offset + limit]
    stages = _stages(root, {r.run for r in shown if r.run})
    facts = []
    for r in shown:
        f = r.facts()
        f["stage"] = stages.get(r.run, "")
        f["waiting"] = bool(need_set & set(r.chain))
        facts.append(f)
    return {"sample": False, "rows": facts, "total": len(out), "all": len(rows),
            "offset": offset, "limit": limit, "q": q, "filter": filt,
            "live": sum(1 for r in rows if r.status == LIVE),
            "live_window_secs": LIVE_SECS}


def _stages(root: str, runs: Iterable[str]) -> Dict[str, str]:
    """The run's current lifecycle stage, for the rows on the page only."""
    from . import plans
    out: Dict[str, str] = {}
    for rid in runs:
        try:
            lc = plans.lifecycle(root, rid)
            out[rid] = "finished" if lc.get("finished") else str(lc.get("current") or "")
        except Exception:  # noqa: BLE001 — a run with no readable lifecycle shows no stage
            out[rid] = ""
    return out


__all__ = ["SessionRow", "sessions", "get", "chain", "page", "timeline_cached",
           "claude_projects_dir", "project_slug", "LIVE", "ENDED", "INACTIVE", "LIVE_SECS",
           "FILTERS"]


# ------------------------------------------------------------------------------------- +/− stats

_STATS: Dict[Tuple[str, float, str], Dict[str, Any]] = {}


def stats(root: str, ids: List[str]) -> Dict[str, Any]:
    """Files and +/− of each session on a page, from the ONE diff source (`diffs.session_diff`).
    Asked for the visible rows only, after the list has drawn (the list never waits on diffs);
    an ended session's answer is kept until it changes, a live one is always recomputed."""
    from . import diffs as D
    from ..knowledge.cancel import checkpoint, report
    known = {r.id: r for r in sessions(root, transcripts=False)}
    out: Dict[str, Any] = {}
    for n, sid in enumerate(ids):
        checkpoint()
        r = known.get(sid)
        key = (sid, r.last if r else 0.0, r.status if r else "")
        hit = _STATS.get(key) if r is not None and r.status != LIVE else None
        if hit is None:
            ds = D.session_diff(root, sid)
            hit = {"files": len(ds.files), "added": sum(f.added for f in ds.files),
                   "removed": sum(f.removed for f in ds.files),
                   "too_large": sum(1 for f in ds.files if f.too_large),
                   "basis": ds.meta.get("basis", ""), "note": ds.note}
            _STATS[key] = hit
            if len(_STATS) > 4096:
                _STATS.clear()
        out[sid] = hit
        report(done=n + 1, total=len(ids), unit="sessions")
    return {"sample": False, "stats": out}
