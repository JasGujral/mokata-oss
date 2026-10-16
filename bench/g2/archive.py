"""Read and VALIDATE a G2 run archive. Everything downstream (STUDY.md, the public proof page,
CTX-CONT, AXI-LEAN, the pipe-token audit) reads through here, so "what counts as a real run" is
decided in exactly one place.

A run set is PUBLISHABLE only if `validate(require_real=True)` returns no problems:
  * `run.json` present, schema known, `kind` re-derived from agent/judge/cloner ids and equal to
    the recorded kind (a hand-edited `"kind": "real"` over a fake agent is caught);
  * ≥ 5 repeats, ≥ 2 conditions including `bare`;
  * every cell complete: each run has its raw stream, parse, verdict and meta;
  * no harness error and no CONTAMINATED run (a condition that did not hold).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import SCHEMA
from .runner import MIN_REAL_REPEATS, derive_kind
from .streamparse import Trajectory, load_trajectory_json
from .tasks import Task, task_from_dict

REQUIRED_FILES = ("stream.jsonl", "trajectory.json", "verdict.json", "meta.json")


class ArchiveError(ValueError):
    pass


@dataclass
class RunRecord:
    condition: str
    task: str
    repeat: int
    path: str
    meta: Dict[str, Any] = field(default_factory=dict)
    verdict: Dict[str, Any] = field(default_factory=dict)
    _traj: Optional[Trajectory] = None

    @property
    def trajectory(self) -> Trajectory:
        if self._traj is None:
            with open(os.path.join(self.path, "trajectory.json"), encoding="utf-8") as fh:
                self._traj = load_trajectory_json(json.load(fh))
        return self._traj

    @property
    def graded(self) -> bool:
        return self.verdict.get("status") == "graded"

    @property
    def passed(self) -> bool:
        return bool(self.graded and self.verdict.get("overall_pass"))


class RunArchive:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        mpath = os.path.join(self.path, "run.json")
        if not os.path.exists(mpath):
            raise ArchiveError(f"{path}: no run.json — not a G2 run archive")
        with open(mpath, encoding="utf-8") as fh:
            self.manifest: Dict[str, Any] = json.load(fh)
        self.records: List[RunRecord] = []
        for cond in self.condition_names:
            for task in self.task_ids:
                for n in range(1, int(self.manifest.get("repeats", 0)) + 1):
                    rdir = os.path.join(self.path, cond, task, f"run{n}")
                    rec = RunRecord(condition=cond, task=task, repeat=n, path=rdir)
                    for name, attr in (("meta.json", "meta"), ("verdict.json", "verdict")):
                        p = os.path.join(rdir, name)
                        if os.path.exists(p):
                            with open(p, encoding="utf-8") as fh:
                                setattr(rec, attr, json.load(fh))
                    self.records.append(rec)

    # --- declared shape --------------------------------------------------------------------
    @property
    def kind(self) -> str:
        return str(self.manifest.get("kind"))

    @property
    def derived_kind(self) -> str:
        m = self.manifest
        return derive_kind((m.get("agent") or {}).get("id", ""), (m.get("judge") or {}).get("id", ""),
                           m.get("cloner", ""))

    @property
    def is_real(self) -> bool:
        return self.kind == "real" and self.derived_kind == "real"

    @property
    def condition_names(self) -> List[str]:
        return [c["name"] for c in self.manifest.get("conditions", [])]

    @property
    def task_ids(self) -> List[str]:
        return [t["id"] for t in self.manifest.get("tasks", [])]

    def tasks(self) -> List[Task]:
        out = []
        for t in self.manifest.get("tasks", []):
            d = dict(t)
            d.setdefault("prompt", "")
            out.append(task_from_dict(d, source="run.json"))
        return out

    def condition(self, name: str) -> Dict[str, Any]:
        return next(c for c in self.manifest.get("conditions", []) if c["name"] == name)

    def cell(self, condition: str, task: str) -> List[RunRecord]:
        return [r for r in self.records if r.condition == condition and r.task == task]

    def cells(self) -> Iterator[Tuple[str, str, List[RunRecord]]]:
        for c in self.condition_names:
            for t in self.task_ids:
                yield c, t, self.cell(c, t)

    def usable(self, records: List[RunRecord]) -> List[RunRecord]:
        return [r for r in records if all(os.path.exists(os.path.join(r.path, f)) for f in
                                          ("trajectory.json", "meta.json"))
                and not r.meta.get("harness_error")]

    # --- the one place "real and complete" is decided --------------------------------------
    def validate(self, require_real: bool = True) -> List[str]:
        m = self.manifest
        problems: List[str] = []
        if m.get("schema") != SCHEMA:
            problems.append(f"unknown schema {m.get('schema')!r} (expected {SCHEMA})")
        if self.kind != self.derived_kind:
            problems.append(f"run.json says kind={self.kind!r} but agent/judge/cloner make it "
                            f"{self.derived_kind!r}")
        if require_real and not self.is_real:
            problems.append(f"not a real run (agent={m.get('agent', {}).get('id')}, "
                            f"judge={m.get('judge', {}).get('id')}, cloner={m.get('cloner')})")
        if not m.get("finished"):
            problems.append("run did not finish (run.json has no 'finished')")
        if require_real and int(m.get("repeats", 0)) < MIN_REAL_REPEATS:
            problems.append(f"repeats={m.get('repeats')} < {MIN_REAL_REPEATS}")
        if "bare" not in self.condition_names or len(self.condition_names) < 2:
            problems.append("needs >= 2 conditions including 'bare'")
        for rec in self.records:
            where = f"{rec.condition}/{rec.task}/run{rec.repeat}"
            missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(rec.path, f))]
            if missing:
                problems.append(f"{where}: missing {', '.join(missing)}")
                continue
            if rec.meta.get("harness_error"):
                problems.append(f"{where}: harness error {rec.meta['harness_error']}")
            for c in rec.meta.get("contamination") or []:
                problems.append(f"{where}: CONTAMINATED — {c}")
        return problems

    def require_real(self) -> None:
        problems = self.validate(require_real=True)
        if problems:
            raise ArchiveError("REFUSING — this archive is not a complete real G2 run:\n  "
                               + "\n  ".join(problems))
