"""G4 — the `mokata-mcp` harness-certification rig (Cursor / Codex CLI / Gemini CLI / Copilot CLI).

The backlog row is one clause ("certify mokata-mcp with Cursor/Codex CLI/Gemini CLI/Copilot CLI
(memory+gates+query)"). This module turns it into criteria a machine and an owner can both grade,
and keeps the two kinds of evidence apart:

  * the CONTRACT SUITE (`run_contract`) drives the criteria against `mokata-mcp` over a real stdio
    MCP session from a minimal client in this module — no third-party client is needed. It proves
    the SERVER half: what any conforming client would see.
  * the MANUAL CHECKLIST (`checklist`) is what an owner runs with the REAL client, recording each
    criterion into the matrix file (`record_result`). Only those recorded rows speak for a client.

⛔ NOTHING HERE CLAIMS CERTIFICATION. `certified()` is True only for a client whose matrix row holds
a recorded PASS — with client version, date and recorder — for EVERY criterion. The shipped matrix
holds none, so no client is certified, and a green contract suite does not change that: it proves
the server, never the client.

THE CRITERIA (ids are stable; the matrix file and the doc key on them)
  C1 config     `mokata setup <harness>` writes an MCP config entry for server `mokata` in the
                client's own config file. A harness whose config setup does NOT write (codex,
                copilot today) is MANUAL — never PASS — until setup writes it.
  C2 starts     the server launched from that entry completes MCP `initialize` over stdio.
  C3 tools      `tools/list` returns the server's tool set, including the probe tools, and it
                equals the tool set this mokata registers (a mismatch is version skew).
  C4 read       a read tool (`status`) answers without error, reporting this mokata's version.
  C5 gate       a gated write (`remember`) REFUSES to commit without a human-minted approval: no
                proposal id -> proposed, nothing written; a model-typed `approve=true` -> nothing
                written; a forged proposal id -> refused.
  C6 memory     memory round-trip: propose -> a human mints the approval OUT OF BAND
                (`mokata approve <id>`) -> redeem commits -> `recall` returns the value -> the same
                approval cannot be redeemed twice.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import MokataError

# --------------------------------------------------------------------------- the criteria

@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    statement: str


CRITERIA = (
    Criterion("C1", "config", "`mokata setup <harness>` writes an MCP config entry for server "
              "`mokata` into the client's own config file"),
    Criterion("C2", "starts", "the server launched from that entry completes MCP `initialize` "
              "over stdio"),
    Criterion("C3", "tools", "`tools/list` returns the server's tool set, including the probe "
              "tools, equal to the tool set this mokata registers"),
    Criterion("C4", "read", "a read tool (`status`) answers without error with this mokata's "
              "version"),
    Criterion("C5", "gate", "a gated write (`remember`) refuses to commit without a human-minted "
              "approval (no id / model-typed approve=true / forged id)"),
    Criterion("C6", "memory", "memory round-trip: propose -> human mints `mokata approve <id>` -> "
              "redeem commits -> `recall` returns it -> the approval cannot be reused"),
)
CRITERION_IDS = tuple(c.id for c in CRITERIA)

# Result states. MANUAL and BLOCKED are NOT passes and never count toward certification.
PASS, FAIL, MANUAL, BLOCKED, UNTESTED = "pass", "fail", "manual", "blocked", "untested"
RESULT_STATES = (PASS, FAIL, MANUAL, BLOCKED, UNTESTED)

# The tools the criteria exercise — C3 requires them in the listing.
PROBE_TOOLS = ("status", "remember", "recall")

# --------------------------------------------------------------------------- the clients

@dataclass(frozen=True)
class CertClient:
    key: str                 # matrix key
    display: str
    harness: str             # the `mokata setup` target
    config_where: str        # where the client reads MCP servers from
    manual_config: str       # what the owner writes when setup does not (empty when it does)
    verify_listing: str      # how the owner sees the server's tools in the real client


CERT_CLIENTS: Dict[str, CertClient] = {
    "cursor": CertClient(
        "cursor", "Cursor", "cursor", "<project>/.cursor/mcp.json (`mcpServers`)", "",
        "Cursor Settings -> MCP: the `mokata` server is enabled and lists its tools"),
    "codex": CertClient(
        "codex", "Codex CLI", "codex", "~/.codex/config.toml (`[mcp_servers.<name>]`)",
        '[mcp_servers.mokata]\ncommand = "mokata-mcp"\nargs = []',
        "run `codex`, then `/mcp`: `mokata` is listed with its tools"),
    "gemini": CertClient(
        "gemini", "Gemini CLI", "gemini", "<project>/.gemini/settings.json (`mcpServers`)", "",
        "run `gemini`, then `/mcp`: `mokata` is CONNECTED and lists its tools"),
    "copilot": CertClient(
        "copilot", "Copilot CLI", "copilot", "~/.copilot/mcp-config.json (`mcpServers`)",
        '{"mcpServers": {"mokata": {"type": "local", "command": "mokata-mcp", '
        '"args": [], "tools": ["*"]}}}',
        "run `copilot`, then `/mcp show`: `mokata` is listed with its tools"),
}

# --------------------------------------------------------------------------- C1: the config

CONFIG_WRITTEN, CONFIG_MANUAL, CONFIG_MISSING, CONFIG_UNREADABLE = (
    "written", "manual", "missing", "unreadable")


@dataclass
class ConfigProbe:
    state: str
    path: Optional[str]
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    detail: str = ""


def setup_config_entry(client: str, root: str, scope: str = "project",
                       home: Optional[str] = None) -> ConfigProbe:
    """Read back the MCP entry `mokata setup <harness>` wrote for `client`, from the SAME path
    setup resolves (`resolve_targets`), so the rig can never grade a file setup does not write."""
    from .harness_paths import MCP_SERVER_NAME
    from .harness_setup import resolve_targets
    c = CERT_CLIENTS[client]
    mcp_path = resolve_targets(scope, root, home, c.harness).mcp_path
    if mcp_path is None:
        return ConfigProbe(CONFIG_MANUAL, None, detail=(
            f"`mokata setup {c.harness}` writes no MCP config for {c.display}; the owner registers "
            f"it by hand in {c.config_where}"))
    if not mcp_path.is_file():
        return ConfigProbe(CONFIG_MISSING, str(mcp_path), detail="config file absent — run "
                           f"`mokata setup {c.harness}` first")
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ConfigProbe(CONFIG_UNREADABLE, str(mcp_path), detail=f"cannot parse: {exc}")
    entry = (data.get("mcpServers") or {}).get(MCP_SERVER_NAME) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
        return ConfigProbe(CONFIG_MISSING, str(mcp_path),
                           detail=f"no `mcpServers.{MCP_SERVER_NAME}` entry with a command")
    args = entry.get("args") or []
    return ConfigProbe(CONFIG_WRITTEN, str(mcp_path), entry["command"],
                       [str(a) for a in args] if isinstance(args, list) else [],
                       detail=f"mcpServers.{MCP_SERVER_NAME} -> {entry['command']}")


# --------------------------------------------------------------------------- the stdio client

class McpClientError(MokataError):
    pass


class StdioMcpClient:
    """A minimal MCP client over stdio: newline-delimited JSON-RPC 2.0, as the spec's stdio
    transport defines it. Enough to drive the criteria — not a general client. A reader thread
    plus a queue gives every request a timeout on every platform (no `select` on Windows pipes)."""

    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, argv: Sequence[str], cwd: str, env: Optional[Dict[str, str]] = None,
                 timeout: float = 60.0) -> None:
        self.argv, self.cwd, self.env, self.timeout = list(argv), cwd, env, timeout
        self.proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._id = 0
        self.server_info: Dict[str, Any] = {}

    def start(self) -> Dict[str, Any]:
        self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        res = self.request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "mokata-cert", "version": "0"}})
        self.server_info = res
        self.notify("notifications/initialized")
        return res

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self._q.put(line)
        self._q.put(None)                                   # EOF: the server went away

    def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        self.stderr_tail = b""
        for line in self.proc.stderr:                       # never let a full pipe stall it
            self.stderr_tail = (self.stderr_tail + line)[-4000:]

    def _send(self, msg: Dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise McpClientError("client not started")
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpClientError(f"server stdin closed: {exc}") from exc

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._id += 1
        rid = self._id
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        while True:
            try:
                line = self._q.get(timeout=self.timeout)
            except queue.Empty:
                raise McpClientError(f"no answer to {method} within {self.timeout:.0f}s")
            if line is None:
                tail = getattr(self, "stderr_tail", b"").decode("utf-8", "replace")[-600:]
                raise McpClientError(f"server exited before answering {method}: {tail}")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line.decode("utf-8"))
            except ValueError:
                raise McpClientError(f"non-JSON on the server's stdout: {line[:200]!r}")
            if resp.get("id") != rid or "method" in resp:
                continue                                    # a notification / server request
            if "error" in resp:
                raise McpClientError(f"{method} -> JSON-RPC error {resp['error']}")
            return resp.get("result") or {}

    def list_tools(self) -> List[str]:
        names: List[str] = []
        cursor: Optional[str] = None
        for _ in range(50):                                 # bounded pagination
            res = self.request("tools/list", {"cursor": cursor} if cursor else None)
            names += [t.get("name") for t in res.get("tools", [])]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return names

    def call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool; return its JSON payload. A tool-level error raises."""
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        if res.get("isError"):
            raise McpClientError(f"tool {name} returned isError: {_text(res)[:300]}")
        sc = res.get("structuredContent")
        if isinstance(sc, dict) and isinstance(sc.get("result"), dict):
            return sc["result"]
        try:
            return json.loads(_text(res))
        except ValueError:
            raise McpClientError(f"tool {name} answered non-JSON: {_text(res)[:200]}")

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait(timeout=10)
        finally:
            for s in (self.proc.stdout, self.proc.stderr):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass


