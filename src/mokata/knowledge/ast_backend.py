"""GR.S1 — the embedded stdlib-AST graph floor.

mokata builds no parser (doc 85 §6). But Python already ships one: this backend walks the
stdlib `ast` over a repo's `.py` files into typed edges — imports (import / from-import),
defs (functions / classes / methods), and calls (Name and best-effort Attribute) — and
answers every `QUERY_KINDS` query through the SAME typed API the grep floor and the adopted
graph use. It is NOT an in-house parser: it is Python's own AST walked into an edge graph,
the permitted zero-dependency floor ABOVE grep.

Honesty (P22). An AST-answered result is `degraded=False`: mokata answers "who calls this /
what breaks if I change it" from a real AST-derived edge graph, out of the box, zero external
deps. The note names the one limit — name-resolution, not type inference — so dynamic dispatch
it cannot see is documented, never overclaimed. Grep stays the EMERGENCY floor: a query with
no AST evidence (a target that lives only in non-`.py` code or in a file `ast.parse` rejects)
falls through to grep with `degraded=True`, exactly as before.

The floor is INCREMENTAL. Per-file edges are cached under `.mokata/temp_local/` keyed by
(mtime, size); an unchanged file is never re-parsed. The cache is transient/internal (24D) —
a missing or torn cache is a silent full re-parse, never a crash — and it stores symbols /
paths / lines only, never source text.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .grep_backend import GrepBackend
from .query import QUERY_KINDS, GraphBackend, QueryResult, Reference

# The one honest sentence an AST answer carries (P22): what it IS, and its one documented limit.
AST_NOTE = ("answered by the embedded AST floor (name-resolution, not type inference); "
            "dynamic dispatch is not resolved")

CACHE_DIRNAME = "knowledge_ast"
CACHE_FILENAME = "edges.json"
CACHE_SCHEMA_VERSION = 1


def parse_source(source: str) -> ast.AST:
    """Parse Python source with the stdlib parser. The single seam the incremental cache
    guards — it is called ONLY on a cache miss, so it is also the point a re-parse spy counts."""
    return ast.parse(source)


# --------------------------------------------------------------------- edge extraction (pure)
@dataclass
class FileEdges:
    """The typed edges from ONE `.py` file — symbols / lines only, never source text."""

    defs: List[Tuple[str, int, str]] = field(default_factory=list)          # (name, line, kind)
    classes: List[Tuple[str, int, List[str]]] = field(default_factory=list)  # (name, line, bases)
    imports: List[Tuple[str, int]] = field(default_factory=list)            # (token, line)
    calls: List[Tuple[str, int, str]] = field(default_factory=list)         # (callee, line, scope)

    def to_dict(self) -> Dict[str, Any]:
        return {"defs": [list(d) for d in self.defs],
                "classes": [[c[0], c[1], list(c[2])] for c in self.classes],
                "imports": [list(i) for i in self.imports],
                "calls": [list(c) for c in self.calls]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileEdges":
        return cls(
            defs=[tuple(x) for x in d.get("defs", [])],
            classes=[(x[0], x[1], list(x[2])) for x in d.get("classes", [])],
            imports=[tuple(x) for x in d.get("imports", [])],
            calls=[tuple(x) for x in d.get("calls", [])],
        )


def _name_of(node: ast.AST) -> Optional[str]:
    """The bare name a Name/Attribute reference resolves to (best-effort): `Base` for a
    `Name`, the final attribute (`Base` in `mod.Base`) for an `Attribute`, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _add_module_tokens(toks: set, dotted: str) -> None:
    """Record a dotted module both whole (`a.b.c`) and per segment (`a`, `b`, `c`) so an
    `imports(<module>)` query matches whether the user names the package or a component."""
    if not dotted:
        return
    toks.add(dotted)
    for seg in dotted.split("."):
        if seg:
            toks.add(seg)


def _import_edges(node: ast.AST) -> List[Tuple[str, int]]:
    line = getattr(node, "lineno", 0)
    toks: set = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            _add_module_tokens(toks, alias.name)
            if alias.asname:
                toks.add(alias.asname)
    elif isinstance(node, ast.ImportFrom):
        mod = node.module or ""
        _add_module_tokens(toks, mod)
        for alias in node.names:
            if alias.name and alias.name != "*":
                toks.add(alias.name)
                if mod:
                    toks.add(f"{mod}.{alias.name}")
            if alias.asname:
                toks.add(alias.asname)
    return [(t, line) for t in sorted(toks) if t]


