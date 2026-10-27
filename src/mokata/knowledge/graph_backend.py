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

from ..errors import failure_class_of
from .query import (BASIS_LEXICAL, BASIS_STRUCTURAL, QUERY_KINDS, BackendError,
                    GraphBackend, GraphIndexUnbuilt, GraphNotIndexed, QueryResult, Reference,
                    floor_symbol, mark_truncated)


def _backend_error(message: str, cause: BaseException) -> BackendError:
    """Wrap a client failure in the typed `BackendError` **carrying the cause's own failure
    class**.

    A1 (0.0.19) — the wrap used to be lossy. Every degrade site here catches broadly (that is
    deliberate: the client is a bring-your-own-tool boundary and its exception classes are the
    adopted tool's, not mokata's), so *"the tool died"* and *"the tool is alive and mute"* both
    arrived at `KnowledgeLayer._run` as one indistinguishable `BackendError` and were announced
    with one class. Stamping the class per instance — the pattern `errors.py` documents and
    `teamdb` already uses — carries the distinction across the boundary WITHOUT teaching this
    adapter the name of any one adopted tool's exceptions. An unclassed cause (a plain
    `RuntimeError` from an injected double) yields the inherited `unreachable`, so nothing that
    worked before changes."""
    err = BackendError(message)
    cls = failure_class_of(cause)
    if cls:
        err.failure_class = cls
    return err


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

    def supports_kind(self, kind: str) -> bool:
        """CRG-NAV (b/d) — delegate to the adopted client's own declaration of what its real
        interface maps. A client that does not declare (an injected double, an alternative
        adopted tool) is assumed to answer everything, so behaviour is unchanged for it."""
        if kind not in QUERY_KINDS:
            return False
        fn = getattr(self.client, "supports_kind", None)
        if not callable(fn):
            return True
        try:
            return bool(fn(kind))
        except Exception:                      # a client that misbehaves is not a refusal
            return True

    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        if kind not in QUERY_KINDS:
            raise ValueError(f"unknown query kind '{kind}'; one of {QUERY_KINDS}")
        try:
            rows = self.client.query(kind, target, root=self.root, depth=depth)
        except GraphIndexUnbuilt as unbuilt:
            raise GraphIndexUnbuilt(self.index_unbuilt_note(unbuilt)) from unbuilt
        except GraphNotIndexed:
            raise                 # the graph is healthy and cannot speak for `target` — not a failure
        except Exception as exc:  # any client/process failure -> degrade upstream
            raise _backend_error(
                f"graph backend '{self.name}' failed on {kind}({target}): {exc}", exc
            ) from exc
        refs = [self._identity(Reference.from_dict(r)) for r in rows]
        result = QueryResult(
            kind=kind, target=target, references=refs, backend=self.name,
            basis=BASIS_STRUCTURAL, note="answered by the codebase graph",
        )
        # 0.0.25 K — the adopted graph CAPS its answers and says so (`CrgRows.truncated/total`);
        # that disclosure is carried onto the answer instead of being dropped at the boundary.
        if getattr(rows, "truncated", False):
            mark_truncated(result, getattr(rows, "total", None),
                           getattr(rows, "cut_why", "")
                           or f"the adopted graph '{self.name}' capped this answer")
        # 0.0.26 P35 (G28/R37) — an ambiguous name is answered over ALL its definitions, labelled.
        n = getattr(rows, "definitions", None)
        if n and n > 1:
            result.definitions = int(n)
            result.candidates = list(getattr(rows, "candidates", []) or [])
            result.note = (f"{result.note} | {n} definitions named `{target}` — merged over all "
                           f"of them; ask by qualified name for one")
        # 0.0.26 P37 (R39) — an update in flight (or one that did not finish) is said, never hidden:
        # the answer comes from the last complete graph meanwhile.
        if getattr(self.client, "can_build_index", False):
            from .crg_build import update_note
            pending = update_note(self.root)
            if pending:
                result.note = f"{result.note} | {pending}"
        return result

    @staticmethod
    def _identity(ref: Reference) -> Reference:
        """0.0.26 O13 (F3) — the answer names symbols the way the FLOOR does (`refund`, not
        `pkg/api.py::refund`), so an IDE node id, a picked symbol or a follow-up query means the
        same thing whichever backend answered. The graph's own id stays in
        `metadata.qualified_name` (what a follow-up GRAPH query sends back)."""
        if ref.symbol and "::" in ref.symbol:
            ref.metadata = dict(ref.metadata or {})
            ref.metadata.setdefault("qualified_name", ref.symbol)
            ref.symbol = floor_symbol(ref.symbol)
        return ref

    # --- the index (0.0.26 O13 — `CRG-AUTO-BUILD`) ---------------------------------------
    def index_status(self):
        """This repo's index state (`crg_build.BuildStatus`), or None for a client that has no
        on-disk index of its own (an injected double, another adopted tool)."""
        if not getattr(self.client, "can_build_index", False):
            return None
        from .crg_build import build_status
        return build_status(self.root)

    def index_unbuilt_note(self, cause: BaseException) -> str:
        """R-a — a query hit an unbuilt index. Start the ONE background build (never wait for it)
        and return the sentence the answer carries."""
        if not getattr(self.client, "can_build_index", False):
            return str(cause)
        from .crg_build import BUILT, ensure_building
        try:
            status = ensure_building(self.root)
        except Exception as exc:                       # noqa: BLE001 — never break the query
            return f"{cause} (the index build could not be started: {exc})"
        if status.state == BUILT:
            return f"{self.name}'s index finished building just now — the next query uses it"
        return status.line(self.name)

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
        ready = getattr(client, "index_ready", None)
        if callable(ready) and not ready(self.root):
            return False                              # no index to refresh: the build's job (O13)
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
        # 0.0.26 O13 (F4) — freshness keeps an EXISTING index current, incrementally. An index that
        # was never built has nothing to freshen; the first build is setup's or first use's job
        # (`crg_build`), so this is a no-op, not a failure (a failure would route the answer to the
        # floor with a "rebuild FAILED" alarm about a build nobody ran).
        ready = getattr(self.client, "index_ready", None)
        if callable(ready):
            try:
                if not ready(self.root):
                    return True
            except Exception:                    # noqa: BLE001
                return True
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
                basis=BASIS_LEXICAL, note="the adopted graph exposes no semantic index")
        try:
            rows = self.client.semantic(query, root=self.root, kind=kind, limit=limit)
        except GraphIndexUnbuilt as unbuilt:
            raise _backend_error(self.index_unbuilt_note(unbuilt), unbuilt) from unbuilt
        except Exception as exc:
            raise _backend_error(
                f"graph backend '{self.name}' semantic search failed: {exc}", exc) from exc
        refs = [Reference.from_dict(r) for r in rows]
        return QueryResult(
            kind="semantic", target=query, references=refs, backend=self.name,
            basis=BASIS_STRUCTURAL, note="answered by the codebase graph's semantic index")