def _text(res: Dict[str, Any]) -> str:
    return "".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict))


# --------------------------------------------------------------------------- the contract run

@dataclass
class CriterionResult:
    id: str
    status: str
    detail: str


@dataclass
class CertReport:
    client: str
    launched_via: List[str]
    results: List[CriterionResult]

    def status(self, cid: str) -> str:
        return next(r.status for r in self.results if r.id == cid)

    @property
    def server_contract_ok(self) -> bool:
        """C2..C6 all PASS — the server half holds. Says nothing about the real client."""
        return all(r.status == PASS for r in self.results if r.id != "C1")

    def render(self) -> str:
        lines = [f"{CERT_CLIENTS[self.client].display} — local contract run "
                 f"(launched: {' '.join(self.launched_via)})"]
        for r in self.results:
            lines.append(f"  {r.id} {r.status.upper():8} {r.detail}")
        lines.append("  NOTE: a local contract run proves the SERVER half only. Certification "
                     "needs the owner's real-client checklist recorded in the matrix.")
        return "\n".join(lines)


def default_approver(env: Optional[Dict[str, str]] = None) -> Callable[[str, str], bool]:
    """The out-of-band human mint, as a terminal would do it: `mokata approve <id> --yes` in a
    SEPARATE process the MCP session does not drive. In the contract suite this stands in for the
    human — it shows the server needs a mint it cannot make itself, not that a human was there."""
    def _approve(root: str, proposal_id: str) -> bool:
        cp = subprocess.run([sys.executable, "-m", "mokata", "approve", proposal_id, "--yes",
                             "--path", root], cwd=root, env=env, capture_output=True,
                            text=True, timeout=120)
        return cp.returncode == 0
    return _approve


