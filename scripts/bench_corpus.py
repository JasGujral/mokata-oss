#!/usr/bin/env python3
"""`ID-BENCH-PX0-CORPUS` (0.0.27 P28) — mokata on real large repos, as medians. NOT run in CI.

    python scripts/bench_corpus.py run --corpus kubernetes --dir /tmp/corpora [--runs 5]
    python scripts/bench_corpus.py run --corpus cpython --dir /tmp/corpora --phases count,graph
    python scripts/bench_corpus.py table results/*.json            # one markdown table

`run` pins the corpus to the commit named below (a shallow fetch of THAT sha, then a check that
HEAD is it — a moved tag can never change what is measured), counts its lines per language, and
times, through the same entry points a user runs:

  index   `mokata index` (the freshness index), cold and warm, with peak RSS;
  graph   `mokata graph build --full` (cold) and `mokata graph build` (warm, nothing changed):
          wall time, peak RSS of the build's process tree, size of the index on disk;
  ide     `mokata ide --foreground` driven through its REAL page with Playwright: server ready,
          first paint, /api/health, ⌘P, workspace search (first hit, complete), go to definition,
          references, the impact ripple on a hub symbol (ring 1, done) and opening a group, opening
          the largest file, the Sessions and Review screens;
  hooks   `mokata-hook session-start | user-prompt-submit | gate-guard` with Claude Code's payloads;
  mcp     `mokata-mcp` over stdio: `query`, `recall`, `spec_check`.

Every timed step runs `--runs` times (default 5); the JSON keeps every sample, the table shows the
median and p95 (nearest rank: with 5 samples the p95 IS the slowest one). A benchmark is evidence
for a human, never a gate: nothing here asserts a number.

What it never does:
  * approve a human gate. `mokata init` is human-gated (P2), so the repo must already be set up.
    With a terminal, `run` starts `mokata init` ON that terminal and you answer it; without one it
    stops and prints the command for you to run.
  * cut a step short. The only limit is `--wait-s` (default 1800 s) per IDE step; a step that hits
    it is recorded as a FAILURE that names the limit, never as a fast or partial sample.
  * touch your home directory: the subprocesses run with HOME set to `<dir>/_bench_home` (your
    Hugging Face cache stays readable, so `recall` can rank by meaning when the model is there).

Stdlib + mokata's own deps (+ Playwright for `ide`, + the `mcp` SDK mokata already requires).
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

SCHEMA = "mokata-bench-corpus/1"
DEFAULT_RUNS = 5
DEFAULT_WAIT_S = 1800.0

# Pinned corpora: px0's own benchmark repos where they are large (doc 84 `ID-BENCH-PX0-CORPUS`).
# `sha` is what is measured; `tag` is only the human name for it. The probe symbols were read off
# each repo's code-review-graph index at that sha (`hub`: the most-called function the graph
# resolves; `define`: a name with one definition; `fuzzy`: a file name and the path it must find).
CORPORA: Dict[str, Dict[str, str]] = {
    "kubernetes": {
        "url": "https://github.com/kubernetes/kubernetes", "tag": "v1.37.2",
        "sha": "c50e5395f1ae2d4a2c05f28fdcc38c0916cc1a32",
        "hub": "HandleError", "define": "NewMainKubelet", "search": "NewController",
        "common": "err",
        "fuzzy": "kubelet.go", "fuzzy_path": "pkg/kubelet/kubelet.go",
        "write": "pkg/kubelet/kubelet.go",
    },
    "typescript": {
        "url": "https://github.com/microsoft/TypeScript", "tag": "v7.0.2",
        "sha": "1e4744d68260a7cb91b62b12edc3f6a2187faaf1",
        "hub": "getSourceFileOfNode", "define": "createTypeChecker", "search": "getTypeOfSymbol",
        "common": "type",
        "fuzzy": "checker.ts", "fuzzy_path": "src/compiler/checker.ts",
        "write": "src/compiler/checker.ts",
    },
    "cpython": {
        "url": "https://github.com/python/cpython", "tag": "v3.15.2",
        "sha": "5e58eaad7c58df58dfa8ea549c87996e01dd1077",
        "hub": "dedent", "define": "get_close_matches", "search": "get_close_matches",
        "common": "self",
        "fuzzy": "difflib.py", "fuzzy_path": "Lib/difflib.py",
        "write": "Lib/difflib.py",
    },
}

LANGUAGES: Dict[str, str] = {
    ".go": "Go", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript",
    ".cts": "TypeScript", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".py": "Python", ".pyi": "Python", ".c": "C", ".h": "C",
    ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".rs": "Rust",
    ".java": "Java", ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".md": "Markdown",
    ".rst": "reStructuredText", ".json": "JSON", ".jsonc": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".html": "HTML", ".css": "CSS", ".s": "Assembly", ".asm": "Assembly", ".proto": "Protobuf",
}


# ------------------------------------------------------------------------------------ the math
def nearest_rank(xs: Sequence[float], p: float) -> float:
    """The p-th percentile by nearest rank: the smallest sample with at least p of them at or
    below it. p95 of 5 samples is the 5th (the slowest), of 20 the 19th."""
    if not xs:
        raise ValueError("no samples")
    if not 0 < p <= 1:
        raise ValueError(f"p must be in (0, 1], got {p}")
    s = sorted(xs)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def summarize(samples: Sequence[float], unit: str = "s") -> Dict[str, Any]:
    """median / p95 / min / max over every sample, with the samples themselves (evidence)."""
    xs = [float(x) for x in samples]
    if not xs:
        raise ValueError("no samples")
    return {"n": len(xs), "unit": unit, "median": statistics.median(xs),
            "p95": nearest_rank(xs, 0.95), "min": min(xs), "max": max(xs), "samples": xs}


# ------------------------------------------------------------------------------- the corpus
def git(args: Sequence[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    from mokata.quiet_spawn import hidden
    return subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, **hidden())


def head_sha(repo: str) -> str:
    r = git(["rev-parse", "HEAD"], cwd=repo)
    if r.returncode:
        raise RuntimeError(f"{repo} is not a git checkout: {r.stderr.strip()}")
    return r.stdout.strip()


def clone_pinned(url: str, sha: str, dest: str) -> str:
    """Make `dest` a checkout of exactly `sha` (one commit deep). An existing checkout at another
    commit is REFUSED, never moved: it may be a repo someone set up and indexed on purpose."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise ValueError(f"a pin is a full 40-hex commit sha, got {sha!r}")
    if os.path.isdir(os.path.join(dest, ".git")):
        have = head_sha(dest)
        if have != sha:
            raise RuntimeError(f"{dest} is at {have}, not the pinned {sha}; "
                               "move it aside or point --dir elsewhere")
        return have
    os.makedirs(dest, exist_ok=True)
    # The corpus is BYTES: a benchmark's sizes and line counts must not depend on the machine's git
    # config (Windows' default `core.autocrlf=true` would check every text file out with CRLF).
    for args in (["init", "-q"], ["config", "core.autocrlf", "false"], ["config", "core.eol", "lf"],
                 ["remote", "add", "origin", url],
                 ["fetch", "-q", "--depth", "1", "origin", sha],
                 ["-c", "advice.detachedHead=false", "checkout", "-q", "--detach", "FETCH_HEAD"]):
        r = git(args, cwd=dest)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(args)} failed in {dest}: {r.stderr.strip()}")
    have = head_sha(dest)
    if have != sha:
        raise RuntimeError(f"fetched {have}, not the pinned {sha}")
    return have


