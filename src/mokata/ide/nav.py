"""Navigation for `mokata ide` (0.0.23 C1, ID.S2): the tree, a file, its outline, ⌘P, and the
structural questions — every answer from mokata's OWN graph layer (`mokata.knowledge`).

    tree(root)                    the repo's own files — the SAME walker the graph uses
                                  (`repo_walk.prune_source_dirs`: venvs, vendor dirs, nested
                                  checkouts, hidden dirs never listed)
    read_file(root, path)         one file, read through the review window's contained reader,
                                  with its change markers against HEAD
    outline(rel, text)            the file's definitions, from the graph layer's own parse seam
    quick_open(root, q, changed)  ⌘P: CHANGED files first, then files, then symbols — fuzzy, with
                                  the matcher that answered NAMED (`rapidfuzz` or the floor)
    query_payload(result)         THE serialisation of a `QueryResult` — the IDE's `/api/query`
                                  and `mokata query --json` both print exactly this, so the two
                                  answers are byte-for-byte the same (C1 exit criterion)
    impact(root, path, lines)     the changed symbols of one file, who calls them, which tests
                                  reach them — each answer carrying its `basis`

Nothing here writes outside `.mokata/` (the graph layer's own AST cache) and nothing here touches
the network. A path that arrives from the page is CONTAINED by `contained_rel` before anything
opens it: absolute paths, `..`, drive letters, NULs and symlinks out of the tree are refused.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diffs as D
from . import snapshots as S
from ..errors import MokataError

MAX_TREE_FILES = 50000          # what the page is sent; ⌘P searches the whole list server-side
QUICK_LIMIT = 12                # rows per ⌘P section
IMPACT_MAX_SYMBOLS = 8
IMPACT_DEPTH = 3
INDEX_TTL = 3.0                 # seconds a walked file list / symbol list is reused for ⌘P

_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRefused(MokataError, ValueError):
    """A path from the page that is not a plain repo-relative name inside the repository. A HARD
    refusal (D5): nothing degrades — the request is answered 400 and nothing is opened."""


# ----------------------------------------------------------------------------------- containment

def contained_rel(root: str, path: str) -> str:
    """`path` as a repo-relative '/' name, or PathRefused. The page only ever sends names; an
    absolute path, a drive letter, a NUL, a `..` escape or a symlink out of the tree is refused
    (realpath'd by `snapshots.rel_path`, the review window's one containment rule)."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise PathRefused("no path")
    if path[0] in "/\\" or _DRIVE.match(path) or os.path.isabs(path):
        raise PathRefused("absolute paths are refused — send a repo-relative name")
    if any(part == ".." for part in re.split(r"[\\/]", path)):
        raise PathRefused("`..` is refused")
    rel = S.rel_path(root, path)
    if rel is None:
        raise PathRefused("outside the repository")
    return rel


# ------------------------------------------------------------------------------------------ tree

def walk_files(root: str) -> List[str]:
    """Every file of the repo's own source, as sorted '/' names — walked with the graph's own
    pruning rule, so the explorer can never list what the graph refuses to index."""
    from ..repo_paths import name_of
    from ..repo_walk import prune_source_dirs
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        prune_source_dirs(dirpath, dirnames)
        dirnames.sort()
        for fn in filenames:
            out.append(str(name_of(os.path.join(dirpath, fn), root)))
    out.sort()
    return out


def tree(root: str) -> Dict[str, Any]:
    files = walk_files(root)
    return {"sample": False, "count": len(files), "files": files[:MAX_TREE_FILES],
            "truncated": len(files) > MAX_TREE_FILES,
            "pruned": "virtualenvs (any name), conda envs, vendor/install dirs, nested checkouts "
                      "and hidden dirs are never listed — the same rule the graph indexes by"}


# ------------------------------------------------------------------------------------- one file

