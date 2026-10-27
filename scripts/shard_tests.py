#!/usr/bin/env python3
"""SHARD-TESTS — split the unit suite across N parallel CI jobs, deterministically, by MEASURED time.

`WINDOWS-CI-TIME` (0.0.26 O6). One job ran the whole unit suite per leg; on Windows that leg took
~59 min (CI #166, 3547 s) — ~2x Linux — and grew every release. Raising the job ceiling (70 -> 89)
only moved the cliff. This splits `discover -s tests -t tests` into N shards that run as N matrix
legs at once, so the gate's wall-clock is the slowest SHARD, not the whole suite.

THE SELECTION IS UNCHANGED — ONLY PARTITIONED
---------------------------------------------
`run` builds the suite with the very call `python -m unittest discover -s tests -t tests` makes
(`TestLoader().discover(start, top_level_dir=top)`, default pattern `test*.py`) and keeps the
top-level module suites assigned to its shard. Every module discovery yields is in EXACTLY ONE
shard (`tests/test_o6_shard_tests.py` proves it), so the union of the N legs is the suite one leg
used to run — the invocation-parity property (`tests/_invocation_parity.py`) reads this command
as that same selection.

THE ASSIGNMENT
--------------
Longest-processing-time-first over `scripts/shard_durations.json` (per-module seconds, MEASURED by
`measure` on a Linux box): modules in descending recorded time (name breaks ties), each to the
currently lightest shard (lowest index breaks ties). Pure and deterministic — every leg computes
the same plan independently, with no coordination. A module the record does not know yet is
costed at the record's MEDIAN, so a new test file never lands unbalanced and never errors; the
record is re-measured when too much of the suite is unknown (the test bounds it).

Shard 1 also carries the steps ci.yml runs once per leg (MCP-SDK check, two-process stress,
integration suite, smoke): their measured seconds (`fixed` in the record) PRELOAD shard 1, so the
balancer gives it fewer modules.

THE CEILING FROM THE RECORD
---------------------------
The record is measured on ONE box; CI runners are slower, and Windows slower still. `ci_scale`
says by how much: `per_os[OS]` multiplies recorded seconds into that runner's seconds, and
`leg_overhead_seconds` is what a leg pays before the suite (checkout, Python, pip). The test job's
`timeout-minutes` in ci.yml is derived from `max(predicted shard) x provisional_imbalance (until
absorbed; 1 after) x max(per_os) + overhead` with
`ci_ceiling.py`'s rule (1.5x, rounded up); `tests/test_o6_shard_tests.py` reds when it no longer
clears that, or sits far above it. `absorb` replaces the estimate with a real CI leg's numbers.

COMMANDS
--------
    shard_tests.py plan    --of N                    the assignment, and each shard's predicted time
    shard_tests.py run     --shard K --of N -s tests -t tests [--record PATH]
    shard_tests.py measure -s tests -t tests --out scripts/shard_durations.json
    shard_tests.py absorb  DIR --group windows-latest-present --os Windows
                           re-base the record on a CI leg's own per-module seconds (DIR = that
                           run's downloaded `ci-clock-*` artifacts; every OS uploads them)

`run` exits 0 green · 1 red (as unittest does) · 2 cannot run (bad K/N, empty shard, unreadable
record). `--record` writes the shard's measured unit seconds for `ci_ceiling.py --from-shards`.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import unittest
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DURATIONS = os.path.join(HERE, "shard_durations.json")

GREEN, RED, CANNOT = 0, 1, 2


# ---- the plan (pure) ------------------------------------------------------------------------------

def load_record(path: str = DURATIONS) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("modules"), dict) or not data["modules"]:
        raise ValueError("%s has no per-module durations" % path)
    return data


def cost_of(modules: Iterable[str], recorded: Dict[str, float]) -> Dict[str, float]:
    """Each module's cost: its recorded seconds, or the record's median when it is not recorded."""
    default = statistics.median(recorded.values()) if recorded else 1.0
    return {m: float(recorded.get(m, default)) for m in modules}


def assign(costs: Dict[str, float], n: int, preload: Sequence[float] = ()) -> List[List[str]]:
    """LPT: heaviest first, each to the lightest shard. `preload[i]` is seconds shard i already
    carries. Deterministic: ties on cost break by name, ties on load by shard index."""
    if n < 1:
        raise ValueError("--of must be >= 1")
    load = [float(preload[i]) if i < len(preload) else 0.0 for i in range(n)]
    shards: List[List[str]] = [[] for _ in range(n)]
    for mod in sorted(costs, key=lambda m: (-costs[m], m)):
        i = min(range(n), key=lambda j: (load[j], j))
        shards[i].append(mod)
        load[i] += costs[mod]
    return [sorted(s) for s in shards]


def plan(modules: Iterable[str], record: dict, n: int) -> Tuple[List[List[str]], List[float]]:
    """The shards and each one's predicted seconds (fixed steps included on shard 1)."""
    costs = cost_of(modules, record["modules"])
    fixed = float(sum((record.get("fixed") or {}).values()))
    shards = assign(costs, n, [fixed])
    predicted = [sum(costs[m] for m in s) + (fixed if i == 0 else 0.0)
                 for i, s in enumerate(shards)]
    return shards, predicted