def tracked_files(repo: str) -> List[str]:
    """Repo-relative POSIX paths git tracks (`git ls-files -z`), else every file under the root."""
    r = git(["ls-files", "-z"], cwd=repo)
    if r.returncode == 0:
        return [p for p in r.stdout.split("\0") if p]
    out = []
    for d, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), repo).replace(os.sep, "/"))
    return out


def count_file(path: str) -> Optional[int]:
    """Lines in a text file (a last line without a newline counts), None for a binary one."""
    n, last, first = 0, b"\n", True
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            if first and b"\0" in chunk[:8192]:
                return None
            first = False
            n += chunk.count(b"\n")
            last = chunk[-1:]
    return n + (0 if last == b"\n" else 1)


def count_lines(repo: str, files: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Lines per language (by extension) over the tracked text files, and the largest one."""
    by_lang: Dict[str, Dict[str, int]] = {}
    total = text_files = binary = 0
    largest: Tuple[int, int, str] = (-1, 0, "")
    for rel in (tracked_files(repo) if files is None else files):
        full = os.path.join(repo, *rel.split("/"))
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        n = count_file(full)
        if n is None:
            binary += 1
            continue
        text_files += 1
        total += n
        lang = LANGUAGES.get(os.path.splitext(rel)[1].lower(), "other")
        slot = by_lang.setdefault(lang, {"files": 0, "lines": 0})
        slot["files"] += 1
        slot["lines"] += n
        size = os.path.getsize(full)
        if (size, n) > largest[:2]:
            largest = (size, n, rel)
    ordered = dict(sorted(by_lang.items(), key=lambda kv: -kv[1]["lines"]))
    return {"total_lines": total, "text_files": text_files, "binary_files": binary,
            "by_language": ordered,
            "largest_file": {"path": largest[2], "bytes": largest[0], "lines": largest[1]}
            if largest[2] else None}


def dir_bytes(path: str) -> int:
    total = 0
    for d, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total


# ------------------------------------------------------------------------- processes & env
def bench_env(home: str) -> Dict[str, str]:
    """The subprocess env: this interpreter's bin dir first on PATH (its `mokata`, `mokata-hook`,
    `code-review-graph`), HOME redirected so nothing lands in the user's own home, and the user's
    Hugging Face cache left readable."""
    env = dict(os.environ)
    real_home = os.path.expanduser("~")
    env.setdefault("HF_HOME", os.path.join(real_home, ".cache", "huggingface"))
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") \
        else SRC
    return env


def peak_rss_unit() -> int:
    """`ru_maxrss` is bytes on macOS, KiB on Linux."""
    return 1 if sys.platform == "darwin" else 1024


def run_timed(argv: Sequence[str], cwd: str, env: Dict[str, str], stdin_text: Optional[str] = None,
              ) -> Dict[str, Any]:
    """Wall time, exit code and peak RSS (bytes; the largest of the process and the children it
    waited for — POSIX only, None on Windows) of one command."""
    from mokata.quiet_spawn import hidden
    t0 = time.perf_counter()
    p = subprocess.Popen(list(argv), cwd=cwd, env=env, text=True,
                         stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **hidden())
    rss = None
    if hasattr(os, "wait4"):
        if stdin_text is not None:
            p.stdin.write(stdin_text)
            p.stdin.close()
        out_box: List[str] = []
        reader = threading.Thread(target=lambda: out_box.append(p.stdout.read()), daemon=True)
        reader.start()
        _pid, status, ru = os.wait4(p.pid, 0)
        wall = time.perf_counter() - t0
        p.returncode = os.waitstatus_to_exitcode(status)
        reader.join()
        out = out_box[0] if out_box else ""
        rss = ru.ru_maxrss * peak_rss_unit()
    else:
        out, _ = p.communicate(stdin_text)
        wall = time.perf_counter() - t0
    return {"wall_s": wall, "rc": p.returncode, "peak_rss_bytes": rss, "out": out}


class Result:
    """What one corpus run produced: metrics (summarized samples), facts, and named failures."""

    def __init__(self, corpus: str, spec: Dict[str, str], runs: int, wait_s: float):
        self.doc: Dict[str, Any] = {
            "schema": SCHEMA, "corpus": dict(spec, name=corpus), "runs": runs,
            "limits": {"ide_step_wait_s": wait_s},
            "host": {"platform": platform.platform(), "python": platform.python_version(),
                     "cpus": os.cpu_count(), "mokata": _mokata_version(),
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "metrics": {}, "facts": {}, "failures": []}

    def metric(self, name: str, samples: Sequence[float], unit: str = "s") -> None:
        if samples:
            self.doc["metrics"][name] = summarize(samples, unit)

    def fact(self, name: str, value: Any) -> None:
        self.doc["facts"][name] = value

    def fail(self, name: str, why: str) -> None:
        self.doc["failures"].append({"step": name, "error": why})
        print(f"  FAILED {name}: {why}", flush=True)


def _mokata_version() -> str:
    try:
        from mokata import __version__
        return __version__
    except ImportError:
        return "unknown"


def validate_result(doc: Dict[str, Any]) -> List[str]:
    """Shape problems in a result document (empty = valid). The table and any later reader rely on
    exactly these keys, so a run that wrote something else is refused, not half-rendered."""
    bad = []
    if doc.get("schema") != SCHEMA:
        bad.append(f"schema is {doc.get('schema')!r}, expected {SCHEMA!r}")
    c = doc.get("corpus") or {}
    for k in ("name", "url", "sha"):
        if not c.get(k):
            bad.append(f"corpus.{k} missing")
    if c.get("sha") and not re.fullmatch(r"[0-9a-f]{40}", c["sha"]):
        bad.append("corpus.sha is not a full commit sha")
    if c.get("measured_sha") and c.get("measured_sha") != c.get("sha"):
        bad.append(f"measured {c['measured_sha']}, not the pinned {c['sha']}")
    for name, m in (doc.get("metrics") or {}).items():
        for k in ("n", "median", "p95", "samples", "unit"):
            if k not in m:
                bad.append(f"metrics.{name}.{k} missing")
        if "samples" in m and m.get("n") != len(m["samples"]):
            bad.append(f"metrics.{name}: n={m.get('n')} but {len(m['samples'])} samples")
    if not isinstance(doc.get("failures"), list):
        bad.append("failures is not a list")
    return bad


# ------------------------------------------------------------------------------ the phases
def phase_init(repo: str, env: Dict[str, str]) -> bool:
    """True when the repo is set up. Never answers the gate: a human does, on their terminal."""
    if os.path.isfile(os.path.join(repo, ".mokata", "manifest.json")):
        return True
    cmd = [sys.executable, "-m", "mokata.cli", "init", "--path", repo, "--profile", "standard"]
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("mokata is not set up in this corpus, and `mokata init` is human-gated.\n"
              "Run it yourself, answer its questions (wire code-review-graph: yes), then re-run:\n"
              f"  {' '.join(cmd)}", flush=True)
        return False
    print(f"$ {' '.join(cmd)}   (human-gated: answer it here)", flush=True)
    return subprocess.call(cmd, cwd=repo, env=env) == 0 and \
        os.path.isfile(os.path.join(repo, ".mokata", "manifest.json"))


def phase_graph(res: Result, repo: str, env: Dict[str, str], runs: int) -> None:
    base = [sys.executable, "-m", "mokata.cli", "graph", "build", "--path", repo]
    for label, extra in (("cold", ["--full"]), ("warm", [])):
        walls, rss = [], []
        for i in range(runs):
            print(f"  graph build {label} {i + 1}/{runs} …", flush=True)
            r = run_timed(base + extra, repo, env)
            if r["rc"] != 0:
                res.fail(f"graph.build.{label}", f"exit {r['rc']}: {r['out'][-600:]}")
                break
            walls.append(r["wall_s"])
            if r["peak_rss_bytes"] is not None:
                rss.append(r["peak_rss_bytes"] / 2 ** 20)
            m = re.search(r"index built: (\d+) files, (\d+) nodes, (\d+) edges", r["out"])
            if m:
                res.fact("graph.index", {"files": int(m[1]), "nodes": int(m[2]), "edges": int(m[3])})
        res.metric(f"graph.build.{label}_s", walls)
        res.metric(f"graph.build.{label}_peak_rss_mb", rss, "MiB")
    from mokata.knowledge.crg_build import INDEX_DIRNAME
    res.fact("graph.disk_mb", round(dir_bytes(os.path.join(repo, INDEX_DIRNAME)) / 2 ** 20, 1))


def phase_index(res: Result, repo: str, env: Dict[str, str], runs: int) -> None:
    """`mokata index` — the per-file freshness index impact and blast radius read. Cold drops its
    state record first (inside the corpus's own .mokata/, nothing else); warm finds nothing changed."""
    from mokata.config import Surface
    record = Surface.load(repo).state.path("knowledge_index")
    cmd = [sys.executable, "-m", "mokata.cli", "index", "--path", repo]
    for label in ("cold", "warm"):
        walls, rss = [], []
        for _ in range(runs):
            if label == "cold" and os.path.exists(record):
                os.remove(record)
            r = run_timed(cmd, repo, env)
            if r["rc"] != 0:
                res.fail(f"index.{label}", f"exit {r['rc']}: {r['out'][-600:]}")
                break
            walls.append(r["wall_s"])
            if r["peak_rss_bytes"] is not None:
                rss.append(r["peak_rss_bytes"] / 2 ** 20)
            m = re.search(r"tracking (\d+) file", r["out"])
            if m:
                res.fact("index.files", int(m[1]))
        res.metric(f"index.{label}_s", walls)
        res.metric(f"index.{label}_peak_rss_mb", rss, "MiB")


def phase_hooks(res: Result, repo: str, env: Dict[str, str], runs: int, spec: Dict[str, str]
                ) -> None:
    hook = shutil.which("mokata-hook", path=os.path.dirname(sys.executable))
    if not hook:
        res.fail("hooks", "mokata-hook is not installed beside this interpreter")
        return
    sid = "bench-corpus-0000"
    target = os.path.join(repo, *spec["write"].split("/"))
    payloads = {
        "session_start": ("session-start", {"session_id": sid, "cwd": repo, "source": "startup",
                                            "hook_event_name": "SessionStart"}),
        "user_prompt_submit": ("user-prompt-submit", {
            "session_id": sid, "cwd": repo, "hook_event_name": "UserPromptSubmit",
            "prompt": f"where is {spec['define']} defined, and what calls {spec['hub']}?"}),
        "pre_write_gate": ("gate-guard", {
            "session_id": sid, "cwd": repo, "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"}}),
    }
    for name, (sub, payload) in payloads.items():
        walls, rcs = [], []
        for _ in range(runs):
            r = run_timed([hook, sub], repo, env, stdin_text=json.dumps(payload))
            walls.append(r["wall_s"])
            rcs.append(r["rc"])
        res.metric(f"hook.{name}_s", walls)
        res.fact(f"hook.{name}.exit_codes", sorted(set(rcs)))