def _changes_vs_head(root: str, rel: str) -> List[Dict[str, Any]]:
    """Contiguous changed regions of `rel` against HEAD, on the NEW side's line numbers:
    `add` (lines only added), `mod` (a hunk that removed and added), `del` (removed lines, marked
    at the line they used to precede). Untracked → one `add` region over the whole file."""
    if not D.is_git(root):
        return []
    code, _ = D._git(root, "rev-parse", "--verify", "HEAD")
    base = "HEAD" if code == 0 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    code, out = D._git(root, "diff", base, "--no-color", "--no-ext-diff", "-U0", "--", rel)
    regions: List[Dict[str, Any]] = []
    if code == 0 and out.strip():
        for fd in D.parse_unified(out[:D.MAX_DIFF_BYTES]):
            for h in fd.hunks:
                adds = [ln.new for ln in h.lines if ln.op == "+" and ln.new]
                dels = [ln for ln in h.lines if ln.op == "-"]
                m = D._HUNK.match(h.header)
                anchor = int(m.group(3)) if m else 1
                if adds:
                    regions.append({"start": min(adds), "end": max(adds),
                                    "kind": "mod" if dels else "add"})
                elif dels:
                    regions.append({"start": max(1, anchor), "end": max(1, anchor), "kind": "del",
                                    "removed": len(dels)})
        return regions
    code, untracked = D._git(root, "ls-files", "--others", "--exclude-standard", "--", rel)
    if code == 0 and untracked.strip():
        return [{"start": 1, "end": 10 ** 9, "kind": "add"}]
    return []


def read_file(root: str, path: str) -> Dict[str, Any]:
    rel = contained_rel(root, path)
    text = D._read_text(root, rel)
    if text is None:
        return {"sample": False, "path": rel, "text": None,
                "note": "not shown — binary, too large, not a regular file, or not readable"}
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    changes = _changes_vs_head(root, rel)
    for c in changes:
        c["end"] = min(c["end"], max(lines, 1))
    return {"sample": False, "path": rel, "text": text, "lines": lines, "changes": changes,
            "changes_against": "HEAD"}


# --------------------------------------------------------------------------------------- outline

