"""The shared stdio harness for mokata's MCP server (0.0.28 lane I).

Drives the REAL server process (`python -m mokata.mcp_server --path <repo>`, or a fault-injecting
launcher that ends in the same `server.main`) over real stdio, the way Claude Code does: raw
newline-delimited JSON-RPC, many requests in flight at once.

**The hang verdict comes from the server's own contract, never from a test-chosen timeout.** The
server promises every tool call an answer within that tool's reply budget
(`server._mcp_timeout_for(op, path)`, read LIVE from the server module: 60 s, `baseline` up to
600 s); after it, a call still working answers `running`, which is an answer. So the harness keeps a
request ledger and judges each outstanding request against that promise:

  * LOST    — a request is outstanding past ITS budget plus the acknowledgement slack, while the
              harness's `ping`s are still being answered (the server is reading and replying; it
              simply never answered this id). The slack is measured, not typed: twice the slowest
              `ping` round trip seen in the same run.
  * WEDGED  — a `ping` is outstanding past the smallest reply budget while the process is alive
              (the server stopped reading or stopped replying).
  * DIED    — the process exited with requests outstanding.

Anything shorter is just slow, and the harness keeps waiting.

Bytes and CPU are DIAGNOSTICS for the failure report only. The byte counter counts replies to
non-ping ids and server-initiated messages (the harness's own pings are excluded, or it would
advance forever). CPU is sampled per process tree: Linux `/proc`, macOS `ps -o time= -g`, Windows
`QueryInformationJobObject(JobObjectBasicAccountingInformation)` on a Job Object the harness assigns
the server to (which also ends the tree on close).

Env is passed explicitly (`env=`); the harness never patches `os.environ` for the server.

It imports nothing of lane B's process door; the Windows Job Object accounting is local ctypes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import _support  # noqa: F401  (puts src/ on the path)

from mokata import quiet_spawn  # noqa: E402

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))

# A launcher that serves the REAL server (`server.main`), with optional injected faults, each keyed
# by an environment variable so the default launch is exactly the shipped server:
#   I_GATE=<file>        the tool named by I_GATE_TOOL (default `lat_check`) blocks until <file>
#                        exists (slow work that ends on a signal the test controls, not on a sleep);
#                        I_COUNT=<file> appends one line per body run of that tool
#   I_STDIN_CHILD=<dir>  `rules` starts a child the way mokata's spawn sites do (capture_output,
#                        no stdin=) which reads one line of ITS stdin; the child writes <dir>/ready
#                        before reading and <dir>/read-<pid> with what it got
#   I_POLLUTE=1          `rules` writes to fd 1 three ways (print, os.write, a child's stdout)
#   I_STARTUP_CHILD=<dir> a startup-registration child reads one line of stdin (the H4 ordering
#                        attack): it is started from `_register_this_window`
LAUNCHER = r"""
import functools, os, subprocess, sys, time
from mokata.mcp import registry, server

def _gated(orig, gate, count):
    @functools.wraps(orig)
    def run(*a, **k):
        if count:
            with open(count, "a") as fh:
                fh.write("run\n")
        while not os.path.exists(gate):
            time.sleep(0.01)
        return orig(*a, **k)
    return run

_READER = ("import os, sys\n"
           "d = sys.argv[1]\n"
           "open(os.path.join(d, 'ready-%d' % os.getpid()), 'w').close()\n"
           "line = sys.stdin.readline()\n"
           "with open(os.path.join(d, 'read-%d' % os.getpid()), 'w') as fh:\n"
           "    fh.write(str(len(line)))\n")

def _stdin_child(orig, d):
    @functools.wraps(orig)
    def run(*a, **k):
        subprocess.run([sys.executable, "-c", _READER, d], capture_output=True)
        return orig(*a, **k)
    return run

def _noisy(orig):
    @functools.wraps(orig)
    def run(*a, **k):
        print("POLLUTE-print")
        os.write(1, b"POLLUTE-fd\n")
        subprocess.run([sys.executable, "-c", "print('POLLUTE-child')"])
        return orig(*a, **k)
    return run

gate = os.environ.get("I_GATE")
child = os.environ.get("I_STDIN_CHILD")
for spec in registry.TOOLS:
    if spec.name == os.environ.get("I_GATE_TOOL", "lat_check") and gate:
        spec.fn = _gated(spec.fn, gate, os.environ.get("I_COUNT"))
    if spec.name == "rules" and child:
        spec.fn = _stdin_child(spec.fn, child)
    if spec.name == "rules" and os.environ.get("I_POLLUTE"):
        spec.fn = _noisy(spec.fn)