def extract_edges(tree: ast.AST) -> FileEdges:
    """Walk a parsed module into typed edges. `scope` is the nearest enclosing function name
    (call attribution); `in_class` marks a class body (so a nested function is a `method`)."""
    edges = FileEdges()

    def walk(node: ast.AST, scope: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                edges.defs.append((child.name, child.lineno,
                                   "method" if in_class else "function"))
                walk(child, child.name, False)
            elif isinstance(child, ast.ClassDef):
                bases = [n for n in (_name_of(b) for b in child.bases) if n]
                edges.defs.append((child.name, child.lineno, "class"))
                edges.classes.append((child.name, child.lineno, bases))
                walk(child, scope, True)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                edges.imports.extend(_import_edges(child))
                walk(child, scope, in_class)
            elif isinstance(child, ast.Call):
                callee = _name_of(child.func)
                if callee:
                    line = getattr(child.func, "lineno", child.lineno)
                    edges.calls.append((callee, line, scope))
                walk(child, scope, in_class)
            else:
                walk(child, scope, in_class)

    walk(tree, "", False)
    return edges


# --------------------------------------------------------------------------- the backend
class AstBackend(GraphBackend):
    """The embedded stdlib-AST floor. `is_graph` stays False: this is a FLOOR above grep, not
    the adopted structural graph — so consumers that gate on a REAL graph (`uses_graph`) are
    unchanged, while `mokata query` becomes honest (`degraded=False`) on Python."""

    is_graph = False

    def __init__(self, root: str, grep: Optional[GrepBackend] = None,
                 name: str = "ast", cache_dir: Optional[str] = None) -> None:
        self.root = root
        self.name = name
        # The grep EMERGENCY floor for per-query fallthrough (non-.py / unparseable evidence).
        self.grep = grep if grep is not None else GrepBackend(root=root)
        if cache_dir is None:
            from .. import MOKATA_DIR, TEMP_LOCAL_DIRNAME
            cache_dir = os.path.join(root, MOKATA_DIR, TEMP_LOCAL_DIRNAME, CACHE_DIRNAME)
        self.cache_dir = cache_dir
        self._edges: Optional[Dict[str, FileEdges]] = None    # rel -> edges, built lazily

    # --- public API ----------------------------------------------------------
    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        if kind not in QUERY_KINDS:
            raise ValueError(f"unknown query kind '{kind}'; one of {QUERY_KINDS}")
        self._ensure_index()
        if kind == "callers":
            refs = self._callers(target)
        elif kind == "callees":
            refs = self._callees(target)
        elif kind == "implementers":
            refs = self._implementers(target)
        elif kind == "imports":
            refs = self._imports(target)
        else:
            refs = self._blast_radius(target, depth)
        if not refs:
            # No AST evidence. The target may live only in non-.py code or a file ast.parse
            # rejected, OR it may be genuinely uncalled — and the AST cannot see dynamic
            # dispatch. Either way, claiming an authoritative "zero" would overclaim, so we
            # fall through to the grep EMERGENCY floor (degraded=True), exactly as before.
            return self.grep.query(kind, target, depth=depth)
        return QueryResult(kind=kind, target=target, references=refs, backend=self.name,
                           degraded=False, note=AST_NOTE)

    # --- structure summary (GR.S4 briefing slice) ----------------------------
    def structure(self, top_n: int = 5):
        """A bounded, graph-derived structure summary for the SessionStart briefing (GR.S4):
        `(module_count, [top-N module names])`. Top modules are ranked by symbol density
        (defs + classes) — a cheap "biggest module" proxy. Returns (0, []) on an empty index."""
        self._ensure_index()
        edges = self._edges or {}
        if not edges:
            return 0, []
        ranked = sorted(
            edges.items(),
            key=lambda kv: (len(kv[1].defs) + len(kv[1].classes), kv[0]),
            reverse=True,
        )
        tops = [os.path.splitext(os.path.basename(rel))[0] for rel, _ in ranked[:max(0, top_n)]]
        return len(edges), tops

    # --- incremental index ---------------------------------------------------
    def invalidate(self) -> None:
        """GR.S4: drop the in-memory edge index so the NEXT query re-parses. The on-disk cache
        is (mtime,size)-keyed, so the re-parse touches only CHANGED files (cost ∝ churn) — this
        is the AST-floor half of the freshness rebuild."""
        self._edges = None

    def _ensure_index(self) -> None:
        if self._edges is not None:
            return
        cache = self._load_cache()
        edges: Dict[str, FileEdges] = {}
        fresh: Dict[str, Any] = {}
        for ab in self._py_files():
            rel = os.path.relpath(ab, self.root)
            try:
                st = os.stat(ab)
            except OSError:
                continue
            hit = cache.get(rel)
            if (hit is not None and hit.get("mtime") == st.st_mtime
                    and hit.get("size") == st.st_size):
                try:
                    edges[rel] = FileEdges.from_dict(hit["edges"])
                    fresh[rel] = hit
                    continue
                except (KeyError, TypeError, IndexError):
                    pass                          # a malformed cache entry → re-parse this file
            fe = self._parse_file(ab)
            if fe is None:
                continue                          # unparseable: a coverage gap, not a crash
            edges[rel] = fe
            fresh[rel] = {"mtime": st.st_mtime, "size": st.st_size, "edges": fe.to_dict()}
        self._edges = edges
        self._save_cache(fresh)

    def _py_files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)

    def _parse_file(self, abspath: str) -> Optional[FileEdges]:
        try:
            with open(abspath, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
            tree = parse_source(source)           # module-level seam (re-parse spy)
        except (OSError, SyntaxError, ValueError):
            return None
        return extract_edges(tree)

    def _load_cache(self) -> Dict[str, Any]:
        try:
            with open(os.path.join(self.cache_dir, CACHE_FILENAME), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}                             # missing OR torn cache → silent full re-parse
        entries = data.get("entries") if isinstance(data, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _save_cache(self, entries: Dict[str, Any]) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            payload = {"schema_version": CACHE_SCHEMA_VERSION, "entries": entries}
            tmp = os.path.join(self.cache_dir, CACHE_FILENAME + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, os.path.join(self.cache_dir, CACHE_FILENAME))
        except OSError:
            pass                                  # transient cache — a write failure is never fatal

    # --- query implementations over the edge index ---------------------------
    def _callers(self, target: str) -> List[Reference]:
        out: List[Reference] = []
        seen: set = set()
        for rel, fe in self._edges.items():
            for callee, line, scope in fe.calls:
                if callee != target:
                    continue
                key = (rel, line)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Reference(rel, line, "", scope or None))
        return out

    def _callees(self, target: str) -> List[Reference]:
        out: List[Reference] = []
        seen: set = set()
        for rel, fe in self._edges.items():
            for callee, line, scope in fe.calls:
                if scope != target or callee == target:
                    continue
                key = (rel, line, callee)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Reference(rel, line, "", callee))
        return out

    def _implementers(self, target: str) -> List[Reference]:
        out: List[Reference] = []
        for rel, fe in self._edges.items():
            for cname, line, bases in fe.classes:
                if target in bases:
                    out.append(Reference(rel, line, "", cname))
        return out

    def _imports(self, target: str) -> List[Reference]:
        out: List[Reference] = []
        seen: set = set()
        for rel, fe in self._edges.items():
            for token, line in fe.imports:
                if token != target:
                    continue
                key = (rel, line)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Reference(rel, line, "", None))
        return out

    def _blast_radius(self, target: str, depth: int) -> List[Reference]:
        """Transitive callers up to `depth` hops — the impact surface of `target` (the same
        BFS the grep floor runs, but over AST-resolved caller edges)."""
        seen_syms = {target}
        seen_refs: set = set()
        out: List[Reference] = []
        frontier = [target]
        for _ in range(max(1, depth)):
            nxt: List[str] = []
            for sym in frontier:
                for r in self._callers(sym):
                    key = (r.path, r.line)
                    if key not in seen_refs:
                        seen_refs.add(key)
                        out.append(r)
                    enc = r.symbol
                    if enc and enc not in seen_syms:
                        seen_syms.add(enc)
                        nxt.append(enc)
            frontier = nxt
            if not frontier:
                break
        return out