def outline(rel: str, text: Optional[str]) -> Dict[str, Any]:
    """The definitions in one file, from the graph layer's own parse seam
    (`ast_backend.parse_source` for Python; `ts_edges.parse_source` for TS when `[graph-ts]` is
    in). Each symbol: name, kind, line, end (Python only — TS carries no end) and depth."""
    from ..knowledge import ast_backend as AB
    from ..knowledge import ts_edges as TS
    if text is None:
        return {"path": rel, "symbols": [], "backend": "", "basis": "", "note": "file not readable"}
    if rel.endswith(".py"):
        try:
            tree_ = AB.parse_source(text)
        except (SyntaxError, ValueError) as exc:
            return {"path": rel, "symbols": [], "backend": "ast", "basis": "",
                    "note": f"the file does not parse ({type(exc).__name__}) — no outline"}
        syms: List[Dict[str, Any]] = []

        def walk(node: ast.AST, depth: int, in_class: bool) -> None:
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    syms.append({"name": ch.name, "kind": "method" if in_class else "function",
                                 "line": ch.lineno, "end": getattr(ch, "end_lineno", ch.lineno),
                                 "depth": depth})
                    walk(ch, depth + 1, False)
                elif isinstance(ch, ast.ClassDef):
                    syms.append({"name": ch.name, "kind": "class", "line": ch.lineno,
                                 "end": getattr(ch, "end_lineno", ch.lineno), "depth": depth})
                    walk(ch, depth + 1, True)
                elif isinstance(ch, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    walk(ch, depth, in_class)
        walk(tree_, 0, False)
        return {"path": rel, "symbols": syms, "backend": "ast", "basis": "structural",
                "note": ""}
    if TS.is_ts_source(rel):
        if not TS.available():
            return {"path": rel, "symbols": [], "backend": "", "basis": "",
                    "note": "TypeScript outline needs the optional graph extra: "
                            "pip install 'mokata[graph-ts]'"}
        fe = TS.parse_source(text, rel)
        if fe is None:
            return {"path": rel, "symbols": [], "backend": "ts", "basis": "",
                    "note": "the file does not parse — no outline"}
        return {"path": rel, "backend": "ts", "basis": "structural", "note": "",
                "symbols": [{"name": n, "kind": k, "line": ln, "end": None, "depth": 0}
                            for n, ln, k in sorted(fe.defs, key=lambda d: d[1])]}
    return {"path": rel, "symbols": [], "backend": "", "basis": "",
            "note": "no outline for this file type (the graph reads Python, and TypeScript "
                    "with [graph-ts])"}


def symbol_at(symbols: Sequence[Dict[str, Any]], line: int) -> Optional[Dict[str, Any]]:
    """The innermost symbol whose span holds `line` (TS spans are open-ended: the last def
    starting at or before the line)."""
    best = None
    for s in symbols:
        end = s.get("end") or 10 ** 9
        if s["line"] <= line <= end and (best is None or s["line"] >= best["line"]):
            best = s
    return best


# --------------------------------------------------------------------------------- fuzzy matcher

def matcher_name() -> str:
    try:
        import rapidfuzz  # noqa: F401
        return "rapidfuzz"
    except ImportError:
        return "python-floor"


def _floor_score(q: str, cand: str) -> Optional[float]:
    """The pure-Python floor: a case-insensitive SUBSEQUENCE match, scored for consecutive runs,
    word-boundary hits and a basename hit, minus a length penalty. None = no match."""
    lc = cand.lower()
    base_start = lc.rfind("/") + 1
    pos = -1
    score = 0.0
    run = 0
    for ch in q:
        nxt = lc.find(ch, pos + 1)
        if nxt < 0:
            return None
        if nxt == pos + 1:
            run += 1
            score += 2.0 * run
        else:
            run = 0
            score += 1.0
        if nxt == 0 or lc[nxt - 1] in "/_.- " or (cand[nxt].isupper() and cand[nxt - 1].islower()):
            score += 3.0
        if nxt >= base_start:
            score += 1.5
        pos = nxt
    if lc[base_start:].startswith(q):
        score += 10.0
    if q in lc:
        score += 5.0
    return score - len(cand) * 0.02


def _subsequence_pattern(ql: str) -> "re.Pattern[str]":
    """`[^a]*a[^b]*b…` — a subsequence test the regex engine runs in C. Each class excludes the
    char it precedes, so the first occurrence is always the right one; on 3.11+ the quantifier is
    possessive and a miss costs one linear scan instead of a backtracking search."""
    plus = "+" if sys.version_info >= (3, 11) else ""
    parts = []
    for ch in ql:
        alts = {ch, ch.upper(), ch.lower()}
        cls = "".join(re.escape(a) for a in sorted(alts))
        parts.append(f"[^{cls}]*{plus}[{cls}]")
    return re.compile("".join(parts), re.DOTALL)


def fuzzy(q: str, candidates: Sequence[str], limit: int = QUICK_LIMIT) -> Tuple[List[Tuple[str, float]], str]:
    """Rank `candidates` against `q`. Returns (rows, matcher) — the matcher that answered is
    always named, so a floor answer is never presented as the accelerator's."""
    q = (q or "").strip()
    if not q:
        return [(c, 0.0) for c in candidates[:limit]], matcher_name()
    ql = q.lower()
    # ONE match rule for both matchers — a case-insensitive subsequence, tested in C — so the
    # accelerator changes how fast and how well the survivors are RANKED, never which files match.
    pat = _subsequence_pattern(ql)
    survivors = [c for c in candidates if pat.match(c) is not None]
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        rows = []
        for c in survivors:
            s = _floor_score(ql, c)
            if s is not None:
                rows.append((c, s))
        rows.sort(key=lambda r: (-r[1], len(r[0]), r[0]))
        return rows[:limit], "python-floor"
    base = [c[c.rfind("/") + 1:].lower() for c in survivors]
    got = process.extract(ql, base, scorer=fuzz.partial_ratio, processor=None,
                          limit=max(limit * 8, 64))
    scored = []
    for _b, sc, i in got:
        c = survivors[i]
        bonus = 20.0 if base[i].startswith(ql) else 10.0 if ql in base[i] else 0.0
        scored.append((c, float(sc) + bonus - len(c) * 0.02))
    scored.sort(key=lambda r: (-r[1], len(r[0]), r[0]))
    return scored[:limit], "rapidfuzz"


# ------------------------------------------------------------------------- the ⌘P index (cached)

_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cached(root: str, key: str, build):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get((root, key))
        if hit and now - hit[0] < INDEX_TTL:
            return hit[1]
    val = build()
    with _CACHE_LOCK:
        _CACHE[(root, key)] = (now, val)
    return val


_SYMS: Dict[str, Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]]] = {}