startup = os.environ.get("I_STARTUP_CHILD")
if startup:
    _orig_reg = server._register_this_window
    def _reg(path, _o=_orig_reg):
        subprocess.run([sys.executable, "-c", _READER, startup], capture_output=True)
        return _o(path)
    server._register_this_window = _reg

sys.exit(server.main(sys.argv[1:]))
"""


def _server_module():
    from mokata.mcp import server as MS
    return MS


def smallest_budget() -> float:
    """The smallest reply budget the server promises (read live from the server module)."""
    return float(_server_module().MCP_SURFACE_TIMEOUT_SECONDS)


def budget_for(op: str, path: str) -> float:
    """The reply budget the server promises `op` on `path` — the server's own function."""
    return float(_server_module()._mcp_timeout_for(op, path))


class Verdict:
    """The outcome of waiting on a set of requests: `ok`, or LOST / WEDGED / DIED with the ids and a
    diagnostic report."""

    def __init__(self, kind: str, ids: Iterable[int] = (), report: str = "") -> None:
        self.kind = kind
        self.ids = sorted(ids)
        self.report = report

    @property
    def ok(self) -> bool:
        return self.kind == "ok"

    def __repr__(self) -> str:
        return f"Verdict({self.kind}, ids={self.ids})\n{self.report}"


