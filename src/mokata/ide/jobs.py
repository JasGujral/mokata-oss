"""0.0.26 P32 `IDE-JOBS` — long work in the IDE is a visible JOB: progress, Cancel, partial results.

Jas, 2026-10-21/22 (overrules U10's 20 s cap): no arbitrary timeouts on graph, search or index work
on multi-million-line repos; when something is running the IDE shows it clearly; long work is a
job with progress, cancel and partial results. Audit A measured why the deadline had to go: every
graph view on kubernetes / typescript-go answered 503 at exactly 20.0 s, kept burning CPU for nobody
after it, and starved the lane for the next question.

THE MODEL (PRD §5.3):

  * A job is keyed by its QUESTION — `(kind, args)`. The same question joins the running job
    (dedupe, as the old heavy lane coalesced). A finished answer goes to the pages that waited for
    it (they re-ask naming the job: `answer`); a NEW ask computes afresh, so an edit since is seen.
  * Jobs run on small bounded LANES of daemon threads (started on first use — nothing at idle):
    graph questions never queue behind a word search, and a 9-minute index build never blocks
    either. NO wall-clock deadline anywhere; the bounds are result sizes, and they are disclosed.
  * Progress and partial answers come from INSIDE the work through `knowledge.cancel.report(...)`
    and are published on the server's event bus as `job` events at <= 1 s cadence (a ticker runs
    only while a job is active). Cancel sets the job's flag; the walks' checkpoints raise
    `Cancelled` and subprocesses are killed, so the CPU really stops.
  * Who still wants it: each REQUEST that asked is a want — `(client, token)`: the page's id (its
    SSE connection) and the request's own token (`X-Mokata-Request`, one per `MK.api` call). A
    superseded request leaves only its own token; a query job is cancelled only when no request of
    any page still wants it (0.0.28 D). A page's stream that drops keeps its wants for
    `RECONNECT_S`; a stream that ends after its replacement opened is not a drop (`stream_closed`).
    A caller with no page (a script, a test) holds a lease of `GRACE_S` from its last ask. INDEX
    builds (`index.*`, `mokata.update`) keep running with nobody watching (PRD §5.3 rule 3).
  * "Failing at use" (R35): a graph/impact job that FAILS is remembered per kind until one
    succeeds; Health reads it and marks the code-graph row degraded with the reason.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import queue
import sys
import threading
import time
import traceback
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..errors import MokataError
from ..knowledge import cancel as C

# --------------------------------------------------------------------------- the vocabulary
QUEUED, RUNNING, PARTIAL, DONE, FAILED, CANCELLED = (
    "queued", "running", "partial", "done", "failed", "cancelled")
#: 0.0.27 P21 — a Cancel was ASKED of a job whose work decides for itself whether it is too late
#: (`SEALING_KINDS`): not finished until the work says so — it either stops (cancelled) or had
#: already passed its commit point (`seal_current`) and finishes as it would have.
CANCELLING = "cancelling"
STATES = (QUEUED, RUNNING, PARTIAL, CANCELLING, DONE, FAILED, CANCELLED)
FINISHED = (DONE, FAILED, CANCELLED)

#: kind -> lane. A lane is a few daemon threads of its own, so one kind of slow work never queues
#: another kind behind it.
KINDS: Dict[str, str] = {
    "graph.view": "query", "graph.query": "query", "impact": "query", "health.check": "query",
    "sessions.stats": "query", "welcome.scan": "query",
    # 0.0.27 P13 — the AC → test → code trace: a graph question per changed definition.
    "proof.trace": "query",
    "search.text": "search", "search.meaning": "search", "tree.walk": "search",
    "index.crg": "maintenance", "index.ast": "maintenance", "mokata.update": "maintenance",
    "model.fetch": "maintenance", "mokata.init": "maintenance",
    "index.meaning": "meaning", "index.symbols": "symbols",
    # P37 (R39): the read-path freshness reconcile — its own lane, so an index update never queues
    # behind a rebuild on `maintenance`, nor holds a query worker.
    "index.fresh": "fresh",
    # P7 (ID-REVIEW-CONTEXT): a test re-run is the project's own command — its own lane, one at a
    # time, so it never holds a query worker nor races another run over the same files; the
    # blame/line history of one hunk is a query.
    "tests.run": "tests", "review.blame": "query",
    # 0.0.27 P24 (read-only LSP): a question to a language server (no deadline — pending with
    # Cancel) and a server's first indexing pass (progress from `$/progress`); their own lane, so
    # a slow server never holds a graph worker.
    "lsp.query": "lsp", "lsp.index": "lsp",
    # P8 (ID-CSV-VIEW): counting the rows of a huge delimited file, and diffing two versions of
    # one, are walks of the whole input — their own lane, so neither queues a graph question.
    "table.count": "table", "table.diff": "table", "table.view": "table",
    # 0.0.27 P46 (CHANGE-EXPLAINER): the fact bundle of one change set (both trees read, every
    # reference grepped) — its own lane, so it never holds a graph worker. NOT a keep kind: a
    # bundle nobody is watching is cancelled like any other read.
    "explain.bundle": "explain",
    # 0.0.27 P5 (ID-DIFF-INTRALINE): the token diff of the line pairs in view when their cost is
    # past the sync bound (a minified line), and a context page deep in a big file — their own
    # lane, so a 5 MB line never queues a graph question.
    "diff.intraline": "diff", "diff.context": "diff",
    # 0.0.27 P4 (ID-COPY-CONTEXT): Find Usages on a hub and Copy with Context's full blast radius
    # walk long (no clock, Cancel instead) — their own lane, so neither holds a graph worker.
    "usages.find": "usages", "copy.context": "usages",
    # 0.0.27 P21 (ID-MERGE-GATED): the gated merge — one at a time per server (its own lane), and
    # the full-suite run that produces its evidence is a `tests.run`.
    "git.merge": "git",
}
LANE_WORKERS = {"query": 2, "search": 2, "maintenance": 1, "meaning": 1, "symbols": 1, "fresh": 1,
                "tests": 1, "lsp": 2, "table": 2, "explain": 1, "diff": 2, "usages": 2, "git": 1}
#: Kinds that keep running with nobody watching (PRD §5.3: "closing every view of a job's result
#: keeps it running only for index builds").
KEEP_KINDS = frozenset({"index.crg", "index.ast", "index.meaning", "index.symbols", "index.fresh",
                        "mokata.update", "model.fetch", "mokata.init", "tests.run", "lsp.index",
                        "git.merge"})
#: 0.0.27 P21 — kinds whose Cancel is a REQUEST the work answers (`CANCELLING`), because past a
#: commit point the honest answer is "too late", not "cancelled".
SEALING_KINDS = frozenset({"git.merge"})
#: Kinds whose failure means the code graph is failing AT USE (R35).
GRAPH_KINDS = frozenset({"graph.view", "graph.query", "impact"})
#: 0.0.26 P39 (R35, G18) — every job whose outcome says whether a CAPABILITY works, and which one:
#: Health is fed by what the work did, not by a presence probe. A failure stands until a later job
#: of the same kind succeeds; it carries the job's kind + args so Health can offer Retry.
CAPABILITY_OF: Dict[str, str] = {
    "graph.view": "graph", "graph.query": "graph", "impact": "graph", "index.crg": "graph",
    "index.ast": "ast", "index.meaning": "search", "search.meaning": "search",
    "search.text": "lexical", "mokata.init": "setup",
}

#: How long a route waits for its job before answering `{job, state, partial}` (202) instead of
#: the result. Measured, not a deadline: on the 2-CPU bed a warm callers/focus on django answers in
#: 0.14-0.47 s and a cold ⌘P outline in < 0.1 s; 300 ms keeps every small answer a plain 200 while
#: anything longer shows its progress after one frame of nothing. The page may ask for less.
SYNC_WINDOW_S = 0.3
TICK_S = 0.5            # job events while a job runs (>= 1 per second per running job)
HISTORY = 20            # finished jobs listed after the active ones
GRACE_S = 10.0          # an anonymous caller's job lives this long after it last asked
RECONNECT_S = 5.0       # a page whose event stream dropped has this long to reconnect
LEFT_S = 30.0           # a request superseded before its handle stays "gone" this long (`leave_request`)
LEFT_MAX = 1024         # ...and at most this many are remembered (the oldest is forgotten first)


#: 0.0.28 D — the token of the request being served (`X-Mokata-Request`), set by the server's one
#: route door for the request's own task; a job started while serving it is wanted by THAT request.
REQUEST: "contextvars.ContextVar[str]" = contextvars.ContextVar("mokata_request_token", default="")


def _canon(args: Dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


class Job:
    """One question being answered. Mutated only under the registry's lock."""

    def __init__(self, reg: "JobRegistry", kind: str, args: Dict[str, Any],
                 fn: Callable[[], Any], label: str, scope: str, keep: bool, n: int) -> None:
        self.reg = reg
        self.kind, self.args, self.fn = kind, dict(args), fn
        self.key = (kind, _canon(args))
        # The id names the QUESTION (kind + a digest of its args) and which run of it this is: a
        # re-run after a cancel is a new id, the same question asked twice while running is not.
        digest = hashlib.sha1(self.key[1].encode("utf-8")).hexdigest()[:8]
        self.id = f"{kind.replace('.', '-')}-{digest}-{n}"
        self.label, self.scope, self.keep = label or kind, scope, keep
        self.state = QUEUED
        self.done: Optional[int] = None
        self.total: Optional[int] = None
        self.unit = ""
        self.note = ""
        self.error = ""
        self.exc: Optional[BaseException] = None
        self.result: Any = None
        self.partial: Any = None
        self.partial_ref = 0
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.cancel = threading.Event()
        self.finished = threading.Event()
        # P37: set when the WORK has stopped (its function returned or raised) — a cancelled job
        # is `finished` at once, its work only at its next checkpoint (`handoff` waits on this).
        self.stopped = threading.Event()
        # client id -> the tokens of its requests that still want this job (0.0.28 D)
        self.watchers: Dict[str, set] = {}
        # client id -> its tokens while its stream is down (restored by `client_back`)
        self.away: Dict[str, set] = {}
        self.lease = 0.0                 # until when an asker with no page still wants it
        self.away_until = 0.0            # until when a page that dropped (`away`) may come back
        self.cancellable = True
        self._wakes: List[Callable[[], None]] = []

    # -- inside the work (any thread) -------------------------------------------------------
    def _report(self, done: Optional[int] = None, total: Optional[int] = None,
                unit: Optional[str] = None, note: Optional[str] = None,
                partial: Any = None, partial_ref: Optional[int] = None) -> None:
        with self.reg._lock:
            if self.state in FINISHED:
                return
            if done is not None:
                self.done = int(done)
            if total is not None:
                self.total = int(total)
            if unit is not None:
                self.unit = unit
            if note is not None:
                self.note = str(note)[:300]
            first = False
            if partial is not None:
                self.partial = partial
                self.partial_ref = partial_ref if partial_ref is not None else self.partial_ref + 1
                first = self.state != PARTIAL
                self.state = PARTIAL
        if first:
            self.reg._emit(self)            # the first partial is shown at once, not at the tick

    # -- what the page sees ---------------------------------------------------------------------
    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started

    def snapshot(self) -> Dict[str, Any]:
        el = self.elapsed()
        eta = None
        # An ETA only where the units are alike (files, hits): ring 2 of a ripple is not "like"
        # ring 1 — a linear guess there would mislead.
        if self.state in (RUNNING, PARTIAL) and self.total and self.done and self.done > 0 \
                and self.unit in ("files", "hits"):
            eta = round(el * (self.total - self.done) / self.done, 1)
        return {"id": self.id, "kind": self.kind, "label": self.label, "scope": self.scope,
                "state": self.state, "done": self.done, "total": self.total, "unit": self.unit,
                "elapsed": round(el, 2), "eta": eta, "partial_ref": self.partial_ref,
                "note": self.note, "error": self.error, "keep": self.keep,
                "cancellable": self.cancellable, "watchers": len(self.watchers),
                "created": self.created, "finished": self.finished_at, "args": self.public_args()}

    def public_args(self) -> Dict[str, Any]:
        """The question's scalar arguments, short — what the drawer needs to label and re-open
        the result (a path list or the repo root stays on the server)."""
        out: Dict[str, Any] = {}
        for k, v in self.args.items():
            if k in ("route", "root") or isinstance(v, (list, dict)):
                continue
            out[k] = v[:200] if isinstance(v, str) else v
        return out