def _defs_of(abspath: str, rel: str) -> List[Tuple[str, int, str]]:
    """One file's definitions, through the graph layer's own parse seams (`ast_backend` for
    Python, `ts_edges` for TS when `[graph-ts]` is in) — the same `FileEdges.defs` the AST floor
    indexes. Unparseable → no rows (a coverage gap, never a crash)."""
    from ..knowledge import ast_backend as AB
    from ..knowledge import ts_edges as TS
    try:
        with open(abspath, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return []
    if rel.endswith(".py"):
        try:
            return list(AB.extract_edges(AB.parse_source(src)).defs)
        except (SyntaxError, ValueError, RecursionError):
            return []
    fe = TS.parse_source(src, rel) if TS.available() else None
    return list(fe.defs) if fe else []


def symbol_index(root: str) -> List[Tuple[str, str, int, str]]:
    """Every definition in the repo's own source: (name, path, line, kind). Built over the SAME
    walk and the SAME parse seam as the graph's AST floor, so ⌘P can only name symbols
    `mokata query defs` can find. Re-parses only files whose (mtime, size) moved, and keeps its
    cache in memory: ⌘P writes nothing."""
    def build():
        from ..knowledge import ts_edges as TS
        prev = _SYMS.get(root, {})
        cur: Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]] = {}
        ts_on = TS.available()
        for rel in walk_files(root):
            if not (rel.endswith(".py") or (ts_on and TS.is_ts_source(rel))):
                continue
            ab = os.path.join(root, *rel.split("/"))
            try:
                st = os.stat(ab)
            except OSError:
                continue
            hit = prev.get(rel)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                cur[rel] = hit
            else:
                cur[rel] = (st.st_mtime, st.st_size, _defs_of(ab, rel))
        _SYMS[root] = cur
        return [(name, rel, int(line), kind)
                for rel, (_m, _s, defs) in sorted(cur.items()) for name, line, kind in defs]
    return _cached(root, "symbols", build)


def quick_open(root: str, q: str, changed: Sequence[str] = (),
               files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """⌘P. Sections in a FIXED order — changed files (always first), files, symbols. `@x` asks
    symbols only; `#x` hands off to text search (C2). The matcher that ranked is named."""
    q = q or ""
    sym_only = q.startswith("@")
    needle = q[1:] if sym_only else q
    changed = [c for c in changed if isinstance(c, str)]
    out: Dict[str, Any] = {"sample": False, "q": q, "changed": [], "files": [], "symbols": []}
    matcher = matcher_name()
    if not sym_only:
        rows, matcher = fuzzy(needle, changed, QUICK_LIMIT)
        out["changed"] = [{"path": c, "score": round(s, 2)} for c, s in rows]
        all_files = files if files is not None else _cached(root, "files",
                                                            lambda: walk_files(root))
        cset = set(changed)
        rest = [f for f in all_files if f not in cset] if cset else all_files
        rows, matcher = fuzzy(needle, rest, QUICK_LIMIT)
        out["files"] = [{"path": c, "score": round(s, 2)} for c, s in rows]
    if needle.strip():
        idx = symbol_index(root)
        names = sorted({r[0] for r in idx})
        rows, matcher = fuzzy(needle, names, QUICK_LIMIT)
        by_name: Dict[str, List[Tuple[str, str, int, str]]] = {}
        for r in idx:
            by_name.setdefault(r[0], []).append(r)
        syms = []
        for name, s in rows:
            for _n, rel, line, kind in by_name.get(name, [])[:3]:
                syms.append({"name": name, "path": rel, "line": line, "kind": kind,
                             "score": round(s, 2), "basis": "structural", "backend": "ast"})
        out["symbols"] = syms[:QUICK_LIMIT]
    out["matcher"] = matcher
    out["matcher_note"] = ("" if matcher == "rapidfuzz" else
                           "pure-Python floor — pip install rapidfuzz for the accelerator")
    return out


# ----------------------------------------------------------------- structural questions (graph)

def _layer(root: str):
    from ..config import Surface
    from ..knowledge import KnowledgeLayer
    return KnowledgeLayer.from_surface(Surface.load(root))


def run(root: str, kind: str, target: str, depth: int = 2, layer: Any = None):
    """The SAME call `mokata query` makes: `knowledge.layer.run_query` over the repo's own
    `KnowledgeLayer` (its configured graph, else the AST floor, else grep)."""
    from ..knowledge.layer import run_query
    return run_query(layer if layer is not None else _layer(root), kind, target, depth=depth)


def query_payload(result: Any) -> str:
    """THE wire form of a `QueryResult`. `mokata query --json` prints exactly this line and the
    IDE's `/api/query` answers exactly these bytes (+ the same trailing newline)."""
    return json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=False)