def phase_mcp(res: Result, repo: str, env: Dict[str, str], runs: int, spec: Dict[str, str]
              ) -> None:
    import asyncio
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as e:
        res.fail("mcp", f"the mcp SDK is not importable: {e}")
        return
    calls = {
        "query_defs": ("query", {"path": repo, "kind": "defs", "target": spec["define"]}),
        "query_callers": ("query", {"path": repo, "kind": "callers", "target": spec["hub"]}),
        "recall": ("recall", {"path": repo, "query": f"how is {spec['hub']} used"}),
        "spec_check": ("spec_check", {"path": repo, "symbols": spec["hub"],
                                      "files": spec["write"]}),
    }

    async def measure(s: Any, t0: float) -> None:
        await s.initialize()
        res.metric("mcp.server_ready_s", [time.perf_counter() - t0])
        for name, (tool, args) in calls.items():
            walls, errs, out = [], [], None
            for _ in range(runs):
                t1 = time.perf_counter()
                out = await s.call_tool(tool, args)
                walls.append(time.perf_counter() - t1)
                if out.isError:
                    errs.append(str(out.content[:1])[:300])
            res.metric(f"mcp.{name}_s", walls)
            if errs:
                res.fail(f"mcp.{name}", errs[0])
            else:
                res.fact(f"mcp.{name}.answer", _mcp_gist(out))

    async def go() -> None:
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "mokata.mcp_server"], cwd=repo, env=env)
        t0 = time.perf_counter()
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            async with stdio_client(params, errlog=quiet) as (r, w):
                async with ClientSession(r, w) as s:
                    await measure(s, t0)

    asyncio.run(go())


