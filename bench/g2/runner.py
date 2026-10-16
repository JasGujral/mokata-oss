"""The G2 matrix runner: conditions × tasks × repeats, archived as `{condition}/{task}/run{N}/`.

Per run directory (all of it re-auditable — the parse and the verdict are DERIVED from files
beside them):
    stream.jsonl    raw agent stream, verbatim
    trajectory.json the parse (turns, per-turn context, timeline, tool results)
    verdict.json    the judge's parsed verdict
    judge_raw.txt   the judge's raw output
    meta.json       clone sha, exit code, argv, wall time, contamination findings

Repeats are INTERLEAVED (run 1 of every cell, then run 2, …) so a drift over the hours a matrix
takes — a model rollout, a rate-limit stretch — lands on every condition alike instead of on
whichever condition happened to run last.

`run.json` at the root declares the run: agent, judge (id + model), cloner, versions, the
condition fingerprints and the matrix. Its `kind` is DERIVED — `real` only when the agent is Claude
Code, the judge is a real judge and the clone was a git clone — never a flag a caller sets.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import SCHEMA, __version__
from .agent import ClaudeCodeAgent
from .conditions import Condition, Runner
from .judge import REAL_JUDGES
from .streamparse import parse_stream
from .tasks import Task

MIN_REAL_REPEATS = 5


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _rmtree(path: str) -> None:
    def retry(func, p, _exc):              # Windows: git objects are read-only
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def derive_kind(agent_id: str, judge_id: str, cloner_id: str) -> str:
    real = agent_id == ClaudeCodeAgent.id and judge_id in REAL_JUDGES and cloner_id == "git"
    return "real" if real else "fixture"


def _mokata_version() -> str:
    try:
        import mokata
        return getattr(mokata, "__version__", "unknown")
    except Exception:  # the harness can run a bare-only matrix with mokata absent
        return "absent"


def run_matrix(*, conditions: Sequence[Condition], tasks: Sequence[Task], repeats: int,
               out_dir: str, agent: Any, judge: Any, cloner: Any,
               setup_runner: Optional[Runner] = None, keep_workdirs: bool = False,
               log: Callable[[str], None] = lambda _m: None) -> str:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise FileExistsError(f"{out_dir} is not empty — a run archive is never overwritten")
    os.makedirs(out_dir, exist_ok=True)
    manifest: Dict[str, Any] = {
        "schema": SCHEMA, "harness_version": __version__,
        "kind": derive_kind(agent.id, judge.id, cloner.id),
        "agent": {"id": agent.id, "version": agent.version()},
        "judge": {"id": judge.id, "model": getattr(judge, "model", "")},
        "cloner": cloner.id, "mokata_version": _mokata_version(),
        "platform": f"{platform.system()} {platform.release()} / py{platform.python_version()}",
        "repeats": repeats,
        "conditions": [{"name": c.name, "description": c.description, "surface": c.surface,
                        "expect_mokata": c.expect_mokata, "fingerprint": c.fingerprint(),
                        "claude_md": c.claude_md, "setup": c.setup,
                        "disallowed_tools": c.disallowed_tools} for c in conditions],
        "tasks": [{"id": t.id, "mechanism": t.mechanism, "prompt": t.prompt, "repo": t.repo, "max_turns": t.max_turns,
                   "rubric": [{"id": c.id, "kind": c.kind, "text": c.text} for c in t.rubric],
                   "decisions": [{"id": d.id, "text": d.text, "probe": list(d.probe)}
                                 for d in t.decisions],
                   "develop_marker": t.develop_marker} for t in tasks],
        "started": _now(), "finished": None,
    }
    _write_json(os.path.join(out_dir, "run.json"), manifest)
    work_root = tempfile.mkdtemp(prefix="g2-work-")
    seq = 0
    try:
        for n in range(1, repeats + 1):
            for task in tasks:
                for cond in conditions:
                    run_dir = os.path.join(out_dir, cond.name, task.id, f"run{n}")
                    os.makedirs(run_dir)
                    workdir = os.path.join(work_root, f"{cond.name}-{task.id}-run{n}")
                    log(f"[g2] {cond.name}/{task.id}/run{n}")
                    seq += 1
                    meta: Dict[str, Any] = {"condition": cond.name, "task": task.id, "repeat": n,
                                            "seq": seq, "started": _now()}
                    try:
                        meta["clone_sha"] = cloner.fresh_clone(task, workdir)
                        extra = cond.prepare(workdir, run_dir, setup_runner)
                        res = agent.run(workdir, task.prompt, max_turns=task.max_turns, extra=extra,
                                        condition=cond.name, repeat=n)
                        with open(os.path.join(run_dir, "stream.jsonl"), "w", encoding="utf-8",
                                  newline="\n") as fh:
                            fh.write("\n".join(res.lines) + ("\n" if res.lines else ""))
                        tr = parse_stream(res.lines)
                        _write_json(os.path.join(run_dir, "trajectory.json"), tr.to_json())
                        meta.update(exit_code=res.exit_code, argv=res.argv, wall_s=res.wall_s,
                                    stderr_tail=res.stderr[-4000:], contamination=cond.check(tr))
                        verdict, raw = judge.grade(task, tr.transcript())
                        _write_json(os.path.join(run_dir, "verdict.json"), verdict.to_json())
                        with open(os.path.join(run_dir, "judge_raw.txt"), "w", encoding="utf-8",
                                  newline="\n") as fh:
                            fh.write(raw or "")
                    except Exception as exc:   # one broken run is RECORDED, never the whole matrix
                        meta["harness_error"] = f"{type(exc).__name__}: {exc}"
                        log(f"[g2]   harness error: {meta['harness_error']}")
                    finally:
                        meta["finished"] = _now()
                        _write_json(os.path.join(run_dir, "meta.json"), meta)
                        if not keep_workdirs and os.path.exists(workdir):
                            _rmtree(workdir)
    finally:
        if not keep_workdirs:
            _rmtree(work_root)
    manifest["finished"] = _now()
    if keep_workdirs:
        manifest["workdirs"] = work_root
    _write_json(os.path.join(out_dir, "run.json"), manifest)
    return out_dir