def _launch_argv(probe: ConfigProbe) -> Optional[List[str]]:
    if probe.state != CONFIG_WRITTEN or not probe.command:
        return None
    exe = shutil.which(probe.command) or (probe.command if os.path.isabs(probe.command) else None)
    return [exe] + probe.args if exe else None


def run_contract(client: str, root: str, *, launch: Optional[Sequence[str]] = None,
                 env: Optional[Dict[str, str]] = None,
                 approve: Optional[Callable[[str, str], bool]] = None,
                 expected_tools: Optional[Sequence[str]] = None,
                 expected_version: Optional[str] = None,
                 subject: str = "mokata-cert-probe", timeout: float = 60.0) -> CertReport:
    """Run C1..C6 for `client` against `root` (which `mokata setup <harness>` has wired).

    `launch` overrides the argv the config entry names (a test pins this tree's interpreter
    rather than whatever `mokata-mcp` is first on PATH); the report records what was launched.
    `expected_tools` / `expected_version` default to THIS mokata's registry and version, so a
    server from another install fails C3/C4 as skew instead of passing as someone else."""
    if client not in CERT_CLIENTS:
        raise ValueError(f"unknown client '{client}'; choose one of {tuple(CERT_CLIENTS)}")
    if expected_tools is None:
        from .mcp.registry import tool_names
        from .mcp import server as _srv  # noqa: F401  — registers every tool module
        expected_tools = tool_names()
    if expected_version is None:
        from . import __version__ as expected_version
    approve = approve or default_approver(env)
    root = str(Path(root).resolve())
    results: List[CriterionResult] = []

    probe = setup_config_entry(client, root)
    c1 = {CONFIG_WRITTEN: PASS, CONFIG_MANUAL: MANUAL}.get(probe.state, FAIL)
    results.append(CriterionResult("C1", c1, probe.detail))

    argv = list(launch) if launch else _launch_argv(probe)
    if not argv:
        why = ("no config entry to launch from" if probe.state != CONFIG_WRITTEN
               else f"`{probe.command}` is not on PATH")
        for cid in CRITERION_IDS[1:]:
            results.append(CriterionResult(cid, BLOCKED, why))
        return CertReport(client, [], results)

    cl = StdioMcpClient(argv, root, env, timeout)
    try:
        try:
            info = cl.start()
            results.append(CriterionResult("C2", PASS, "initialize answered; server "
                                            f"{info.get('serverInfo', {}).get('name', '?')}"))
        except (McpClientError, OSError) as exc:
            results.append(CriterionResult("C2", FAIL, str(exc)))
            for cid in CRITERION_IDS[2:]:
                results.append(CriterionResult(cid, BLOCKED, "server did not start"))
            return CertReport(client, argv, results)

        results.append(_c3(cl, expected_tools))
        results.append(_c4(cl, root, expected_version))
        results.append(_c5(cl, root, subject))
        results.append(_c6(cl, root, subject, approve))
    finally:
        cl.close()
    return CertReport(client, argv, results)


