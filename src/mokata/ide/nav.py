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
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diffs as D
from . import snapshots as S
from ..errors import MokataError

MAX_TREE_FILES = 50000          # what the page is sent; ⌘P searches the whole list server-side
QUICK_LIMIT = 12                # rows per ⌘P section
IMPACT_MAX_SYMBOLS = 8
IMPACT_MAX_CALLERS = 200          # caller references per symbol sent to the page (count kept)
IMPACT_DEPTH = 3

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
    # lane S: the explorer reads the same kept list ⌘P does (re-walked on a change signal), so
    # the tree answers without a walk while heavy graph work holds the interpreter.
    files = files_of(root)
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


# ------------------------------------------------------------------------------------ the ⌘P index
#
# 0.0.25 (lane S — the 1M-line scale run). The first ⌘P after every start re-parsed EVERY file with
# the full edge extractor (17–19 s), ignoring the AST floor's own on-disk cache, and after 3 s of
# idle (`INDEX_TTL`) every ⌘P re-walked and stat'ed all 7k files again. Now:
#
#   * the FILE list is walked once and kept until a real change signal (`note_change` — the server
#     calls it when its fingerprint moves or a hook reports an edit). Past `FILES_MAX_AGE` (edits no
#     signal saw) it is re-walked on a background thread while the old list keeps answering.
#   * the SYMBOL list is read off the graph layer's OWN AST edge index (`AstBackend`) — already in
#     memory once the graph has answered anything, else loaded from its (mtime, size) disk cache —
#     so ⌘P names exactly what `mokata query defs` can find, and is rebuilt exactly when that index
#     is (the freshness controller drops it on a real file change; `note_change` drops it too).
#   * nothing blocks the page: the index builds on a background thread and ⌘P waits at most
#     `QUICK_WAIT_SECS` for it — then it answers the files it has and SAYS the symbols are loading.
#
# Both caches are per repository and bounded (`MAX_ROOTS`, least recently used dropped).

FILES_MAX_AGE = 30.0            # seconds before an unsignalled file list is re-walked (background)
QUICK_WAIT_SECS = 1.0           # the longest ⌘P waits for the symbol index before answering without
MAX_ROOTS = 8                   # repositories whose file / symbol lists one process keeps
SYMBOLS_BUILDING_NOTE = ("the symbol index is still loading from the graph layer — files answer "
                         "now; symbols appear here when it is ready")

_WALKED = "walked"              # the symbol source when no AST floor is in the layer


class _Files:
    """One repo's file list: walked once, re-walked on a change signal (at the next ask) or, past
    FILES_MAX_AGE, in the background while the old list answers."""

    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        self.rows: Optional[List[str]] = None
        self.at = 0.0
        self.dirty = False
        self.refreshing = False

    def get(self) -> List[str]:
        with self.lock:
            rows, dirty = self.rows, self.dirty
            old = rows is not None and time.monotonic() - self.at > FILES_MAX_AGE
            spawn = old and not dirty and not self.refreshing
            if spawn:
                self.refreshing = True
        if rows is None or dirty:
            return self._walk()
        if spawn:
            threading.Thread(target=self._walk, daemon=True, name="mokata-files").start()
        return rows

    def _walk(self) -> List[str]:
        with self.lock:
            self.dirty = False
        rows = walk_files(self.root)
        with self.lock:
            self.rows, self.at, self.refreshing = rows, time.monotonic(), False
        return rows

    def invalidate(self) -> None:
        with self.lock:
            self.dirty = True


def _defs_of(abspath: str, rel: str) -> List[Tuple[str, int, str]]:
    """One file's definitions through the graph layer's own parse seams — used ONLY when the layer
    carries no AST floor to read them from (a repo mokata cannot load a graph layer for)."""
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


def _ast_floor(layer: Any) -> Any:
    """The layer's AST floor (primary or fallback), or None."""
    from ..knowledge.ast_backend import AstBackend
    for b in (getattr(layer, "primary", None), getattr(layer, "fallback", None)):
        if isinstance(b, AstBackend):
            return b
    return None