def _is_test(path: str) -> bool:
    from ..gate_hook import is_test_path
    return is_test_path(path)


def parse_lines(spec: str) -> List[Tuple[int, int]]:
    """'3-9,14,20-22' → [(3,9),(14,14),(20,22)]; junk is ignored, never raised on."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            continue
        if lo > 0 and hi >= lo:
            out.append((lo, min(hi, lo + 100000)))
    return out[:500]


def impact(root: str, path: str, lines: str = "") -> Dict[str, Any]:
    """One changed file's structural impact: which symbols the changed lines sit in, who calls
    each, and which TESTS reach each through the blast radius — every list carrying the `basis`
    of the query that produced it. `untested` is claimed ONLY on a structural answer; a lexical
    one says `unknown` (an absence of evidence is never shown as evidence of absence)."""
    rel = contained_rel(root, path)
    is_test = _is_test(rel)
    text = D._read_text(root, rel)
    ol = outline(rel, text)
    ranges = parse_lines(lines)
    changed_syms: List[Dict[str, Any]] = []
    seen = set()
    for lo, hi in ranges:
        for s in ol["symbols"]:
            end = s.get("end") or s["line"]
            if s["line"] <= hi and end >= lo and s["name"] not in seen:
                # innermost-first is not needed here: every enclosing def is "changed"
                seen.add(s["name"])
                changed_syms.append(s)
    changed_syms = sorted(changed_syms, key=lambda s: (-s.get("depth", 0), s["line"]))
    out: Dict[str, Any] = {"sample": False, "path": rel, "is_test": is_test, "symbols": [],
                           "status": "test" if is_test else "no-symbols"}
    if is_test or not changed_syms:
        out["note"] = ("a test file" if is_test else
                       "no changed definition in this file the graph can name")
        return out
    layer = _layer(root)
    for s in changed_syms[:IMPACT_MAX_SYMBOLS]:
        callers = run(root, "callers", s["name"], layer=layer)
        blast = run(root, "blast_radius", s["name"], depth=IMPACT_DEPTH, layer=layer)
        tests = sorted({f"{r.path}:{r.line}" + (f" {r.symbol}" if r.symbol else "")
                        for r in blast.references if _is_test(r.path)})
        out["symbols"].append({
            "name": s["name"], "kind": s["kind"], "line": s["line"],
            "callers": callers.to_dict(), "tests": tests,
            "blast": {"basis": blast.basis, "backend": blast.backend, "count": blast.count,
                      "note": blast.note},
            "covered": bool(tests),
            "verdict": "covered" if tests else ("untested" if blast.structural else "unknown")})
    verdicts = {s["verdict"] for s in out["symbols"]}
    out["status"] = ("untested" if "untested" in verdicts else
                     "unknown" if "unknown" in verdicts else "covered")
    return out


__all__ = ["PathRefused", "contained_rel", "walk_files", "tree", "read_file", "outline",
           "symbol_at", "fuzzy", "matcher_name", "quick_open", "symbol_index", "run",
           "query_payload", "parse_lines", "impact"]
