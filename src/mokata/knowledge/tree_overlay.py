"""P41 MCP-FOLLOWS-WORKTREE — answer a code question for a linked worktree, from the main graph.

A session works in `.claude/worktrees/<name>/` (P31); the code graph (code-review-graph's index, the
AST floor's parse cache) is built on the MAIN checkout. Re-building either per worktree would cost a
full index per session and write inside the worktree (P31: nothing is ever written there). So a
worktree question is answered as:

    the main checkout's graph, with every file the worktree CHANGED taken out of it and re-read from
    the worktree — "graph + N files from your worktree".

THE CHANGED SET (`tree_changes`) is everything in which the worktree's files can differ from the main
checkout's: the commits between the main checkout's HEAD and the worktree's HEAD (a tree-to-tree
diff, ~3 ms) plus the worktree's uncommitted and untracked files (ONE `git status`, the same stat
pass P31 measured). Measured on kubernetes (31,461 files): 120-170 ms. The main checkout's own
uncommitted edits are not subtracted: under P31's `always` policy the main checkout is not edited.

HOW EACH BACKEND IS OVERLAID (`TreeLayer.over`)
  * the AST floor   -> `OverlayAstBackend`: the main index with the changed files' entries REPLACED
                       by a parse of the worktree's copies (and deleted files dropped). Every query
                       kind, the blast radius and the verified-zero rules run unchanged on the merged
                       index, so a worktree answer is exactly the answer a fresh index of the
                       worktree would give — without re-parsing the other files.
  * the grep floor  -> a grep floor rooted at the worktree (it reads files live; nothing to merge).
  * an adopted graph (code-review-graph) -> `OverlayGraphBackend`: the graph's answer with every
                       reference INTO a changed file removed, plus that kind's edges from the changed
                       files (AST-parsed where the floor can parse them, lexical otherwise — and a
                       lexical addition demotes the answer, said). A blast radius walks on through
                       the changed files' callers and asks the graph once per symbol the worktree
                       introduced into the walk (bounded).

Every answer carries the label in its note; an overlay that could not read the worktree says so and
answers from the main graph alone — never silently main-only.

Read-only: nothing here writes a cache, an index or a file. Copyright 2026 MoStack. Licensed under
the Apache License, Version 2.0.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..repo_paths import path_of
from .ast_backend import AstBackend, FileEdges, OverlayAstBackend
from .grep_backend import SOURCE_EXTENSIONS, GrepBackend
from .layer import KnowledgeLayer, _join_note
from .query import (BASIS_STRUCTURAL, BackendError, GraphBackend, QueryResult, Reference,
                    mark_truncated)

#: The label every worktree answer carries.
TREE_NOTE = "graph + {n} file{s} from your worktree {label}"
TREE_NOTE_CLEAN = "graph — your worktree {label} has no changes against the main checkout"
TREE_NOTE_FAILED = ("your worktree {label}'s changes could not be read ({why}) — answered from the "
                    "main checkout's graph WITHOUT them")
#: A reference that came from a worktree file is marked in its metadata.
WORKTREE_META = "worktree"

#: The most changed files one overlay takes (P31's `MAX_CHANGED_FILES`); past it the answer says so.
MAX_OVERLAY_FILES = 2000
#: A blast radius asks the graph once per symbol the WORKTREE brought into the walk, at most this
#: many times, within this many seconds; past either bound it is marked truncated.
MAX_GRAPH_EXPANSIONS = 25
OVERLAY_BLAST_BUDGET_S = 2.0
#: An untracked directory is expanded to its files with a bounded walk.
MAX_UNTRACKED_WALK = 5000


# ============================================================================ the changed set
def _in_source(rel: str) -> bool:
    """Whether `rel` is in the corpus the main graph indexes: no hidden or install directory on
    the way (the same rule `repo_walk.prune_source_dirs` applies to a walk)."""
    from ..languages import VENDOR_DIRS
    parts = rel.split("/")
    return not any(p.startswith(".") or p in VENDOR_DIRS for p in parts[:-1]) and bool(parts[-1])


def _parse_status(raw: bytes) -> List[Tuple[str, str]]:
    """`git status --porcelain -z --no-renames` -> [(status, rel)] with status M / D / ?."""
    out: List[Tuple[str, str]] = []
    for entry in raw.decode("utf-8", errors="replace").split("\0"):
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        out.append(("?" if xy == "??" else "D" if "D" in xy else "M", rel))
    return out


def _expand_dir(tree: str, rel_dir: str, budget: List[int]) -> List[str]:
    from ..repo_paths import name_of
    from ..repo_walk import prune_source_dirs
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(path_of(tree, rel_dir.rstrip("/"))):
        prune_source_dirs(dirpath, dirnames)
        for fn in filenames:
            if budget[0] <= 0:
                return out
            budget[0] -= 1
            out.append(str(name_of(os.path.join(dirpath, fn), tree)))
    return out


def tree_changes(tree: str, main: str) -> Tuple[Dict[str, str], bool, str]:
    """`({rel: status}, ok, why)` — every file in which `tree`'s working copy can differ from the
    main checkout's HEAD (see the module doc). Never raises."""
    from .. import session_worktrees as SW
    main_head, tree_head = SW.head_of(main), SW.head_of(tree)
    if not main_head or not tree_head:
        return {}, False, "no HEAD to compare against"
    out: Dict[str, str] = {}
    if main_head != tree_head:
        code, txt = SW._git(tree, "diff", "--name-status", "-z", "--no-renames",
                            "--end-of-options", main_head, tree_head)
        if code != 0:
            return {}, False, f"git diff failed: {txt.strip()[:160]}"
        for st, rel in SW._parse_name_status(txt):
            out[rel] = st
    code, raw = SW._git_bytes(tree, "status", "--porcelain", "-z", "--no-renames",
                              "--untracked-files=normal")
    if code != 0:
        return {}, False, "git status failed"
    budget = [MAX_UNTRACKED_WALK]
    for st, rel in _parse_status(raw):
        if st == "?" and rel.endswith("/"):
            for f in _expand_dir(tree, rel, budget):
                out[f] = "?"
            continue
        out[rel] = st
    return {r: s for r, s in out.items() if _in_source(r)}, True, ""


