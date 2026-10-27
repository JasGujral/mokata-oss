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
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diffs as D
from . import snapshots as S
from ..errors import MokataError
from ..knowledge.query import BUDGET_DEFAULT, BUDGET_FULL

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


#: Entries one `/api/tree` call returns (directories first, then files). A directory or a filter
#: with more is PAGED (`next` = the offset to ask for), never cut silently. ~70 B per entry, so a
#: page stays far under the 300 KB per call PRD R19 sets (TypeScript's 7k-file test dir included).
TREE_PAGE = 2000
TREE_PRUNED = ("virtualenvs (any name), conda envs, vendor/install dirs, nested checkouts and "
               "hidden dirs are never listed — the same rule the graph indexes by")


class _DirIndex:
    """The kept file list as directories: dir -> (child dirs, direct files), and the number of
    files under every directory. Built once per file list (O(files × depth)), so a directory
    listing never walks the disk nor the whole list again."""

    def __init__(self, files: Sequence[str]):
        self.files = files
        self.subdirs: Dict[str, set] = {"": set()}
        self.direct: Dict[str, List[str]] = {"": []}
        self.under: Dict[str, int] = {"": len(files)}
        for p in files:
            parts = p.split("/")
            parent = ""
            for i in range(len(parts) - 1):
                d = "/".join(parts[:i + 1])
                if d not in self.subdirs:
                    self.subdirs[d] = set()
                    self.direct[d] = []
                    self.subdirs[parent].add(d)
                self.under[d] = self.under.get(d, 0) + 1
                parent = d
            self.direct[parent].append(p)


_DIR_INDEX: Dict[str, Tuple[int, _DirIndex]] = {}


def _dir_index(root: str) -> _DirIndex:
    files = files_of(root)
    key = os.path.abspath(root)
    hit = _DIR_INDEX.get(key)
    if hit is not None and hit[0] == id(files) and hit[1].files is files:
        return hit[1]
    idx = _DirIndex(files)
    if len(_DIR_INDEX) > MAX_ROOTS:
        _DIR_INDEX.clear()
    _DIR_INDEX[key] = (id(files), idx)
    return idx


#: …and at most this many bytes of entries per call: TypeScript's 7,028-file test directory has long
#: paths, so 2,000 entries came to 361 KB (measured) — the byte budget keeps every call ≤ 300 KB.
TREE_PAGE_BYTES = 240 * 1024


def _entry_bytes(e: Any) -> int:
    if isinstance(e, dict):
        return 48 + 2 * len(e.get("path", "")) + len(e.get("name", ""))
    return 8 + len(str(e))


def _page(rows: List[Any], offset: int, limit: int) -> Tuple[List[Any], Optional[int]]:
    offset = max(0, offset)
    got: List[Any] = []
    used = 0
    for e in rows[offset:offset + limit]:
        used += _entry_bytes(e)
        if got and used > TREE_PAGE_BYTES:
            break
        got.append(e)
    nxt = offset + len(got)
    return got, (nxt if nxt < len(rows) else None)