def _mcp_gist(out: Any) -> Any:
    try:
        d = json.loads(out.content[0].text)
    except (ValueError, IndexError, AttributeError):
        return None
    if not isinstance(d, dict):
        return None
    keep = {k: d[k] for k in ("backend", "basis", "status", "count", "enabled") if k in d}
    if isinstance(d.get("references"), list):
        keep["references"] = len(d["references"])
    return keep


# ----------------------------------------------------------------------------------- the IDE
class Ide:
    """One `mokata ide --foreground` server and one Chromium page on it."""

    def __init__(self, repo: str, env: Dict[str, str], wait_s: float):
        self.repo, self.env, self.wait_s = repo, env, wait_s
        self.proc: Optional[subprocess.Popen] = None
        self.errors: List[str] = []
        self.bad: List[str] = []

    def start(self) -> Tuple[str, float]:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        from mokata.quiet_spawn import detached
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mokata.cli", "ide", "--path", self.repo, "--no-browser",
             "--foreground", "--port", str(port)], cwd=self.repo, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            **detached())
        url = None
        while url is None:
            line = self.proc.stdout.readline()
            if not line and self.proc.poll() is not None:
                raise RuntimeError(f"`mokata ide` exited {self.proc.returncode} before its URL")
            m = re.search(r"(http://127\.0\.0\.1:%d\S*)" % port, line)
            url = m.group(1) if m else None
            if time.perf_counter() - t0 > self.wait_s:
                raise RuntimeError(f"`mokata ide` printed no URL within the {self.wait_s:.0f} s limit")
        while not _accepts(port):      # the URL is printed just before the socket listens
            if self.proc.poll() is not None:
                raise RuntimeError(f"`mokata ide` exited {self.proc.returncode} after its URL")
            if time.perf_counter() - t0 > self.wait_s:
                raise RuntimeError(f"`mokata ide` did not listen within the {self.wait_s:.0f} s limit")
            time.sleep(0.02)
        ready = time.perf_counter() - t0
        threading.Thread(target=lambda: [None for _ in self.proc.stdout], daemon=True).start()
        return url, ready

    def stop(self) -> None:
        p = self.proc
        if p is None or p.poll() is not None:
            return
        if os.name == "nt":
            from mokata.crg_resolve import _end_tree     # taskkill /T by its System32 path
            _end_tree(p)
        else:
            import signal
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(30)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        p.wait()

    def server_rss_mb(self) -> Optional[float]:
        if os.name == "nt" or self.proc is None:
            return None
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(self.proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        return round(int(r.stdout.strip()) / 1024, 1) if r.stdout.strip().isdigit() else None


def _accepts(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_js(page: Any, expr: str, arg: Any, limit: float, poll: float = 0.02) -> Optional[float]:
    """Seconds until `expr` is truthy in the page, or None at the (disclosed) limit."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit:
        if page.evaluate(expr, arg):
            return time.perf_counter() - t0
        time.sleep(poll)
    return None


GRAPH_DONE = ("(s) => MK.graph.data && MK.graph.data.symbol === s && !MK.graph.data.partial")
GRAPH_RING1 = ("(s) => MK.graph.data && MK.graph.data.symbol === s && MK.graph.cy && "
               "MK.graph.cy.nodes().length > 1")
SEARCH_FIRST = r"(q) => new RegExp(q + ' · ≥?[1-9]\\d* hit').test(document.getElementById('sr-word-h').textContent)"
SEARCH_DONE = r"(q) => { const t = document.getElementById('sr-word-h').textContent; return new RegExp(q + ' · ≥?\\d+ hit\\(s\\) in \\d+ file\\(s\\) · ').test(t); }"


def phase_ide(res: Result, repo: str, env: Dict[str, str], runs: int, spec: Dict[str, str],
              wait_s: float, largest: Optional[Dict[str, Any]]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        res.fail("ide", "Playwright is not installed in this interpreter (pip install playwright "
                        "&& python -m playwright install chromium)")
        return
    ide = Ide(repo, env, wait_s)
    pw = sync_playwright().start()      # Chromium starts with the REAL home; only the server's moves
    browser = pw.chromium.launch()
    try:
        url, ready = ide.start()
        res.metric("ide.server_ready_s", [ready])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: ide.errors.append(str(e)))
        page.on("response", lambda r: ide.bad.append(f"{r.status} {r.url}") if r.status >= 500
                else None)
        root = url.split("/pair")[0] + "/"
        _ide_steps(res, page, url, root, runs, spec, wait_s, largest)
        res.fact("ide.server_rss_mb_after", ide.server_rss_mb())
        res.fact("ide.page_errors", ide.errors[-5:])
        res.fact("ide.responses_ge_500", ide.bad[-10:])
    except Exception as e:     # noqa: BLE001 — a benchmark records what broke and carries on
        res.fail("ide", f"{type(e).__name__}: {e}")
    finally:
        try:
            browser.close()
            pw.stop()
        finally:
            ide.stop()


def _step(res: Result, name: str, runs: int, once: Callable[[int], Optional[float]],
          limit: float) -> None:
    xs = []
    for i in range(runs):
        took = once(i)
        if took is None:
            res.fail(name, f"not done within the {limit:.0f} s limit (run {i + 1})")
            break
        xs.append(took)
    res.metric(name, xs)


def _ide_steps(res: Result, page: Any, url: str, root: str, runs: int, spec: Dict[str, str],
               wait_s: float, largest: Optional[Dict[str, Any]]) -> None:
    ev = page.evaluate
    restored = "() => window.MK && MK.ui && MK.ui.restored === true"

    def load(i: int) -> Optional[float]:
        t0 = time.perf_counter()
        page.goto(url if i == 0 else root)
        took = wait_js(page, restored, None, wait_s)
        return None if took is None else time.perf_counter() - t0

    fcp: List[float] = []

    def load_and_paint(i: int) -> Optional[float]:
        took = load(i)
        v = ev("() => (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime")
        if v:
            fcp.append(v / 1000)
        return took

    _step(res, "ide.page_restored_s", runs, load_and_paint, wait_s)
    res.metric("ide.first_contentful_paint_s", fcp)

    health = []
    for _ in range(runs):
        ms, status = ev("""async () => { const t = performance.now(); const r = await fetch('/api/health');
            await r.text(); return [performance.now() - t, r.status]; }""")
        health.append(ms / 1000)
        res.fact("ide.health_status", status)
    res.metric("ide.api_health_s", health)

    # ⌘P — the palette's file list, typed name to the expected row on screen
    want = spec["fuzzy_path"]
    row = ("(p) => [...document.querySelectorAll('#qo .qo-row')].some((li) => "
           "li.textContent.replace(/\\s+/g, ' ').includes(p.split('/').pop()) && "
           "li.textContent.includes(p.split('/').slice(0, -1).join('/')))")

    def quick(_i: int) -> Optional[float]:
        ev("() => { const q = document.getElementById('qo'); if (q && !q.hidden) "
           "document.getElementById('qo-input').dispatchEvent(new KeyboardEvent('keydown', "
           "{key: 'Escape', bubbles: true})); }")
        ev("() => MK.openQuick()")
        page.fill("#qo-input", "")
        # the empty palette lists recent files — the target among them once opened — so the clock
        # stops only on rows painted after the server answered THIS query
        asked = lambda r: ("/api/quickopen" in r.url and  # noqa: E731
                           spec["fuzzy"] in (r.request.post_data or ""))
        t0 = time.perf_counter()
        with page.expect_response(asked, timeout=wait_s * 1000):
            page.fill("#qo-input", spec["fuzzy"])
        took = wait_js(page, row, want, wait_s)
        page.keyboard.press("Escape")
        return None if took is None else time.perf_counter() - t0

    _step(res, "ide.quick_open_s", runs, quick, wait_s)

    # workspace search — the first hit painted, then the answer complete (a rare name, then a
    # common word whose first page is full)
    for label, q in (("search", spec["search"]), ("search_common", spec["common"])):
        done_runs: List[float] = []

        def search(_i: int, q: str = q, done_runs: List[float] = done_runs) -> Optional[float]:
            ev("() => MK.openSearch('')")
            page.fill("#sq", "")
            ev("() => document.getElementById('sq').oninput()")
            time.sleep(0.4)
            t0 = time.perf_counter()
            page.fill("#sq", q)
            ev("() => document.getElementById('sq').oninput()")
            first = wait_js(page, SEARCH_FIRST, q, wait_s)
            if first is None:
                return None
            done = wait_js(page, SEARCH_DONE, q, wait_s)
            if done is not None:
                done_runs.append(time.perf_counter() - t0)
            return first

        _step(res, f"ide.{label}_first_hit_s", runs, search, wait_s)
        res.metric(f"ide.{label}_complete_s", done_runs)
        res.fact(f"ide.{label}_header", ev("() => document.getElementById('sr-word-h').textContent"))

    # go to definition and references, through the inspector's own calls
    sym = spec["define"]
    defs_done = ("() => { const a = MK.nav.answers.defs; if (!a) return false; "
                 "const r = a.references || []; if (r.length === 1) return MK.nav.path === r[0].path "
                 "&& MK.nav.line === r[0].line; return true; }")

    def define(_i: int) -> Optional[float]:
        ev("() => { MK.setMode('navigate'); MK.nav.sym = null; MK.nav.answers = {}; }")
        t0 = time.perf_counter()
        ev("(s) => { MK.goToDefinition(s); }", sym)
        took = wait_js(page, defs_done, None, wait_s)
        return None if took is None else time.perf_counter() - t0

    _step(res, "ide.go_to_definition_s", runs, define, wait_s)
    res.fact("ide.definition", ev("() => { const a = MK.nav.answers.defs || {}; return {backend: "
                                  "a.backend, basis: a.basis, n: (a.references || []).length, "
                                  "at: [MK.nav.path, MK.nav.line]}; }"))

    def refs(_i: int) -> Optional[float]:
        ev("() => { MK.nav.tab = 'refs'; MK.nav.sym = null; MK.nav.answers = {}; }")
        t0 = time.perf_counter()
        ev("(s) => { MK.selectSymbol(s); }", spec["hub"])
        took = wait_js(page, "() => !!MK.nav.answers.refs", None, wait_s)
        return None if took is None else time.perf_counter() - t0

    _step(res, "ide.references_s", runs, refs, wait_s)
    res.fact("ide.references", ev("() => { const a = MK.nav.answers.refs || {}; return {backend: "
                                  "a.backend, basis: a.basis, n: (a.references || []).length, "
                                  "error: a.error || null}; }"))

    # impact: the ripple on the hub symbol (ring 1 on screen, then done), then open a group
    hub = spec["hub"]
    ring1: List[float] = []
    expand: List[float] = []

    def ripple(_i: int) -> Optional[float]:
        ev("() => { if (MK.graph) MK.graph.data = null; }")
        t0 = time.perf_counter()
        ev("(s) => { MK.openGraph('ripple', s); }", hub)
        r1 = wait_js(page, GRAPH_RING1, hub, wait_s)
        if r1 is None:
            return None
        ring1.append(r1)
        done = wait_js(page, GRAPH_DONE, hub, wait_s, 0.1)
        if done is None:
            return None
        cluster = ev("() => (MK.graph.cy.nodes(\"[kind = 'cluster']\").first().data() || {}).id || null")
        if cluster:
            n0 = ev("() => MK.graph.cy.nodes().length")
            t1 = time.perf_counter()
            ev("(id) => { MK.graph.cy.getElementById(id).emit('tap'); }", cluster)
            took = wait_js(page, "(n) => MK.graph.cy.nodes().length !== n", n0, wait_s)
            if took is not None:
                expand.append(time.perf_counter() - t1)
        return time.perf_counter() - t0

    _step(res, "ide.impact_ripple_done_s", runs, ripple, wait_s)
    res.metric("ide.impact_ripple_ring1_s", ring1)
    res.metric("ide.impact_open_group_s", expand)
    res.fact("ide.impact", ev("() => MK.graph.data ? {nodes: (MK.graph.data.nodes || []).length, "
                              "more: !!MK.graph.data.more, basis: MK.graph.data.basis} : null"))

    # the largest file in the repo, opened in the viewer
    if largest and largest.get("path"):
        lp = largest["path"]
        opened = ("(p) => MK.nav.path === p && MK.nav.file && (MK.nav.file.path === p || "
                  "MK.nav.file.error)")

        def big(_i: int) -> Optional[float]:
            ev("() => { MK.nav.path = null; MK.nav.file = null; }")
            t0 = time.perf_counter()
            ev("(p) => { MK.openFile(p, 1); }", lp)
            took = wait_js(page, opened, lp, wait_s)
            return None if took is None else time.perf_counter() - t0

        _step(res, "ide.open_largest_file_s", runs, big, wait_s)
        res.fact("ide.largest_file_answer", ev(
            "() => { const f = MK.nav.file || {}; return {error: f.error || null, note: f.note || "
            "null, lines: f.lines || (f.text ? f.text.split('\\n').length : null), truncated: "
            "f.truncated || null, size: f.size || null}; }"))

    # the Sessions and Review screens, each read as the screen reads itself
    def sessions(_i: int) -> Optional[float]:
        ev("() => MK.setMode('sessions')")
        ms = ev("async () => { const t = performance.now(); await MK.loadSessionList(false); "
                "return performance.now() - t; }")
        return ms / 1000

    def review(_i: int) -> Optional[float]:
        ev("() => MK.setMode('review')")
        ms = ev("async () => { const t = performance.now(); await MK.refresh(); "
                "return performance.now() - t; }")
        return ms / 1000

    _step(res, "ide.sessions_screen_s", runs, sessions, wait_s)
    _step(res, "ide.review_screen_s", runs, review, wait_s)


# ------------------------------------------------------------------------------- the driver
PHASES = ("clone", "count", "index", "graph", "hooks", "mcp", "ide")


def run_corpus(name: str, base_dir: str, runs: int, phases: Sequence[str], wait_s: float,
               ) -> Dict[str, Any]:
    spec = CORPORA[name]
    repo = os.path.join(base_dir, name)
    res = Result(name, spec, runs, wait_s)
    env = bench_env(os.path.join(base_dir, "_bench_home"))
    print(f"== {name} @ {spec['sha'][:12]} ({spec['tag']})", flush=True)
    if "clone" in phases:
        res.doc["corpus"]["measured_sha"] = clone_pinned(spec["url"], spec["sha"], repo)
    else:
        have = head_sha(repo)
        if have != spec["sha"]:
            raise SystemExit(f"{repo} is at {have}, not the pinned {spec['sha']}")
        res.doc["corpus"]["measured_sha"] = have
    largest = None
    if "count" in phases or "ide" in phases:
        t0 = time.perf_counter()
        counted = count_lines(repo)
        counted["count_s"] = round(time.perf_counter() - t0, 1)
        res.doc["corpus"]["lines"] = counted
        largest = counted["largest_file"]
        print(f"  {counted['total_lines']:,} lines in {counted['text_files']:,} text files", flush=True)
    if not ({"index", "graph", "hooks", "mcp", "ide"} & set(phases)):
        return res.doc
    if not phase_init(repo, env):
        res.fail("init", "mokata is not set up in this corpus (human-gated; see above)")
        return res.doc
    if "index" in phases:
        phase_index(res, repo, env, runs)
    if "graph" in phases:
        phase_graph(res, repo, env, runs)
    if "hooks" in phases:
        phase_hooks(res, repo, env, runs, spec)
    if "mcp" in phases:
        phase_mcp(res, repo, env, runs, spec)
    if "ide" in phases:
        phase_ide(res, repo, env, runs, spec, wait_s, largest)
    return res.doc


def render_table(docs: Sequence[Dict[str, Any]]) -> str:
    """One markdown table: a row per metric, a column (median / p95) per corpus."""
    names = [d["corpus"]["name"] for d in docs]
    head = ["| corpus |"] + [f" {n} |" for n in names]
    lines = ["".join(head), "|---|" + "---|" * len(names)]
    lines.append("| sha | " + " | ".join(f"`{d['corpus']['sha'][:12]}` ({d['corpus'].get('tag', '')})"
                                           for d in docs) + " |")
    lines.append("| lines | " + " | ".join(
        f"{(d['corpus'].get('lines') or {}).get('total_lines', 0):,}" for d in docs) + " |")
    metrics: List[str] = []
    for d in docs:
        metrics += [m for m in d["metrics"] if m not in metrics]
    lines.append("")
    lines.append("| metric (median / p95) |" + "".join(f" {n} |" for n in names))
    lines.append("|---|" + "---|" * len(names))
    for m in metrics:
        cells = []
        for d in docs:
            s = d["metrics"].get(m)
            cells.append(f" {_fmt(s['median'], s['unit'])} / {_fmt(s['p95'], s['unit'])} (n={s['n']}) |"
                         if s else " — |")
        lines.append(f"| {m} |" + "".join(cells))
    fails = [(d["corpus"]["name"], f) for d in docs for f in d["failures"]]
    if fails:
        lines += ["", "Failures:"] + [f"- {n} · {f['step']}: {f['error']}" for n, f in fails]
    return "\n".join(lines) + "\n"


def _fmt(v: float, unit: str) -> str:
    if unit == "s":
        return f"{v * 1000:.0f} ms" if v < 1 else f"{v:.2f} s"
    return f"{v:.0f} {unit}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="measure one pinned corpus")
    r.add_argument("--corpus", required=True, choices=sorted(CORPORA))
    r.add_argument("--dir", required=True, help="where the corpora live (cloned if absent)")
    r.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    r.add_argument("--phases", default=",".join(PHASES))
    r.add_argument("--wait-s", type=float, default=DEFAULT_WAIT_S,
                   help="the one limit: per IDE step, recorded as a failure when hit")
    r.add_argument("--out", default=None, help="JSON path (default <dir>/<corpus>.bench.json)")
    t = sub.add_parser("table", help="markdown table of medians/p95 from result JSON files")
    t.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "table":
        docs = []
        for f in a.files:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            bad = validate_result(d)
            if bad:
                print(f"{f}: not a {SCHEMA} result: {'; '.join(bad)}", file=sys.stderr)
                return 2
            docs.append(d)
        sys.stdout.write(render_table(docs))
        return 0
    if a.runs < 1:
        ap.error("--runs must be at least 1")
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]
    unknown = sorted(set(phases) - set(PHASES))
    if unknown:
        ap.error(f"unknown phase(s) {unknown}; known: {', '.join(PHASES)}")
    base = os.path.abspath(a.dir)
    doc = run_corpus(a.corpus, base, a.runs, phases, a.wait_s)
    bad = validate_result(doc)
    out = a.out or os.path.join(base, f"{a.corpus}.bench.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    print(f"wrote {out}")
    sys.stdout.write(render_table([doc]))
    if bad:
        print("result shape problems: " + "; ".join(bad), file=sys.stderr)
        return 2
    return 1 if doc["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
