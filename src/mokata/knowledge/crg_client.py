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
      - `traverse_graph_tool(query, depth, mode)` — fuzzy-started, UNDIRECTED BFS/DFS; NOT used
        (0.0.26 O2: mokata's blast_radius is a directed walk over `callers_of`, see below)
      - `semantic_search_nodes_tool(query, kind, limit)` — hybrid FTS + local embedding
  * Result rows (re-measured on 2.3.9, 0.0.26 O2): `results:[{name, qualified_name, kind,
    file_path, line_start, line_end, is_test, call_site?:{line, file?}}]` + `edges:[{source,
    target, kind, file_path, line}]` (kind in CALLS/IMPORTS_FROM/CONTAINS/TESTED_BY/INHERITS/
    IMPLEMENTS). `file_path` and `qualified_name` are ABSOLUTE (`/repo/pkg/m.py::fn`); the
    call-site patterns carry one row per CALL SITE with the call's own line in `call_site`.
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
import os
import re
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..quiet_spawn import hidden
from ..bounded_io import (BoundedStderrTail, READ_CANCELLED, READ_CLOSED, READ_ERROR,
                          READ_TIMEOUT,
                          read_line_bounded)
from ..degrade import FAILURE_TIMEOUT
from ..errors import DegradedCapability
from .query import GraphIndexUnbuilt, GraphNotIndexed

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

# CRG-NAV (b) — "where is X defined" has no op in code-review-graph's MCP interface: its
# `query_graph_tool` patterns (callers_of / callees_of / imports_of / importers_of / children_of /
# tests_for / inheritors_of / file_summary) contain no definition-site lookup, and
# `semantic_search_nodes_tool` ranks by similarity rather than resolving a name.
#
# 0.0.26 P35 (`NAV-AT-SCALE`) — so `defs` is answered from the tool's own NODE TABLE instead
# (`crg_nodes`: its `graph.db`, opened read-only), an exact-name lookup over every definition the
# graph parsed, in every language it indexes. Measured on kubernetes: `defs NewMainKubelet` went
# from the grep floor (13–17 s) to 0.08 s, structural. A client with no on-disk index (an injected
# transport) has no node table and keeps declaring `defs` unsupported; the layer then routes the
# kind to the floor, as before.

# 0.0.26 O2 (`CRG-BLAST-EMPTY`, ruling U9) — WHY `traverse_graph_tool` WAS THE WRONG OP, measured by
# reading `code_review_graph/tools/query.py::traverse_graph_func` (2.3.9), not guessed:
#   1. its rows live under `traversal` (keys `name/qualified_name/kind/file/depth`) — the parser
#      read `results`/`impacted_nodes`, so every answer normalized to [];
#   2. it follows `get_edges_by_source` AND `get_edges_by_target` — callees, the containing File,
#      and that file's OTHER functions are all "reached" (a leaf's depth-2 walk visits `unused()` in
#      the same file via CONTAINS), so reading it would have been a wrong answer, not an empty one;
#   3. its start node is `hybrid_search(query, limit=1)` — a fuzzy best match, not the symbol;
#   4. it carries no line numbers.
# A blast radius is WHO DEPENDS ON the changed symbol. The op that answers exactly that, symbol-
# scoped and directed, is `query_graph_tool(pattern="callers_of")`; mokata walks it transitively to
# `depth` hops — the same question the AST floor's `_blast_radius_bounded` answers (transitive
# callers), so the two backends agree on what the word means.
#   * `importers_of` is NOT walked: on 2.3.9 it resolves a symbol to its FILE and returns every
#     importer of that file, so `from m import other` would count as depending on `m.fn` — a file-
#     granular over-report. `references_to` (symbol-scoped) returned 0 for a Python `from m import
#     fn` on the measured repo. A caller of `fn` is what a change to `fn` can break.
#   * `status: "not_found"` / `"ambiguous"` on the TARGET raises `GraphNotIndexed`: the graph is
#     healthy and cannot speak for the symbol, so the layer answers from the AST floor and says so.
#     An INDEXED symbol with zero callers is a real structural zero and stays one.
# Bounded like the AST floor (0.0.25 K): a reference cap, a cap on symbols expanded (each is one MCP
# round trip), and a wall-clock budget. A bound that bites is disclosed (`CrgRows.cut_why`).
CRG_BLAST_MAX_REFS = 20000
CRG_BLAST_MAX_EXPANSIONS = 256
CRG_BLAST_TIME_BUDGET_S = 10.0


# 0.0.25 K — how many rows mokata asks `query_graph_tool` for. The tool's own default is 100 and it
# reports what it cut (`result_count` / `results_omitted`); mokata passed nothing, so every hub answer
# was the first 100 call sites, and `_normalize` threw the counts away — a partial answer presented
# as complete. The cap is now mokata's (sized to the IDE's 2000-node graph bound) and what is still
# cut is carried onto the QueryResult (`CrgRows` → `mark_truncated`). Verified against the real
# 2.3.9 schema: `query_graph_tool(pattern, target, repo_root, detail_level, max_results,
# resolution)`; `traverse_graph_tool` takes NO `max_results` (it is bounded by `token_budget` and
# reports `truncated`), so nothing is passed there.
CRG_MAX_RESULTS = 2000

# 0.0.26 O13 — on a million-line repo a bare name is usually AMBIGUOUS. Measured on kubernetes:
# `NewSharedInformerFactoryWithOptions` matches 12 nodes (one per generated client), so every
# query by name fell to the grep floor although the graph was built. The floor answers "every
# symbol with this NAME" anyway, so the graph now answers the same question exactly: the union over
# the candidates it names, up to this many (a cut is disclosed as truncation).
CRG_AMBIGUOUS_MAX = 64


class CrgRows(list):
    """The normalized rows of ONE code-review-graph answer, plus what the tool said it LEFT OUT.

    A `list` (the `GraphQueryClient` protocol is unchanged for every consumer that iterates it) that
    also carries `truncated` and `total` — the tool's own `result_count` / `total_impacted` when it
    reports one. `CodeReviewGraphBackend.query` reads them onto the QueryResult."""

    truncated: bool = False
    total: Optional[int] = None
    cut_why: str = ""               # which bound cut it, when mokata's own walk did (blast radius)
    definitions: Optional[int] = None   # P35: an ambiguous name answered as a union over N nodes
    candidates: List[Dict[str, Any]] = []


# Version-compat handshake bounds (semver, inclusive lower / exclusive upper-major). CRG exposes
# no schema version, so the handshake is on the CLI `--version` string. The adopted-tool contract
# mokata maps was grounded against the 2.x line.
# 0.0.26 O15: the range lives in `crg_resolve` (import-light: resolution runs on hook paths).
from ..crg_resolve import CRG_MAX_MAJOR, CRG_MIN_VERSION, version_out_of_range  # noqa: E402,F401


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
    why = version_out_of_range(v)
    if why:
        raise CrgVersionSkew(why)
    return v


def _candidate(c: Dict[str, Any], root: str) -> Dict[str, Any]:
    """One definition an ambiguous name matched, repo-relative (P35 G28): enough for a reader to
    show "18 definitions" and ask again for exactly one by qualified name."""
    line = c.get("line_start", c.get("line"))
    try:
        line = int(line) if line is not None else None
    except (TypeError, ValueError):
        line = None
    return {"qualified_name": _rel(str(c.get("qualified_name") or ""), root),
            "path": _rel(str(c.get("file_path") or c.get("file") or ""), root),
            "line": line, "kind": c.get("kind")}


def _rel(value: str, root: str) -> str:
    """CRG reports ABSOLUTE paths (`/repo/pkg/m.py`, `/repo/pkg/m.py::fn`). Strip `root` off the
    front so the answer is repo-relative and `/`-separated, like every floor's. A value outside
    `root` (or a bare name) is returned unchanged — never guessed at."""
    if not value or not root:
        return value
    path, sep, rest = value.partition("::")
    if not os.path.isabs(path):
        return value
    from ..repo_paths import name_of
    try:
        rel = str(name_of(os.path.realpath(path), os.path.realpath(os.path.abspath(root))))
    except (OSError, ValueError):     # ValueError: different drives on Windows
        return value
    if rel == ".." or rel.startswith("../"):
        return value
    return rel + sep + rest


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
        index_probe: Optional[Callable[[str], bool]] = None,
        nodes: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.name = name
        self.root = root
        self.timeout = timeout
        self._call_tool = call_tool or self._default_call_tool
        self._run_cli = run_cli or self._default_run_cli
        # 0.0.26 O13 — the REAL tool's index lives on disk in the repo; an injected transport (a
        # test double, a recorded reply) speaks for its own index, so it is taken as built unless a
        # probe is given. `can_build_index` says whether this client may start a real build.
        self.can_build_index = call_tool is None and run_cli is None
        if index_probe is None:
            if call_tool is None:
                from .crg_build import index_built as index_probe
            else:
                index_probe = lambda _root: True          # noqa: E731
        self._index_probe = index_probe
        self._index_ok = False
        # P35 — root -> `crg_nodes.NodeReader` (or None): the node table `defs` is read from.
        if nodes is None and call_tool is None:
            from .crg_nodes import reader as nodes
        self._nodes = nodes

    # --- the index (0.0.26 O13) ----------------------------------------------------------
    def index_ready(self, root: str) -> bool:
        """Whether this repo's index is BUILT (code-review-graph's own record). A positive answer
        is remembered for the client's life — an index does not un-build itself — and forgotten
        the moment the tool says its graph is empty."""
        if self._index_ok:
            return True
        try:
            self._index_ok = bool(self._index_probe(root))
        except Exception:                              # noqa: BLE001 — unknown is "not built"
            self._index_ok = False
        return self._index_ok

    def _require_index(self, root: str) -> None:
        """Refuse to ask a graph that has no index: no `serve` is started (it would only create an
        empty DB in the repo and answer "graph is empty" forever — O11 F1), and the layer answers
        from the floor while the build runs."""
        if not self.index_ready(root):
            raise GraphIndexUnbuilt("code-review-graph has no index for this repo yet")

    def _empty_graph(self, payload: Dict[str, Any]) -> bool:
        why = str(payload.get("confidence") or "")
        if why.startswith("graph is empty"):
            self._index_ok = False
            return True
        return False

    # --- typed queries (GraphQueryClient protocol) --------------------------------------
    def supports_kind(self, kind: str) -> bool:
        """CRG-NAV (b) — whether the REAL code-review-graph interface has an op for `kind`.
        False for `defs` (no definition-site pattern exists); the layer reads this and answers
        that one kind from the floor with an honest note, so a MAPPING gap is never reported as
        a graph FAILURE and never costs a wasted recovery attempt."""
        if kind == "defs":
            return self._nodes is not None
        return kind in _KIND_TO_PATTERN or kind in ("blast_radius", "refs")

    def query(self, kind: str, target: str, root: str, depth: int = 1) -> List[Dict[str, Any]]:
        self._require_index(root)
        if kind == "defs":
            return self._defs(target, root)
        if kind == "refs":
            return self._refs(target, root)
        if kind == "blast_radius":
            return self._blast_radius(target, root, depth)
        else:
            pattern = _KIND_TO_PATTERN.get(kind)
            if pattern is None:
                raise CrgUnavailable(f"code-review-graph has no mapping for kind '{kind}'")
            payload = self._ask(pattern, target, root)
        return self._normalize(self._answered(payload, pattern, target), root)

    def _ask(self, pattern: str, target: str, root: str) -> Dict[str, Any]:
        """`query_graph_tool(pattern, target)`, with an AMBIGUOUS name answered as the union over
        the candidates the tool lists (see `CRG_AMBIGUOUS_MAX`). Anything else is returned as-is."""
        payload = self._call_tool("query_graph_tool", {
            "pattern": pattern, "target": target, "repo_root": root,
            "max_results": CRG_MAX_RESULTS})
        if not (isinstance(payload, dict) and payload.get("status") == "ambiguous"):
            return payload
        cands = self._exact_candidates(payload, target, root)
        names = [c.get("qualified_name") for c in cands]
        if not names:
            return payload
        merged: Dict[str, Any] = {"status": "ok", "results": [], "edges": [],
                                  "ambiguous_candidates": len(names),
                                  "candidates": [_candidate(c, root) for c in cands]}
        if len(names) > CRG_AMBIGUOUS_MAX:
            merged["truncated"] = True
        answered = 0
        from .cancel import checkpoint, report
        cap = min(len(names), CRG_AMBIGUOUS_MAX)
        for i, qn in enumerate(names[:CRG_AMBIGUOUS_MAX]):
            checkpoint()                     # P32: a cancelled job stops between candidates
            report(note=f"{target}: {cap} definitions, asking {i + 1}/{cap}")
            part = self._call_tool("query_graph_tool", {
                "pattern": pattern, "target": qn, "repo_root": root,
                "max_results": CRG_MAX_RESULTS})
            if not isinstance(part, dict) or part.get("status", "ok") != "ok":
                continue
            answered += 1
            merged["results"].extend(part.get("results") or [])
            merged["edges"].extend(part.get("edges") or [])
            if part.get("truncated") or (part.get("results_omitted") or 0):
                merged["truncated"] = True
        # No candidate answered: that is not a zero — keep the tool's own "ambiguous" verdict.
        return merged if answered else payload

    def _exact_candidates(self, payload: Dict[str, Any], target: str,
                          root: str) -> List[Dict[str, Any]]:
        """0.0.26 P35 (G28) — the definitions an ambiguous name REALLY names. code-review-graph's
        candidate list is a fuzzy full-text search capped at 20 (`search_nodes`): on kubernetes
        `HandleError` listed `HandleErrorWithContext` & co., so the O13 union merged callers of
        OTHER functions into the answer. Only exact-name candidates are kept — all of them, from
        the node table when the client has one (the tool's list is capped), else the tool's own
        list filtered to the exact name."""
        name = target.rsplit("::", 1)[-1]
        reader = None
        if self._nodes is not None:
            reader = self._nodes(root)
        if reader is not None:
            try:
                return [{"qualified_name": d["graph_id"], "file_path": d["path"],
                         "line_start": d["line"], "kind": d["kind"]}
                        for d in reader.defs(name)]
            except (sqlite3.Error, OSError, ValueError):
                pass                                    # unreadable table: the tool's own list
        out = []
        for c in payload.get("candidates") or []:
            if not isinstance(c, dict) or not c.get("qualified_name"):
                continue
            qn = str(c["qualified_name"])
            tail = qn.rsplit("::", 1)[-1]
            if c.get("name") == name or tail == name or tail.endswith("." + name):
                out.append(c)
        return out

    def _answered(self, payload: Any, pattern: str, target: str) -> Dict[str, Any]:
        """0.0.26 O11 (`CRG-ZERO-FROM-NO-ANSWER`) — a `query_graph_tool` payload that is NOT an
        answer never normalizes to rows. Measured on 2.3.9: a graph that indexes nothing (a repo
        it never built, or the symbol absent) replies `status: "not_found"` with confidence "graph
        is empty: nothing is indexed, so this 0 says nothing about the code"; `_normalize` read
        that as ZERO callers — basis structural — and the IDE's ripple said a 30-caller hub had no
        callers. Only `_blast_radius` checked the status; callers/callees/imports/implementers/
        refs did not. Now one rule for every pattern: not_found/ambiguous on the target is
        `GraphNotIndexed` (the layer answers from the floor and says so), `error` is
        `CrgUnavailable` (degrade loudly). An `ok` answer with zero rows stays a real zero."""
        payload = payload if isinstance(payload, dict) else {}
        status = payload.get("status", "ok")
        if status in ("not_found", "ambiguous"):
            why = payload.get("confidence") or payload.get("summary") or status
            if self._empty_graph(payload):
                # 0.0.26 O13 — NOTHING is indexed (the build has not run, or is running): not a
                # statement about `target`. The backend starts the build; the floor answers.
                raise GraphIndexUnbuilt(f"code-review-graph's index is empty ({why})")
            raise GraphNotIndexed(
                f"code-review-graph holds no single node for `{target}` ({status}: {why})")
        if status == "error":
            raise CrgUnavailable(
                f"code-review-graph {pattern}({target}) failed: {payload.get('error')}")
        return payload

    def _blast_radius(self, target: str, root: str, depth: int) -> "CrgRows":
        """0.0.26 O2 — transitive CALLERS of `target` up to `depth` hops, over `callers_of`.

        Hop 1 asks for `target` as the user spelled it; every deeper hop asks for the caller's
        own `qualified_name` (exact node — never a fuzzy re-resolution). One row per call site,
        de-duplicated on (path, line). See the module comment above `CRG_BLAST_MAX_REFS` for why
        this op and not `traverse_graph_tool` / `importers_of`."""
        hops = max(1, int(depth))
        rows = CrgRows()
        seen_sites: set = set()
        expanded = {target}
        frontier = [target]
        asked = 0
        deadline: Optional[float] = None
        for hop in range(1, hops + 1):
            nxt: List[str] = []
            for sym in frontier:
                from .cancel import checkpoint
                checkpoint()                 # P32: a cancelled job stops between expansions
                if asked >= CRG_BLAST_MAX_EXPANSIONS or (
                        deadline is not None and time.monotonic() > deadline):
                    rows.truncated = True
                    rows.cut_why = (
                        f"blast radius stopped during hop {hop} of {hops} at the "
                        + (f"{CRG_BLAST_MAX_EXPANSIONS}-symbol expansion cap"
                           if asked >= CRG_BLAST_MAX_EXPANSIONS
                           else f"{CRG_BLAST_TIME_BUDGET_S:g}s time budget")
                        + f" with {len(rows)} reference(s); deeper callers were not walked")
                    return rows
                payload = (self._ask("callers_of", sym, root) if hop == 1 else
                           self._call_tool("query_graph_tool", {
                               "pattern": "callers_of", "target": sym, "repo_root": root,
                               "max_results": CRG_MAX_RESULTS}))
                asked += 1
                if deadline is None:
                    # The budget starts AFTER the first answer: that one pays the serve cold start.
                    deadline = time.monotonic() + CRG_BLAST_TIME_BUDGET_S
                payload = payload if isinstance(payload, dict) else {}
                status = payload.get("status", "ok")
                if status in ("not_found", "ambiguous"):
                    if hop == 1:
                        why = payload.get("confidence") or payload.get("summary") or status
                        if self._empty_graph(payload):
                            raise GraphIndexUnbuilt(f"code-review-graph's index is empty ({why})")
                        raise GraphNotIndexed(
                            f"code-review-graph holds no single node for `{target}` "
                            f"({status}: {why})")
                    continue          # a graph-supplied qualified name that no longer resolves
                if status == "error":
                    raise CrgUnavailable(
                        f"code-review-graph callers_of({sym}) failed: {payload.get('error')}")
                part = self._normalize(payload, root)
                if part.truncated:
                    rows.truncated = True
                for row in part:
                    key = (row["path"], row["line"])
                    if key in seen_sites:
                        continue
                    if len(rows) >= CRG_BLAST_MAX_REFS:
                        rows.truncated = True
                        rows.cut_why = (f"blast radius stopped at the {CRG_BLAST_MAX_REFS}-"
                                        f"reference cap during hop {hop} of {hops}; more callers "
                                        "exist and were not walked")
                        return rows
                    seen_sites.add(key)
                    row["edge_type"] = row.get("edge_type") or "CALLS"
                    row["metadata"]["hop"] = hop
                    rows.append(row)
                    qn = row["metadata"].get("qualified_name")
                    if qn and qn not in expanded:
                        expanded.add(qn)
                        nxt.append(qn)
            frontier = nxt
            if not frontier:
                break
        if rows.truncated and not rows.cut_why:
            rows.cut_why = (f"code-review-graph capped a callers_of answer at {CRG_MAX_RESULTS} "
                            "rows inside this walk")
        return rows

    def _defs(self, target: str, root: str) -> "CrgRows":
        """P35 — every definition named `target`, from the graph's node table (see the note above
        `_REFS_PATTERNS`). None found is `GraphNotIndexed`: the graph holds no node by that name,
        which is not a structural zero (a dynamically created name is the floor's to find)."""
        reader = self._nodes(root) if self._nodes is not None else None
        if reader is None:
            raise GraphIndexUnbuilt("code-review-graph has no index for this repo yet")
        name = target.rsplit("::", 1)[-1]
        found = reader.defs(name)
        if "::" in target:
            want = target
            exact = [d for d in found if d["qualified_name"] == want
                     or want.endswith("/" + d["qualified_name"])]
            found = exact or found
        if not found:
            raise GraphNotIndexed(f"code-review-graph holds no definition named `{target}`")
        rows = CrgRows()
        for d in found:
            rows.append({"path": d["path"], "line": d["line"], "snippet": "",
                         "symbol": d["qualified_name"], "edge_type": "DEFINES",
                         "metadata": {"qualified_name": d["qualified_name"], "kind": d["kind"],
                                      "end": d["end"], "language": d["language"]}})
        return rows

    def _refs(self, target: str, root: str) -> List[Dict[str, Any]]:
        """CRG-NAV (b) — `refs` over the union of the inbound patterns, de-duplicated on
        (path, line, symbol) so a symbol that is both called and imported in one place is ONE
        reference. A pattern that finds nothing contributes nothing; a pattern that FAILS raises
        (CrgUnavailable) and the whole answer degrades to the floor, exactly as a single-pattern
        kind would — the composition adds no new failure mode."""
        rows = CrgRows()
        seen = set()
        for pattern in _REFS_PATTERNS:
            payload = self._ask(pattern, target, root)
            part = self._normalize(self._answered(payload, pattern, target), root)
            # Any pattern that was cut makes the union partial; the union's TRUE size is unknown
            # (the cut rows may duplicate kept ones), so no total is claimed for it.
            if part.truncated:
                rows.truncated = True
            for row in part:
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
        symbol is NOT a false positive, unlike inferring existence from caller counts).
        0.0.26 O13: an unbuilt index raises `GraphIndexUnbuilt` (the backend reads any raise as
        "exists") — an empty graph's `not_found` must never flag every symbol as drifted."""
        self._require_index(root)
        payload = self._call_tool("query_graph_tool", {
            "pattern": "callers_of", "target": symbol, "repo_root": root})
        status = (payload or {}).get("status", "ok")
        return status != "not_found"

    def semantic(self, query: str, root: str, kind: Optional[str] = None,
                 limit: int = 20) -> List[Dict[str, Any]]:
        self._require_index(root)
        params: Dict[str, Any] = {"query": query, "limit": int(limit), "repo_root": root}
        if kind:
            params["kind"] = kind
        payload = self._call_tool("semantic_search_nodes_tool", params)
        return self._normalize(payload, root)

    # --- operational acts (run-state; no durable/manifest write) ------------------------
    def refresh(self, root: str, full: bool = False) -> bool:
        """Rebuild (`build`) or incrementally refresh (`update`) the CRG index. An OPERATIONAL
        act (GR.S2(k)) — never a durable/manifest write.

        0.0.26 O13 (F4): `update` runs ONLY on an index that exists — on an unbuilt one it is a
        full build in disguise, run by whichever freshness pass got there first, unbounded by
        anything but the 30 s CLI timeout. The first build belongs to `crg_build` (setup, first
        use, `mokata graph build`): for the real tool a full rebuild goes through `build_now`, under
        the repo's build lock and its generous timeout, so two builds can never overlap."""
        if not full and not self.index_ready(root):
            return False
        if full and self.can_build_index:
            from .crg_build import build_now
            ok = build_now(root, full=True).ok
            self._index_ok = ok
            return ok
        if self.can_build_index:
            # 0.0.26 P37 (R39) — the freshness `update` of the real tool goes through `build_now`
            # too: the repo's build lock, NO time limit (the 30 s CLI timeout killed updates on
            # kubernetes mid-write and left a complete index marked `in-progress`), progress and
            # Cancel from the job it runs in, and the update recorded per attempt, so a killed one
            # never demotes the complete index it started from (`crg_build.update_state`).
            from .crg_build import build_now
            res = build_now(root)
            if res.busy is not None:
                return True      # another process is building/updating it: answer from this one
            return res.ok
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
        """The tool's version. A SUCCESSFUL answer is remembered for this client's life (0.0.25
        `IDE-SERVER-LIGHT`): `--version` is a Python cold start, and the IDE's long-lived client
        used to pay it on every query. A failure is never cached — the next call asks again."""
        cached = getattr(self, "_version", None)
        if cached:
            return cached
        rc, out, err = self._run_cli(["--version"])
        if rc != 0:
            return None
        self._version = parse_crg_version(out) or parse_crg_version(err)
        return self._version

    def health(self, root: str) -> bool:
        """A cheap liveness probe. With a live `serve` session the answer is that process being
        alive (no spawn at all); otherwise the tool answers `--version`. (The backend's query
        path is what ultimately proves the serve/index is live; this is the fast pre-check.)"""
        session = getattr(self, "_session", None)
        proc = getattr(session, "_proc", None)
        if proc is not None and proc.poll() is None:
            return True
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
    def _cut(payload: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
        """0.0.25 K — what the tool says it LEFT OUT of `payload`: `(truncated, total)`.

        Read from the grounded 2.3.9 shapes: `query_graph_tool` → `result_count` +
        `results_omitted`; `get_impact_radius_tool` → `truncated` + `total_impacted` +
        `nodes_omitted`; `traverse_graph_tool` → `truncated` (token budget, no total). A field
        that is absent or malformed says nothing — it never manufactures a cut."""
        def _int(v: Any) -> Optional[int]:
            if isinstance(v, bool):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        omitted = _int(payload.get("results_omitted"))
        if omitted is None:
            omitted = _int(payload.get("nodes_omitted"))
        total = _int(payload.get("result_count"))
        if total is None:
            total = _int(payload.get("total_impacted"))
        truncated = bool(payload.get("truncated") is True or (omitted or 0) > 0)
        return truncated, (total if truncated else None)

    @staticmethod
    def _normalize(payload: Dict[str, Any], root: str = ".") -> "CrgRows":
        """CRG node+edge results -> mokata row dicts (rich: edge_type + metadata). Defensive:
        CRG wrapper keys vary by tool, so we read `results`/`impacted_nodes` tolerantly and
        never raise on a missing field (a shape drift degrades to fewer fields, not a crash).
        0.0.25 K: returns `CrgRows`, which also carries what the tool reported it cut.

        0.0.26 O2 — read against the REAL 2.3.9 shape (see the module docstring): edges are keyed
        `source`/`target` (the old `*_qualified` keys never existed on the wire, so `edge_type` was
        always None); a call-site row's line is `call_site.line` (the CALL, not the caller's `def`);
        and CRG's absolute paths/qualified names are made relative to `root` so a graph answer
        names files the way every floor answer does. `metadata.qualified_name` keeps the tool's
        own id verbatim — that is what a follow-up query must send back."""
        if not isinstance(payload, dict):
            return CrgRows()
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
            for key in ("target", "source"):
                q = e.get(key)
                if q and k and q not in edge_kind:
                    edge_kind[q] = k
        rows = CrgRows()
        rows.truncated, rows.total = CodeReviewGraphClient._cut(payload)
        if payload.get("ambiguous_candidates"):
            rows.definitions = int(payload["ambiguous_candidates"])
            rows.candidates = list(payload.get("candidates") or [])
        for n in nodes:
            if not isinstance(n, dict):
                continue
            qname = n.get("qualified_name") or n.get("name") or ""
            file_path = n.get("file_path") or n.get("path") or ""
            site = n.get("call_site") if isinstance(n.get("call_site"), dict) else {}
            line = site.get("line", n.get("line_start", n.get("line", 0)))
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0
            meta: Dict[str, Any] = {
                "qualified_name": qname,
                "kind": n.get("kind"),
                "is_test": bool(n.get("is_test", False)),
            }
            if n.get("target_resolution"):
                meta["resolution"] = n.get("target_resolution")
            rows.append({
                "path": _rel(site.get("file") or file_path, root),
                "line": line,
                "snippet": "",
                "symbol": _rel(qname, root) or None,
                "edge_type": edge_kind.get(qname),
                "metadata": meta,
            })
        return rows

    # --- real transports (opt-in real-CRG CI leg validates these) -----------------------
    def _command(self) -> str:
        """The binary this client runs. 0.0.26 O15: for code-review-graph that is ONLY the
        resolved one (mokata's own environment, version-checked, or an explicit `MOKATA_CRG`) —
        never a bare name a subprocess would look up on PATH. Unresolved -> `CrgUnavailable`
        naming why (and any copy on PATH outside mokata's environment it refused to use)."""
        from ..detect import ENV_BOUND_COMMANDS, find_command
        path = find_command(self.name)
        if path:
            return path
        if self.name in ENV_BOUND_COMMANDS:
            from ..crg_resolve import resolve_crg
            raise CrgUnavailable(resolve_crg().describe())
        return self.name

    def _default_run_cli(self, args: List[str]) -> Tuple[int, str, str]:
        cmd = [self._command()] + list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=self.timeout, cwd=self.root,
                                  stdin=subprocess.DEVNULL, **hidden())
        except (OSError, subprocess.SubprocessError) as exc:
            raise CrgUnavailable(f"could not run {self.name}: {exc}") from exc
        # Secret-safety (B-VER precedent): CLI output for version/build/update carries no env
        # secret, but we never surface raw output — only the parsed version — so nothing leaks.
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def close(self) -> None:
        """Stop this client's `code-review-graph serve` session, if it has one (0.0.25
        `IDE-SERVER-LIGHT`). A long-lived owner (the IDE server's per-repo layer cache) calls this
        when it drops the client, so no serve process outlives the thing that used it. A POOLED
        session (O14b) belongs to the process's pool, not to this client: it is only forgotten."""
        session = getattr(self, "_session", None)
        self._session = None
        if session is not None and getattr(session, "pooled", False) is not True:
            session.close()

    def _default_call_tool(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        session = getattr(self, "_session", None)
        if session is None:
            command = self._command()
            session = _pooled_session(command, self.root, self.timeout)
            if session is None:
                session = _McpStdioSession(command, root=self.root, timeout=self.timeout)
            self._session = session
        try:
            return session.call(tool, params)
        except CrgTimeout:
            _drop_pooled(session)
            # A1 — the SECOND half of killing a desynchronised session, and it is not the same
            # half as `close()`. `close()` disposes of the PROCESS; this disposes of the
            # CONVERSATION — the object holding the request-id counter and the dead pipes, cached
            # HERE and reachable long after the process is gone. Dropping it is what guarantees
            # the next call builds a session from scratch rather than resuming a numbering the
            # server on the other end never saw the end of.
            self._session = None
            raise


# ======================================================================================
# O14b — ONE `serve` session per (command, repo) for a long-lived process
# ======================================================================================
# Measured 2026-10-22 on a django copy: every MCP call that touched the graph built a fresh
# `KnowledgeLayer` -> client -> `code-review-graph serve`, and nothing closed it when the call's
# layer was dropped (the session is held in a reference cycle with its stderr drain), so each call
# left one more live `serve` process behind until the MCP server exited (+1 per `query`), and every
# call paid the serve start-up again (~6-9 s on that repo). A long-lived process (the MCP server)
# turns the pool on at start: clients then share one session per repo, which is what the IDE
# server's per-repo layer cache already does. Off by default — a CLI command or a hook is one call
# and exits. A desynchronised (timed-out) session is dropped from the pool and the next call starts
# a clean one, exactly as before.
_POOL_LOCK = threading.Lock()
_POOL: Optional[Dict[Tuple[str, str], "_McpStdioSession"]] = None


def enable_session_pool() -> None:
    """Share one `serve` session per (command, repo) for the rest of this process's life."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = {}
            import atexit
            atexit.register(close_session_pool)


def close_session_pool() -> None:
    """Stop every pooled session and turn pooling off (process exit, tests)."""
    global _POOL
    with _POOL_LOCK:
        sessions, _POOL = list((_POOL or {}).values()), None
    for session in sessions:
        session.close()


def _pooled_session(command: str, root: str, timeout: float) -> Optional["_McpStdioSession"]:
    with _POOL_LOCK:
        if _POOL is None:
            return None
        key = (command, os.path.realpath(root or "."))
        session = _POOL.get(key)
        if session is None:
            session = _POOL[key] = _McpStdioSession(command, root=root, timeout=timeout)
            session.pooled = True
        return session


def _drop_pooled(session: "_McpStdioSession") -> None:
    with _POOL_LOCK:
        if _POOL is None:
            return
        for key, held in list(_POOL.items()):
            if held is session:
                del _POOL[key]


def client_info() -> Dict[str, str]:
    """The MCP `clientInfo` mokata sends: its name AND its version (both required by the schema)."""
    from .. import __version__
    return {"name": "mokata", "version": str(__version__)}


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
        self.pooled = False                  # O14b — owned by the process pool, not one client
        # 0.0.25 `IDE-SERVER-LIGHT`: the IDE now keeps ONE session per repo for its lifetime and
        # answers requests on worker threads. A JSON-RPC exchange is write-then-read on one pipe
        # pair, so two threads interleaving would hand each other's answers back — the
        # desynchronised-session failure this class already kills sessions over. One at a time.
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            self._proc = subprocess.Popen(
                [self.command, "serve", "--repo", self.root],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, **hidden())
        except (OSError, subprocess.SubprocessError) as exc:
            raise CrgUnavailable(f"could not start {self.command} serve: {exc}") from exc
        # A1 — stderr was `DEVNULL`, which threw away the subprocess's own account of why it hung;
        # the people who filed #45/#46/#53 could not say what happened because the code discarded
        # it. It is now captured — but DRAINED CONTINUOUSLY, never left sitting in a pipe: an
        # unread `PIPE` fills its ~64 KiB buffer and blocks the server on its next write, which
        # would be a brand-new hang introduced by the fix for a hang.
        self._stderr = BoundedStderrTail(self._proc.stderr)
        # 0.0.25 K — `clientInfo.version` is REQUIRED by the MCP schema, and code-review-graph
        # 2.3.9 enforces it: without it `initialize` is rejected with -32602, every CRG query raised
        # `CrgUnavailable`, and every answer silently fell back to the AST floor.
        self._rpc("initialize", {"protocolVersion": "2024-11-05",
                                 "capabilities": {}, "clientInfo": client_info()})
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
        from .cancel import Cancelled, cancelled
        read = read_line_bounded(proc.stdout, self.timeout, cancelled)
        if read.outcome == READ_CANCELLED:
            self.close()                     # its answer would arrive desynchronised
            raise Cancelled("cancelled while code-review-graph was answering")
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
        with self._lock:
            self._ensure()
            result = self._rpc("tools/call", {"name": tool, "arguments": params})
        # MCP tool results wrap content; CRG returns a JSON text block.
        content = result.get("content") or []
        if result.get("isError") is True:
            # 0.0.26 O11 — an MCP tool ERROR is not an empty answer. Measured on 2.3.9: a repo_root
            # with no .git/.code-review-graph replies `isError: true` with a text block ("repo_root
            # does not look like a project root"); that text is not JSON, so the loop below fell
            # through and returned the wrapper, which `_normalize` read as zero rows — a silent
            # structural zero. It is the tool failing to answer: degrade, loudly.
            texts = [str(b.get("text") or "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            detail = " ".join(texts).strip()[:300] or "(no detail)"
            try:
                from ..govern.secrets import scan
                if scan(text=detail):
                    detail = "(withheld: the error contained secret-shaped content)"
            except Exception:                # fail CLOSED, as `_stderr_tail` does
                detail = "(withheld: the error could not be checked for secrets)"
            raise CrgUnavailable(f"code-review-graph {tool} returned an error: {detail}")
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