class _Job:
    """Windows only: a Job Object the server is assigned to — accounting for the CPU diagnostic and
    an end for the whole tree on close (KILL_ON_JOB_CLOSE). Local ctypes; nothing of lane B's."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Ext(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        ext = _Ext()
        ext.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.handle, 9, ctypes.byref(ext), ctypes.sizeof(ext))
        proc = k32.OpenProcess(0x0001 | 0x0100, False, pid)     # TERMINATE | SET_QUOTA
        if proc:
            k32.AssignProcessToJobObject(self.handle, proc)
            k32.CloseHandle(proc)

    def cpu_seconds(self) -> Optional[float]:
        import ctypes

        class _Acct(ctypes.Structure):     # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64),
                        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", ctypes.c_uint32),
                        ("TotalProcesses", ctypes.c_uint32),
                        ("ActiveProcesses", ctypes.c_uint32),
                        ("TotalTerminatedProcesses", ctypes.c_uint32)]

        info = _Acct()
        ok = self._k32.QueryInformationJobObject(self.handle, 1, ctypes.byref(info),
                                                 ctypes.sizeof(info), None)
        if not ok:
            return None
        return (info.TotalUserTime + info.TotalKernelTime) / 1e7

    def close(self) -> None:
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)
            self._k32.CloseHandle(self.handle)
            self.handle = None


class McpStdio:
    """Raw JSON-RPC over the real server's stdio, with a request ledger and the contract verdict.

    `launcher=True` serves through `LAUNCHER` (fault injection by env); otherwise the shipped module
    entry point. `drain_stderr=False` never reads stderr (a non-draining client)."""

    def __init__(self, repo: str, env: Optional[Dict[str, str]] = None, launcher: bool = False,
                 drain_stderr: bool = True, initialize: bool = True,
                 session_id: str = "run-i1-harness") -> None:
        self.repo = repo
        e = dict(os.environ)
        e.update({"PYTHONPATH": SRC, "MOKATA_SESSION_ID": session_id,
                  "PYTHONDONTWRITEBYTECODE": "1"})
        e.update(env or {})
        e = self._spawn_env(e)
        argv = ([sys.executable, "-c", LAUNCHER] if launcher
                else [sys.executable, "-m", "mokata.mcp_server"]) + ["--path", repo]
        kw: Dict[str, Any] = dict(quiet_spawn.hidden())
        if os.name != "nt":
            kw["start_new_session"] = True              # its own group: the tree ends with it
        self.p = subprocess.Popen(argv, cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=e, **kw)
        self._job = _Job(self.p.pid) if os.name == "nt" else None
        self.cv = threading.Condition()          # an RLock: `start` may run inside `settle`
        self.eof = False
        import queue
        self._outq: "queue.Queue[Optional[bytes]]" = queue.Queue()
        threading.Thread(target=self._write, daemon=True).start()
        self.nid = 0
        self.ledger: Dict[int, Dict[str, Any]] = {}     # id -> {method, tool, sent, budget}
        self.replies: Dict[int, Any] = {}               # id -> (msg, at)
        self.ping_ids: set = set()
        self.max_ping_rtt = 0.0
        self.garbage: List[bytes] = []
        self.server_messages: List[Dict[str, Any]] = []
        self.reply_bytes = 0                            # diagnostic: non-ping replies only
        self.stderr_chunks: List[bytes] = []
        self.order: List[int] = []                      # reply arrival order (ids)
        threading.Thread(target=self._read, daemon=True).start()
        if drain_stderr:
            threading.Thread(target=self._err, daemon=True).start()
        if initialize:
            msg = self.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "mokata-i1-harness", "version": "0"}})
            if msg is None or "result" not in msg:
                raise AssertionError(f"initialize was not answered: {msg!r}\n{self.diagnostics()}")
            self.notify("notifications/initialized")

    def _spawn_env(self, env: Dict[str, str]) -> Dict[str, str]:
        """Hook: a subclass may edit the server's (explicit) env before the spawn."""
        return env

    # ------------------------------------------------------------------ the wire
    def _read(self) -> None:
        for line in self.p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                with self.cv:
                    self.garbage.append(line[:200])
                continue
            now = time.perf_counter()
            with self.cv:
                rid = msg.get("id") if isinstance(msg, dict) else None
                if rid in self.ledger and rid not in self.replies:
                    self.replies[rid] = (msg, now)
                    self.order.append(rid)
                    if rid in self.ping_ids:
                        self.max_ping_rtt = max(self.max_ping_rtt, now - self.ledger[rid]["sent"])
                    else:
                        self.reply_bytes += len(line)
                else:
                    self.server_messages.append(msg)
                    self.reply_bytes += len(line)
                self.cv.notify_all()
        with self.cv:
            self.eof = True
            self.cv.notify_all()

    def _err(self) -> None:
        for chunk in iter(lambda: self.p.stderr.read1(65536), b""):
            with self.cv:
                self.stderr_chunks.append(chunk)

    def stderr_text(self) -> str:
        with self.cv:
            return b"".join(self.stderr_chunks).decode("utf-8", "replace")

    def send_raw(self, data: bytes) -> None:
        """Queued: a server that stopped reading must never block the harness's own verdict."""
        self._outq.put(data)

    def _write(self) -> None:
        while True:
            data = self._outq.get()
            if data is None:
                break
            try:
                self.p.stdin.write(data)
                self.p.stdin.flush()
            except (OSError, ValueError):
                break
        try:
            self.p.stdin.close()
        except (OSError, ValueError):
            pass

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw((json.dumps(msg) + "\n").encode("utf-8"))

    def start(self, method: str, params: Optional[Dict[str, Any]] = None) -> int:
        with self.cv:
            self.nid += 1
            rid = self.nid
            tool = (params or {}).get("name") if method == "tools/call" else None
            budget = budget_for(tool, self.repo) if tool else smallest_budget()
            self.ledger[rid] = {"method": method, "tool": tool, "sent": time.perf_counter(),
                                "budget": budget}
            if method == "ping":
                self.ping_ids.add(rid)
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw((json.dumps(msg) + "\n").encode("utf-8"))
        return rid

    def tool_start(self, name: str, args: Optional[Dict[str, Any]] = None) -> int:
        return self.start("tools/call", {"name": name, "arguments": dict(args or {})})

    # ------------------------------------------------------------------ the verdict
    def settle(self, ids: Iterable[int]) -> Verdict:
        """Wait until every id is answered, or the contract says one never will be (LOST / WEDGED /
        DIED, see the module note). Pings are sent one at a time; between them the harness waits
        for progress (any reply), so a slow-but-healthy call is simply waited for."""
        want = list(ids)
        ping: Optional[int] = None
        with self.cv:
            while True:
                missing = [i for i in want if i not in self.replies]
                if not missing:
                    return Verdict("ok")
                if self.eof:
                    return Verdict("DIED", missing, self.diagnostics())
                if ping is None:
                    ping = self.start("ping")
                elif ping in self.replies:
                    ping_sent = self.ledger[ping]["sent"]
                    slack = 2 * self.max_ping_rtt
                    lost = [i for i in missing
                            if ping_sent > self.ledger[i]["sent"] + self.ledger[i]["budget"] + slack]
                    if lost:
                        return Verdict("LOST", lost, self.diagnostics())
                    ping = None
                    self.cv.wait(0.25)
                else:
                    if (time.perf_counter() - self.ledger[ping]["sent"] > smallest_budget()
                            and self.p.poll() is None):
                        return Verdict("WEDGED", missing + [ping], self.diagnostics())
                    self.cv.wait(0.25)

    def reply(self, rid: int) -> Any:
        with self.cv:
            got = self.replies.get(rid)
        return got[0] if got else None

    def latency(self, rid: int) -> Optional[float]:
        with self.cv:
            got = self.replies.get(rid)
            return (got[1] - self.ledger[rid]["sent"]) if got else None

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        rid = self.start(method, params)
        v = self.settle([rid])
        if not v.ok:
            raise AssertionError(f"{method} was not answered: {v!r}")
        return self.reply(rid)

    def tool(self, name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """One tool call, settled by the contract; returns the decoded payload."""
        rid = self.tool_start(name, args)
        v = self.settle([rid])
        if not v.ok:
            raise AssertionError(f"tool {name} was not answered: {v!r}")
        return payload(self.reply(rid))

    def tools_list(self) -> List[Dict[str, Any]]:
        return self.request("tools/list")["result"]["tools"]

    # ------------------------------------------------------------------ diagnostics
    def cpu_seconds(self) -> Optional[float]:
        try:
            if self._job is not None:
                return self._job.cpu_seconds()
            if sys.platform.startswith("linux"):
                total = 0
                tck = os.sysconf("SC_CLK_TCK")
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        with open(f"/proc/{pid}/stat", "rb") as fh:
                            fields = fh.read().rsplit(b")", 1)[1].split()
                    except OSError:
                        continue
                    if int(fields[2]) == self.p.pid:       # pgrp
                        total += int(fields[11]) + int(fields[12])
                return total / tck
            out = subprocess.run(["ps", "-o", "time=", "-g", str(self.p.pid)],
                                 capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                 **quiet_spawn.hidden()).stdout
            secs = 0.0
            for ln in out.split():
                parts = ln.replace("-", ":").split(":")
                v = 0.0
                for part in parts:
                    v = v * 60 + float(part)
                secs += v
            return secs
        except (OSError, ValueError):
            return None

    def diagnostics(self) -> str:
        with self.cv:
            outstanding = {i: dict(self.ledger[i], age=round(time.perf_counter()
                                                             - self.ledger[i]["sent"], 2))
                           for i in self.ledger if i not in self.replies}
            nbytes, garbage = self.reply_bytes, list(self.garbage)
        return (f"alive={self.p.poll() is None} reply_bytes(non-ping)={nbytes} "
                f"cpu_s={self.cpu_seconds()} max_ping_rtt={self.max_ping_rtt:.3f}\n"
                f"outstanding={json.dumps(outstanding, default=str)[:2000]}\n"
                f"garbage={garbage[:5]}\nstderr tail={self.stderr_text()[-1500:]}")

    # ------------------------------------------------------------------ teardown
    def close(self) -> None:
        self._outq.put(None)                        # EOF on the protocol input, after queued sends
        try:
            self.p.wait(30)
        except subprocess.TimeoutExpired:
            pass
        # end the whole tree (a stuck grandchild must not outlive the test or hold a pipe)
        if self._job is not None:
            self._job.close()
        else:
            try:
                quiet_spawn.signal_group(self.p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait()
        for stream in (self.p.stdout, self.p.stderr):
            try:
                stream.close()
            except OSError:
                pass


def payload(msg: Any) -> Any:
    """The tool's structured result out of a `tools/call` reply."""
    if msg is None:
        return None
    if "error" in msg:
        return {"_rpc_error": msg["error"]}
    res = msg.get("result") or {}
    sc = res.get("structuredContent")
    if isinstance(sc, dict):
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    for c in res.get("content", []):
        if c.get("type") == "text":
            try:
                return json.loads(c["text"])
            except ValueError:
                return {"_text": c["text"], "_isError": res.get("isError")}
    return {"_raw": msg}


def make_repo(prefix: str = "i1-", init: bool = True, files: int = 6) -> str:
    """A small repo with a few python files; `mokata init`-ed unless `init=False`."""
    import tempfile
    d = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(d, "src"))
    for i in range(files):
        with open(os.path.join(d, "src", f"m{i}.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"def f{i}(x):\n    return x + {i}\n")
    if init:
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    return d


# ---------------------------------------------------------------------- schema-built calls
def _resolve(prop: Dict[str, Any], defs: Dict[str, Any]) -> Dict[str, Any]:
    ref = prop.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return defs.get(ref.split("/")[-1], {})
    return prop


def sample_value(name: str, prop: Dict[str, Any], defs: Optional[Dict[str, Any]] = None) -> Any:
    """A schema-valid value for one property: the first enum member, else a typed sample; an array
    gets ONE schema-built item, an object its required keys (resolving `$defs` references)."""
    defs = defs or {}
    prop = _resolve(prop, defs)
    if "enum" in prop:
        return prop["enum"][0]
    if "const" in prop:
        return prop["const"]
    alts = prop.get("anyOf") or []
    if alts:
        real = [a for a in alts if _resolve(a, defs).get("type") != "null"]
        return sample_value(name, real[0], defs) if real else None
    t = prop.get("type")
    if t == "boolean":
        return False
    if t == "integer":
        return int(prop.get("minimum", 1))
    if t == "number":
        return float(prop.get("minimum", 1))
    if t == "array":
        items = _resolve(prop.get("items") or {}, defs)
        if items.get("type") == "object" or "properties" in items:
            return [sample_value(name, items, defs)]
        return [sample_value(name, items, defs)] if items.get("type") or "enum" in items else []
    if t == "object":
        props = prop.get("properties") or {}
        return {k: sample_value(k, v, defs) for k, v in props.items()
                if k in (prop.get("required") or [])}
    return f"sample-{name}"


#: (tool, field) refusals that are about the WORLD, not the shape: the schema says "a file path" or
#: "a proposal subject" and a schema-built sample names a file or subject that does not exist; a
#: spec's criteria/test/task ids must agree with each other, which no JSON schema can say. Each is
#: a refusal the schema could not have prevented, and none carries the front door's `accepted`.
WORLD_REFUSALS = {
    ("apply_proposal", "subject"): "names no pending proposal in a fresh repo",
    ("consolidate", "session"): "names no session in a fresh repo",
    ("ide_open", "file"): "the sample file does not exist",
    ("import_stack", "file"): "the sample file does not exist",
    ("memory_import", "file"): "the sample file does not exist",
    ("vault_push", "file"): "the sample file does not exist",
    ("vault_pull", "name"): "the sample names no vault entry",
    ("stacks_install", "name"): "the sample name is not in the stack catalog",
    ("worktree_merge", "target"): "the sample names no worktree",
    ("spec_emit", "spec"): "the sample's ac_ids do not name its criteria (cross-field)",
    ("spec_amend", "spec"): "the sample's ac_ids do not name its criteria (cross-field)",
}


def world_refusal(name: str, out: Any) -> bool:
    """True when `out` is a refusal about the world (above), not about the call's shape."""
    return (isinstance(out, dict) and out.get("reason_code") == "invalid_input"
            and "accepted" not in out and (name, out.get("field")) in WORLD_REFUSALS)


def _conditionally_required() -> Dict[str, Any]:
    """Fields a tool needs on a FIRST call that JSON Schema cannot mark required, because a second
    form of the call does without them. `spec_emit` (0.0.28 round-3 ruling): the four spec fields
    are required unless `proposal_id` alone redeems an approved proposal — the description says so,
    and this is the first-call form. Read from the product, never retyped here."""
    from mokata.mcp.tools_spec import EMIT_REQUIRED
    return {"spec_emit": EMIT_REQUIRED}


def required_call(tool: Dict[str, Any], repo: str) -> Dict[str, Any]:
    """The smallest valid FIRST call: `path`, every required property, and any property the tool
    requires on a first call that the schema cannot express (`_conditionally_required`)."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    defs = schema.get("$defs") or {}
    args: Dict[str, Any] = {"path": repo} if "path" in props else {}
    for name in list(schema.get("required") or []) + list(
            _conditionally_required().get(tool.get("name"), ())):
        if name not in args:
            args[name] = sample_value(name, props.get(name, {}), defs)
    return args


def stringified(args: Dict[str, Any]) -> Dict[str, Any]:
    """The same call with every array/object argument sent as a JSON STRING (what models do)."""
    return {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in args.items()}