class _Lane:
    def __init__(self, name: str, workers: int) -> None:
        self.name, self.workers = name, max(1, workers)
        self.q: "queue.Queue[Optional[Job]]" = queue.Queue()
        self.threads: List[threading.Thread] = []

    def put(self, job: Job) -> None:
        if not self.threads:                       # started on first use: nothing at idle
            self.threads = [threading.Thread(target=self._work, daemon=True,
                                             name=f"mokata-job-{self.name}-{i}")
                            for i in range(self.workers)]
            for t in self.threads:
                t.start()
        self.q.put_nowait(job)

    def _work(self) -> None:
        while True:
            job = self.q.get()
            if job is None:
                return
            job.reg._run(job)

    def close(self) -> None:
        for _ in self.threads:
            self.q.put_nowait(None)


class JobRegistry:
    """Every job of one IDE server (see the module note)."""

    def __init__(self, lanes: Optional[Dict[str, int]] = None) -> None:
        self._lock = threading.RLock()
        self._active: Dict[Tuple[str, str], Job] = {}
        self._by_id: "OrderedDict[str, Job]" = OrderedDict()
        self._lanes = {n: _Lane(n, w) for n, w in (lanes or LANE_WORKERS).items()}
        self._publish: Optional[Callable[[Dict[str, Any]], None]] = None
        self._ticker: Optional[threading.Thread] = None
        self._closed = False
        self._failing: Dict[str, Dict[str, Any]] = {}
        self._runs: Dict[Tuple[str, str], int] = {}
        self._away: Dict[str, List[str]] = {}
        # (client, token) -> until when: requests superseded before the page learned their job.
        # Insertion order IS expiry order (every entry lives LEFT_S), so the cap evicts the front
        # in O(1) and the ticker prunes only the expired front (verifier R2-1).
        self._left: "OrderedDict[Tuple[str, str], float]" = OrderedDict()
        self._streams: Dict[str, int] = {}
        self.started = 0                           # jobs that actually ran (tests read it)

    # -- wiring ---------------------------------------------------------------------------
    def attach(self, publish: Callable[[Dict[str, Any]], None]) -> None:
        """`publish(event)` must be callable from any thread (the server hands the bus over)."""
        self._publish = publish

    def _emit(self, job: Job) -> None:
        pub = self._publish
        if pub is None:
            return
        with self._lock:
            snap = job.snapshot()
        try:
            pub({"kind": "job", "job": snap})
        except RuntimeError:                       # the loop is closing: nobody is listening
            pass

    # -- starting / joining -------------------------------------------------------------------
    def start(self, kind: str, args: Dict[str, Any], fn: Callable[[], Any], *, label: str = "",
              scope: str = "", keep: Optional[bool] = None, client: Optional[str] = None,
              token: Optional[str] = None,
              on_end: Optional[Callable[[], None]] = None) -> Job:
        """The job answering `(kind, args)`: the running one, or a new one. A FINISHED answer is
        never handed to a new question (the repo may have moved since); only the page that waited
        for it fetches it, by id (`answer`)."""
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        key = (kind, _canon(args))
        new = False
        with self._lock:
            job = self._active.get(key)
            if job is None:
                if len(self._runs) > 4096:
                    self._runs = {k: v for k, v in self._runs.items() if k in self._active}
                self._runs[key] = n = self._runs.get(key, 0) + 1
                job = Job(self, kind, args, fn, label, scope,
                          kind in KEEP_KINDS if keep is None else keep, n)
                if on_end is not None:
                    job._wakes.append(on_end)
                if self._closed:
                    job.cancel.set()
                self._active[key] = job
                self._by_id[job.id] = job
                self._trim()
                new = True
            self._watch(job, client, REQUEST.get() if token is None else token)
        if new:
            self._lanes[KINDS[kind]].put(job)
            self._emit(job)
            self._ensure_ticker()
        return job

    def _watch(self, job: Job, client: Optional[str], token: str) -> None:
        if client:
            if self._left.get((client, token or ""), 0.0) > time.monotonic():
                return                                     # that request was already superseded
            job.watchers.setdefault(client, set()).add(token or "")
        else:
            job.lease = time.monotonic() + GRACE_S

    def watch(self, job: Job, client: Optional[str], token: str = "") -> None:
        with self._lock:
            self._watch(job, client, token)

    def leave(self, job: Job, client: Optional[str], token: Optional[str]) -> None:
        """One REQUEST stopped wanting it (superseded): only its own token goes. A query job that
        no request of any page still wants is cancelled. A leave names its request — there is no
        "leave for the whole page" (that is the page's stream closing, `client_gone`)."""
        if not token:
            raise ValueError("a leave names the request that leaves (its token)")
        with self._lock:
            for held in (job.watchers, job.away):
                toks = held.get(client or "")
                if toks is not None:
                    toks.discard(token)
                    if not toks:
                        del held[client or ""]
            orphan = self._orphan(job, time.monotonic())
        if orphan:
            self._cancel(job, "nobody is waiting for this any more")

    def leave_request(self, client: Optional[str], token: Optional[str]) -> None:
        """One REQUEST was superseded before its page learned which job (if any) it started — the
        page aborted the fetch that would have said (0.0.28 D, verifier L6). Its want leaves every job
        it is in, and a job that request starts later (this leave travels on its own connection and
        can arrive first) never takes it: the token is remembered for `LEFT_S`."""
        if not token:
            raise ValueError("a leave names the request that leaves (its token)")
        who = client or ""
        now = time.monotonic()
        hit: List[Job] = []
        with self._lock:
            self._left.pop((who, token), None)             # a repeat moves to the back (its new expiry)
            self._left[(who, token)] = now + LEFT_S
            while len(self._left) > LEFT_MAX:              # bounded with no ticker running: oldest goes
                self._left.popitem(last=False)
            for job in self._active.values():
                for held in (job.watchers, job.away):
                    toks = held.get(who)
                    if toks is not None and token in toks:
                        toks.discard(token)
                        if not toks:
                            del held[who]
                        hit.append(job)
            orphans = [j for j in dict.fromkeys(hit) if self._orphan(j, now)]
        for j in orphans:
            self._cancel(j, "nobody is waiting for this any more")

    @staticmethod
    def _orphan(job: Job, now: float) -> bool:
        """Nobody wants it: no request of a page here, none of a page that is away inside its
        reconnect window (`away_until`), and no page-less asker's lease still running."""
        if job.keep or job.state in FINISHED or job.watchers:
            return False
        if job.away and now <= job.away_until:
            return False
        return now > job.lease

    def _forget_away(self, now: float) -> None:
        """Under the lock: past a page's reconnect window nothing of it is held (bounded growth): its
        tokens leave every job whose window ran out, and the registry's list drops every id that is no
        longer an active job waiting for that page."""
        for j in self._by_id.values():
            if j.away and (j.state in FINISHED or now > j.away_until):
                j.away.clear()
        live = {j.id: j for j in self._active.values()}
        for client in list(self._away):
            ids = [i for i in self._away[client] if i in live and client in live[i].away]
            if ids:
                self._away[client] = ids
            else:
                del self._away[client]

    def stream_opened(self, client: str) -> bool:
        """A page's event stream opened; True when it is the page's only open stream. A page can
        hold two for a moment (an EventSource reconnects before the server notices the old one
        ended), so the page is gone only when its LAST stream closes (`stream_closed`)."""
        with self._lock:
            n = self._streams[client] = self._streams.get(client, 0) + 1
            return n == 1

    def stream_closed(self, client: str) -> bool:
        """A page's event stream ended; True when it was its last one — then, and only then, the
        page has gone (`client_gone`). A stale stream ending after its replacement opened: False."""
        with self._lock:
            n = self._streams.get(client, 0) - 1
            if n > 0:
                self._streams[client] = n
                return False
            self._streams.pop(client, None)
            return True

    def client_gone(self, client: str) -> None:
        """A page's event stream closed. An EventSource reconnects by itself (a network blip, a
        server hiccup), so its jobs are not dropped at once: each waits `RECONNECT_S` (`away_until`),
        and `client_back` within it re-attaches every request it had. Past it, a query job nobody
        wants stops. A second drop before the page came back loses nothing (appended, never
        assigned)."""
        until = time.monotonic() + RECONNECT_S
        with self._lock:
            jobs = [j for j in self._active.values() if client in j.watchers]
            for j in jobs:
                j.away.setdefault(client, set()).update(j.watchers.pop(client))
                j.away_until = max(j.away_until, until)
            if jobs:
                held = self._away.setdefault(client, [])
                held.extend(j.id for j in jobs if j.id not in held)
        if jobs:
            self._ensure_ticker()

    def client_back(self, client: str) -> None:
        """The page's stream reconnected: it wants what it wanted before — request by request."""
        with self._lock:
            for jid in self._away.pop(client, []):
                j = self._by_id.get(jid)
                if j is None:
                    continue
                toks = j.away.pop(client, None)
                if toks and j.state not in FINISHED:
                    j.watchers.setdefault(client, set()).update(toks)

    # -- reading ----------------------------------------------------------------------------
    def answer(self, job_id: str, kind: str, args: Dict[str, Any]) -> Optional[Job]:
        """The DONE job `job_id`, if it answered exactly this question — what a page that waited
        on it re-asks for (the route's `job=` parameter). Anything else: None (ask afresh)."""
        with self._lock:
            j = self._by_id.get(job_id)
            if j is not None and j.state == DONE and j.key == (kind, _canon(args)):
                return j
            return None

    def find(self, kind: str, args: Dict[str, Any]) -> Optional[Job]:
        """The ACTIVE job answering `(kind, args)`, if one runs."""
        with self._lock:
            return self._active.get((kind, _canon(args)))

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._by_id.get(job_id)

    def listing(self) -> Dict[str, Any]:
        with self._lock:
            active = [j.snapshot() for j in self._active.values()]
            done = [j.snapshot() for j in reversed(self._by_id.values()) if j.state in FINISHED]
        return {"active": active, "recent": done[:HISTORY]}

    def failing(self) -> Dict[str, Dict[str, Any]]:
        """R35 — per kind (`CAPABILITY_OF`), the last failure not yet followed by a success."""
        with self._lock:
            return {k: dict(v) for k, v in self._failing.items()}

    def running_kept(self) -> List[Job]:
        """P37 — the jobs that outlive their page (index builds, updates; `KEEP_KINDS`) and are
        running in THIS process: what stopping the server must ask about."""
        with self._lock:
            return [j for j in self._active.values() if j.keep and j.cancellable
                    and j.state not in FINISHED]

    def busy(self) -> int:
        with self._lock:
            return len(self._active)

    # -- cancelling -------------------------------------------------------------------------
    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job is not None:
            self._cancel(job, "cancelled")
        return job

    def _cancel(self, job: Job, why: str) -> None:
        with self._lock:
            if job.state in FINISHED or not job.cancellable:
                return
            job.cancel.set()
            if job.kind in SEALING_KINDS and job.state in (RUNNING, PARTIAL, CANCELLING):
                # P21: the work answers — it stops before its commit point, or it already sealed.
                job.state = CANCELLING
                job.note = "cancelling — stopping before the commit point"
                defer = True
            else:
                defer = False
        if defer:
            self._emit(job)
            return
        with self._lock:
            if job.state in FINISHED:
                return
            # The work stops at its next checkpoint (per file / per wait slice); the job is said
            # cancelled NOW so the page never waits on a walk's last file to be told.
            self._finish(job, CANCELLED, error="" if why == "cancelled" else why)
        self._emit(job)

    # -- running ----------------------------------------------------------------------------
    def _run(self, job: Job) -> None:
        with self._lock:
            if job.cancel.is_set() or job.state in FINISHED:
                if job.state not in FINISHED:
                    self._finish(job, CANCELLED, error="nobody was waiting any more — not started")
                skip = True
            else:
                skip = False
                job.state, job.started = RUNNING, time.time()
                self.started += 1
        if skip:
            job.stopped.set()
            self._emit(job)
            return
        self._emit(job)
        try:
            self._run_work(job)
        finally:
            job.stopped.set()

    def _run_work(self, job: Job) -> None:
        state, result, error, exc = DONE, None, "", None
        prev = getattr(_CURRENT, "job", None)
        _CURRENT.job = job
        try:
            with C.scope(job.cancel), C.reporting(job._report):
                result = job.fn()
            if job.cancel.is_set() and job.cancellable:
                state = CANCELLED
        except C.Cancelled:
            state = CANCELLED
        except MokataError as e:                   # a failure mokata DEFINED: said as such
            state, error, exc = FAILED, f"{type(e).__name__}: {e}"[:500], e
        except Exception as e:  # noqa: BLE001 — a bug: failed + traceback on stderr, never lost
            state, error, exc = FAILED, f"{type(e).__name__}: {e}"[:500], e
            try:
                sys.stderr.write(f"mokata ide: job {job.kind} failed — this is a bug, please "
                                 "report it:\n" + "".join(traceback.format_exception(
                                     type(e), e, e.__traceback__)))
            except (OSError, ValueError):          # pragma: no cover — no stderr
                pass
        finally:
            _CURRENT.job = prev
        with self._lock:
            if job.state not in FINISHED:
                job.result, job.exc = result, exc
                self._finish(job, state, error=error)
            if state == DONE and job.kind in CAPABILITY_OF:
                self._failing.pop(job.kind, None)
            elif state == FAILED and job.kind in CAPABILITY_OF:
                self._failing[job.kind] = {
                    "kind": job.kind, "capability": CAPABILITY_OF[job.kind], "label": job.label,
                    "error": error, "at": time.time(), "id": job.id,
                    "args": {k: v for k, v in job.args.items() if k != "route"}}
        self._emit(job)

    def _finish(self, job: Job, state: str, error: str = "") -> None:
        """Under the lock: the job's terminal state, its waiters woken, kept or forgotten."""
        job.state, job.finished_at = state, time.time()
        if job.started is None:
            job.started = job.finished_at
        if error:
            job.error = error
        if state == DONE and job.partial is not None:
            job.partial = None                     # the result supersedes it
        if self._active.get(job.key) is job:
            del self._active[job.key]
        job.finished.set()
        wakes, job._wakes = job._wakes, []
        for w in wakes:
            w()

    def on_finish(self, job: Job, wake: Callable[[], None]) -> None:
        with self._lock:
            if not job.finished.is_set():
                job._wakes.append(wake)
                return
        wake()

    def _trim(self) -> None:
        """Under the lock: every active job stays addressable, and the last HISTORY finished ones
        (a page fetches its answer within moments of the `done` event)."""
        finished = [i for i, j in self._by_id.items() if j.state in FINISHED]
        for i in finished[:max(0, len(finished) - HISTORY)]:
            del self._by_id[i]

    # -- the ticker: progress at <= 1 s, leases, and nothing at idle --------------------------
    def _ensure_ticker(self) -> None:
        with self._lock:
            if self._ticker is not None and self._ticker.is_alive():
                return
            self._ticker = threading.Thread(target=self._tick, daemon=True,
                                            name="mokata-job-ticker")
            self._ticker.start()

    def _tick(self) -> None:
        while True:
            time.sleep(TICK_S)
            if not self._sweep(time.monotonic()):
                return

    def _sweep(self, now: float) -> bool:
        """One tick at `now`: cancel the query jobs no request wants past their lease, publish the
        rest. False (and the ticker ends) when nothing is active."""
        with self._lock:
            active = list(self._active.values())
            orphans = [j for j in active if self._orphan(j, now)]
            self._forget_away(now)
            while self._left and next(iter(self._left.values())) <= now:
                self._left.popitem(last=False)             # only the expired front: order is expiry
            if not active:
                self._ticker = None
                return False
        for j in orphans:
            self._cancel(j, "nobody is waiting for this any more")
        for j in active:
            if j not in orphans:
                self._emit(j)
        return True

    def close(self, wait: float = 0.0) -> None:
        """Cancel everything; with `wait`, give the running work that long to reach its next
        checkpoint (a build kills its subprocess there — none outlives the server). A job SEALED
        past its commit point (P21: a merge writing the main checkout) is waited for in full — a
        shutdown never abandons a half-written checkout."""
        with self._lock:
            self._closed = True
            jobs = list(self._active.values())
        for j in jobs:
            with self._lock:
                if j.cancellable:
                    j.cancel.set()
        for j in jobs:
            if not j.cancellable:
                j.stopped.wait(SEALED_WAIT_S)
        for lane in self._lanes.values():
            lane.close()
        end = time.monotonic() + wait
        for lane in self._lanes.values():
            for t in lane.threads:
                left = end - time.monotonic()
                if left <= 0:
                    return
                t.join(left)


