"""GR.S2 (j) — the REAL code-review-graph client, grounded against the ACTUAL interface.

P-grounding: the 0.0.13 `SubprocessGraphClient` assumed `<tool> query <kind> <target> --json`.
That interface does not exist. The real code-review-graph (v2.3.x, package `code-review-graph`,
console script `code-review-graph` — NOT `crg`) works like this (verified 2026-07-15 from
`tirth8205/code-review-graph`):

  * Symbol queries are **MCP-only** — reachable by running `code-review-graph serve` (an MCP
    stdio server) and calling tools. There is no one-shot CLI query subcommand.
      - `query_graph_tool(pattern, target)` with pattern in {callers_of, callees_of, imports_of,
        importers_of, children_of, tests_for, inheritors_of, file_summary}
      - `get_impact_radius_tool(changed_files, max_depth)` — file-scoped blast radius
      - `traverse_graph_tool(query, depth, mode)` — symbol-scoped BFS/DFS (mokata's blast_radius)
      - `semantic_search_nodes_tool(query, kind, limit)` — hybrid FTS + local embedding
  * Result rows: `results:[{name, qualified_name, kind, file_path, line_start, line_end,
    is_test}]` + `edges:[{source_qualified, target_qualified, kind, file_path}]`
    (kind in CALLS/IMPORTS_FROM/CONTAINS/TESTED_BY/INHERITS/IMPLEMENTS).
  * Build / refresh via the CLI: `build` (full), `update [--base ref]` (incremental).
  * Version via `code-review-graph --version` -> "code-review-graph X.Y.Z" (no schema/compat
    version is exposed — the handshake parses this semver string).
  * Semantic uses CRG's OWN bundled local embedding (`[embeddings]` extra, `all-MiniLM-L6-v2`,
    no API key) — doc-85 boundary: this is the ADOPTED tool's capability, not mokata building a
    vector-first code RAG of its own. mokata's own retrieval stays lexical-first.

Transport is injected (`call_tool` for MCP tools, `run_cli` for the CLI) so the kind->CRG
MAPPING and result NORMALIZATION are unit-tested on a fake transport; the real stdio MCP
session (`_McpStdioSession`) is exercised only by the opt-in real-CRG CI leg. mokata carries no
`mcp` client dependency — the session speaks JSON-RPC over stdio with the stdlib.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..bounded_io import (BoundedStderrTail, READ_CLOSED, READ_ERROR, READ_TIMEOUT,
                          read_line_bounded)
from ..degrade import FAILURE_TIMEOUT
from ..errors import DegradedCapability

# The console script + MCP tool names, pinned to the grounded interface.
CRG_COMMAND = "code-review-graph"

# mokata's typed query kind -> CRG `query_graph_tool` pattern. `blast_radius` is special-cased
# (symbol-scoped traversal), so it is not in this map.
_KIND_TO_PATTERN: Dict[str, str] = {
    "callers": "callers_of",
    "callees": "callees_of",
    "imports": "imports_of",
    "implementers": "inheritors_of",
}

# CRG-NAV (b) — `refs` ("everywhere this is referenced") is the UNION of the INBOUND patterns the
# grounded interface exposes: who calls it, who imports it, who inherits it. It is composed from
# patterns already on record above/here, not a new tool and not a guess: each is a documented
# `query_graph_tool` pattern (see the module docstring). A reference that is none of those three
# — a bare name passed as a value — is not a graph edge CRG holds; that is the lexical floor's
# question, and a floor answer says so.
_REFS_PATTERNS: Tuple[str, ...] = ("callers_of", "importers_of", "inheritors_of")

# CRG-NAV (b) — the navigation intent code-review-graph CANNOT back. Its `query_graph_tool`
# patterns (callers_of / callees_of / imports_of / importers_of / children_of / tests_for /
# inheritors_of / file_summary) contain no definition-site lookup, and `semantic_search_nodes_tool`
# ranks by similarity rather than resolving a name — mapping "where is X defined" onto it would be
# a fuzzy answer wearing a structural label. So the client REFUSES the kind here and the layer
# routes it to the AST floor, which answers it exactly on a Python repo.
UNMAPPED_KINDS: Tuple[str, ...] = ("defs",)

# Version-compat handshake bounds (semver, inclusive lower / exclusive upper-major). CRG exposes
# no schema version, so the handshake is on the CLI `--version` string. The adopted-tool contract
# mokata maps was grounded against the 2.x line.
CRG_MIN_VERSION: Tuple[int, ...] = (2, 0, 0)
CRG_MAX_MAJOR: int = 3            # refuse 3.x until the interface is re-grounded


class CrgUnavailable(DegradedCapability):
    """The code-review-graph tool COULD NOT ANSWER (serve not running, CLI missing, index
    absent, pipe closed, reply not JSON-RPC). The layer degrades to the AST floor on this —
    never a silent wrong answer.

    Its SIBLING is `CrgTimeout` below: *it could not answer* and *it did not answer* are two
    facts, and this class carries only the first (doc 85 §7g)."""


class CrgTimeout(DegradedCapability):
    """The code-review-graph tool is ALIVE and did not answer within the bound.

    ⛔ A SIBLING of `CrgUnavailable`, NEVER a subclass — doc 85 §7g. `CrgUnavailable` means the
    tool *could not answer at all*: the process died, the CLI is missing, the pipe closed, the
    index is absent. This one means it *did not answer*: the process is running, its pipe is open,
    and nothing came back. Those are two different facts with two different remedies (reinstall or
    re-adopt, versus look at the tool's own index/logs or raise the bound), and a subclass would be
    absorbed by any `except CrgUnavailable` so the two would re-collapse WITHOUT LEAVING A MARK —
    the failure mode §7g exists to refuse. The split is safe to make because `src/` contains no
    `except CrgUnavailable` at all (verified at 0.0.19 stage 01): every degrade site catches
    broadly, so no call site loses its degrade by it.

    The layer degrades to the AST floor on this exactly as it does on `CrgUnavailable` — and says
    LOUDLY which of the two happened, classed `degrade.FAILURE_TIMEOUT`."""

    failure_class = FAILURE_TIMEOUT


class CrgVersionSkew(DegradedCapability):
    """The installed code-review-graph reports a version outside the range mokata's client was
    grounded against — refuse to trust its results rather than mis-map them."""


def parse_crg_version(text: str) -> Optional[str]:
    """Pull the semver out of `code-review-graph X.Y.Z` (tolerant of extra words/newlines)."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return ".".join(m.groups()) if m else None


def _version_tuple(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        return ()


def check_version_string(v: Optional[str]) -> str:
    """Range-check a raw version string. Returns it when in range, else raises CrgVersionSkew /
    CrgUnavailable. Shared by the client and the backend handshake so the range lives in one
    place."""
    if not v:
        raise CrgUnavailable("code-review-graph did not report a version")
    vt = _version_tuple(v)
    if not vt or vt < CRG_MIN_VERSION or vt[0] >= CRG_MAX_MAJOR:
        raise CrgVersionSkew(
            f"code-review-graph {v} is outside the supported range "
            f">={'.'.join(map(str, CRG_MIN_VERSION))},<{CRG_MAX_MAJOR}.0.0")
    return v


class CodeReviewGraphClient:
    """Speaks mokata's `GraphQueryClient` protocol (`query(kind, target, root, depth)`) plus the
    richer surface the full-capacity backend uses (`semantic`, `refresh`, `version`, `health`)."""

    supports_semantic = True

    def __init__(
        self,
        name: str = CRG_COMMAND,
        root: str = ".",
        *,
        call_tool: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        run_cli: Optional[Callable[[List[str]], Tuple[int, str, str]]] = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.root = root
        self.timeout = timeout
        self._call_tool = call_tool or self._default_call_tool
        self._run_cli = run_cli or self._default_run_cli

    # --- typed queries (GraphQueryClient protocol) --------------------------------------
    def supports_kind(self, kind: str) -> bool:
        """CRG-NAV (b) — whether the REAL code-review-graph interface has an op for `kind`.
        False for `defs` (no definition-site pattern exists); the layer reads this and answers
        that one kind from the floor with an honest note, so a MAPPING gap is never reported as
        a graph FAILURE and never costs a wasted recovery attempt."""
        if kind in UNMAPPED_KINDS:
            return False
        return kind in _KIND_TO_PATTERN or kind in ("blast_radius", "refs")

    def query(self, kind: str, target: str, root: str, depth: int = 1) -> List[Dict[str, Any]]:
        if kind == "refs":
            return self._refs(target, root)
        if kind == "blast_radius":
            payload = self._call_tool("traverse_graph_tool", {
                "query": target, "depth": max(1, int(depth)), "mode": "bfs",
                "repo_root": root})
        else:
            pattern = _KIND_TO_PATTERN.get(kind)
            if pattern is None:
                raise CrgUnavailable(f"code-review-graph has no mapping for kind '{kind}'")
            payload = self._call_tool("query_graph_tool", {
                "pattern": pattern, "target": target, "repo_root": root})
        return self._normalize(payload)

    def _refs(self, target: str, root: str) -> List[Dict[str, Any]]:
        """CRG-NAV (b) — `refs` over the union of the inbound patterns, de-duplicated on
        (path, line, symbol) so a symbol that is both called and imported in one place is ONE
        reference. A pattern that finds nothing contributes nothing; a pattern that FAILS raises
        (CrgUnavailable) and the whole answer degrades to the floor, exactly as a single-pattern
        kind would — the composition adds no new failure mode."""
        rows: List[Dict[str, Any]] = []
        seen = set()
        for pattern in _REFS_PATTERNS:
            payload = self._call_tool("query_graph_tool", {
                "pattern": pattern, "target": target, "repo_root": root})
            for row in self._normalize(payload):
                key = (row.get("path"), row.get("line"), row.get("symbol"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        return rows

    def resolves(self, symbol: str, root: str) -> bool:
        """Authoritative existence check for docsync's symbol-drift audit. CRG's `query_graph_tool`
        returns `status: "not_found"` for an absent node and `ok`/`ambiguous` for a present one —
        so a doc that references a renamed/deleted symbol is caught precisely (a stale-but-uncalled
        symbol is NOT a false positive, unlike inferring existence from caller counts)."""
        payload = self._call_tool("query_graph_tool", {
            "pattern": "callers_of", "target": symbol, "repo_root": root})
        status = (payload or {}).get("status", "ok")
        return status != "not_found"

    def semantic(self, query: str, root: str, kind: Optional[str] = None,
                 limit: int = 20) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"query": query, "limit": int(limit), "repo_root": root}
        if kind:
            params["kind"] = kind
        payload = self._call_tool("semantic_search_nodes_tool", params)
        return self._normalize(payload)

    # --- operational acts (run-state; no durable/manifest write) ------------------------
    def refresh(self, root: str, full: bool = False) -> bool:
        """Rebuild (`build`) or incrementally refresh (`update`) the CRG index. An OPERATIONAL
        act (GR.S2(k)) — never a durable/manifest write."""
        args = ["build"] if full else ["update"]
        rc, _out, _err = self._run_cli(args)
        return rc == 0

    def provision_semantic(self, root: str) -> bool:
        """Enable + provision CRG's OWN bundled local semantic index (`embed --provider local`,
        model all-MiniLM-L6-v2, no API key). Doc-85 boundary: this switches on the ADOPTED
        tool's capability — mokata's own retrieval stays lexical-first.

        DB.S4 SEAM HOOK (audit fix #1, 2026-07-15): the `mokata[embeddings]` seam does NOT exist
        yet (DB.S4 builds it, position 16). Until then GR.S2 uses CRG's bundled embedding here;
        when DB.S4 lands, the no-duplicate-channel seam-reuse wires in at THIS call site so a
        single embedding provider serves both mokata memory and the adopted graph."""
        rc, _out, _err = self._run_cli(["embed", "--provider", "local"])
        return rc == 0

    def version(self, root: Optional[str] = None) -> Optional[str]:
        rc, out, err = self._run_cli(["--version"])
        if rc != 0:
            return None
        return parse_crg_version(out) or parse_crg_version(err)

    def health(self, root: str) -> bool:
        """A cheap liveness probe: the tool answers `--version`. (The backend's query path is
        what ultimately proves the serve/index is live; this is the fast pre-check.)"""
        try:
            return self.version(root) is not None
        except Exception:
            return False

    def check_version_compat(self, root: Optional[str] = None) -> str:
        """Version-compat handshake (adopt + upgrade). Returns the semver string or raises
        CrgVersionSkew when outside the grounded range."""
        return check_version_string(self.version(root))

    # --- result normalization -----------------------------------------------------------
    @staticmethod
    def _normalize(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """CRG node+edge results -> mokata row dicts (rich: edge_type + metadata). Defensive:
        CRG wrapper keys vary by tool, so we read `results`/`impacted_nodes` tolerantly and
        never raise on a missing field (a shape drift degrades to fewer fields, not a crash)."""
        if not isinstance(payload, dict):
            return []
        nodes = payload.get("results")
        if nodes is None:
            nodes = payload.get("impacted_nodes") or []
        edges = payload.get("edges") or []
        # edge kind by the qualified name it touches (best-effort: target then source).
        edge_kind: Dict[str, str] = {}
        for e in edges:
            if not isinstance(e, dict):
                continue
            k = e.get("kind")
            for key in ("target_qualified", "source_qualified"):
                q = e.get(key)
                if q and k and q not in edge_kind:
                    edge_kind[q] = k
        rows: List[Dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            qname = n.get("qualified_name") or n.get("name") or ""
            file_path = n.get("file_path") or n.get("path") or ""
            line = n.get("line_start", n.get("line", 0))
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0
            rows.append({
                "path": file_path,
                "line": line,
                "snippet": "",
                "symbol": qname or None,
                "edge_type": edge_kind.get(qname),
                "metadata": {
                    "qualified_name": qname,
                    "kind": n.get("kind"),
                    "is_test": bool(n.get("is_test", False)),
                },
            })
        return rows

    # --- real transports (opt-in real-CRG CI leg validates these) -----------------------
    def _default_run_cli(self, args: List[str]) -> Tuple[int, str, str]:
        cmd = [self.name] + list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self.timeout, cwd=self.root)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CrgUnavailable(f"could not run {self.name}: {exc}") from exc
        # Secret-safety (B-VER precedent): CLI output for version/build/update carries no env
        # secret, but we never surface raw output — only the parsed version — so nothing leaks.
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _default_call_tool(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = getattr(self, "_session", None)
        if session is None:
            session = _McpStdioSession(self.name, root=self.root, timeout=self.timeout)
            self._session = session
        try:
            return session.call(tool, params)
        except CrgTimeout:
            # A1 — the SECOND half of killing a desynchronised session, and it is not the same
            # half as `close()`. `close()` disposes of the PROCESS; this disposes of the
            # CONVERSATION — the object holding the request-id counter and the dead pipes, cached
            # HERE and reachable long after the process is gone. Dropping it is what guarantees
            # the next call builds a session from scratch rather than resuming a numbering the
            # server on the other end never saw the end of.
            self._session = None
            raise


class _McpStdioSession:
    """A minimal JSON-RPC-over-stdio MCP client to `code-review-graph serve`. Stdlib-only, no
    `mcp` dependency. Validated by the opt-in real-CRG CI leg; unit tests inject `call_tool`
    instead.

    TWO EXITS, AND THEY CARRY DIFFERENT FACTS (doc 85 §7g):

      * `CrgUnavailable` — the transport FAILED. The process would not start, the write raised,
        the server closed the connection, or what came back was not JSON-RPC. It COULD NOT ANSWER.
      * `CrgTimeout` — the server is ALIVE and said NOTHING inside `self.timeout`. It DID NOT
        ANSWER. Before 0.0.19 this exit did not exist and neither did the bound: `readline()` had
        nothing bounding it, so a server that started and went quiet raised nothing, returned
        nothing and wedged the caller forever (#45, #46, #53) — which made the safety property the
        old docstring promised UNREACHABLE for the failure mode that actually occurred.

    Either exit degrades the layer to the AST floor; the degrade layer says WHICH.

    ⛔ A TIMED-OUT SESSION IS DESYNCHRONISED AND IS KILLED, NEVER REUSED. The request was written
    and its reply was never read, so a surviving process would hand the NEXT `_rpc` the PREVIOUS
    call's answer — a silently wrong graph answer, the one outcome `CrgUnavailable`'s docstring
    promises never happens. `_rpc` therefore calls `close()` on the timeout path, and the owning
    client drops the cached session, so the next call starts a clean process."""

    def __init__(self, command: str, root: str = ".", timeout: float = 30.0) -> None:
        self.command = command
        self.root = root
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._stderr: Optional[BoundedStderrTail] = None
        self._id = 0

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
                [self.command, "serve", "--repo", self.root],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CrgUnavailable(f"could not start {self.command} serve: {exc}") from exc
        # A1 — stderr was `DEVNULL`, which threw away the subprocess's own account of why it hung;
        # the people who filed #45/#46/#53 could not say what happened because the code discarded
        # it. It is now captured — but DRAINED CONTINUOUSLY, never left sitting in a pipe: an
        # unread `PIPE` fills its ~64 KiB buffer and blocks the server on its next write, which
        # would be a brand-new hang introduced by the fix for a hang.
        self._stderr = BoundedStderrTail(self._proc.stderr)
        self._rpc("initialize", {"protocolVersion": "2024-11-05",
                                 "capabilities": {}, "clientInfo": {"name": "mokata"}})
        self._notify("notifications/initialized", {})
        return self._proc

    def _stderr_tail(self) -> str:
        """The server's last words, capped and SECRET-CHECKED.

        This is FOREIGN text — mokata did not choose what is in it — so it goes through mokata's
        own scanner and the whole tail is WITHHELD if anything secret-shaped is in it, rather than
        redacted around. Redacting text we did not author is a guess, and the tail is a diagnostic
        courtesy, not evidence we owe the user; dropping it costs a hint, leaking would cost a
        credential."""
        tail = self._stderr
        text = tail.tail() if tail is not None else ""
        if not text:
            return ""
        try:
            from ..govern.secrets import scan
        except Exception:                    # fail CLOSED: unable to check ⇒ do not surface it
            return "(withheld: the server's stderr could not be checked for secrets)"
        if scan(text=text):
            return "(withheld: the server's stderr contained secret-shaped content)"
        return text

    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        proc = self._proc or self._ensure()
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        try:
            assert proc.stdin and proc.stdout
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError, AssertionError) as exc:
            raise CrgUnavailable(f"code-review-graph serve I/O failed: {exc}") from exc
        # A1 — THE BOUND. `self.timeout` has been stored on this class since the day it was
        # written and was applied on the OTHER transport path only (`_default_run_cli` passes it
        # to `subprocess.run`). One transport bounded and one not, in the same file, from the same
        # attribute: a separated pair, now rejoined. The reader is a THREAD, not `select` —
        # `select` on a pipe does not work on Windows (`bounded_io` carries the argument).
        read = read_line_bounded(proc.stdout, self.timeout)
        if read.outcome == READ_TIMEOUT:
            detail = self._stderr_tail()     # read BEFORE the kill; the drain already has it
            self.close()                     # DESYNCHRONISED — see the class docstring
            raise CrgTimeout(
                # `:g`, not `:.0f` — a 0.6s bound reported as "within 1s" is a small untruth in
                # the one message a user reads to decide whether to raise the bound.
                f"code-review-graph serve did not answer {method} within "
                f"{self.timeout:g}s" + (f" (server stderr: {detail})" if detail else ""))
        if read.outcome == READ_ERROR:
            raise CrgUnavailable(
                f"code-review-graph serve I/O failed: {read.error}") from read.error
        if read.outcome == READ_CLOSED:
            raise CrgUnavailable("code-review-graph serve closed the connection")
        line = read.text
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CrgUnavailable(f"code-review-graph serve sent non-JSON: {exc}") from exc
        if "error" in msg:
            raise CrgUnavailable(f"code-review-graph serve error: {msg['error']}")
        return msg.get("result", {})

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        proc = self._proc
        if proc and proc.stdin:
            try:
                proc.stdin.write(json.dumps(
                    {"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
                proc.stdin.flush()
            except OSError:
                pass

    def call(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure()
        result = self._rpc("tools/call", {"name": tool, "arguments": params})
        # MCP tool results wrap content; CRG returns a JSON text block.
        content = result.get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block.get("text") or "{}")
                except json.JSONDecodeError:
                    continue
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        """Kill the server process and forget it, so the next call starts clean.

        A1 — this now REAPS. `terminate()` alone only sends the signal; a process that has already
        stopped answering is exactly the one least likely to act on it, and leaving it unwaited
        left the very process this path exists to get rid of still running. The `kill()` fallback
        is the same shape `mcp_admin._terminate` uses for the same reason."""
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._proc = None
        self._stderr = None
