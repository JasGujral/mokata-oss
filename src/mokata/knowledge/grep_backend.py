"""B3 — the grep floor: a lexical implementation of the typed query API.

This is the *documented fallback* when no structural graph is present. It is regex/text
search over source files — NOT a parser and NOT a graph. Results are approximate by
design (every result is marked `degraded`); the value is that the same typed API keeps
working with zero external dependencies, on any machine, so the engine never hard-fails.

Stage 65: the floor is LANGUAGE-AWARE. The file walk and the structural-query patterns are
driven by `mokata.languages` — a per-language table of lexical heuristics (def/func/fn,
import/require/use, class/impl/interface) covering Python / JS-TS / Go / Rust / Java, with a
generic fallback for any other extension. Still heuristic-only: it stays the floor and the
result `note` announces it is lexical; the adopted graph adapter is always preferred.
"""

from __future__ import annotations

import codecs
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .. import languages
from .cancel import checkpoint, report
from ..repo_paths import RepoName, name_of
from ..repo_walk import prune_source_dirs
from .query import (BASIS_LEXICAL, GREP_FLOOR_IMPACT_NOTE, GREP_FLOOR_NAV_NOTE,
                    IMPACT_KINDS, NAVIGATION_KINDS, QUERY_KINDS,
                    GraphBackend, QueryResult, Reference)

LEXICAL_NOTE = "lexical fallback (no structural graph; results are approximate)"

# Kept as a module symbol for back-compat; the default walk now spans every known language.
SOURCE_EXTENSIONS = languages.SOURCE_EXTENSIONS


_CALL_RE_CACHE: Dict[Tuple[str, str], "re.Pattern"] = {}

#: 0.0.27 P4 (R2-2) — the streaming `refs` scan reads at most this many bytes of a line at once,
#: so one huge line never becomes one huge string.
REFS_BLOCK = 1 << 20
#: A reference's snippet is a DISPLAY window: at most this many characters around the match, `…`
#: marking a clip. A clip of the snippet is never a clip of the answer (every row is kept).
REFS_SNIPPET_MAX = 240