def _guard(cid: str, fn: Callable[[], CriterionResult]) -> CriterionResult:
    try:
        return fn()
    except (McpClientError, OSError, KeyError, TypeError, ValueError) as exc:
        return CriterionResult(cid, FAIL, f"{type(exc).__name__}: {exc}")


def _c3(cl: StdioMcpClient, expected: Sequence[str]) -> CriterionResult:
    def run() -> CriterionResult:
        got = cl.list_tools()
        missing = [t for t in PROBE_TOOLS if t not in got]
        if missing:
            return CriterionResult("C3", FAIL, f"probe tools missing from tools/list: {missing}")
        if set(got) != set(expected):
            extra, lack = sorted(set(got) - set(expected)), sorted(set(expected) - set(got))
            return CriterionResult("C3", FAIL, f"version skew: server serves {len(got)} tools, "
                                   f"this mokata registers {len(expected)} (extra={extra[:5]}, "
                                   f"missing={lack[:5]})")
        return CriterionResult("C3", PASS, f"{len(got)} tools listed, equal to the registry")
    return _guard("C3", run)


def _c4(cl: StdioMcpClient, root: str, version: str) -> CriterionResult:
    def run() -> CriterionResult:
        st = cl.call("status", {"path": root})
        if st.get("version") != version:
            return CriterionResult("C4", FAIL, f"status answered version {st.get('version')!r}, "
                                   f"expected {version!r} (version skew)")
        return CriterionResult("C4", PASS, f"status answered (version {version}, "
                               f"profile {st.get('profile')})")
    return _guard("C4", run)