class _Symbols:
    """One repo's ⌘P symbol rows, derived from the AST floor's edge index (see above)."""

    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        self.rows: Optional[List[Tuple[str, str, int, str]]] = None
        self.names: List[str] = []
        self.by_name: Dict[str, List[Tuple[str, str, int, str]]] = {}
        self.source: Any = None             # the edge dict the rows came from (identity), or _WALKED
        self.walked: Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]] = {}
        self.at = 0.0
        self.dirty = False
        self.done = threading.Event()
        self.done.set()
        self.thread: Optional[threading.Thread] = None

    def _stale(self) -> bool:
        if self.rows is None or self.dirty:
            return True
        if self.source is _WALKED:
            return time.monotonic() - self.at > FILES_MAX_AGE
        hit = _LAYERS.get(os.path.abspath(self.root))
        floor = _ast_floor(hit[1]) if hit is not None else None
        return floor is None or getattr(floor, "_edges", None) is not self.source

    def get(self, wait: Optional[float]):
        """(rows or None, state) — state is `ready`, `refreshing` (older rows, a rebuild running)
        or `building` (no rows yet). `wait` bounds how long this call waits for a running build
        (None = until it is done)."""
        with self.lock:
            stale = self._stale()
            if stale and (self.thread is None or not self.thread.is_alive()):
                drop, self.dirty = self.dirty, False
                self.done = threading.Event()
                self.thread = threading.Thread(target=self._build, args=(self.done, drop),
                                               daemon=True, name="mokata-symbols")
                self.thread.start()
            done = self.done
        if stale:
            done.wait(wait)
        with self.lock:
            if self.rows is None:
                return None, "building"
            return self.rows, ("ready" if done.is_set() else "refreshing")

    def _build(self, done: threading.Event, drop: bool) -> None:
        try:
            try:
                layer = _layer(self.root)
            except MokataError:
                layer = None                 # not a repo mokata can load a layer for: walk it
            floor = _ast_floor(layer) if layer is not None else None
            if floor is not None:
                if drop:
                    floor.invalidate()       # a change signal: re-read only what moved
                if getattr(floor, "_edges", None) is None:
                    floor.structure()        # the floor's public load: (mtime, size) disk cache
                edges = getattr(floor, "_edges", None) or {}
                rows = [(name, rel, int(line), kind) for rel, fe in sorted(edges.items())
                        for name, line, kind in fe.defs]
                self._publish(rows, edges)
            else:
                self._publish(self._walk(), _WALKED)
        finally:
            done.set()

    def _walk(self) -> List[Tuple[str, str, int, str]]:
        from ..knowledge import ts_edges as TS
        cur: Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]] = {}
        ts_on = TS.available()
        for rel in walk_files(self.root):
            if not (rel.endswith(".py") or (ts_on and TS.is_ts_source(rel))):
                continue
            ab = os.path.join(self.root, *rel.split("/"))
            try:
                st = os.stat(ab)
            except OSError:
                continue
            hit = self.walked.get(rel)
            cur[rel] = hit if hit and hit[0] == st.st_mtime and hit[1] == st.st_size else \
                (st.st_mtime, st.st_size, _defs_of(ab, rel))
        self.walked = cur
        return [(name, rel, int(line), kind)
                for rel, (_m, _s, defs) in sorted(cur.items()) for name, line, kind in defs]

    def _publish(self, rows: List[Tuple[str, str, int, str]], source: Any) -> None:
        by_name: Dict[str, List[Tuple[str, str, int, str]]] = {}
        for r in rows:
            by_name.setdefault(r[0], []).append(r)
        with self.lock:
            self.rows, self.source, self.at = rows, source, time.monotonic()
            self.by_name, self.names = by_name, sorted(by_name)

    def invalidate(self) -> None:
        with self.lock:
            self.dirty = True


_PER_ROOT: "OrderedDict[Tuple[str, str], Any]" = OrderedDict()
_PER_ROOT_LOCK = threading.Lock()


def _per_root(root: str, kind: str, make):
    key = (os.path.abspath(root), kind)
    with _PER_ROOT_LOCK:
        hit = _PER_ROOT.get(key)
        if hit is None:
            hit = _PER_ROOT[key] = make(root)
            while len(_PER_ROOT) > 2 * MAX_ROOTS:
                _PER_ROOT.popitem(last=False)
        _PER_ROOT.move_to_end(key)
        return hit


def files_of(root: str) -> List[str]:
    """The repo's file list for ⌘P and search (see the section note: no idle re-walk)."""
    return _per_root(root, "files", _Files).get()


