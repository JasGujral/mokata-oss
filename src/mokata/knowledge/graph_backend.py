"""B1 — the code-review-graph adapter.

mokata adopts a commodity codebase-graph backend; it does NOT build a parser or an
in-house graph. This adapter is the boundary: it translates mokata's typed queries into
calls on a `GraphQueryClient` and the client's rows back into the typed `QueryResult`
shape. All structural work happens inside the adopted tool.

The client is injected, so the exact wire protocol of the real tool lives in one place
(the "bring your own tool" boundary). `SubprocessGraphClient` is a best-effort default
that shells out to the resolved tool against a JSON query interface; if the tool's real
CLI differs, only the client changes. Any client failure raises `BackendError`, which
the knowledge layer turns into a graceful grep fallback (A3).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional, Protocol

from .query import QUERY_KINDS, BackendError, GraphBackend, QueryResult, Reference


class GraphQueryClient(Protocol):
    """The contract a graph backend must satisfy. Returns a list of row dicts with
    keys: path, line, snippet, symbol."""

    def query(self, kind: str, target: str, root: str,
              depth: int = 1) -> List[Dict[str, Any]]:
        ...


class SubprocessGraphClient:
    """Best-effort default: invoke the resolved graph tool and parse JSON rows.

    Assumes an interface of the shape
        <tool> query <kind> <target> --root <root> [--depth N] --json
    emitting a JSON array of {path,line,snippet,symbol}. Adopting a tool whose CLI
    differs means swapping this client — nothing else in mokata changes.
    """

    def __init__(self, tool: str, timeout: float = 30.0) -> None:
        self.tool = tool
        self.timeout = timeout

    def query(self, kind: str, target: str, root: str,
              depth: int = 1) -> List[Dict[str, Any]]:
        cmd = [self.tool, "query", kind, target, "--root", root,
               "--depth", str(depth), "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{self.tool} exited {proc.returncode}: {proc.stderr.strip()}"
            )
        data = json.loads(proc.stdout or "[]")
        if not isinstance(data, list):
            raise RuntimeError(f"{self.tool} returned non-list JSON")
        return data


class CodeReviewGraphBackend(GraphBackend):
    is_graph = True

    def __init__(self, name: str, root: str,
                 client: Optional[GraphQueryClient] = None) -> None:
        self.name = name
        self.root = root
        self.client = client or SubprocessGraphClient(name)

    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        if kind not in QUERY_KINDS:
            raise ValueError(f"unknown query kind '{kind}'; one of {QUERY_KINDS}")
        try:
            rows = self.client.query(kind, target, root=self.root, depth=depth)
        except Exception as exc:  # any client/process failure -> degrade upstream
            raise BackendError(
                f"graph backend '{self.name}' failed on {kind}({target}): {exc}"
            ) from exc
        refs = [Reference.from_dict(r) for r in rows]
        return QueryResult(
            kind=kind, target=target, references=refs, backend=self.name,
            degraded=False, note="answered by the codebase graph",
        )