def _recall_values(cl: StdioMcpClient, root: str, subject: str) -> List[str]:
    res = cl.call("recall", {"path": root, "subject": subject})
    return [i.get("value") for i in res.get("items", []) if i.get("subject") == subject]


def _c5(cl: StdioMcpClient, root: str, subject: str) -> CriterionResult:
    def run() -> CriterionResult:
        args = {"path": root, "subject": subject, "value": "c5-must-not-land"}
        bad: List[str] = []
        r1 = cl.call("remember", dict(args))
        if r1.get("committed") or r1.get("status") != "proposed":
            bad.append(f"no-id call -> {r1.get('status')}")
        r2 = cl.call("remember", dict(args, approve=True))
        if r2.get("committed"):
            bad.append("model-typed approve=true committed")
        r3 = cl.call("remember", dict(args, proposal_id="p-forged000000"))
        if r3.get("committed") or r3.get("status") != "refused":
            bad.append(f"forged id -> {r3.get('status')}")
        if "c5-must-not-land" in _recall_values(cl, root, subject):
            bad.append("the value is in memory")
        if bad:
            return CriterionResult("C5", FAIL, "gate leaked: " + "; ".join(bad))
        return CriterionResult("C5", PASS, "proposed / not committed on approve=true / forged id "
                               f"refused ({r3.get('reason_code')}); nothing written")
    return _guard("C5", run)


def _c6(cl: StdioMcpClient, root: str, subject: str,
        approve: Callable[[str, str], bool]) -> CriterionResult:
    def run() -> CriterionResult:
        args = {"path": root, "subject": subject, "value": "c6-round-trip"}
        prop = cl.call("remember", dict(args))
        pid = prop.get("proposal_id")
        if not pid:
            return CriterionResult("C6", FAIL, f"no proposal id from remember: {prop.get('status')}")
        if not approve(root, pid):
            return CriterionResult("C6", FAIL, f"the out-of-band mint `mokata approve {pid}` failed")
        done = cl.call("remember", dict(args, proposal_id=pid))
        if not done.get("committed"):
            return CriterionResult("C6", FAIL, f"redeem did not commit: {done.get('status')} "
                                   f"{done.get('reason_code', '')}")
        if "c6-round-trip" not in _recall_values(cl, root, subject):
            return CriterionResult("C6", FAIL, "committed, but recall does not return the value")
        again = cl.call("remember", dict(args, proposal_id=pid))
        if again.get("committed"):
            return CriterionResult("C6", FAIL, "the same approval committed twice")
        return CriterionResult("C6", PASS, f"propose -> mint -> commit -> recall; reuse refused "
                               f"({again.get('reason_code')})")
    return _guard("C6", run)


# --------------------------------------------------------------------------- the manual checklist

def checklist(client: str) -> str:
    """The owner's per-client run sheet: the same six criteria, against the REAL client."""
    c = CERT_CLIENTS[client]
    cfg = (f"Run `mokata setup {c.harness}` in a scratch git repo. Confirm {c.config_where} has "
           f"an `mcpServers.mokata` entry.")
    if c.manual_config:
        cfg = (f"Run `mokata setup {c.harness}` in a scratch git repo (it writes the commands but "
               f"NOT the MCP config). Register the server by hand in {c.config_where}:\n\n"
               + "\n".join("      " + ln for ln in c.manual_config.splitlines())
               + "\n\n    Record C1 as `manual` — it is a PASS only once setup writes this itself."
               "\n    Verify the snippet against the client's current MCP docs before recording.")
    steps = [
        ("C1", cfg),
        ("C2", f"Start {c.display} in that repo. The `mokata` server shows as connected/running "
               "(no start error)."),
        ("C3", c.verify_listing + ". `status`, `remember` and `recall` are among them."),
        ("C4", "Ask the agent to call the mokata `status` tool. It answers with mokata's version "
               "and profile."),
        ("C5", "Ask the agent to `remember` subject `cert-probe` = `c5`. It must come back "
               "`proposed`/awaiting approval and NOTHING is written (`mokata memory list` or a "
               "`recall` shows no `c5`). Ask it to retry with approve=true: still not committed."),
        ("C6", "Run the `mokata approve <id>` it printed IN YOUR TERMINAL. Ask the agent to re-call "
               "`remember` with that proposal_id: `committed`. Ask it to `recall` `cert-probe`: the "
               "value comes back. Ask it to re-call with the same id: refused (used)."),
    ]
    out = [f"# {c.display} — mokata-mcp certification checklist", "",
           f"Client key: `{c.key}`  ·  setup target: `mokata setup {c.harness}`", ""]
    for cid, text in steps:
        crit = next(x for x in CRITERIA if x.id == cid)
        out.append(f"- [ ] **{cid} {crit.name}** — {crit.statement}.")
        out.append(f"    {text}")
    out += ["", "Record each result (pass/fail/manual) with the client's version:",
            f"    python scripts/harness_cert.py record {c.key} <C1..C6> <pass|fail|manual> "
            "--client-version <x.y.z> --by <you> [--note ...]"]
    return "\n".join(out)