def list_modules(start: str) -> List[str]:
    """The module names `discover -s start` loads at the top level (default pattern `test*.py`).
    Cheap (no import); `discovered_modules` is the authority and the test checks they agree."""
    return sorted(n[:-3] for n in os.listdir(start)
                  if n.startswith("test") and n.endswith(".py")
                  and os.path.isfile(os.path.join(start, n)))


# ---- the suite (the same loader call `python -m unittest discover` makes) -------------------------

def _discover(start: str, top: str) -> unittest.TestSuite:
    top_abs = os.path.abspath(top)
    if top_abs not in sys.path:
        sys.path.insert(0, top_abs)
    return unittest.TestLoader().discover(start, top_level_dir=top)


def _module_of(suite) -> Optional[str]:
    stack = [suite]
    while stack:
        s = stack.pop(0)
        if isinstance(s, unittest.TestSuite):
            stack[:0] = list(s)
        else:
            mod = type(s).__module__
            # A module that fails to import is a `_FailedTest` carrying the module's name.
            if mod == "unittest.loader":
                return getattr(s, "_testMethodName", None)
            return mod
    return None


def by_module(suite: unittest.TestSuite) -> Dict[str, unittest.TestSuite]:
    out: Dict[str, unittest.TestSuite] = {}
    for child in suite:
        name = _module_of(child)
        if name is None:
            continue                            # an empty module suite: nothing to run anywhere
        out.setdefault(name, unittest.TestSuite()).addTest(child)
    return out


def discovered_modules(start: str, top: str) -> List[str]:
    return sorted(by_module(_discover(start, top)))


