#!/usr/bin/env python3
"""Medians for `mokata ide` ⌘P on a generated 100k-file tree — 0.0.23 C4. NOT run in CI.

    python scripts/bench_ide_fuzzy.py [--files 100000] [--runs 30] [--keep DIR]

It writes a synthetic source tree (empty files under nested package dirs, plus a `venv/` the walker
must prune) into a temp dir, then measures, with the SAME functions the IDE calls:

  * walk     `nav.walk_files` — the explorer's / ⌘P's file list (the graph's own pruning walk)
  * fuzzy    `nav.fuzzy` over the whole list, for a fixed set of typical queries — p50 / p95 / max
             per query, for whichever matcher is importable here (rapidfuzz, else the floor) and,
             with --floor, the pure-Python floor forced

The numbers are printed, never asserted: a benchmark is evidence for a human, not a gate.
"""
from __future__ import annotations

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

QUERIES = ("base", "rtc", "test_cli", "nav", "srcmodpkg", "util/io", "zz9", "handler.py",
           "conf", "a", "ProjectEnv", "mdl7")

WORDS = ("core", "util", "net", "io", "cli", "conf", "engine", "govern", "memory", "graph",
         "handler", "parse", "model", "view", "store", "cache", "auth", "api", "base", "env")


def make_tree(root: str, n: int) -> int:
    """`n` files: pkgNN/modNN/<word>_<i>.py, a sprinkling of tests/, and a venv to be pruned."""
    made = 0
    per_dir = 50
    i = 0
    while made < n:
        d = os.path.join(root, "src", f"pkg{i // 2000:02d}", f"mod{(i // per_dir) % 40:02d}",
                         f"sub{i // per_dir:04d}")
        os.makedirs(d, exist_ok=True)
        for k in range(per_dir):
            if made >= n:
                break
            w = WORDS[(i + k) % len(WORDS)]
            name = f"test_{w}_{i + k}.py" if (i + k) % 9 == 0 else f"{w}_{i + k}.py"
            with open(os.path.join(d, name), "wb"):
                pass
            made += 1
        i += per_dir
    venv = os.path.join(root, "venv")
    os.makedirs(os.path.join(venv, "lib"), exist_ok=True)
    with open(os.path.join(venv, "pyvenv.cfg"), "w", encoding="utf-8") as fh:
        fh.write("home = /usr\n")
    for k in range(500):
        with open(os.path.join(venv, "lib", f"site_{k}.py"), "wb"):
            pass
    return made


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def bench(files, runs, force_floor):
    from mokata.ide import nav
    out = {}
    ctx = mock.patch.dict(sys.modules, {"rapidfuzz": None}) if force_floor else _null()
    with ctx:
        for q in QUERIES:
            times = []
            for _ in range(runs):
                t0 = time.perf_counter()
                rows, matcher = nav.fuzzy(q, files)
                times.append((time.perf_counter() - t0) * 1000)
            out[q] = (statistics.median(times), pct(times, .95), max(times), matcher, len(rows))
    return out


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=100000)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--floor", action="store_true", help="also force the pure-Python floor")
    ap.add_argument("--keep", default=None, help="build the tree here and keep it")
    a = ap.parse_args(argv)
    from mokata.ide import nav
    root = a.keep or tempfile.mkdtemp(prefix="mokata-bench-")
    try:
        t0 = time.perf_counter()
        made = make_tree(root, a.files)
        print(f"tree: {made} files (+500 in a venv/ the walker must prune) in "
              f"{time.perf_counter() - t0:.1f}s at {root}")
        walks = []
        for _ in range(5):
            t0 = time.perf_counter()
            files = nav.walk_files(root)
            walks.append((time.perf_counter() - t0) * 1000)
        assert not [f for f in files if f.startswith("venv/")], "the venv was not pruned"
        print(f"walk_files: {len(files)} files · p50 {statistics.median(walks):.0f} ms "
              f"(min {min(walks):.0f}, max {max(walks):.0f}) over 5 walks")
        modes = [False] + ([True] if a.floor and nav.matcher_name() != "python-floor" else [])
        for force in modes:
            res = bench(files, a.runs, force)
            matcher = next(iter(res.values()))[3]
            print(f"\nfuzzy ({matcher}) over {len(files)} paths, {a.runs} runs per query:")
            print(f"  {'query':<12} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8}  rows")
            for q, (p50, p95, mx, _m, n) in res.items():
                print(f"  {q:<12} {p50:8.1f} {p95:8.1f} {mx:8.1f}  {n}")
            allp50 = statistics.median([v[0] for v in res.values()])
            print(f"  median of per-query p50: {allp50:.1f} ms")
    finally:
        if not a.keep:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