# --------------------------------------------------------------------------- the matrix file

MATRIX_VERSION = 1


def empty_matrix() -> Dict[str, Any]:
    return {"version": MATRIX_VERSION,
            "criteria": {c.id: c.statement for c in CRITERIA},
            "clients": {k: {cid: {"result": UNTESTED} for cid in CRITERION_IDS}
                        for k in CERT_CLIENTS}}


def load_matrix(path: str) -> Dict[str, Any]:
    """Load the matrix. An absent file is the empty matrix; an unparseable one REFUSES (recorded
    owner evidence is never silently replaced by a blank matrix)."""
    p = Path(path)
    if not p.exists():
        return empty_matrix()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != MATRIX_VERSION:
        raise ValueError(f"{path}: not a v{MATRIX_VERSION} certification matrix")
    return data


def record_result(path: str, client: str, criterion: str, result: str, *,
                  client_version: str, recorder: str, date: str, note: str = "",
                  mokata_version: str = "") -> Dict[str, Any]:
    """Record ONE owner-observed result. A PASS without a client version, recorder and date is
    refused — an undated, unattributed pass is exactly the false claim this file must not hold."""
    if client not in CERT_CLIENTS:
        raise ValueError(f"unknown client '{client}'")
    if criterion not in CRITERION_IDS:
        raise ValueError(f"unknown criterion '{criterion}'")
    if result not in (PASS, FAIL, MANUAL):
        raise ValueError("result must be pass, fail or manual")
    if not (client_version.strip() and recorder.strip() and date.strip()):
        raise ValueError("a recorded result needs --client-version, --by and a date")
    if not mokata_version:
        from . import __version__ as mokata_version
    m = load_matrix(path)
    m["clients"].setdefault(client, {})[criterion] = {
        "result": result, "client_version": client_version, "recorded_by": recorder,
        "date": date, "mokata_version": mokata_version, "note": note}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return m


def _is_evidenced_pass(cell: Any) -> bool:
    return (isinstance(cell, dict) and cell.get("result") == PASS
            and all(str(cell.get(k, "")).strip()
                    for k in ("client_version", "recorded_by", "date")))


def certified(matrix: Dict[str, Any], client: str) -> bool:
    """True ONLY when every criterion holds an evidenced PASS for this client."""
    row = matrix.get("clients", {}).get(client, {})
    return all(_is_evidenced_pass(row.get(cid)) for cid in CRITERION_IDS)


def render_matrix(matrix: Dict[str, Any]) -> str:
    head = "| client | " + " | ".join(CRITERION_IDS) + " | certified |"
    lines = [head, "|" + "---|" * (len(CRITERION_IDS) + 2)]
    for key, c in CERT_CLIENTS.items():
        row = matrix.get("clients", {}).get(key, {})
        cells = [str((row.get(cid) or {}).get("result", UNTESTED)) for cid in CRITERION_IDS]
        lines.append(f"| {c.display} | " + " | ".join(cells) + " | "
                     + ("yes" if certified(matrix, key) else "no") + " |")
    return "\n".join(lines)
