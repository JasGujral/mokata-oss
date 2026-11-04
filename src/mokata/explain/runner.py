"""The `explain.bundle` job when the SKILL asks (`explain_bundle` over MCP): it runs in the MCP
process itself — no cross-process call, no new credential — and publishes its progress to a
status file the IDE reads (`reviews/_jobs/<id>.json`).

  * POLL LEASE: the job lives only while someone polls it. `explain_bundle` polls; if nobody has
    for `LEASE_S`, the job cancels itself — Esc on the tool call never leaves an orphan running.
    It is never a keep-running kind.
  * CANCEL: `explain_bundle(cancel=<job>)`, or the IDE writing `<id>.cancel` next to the status
    file. Both are checked between files and between git calls; git children are killed
    (`kill_on_cancel`) before the job ends.
  * A partial bundle is never cached or served: the cache is written only after `build` returns.
  * Without an IDE nobody sees the status file; the user sees Claude relay the progress line the
    tool returns, and `cancel=` is the explicit stop. The skill says so.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import secrets as _secrets
import threading
import time
from typing import Any, Dict, Optional

from ..errors import MokataError
from ..knowledge import cancel as C
from . import bundle as B
from . import store as ST

KIND = "explain.bundle"
LEASE_S = 30.0
WATCH_S = 0.5

_LOCK = threading.Lock()
_JOBS: Dict[str, "BundleJob"] = {}
_BY_KEY: Dict[str, str] = {}


def _cached(root: str, spec: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The cached bundle of EXACTLY this target as it stands now (same SHAs, same uncommitted
    bytes) — rebuilt otherwise. Resolving is cheap; building is the long part."""
    r = B.resolve(root, spec)
    t = dict(r["target"])
    t["dirty_hash"] = B.current_dirty_hash(root, spec) if t.get("dirty") else ""
    return ST.find_cached(root, t)


class BundleJob:
    def __init__(self, root: str, spec: Dict[str, str], now: Optional[float] = None) -> None:
        self.id = _secrets.token_hex(8)
        self.root = root
        self.spec = spec
        self.flag = threading.Event()
        self.state = "running"
        self.done = 0
        self.total = 0
        self.unit = "files"
        self.note = "starting"
        self.error = ""
        self.result: Optional[Dict[str, Any]] = None
        self.started = time.time() if now is None else now
        self.last_poll = self.started
        self.cancel_why = ""
        self._last_write = 0.0
        self._thread = threading.Thread(target=self._run, name=f"mokata-explain-{self.id}",
                                        daemon=True)
        self._watch = threading.Thread(target=self._watch_loop, name=f"mokata-explain-w-{self.id}",
                                       daemon=True)

    # ---------------------------------------------------------------------------- status
    def snapshot(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": KIND, "spec": B.target_text(self.spec), "state": self.state,
                "done": min(self.done, self.total) if self.total else self.done,
                "total": self.total, "unit": self.unit, "note": self.note, "error": self.error,
                "started": self.started, "updated": time.time(), "last_poll": self.last_poll,
                "lease_s": LEASE_S, "cancel_why": self.cancel_why, "where": "mcp",
                "content_hash": (self.result or {}).get("content_hash", "")}

    def _publish(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_write < WATCH_S:
            return
        self._last_write = now
        try:
            ST.write_job(self.root, self.id, self.snapshot())
        except (OSError, MokataError):
            pass                          # the status file is a window; the job does not need it

    def _report(self, done: Any = None, total: Any = None, unit: Any = None, note: Any = None,
                **_kw: Any) -> None:
        if total is not None:
            self.total = int(total)
        if done is not None:
            self.done = int(done)
        if unit:
            self.unit = str(unit)
        if note is not None:
            self.note = str(note)[:200]
        self._publish()

    # ---------------------------------------------------------------------------- run
    def start(self) -> "BundleJob":
        self._publish(force=True)
        self._thread.start()
        self._watch.start()
        return self

    def _run(self) -> None:
        try:
            with C.scope(self.flag), C.reporting(self._report):
                hit = _cached(self.root, self.spec)
                built = None if hit else B.build(self.root, self.spec)
                C.checkpoint()
            if hit:
                self.result = {"content_hash": hit["bundle"]["content_hash"],
                               "pruned_bundles": 0, "cached": True}
            else:
                rec = ST.cache_bundle(self.root, built)
                self.result = {"content_hash": built["bundle"]["content_hash"],
                               "pruned_bundles": rec.get("pruned", 0), "cached": False,
                               "cache": rec.get("cache", "")}
            built = rec = None              # the record lives in the cache or its spill only
            self.state = "done"
            self.note = "done"
        except C.Cancelled:
            self.state = "cancelled"
            self.note = self.cancel_why or "cancelled"
        except (B.BundleRefused, ST.UnsafeStore) as exc:
            self.state = "failed"
            self.error = str(exc)
        except (OSError, MokataError, ValueError) as exc:
            self.state = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        self._publish(force=True)

    def _watch_loop(self) -> None:
        while self.state == "running":
            if time.time() - self.last_poll > LEASE_S:
                self.cancel("nobody polled it for %ds (the tool call was abandoned)" % LEASE_S)
            elif ST.cancel_requested(self.root, self.id):
                self.cancel("cancelled from the IDE")
            self.flag.wait(WATCH_S)

    def cancel(self, why: str) -> None:
        if not self.flag.is_set():
            self.cancel_why = why
            self.flag.set()

    def wait(self, secs: float) -> None:
        self._thread.join(secs)


def start(root: str, spec: Dict[str, str]) -> BundleJob:
    """Start the bundle job for `spec`, or join the one already running for it."""
    key = f"{root}\0{B.target_text(spec)}"
    with _LOCK:
        jid = _BY_KEY.get(key)
        job = _JOBS.get(jid or "")
        if job is not None and job.state == "running":
            job.last_poll = time.time()
            return job
        job = BundleJob(root, spec)
        _JOBS[job.id] = job
        _BY_KEY[key] = job.id
        for old in [j for j in _JOBS.values() if j.state != "running"][:-20]:
            _JOBS.pop(old.id, None)
    ST.prune_jobs(root)
    return job.start()


def get(job_id: str) -> Optional[BundleJob]:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        job.last_poll = time.time()
    return job


def shutdown() -> None:
    """Cancel every running job (the MCP server is stopping)."""
    with _LOCK:
        jobs = list(_JOBS.values())
    for j in jobs:
        j.cancel("the MCP server stopped")


__all__ = ["KIND", "LEASE_S", "BundleJob", "start", "get", "shutdown"]