# --------------------------------------------------------------------------- sealing (P21)
_CURRENT = threading.local()
#: How long a shutdown waits for a sealed job (a merge's checkout write) — long, never forever.
SEALED_WAIT_S = 300.0


def seal_current() -> bool:
    """0.0.27 P21 — called by work at its commit point. Atomically, under the registry lock: False
    when a Cancel already won (the work must stop, unchanged); otherwise the job becomes
    uncancellable and True. Outside a job (the CLI, a test) there is nothing to race: True."""
    job = getattr(_CURRENT, "job", None)
    if job is None:
        return not (C.current() is not None and C.current().is_set())
    with job.reg._lock:
        if job.cancel.is_set():
            return False
        job.cancellable = False
        job.note = "past the commit point — cannot be cancelled"
    job.reg._emit(job)
    return True


# --------------------------------------------------------------------------- module-level door
# Background work started deep inside a module (the meaning index's first build, ⌘P's symbol
# index) runs as a job of the server that is serving this process, so it is visible and
# cancellable. With no server (the CLI, a unit test) it is a plain daemon thread, as before.
_INSTALLED: Optional[JobRegistry] = None


def install(reg: Optional[JobRegistry]) -> None:
    global _INSTALLED
    _INSTALLED = reg


def installed() -> Optional[JobRegistry]:
    return _INSTALLED


def background(kind: str, args: Dict[str, Any], fn: Callable[[], Any], *, label: str,
               scope: str = "", on_end: Optional[Callable[[], None]] = None) -> Optional[Job]:
    """`on_end` runs however the job ends — even one cancelled before it started, whose `fn`
    never ran (a waiter on the work must never wait for ever)."""
    reg = _INSTALLED
    if reg is not None:
        return reg.start(kind, args, fn, label=label, scope=scope, keep=True, on_end=on_end)
    threading.Thread(target=fn, daemon=True, name=f"mokata-{kind}").start()
    return None


__all__ = ["CANCELLING", "REQUEST", "CAPABILITY_OF", "Job", "JobRegistry", "KINDS", "SEALING_KINDS", "STATES", "SYNC_WINDOW_S", "TICK_S", "install",
           "installed", "background", "seal_current"]
