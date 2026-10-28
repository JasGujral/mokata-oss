"""R7 (0.0.26 Lane R) — the A/B benchmark: Claude Code WITH mokata vs WITHOUT, scored by TESTS.

THE RULE IT EXISTS FOR (doc 85, doc 113 §4 R7): launch copy makes NO performance claim unless a
reproducible, published harness backs it — medians, the raw data committed, honest framing. This is
that harness. `tests/test_r7_no_unbacked_perf_claims.py` keeps product docs to it.

DESIGN
  * CORPUS — `bench/ab/tasks/<id>/`: `task.json` (id, title, prompt, max_turns), `repo/` (the
    starting code the agent works in), `hidden/` (the acceptance tests the agent never sees: copied
    in only AFTER the agent stops), `reference/` (a known-good solution: used by the dry run and by
    `check-corpus`, which proves every task's hidden tests FAIL on `repo/` and PASS on `reference/`).
  * ARMS — the G2 conditions (`bench.g2.conditions`): `bare` (no mokata, no MCP) and `mokata`
    (`mokata setup claude --scope project`), with G2's isolation flags and contamination check.
  * SCORE — RECORDED TEST OUTCOMES, never an LLM judge: the hidden suite is run with
    `python -m unittest` in the agent's workdir; `passed / total` and `solved = passed == total`.
  * N — `--repeats N` per task per arm (≥5 for a publishable run); MEDIANS per task and arm, plus
    the raw rows (`raw.jsonl`) and the run's facts (`run.json`: Claude Code version, mokata version,
    corpus hash, n, date) so every number can be re-derived (`summarize --raw`).
  * WHO PAYS — the operator's own `claude` login, headless, ONLY with `run --live --yes`: refused
    when `CI` is set; the child environment is scrubbed of every API-key variable (G2's list), so a
    run can never drift onto metered billing. mokata itself needs no key and reads none.
  * DRY RUN — `run --agent dry`: a scripted agent copies `reference/` into the workdir for the
    `mokata` arm and changes nothing for `bare`. No model call; it exercises the whole pipeline
    (prepare, agent, hidden tests, scoring, medians, report) and is what CI tests.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TASKS_DIR = os.path.join(HERE, "tasks")
ARMS = ("bare", "mokata")
MIN_PUBLISHABLE_REPEATS = 5
HIDDEN_DIRNAME = ".ab_hidden"
HIDDEN_TIMEOUT_S = 300

_RAN = re.compile(r"^Ran (\d+) tests?", re.M)
_FAILED = re.compile(r"^FAILED \(([^)]*)\)", re.M)


class CorpusError(ValueError):
    """A task directory is malformed."""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    prompt: str
    max_turns: int
    root: str

    @property
    def repo(self) -> str:
        return os.path.join(self.root, "repo")

    @property
    def hidden(self) -> str:
        return os.path.join(self.root, "hidden")

    @property
    def reference(self) -> str:
        return os.path.join(self.root, "reference")


def load_tasks(directory: str = DEFAULT_TASKS_DIR, only: Optional[Sequence[str]] = None) -> List[Task]:
    tasks = []
    for name in sorted(os.listdir(directory)):
        root = os.path.join(directory, name)
        spec = os.path.join(root, "task.json")
        if not os.path.isfile(spec):
            continue
        with open(spec, encoding="utf-8") as fh:
            d = json.load(fh)
        for key in ("id", "title", "prompt"):
            if not isinstance(d.get(key), str) or not d[key].strip():
                raise CorpusError(f"{spec}: `{key}` is required")
        if d["id"] != name:
            raise CorpusError(f"{spec}: id {d['id']!r} must equal its directory {name!r}")
        for sub in ("repo", "hidden", "reference"):
            if not os.path.isdir(os.path.join(root, sub)):
                raise CorpusError(f"{root}: missing {sub}/")
        tasks.append(Task(d["id"], d["title"], d["prompt"], int(d.get("max_turns", 40)), root))
    if only:
        tasks = [t for t in tasks if t.id in set(only)]
    return tasks


def corpus_hash(tasks: Sequence[Task]) -> str:
    """sha256 over every corpus file (path + bytes) — a result names exactly the corpus it ran."""
    h = hashlib.sha256()
    for t in tasks:
        for dirpath, dirnames, filenames in os.walk(t.root):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.endswith(".pyc"):
                    continue
                p = os.path.join(dirpath, fn)
                h.update(os.path.relpath(p, os.path.dirname(t.root)).replace(os.sep, "/").encode())
                with open(p, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


# ------------------------------------------------------------------------------------ scoring

@dataclass
class Score:
    passed: int
    total: int
    output_tail: str = ""

    @property
    def solved(self) -> bool:
        return self.total > 0 and self.passed == self.total

    @property
    def fraction(self) -> float:
        return self.passed / self.total if self.total else 0.0


def parse_unittest(output: str) -> Score:
    """`Ran N tests` and `FAILED (failures=a, errors=b)` → passed / total. No `Ran` line (the
    suite could not even import) is 0 of 0 — unsolved, never a pass."""
    ran = _RAN.findall(output)
    total = int(ran[-1]) if ran else 0
    bad = 0
    m = _FAILED.findall(output)
    if m:
        for part in m[-1].split(","):
            k, _, v = part.strip().partition("=")
            if k in ("failures", "errors") and v.isdigit():
                bad += int(v)
    return Score(max(0, total - bad), total, output[-1500:])


def score_workdir(workdir: str, hidden_dir: str, python: str = sys.executable) -> Score:
    """Copy the hidden suite in (AFTER the agent stopped) and run it — the recorded outcome."""
    dest = os.path.join(workdir, HIDDEN_DIRNAME)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(hidden_dir, dest)
    try:
        proc = subprocess.run([python, "-m", "unittest", "discover", "-s", HIDDEN_DIRNAME,
                               "-t", HIDDEN_DIRNAME], cwd=workdir, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=HIDDEN_TIMEOUT_S,
                              stdin=subprocess.DEVNULL,
                              env=dict(os.environ, PYTHONPATH=workdir, PYTHONDONTWRITEBYTECODE="1"))
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        out = f"hidden suite timed out after {HIDDEN_TIMEOUT_S}s"
    return parse_unittest(out)


# ------------------------------------------------------------------------------------ agents

class DryAgent:
    """No model call. The `mokata` arm gets the task's reference solution, `bare` gets nothing —
    a fixed, known answer that makes the pipeline's every step checkable in CI."""
    id = "dry"

    def __init__(self, solves: Sequence[str] = ("mokata",)):
        self.solves = tuple(solves)

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return "dry-run (no model)"

    def run(self, workdir: str, task: Task, arm: str, extra: Sequence[str]) -> Dict[str, Any]:
        if arm in self.solves:
            shutil.copytree(task.reference, workdir, dirs_exist_ok=True)
        return {"exit_code": 0, "wall_s": 0.0, "turns": 0, "usage": {}, "contamination": []}


