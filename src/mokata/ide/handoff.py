"""Closing the IDE with work running (0.0.26 P37; Jas, 2026-10-22: "while closing, it should
confirm to run in background").

The jobs that outlive their page (`jobs.KEEP_KINDS`: a code-graph build or update, the meaning
index, the symbol / AST index, `mokata update`, the model download) are never killed silently when
the server stops:

    ask(jobs)          the terminal prompt on Ctrl+C: [b]ackground / [c]ancel safely / [w]ait
    stop(reg, root, choice)
        background     each job is stopped at its next checkpoint here and handed to a DETACHED
                       worker (`python -m mokata.ide.handoff <record>`, `quiet_spawn.detached`)
                       that runs the same work to the end and writes its outcome to the record
        cancel         each job stops at its next checkpoint; the index it was writing is left as
                       the last COMPLETE one (a rebuild is written beside it, an update is recorded
                       — `crg_build`, R39) and the record says `cancelled`
        wait           the server waits for the jobs to finish, saying how far they are
    A stop with no terminal to ask (no TTY, SIGTERM, the idle exit) is `background`.

The records live in the IDE's own directory (`statedir.ide_dir(root)/jobs/`, run-state, never
committed). The next `mokata ide` lists them in Activity and Health (`snapshots`, `health_rows`):
still running (the worker is alive), done or failed — with the worker's own message.

A record names a KIND and its arguments, never code: the worker runs only the work `WORK` maps that
kind to, so a record file cannot make it run anything else.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

BACKGROUND = "background"
CANCEL = "cancel"
WAIT = "wait"
CHOICES = (BACKGROUND, CANCEL, WAIT)
#: How long a job is given to reach its next checkpoint before it is handed over / recorded.
STOP_WAIT_S = 20.0
#: Records kept (newest first) and how long a finished one is listed.
KEEP_RECORDS = 20
LIST_FOR_S = 7 * 24 * 3600.0
_PROGRESS_EVERY_S = 2.0


# ----------------------------------------------------------------------------- the records
def records_dir(root: str) -> str:
    from .statedir import ide_dir
    return os.path.join(ide_dir(root), "jobs")


def _write(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, sort_keys=True)
    os.replace(tmp, path)


def _read(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _alive(pid: Any) -> bool:
    from ..session_registry import pid_alive
    return pid_alive(pid)


def _record_for(job: Any, root: str, state: str, **extra: Any) -> Dict[str, Any]:
    return {"id": job.id, "kind": job.kind, "label": job.label, "scope": job.scope,
            "root": os.path.abspath(root), "args": job.public_args(), "state": state,
            "done": job.done, "total": job.total, "unit": job.unit, "note": "",
            "error": "", "created": job.created, "handed": time.time(), **extra}


# ----------------------------------------------------------------------------- the work, by kind
def _crg_rebuild(root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from ..knowledge import crg_build
    res = crg_build.build_now(root, full=True, swap=True)
    if res.busy is not None:
        return {"ok": False, "message": f"not run: {res.busy.line()}"}
    return {"ok": res.ok, "message": res.detail if res.ok else res.error}


def _crg_update(root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from ..knowledge import crg_build
    if crg_build.probe_db(crg_build.db_path(root)) == crg_build.INDEX_ABSENT:
        return {"ok": True, "message": "no code-graph index to keep current"}
    res = crg_build.build_now(root)          # an interrupted update is repaired beside the index
    if res.busy is not None:
        return {"ok": True, "message": f"another process is updating it: {res.busy.line()}"}
    return {"ok": res.ok, "message": res.detail if res.ok else res.error}


def _meaning(root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from . import search as SR
    emb = SR._load_embedder(allow_network=False)
    info = SR.build_index(root, emb) or {}
    return {"ok": True, "message": f"meaning index built ({info.get('embedded', '?')} chunks)"}


def _ast(root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from ..knowledge.freshness import _ast_backends
    from . import nav
    backends = _ast_backends(nav._layer(root))
    if not backends:
        return {"ok": True, "message": "no built-in AST index here: rebuilt at the next start"}
    for b in backends:
        ensure = getattr(b, "_ensure_index", None)
        if callable(ensure):
            ensure()                         # fills the on-disk parse cache the next start reads
    return {"ok": True, "message": "AST index parsed; the next start reads it from its cache"}


def _update(root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from ..quiet_spawn import hidden
    from ..update import plan_update
    plan = plan_update(root, check=False)
    if plan.argv is None:
        return {"ok": False, "message": f"run it yourself: {plan.command}"}
    rc = subprocess.run(list(plan.argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, check=False, **hidden()).returncode
    return {"ok": rc == 0, "message": "mokata updated" if rc == 0 else f"update exited {rc}"}


def _fetch(_root: str, _args: Dict[str, Any]) -> Dict[str, Any]:
    from . import search as SR
    out = SR.fetch_model()
    return {"ok": bool(out.get("available")), "message": out.get("note") or "model fetched"}


#: kind -> the work a detached worker finishes. A follow-only job (a build another process runs)
#: has nothing to hand over: it is that process's.
WORK: Dict[str, Callable[[str, Dict[str, Any]], Dict[str, Any]]] = {
    "index.crg": _crg_rebuild, "index.fresh": _crg_update, "index.meaning": _meaning,
    "index.ast": _ast, "index.symbols": _ast, "mokata.update": _update, "model.fetch": _fetch,
}


def handable(job: Any) -> bool:
    return job.kind in WORK and not (job.args or {}).get("follow")


# ----------------------------------------------------------------------------- stopping
def _line(j: Any) -> str:
    parts = [j.label or j.kind]
    if j.total:
        parts.append(f"{j.done or 0}/{j.total} {j.unit}".strip())
    elif j.done:
        parts.append(f"{j.done} {j.unit}".strip())
    parts.append(f"{int(j.elapsed())}s")
    if j.note:
        parts.append(j.note)
    return " · ".join(parts)


def describe(jobs: List[Any]) -> str:
    return "\n".join("  · " + _line(j) for j in jobs)


def ask(jobs: List[Any], *, read: Callable[[str], str] = input,
        say: Callable[[str], None] = print, root: str = ".") -> str:
    """The terminal prompt. Anything unrecognised asks again; end of input is `background`."""
    say(f"\nmokata ide: {len(jobs)} job(s) running:\n{describe(jobs)}")
    from ..notify import announce_prompt
    announce_prompt(root)                       # a blocking read: the attention signal says so
    while True:
        try:
            got = read("[b]ackground (finish after the IDE closes) / [c]ancel safely / [w]ait? "
                       ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return BACKGROUND
        for c in CHOICES:
            if got and c.startswith(got[0]):
                return c


def _wait_stopped(jobs: List[Any], secs: float) -> None:
    end = time.monotonic() + secs
    for j in jobs:
        j.stopped.wait(max(0.0, end - time.monotonic()))


def stop(reg: Any, root: str, choice: str, *, say: Callable[[str], None] = print,
         spawn: Optional[Callable[[str], int]] = None, wait_s: float = STOP_WAIT_S) -> List[Dict]:
    """Act on `choice` for every kept job running in `reg`; returns the records written."""
    jobs = reg.running_kept()
    if not jobs:
        return []
    out: List[Dict[str, Any]] = []
    if choice == WAIT:
        try:
            while any(not j.finished.is_set() for j in jobs):
                say(describe([j for j in jobs if not j.finished.is_set()]))
                for j in jobs:
                    j.finished.wait(_PROGRESS_EVERY_S)
        except KeyboardInterrupt:
            choice = BACKGROUND
            jobs = [j for j in jobs if not j.finished.is_set()]
        else:
            return out
    for j in jobs:
        reg.cancel(j.id)                     # stops at its next checkpoint (kills a subprocess)
    _wait_stopped(jobs, wait_s)
    for j in jobs:
        path = os.path.join(records_dir(root), f"{j.id}.json")
        if choice == BACKGROUND and handable(j):
            rec = _record_for(j, root, BACKGROUND, note="continuing after the IDE closed")
            _write(path, rec)
            rec["pid"] = (spawn or _spawn)(path)
            _write(path, rec)
            say(f"  → {j.label}: continues in the background (pid {rec['pid']})")
        else:
            why = ("cancelled when the IDE closed — the last complete index is kept"
                   if choice == CANCEL else
                   "cancelled when the IDE closed — it is redone at the next start")
            rec = _record_for(j, root, "cancelled", note=why, finished=time.time(),
                              stopped=j.stopped.is_set())
            _write(path, rec)
            say(f"  → {j.label}: {why}")
        out.append(rec)
    _prune(root)
    return out


def _spawn(record: str) -> int:
    from ..quiet_spawn import detached
    log = os.path.splitext(record)[0] + ".log"
    with open(log, "a", encoding="utf-8", newline="\n") as fh:
        proc = subprocess.Popen([sys.executable, "-m", "mokata.ide.handoff", record],
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                **detached())
    return proc.pid


# ----------------------------------------------------------------------------- the worker
def run_worker(record: str) -> int:
    """`python -m mokata.ide.handoff <record>`: finish the record's work, say how far it got, and
    write the outcome. Never raises: an outcome is always written."""
    from ..knowledge.cancel import reporting
    rec = _read(record)
    work = WORK.get(str(rec.get("kind")))
    root = str(rec.get("root") or "")
    if work is None or not root:
        rec.update(state="failed", error="nothing this worker can run", finished=time.time())
        _write(record, rec)
        return 2
    rec.update(state=BACKGROUND, pid=os.getpid(), started=time.time())
    _write(record, rec)
    last = [0.0]

    def progress(done: Optional[int] = None, total: Optional[int] = None,
                 unit: Optional[str] = None, note: Optional[str] = None, **_kw: Any) -> None:
        for k, v in (("done", done), ("total", total), ("unit", unit), ("note", note)):
            if v is not None:
                rec[k] = v
        if time.monotonic() - last[0] >= _PROGRESS_EVERY_S:
            last[0] = time.monotonic()
            try:
                _write(record, rec)
            except OSError:
                pass
    try:
        with reporting(progress):
            got = work(root, dict(rec.get("args") or {}))
        ok, msg = bool(got.get("ok")), str(got.get("message") or "")
        rec.update(state="done" if ok else "failed", note=msg if ok else "",
                   error="" if ok else msg)
    except Exception as exc:  # noqa: BLE001 — the record IS the report: always written
        rec.update(state="failed", error=f"{type(exc).__name__}: {exc}"[:500])
    rec["finished"] = time.time()
    _write(record, rec)
    return 0 if rec["state"] == "done" else 1


# ----------------------------------------------------------------------------- the next start
def records(root: str) -> List[Dict[str, Any]]:
    d = records_dir(root)
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return []
    out = [r for r in (_read(os.path.join(d, n)) for n in names) if r.get("id")]
    return sorted(out, key=lambda r: -float(r.get("handed") or 0))


def snapshots(root: str, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The records as Activity job snapshots: a live worker is `running` (not cancellable from
    here: it is another process), a dead one with no verdict `failed`."""
    now = time.time() if now is None else now
    out = []
    for r in records(root):
        state = r.get("state")
        if state == BACKGROUND:
            if _alive(r.get("pid")):
                state, note = "running", f"continuing after the IDE closed (pid {r.get('pid')})"
            else:
                state, note = "failed", ""
                r["error"] = r.get("error") or "the background worker stopped without a verdict"
        else:
            note = r.get("note") or ""
            if now - float(r.get("finished") or r.get("handed") or 0) > LIST_FOR_S:
                continue
        out.append({"id": r["id"], "kind": r.get("kind"), "label": r.get("label"),
                    "scope": r.get("scope"), "state": state, "done": r.get("done"),
                    "total": r.get("total"), "unit": r.get("unit") or "", "note": note,
                    "error": r.get("error") or "", "keep": True, "cancellable": False,
                    "background": True, "created": r.get("created"),
                    "finished": r.get("finished"), "elapsed": None, "eta": None,
                    "partial_ref": 0, "watchers": 0, "args": r.get("args") or {}})
    return out


def _prune(root: str) -> None:
    d = records_dir(root)
    for r in records(root)[KEEP_RECORDS:]:
        if r.get("state") == BACKGROUND and _alive(r.get("pid")):
            continue
        for ext in (".json", ".log"):
            try:
                os.remove(os.path.join(d, f"{r['id']}{ext}"))
            except OSError:
                pass


if __name__ == "__main__":                       # pragma: no cover — the detached worker
    raise SystemExit(run_worker(sys.argv[1]) if len(sys.argv) == 2 else 2)


__all__ = ["BACKGROUND", "CANCEL", "WAIT", "WORK", "ask", "stop", "run_worker", "records",
           "snapshots", "records_dir"]
