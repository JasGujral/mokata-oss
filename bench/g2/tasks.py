"""G2 tasks — one JSON file per task, a per-task rubric, and the mechanism it exercises.

The TASK TAXONOMY isolates which mokata mechanism each task exercises (G2 methodology bound,
doc 84 G2 row): `graph` · `memory` · `gates` · `error-recovery`. A result reported per mechanism is
what lets a reader see WHICH mechanism earned (or lost) a delta, instead of one blended number.

A task's repo is either a pinned git URL (`{"url": …, "ref": <sha>}`) or a TEMPLATE directory in
this package (`{"template": "fixtures/sample-repo"}`) that the cloner materializes into a git repo
once and then clones fresh for every run.

`decisions` (optional, CTX-CONT / D10): decisions the prompt states EARLY, each with `probe` terms
whose presence in later context means the decision survived. See `bench.g2.continuity`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MECHANISMS = ("graph", "memory", "gates", "error-recovery")
CRITERION_KINDS = ("outcome", "defect", "secret", "process")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS_DIR = os.path.join(HERE, "tasks")


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class Criterion:
    id: str
    kind: str
    text: str


@dataclass(frozen=True)
class Decision:
    id: str
    text: str
    probe: tuple                     # lower-cased terms; ALL must appear for "present"


@dataclass
class Task:
    id: str
    mechanism: str
    prompt: str
    repo: Dict[str, str]
    rubric: List[Criterion]
    max_turns: int = 40
    decisions: List[Decision] = field(default_factory=list)
    develop_marker: Optional[str] = None   # tool name whose first use starts "develop" (default: first edit)
    source: str = ""

    def template_dir(self) -> Optional[str]:
        t = self.repo.get("template")
        return os.path.join(HERE, t) if t else None


def task_from_dict(d: Dict[str, Any], source: str = "") -> Task:
    for key in ("id", "mechanism", "prompt", "repo", "rubric"):
        if key not in d:
            raise TaskError(f"{source}: task is missing {key!r}")
    if d["mechanism"] not in MECHANISMS:
        raise TaskError(f"{source}: mechanism {d['mechanism']!r} not in {MECHANISMS}")
    repo = d["repo"]
    if not (isinstance(repo, dict) and (repo.get("template") or (repo.get("url") and repo.get("ref")))):
        raise TaskError(f"{source}: repo must be {{'template': dir}} or {{'url': …, 'ref': <pinned sha>}}")
    rubric = []
    for c in d["rubric"]:
        if c.get("kind") not in CRITERION_KINDS:
            raise TaskError(f"{source}: criterion {c.get('id')!r} kind must be one of {CRITERION_KINDS}")
        rubric.append(Criterion(id=str(c["id"]), kind=c["kind"], text=str(c["text"])))
    if not rubric:
        raise TaskError(f"{source}: a task with no rubric cannot be judged")
    decisions = [Decision(id=str(x["id"]), text=str(x["text"]),
                          probe=tuple(str(p).lower() for p in x["probe"]))
                 for x in d.get("decisions") or []]
    for dec in decisions:
        if not dec.probe:
            raise TaskError(f"{source}: decision {dec.id!r} has no probe terms")
    return Task(id=str(d["id"]), mechanism=d["mechanism"], prompt=str(d["prompt"]), repo=dict(repo),
                rubric=rubric, max_turns=int(d.get("max_turns", 40)), decisions=decisions,
                develop_marker=d.get("develop_marker"), source=source)


def load_tasks(directory: str = DEFAULT_TASKS_DIR, only: Optional[List[str]] = None) -> List[Task]:
    tasks = []
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json"):
            path = os.path.join(directory, fn)
            with open(path, encoding="utf-8") as fh:
                tasks.append(task_from_dict(json.load(fh), source=fn))
    if only:
        wanted = set(only)
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            raise TaskError(f"unknown task id(s): {sorted(missing)}")
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise TaskError(f"duplicate task ids in {directory}")
    if not tasks:
        raise TaskError(f"no tasks in {directory}")
    return tasks