class LiveAgent:
    """Claude Code headless on the operator's login (G2's agent: API-key env scrubbed)."""
    id = "claude-code"

    def __init__(self, claude_bin: str = "claude"):
        from bench.g2.agent import ClaudeCodeAgent
        self._agent = ClaudeCodeAgent(claude_bin=claude_bin)

    def available(self) -> bool:
        return self._agent.available()

    def version(self) -> str:
        return self._agent.version()

    def run(self, workdir: str, task: Task, arm: str, extra: Sequence[str]) -> Dict[str, Any]:
        from bench.g2.streamparse import parse_stream
        res = self._agent.run(workdir, task.prompt, max_turns=task.max_turns, extra=extra,
                              condition=arm, repeat=0)
        tr = parse_stream(res.lines)
        cond = _condition(arm)
        return {"exit_code": res.exit_code, "wall_s": res.wall_s, "turns": tr.num_turns,
                "usage": {"context_tokens": tr.total_context_tokens,
                          "output_tokens": tr.output_tokens},
                "contamination": cond.check(tr),
                "stream": res.lines}


def _condition(arm: str):
    from bench.g2 import conditions as C
    return {"bare": C.bare, "mokata": C.mokata_on}[arm]()


# ------------------------------------------------------------------------------------ the run

@dataclass
class Row:
    task: str
    arm: str
    repeat: int
    solved: bool
    passed: int
    total: int
    score: float
    wall_s: float
    turns: int
    agent_exit: int
    usage: Dict[str, Any] = field(default_factory=dict)
    contamination: List[str] = field(default_factory=list)


def _fresh_workdir(task: Task, base: str) -> str:
    work = tempfile.mkdtemp(prefix=f"ab-{task.id}-", dir=base)
    shutil.copytree(task.repo, work, dirs_exist_ok=True)
    for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=ab@mokata.invalid", "-c", "user.name=ab", "commit",
                  "-qm", "task start"]):
        try:            # a git repo, as the agent would find it; without git the copy still runs
            subprocess.run(argv, cwd=work, capture_output=True, stdin=subprocess.DEVNULL)
        except OSError:
            break
    return work