def tree(root: str, dir: str = "", filter: str = "", offset: int = 0,
         limit: int = TREE_PAGE) -> Dict[str, Any]:
    """The explorer, LAZY (0.0.26 P35, G13/R19). It used to send every path (TypeScript: 50,000
    of 67,887, 4.2 MB, `truncated`) and filter in the page, so 17.9k files could not be found
    from Files. Now one call answers ONE of:

      * a directory's children (`dir=`, '' = the root): sub-directories with the number of files
        under each, then its files — paged by `offset`/`next`;
      * a FILTER over every path in the repo (`filter=`, case-insensitive substring; basename
        hits first) — paged the same way, with the true `total`.

    The list is the same kept walk ⌘P reads (re-walked on a change signal)."""
    idx = _dir_index(root)
    limit = max(1, min(int(limit or TREE_PAGE), TREE_PAGE))
    out: Dict[str, Any] = {"sample": False, "count": len(idx.files), "pruned": TREE_PRUNED}
    flt = (filter or "").strip().lower()
    if flt:
        base_hits, path_hits = [], []
        for p in idx.files:
            lp = p.lower()
            if flt in lp:
                (base_hits if flt in lp[lp.rfind("/") + 1:] else path_hits).append(p)
        rows = base_hits + path_hits
        got, nxt = _page(rows, offset, limit)
        out.update(mode="filter", filter=filter, total=len(rows), offset=max(0, offset),
                   next=nxt, entries=[{"kind": "file", "path": p,
                                       "name": p[p.rfind("/") + 1:]} for p in got])
        return out
    d = (dir or "").strip("/")
    if d and d not in idx.subdirs:
        d = contained_rel(root, d)
        if d not in idx.subdirs:
            raise PathRefused("no such directory in the explorer's list")
    rows: List[Dict[str, Any]] = [
        {"kind": "dir", "path": sd, "name": sd[sd.rfind("/") + 1:], "files": idx.under.get(sd, 0)}
        for sd in sorted(idx.subdirs[d])]
    rows += [{"kind": "file", "path": p, "name": p[p.rfind("/") + 1:]}
             for p in sorted(idx.direct[d])]
    got, nxt = _page(rows, offset, limit)
    out.update(mode="dir", dir=d, total=len(rows), offset=max(0, offset), next=nxt, entries=got,
               files_under=idx.under.get(d, 0))
    return out


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
        # 0.0.26 P1: a text file past the whole-file cap is still viewable — in windows, through
        # `/api/highlight` (a sparse chunk index; never read whole). No change marks past the cap.
        from . import highlight as H
        big = H.probe(root, rel)
        if big is not None:
            return {"sample": False, "path": rel, "text": None, "windowed": True,
                    "lines": big["lines"], "size": big["size"], "changes": [],
                    "changes_against": "",
                    "note": f"large file ({big['lines']:,} lines) — read in windows of "
                            f"{H.CHUNK:,} lines; change marks are not computed for a file this "
                            f"size"}
        return {"sample": False, "path": rel, "text": None,
                "note": "not shown — binary, too large, not a regular file, or not readable"}
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    changes = _changes_vs_head(root, rel)
    for c in changes:
        c["end"] = min(c["end"], max(lines, 1))
    return {"sample": False, "path": rel, "text": text, "lines": lines, "changes": changes,
            "changes_against": "HEAD"}


# --------------------------------------------------------------------------------------- outline

def outline(rel: str, text: Optional[str], root: Optional[str] = None) -> Dict[str, Any]:
    """The definitions in one file. Python: the graph layer's own AST parse (`ast_backend`), fresh
    from the text. Every other language code-review-graph indexes: the graph's NODE TABLE
    (`knowledge.crg_nodes`, 0.0.26 P35) — with end lines and nesting, and for a file too large to
    read whole. TS without a built graph: `ts_edges` when `[graph-ts]` is in. A language nothing
    indexes is SAID ("no index for Go — …"), never shown as an empty outline. Each symbol: name,
    kind, line, end (None when unknown) and depth."""
    from ..knowledge import ast_backend as AB
    from ..knowledge import ts_edges as TS
    if rel.endswith(".py") and text is not None:
        return _python_outline(rel, text, AB)
    crg = _crg_outline(root, rel) if root else None
    if crg is not None:
        return crg
    if text is None:
        size = _size_of(root, rel) if root else None
        if size is not None and size > _read_cap():
            # 0.0.26 P35 (G33) — say WHY: it was "not readable", which read as a broken file.
            return {"path": rel, "symbols": [], "backend": "", "basis": "",
                    "note": f"too large to outline ({_mb(size)}) — build the code graph "
                            f"(`mokata graph build`) to outline it from the index"}
        return {"path": rel, "symbols": [], "backend": "", "basis": "", "note": "file not readable"}
    if TS.is_ts_source(rel):
        if not TS.available():
            return {"path": rel, "symbols": [], "backend": "", "basis": "",
                    "note": "TypeScript outline needs the optional graph extra: "
                            + _graph_ts_line() + " — or build the code graph (`mokata graph "
                            "build`)"}
        try:
            fe = TS.parse_source(text, rel)
        except RecursionError:
            # 0.0.26 P35 — never a raw 500: the TS walk is iterative (O13), but one pathological
            # file must stay an outline NOTE even if a future parse seam recurses again.
            fe = None
        if fe is None:
            return {"path": rel, "symbols": [], "backend": "ts", "basis": "",
                    "note": "the file does not parse — no outline"}
        return {"path": rel, "backend": "ts", "basis": "structural", "note": "",
                "symbols": [{"name": n, "kind": k, "line": ln, "end": None, "depth": 0}
                            for n, ln, k in sorted(fe.defs, key=lambda d: d[1])]}
    return {"path": rel, "symbols": [], "backend": "", "basis": "",
            "note": no_index_note(rel)}