def _clip(text: str, a: int, b: int, cut: bool = False) -> str:
    """The snippet for a match at text[a:b]: the whole stripped line when it fits, else a window
    of `REFS_SNIPPET_MAX` characters around the match with `…` where it was cut. `cut` says the
    text is itself a piece of a longer line."""
    if not cut and len(text) <= REFS_SNIPPET_MAX:
        return text.strip()
    half = max(0, (REFS_SNIPPET_MAX - (b - a)) // 2)
    lo, hi = max(0, a - half), min(len(text), b + half)
    out = text[lo:hi].strip()
    return ("…" if lo > 0 or cut else "") + out + ("…" if hi < len(text) or cut else "")


@dataclass
class RefsScope:
    """What one lexical `refs` scan covered — so its label states both error sides and its reach,
    not only "approximate"."""
    listed: int = 0                     # source files in scope (after pruning + extension filter)
    scanned: int = 0                    # of those, read to the end
    unreadable: List[str] = field(default_factory=list)
    pruned_dirs: int = 0                # directories the walk skipped (hidden/vendor/env/checkout)
    other_files: int = 0                # files the walk saw with a non-source extension
    extensions: Tuple[str, ...] = ()

    def line(self) -> str:
        """The scope sentence an answer carries."""
        exts = " ".join(self.extensions)
        un = len(self.unreadable)
        return (f"lexical scan of {self.scanned} of {self.listed} source files ({exts})"
                + (f"; {un} unreadable" if un else "")
                + (f"; skipped {self.other_files} files of other types" if self.other_files else "")
                + (f" and {self.pruned_dirs} hidden/vendor/environment/nested-checkout "
                   f"directories" if self.pruned_dirs else "")
                + "; word matches, one row per matching LINE — may include comments, strings and other symbols of the same "
                  "name, and misses names built at run time")


#: What may precede a called name. Unqualified only (`x(`, never `a.x(`) — or, for a language
#: whose calls are spelled qualified (`Language.qualified_calls`, P35 G30), also `pkg.x(` /
#: `mod::x(` (still never `fooX(`).
_BARE, _QUALIFIED = r"(?<![\w.])", r"(?<!\w)"


def _before(lang: Optional["languages.Language"]) -> str:
    return _QUALIFIED if lang is not None and lang.qualified_calls else _BARE


def _calls(line: str, sym: str, lang: Optional["languages.Language"] = None) -> bool:
    key = (sym, _before(lang))
    pat = _CALL_RE_CACHE.get(key)
    if pat is None:
        if len(_CALL_RE_CACHE) > 4096:
            _CALL_RE_CACHE.clear()
        pat = _CALL_RE_CACHE[key] = re.compile(key[1] + re.escape(sym) + r"\s*\(")
    return bool(pat.search(line))


class _Counts:
    """0.0.26 P32 — a running callers/refs walk says how far it got ("≥120 so far", n/N files) to
    the job it runs in, at most every `every` seconds. A no-op outside a job."""

    def __init__(self, what: str, files: Any, every: float = 0.3) -> None:
        self.what, self.every = what, every
        self.total = len(files) if isinstance(files, list) else None
        self.at = time.monotonic()

    def __call__(self, n: int, found: int) -> None:
        now = time.monotonic()
        if now - self.at >= self.every:
            self.at = now
            report(done=n, total=self.total, unit="files", note=f"{self.what}: ≥{found} so far")


class GrepBackend(GraphBackend):
    is_graph = False

    def __init__(self, root: str, name: str = "grep",
                 extensions: Tuple[str, ...] = SOURCE_EXTENSIONS) -> None:
        self.root = root
        self.name = name
        self.extensions = tuple(extensions)

    # --- public API ----------------------------------------------------------
    def query(self, kind: str, target: str, depth: int = 1,
              files: Optional[List[str]] = None) -> QueryResult:
        """`files` (0.0.25 K) SCOPES the corpus to those absolute paths instead of the repo walk —
        the AST floor passes the files its index does NOT hold, so the lexical floor never re-reads
        Python the structural index has already answered for."""
        # 0.0.23 `BLAST-RADIUS-WALKS-THE-CORPUS-PER-SYMBOL` — every helper used to re-walk and
        # re-read the whole tree, so a depth-2 blast radius read the corpus once PER CALLER found
        # at hop 1 (31 full reads for 30 callers) and `spec amend`'s 20 s lens timed out on an
        # ordinary repo. The corpus is now read ONCE per query and held only for that query —
        # never across calls, so an edit between two queries is always seen.
        self._corpus_cache = {} if files is None else {None: list(files)}
        try:
            return self._query(kind, target, depth)
        finally:
            self._corpus_cache = None

    def _query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
        if kind not in QUERY_KINDS:
            raise ValueError(
                f"unknown query kind '{kind}'; one of {QUERY_KINDS}"
            )
        if kind == "callers":
            refs = self._callers(target)
        elif kind == "callees":
            refs = self._callees(target)
        elif kind == "implementers":
            refs = self._implementers(target)
        elif kind == "imports":
            refs = self._imports(target)
        elif kind == "defs":
            refs = self._defs(target)
        elif kind == "refs":
            scope = RefsScope()
            refs = list(self.iter_refs(target, scope))
        else:
            refs = self._blast_radius(target, depth)
        # CRG-NAV (d): a NAVIGATION answer produced by the lexical floor says so, and names the
        # one step that buys the full chain. Attached here — at the floor itself — so it rides
        # every route that reaches it (the layer's degrade, the AST floor's fallthrough, or a
        # direct grep-only repo) with no second mechanism. Impact (`blast_radius`) is excluded:
        # that is GRAPH-FIRST-IMPACT's surface, not this row's.
        note = LEXICAL_NOTE
        if kind == "refs":
            note = f"{note} | {scope.line()}"
        if kind in NAVIGATION_KINDS:
            note = f"{note} | {GREP_FLOOR_NAV_NOTE}"
        elif kind in IMPACT_KINDS:
            # GRAPH-FIRST-IMPACT (d) — the impact twin: a lexical blast radius says it is degraded.
            note = f"{note} | {GREP_FLOOR_IMPACT_NOTE}"
        return QueryResult(
            kind=kind, target=target, references=refs, backend=self.name,
            basis=BASIS_LEXICAL, note=note,
        )

    # --- file helpers --------------------------------------------------------
    def _files(self):
        cache = getattr(self, "_corpus_cache", None)
        if cache is not None:
            files = cache.get(None)
            if files is None:
                files = cache[None] = list(self._walk_files())
            return files
        return self._walk_files()

    def _read(self, path: str) -> List[str]:
        checkpoint()                    # P32: a cancelled job stops here, per file
        cache = getattr(self, "_corpus_cache", None)
        if cache is None:
            return self._read_file(path)
        lines = cache.get(path)
        if lines is None:
            lines = cache[path] = self._read_file(path)
        return lines

    def _walk_files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Skip hidden dirs (e.g. .mokata, .git) so config/state isn't scanned, AND nested
            # checkouts — a vendored dependency or a worktree is somebody else's source, and
            # answering `defs`/`callers` from it duplicates every symbol it holds.
            prune_source_dirs(dirpath, dirnames)
            checkpoint()                # P32: per directory of the walk
            for fn in filenames:
                if fn.endswith(self.extensions):
                    yield os.path.join(dirpath, fn)

    @staticmethod
    def _read_file(path: str) -> List[str]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def _rel(self, path: str) -> RepoName:
        """The NAME every `Reference` this backend emits carries. Not a path — nothing opens
        it; it is rendered into `crg nav` output and compared against `svc/core.py`. Four of
        the eleven Windows failures on run 32144708930 came through this one method."""
        return name_of(path, self.root)

    @staticmethod
    def _lang(path: str) -> languages.Language:
        return languages.language_for(path)

    @staticmethod
    def _enclosing(lang: languages.Language, lines: List[str], idx: int) -> Optional[str]:
        """Nearest preceding scope opener (def/func/fn/class/type) with smaller indentation
        than line `idx`. Indentation is a lexical proxy for nesting — approximate for
        brace languages, which is exactly the floor's contract."""
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        for j in range(idx - 1, -1, -1):
            line = lines[j]
            this_indent = len(line) - len(line.lstrip())
            if this_indent < indent:
                name = lang.scope_name(line)
                if name:
                    return name
        return None

    # --- query implementations ----------------------------------------------
    def _callers(self, target: str) -> List[Reference]:
        if not target or any(c.isspace() for c in target):
            return []                    # not a symbol: no call site can spell it (see above)
        calls = {b: re.compile(b + re.escape(target) + r"\s*\(") for b in (_BARE, _QUALIFIED)}
        out: List[Reference] = []
        files = self._files()
        tick = _Counts(f"callers of {target}", files)
        for n, path in enumerate(files):
            tick(n, len(out))
            lang = self._lang(path)
            call = calls[_before(lang)]
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.defines(line, target):
                    continue
                if call.search(line):
                    out.append(Reference(self._rel(path), i + 1, line.strip(),
                                         self._enclosing(lang, lines, i)))
        return out

    def _callers_of_many(self, targets: List[str]) -> Dict[str, List[Reference]]:
        """`_callers` for every target in ONE pass over the corpus — the same references, in the
        same per-target (file, line) order, as calling `_callers` once per target."""
        out: Dict[str, List[Reference]] = {t: [] for t in targets}
        # A target with whitespace in it is not a symbol (an acceptance-criterion sentence handed
        # to the impact lens by `spec amend`), and no call site spells one — skip the walk for it.
        targets = [t for t in targets if t and not any(c.isspace() for c in t)]
        if not targets:
            return out
        alts = "|".join(re.escape(t) for t in sorted(set(targets), key=len, reverse=True))
        call = re.compile(_QUALIFIED + r"(" + alts + r")\s*\(")   # a prefilter; judged below
        for path in self._files():
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                hit = {m.group(1) for m in call.finditer(line)}
                if not hit:
                    continue
                # `hit` only PREFILTERS lines: each target is then judged by its own pattern, so
                # overlapping names (`foo` / `foobar`) answer exactly as `_callers` would
                for sym in targets:
                    if _calls(line, sym, lang):
                        if lang.defines(line, sym):
                            continue
                        out[sym].append(Reference(self._rel(path), i + 1, line.strip(),
                                                  self._enclosing(lang, lines, i)))
        return out

    def _callees(self, target: str) -> List[Reference]:
        calls = {b: re.compile(b + r"(\w+)\s*\(") for b in (_BARE, _QUALIFIED)}
        out: List[Reference] = []
        for path in self._files():
            lang = self._lang(path)
            call = calls[_before(lang)]
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.func_name(line) != target:
                    continue
                for j, body_line in languages.iter_block_lines(lines, i, lang.block_style):
                    for cm in call.finditer(body_line):
                        name = cm.group(1)
                        if name == target or lang.is_call_keyword(name):
                            continue
                        out.append(Reference(self._rel(path), j + 1,
                                             body_line.strip(), name))
        return out

    def _implementers(self, target: str) -> List[Reference]:
        word = re.compile(r"(?<![\w.])" + re.escape(target) + r"(?![\w])")
        out: List[Reference] = []
        for path in self._files():
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                rel = lang.inheritance(line)
                if rel and word.search(rel[1]):
                    out.append(Reference(self._rel(path), i + 1, line.strip(), rel[0]))
        return out

    def _imports(self, target: str) -> List[Reference]:
        # Unlike callers/implementers, imports are DOTTED/qualified (`com.example.mod_a`,
        # `crate::mod_a`) so a leading `.`/`:` must NOT exclude the match — only word chars do.
        word = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(target) + r"(?![A-Za-z0-9_])")
        out: List[Reference] = []
        files = self._files()
        tick = _Counts(f"references to {target}", files)
        for n, path in enumerate(files):
            tick(n, len(out))
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.is_import_line(line) and word.search(line):
                    out.append(Reference(self._rel(path), i + 1, line.strip(), None))
        return out

    def _defs(self, target: str) -> List[Reference]:
        """CRG-NAV (b) — WHERE the symbol is defined. The per-language `defines` heuristic is
        already the predicate `_callers` uses to EXCLUDE a definition line; here it is the
        predicate we select on, so the floor answers "find this symbol" with the same lexical
        table rather than a second set of patterns."""
        out: List[Reference] = []
        for path in self._files():
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.defines(line, target):
                    out.append(Reference(self._rel(path), i + 1, line.strip(), target))
        return out

    def iter_refs(self, target: str, scope: Optional["RefsScope"] = None) -> Iterator[Reference]:
        """0.0.27 P4 (R2-2) — the lexical `refs` scan, STREAMED: one file at a time, in repo
        path order, read in blocks of at most `REFS_BLOCK` bytes, with a cancel checkpoint per
        block. Nothing is cached across files and no line is ever held whole: a 200 MB one-line
        file costs one block, and each reference's snippet is clipped AT MATCH TIME to
        `REFS_SNIPPET_MAX` characters around the match (`…` marks a clip).

        Word boundary: a match may not touch a Unicode word character or `$` on either side.
        Lines are the viewer's (`textlines`): split on `\n` only. One Reference per matching
        line (the first match's 1-based column in `metadata["col"]`), the symbol it sits in by
        the same indentation rule `_enclosing` uses. `scope` (optional) is filled with what the
        scan covered, so a caller can say it — the floor's label is never only its false
        positives."""
        scope = scope if scope is not None else RefsScope()
        if not target:
            return
        word = re.compile(r"(?<![\w$])" + re.escape(target) + r"(?![\w$])")
        files = self._refs_files(scope)
        tick = _Counts(f"references to {target}", files)
        found = 0
        for n, path in enumerate(files):
            tick(n, found)
            checkpoint()
            try:
                fh = open(path, "rb")
            except OSError:
                scope.unreadable.append(str(self._rel(path)))
                continue
            try:
                with fh:
                    for ref in self._scan_refs(fh, path, word, target):
                        found += 1
                        yield ref
            except OSError:
                scope.unreadable.append(str(self._rel(path)))
                continue
            scope.scanned += 1
        report(done=len(files), total=len(files), unit="files",
               note=f"references to {target}: {found}")

    def _refs_files(self, scope: "RefsScope") -> List[str]:
        """The files a `refs` scan reads, sorted by their repo name (so rows come out in
        (path, line) order): the scoped list the AST floor handed over, else the repo walk —
        whose listing phase reports a running count (it has no total until it ends)."""
        cache = getattr(self, "_corpus_cache", None)
        listed = cache.get(None) if cache else None
        if listed is None:
            listed = []
            t0 = time.monotonic()
            for dirpath, dirnames, filenames in os.walk(self.root):
                before = len(dirnames)
                prune_source_dirs(dirpath, dirnames)
                scope.pruned_dirs += before - len(dirnames)
                dirnames.sort()
                checkpoint()
                for fn in filenames:
                    if fn.endswith(self.extensions):
                        listed.append(os.path.join(dirpath, fn))
                    else:
                        scope.other_files += 1
                if time.monotonic() - t0 >= 0.3:
                    t0 = time.monotonic()
                    report(done=len(listed), unit="files listed", note="listing source files")
        files = sorted(listed, key=lambda p: str(self._rel(p)))
        scope.listed = len(files)
        scope.extensions = tuple(self.extensions)
        return files

    def _scan_refs(self, fh: Any, path: str, word: "re.Pattern[str]",
                   target: str) -> Iterator[Reference]:
        rel = self._rel(path)
        lang = self._lang(path)
        openers: List[Tuple[int, str]] = []      # (indent, name), indents strictly increasing
        carry_n = len(target) + 1
        lineno = 0
        while True:
            chunk = fh.readline(REFS_BLOCK)
            if not chunk:
                return
            lineno += 1
            if lineno % 1024 == 0:
                checkpoint()
            dec = codecs.getincrementaldecoder("utf-8")("replace")
            final = chunk.endswith(b"\n") or len(chunk) < REFS_BLOCK
            head = dec.decode(chunk, final=final)
            first = head[:-1] if head.endswith("\n") else head
            first = first[:-1] if final and first.endswith("\r") else first
            indent = len(first) - len(first.lstrip())
            hit: Optional[Tuple[int, str]] = None     # (col, snippet)
            if final:
                m = word.search(first)
                if m:
                    hit = (m.start() + 1, _clip(first, m.start(), m.end()))
            else:
                # a line longer than one block: scan it in pieces, never whole
                base, buf = 0, first
                while True:
                    checkpoint()
                    for m in word.finditer(buf):
                        if base and m.start() == 0:
                            continue           # its left neighbour is in the previous piece
                        if not final and m.end() >= len(buf):
                            break              # its right neighbour is in the next piece
                        hit = (base + m.start() + 1, _clip(buf, m.start(), m.end(), True))
                        break
                    if hit is not None or final:
                        break
                    more = fh.readline(REFS_BLOCK)
                    final = not more or more.endswith(b"\n") or len(more) < REFS_BLOCK
                    piece = dec.decode(more, final=final)
                    if final:
                        piece = piece[:-1] if piece.endswith("\n") else piece
                        piece = piece[:-1] if piece.endswith("\r") else piece
                    keep = buf[-carry_n:]
                    base += len(buf) - len(keep)
                    buf = keep + piece
                while not final:                 # drain the rest of the line, unread
                    checkpoint()
                    more = fh.readline(REFS_BLOCK)
                    final = not more or more.endswith(b"\n") or len(more) < REFS_BLOCK
            if hit is not None:
                enc = None
                for oi, oname in reversed(openers):
                    if oi < indent:
                        enc = oname
                        break
                meta: Dict[str, Any] = {"col": hit[0]}
                if lang.defines(first, target):
                    meta["defines"] = True         # this line DEFINES the name (P4 ambiguity)
                yield Reference(rel, lineno, hit[1], enc, metadata=meta)
            name = lang.scope_name(first) if first.strip() else None
            if name:
                while openers and openers[-1][0] >= indent:
                    openers.pop()
                openers.append((indent, name))

    def _blast_radius(self, target: str, depth: int) -> List[Reference]:
        """Transitive callers up to `depth` hops — the impact surface of `target`."""
        seen_syms = {target}
        seen_refs = set()
        out: List[Reference] = []
        frontier = [target]
        for _ in range(max(1, depth)):
            nxt: List[str] = []
            by_sym = self._callers_of_many(frontier)
            for sym in frontier:
                for r in by_sym.get(sym, ()):
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