class _TimedResult(unittest.TextTestResult):
    """unittest's own result, plus wall-clock per module (recorded for the next re-measure)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.per_module: Dict[str, float] = {}
        self._mod, self._t = None, 0.0

    def startTest(self, test):
        mod = type(test).__module__
        now = time.perf_counter()
        if mod != self._mod:
            self._flush(now)
            self._mod, self._t = mod, now
        super().startTest(test)

    def _flush(self, now):
        if self._mod is not None:
            self.per_module[self._mod] = round(
                self.per_module.get(self._mod, 0.0) + now - self._t, 3)

    def stopTestRun(self):
        self._flush(time.perf_counter())
        self._mod = None
        super().stopTestRun()


def _write_json(path: str, data: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


# ---- commands -------------------------------------------------------------------------------------

def cmd_plan(a) -> int:
    record = load_record(a.durations)
    shards, predicted = plan(list_modules(a.start), record, a.of)
    unknown = [m for s in shards for m in s if m not in record["modules"]]
    for i, (s, p) in enumerate(zip(shards, predicted), 1):
        print("shard %d/%d  %3d modules  predicted %6.0fs (%s)" % (i, a.of, len(s), p, record.get(
            "measured_on", "?")))
    print("unrecorded modules (costed at the median): %d" % len(unknown))
    return GREEN


def cmd_run(a) -> int:
    if not 1 <= a.shard <= a.of:
        print("shard_tests: --shard %d is not in 1..%d" % (a.shard, a.of), file=sys.stderr)
        return CANNOT
    try:
        record = load_record(a.durations)
    except (OSError, ValueError) as exc:
        print("shard_tests: cannot read the durations record: %s" % exc, file=sys.stderr)
        return CANNOT
    suites = by_module(_discover(a.start, a.top))
    shards, predicted = plan(suites, record, a.of)
    mine = shards[a.shard - 1]
    if not mine:
        print("shard_tests: shard %d/%d is EMPTY — fewer modules than shards?" % (a.shard, a.of),
              file=sys.stderr)
        return CANNOT
    suite = unittest.TestSuite(suites[m] for m in mine)
    print("shard %d/%d: %d of %d modules, predicted %.0fs on the recorded box"
          % (a.shard, a.of, len(mine), len(suites), predicted[a.shard - 1]), flush=True)
    t0 = time.perf_counter()
    # `python -m unittest` (unittest.main) turns warnings on ('default') unless -W was given; a
    # bare TextTestRunner does not. Same here, so a test that reads a warning sees the same thing.
    runner = unittest.TextTestRunner(resultclass=_TimedResult, verbosity=a.verbosity,
                                     warnings=None if sys.warnoptions else "default")
    result = runner.run(suite)
    seconds = time.perf_counter() - t0
    if a.record:
        _write_json(a.record, {
            "shard": a.shard, "of": a.of, "unit_seconds": round(seconds, 1),
            "predicted_seconds": round(predicted[a.shard - 1], 1),
            "tests": result.testsRun, "ok": result.wasSuccessful(),
            "per_module": result.per_module})
    slow = sorted(result.per_module.items(), key=lambda kv: -kv[1])[:10]
    print("shard %d/%d took %.0fs; slowest modules: %s"
          % (a.shard, a.of, seconds, ", ".join("%s %.0fs" % kv for kv in slow)))
    return GREEN if result.wasSuccessful() else RED


def cmd_measure(a) -> int:
    """Run every module once, in discovery order (exactly the order one unsharded leg runs them),
    timing each; write the record `plan`/`run` read. Keeps the existing `fixed` block."""
    suites = by_module(_discover(a.start, a.top))
    previous = {}
    if os.path.exists(a.out):
        try:
            previous = load_record(a.out)
        except (OSError, ValueError):
            previous = {}
    # A full measure is the whole suite (~an hour on a dev box); a restart must not lose it. Each
    # module's seconds are appended to `<out>.partial` as it finishes, and `--resume` skips the
    # modules already there. The record itself is written once, at the end, whole.
    partial = a.out + ".partial"
    durations: Dict[str, float] = {}
    if a.resume and os.path.exists(partial):
        with open(partial, encoding="utf-8") as fh:
            for line in fh:
                try:
                    name, secs = json.loads(line)
                    durations[str(name)] = float(secs)
                except (ValueError, TypeError):
                    continue                # a line cut by the restart: that module runs again
    elif os.path.exists(partial):
        os.remove(partial)
    stream = open(os.devnull, "w", encoding="utf-8")
    try:
        with open(partial, "a", encoding="utf-8", newline="\n") as log:
            for name, suite in suites.items():
                if name in durations:
                    continue
                t0 = time.perf_counter()
                unittest.TextTestRunner(stream=stream, verbosity=0,
                                        warnings=None if sys.warnoptions else "default").run(suite)
                durations[name] = round(time.perf_counter() - t0, 2)
                log.write(json.dumps([name, durations[name]]) + "\n")
                log.flush()
    finally:
        stream.close()
    durations = {m: durations[m] for m in suites if m in durations}
    _write_json(a.out, {
        "measured_on": "%s %s, python %s, %s" % (platform.system(), platform.machine(),
                                               platform.python_version(),
                                               time.strftime("%Y-%m-%d")),
        "total_seconds": round(sum(durations.values()), 1),
        "fixed": previous.get("fixed", {}),
        "ci_scale": previous.get("ci_scale", {}),
        "modules": durations})
    os.remove(partial)
    print("measured %d modules, %.0fs -> %s" % (len(durations), sum(durations.values()), a.out))
    return GREEN


def _shard_records(dirpath: str, group: str) -> Tuple[Optional[List[dict]], str]:
    """Every shard record of one CI leg (`group`, as `ci_ceiling.py --record --group` wrote it)
    under `dirpath` — one sub-directory per shard artifact, `job.json` + `unit.json` — or
    (None, why) unless it is a complete 1..N set."""
    found: Dict[int, dict] = {}
    of = set()
    for d in sorted(os.listdir(dirpath)):
        sub = os.path.join(dirpath, d)
        try:
            with open(os.path.join(sub, "job.json"), encoding="utf-8") as fh:
                job = json.load(fh)
            with open(os.path.join(sub, "unit.json"), encoding="utf-8") as fh:
                unit = json.load(fh)
        except (OSError, ValueError):
            continue
        if job.get("group") != group:
            continue
        found[int(unit["shard"])] = unit
        of.add(int(unit["of"]))
    if len(of) != 1 or set(found) != set(range(1, next(iter(of)) + 1)):
        return None, "group %r: shards %s under %s — not a complete 1..N set" % (
            group, sorted(found), dirpath)
    return [found[k] for k in sorted(found)], ""


def cmd_absorb(a) -> int:
    """Re-base the record on ONE CI leg's measured per-module seconds (download that run's
    `ci-clock-*` artifacts into a directory first). Balancing on the SLOW OS's own times is the
    point: Windows does not slow every module by the same factor (process spawns and file locks
    cost it most). The leg's OS becomes scale 1.0 and the other OSes keep their ratio to it."""
    try:
        record = load_record(a.out)
    except (OSError, ValueError) as exc:
        print("shard_tests: cannot read %s: %s" % (a.out, exc), file=sys.stderr)
        return CANNOT
    shards, why = _shard_records(a.dir, a.group)
    if shards is None:
        print("shard_tests: cannot absorb: %s" % why, file=sys.stderr)
        return CANNOT
    modules: Dict[str, float] = {}
    for u in shards:
        for m, t in (u.get("per_module") or {}).items():
            modules[m] = round(float(t), 2)
    scale = dict(record.get("ci_scale") or {})
    per_os = dict(scale.get("per_os") or {})
    base = float(per_os.get(a.os, 0) or 0)
    if base <= 0:
        print("shard_tests: the record has no ci_scale for %r" % a.os, file=sys.stderr)
        return CANNOT
    scale["per_os"] = {o: round(float(v) / base, 3) for o, v in per_os.items()}
    # Real CI clocks replace the provisional imbalance allowance (see THE CEILING FROM THE RECORD).
    scale.pop("provisional_imbalance", None)
    scale.pop("provisional_why", None)
    record.update({"measured_on": "CI leg %s (absorbed %s)" % (a.group, time.strftime("%Y-%m-%d")),
                   "total_seconds": round(sum(modules.values()), 1),
                   "ci_scale": scale, "modules": modules})
    # The once-per-leg steps' seconds move to the same unit (that OS's CI seconds).
    record["fixed"] = {k: round(float(v) * base, 1) for k, v in (record.get("fixed") or {}).items()}
    _write_json(a.out, record)
    print("absorbed %d modules from %d shards of %s -> %s"
          % (len(modules), len(shards), a.group, a.out))
    return GREEN


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="shard_tests", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ab = sub.add_parser("absorb")
    ab.add_argument("dir", help="a directory of downloaded ci-clock-* shard artifacts")
    ab.add_argument("--group", required=True, help="the leg, e.g. windows-latest-present")
    ab.add_argument("--os", required=True, help="that leg's OS key in ci_scale.per_os")
    ab.add_argument("--out", default=DURATIONS)
    for name in ("plan", "run", "measure"):
        p = sub.add_parser(name)
        p.add_argument("-s", dest="start", default="tests", help="discovery start (as unittest -s)")
        p.add_argument("-t", dest="top", default=None, help="top-level dir (as unittest -t)")
        p.add_argument("--durations", default=DURATIONS, help=argparse.SUPPRESS)
        if name in ("plan", "run"):
            p.add_argument("--of", type=int, required=True, help="number of shards")
        if name == "run":
            p.add_argument("--shard", type=int, required=True, help="this shard, 1-based")
            p.add_argument("--record", default=None, help="write this shard's measured time here")
            p.add_argument("-v", dest="verbosity", action="store_const", const=2, default=1)
        if name == "measure":
            p.add_argument("--out", default=DURATIONS)
            p.add_argument("--resume", action="store_true",
                           help="keep the modules an interrupted measure already timed")
    a = ap.parse_args(argv)
    if a.cmd == "absorb":
        return cmd_absorb(a)
    a.top = a.top or a.start
    return {"plan": cmd_plan, "run": cmd_run, "measure": cmd_measure}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