def note_change(root: str) -> None:
    """A change signal (the server's fingerprint moved, or a hook reported an edit): the next ⌘P
    re-walks the file list and re-reads the symbols that moved."""
    _per_root(root, "files", _Files).invalidate()
    _per_root(root, "symbols", _Symbols).invalidate()


def symbol_index(root: str) -> List[Tuple[str, str, int, str]]:
    """Every definition in the repo's own source: (name, path, line, kind) — the AST floor's own
    `defs`, so ⌘P can only name symbols `mokata query defs` can find. Waits for the index."""
    rows, _state = _per_root(root, "symbols", _Symbols).get(None)
    return list(rows or [])


def quick_open(root: str, q: str, changed: Sequence[str] = (),
               files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """⌘P. Sections in a FIXED order — changed files (always first), files, symbols. `@x` asks
    symbols only; `#x` hands off to text search (C2). The matcher that ranked is named. Never
    waits on the symbol index longer than QUICK_WAIT_SECS: `symbols_state` says what answered."""
    q = q or ""
    sym_only = q.startswith("@")
    needle = q[1:] if sym_only else q
    changed = [c for c in changed if isinstance(c, str)]
    out: Dict[str, Any] = {"sample": False, "q": q, "changed": [], "files": [], "symbols": []}
    matcher = matcher_name()
    if not sym_only:
        rows, matcher = fuzzy(needle, changed, QUICK_LIMIT)
        out["changed"] = [{"path": c, "score": round(s, 2)} for c, s in rows]
        all_files = files if files is not None else files_of(root)
        cset = set(changed)
        rest = [f for f in all_files if f not in cset] if cset else all_files
        rows, matcher = fuzzy(needle, rest, QUICK_LIMIT)
        out["files"] = [{"path": c, "score": round(s, 2)} for c, s in rows]
    out["symbols_state"] = "ready"
    if needle.strip():
        sx = _per_root(root, "symbols", _Symbols)
        idx, out["symbols_state"] = sx.get(QUICK_WAIT_SECS)
        if idx is None:
            out["symbols_note"] = SYMBOLS_BUILDING_NOTE
        else:
            with sx.lock:
                names, by_name = sx.names, sx.by_name
            rows, matcher = fuzzy(needle, names, QUICK_LIMIT)
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

# 0.0.25 `IDE-SERVER-LIGHT` — ONE KnowledgeLayer per repo for the server's life.
#
# Until 0.0.25 every page call built a fresh layer, and with code-review-graph adopted each build ran
# `code-review-graph --version` (a Python cold start) and each graph answer started a NEW
# `code-review-graph serve` (~125 MB), dropping the previous one unclosed. Measured on a 2-file
# repo, 15 page calls: 15 `--version` runs + 9 `serve` processes + zombies, and the slow
# restarts tipped the graph into "DEGRADED — backend unreachable". On Windows every one of those
# was also a cmd window (`IDE-WIN-CMD-FLASH`). The layer is rebuilt only when the manifest changes
# (an adopt/unadopt), and the replaced layer's CRG session is closed, not abandoned.
_LAYERS: Dict[str, Tuple[Any, Any]] = {}
_LAYERS_LOCK = threading.Lock()


def _manifest_key(root: str) -> Any:
    try:
        st = os.stat(os.path.join(root, ".mokata", "manifest.json"))
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _close_layer(layer: Any) -> None:
    client = getattr(getattr(layer, "primary", None), "client", None)
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except (OSError, RuntimeError, ValueError):
            # closing a dropped helper must never fail the page call that replaced it; the
            # session's own close() already absorbs the process errors (it reaps or kills).
            pass


# 0.0.25 — ONE freshness reconcile per page action, not one per graph question. Every structural
# query front-runs `FreshnessController.ensure_fresh`, which spawns `git diff --name-only <sha>` AND
# `git ls-files --others` (a walk of the whole working tree). A ripple asks ~10 questions, so one
# click on "Show" ran ~20 git processes — measured: 24 spawns for one ripple of a 9-node graph; on a
# large repo each `ls-files --others` is a full untracked walk. Inside this window the last outcome
# is reused (minus "rebuilt"/"changed", which happened once): an edit is seen by the next action.
FRESH_WINDOW_SECS = 2.0


class _OncePerWindow:
    """Wraps a layer's freshness controller so `ensure_fresh` runs at most once per window.
    Serialised: two page calls arriving together share one reconcile instead of racing two."""

    def __init__(self, inner: Any, window: float = FRESH_WINDOW_SECS, clock: Any = time.monotonic):
        self._inner, self._window, self._clock = inner, window, clock
        self._lock = threading.Lock()
        self._at: Optional[float] = None
        self._out: Any = None

    def ensure_fresh(self, layer: Any) -> Any:
        with self._lock:
            if self._at is not None and self._clock() - self._at < self._window:
                from dataclasses import is_dataclass, replace
                out = self._out
                return replace(out, rebuilt=False, changed=[]) if is_dataclass(out) else out
            self._out = self._inner.ensure_fresh(layer)
            self._at = self._clock()
            return self._out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _layer(root: str):
    from ..config import Surface
    from ..knowledge import KnowledgeLayer
    key, where = _manifest_key(root), os.path.abspath(root)
    with _LAYERS_LOCK:
        hit = _LAYERS.get(where)
        if hit is not None and hit[0] == key:
            return hit[1]
        layer = KnowledgeLayer.from_surface(Surface.load(root))
        if getattr(layer, "freshness", None) is not None:
            layer.freshness = _OncePerWindow(layer.freshness)
        _LAYERS[where] = (key, layer)
    if hit is not None:
        _close_layer(hit[1])
    return layer


def close_layers() -> None:
    """Close every cached layer's helper processes (the server calls this on the way out)."""
    with _LAYERS_LOCK:
        layers = [v[1] for v in _LAYERS.values()]
        _LAYERS.clear()
    for layer in layers:
        _close_layer(layer)


# 0.0.25 (lane S) — a page that stopped waiting stops the work. Heavy routes (impact, the graph
# views, query) run on the server's own bounded lane with a deadline; when the deadline passes or
# the browser goes away, the lane sets this thread's cancel flag and the work stops at its next
# graph question instead of running to the end for nobody. (A single slow question still finishes —
# the knowledge layer bounds its own queries; this bounds how MANY run for an abandoned request.)
_CTX = threading.local()


class Abandoned(MokataError):
    """The request this work was for is gone (its deadline passed or its client left)."""


@contextmanager
def cancellable(flag: threading.Event):
    prev = getattr(_CTX, "cancel", None)
    _CTX.cancel = flag
    try:
        yield
    finally:
        _CTX.cancel = prev


def check_abandoned() -> None:
    flag = getattr(_CTX, "cancel", None)
    if flag is not None and flag.is_set():
        raise Abandoned("the request was abandoned — no one is waiting for this answer")


# 0.0.25 (lane S) — the status bar must name the graph that is ACTUALLY answering. With
# code-review-graph adopted but failing, every answer came from the AST floor while the status bar
# still said "code-review-graph". Each answer the IDE gets is read here (no extra query): when an
# adopted graph's answer came from another backend for a reason other than a mapping gap, the repo
# is marked degraded with the answer's own note; an answer from the graph itself clears it.
_HEALTH: Dict[str, Dict[str, Any]] = {}
_MAPPING_GAP = "exposes no '"


def _note_answer(root: str, layer: Any, result: Any) -> None:
    primary = getattr(layer, "primary", None)
    if primary is None or not getattr(primary, "is_graph", False):
        return
    key = os.path.abspath(root)
    backend = getattr(result, "backend", "") or ""
    note = getattr(result, "note", "") or ""
    if backend == primary.name:
        _HEALTH.pop(key, None)
    elif _MAPPING_GAP not in note:
        _HEALTH[key] = {"configured": primary.name, "answering": backend, "why": note[:240],
                        "at": time.time()}


def graph_health(root: str) -> Optional[Dict[str, Any]]:
    """The last degrade the IDE saw for this repo's adopted graph, or None (healthy / not known)."""
    return _HEALTH.get(os.path.abspath(root))


def degraded_line(h: Dict[str, Any]) -> str:
    why = f" ({h['why']})" if h.get("why") else ""
    return f"{h['configured']} — degraded, answering from {h['answering'] or 'the floor'}{why}"


def run(root: str, kind: str, target: str, depth: int = 2, layer: Any = None):
    """The SAME call `mokata query` makes: `knowledge.layer.run_query` over the repo's own
    `KnowledgeLayer` (its configured graph, else the AST floor, else grep). Checks first that
    someone is still waiting for the answer (`check_abandoned`)."""
    from ..knowledge.layer import run_query
    check_abandoned()
    layer = layer if layer is not None else _layer(root)
    result = run_query(layer, kind, target, depth=depth)
    _note_answer(root, layer, result)
    return result


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


IMPACT_ON_DEMAND_MAX = 40          # symbols one on-demand request may ask for


def impact(root: str, path: str, lines: str = "",
           symbols: Optional[Sequence[str]] = None) -> Dict[str, Any]:
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
    # The cap is DISCLOSED (0.0.25 lane S): a file with more changed definitions than the cap used
    # to lose the rest silently — the page then judged the file on the first few alone.
    # 0.0.25 (Jas: "instead of impact unknown, request the impact calculation on demand"): the
    # automatic pass computes the first IMPACT_MAX_SYMBOLS; the rest come back as `pending`, and
    # the page asks for them BY NAME (`symbols=`) when the human wants them — a capped file is
    # `partial`, never `unknown`.
    out["symbols_changed"] = len(changed_syms)
    if symbols:
        wanted = {n for n in symbols if isinstance(n, str)}
        todo = [s for s in changed_syms if s["name"] in wanted][:IMPACT_ON_DEMAND_MAX]
        out["on_demand"] = True
    else:
        todo = changed_syms[:IMPACT_MAX_SYMBOLS]
        out["on_demand"] = False
    done_names = {s["name"] for s in todo}
    out["pending"] = [] if symbols else [
        {"name": s["name"], "kind": s["kind"], "line": s["line"]}
        for s in changed_syms if s["name"] not in done_names]
    out["symbols_capped"] = bool(out["pending"])
    if out["symbols_capped"]:
        out["cap_note"] = (f"calculated {len(todo)} of {len(changed_syms)} changed definitions "
                           f"(innermost first); {len(out['pending'])} more on request")
    for s in todo:
        callers = run(root, "callers", s["name"], layer=layer)
        blast = run(root, "blast_radius", s["name"], depth=IMPACT_DEPTH, layer=layer)
        tests = sorted({f"{r.path}:{r.line}" + (f" {r.symbol}" if r.symbol else "")
                        for r in blast.references if _is_test(r.path)})
        # lane S: the page is sent at most IMPACT_MAX_CALLERS caller references (a megahub has
        # 85k), with the true count beside them.
        cd = callers.to_dict()
        refs = cd.get("references") or []
        cd["references_total"] = len(refs)
        cd["references_capped"] = len(refs) > IMPACT_MAX_CALLERS
        cd["references"] = refs[:IMPACT_MAX_CALLERS]
        # lane S (with the knowledge lane's `truncated`/`total`): a blast radius the graph CUT is
        # not a complete answer — tests may lie past the cut, so it can never prove `untested`.
        cut = bool(getattr(blast, "truncated", False))
        total = getattr(blast, "total", None)
        bd = {"basis": blast.basis, "backend": blast.backend, "count": blast.count,
              "note": blast.note, "truncated": cut, "total": total}
        if tests:
            verdict = "covered"
        elif blast.structural and not cut:
            verdict = "untested"
        else:
            verdict = "unknown"
        if cut and not tests:
            bd["not_determined"] = (f"not determined — the blast radius was cut at {blast.count}"
                                    + (f" of {total}" if total else "") + " references; tests "
                                    "may lie past the cut")
        out["symbols"].append({
            "name": s["name"], "kind": s["kind"], "line": s["line"],
            "callers": cd, "tests": tests, "blast": bd,
            "covered": bool(tests), "verdict": verdict})
    verdicts = {s["verdict"] for s in out["symbols"]}
    out["status"] = ("untested" if "untested" in verdicts else
                     "unknown" if "unknown" in verdicts else
                     "partial" if out["pending"] else "covered")
    h = graph_health(root)
    if h:
        out["graph_note"] = degraded_line(h)
    return out


__all__ = ["PathRefused", "contained_rel", "walk_files", "tree", "read_file", "outline",
           "symbol_at", "fuzzy", "matcher_name", "quick_open", "symbol_index", "run",
           "query_payload", "parse_lines", "impact"]
