"""B1 — the code-review-graph adapter.

mokata adopts a commodity codebase-graph backend; it does NOT build a parser or an
in-house graph. This adapter is the boundary: it translates mokata's typed queries into
calls on a graph client and the client's rows back into the typed `QueryResult` shape.
All structural work happens inside the adopted tool.

The client is injected, so the exact wire protocol of the real tool lives in one place
(the "bring your own tool" boundary). GR.S2 replaced the best-effort `SubprocessGraphClient`
CLI assumption with `crg_client.CodeReviewGraphClient`, grounded against the REAL
code-review-graph interface (MCP-served symbol queries + CLI build/version). Any client
failure raises `BackendError`, which the knowledge layer turns into a graceful degrade to
the AST/grep floor (A3) — never a silent wrong answer.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Protocol

from .query import QUERY_KINDS, BackendError, GraphBackend, QueryResult, Reference


class GraphQueryClient(Protocol):
    """The contract a graph backend client must satisfy. Returns a list of row dicts with
    keys: path, line, snippet, symbol (GR.S2 adds optional edge_type, metadata)."""

    def query(self, kind: str, target: str, root: str,
              depth: int = 1) -> List[Dict[str, Any]]:
        ...


class SubprocessGraphClient:
    """DEPRECATED (GR.S2). The 0.0.13 assumption that code-review-graph exposes
    `<tool> query <kind> <target> --json` was WRONG — the real tool has no query subcommand
    (symbol queries are MCP-only). Kept only so the name still imports; use
    `crg_client.CodeReviewGraphClient`, which speaks the real interface."""

    def __init__(self, tool: str, timeout: float = 30.0) -> None:
        warnings.warn(
            "SubprocessGraphClient is deprecated (its CLI shape does not match the real "
            "code-review-graph); use crg_client.CodeReviewGraphClient",
            DeprecationWarning, stacklevel=2,
        )
        from .crg_client import CodeReviewGraphClient
        self._real = CodeReviewGraphClient(name=tool, timeout=timeout)

    def query(self, kind: str, target: str, root: str,
              depth: int = 1) -> List[Dict[str, Any]]:
        return self._real.query(kind, target, root=root, depth=depth)


class CodeReviewGraphBackend(GraphBackend):
    is_graph = True

    def __init__(self, name: str, root: str,
                 client: Optional[GraphQueryClient] = None) -> None:
        self.name = name
        self.root = root
        if client is None:
            from .crg_client import CodeReviewGraphClient
            client = CodeReviewGraphClient(name=name, root=root)
        self.client = client

    @property
    def supports_semantic(self) -> bool:
        """True when the adopted client exposes a semantic index (CRG's own bundled embedding).
        The AST/grep floors never do — semantic is the ADOPTED tool's capability (doc 85)."""
        return bool(getattr(self.client, "supports_semantic", False)) and hasattr(
            self.client, "semantic")

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

    @property
    def supports_resolve(self) -> bool:
        """True when the adopted client can AUTHORITATIVELY answer symbol existence (CRG's
        `not_found` status). Used by docsync's symbol-drift audit (GR.S2 rider)."""
        return hasattr(self.client, "resolves")

    def resolves(self, symbol: str) -> bool:
        """Whether the adopted graph resolves `symbol` (exists). Degrade-clean: a client failure
        returns True (don't manufacture a false 'stale symbol' finding on a graph hiccup)."""
        fn = getattr(self.client, "resolves", None)
        if not callable(fn):
            return True
        try:
            return bool(fn(symbol, root=self.root))
        except Exception:
            return True

    # --- keep-functional machinery (GR.S2(k); operational acts, run-state only) ----------
    def handshake(self) -> Optional[str]:
        """Version-compat handshake (adopt + upgrade). Returns the semver or raises
        CrgVersionSkew / CrgUnavailable. A no-op (returns None) for a client that reports no
        version — the handshake only refuses a version it KNOWS is out of range."""
        client = self.client
        checker = getattr(client, "check_version_compat", None)
        if callable(checker):
            return checker(self.root)
        # Any client exposing version() is still range-checked (the fake double, an alt tool).
        version = getattr(client, "version", None)
        if callable(version):
            from .crg_client import check_version_string
            return check_version_string(version(self.root))
        return None

    def recover(self) -> bool:
        """One bounded, OPERATIONAL recovery attempt: re-probe health; if the tool is down,
        ask it to refresh/rebuild its index; re-probe. Returns True iff the tool is live again.
        NEVER a durable/manifest write — this is run-state only (GR.S2(k))."""
        client = self.client
        health = getattr(client, "health", None)
        if not callable(health):
            return False
        try:
            if health(self.root):
                return True                       # a transient blip already cleared
            refresh = getattr(client, "refresh", None)
            if callable(refresh):
                refresh(self.root, full=False)    # index refresh/rebuild (operational)
            return bool(health(self.root))
        except Exception:
            return False

    def refresh_index(self) -> bool:
        """GR.S4 / GR.S2(k): PROACTIVELY refresh the adopted graph's index (`update`, never a
        full rebuild) so a freshness signal keeps the graph current BEFORE it answers. Distinct
        from `recover()` (reactive, health-gated): this is the freshness-driven incremental
        re-index. True on success; False (degrade-clean, never raises) is treated as a rebuild
        failure by the caller ⇒ answer from the AST floor, never from stale graph data."""
        refresh = getattr(self.client, "refresh", None)
        if not callable(refresh):
            return True                          # no refreshable index ⇒ nothing to fail
        try:
            return bool(refresh(self.root, full=False))
        except Exception:
            return False

    def semantic(self, query: str, kind: Optional[str] = None,
                 limit: int = 20) -> QueryResult:
        """Semantic (hybrid FTS + embedding) search over the adopted graph. Returns a
        QueryResult with kind='semantic'. Degrades honestly when the client has no semantic
        index rather than pretending — mokata's own retrieval stays lexical-first (doc 85)."""
        if not self.supports_semantic:
            return QueryResult(
                kind="semantic", target=query, references=[], backend=self.name,
                degraded=True, note="the adopted graph exposes no semantic index")
        try:
            rows = self.client.semantic(query, root=self.root, kind=kind, limit=limit)
        except Exception as exc:
            raise BackendError(
                f"graph backend '{self.name}' semantic search failed: {exc}") from exc
        refs = [Reference.from_dict(r) for r in rows]
        return QueryResult(
            kind="semantic", target=query, references=refs, backend=self.name,
            degraded=False, note="answered by the codebase graph's semantic index")