def no_index_note(rel: str) -> str:
    """P35 (G7) — a language no index covers is STATED, with the step that indexes it."""
    from ..knowledge.crg_nodes import label_of, language_of
    lang = language_of(rel)
    if lang is None:
        return "no outline for this file type"
    return (f"no index for {label_of(lang)} — build the code graph (`mokata graph build`) to "
            f"outline, ⌘P and go to definitions in it")


def _read_cap() -> int:
    return S.MAX_FILE_BYTES


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB"


def _size_of(root: str, rel: str) -> Optional[int]:
    try:
        return os.path.getsize(os.path.join(root, *rel.split("/")))
    except OSError:
        return None


def _python_outline(rel: str, text: str, AB: Any) -> Dict[str, Any]:
    try:
        tree_ = AB.parse_source(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {"path": rel, "symbols": [], "backend": "ast", "basis": "",
                "note": f"the file does not parse ({type(exc).__name__}) — no outline"}
    syms: List[Dict[str, Any]] = []
    # an explicit stack (P35): a pathologically nested file never recurses the outline itself
    stack: List[Tuple[Any, int, bool]] = [(tree_, 0, False)]
    while stack:
        node, depth, in_class = stack.pop()
        kids = []
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kids.append(({"name": ch.name, "kind": "method" if in_class else "function",
                              "line": ch.lineno, "end": getattr(ch, "end_lineno", ch.lineno),
                              "depth": depth}, ch, depth + 1, False))
            elif isinstance(ch, ast.ClassDef):
                kids.append(({"name": ch.name, "kind": "class", "line": ch.lineno,
                              "end": getattr(ch, "end_lineno", ch.lineno), "depth": depth},
                             ch, depth + 1, True))
            elif isinstance(ch, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                kids.append((None, ch, depth, in_class))
        for sym, ch, d, ic in reversed(kids):
            stack.append((ch, d, ic))
        for sym, _ch, _d, _ic in kids:
            if sym is not None:
                syms.append(sym)
    syms.sort(key=lambda s: (s["line"], s["depth"]))
    return {"path": rel, "symbols": syms, "backend": "ast", "basis": "structural", "note": ""}


def _crg_outline(root: str, rel: str) -> Optional[Dict[str, Any]]:
    """One file's outline from code-review-graph's node table, or None (no built index, or the
    index holds no such file). An index older than the file is SAID (lines may have moved)."""
    from ..knowledge import crg_nodes as N
    reader = N.reader(root)
    if reader is None:
        return None
    import sqlite3
    try:
        got = reader.outline(rel)
    except (sqlite3.Error, OSError, ValueError):
        return None                          # an unreadable index is "no index"
    if got is None:
        return None
    note = ""
    try:
        with open(os.path.join(root, *rel.split("/")), "rb") as fh:
            current = N.file_digest(fh.read())
        if got["file_hash"] and current != got["file_hash"]:
            note = ("the code graph indexed an older version of this file — lines may have "
                    "moved; it re-indexes on the next update")
    except OSError:
        pass
    syms = [{k: s[k] for k in ("name", "kind", "line", "end", "depth", "qualified_name")}
            for s in got["symbols"]]
    return {"path": rel, "symbols": syms, "backend": "code-review-graph",
            "basis": "structural", "note": note, "language": got["language"]}


def _graph_ts_line() -> str:
    """The installer-aware, platform-quoted `mokata[graph-ts]` line (0.0.26 L1)."""
    from ..install_route import plan_extra_install
    return plan_extra_install("graph-ts").display


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
    char it precedes, so the first occurrence is always the right one; the quantifier is possessive
    (Python 3.11+, inside mokata's 3.12 floor) so a miss costs one linear scan, never a
    backtracking search."""
    parts = []
    for ch in ql:
        alts = {ch, ch.upper(), ch.lower()}
        cls = "".join(re.escape(a) for a in sorted(alts))
        parts.append(f"[^{cls}]*+[{cls}]")
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
#   * the SYMBOL list is read off the graph layer's OWN AST edge index (`AstBackend.definitions()`) — in
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
    try:
        fe = TS.parse_source(src, rel) if TS.available() else None
    except RecursionError:
        return []          # P35: one pathological file is "unparsed", never the whole build
    return list(fe.defs) if fe else []


def _ast_floor(layer: Any) -> Any:
    """The layer's AST floor (primary or fallback), or None."""
    from ..knowledge.ast_backend import AstBackend
    for b in (getattr(layer, "primary", None), getattr(layer, "fallback", None)):
        if isinstance(b, AstBackend):
            return b
    return None


class _Symbols:
    """One repo's ⌘P symbol rows. Sources, per file (0.0.26 P35):

      * the AST floor's edge index for the files it holds (Python, TS with `[graph-ts]`) — read
        from the current files, so a just-saved edit is in it;
      * code-review-graph's NODE TABLE (`knowledge.crg_nodes`) for every other file its BUILT
        index holds, in every language it indexes (kubernetes: 136k Go definitions in 0.4 s warm);
      * with neither, a walk of the parse seams (`_defs_of`), kept per file so it resumes.

    `languages` says what the rows cover, so a language nothing indexes is stated, not shown as
    "no matches"."""

    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        self.rows: Optional[List[Tuple[str, str, int, str]]] = None
        self.names: List[str] = []
        self.by_name: Dict[str, List[Tuple[str, str, int, str]]] = {}
        self.source: Any = None             # (floor generation or _WALKED, graph stamp or None)
        self.crg_files: set = set()          # files whose rows came from code-review-graph
        self.languages: Dict[str, int] = {}  # language -> definitions the rows hold
        self.error = ""                      # the last build's failure (P35: never "loading" forever)
        self.graph_rows: Optional[Tuple[Any, List[Tuple[str, str, int, str]], Dict[str, int]]] = None
        self.walked: Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]] = {}
        self.at = 0.0
        self.dirty = False
        self.done = threading.Event()
        self.done.set()

    def _stale(self) -> bool:
        if self.rows is None or self.dirty:
            return True
        floor_src, stamp = self.source if isinstance(self.source, tuple) else (self.source, None)
        from ..knowledge import crg_nodes as N
        reader = N.reader(self.root)
        if (reader.stamp() if reader is not None else None) != stamp:
            return True
        if floor_src is _WALKED:
            return time.monotonic() - self.at > FILES_MAX_AGE
        if floor_src is _GRAPH_ONLY:
            return False
        hit = _LAYERS.get(os.path.abspath(self.root))
        floor = _ast_floor(hit[1]) if hit is not None else None
        if floor is None:
            return True
        gen = floor.index_generation
        return gen is None or gen != floor_src

    def get(self, wait: Optional[float]):
        """(rows or None, state) — state is `ready`, `refreshing` (older rows, a rebuild running),
        `building` (no rows yet) or `failed` (the last build raised; `error` says why)."""
        with self.lock:
            stale = self._stale()
            # `done` is set in the build's `finally`, AFTER it published — a thread still alive
            # with `done` set is only unwinding, and waiting on it would serve the old rows once
            # more (seen by O8's rebuild test: ⌘P missed a definition right after a rebuild).
            if stale and self.done.is_set() and not (self.error and self.rows is None
                                                     and not self.dirty):
                drop, self.dirty = self.dirty, False
                self.done = threading.Event()
                done = self.done
                start = True
            else:
                start = False
            done = self.done
        if start:
            # 0.0.26 P32 — the build is an `index.symbols` JOB of the serving IDE (visible in
            # Activity with n/N, cancellable); with no server it is a plain thread.
            from .jobs import background
            background("index.symbols", {"root": os.path.abspath(self.root)},
                       lambda: self._build(done, drop), label="symbol index (⌘P)",
                       scope=os.path.basename(os.path.abspath(self.root)), on_end=done.set)
        if stale:
            done.wait(wait)
        with self.lock:
            if self.rows is None:
                return None, ("failed" if self.error and done.is_set() else "building")
            return self.rows, ("ready" if done.is_set() else "refreshing")

    def _build(self, done: threading.Event, drop: bool) -> None:
        from ..knowledge import crg_nodes as N
        from ..knowledge.cancel import Cancelled
        try:
            try:
                layer = _layer(self.root)
            except MokataError:
                layer = None                 # not a repo mokata can load a layer for: walk it
            floor = _ast_floor(layer) if layer is not None else None
            reader = N.reader(self.root)
            stamp = reader.stamp() if reader is not None else None
            graph_rows: List[Tuple[str, str, int, str]] = []
            langs: Dict[str, int] = {}
            if reader is not None:
                kept = self.graph_rows
                if kept is not None and kept[0] == stamp:
                    graph_rows, langs = kept[1], dict(kept[2])    # the graph did not move
                else:
                    graph_rows, langs = reader.symbols()
                    self.graph_rows = (stamp, graph_rows, dict(langs))
            if floor is not None:
                if drop:
                    floor.invalidate()       # a change signal: re-read only what moved
                # O8 — the floor's PUBLIC accessor (loads via its (mtime, size) disk cache). The
                # generation is read on both sides: a rebuild that interleaves leaves the rows
                # marked stale (None), so the next ask rebuilds them rather than trusting them.
                before = floor.index_generation
                own = floor.definitions()
                after = floor.index_generation
                src: Any = after if before in (None, after) else None
            elif reader is not None:
                own, src = [], _GRAPH_ONLY
            else:
                own, src = self._walk(), _WALKED
            mine = {r[1] for r in own}
            rows = list(own) + [r for r in graph_rows if r[1] not in mine]
            for r in own:
                lang = _lang_key(r[1])
                langs[lang] = langs.get(lang, 0) + 1
            self._publish(rows, (src, stamp), {r[1] for r in graph_rows} - mine, langs)
        except Cancelled:
            raise
        except Exception as exc:             # noqa: BLE001 — a failed build is a STATE (P35)
            with self.lock:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            done.set()

    def _walk(self) -> List[Tuple[str, str, int, str]]:
        from ..knowledge import ts_edges as TS
        from ..knowledge.cancel import checkpoint, report
        cur: Dict[str, Tuple[float, int, List[Tuple[str, int, str]]]] = {}
        ts_on = TS.available()
        todo = [rel for rel in walk_files(self.root)
                if rel.endswith(".py") or (ts_on and TS.is_ts_source(rel))]
        for i, rel in enumerate(todo):
            checkpoint()
            if i % 200 == 0:
                report(done=i, total=len(todo), unit="files", note="reading definitions")
            ab = os.path.join(self.root, *rel.split("/"))
            try:
                st = os.stat(ab)
            except OSError:
                continue
            hit = self.walked.get(rel)
            if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
                cur[rel] = hit
            else:
                cur[rel] = (st.st_mtime, st.st_size, _defs_of(ab, rel))
                # 0.0.26 P35 (A-#4) — kept PER FILE as it is read: a build that dies or is
                # cancelled part-way resumes from the files it already read, never from zero.
                self.walked[rel] = cur[rel]
        self.walked = cur
        return [(name, rel, int(line), kind)
                for rel, (_m, _s, defs) in sorted(cur.items()) for name, line, kind in defs]

    def _publish(self, rows: List[Tuple[str, str, int, str]], source: Any,
                 crg_files: Optional[set] = None, langs: Optional[Dict[str, int]] = None) -> None:
        by_name: Dict[str, List[Tuple[str, str, int, str]]] = {}
        for r in rows:
            by_name.setdefault(r[0], []).append(r)
        with self.lock:
            self.rows, self.source, self.at = rows, source, time.monotonic()
            self.by_name, self.names = by_name, sorted(by_name)
            self.crg_files = crg_files or set()
            self.languages = dict(langs or {})
            self.error = ""

    def invalidate(self) -> None:
        with self.lock:
            self.dirty = True


_GRAPH_ONLY = "graph"           # the symbol source when only code-review-graph's index answers


def _lang_key(rel: str) -> str:
    from ..knowledge.crg_nodes import language_of
    return language_of(rel) or ""


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
    # P41 — a session's worktree edits arrive as the SAME repo's change signal (hooks post to the
    # main checkout's IDE), so the worktree lists ⌘P keeps are re-read too (only marked here).
    with _PER_ROOT_LOCK:
        others = [v for (r, _k), v in _PER_ROOT.items() if r != os.path.abspath(root)]
    for v in others:
        v.invalidate()


def symbol_index(root: str) -> List[Tuple[str, str, int, str]]:
    """Every definition in the repo's own source: (name, path, line, kind) — the AST floor's own
    `defs`, so ⌘P can only name symbols `mokata query defs` can find. Waits for the index."""
    rows, _state = _per_root(root, "symbols", _Symbols).get(None)
    return list(rows or [])


def quick_open(root: str, q: str, changed: Sequence[str] = (),
               files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """⌘P. Sections in a FIXED order — changed files (always first), files, symbols. `@x` asks
    the workspace symbols only (the page's palette sends it for its `#` prefix — 0.0.26 P3). The
    matcher that ranked is named. Never waits on the symbol index longer than QUICK_WAIT_SECS:
    `symbols_state` says what answered."""
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
            if out["symbols_state"] == "failed":
                out["symbols_note"] = (f"the symbol index failed to build ({sx.error}) — "
                                       "rebuild it from Activity")
            else:
                out["symbols_note"] = SYMBOLS_BUILDING_NOTE
                prog = _symbols_progress(root)
                if prog:
                    out["symbols_progress"] = prog
        else:
            with sx.lock:
                names, by_name, from_graph = sx.names, sx.by_name, sx.crg_files
                langs = dict(sx.languages)
            rows, matcher = fuzzy(needle, names, QUICK_LIMIT)
            syms = []
            for name, s in rows:
                for _n, rel, line, kind in by_name.get(name, [])[:3]:
                    syms.append({"name": name, "path": rel, "line": line, "kind": kind,
                                 "score": round(s, 2), "basis": "structural",
                                 "backend": "code-review-graph" if rel in from_graph else "ast"})
            out["symbols"] = syms[:QUICK_LIMIT]
            missing = unindexed_languages(root, langs)
            if missing:
                # P35 (G7): a language the symbol rows do not cover is SAID — k8s used to answer
                # "ready" with 0 symbols and no note.
                out["symbols_note"] = (f"no symbol index for {', '.join(missing)} — build the "
                                       f"code graph (`mokata graph build`) to search its symbols")
                out["symbols_unindexed"] = missing
    out["matcher"] = matcher
    out["matcher_note"] = ("" if matcher == "rapidfuzz" else
                           "pure-Python floor — pip install rapidfuzz for the accelerator")
    return out


def _symbols_progress(root: str) -> Optional[Dict[str, Any]]:
    """n/N of the running `index.symbols` job, when a server runs it (P35 R14)."""
    from .jobs import installed
    reg = installed()
    job = reg.find("index.symbols", {"root": os.path.abspath(root)}) if reg is not None else None
    if job is None:
        return None
    snap = job.snapshot()
    return {k: snap.get(k) for k in ("id", "state", "done", "total", "unit", "note")}


#: A language counts as present in the repo from this many of its files (a stray script does not
#: earn a "no index for Bash" note).
UNINDEXED_MIN_FILES = 5


_LANG_COUNTS: Dict[int, Tuple[Any, Dict[str, int]]] = {}


def _languages_of(files: List[str]) -> Dict[str, int]:
    """language -> file count of a kept file list, computed once per list (not per keystroke)."""
    from ..knowledge.crg_nodes import LANGUAGE_OF_EXT
    hit = _LANG_COUNTS.get(id(files))
    if hit is not None and hit[0] is files:
        return hit[1]
    seen: Dict[str, int] = {}
    for rel in files:
        dot = rel.rfind(".")
        if dot > rel.rfind("/"):
            lang = LANGUAGE_OF_EXT.get(rel[dot:].lower())
            if lang is not None:
                seen[lang] = seen.get(lang, 0) + 1
    if len(_LANG_COUNTS) > MAX_ROOTS:
        _LANG_COUNTS.clear()
    _LANG_COUNTS[id(files)] = (files, seen)
    return seen


def unindexed_languages(root: str, langs: Dict[str, int]) -> List[str]:
    """The languages with ≥ UNINDEXED_MIN_FILES source files in the repo that the symbol rows do
    not cover, as labels ("Go"). Reads the kept file list — no walk of its own."""
    from ..knowledge.crg_nodes import label_of
    seen = _languages_of(files_of(root))
    covered = {k for k, n in langs.items() if n}
    # a TS repo's .tsx rows count for TypeScript and vice versa
    if covered & {"typescript", "tsx"}:
        covered |= {"typescript", "tsx"}
    out = []
    for lang, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        label = label_of(lang)
        if n >= UNINDEXED_MIN_FILES and lang not in covered and label not in out \
                and lang != "bash":
            out.append(label)
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
    if getattr(layer, "main_layer", None) is not None:
        return          # P41: a worktree's layer borrows the main layer's helper; that one closes it
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
    Serialised: two page calls arriving together share one reconcile instead of racing two.

    0.0.26 P37 (R39) — in the IDE the reconcile is a JOB (`index.fresh`: visible in Activity, with
    the update's n/N files, cancellable), never inline inside a query: a query waits for it at
    most `jobs.SYNC_WINDOW_S` (a reconcile that finds nothing is answered at once, as before) and
    otherwise answers from the last complete graph, saying "updating…". Outside the IDE (no job
    registry: the CLI, tests) it runs inline, unchanged."""

    def __init__(self, inner: Any, window: float = FRESH_WINDOW_SECS, clock: Any = time.monotonic):
        self._inner, self._window, self._clock = inner, window, clock
        self._lock = threading.Lock()
        self._at: Optional[float] = None
        self._out: Any = None
        self._job: Any = None

    def _cached(self) -> Any:
        from dataclasses import is_dataclass, replace
        out = self._out
        return replace(out, rebuilt=False, changed=[]) if is_dataclass(out) else out

    def ensure_fresh(self, layer: Any) -> Any:
        from . import jobs
        reg = jobs.installed()
        if reg is None:
            with self._lock:
                if self._at is not None and self._clock() - self._at < self._window:
                    return self._cached()
                self._out = self._inner.ensure_fresh(layer)
                self._at = self._clock()
                return self._out
        with self._lock:
            if self._at is not None and self._clock() - self._at < self._window:
                return self._cached()
            job = self._job
            if job is None or job.finished.is_set():
                root = os.path.abspath(str(getattr(self._inner, "root", "") or ""))
                job = self._job = reg.start(
                    "index.fresh", {"root": root}, lambda: self._reconcile(layer),
                    label="keeping the code graph current", scope=os.path.basename(root),
                    keep=True)
        job.finished.wait(jobs.SYNC_WINDOW_S)
        with self._lock:
            if job.finished.is_set() and self._out is not None:
                return self._out            # the reconcile finished inside the window: as before
        from ..knowledge.freshness import FreshnessOutcome
        return FreshnessOutcome(fresh=True, note=UPDATING_NOTE)

    def _reconcile(self, layer: Any) -> Dict[str, Any]:
        out = self._inner.ensure_fresh(layer)
        with self._lock:
            self._out, self._at = out, self._clock()
        return {"rebuilt": bool(getattr(out, "rebuilt", False)),
                "changed": len(getattr(out, "changed", []) or []),
                "note": str(getattr(out, "note", "") or "")[:300]}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


#: What a query says while its reconcile (an index update) runs as a job.
UPDATING_NOTE = ("code graph updating… — answered from the last complete graph; the update runs "
                 "in Activity")


class _StatFreshness:
    """The freshness of the FLOOR layer on a repo mokata has not set up (0.0.26 P29): no git
    reconcile and no run-state (those write under `.mokata/`) — the AST floor drops its index and
    re-stats on the next question, re-parsing only files whose (mtime, size) moved. Wrapped in
    `_OncePerWindow`, so it runs at most once per window, like the governed reconcile."""

    def ensure_fresh(self, layer: Any) -> None:
        for b in (getattr(layer, "primary", None), getattr(layer, "fallback", None)):
            drop = getattr(b, "invalidate", None)
            if callable(drop):
                drop()
        return None

    def recheck_after_answer(self, _layer: Any, _result: Any) -> bool:
        return False


def _floor_layer(root: str):
    """The knowledge layer of a repo with no `.mokata/manifest.json`: what `mokata init` would
    wire (the standard profile's chain, in memory — nothing is written), answered by the FLOOR
    only — the AST reader on a Python repo, the lexical floor otherwise. No PATH-detected external
    graph is used here: its first use writes a disclosure marker and its own index into the repo,
    and a folder someone merely opened is not consent to that. Its parse cache goes to the user's
    profile (`statedir.ast_cache_dir`), never into the repo."""
    from .. import __version__
    from ..detect import Detector
    from ..knowledge import KnowledgeLayer
    from ..knowledge.ast_backend import AstBackend
    from ..knowledge.layer import GrepBackend, _floor_backend
    from ..manifest import Manifest
    from ..profiles import build_manifest_data
    from ..router import Router
    from .statedir import ast_cache_dir
    router = Router(Manifest.from_dict(build_manifest_data("standard", __version__)),
                    Detector())
    floor = _floor_backend(router, root)
    if isinstance(floor, AstBackend):
        floor = AstBackend(root=root, grep=GrepBackend(root=root, name="grep"),
                           cache_dir=ast_cache_dir(root))
    return KnowledgeLayer(floor, None, freshness=_StatFreshness())


def _tree_layer(tree: str, main: str):
    """P41 — a linked worktree's layer: the main checkout's (cached) layer with the worktree's
    changed files overlaid (`knowledge.tree_overlay`). Its changed set is re-read at most once per
    FRESH_WINDOW_SECS, the same window the main layer's freshness reconcile uses."""
    from ..knowledge.tree_overlay import TreeLayer
    base = _layer(main)
    where = os.path.abspath(tree)
    with _LAYERS_LOCK:
        hit = _LAYERS.get(where)
        if hit is not None and hit[0] == ("tree", id(base)):
            return hit[1]
        layer = TreeLayer.over(base, tree, window=FRESH_WINDOW_SECS)
        _LAYERS[where] = (("tree", id(base)), layer)
    return layer


def _layer(root: str):
    from ..config import Surface
    from ..knowledge import KnowledgeLayer
    from ..tree_scope import code_tree_for
    from .statedir import initialized
    wt = code_tree_for(root)
    if wt is not None:
        return _tree_layer(*wt)
    key, where = _manifest_key(root), os.path.abspath(root)
    with _LAYERS_LOCK:
        hit = _LAYERS.get(where)
        if hit is not None and hit[0] == key:
            return hit[1]
        layer = KnowledgeLayer.from_surface(Surface.load(root)) if initialized(root) \
            else _floor_layer(root)
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


# 0.0.26 P32 — a page that stopped wanting an answer stops the work: heavy questions run as JOBS
# (`ide/jobs.py`) whose Cancel sets this thread's flag (`knowledge.cancel`); `run` checks it
# before and between graph questions, and the walks underneath check it per file.

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


def run(root: str, kind: str, target: str, depth: int = 2, layer: Any = None,
        budget: str = BUDGET_DEFAULT):
    """The SAME call `mokata query` makes: `knowledge.layer.run_query` over the repo's own
    `KnowledgeLayer` (its configured graph, else the AST floor, else grep). Checks first that
    its job was not cancelled (`knowledge.cancel.checkpoint`). 0.0.26 O7: `budget="full"` (blast
    radius only, asked for by a human) walks long — and checks again between its steps, so a page
    that stops waiting stops the walk."""
    from ..knowledge.cancel import checkpoint
    from ..knowledge.layer import run_query
    checkpoint()
    layer = layer if layer is not None else _layer(root)
    result = run_query(layer, kind, target, depth=depth, budget=budget, checkpoint=checkpoint)
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
           symbols: Optional[Sequence[str]] = None,
           budget: str = BUDGET_DEFAULT) -> Dict[str, Any]:
    """One changed file's structural impact: which symbols the changed lines sit in, who calls
    each, and which TESTS reach each through the blast radius — every list carrying the `basis`
    of the query that produced it. `untested` is claimed ONLY on a structural answer; a lexical
    one says `unknown` (an absence of evidence is never shown as evidence of absence)."""
    rel = contained_rel(root, path)
    is_test = _is_test(rel)
    text = D._read_text(root, rel)
    ol = outline(rel, text, root)
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
    # 0.0.26 O7 (BLAST-FULL-ON-REQUEST) — the full budget is ONLY ever on request, for ONE named
    # symbol: a radius the default bound cut comes back `full_available`, and the page asks again
    # for that symbol with `budget=full`. One symbol keeps the request inside one hard ceiling.
    full = budget == BUDGET_FULL
    if budget not in (BUDGET_DEFAULT, BUDGET_FULL):
        raise ValueError(f"budget must be '{BUDGET_DEFAULT}' or '{BUDGET_FULL}'")
    if full and (not symbols or len(symbols) != 1):
        raise ValueError("the full blast-radius budget is asked for one named symbol at a time")
    out["budget"] = budget
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
        blast = run(root, "blast_radius", s["name"], depth=IMPACT_DEPTH, layer=layer,
                    budget=budget)
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
              "note": blast.note, "truncated": cut, "total": total, "budget": budget,
              # O7: a cut the DEFAULT bound made can be walked again with the full budget.
              "full_available": cut and not full}
        if tests:
            verdict = "covered"
        elif blast.structural and not cut:
            verdict = "untested"
        else:
            verdict = "unknown"
        if cut and not tests:
            bd["not_determined"] = (f"not determined — the blast radius was cut at {blast.count}"
                                    + (f" of {total}" if total else "") + " references"
                                    + (" even with the full budget" if full else "")
                                    + "; tests may lie past the cut")
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