# ============================================================================ one snapshot
@dataclass
class Snapshot:
    """The worktree's changed files at one moment, parsed."""

    version: int
    changed: Dict[str, str] = field(default_factory=dict)   # rel -> status (every changed file)
    edges: Dict[str, FileEdges] = field(default_factory=dict)  # rel -> edges (AST-parsed files)
    lexical: List[str] = field(default_factory=list)        # rels the floor cannot parse
    ok: bool = True
    why: str = ""
    total: int = 0                                          # changed files before the cap
    small: Optional[AstBackend] = None                      # an index over `edges` only

    @property
    def truncated(self) -> bool:
        return self.total > len(self.changed)


def _parseable(rel: str) -> bool:
    from . import ts_edges
    return rel.endswith(".py") or (ts_edges.available() and ts_edges.is_ts_source(rel))


class Overlay:
    """The worktree's changes, recomputed at most once per `window` seconds (None: once)."""

    def __init__(self, tree: str, main: str, window: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.tree, self.main, self.window, self.clock = tree, main, window, clock
        self._lock = threading.Lock()
        self._snap: Optional[Snapshot] = None
        self._at = 0.0
        self._version = 0
        self.last_ms = 0.0                      # what the last recompute cost (measured)

    def current(self) -> Snapshot:
        with self._lock:
            if self._snap is not None and (self.window is None
                                           or self.clock() - self._at < self.window):
                return self._snap
            t0 = time.perf_counter()
            self._version += 1
            self._snap = self._build(self._version)
            self._at = self.clock()
            self.last_ms = (time.perf_counter() - t0) * 1000.0
            return self._snap

    def invalidate(self) -> None:
        with self._lock:
            self._snap = None

    def _build(self, version: int) -> Snapshot:
        changed, ok, why = tree_changes(self.tree, self.main)
        total = len(changed)
        if total > MAX_OVERLAY_FILES:
            changed = dict(sorted(changed.items())[:MAX_OVERLAY_FILES])
        parser = AstBackend(root=self.tree, grep=GrepBackend(root=self.tree),
                            cache_dir=os.path.join(self.tree, "__unused__"))
        edges: Dict[str, FileEdges] = {}
        lexical: List[str] = []
        for rel in sorted(changed):
            ab = path_of(self.tree, rel)
            if not os.path.isfile(ab):
                continue                                   # deleted: its old edges just go
            if _parseable(rel):
                fe = parser._parse_file(ab)
                if fe is not None:
                    edges[rel] = fe
                    continue
            if rel.lower().endswith(SOURCE_EXTENSIONS):
                lexical.append(rel)
        return Snapshot(version=version, changed=changed, edges=edges, lexical=lexical, ok=ok,
                        why=why, total=total, small=AstBackend.of_edges(self.tree, edges))


# ============================================================================ an adopted graph
def _tag(refs: List[Reference]) -> List[Reference]:
    for r in refs:
        r.metadata = dict(r.metadata or {}, **{WORKTREE_META: True})
    return refs


def _lexical_query(tree: str, snap: Snapshot, kind: str, target: str) -> List[Reference]:
    if not snap.lexical:
        return []
    g = GrepBackend(root=tree)
    res = g.query(kind, target, depth=1, files=[path_of(tree, r) for r in snap.lexical])
    return list(res.references)


def _lexical_callers_of_many(tree: str, snap: Snapshot,
                             syms: List[str]) -> Dict[str, List[Reference]]:
    if not snap.lexical or not syms:
        return {}
    g = GrepBackend(root=tree)
    g._corpus_cache = {None: [path_of(tree, r) for r in snap.lexical]}
    try:
        return g._callers_of_many(syms)
    finally:
        g._corpus_cache = None


def _small_refs(snap: Snapshot, kind: str, target: str) -> List[Reference]:
    small = snap.small
    if small is None or not snap.edges:
        return []
    fn = {"callers": small._callers, "callees": small._callees, "defs": small._defs,
          "implementers": small._implementers, "imports": small._imports}.get(kind)
    return list(fn(target)) if fn is not None else []


class OverlayGraphBackend(GraphBackend):
    """An adopted graph's answers, with the worktree's changed files taken out and re-read."""

    def __init__(self, inner: GraphBackend, tree: str, overlay: Overlay) -> None:
        self.inner, self.tree, self.overlay = inner, tree, overlay
        self.name = inner.name
        self.is_graph = getattr(inner, "is_graph", False)

    def __getattr__(self, name: str) -> Any:
        if name in ("inner", "tree", "overlay"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    @property
    def root(self) -> str:
        return getattr(self.inner, "root", self.tree)

    def supports_kind(self, kind: str) -> bool:
        return self.inner.supports_kind(kind)

    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        res = self.inner.query(kind, target, depth=depth)
        return self._overlay(res, kind, target, depth)

    def blast_radius_full(self, target: str, depth: int = 1,
                          checkpoint: Optional[Callable[[], None]] = None) -> QueryResult:
        res = self.inner.blast_radius_full(target, depth=depth, checkpoint=checkpoint)
        return self._overlay(res, "blast_radius", target, depth)

    def semantic(self, query: str, kind: Optional[str] = None, limit: int = 20) -> QueryResult:
        res = self.inner.semantic(query, kind=kind, limit=limit)
        snap = self.overlay.current()
        kept = [r for r in res.references if r.path not in snap.changed]
        if len(kept) != len(res.references) or snap.changed:
            res.references = kept
            res.note = _join_note(res.note, f"{len(snap.changed)} file(s) your worktree changed "
                                            "are not in the semantic index and were left out")
        return res

    # --- the result-level overlay ------------------------------------------------------------
    def _overlay(self, res: QueryResult, kind: str, target: str, depth: int) -> QueryResult:
        snap = self.overlay.current()
        if not snap.ok or not snap.changed:
            return res
        changed = snap.changed
        kept = [r for r in res.references if r.path not in changed]
        lexical: List[Reference] = []
        cut = ""
        if kind == "blast_radius":
            structural, lexical, cut = self._blast(kept, target, depth, snap)
        elif kind == "refs":
            # "everywhere it is referenced" is lexical on every floor: read every changed source
            # file the worktree still has, parseable or not.
            structural = []
            present = [r for r in changed if r.lower().endswith(SOURCE_EXTENSIONS)
                       and os.path.isfile(path_of(self.tree, r))]
            lexical = _lexical_query(self.tree, Snapshot(version=snap.version, lexical=present),
                                     "refs", target)
        else:
            structural = _small_refs(snap, kind, target)
            lexical = _lexical_query(self.tree, snap, kind, target)
        seen = {(r.path, r.line, r.symbol) for r in kept}
        adds: List[Reference] = []
        for r in structural + lexical:
            k = (r.path, r.line, r.symbol)
            if k not in seen:
                seen.add(k)
                adds.append(r)
        res.references = kept + _tag(adds)
        added = {id(r) for r in adds}
        n_lex = sum(1 for r in lexical if id(r) in added)
        if n_lex and res.structural:
            res.demote_to_floor(f"{n_lex} reference(s) come from changed worktree files the AST "
                                "floor cannot parse, so they are lexical")
        elif adds and res.verified_empty:
            res.basis = BASIS_STRUCTURAL
        if cut:
            mark_truncated(res, None, cut)
        return res

    def _blast(self, kept: List[Reference], target: str, depth: int,
               snap: Snapshot) -> Tuple[List[Reference], List[Reference], str]:
        """Walk on from the graph's own answer through the worktree's changed files: callers in
        changed files of every symbol already in the radius, and — for a symbol the WORKTREE
        brought in — its callers from the graph (bounded)."""
        changed = snap.changed
        known: Set[str] = {target} | {r.symbol for r in kept if r.symbol}
        walked_by_graph = set(known)          # the graph's own radius already holds their callers
        seen = {(r.path, r.line) for r in kept}
        structural: List[Reference] = []
        lexical: List[Reference] = []
        frontier = sorted(known)
        asked, cut = 0, ""
        deadline = time.monotonic() + OVERLAY_BLAST_BUDGET_S
        for _hop in range(max(1, depth)):
            nxt: List[str] = []
            lex = _lexical_callers_of_many(self.tree, snap, frontier)
            for sym in frontier:
                found = [(r, False) for r in _small_refs(snap, "callers", sym)]
                found += [(r, True) for r in lex.get(sym, [])]
                if sym not in walked_by_graph:
                    if asked >= MAX_GRAPH_EXPANSIONS or time.monotonic() > deadline:
                        cut = cut or (f"the worktree overlay asked the graph for {asked} new "
                                      "symbol(s) and stopped; deeper callers were not walked")
                    else:
                        asked += 1
                        walked_by_graph.add(sym)
                        try:
                            more = self.inner.query("callers", sym, depth=1).references
                        except BackendError:
                            more = []
                        found += [(r, False) for r in more if r.path not in changed]
                for r, is_lex in found:
                    key = (r.path, r.line)
                    if key in seen:
                        continue
                    seen.add(key)
                    (lexical if is_lex else structural).append(r)
                    if r.symbol and r.symbol not in known:
                        known.add(r.symbol)
                        nxt.append(r.symbol)
            frontier = nxt
            if not frontier:
                break
        return structural, lexical, cut


# ============================================================================ the layer
class _MainFreshness:
    """The main graph's freshness, asked about the main checkout's part of an answer only (the
    worktree's references are re-read on every answer and are never "stale")."""

    def __init__(self, main_layer: KnowledgeLayer, overlay: Overlay) -> None:
        self.main_layer, self.overlay = main_layer, overlay

    def ensure_fresh(self, _layer: Any) -> Any:
        return self.main_layer.freshness.ensure_fresh(self.main_layer)

    def recheck_after_answer(self, _layer: Any, result: Any) -> bool:
        changed = self.overlay.current().changed
        view = copy.copy(result)
        view.references = [r for r in getattr(result, "references", []) if r.path not in changed]
        return self.main_layer.freshness.recheck_after_answer(self.main_layer, view)

    def __getattr__(self, name: str) -> Any:
        if name in ("main_layer", "overlay"):
            raise AttributeError(name)
        return getattr(self.main_layer.freshness, name)


def _wrap(backend: Optional[GraphBackend], tree: str, overlay: Overlay) -> Optional[GraphBackend]:
    if backend is None:
        return None
    if isinstance(backend, AstBackend):
        return OverlayAstBackend(backend, tree, overlay)
    if isinstance(backend, GrepBackend):
        return GrepBackend(root=tree, name=backend.name, extensions=backend.extensions)
    return OverlayGraphBackend(backend, tree, overlay)


class TreeLayer(KnowledgeLayer):
    """A worktree's knowledge layer: the main checkout's layer, overlaid (module doc)."""

    def __init__(self, main_layer: KnowledgeLayer, tree: str, overlay: Overlay) -> None:
        self.main_layer, self.tree, self.overlay = main_layer, tree, overlay
        fresh = (_MainFreshness(main_layer, overlay)
                 if getattr(main_layer, "freshness", None) is not None else None)
        super().__init__(_wrap(main_layer.primary, tree, overlay),
                         _wrap(main_layer.fallback, tree, overlay), index=None, freshness=fresh)

    @classmethod
    def over(cls, main_layer: KnowledgeLayer, tree: str,
             window: Optional[float] = None) -> "TreeLayer":
        from ..repo_identity import canonical_repo_root
        return cls(main_layer, tree, Overlay(tree, canonical_repo_root(tree), window=window))

    @property
    def pinned_graph_tool(self) -> Optional[str]:
        return self.main_layer.pinned_graph_tool

    def tree_note(self) -> str:
        from ..repo_identity import worktree_label
        snap = self.overlay.current()
        label = f"`{worktree_label(self.tree)}`"
        if not snap.ok:
            return TREE_NOTE_FAILED.format(label=label, why=snap.why)
        if not snap.changed:
            return TREE_NOTE_CLEAN.format(label=label)
        n = len(snap.changed)
        note = TREE_NOTE.format(n=n, s="" if n == 1 else "s", label=label)
        if snap.truncated:
            note += (f" (the first {n} of {snap.total} changed files; the rest answer from the "
                     "main checkout)")
        return note

    def _labelled(self, res: QueryResult) -> QueryResult:
        res.note = _join_note(res.note, self.tree_note())
        return res

    def _run(self, kind: str, target: str, depth: int = 1, budget: str = "default",
             checkpoint: Optional[Callable[[], None]] = None) -> QueryResult:
        return self._labelled(super()._run(kind, target, depth=depth, budget=budget,
                                           checkpoint=checkpoint))

    def semantic(self, query: str, kind: Optional[str] = None, limit: int = 20) -> QueryResult:
        return self._labelled(super().semantic(query, kind=kind, limit=limit))


__all__ = ["TreeLayer", "Overlay", "OverlayAstBackend", "OverlayGraphBackend", "tree_changes",
           "TREE_NOTE", "WORKTREE_META", "MAX_OVERLAY_FILES"]