def run_matrix(tasks: Sequence[Task], *, repeats: int, agent: Any, out_dir: str,
               arms: Sequence[str] = ARMS, prepare: bool = True,
               setup_runner: Optional[Callable[[Sequence[str], str], int]] = None) -> List[Row]:
    """Every task × arm × repeat: fresh copy → condition → agent → hidden suite → a raw row.
    Rows are appended to `<out_dir>/raw.jsonl` as they finish (a killed run keeps what it had)."""
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "raw.jsonl")
    scratch = tempfile.mkdtemp(prefix="ab-work-")
    rows: List[Row] = []
    try:
        for task in tasks:
            for arm in arms:
                for rep in range(1, repeats + 1):
                    work = _fresh_workdir(task, scratch)
                    run_dir = os.path.join(out_dir, arm, task.id, f"run{rep}")
                    os.makedirs(run_dir, exist_ok=True)
                    extra: List[str] = []
                    if prepare:
                        extra = _condition(arm).prepare(work, run_dir, runner=setup_runner)
                    res = agent.run(work, task, arm, extra)
                    stream = res.pop("stream", None)
                    if stream is not None:
                        with open(os.path.join(run_dir, "stream.jsonl"), "w",
                                  encoding="utf-8", newline="\n") as fh:
                            fh.write("\n".join(stream) + "\n")
                    sc = score_workdir(work, task.hidden)
                    with open(os.path.join(run_dir, "hidden_tests.txt"), "w",
                              encoding="utf-8", newline="\n") as fh:
                        fh.write(sc.output_tail)
                    row = Row(task.id, arm, rep, sc.solved, sc.passed, sc.total,
                              round(sc.fraction, 4), res["wall_s"], res["turns"], res["exit_code"],
                              res.get("usage", {}), res.get("contamination", []))
                    rows.append(row)
                    with open(raw_path, "a", encoding="utf-8", newline="\n") as fh:
                        fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")
                    shutil.rmtree(work, ignore_errors=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return rows


def read_raw(path: str) -> List[Row]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(Row(**json.loads(line)))
    return rows


# ------------------------------------------------------------------------------------ the summary

def _median(xs: List[float]) -> Optional[float]:
    return round(statistics.median(xs), 4) if xs else None


def summarize(rows: Sequence[Row]) -> Dict[str, Any]:
    """Medians per task × arm and overall, from the raw rows ALONE (so a published summary is
    re-derivable from the committed raw data). A contaminated row is excluded and counted."""
    clean = [r for r in rows if not r.contamination]
    out: Dict[str, Any] = {"rows": len(rows), "contaminated": len(rows) - len(clean),
                           "tasks": {}, "overall": {}}
    for task in sorted({r.task for r in clean}):
        out["tasks"][task] = {}
        for arm in ARMS:
            rs = [r for r in clean if r.task == task and r.arm == arm]
            if rs:
                out["tasks"][task][arm] = _cell(rs)
    for arm in ARMS:
        rs = [r for r in clean if r.arm == arm]
        if rs:
            cell = _cell(rs)
            cell["median_task_score"] = _median([out["tasks"][t][arm]["median_score"]
                                                 for t in out["tasks"] if arm in out["tasks"][t]])
            out["overall"][arm] = cell
    b, m = out["overall"].get("bare"), out["overall"].get("mokata")
    if b and m:
        out["delta"] = {"solve_rate": round(m["solve_rate"] - b["solve_rate"], 4),
                        "median_task_score": round((m["median_task_score"] or 0)
                                                   - (b["median_task_score"] or 0), 4)}
    return out


def _cell(rs: Sequence[Row]) -> Dict[str, Any]:
    tokens = [float(r.usage.get("context_tokens", 0) + r.usage.get("output_tokens", 0))
              for r in rs if r.usage]
    return {"n": len(rs), "solve_rate": round(sum(r.solved for r in rs) / len(rs), 4),
            "median_score": _median([r.score for r in rs]),
            "median_wall_s": _median([r.wall_s for r in rs]),
            "median_turns": _median([float(r.turns) for r in rs]),
            "median_tokens": _median(tokens)}


def render_results(summary: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """RESULTS.md — what was run, the medians, and what may (not) be claimed from them."""
    publishable = (meta.get("agent") == "claude-code" and meta.get("repeats", 0)
                   >= MIN_PUBLISHABLE_REPEATS and summary["contaminated"] == 0)
    lines = ["# mokata A/B benchmark — results", "",
             f"- agent: {meta.get('agent')} ({meta.get('agent_version')})",
             f"- mokata: {meta.get('mokata_version')} · corpus sha256 `{meta.get('corpus')}`",
             f"- tasks: {meta.get('n_tasks')} · repeats per task per arm: {meta.get('repeats')} · "
             f"date: {meta.get('date')}",
             f"- rows: {summary['rows']} · contaminated (excluded): {summary['contaminated']}",
             f"- **publishable: {'yes' if publishable else 'NO'}** — a claim needs a live Claude "
             f"Code run, ≥{MIN_PUBLISHABLE_REPEATS} repeats and zero contaminated rows", ""]
    if meta.get("agent") == "dry":
        lines += ["> **DRY RUN — not a measurement.** The scripted agent applied the reference "
                  "solution to the `mokata` arm and nothing to `bare`; these numbers only prove "
                  "the pipeline. Never cite them.", ""]
    lines += [
             "| task | arm | n | solve rate | median score | median turns | median tokens |",
             "|---|---|---|---|---|---|---|"]
    for task, arms in summary["tasks"].items():
        for arm, c in arms.items():
            lines.append(f"| {task} | {arm} | {c['n']} | {c['solve_rate']:.0%} | "
                         f"{c['median_score']} | {c['median_turns']} | {c['median_tokens']} |")
    lines += ["", "## Overall", ""]
    for arm, c in summary["overall"].items():
        lines.append(f"- **{arm}**: solve rate {c['solve_rate']:.0%} · median task score "
                     f"{c['median_task_score']} · median tokens {c['median_tokens']}")
    if "delta" in summary:
        lines.append(f"- **delta (mokata − bare)**: solve rate {summary['delta']['solve_rate']:+.0%}"
                     f" · median task score {summary['delta']['median_task_score']:+}")
    lines += ["", "Scored by the hidden test suites' recorded outcomes (`python -m unittest`), never "
              "by a model. Raw rows: `raw.jsonl`; re-derive with `python -m bench.ab summarize "
              "--raw raw.jsonl`.", ""]
    return "\n".join(lines)


def write_report(out_dir: str, rows: Sequence[Row], meta: Dict[str, Any]) -> Dict[str, Any]:
    summary = summarize(rows)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    with open(os.path.join(out_dir, "RESULTS.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_results(summary, meta))
    return summary


def make_meta(tasks: Sequence[Task], agent: Any, repeats: int) -> Dict[str, Any]:
    try:
        from mokata import __version__ as mv
    except Exception:  # noqa: BLE001 — a harness fact, not a product path
        mv = "unknown"
    return {"agent": agent.id, "agent_version": agent.version(), "mokata_version": mv,
            "repeats": repeats, "n_tasks": len(tasks), "tasks": [t.id for t in tasks],
            "corpus": corpus_hash(tasks), "arms": list(ARMS),
            "date": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "scoring": "hidden unittest suite, recorded outcome (no LLM judge)"}


def check_corpus(tasks: Sequence[Task]) -> List[str]:
    """Every task must be a real discriminator: its hidden suite FAILS on `repo/` (there is work to
    do) and PASSES on `reference/` (the work is doable). Returns the problems (empty = sound)."""
    problems = []
    base = tempfile.mkdtemp(prefix="ab-check-")
    try:
        for t in tasks:
            work = _fresh_workdir(t, base)
            if score_workdir(work, t.hidden).solved:
                problems.append(f"{t.id}: the hidden suite already passes on repo/ — nothing to do")
            shutil.copytree(t.reference, work, dirs_exist_ok=True)
            sc = score_workdir(work, t.hidden)
            if not sc.solved:
                problems.append(f"{t.id}: the reference does not pass the hidden suite "
                                f"({sc.passed}/{sc.total})")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return problems


__all__ = ["ARMS", "MIN_PUBLISHABLE_REPEATS", "Task", "load_tasks", "corpus_hash", "Score",
           "parse_unittest", "score_workdir", "DryAgent", "LiveAgent", "Row", "run_matrix",
           "read_raw", "summarize", "render_results", "write_report", "make_meta",
           "check_corpus", "CorpusError"]
