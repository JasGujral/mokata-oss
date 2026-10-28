"""Server-side syntax highlighting for the IDE — the file viewer, every diff, and spec/AC snippets
(0.0.26 P1, ID-SYNTAX-HIGHLIGHT; doc 109 §13.3).

WHAT. Pygments (pure Python, BSD; ~600 lexers in 2.19) turns text into TOKEN SPANS — never HTML.
Each line is a list of `[cls, text]` segments where `cls` is one of a small, fixed set of theme
token names (`TOKEN_CLASSES`: kw, str, com, num, fn, cls, ...; "" = plain). The page renders the
spans with `textContent` and maps each class to a CSS variable, so light / dark / high-contrast
are the page's choice and no Pygments style or markup ever reaches the DOM.

WINDOWED. A file is cut into `CHUNK`-line chunks; a request names a line window and only the
chunks it touches are read and lexed. Each chunk is lexed with up to `LEAD` lines of lead-in before
it (tokens discarded) so a string/comment that opened just above the chunk still colours right —
the answer says so (`basis`). Chunks are cached in a bounded LRU keyed by (path, mtime, size,
chunk); a change to the file changes the key, so a stale chunk is never served. A large file is
never read whole: a sparse index of chunk byte offsets (one int per 1,000 lines) is built once per
(path, mtime, size) by one streaming pass, and a chunk is read by seek.

LIMITS, each disclosed in the answer's `plain_reason` (never silently plain):
  * a file over `HL_MAX_BYTES` is served as plain text, windowed the same way;
  * a chunk holding a line over `LONG_LINE` chars (minified code) is served plain;
  * a file over `READ_MAX_BYTES`, binary, or not a regular file inside the repo is not served;
  * Pygments missing (the `ide` extra not installed) → plain, with the install line.
Language: by file name first (Pygments' filename patterns, disambiguated by content), then by
content (shebang / modeline / analysers) when the name says nothing; `detected_by` says which.

Nothing here runs at idle: every call is a request's. The IDE never installs Pygments.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

CHUNK = 1000                     # lines per cached chunk
LEAD = 200                       # lines of lead-in lexed before a chunk (state carry)
MAX_WINDOW = 5000                # lines one request may ask for
HL_MAX_BYTES = 16 * 1024 * 1024  # above: plain text (still windowed)
READ_MAX_BYTES = 256 * 1024 * 1024   # above: not served at all
LONG_LINE = 4000                 # a chunk with a longer line is plain (minified)
CACHE_CHUNKS = 64                # bounded LRU: chunks
CACHE_BYTES = 48 * 1024 * 1024   # ... and an approximate byte budget across them
INDEX_FILES = 16                 # bounded LRU: per-file chunk-offset indexes
SNIPPET_MAX_LINES = 20000        # a diff / snippet body larger than this is plain
SNIPPET_MAX_BYTES = 4 * 1024 * 1024
INSTALL = "pip install 'mokata[ide]'"

# The theme tokens. The page has a CSS variable per class (static/features/highlight.css).
TOKEN_CLASSES = ("kw", "con", "ty", "str", "doc", "esc", "num", "com", "pre", "fn", "cls", "dec",
                 "bi", "tag", "attr", "var", "op", "pun", "hd", "ins", "del", "err", "em", "strong")


def available() -> bool:
    try:
        import pygments  # noqa: F401
    except ImportError:
        return False
    return True


def lexer_count() -> int:
    if not available():
        return 0
    from pygments.lexers import get_all_lexers
    return sum(1 for _ in get_all_lexers())


# ------------------------------------------------------------------------------- token classes

_CLS_CACHE: Dict[Any, str] = {}


def _class_map() -> Dict[Any, str]:
    from pygments import token as T
    return {
        T.Keyword: "kw", T.Keyword.Constant: "con", T.Keyword.Type: "ty",
        T.Operator.Word: "kw",
        T.Name.Builtin: "bi", T.Name.Builtin.Pseudo: "bi", T.Name.Function: "fn",
        T.Name.Function.Magic: "fn", T.Name.Class: "cls", T.Name.Exception: "cls",
        T.Name.Namespace: "cls", T.Name.Decorator: "dec", T.Name.Tag: "tag",
        T.Name.Attribute: "attr", T.Name.Constant: "con", T.Name.Variable: "var",
        T.Name.Label: "var", T.Name.Entity: "con",
        T.String: "str", T.String.Doc: "doc", T.String.Escape: "esc", T.String.Interpol: "esc",
        T.Number: "num", T.Literal: "num",
        T.Comment: "com", T.Comment.Preproc: "pre", T.Comment.PreprocFile: "str",
        T.Operator: "op", T.Punctuation: "pun",
        T.Generic.Heading: "hd", T.Generic.Subheading: "hd", T.Generic.Inserted: "ins",
        T.Generic.Deleted: "del", T.Generic.Emph: "em", T.Generic.Strong: "strong",
        T.Generic.Error: "err", T.Error: "err",
        T.Token: "", T.Text: "", T.Name: "", T.Generic: "",
    }


def token_class(ttype: Any) -> str:
    """The theme class of a Pygments token type: the nearest ancestor in the map ("" = plain)."""
    got = _CLS_CACHE.get(ttype)
    if got is not None:
        return got
    m = _class_map()
    t = ttype
    while t not in m and t.parent is not None:
        t = t.parent
    cls = m.get(t, "")
    _CLS_CACHE[ttype] = cls
    return cls


# ------------------------------------------------------------------------------------- lexers

def _new(cls: Any) -> Any:
    # stripnl/ensurenl/tabsize: Pygments must not move a single line — the row map depends on it.
    return cls(stripnl=False, stripall=False, ensurenl=True, tabsize=0)


def detect(path: str, head: str) -> Tuple[Any, str, str]:
    """(lexer, language name, detected_by) for a file name + its first few KB.
    detected_by: "filename" | "content" | "none"."""
    from pygments.util import ClassNotFound
    from pygments.lexers import find_lexer_class_for_filename
    from pygments.lexers.special import TextLexer
    from ..repo_paths import split_path
    parts = split_path(path)                      # either separator: the file NAME is the last part
    name = parts[-1] if parts else ""
    try:
        cls = find_lexer_class_for_filename(name, head)
    except ClassNotFound:
        cls = None
    if cls is not None and cls is not TextLexer:
        return _new(cls), cls.name, "filename"
    if head.strip():
        cls = _by_content(head)
        if cls is not None and cls is not TextLexer:
            return _new(cls), cls.name, "content"
    return _new(TextLexer), "Text only", "none"


CONTENT_MIN_SCORE = 0.3     # an analyser must be this sure (a shebang scores 1.0; prose ~0)


def _by_content(head: str) -> Any:
    """A modeline (`vim: ft=…`) first, else the most confident content analyser (shebangs,
    `<?php`, `<!DOCTYPE`…) — but only above CONTENT_MIN_SCORE, so a README never turns into
    some language by a coin-flip."""
    from pygments.lexers import _iter_lexerclasses, get_lexer_by_name
    from pygments.modeline import get_filetype_from_buffer
    from pygments.util import ClassNotFound
    ft = get_filetype_from_buffer(head)
    if ft:
        try:
            return type(get_lexer_by_name(ft))
        except ClassNotFound:
            pass
    best, score = None, 0.0
    for cls in _iter_lexerclasses():
        rv = cls.analyse_text(head)           # Pygments' make_analysator already traps errors
        if rv > score:
            best, score = cls, rv
            if rv >= 1.0:
                break
    return best if score >= CONTENT_MIN_SCORE else None


def by_name(lang: str) -> Optional[Any]:
    from pygments.util import ClassNotFound
    from pygments.lexers import get_lexer_by_name
    try:
        return _new(type(get_lexer_by_name(lang.strip().lower())))
    except (ClassNotFound, ValueError):
        return None


def lex_lines(lexer: Any, lines: Sequence[str]) -> List[List[List[str]]]:
    """Lex `lines` as ONE text and give back one row of `[cls, text]` segments per input line —
    exactly len(lines) rows, whatever the lexer does (adjacent same-class segments merged)."""
    # Pygments turns a lone "\r" into a line break; it is invisible in a viewer, so it goes.
    lines = [ln.replace("\r", "") if "\r" in ln else ln for ln in lines]
    rows: List[List[List[str]]] = [[] for _ in lines]
    if not lines:
        return rows
    text = "\n".join(lines) + "\n"
    i = 0
    for ttype, value in lexer.get_tokens(text):
        if not value:
            continue
        cls = token_class(ttype)
        parts = value.split("\n")
        for k, part in enumerate(parts):
            if k:
                i += 1
            if part and i < len(rows):
                row = rows[i]
                if row and row[-1][0] == cls:
                    row[-1][1] += part
                else:
                    row.append([cls, part])
    for k, (row, ln) in enumerate(zip(rows, lines)):
        if "".join(t for _c, t in row) != ln:      # a lexer that moved text: never show it wrong
            rows[k] = [["", ln]] if ln else []
    return rows


def plain_rows(lines: Sequence[str]) -> List[List[List[str]]]:
    return [[["", ln]] if ln else [] for ln in lines]


# --------------------------------------------------------------------------------- the caches

class _LRU:
    def __init__(self, max_items: int, max_bytes: int) -> None:
        self.max_items, self.max_bytes = max_items, max_bytes
        self._d: "OrderedDict[Any, Tuple[Any, int]]" = OrderedDict()
        self.bytes = 0
        self.hits = self.misses = self.evictions = 0
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            got = self._d.get(key)
            if got is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return got[0]

    def put(self, key: Any, value: Any, size: int) -> None:
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self.bytes -= old[1]
            self._d[key] = (value, size)
            self.bytes += size
            while self._d and (len(self._d) > self.max_items or self.bytes > self.max_bytes):
                _k, (_v, sz) = self._d.popitem(last=False)
                self.bytes -= sz
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self.bytes = 0
            self.hits = self.misses = self.evictions = 0

    def __len__(self) -> int:
        return len(self._d)

    def info(self) -> Dict[str, int]:
        return {"items": len(self._d), "bytes": self.bytes, "max_items": self.max_items,
                "max_bytes": self.max_bytes, "hits": self.hits, "misses": self.misses,
                "evictions": self.evictions}


CHUNKS = _LRU(CACHE_CHUNKS, CACHE_BYTES)
INDEXES = _LRU(INDEX_FILES, 1 << 62)
SNIPPETS = _LRU(32, 8 * 1024 * 1024)


def cache_info() -> Dict[str, Any]:
    return {"chunks": CHUNKS.info(), "indexes": INDEXES.info(), "snippets": SNIPPETS.info()}


def _rows_size(rows: List[List[List[str]]]) -> int:
    # An honest-enough estimate of what a chunk's rows hold in memory (strings + list overhead).
    return sum(64 + sum(80 + len(t) for _c, t in r) for r in rows)


# ------------------------------------------------------------------------------ file windows

class _Index:
    """One streaming pass over a file: the byte offset of every CHUNK-th line, the line count,
    whether it is binary, the longest line seen per chunk, and its head (for detection)."""
    __slots__ = ("offsets", "lines", "binary", "longest", "head", "size")

    def __init__(self) -> None:
        self.offsets: List[int] = [0]
        self.lines = 0
        self.binary = False
        self.longest: List[int] = []
        self.head = ""
        self.size = 0


def _build_index(fh: Any, size: int) -> _Index:
    ix = _Index()
    ix.size = size
    first = fh.read(65536)
    if b"\0" in first[:8192]:
        ix.binary = True
        return ix
    ix.head = first[:4096].decode("utf-8", errors="replace")
    pos, line, line_start, longest = 0, 0, 0, 0
    buf = first
    while buf:
        start = 0
        while True:
            nl = buf.find(b"\n", start)
            if nl < 0:
                break
            ln_len = pos + nl - line_start
            if ln_len > longest:
                longest = ln_len
            line += 1
            line_start = pos + nl + 1
            if line % CHUNK == 0:
                ix.offsets.append(line_start)
                ix.longest.append(longest)
                longest = 0
            start = nl + 1
        pos += len(buf)
        buf = fh.read(1 << 20)
    if pos > line_start:                      # a last line with no newline
        longest = max(longest, pos - line_start)
        line += 1
    if line > CHUNK * len(ix.longest):        # the last, partial chunk
        ix.longest.append(longest)
    ix.lines = line
    return ix


def _read_chunk(fh: Any, ix: _Index, k: int) -> List[str]:
    start = ix.offsets[k]
    end = ix.offsets[k + 1] if k + 1 < len(ix.offsets) else ix.size
    fh.seek(start)
    data = fh.read(max(0, end - start))
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [ln[:-1] if ln.endswith("\r") else ln for ln in lines]


def window(root: str, path: str, start: int, end: int) -> Dict[str, Any]:
    """The token rows of lines start..end (1-based, inclusive) of a repo file. The answer:
    path, lang, detected_by, lines (total), start, end, rows, plain (bool), plain_reason, basis,
    truncated (the window was clamped to MAX_WINDOW), cached (every chunk came from the cache)."""
    from . import diffs as D
    from . import nav
    rel = nav.contained_rel(root, path)
    got = D.open_contained(root, rel)
    if not got:
        return not_served(rel, "gone" if got == "" else "not a regular, readable file inside "
                                                        "the repository")
    fh, st = got
    with fh:
        if st.st_size > READ_MAX_BYTES:
            return not_served(rel, f"the file is {_mb(st.st_size)}; the viewer stops at "
                                    f"{_mb(READ_MAX_BYTES)}")
        ikey = (root, rel, st.st_mtime_ns, st.st_size)
        ix = INDEXES.get(ikey)
        if ix is None:
            ix = _build_index(fh, st.st_size)
            INDEXES.put(ikey, ix, 64 + 8 * len(ix.offsets))
        if ix.binary:
            return not_served(rel, "binary file")
        return _answer(rel, ix, start, end, lambda k: _read_chunk(fh, ix, k), ikey)


def window_text(path: str, text: str, start: int, end: int, key: Any) -> Dict[str, Any]:
    """`window` over text already in memory (the SAMPLE source) — same answer shape."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    ix = _Index()
    ix.lines, ix.size, ix.head = len(lines), len(text.encode()), text[:4096]
    ix.offsets = list(range(0, len(lines), CHUNK)) or [0]
    ix.longest = [max((len(x) for x in lines[i:i + CHUNK]), default=0)
                  for i in range(0, max(len(lines), 1), CHUNK)]
    return _answer(path, ix, start, end, lambda k: lines[k * CHUNK:(k + 1) * CHUNK], key)


def _answer(rel: str, ix: _Index, start: int, end: int, chunk_lines, key: Any) -> Dict[str, Any]:
    total = ix.lines
    start = max(1, int(start or 1))
    end = int(end or start + CHUNK - 1)
    truncated = False
    if end - start + 1 > MAX_WINDOW:
        end, truncated = start + MAX_WINDOW - 1, True
    end = min(end, total)
    base = {"path": rel, "lines": total, "start": start, "end": end, "truncated": truncated,
            "chunk": CHUNK, "plain": False, "plain_reason": "", "rows": [], "lang": "",
            "detected_by": "none",
            "basis": f"windowed — each {CHUNK:,}-line chunk is lexed with up to {LEAD} lines "
                     f"of lead-in before it", "cached": True}
    if total == 0 or start > total:
        return base
    whole_plain = ""
    if not available():
        whole_plain = f"syntax highlighting needs Pygments: {INSTALL}"
    elif ix.size > HL_MAX_BYTES:
        whole_plain = (f"the file is {_mb(ix.size)}; highlighting stops at {_mb(HL_MAX_BYTES)} "
                       f"— shown as plain text")
    lexer, lang, by = (None, "Text only", "none")
    if not whole_plain:
        lexer, lang, by = _detect_cached(rel, ix.head, key)
    base.update(lang=lang, detected_by=by)
    rows: List[List[List[str]]] = []
    reasons: List[str] = []
    all_cached = True
    for k in range((start - 1) // CHUNK, (end - 1) // CHUNK + 1):
        ck = (key, k, bool(whole_plain))
        got = CHUNKS.get(ck)
        if got is None:
            all_cached = False
            lines = chunk_lines(k)
            if whole_plain or lexer is None:
                got = (plain_rows(lines), whole_plain)
            elif k < len(ix.longest) and ix.longest[k] > LONG_LINE:
                got = (plain_rows(lines), f"lines {k * CHUNK + 1:,}–{k * CHUNK + len(lines):,} "
                                          f"hold a line over {LONG_LINE:,} characters (minified?) "
                                          f"— shown as plain text")
            else:
                lead: List[str] = []
                if k > 0:
                    prev = chunk_lines(k - 1)
                    lead = prev[-LEAD:] if LEAD > 0 else []
                    if any(len(x) > LONG_LINE for x in lead):
                        lead = []
                lexed = lex_lines(lexer, lead + lines)
                got = (lexed[len(lead):], "")
            CHUNKS.put(ck, got, _rows_size(got[0]))
        chunk_rows, reason = got
        lo = max(start, k * CHUNK + 1) - (k * CHUNK + 1)
        hi = min(end, k * CHUNK + len(chunk_rows)) - (k * CHUNK + 1)
        rows.extend(chunk_rows[lo:hi + 1])
        if reason and reason not in reasons:
            reasons.append(reason)
    base.update(rows=rows, cached=all_cached, plain=bool(reasons),
                plain_reason="; ".join(reasons))
    return base


_LEXERS = _LRU(64, 1 << 62)


def _detect_cached(rel: str, head: str, key: Any) -> Tuple[Any, str, str]:
    got = _LEXERS.get(key)
    if got is None:
        got = detect(rel, head)
        _LEXERS.put(key, got, 1)
    return got


def not_served(rel: str, why: str) -> Dict[str, Any]:
    return {"path": rel, "lines": 0, "start": 0, "end": 0, "truncated": False, "chunk": CHUNK,
            "plain": True, "plain_reason": why, "rows": [], "lang": "", "detected_by": "none",
            "basis": "", "cached": False, "not_served": True}


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def probe(root: str, rel: str) -> Optional[Dict[str, Any]]:
    """Is `rel` a text file the viewer can show in windows? {lines, size} or None."""
    from . import diffs as D
    got = D.open_contained(root, rel)
    if not got:
        return None
    fh, st = got
    with fh:
        if st.st_size > READ_MAX_BYTES:
            return None
        ikey = (root, rel, st.st_mtime_ns, st.st_size)
        ix = INDEXES.get(ikey)
        if ix is None:
            ix = _build_index(fh, st.st_size)
            INDEXES.put(ikey, ix, 64 + 8 * len(ix.offsets))
    if ix.binary:
        return None
    return {"lines": ix.lines, "size": st.st_size}


# --------------------------------------------------------------------------------- diffs

def diff(path: str, hunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Token rows for every line of a file's diff hunks, each line coloured IN ITS OWN SIDE: a
    hunk's old side (context + removed) and new side (context + added) are each lexed as one
    contiguous text, so a line's tokens are the ones it has in that version of the file; the rows
    come back in the hunk's own line order. `hunks`: [{lines: [[op, old, new, text], ...]}]."""
    total = sum(len(h.get("lines") or []) for h in hunks)
    size = sum(len(str(ln[3])) for h in hunks for ln in (h.get("lines") or []) if len(ln) > 3)
    out = {"path": path, "lang": "", "detected_by": "none", "plain": False, "plain_reason": "",
           "hunks": []}
    reason = ""
    if not available():
        reason = f"syntax highlighting needs Pygments: {INSTALL}"
    elif total > SNIPPET_MAX_LINES or size > SNIPPET_MAX_BYTES:
        reason = f"the diff is {total:,} lines; highlighting stops at {SNIPPET_MAX_LINES:,}"
    if reason:
        out.update(plain=True, plain_reason=reason,
                   hunks=[plain_rows([str(ln[3]) for ln in (h.get("lines") or [])])
                          for h in hunks])
        return out
    key = ("diff", path, hashlib.sha1(repr([h.get("lines") for h in hunks]).encode(
        "utf-8", "replace")).hexdigest())
    got = SNIPPETS.get(key)
    if got is not None:
        return got
    head = "\n".join(str(ln[3]) for h in hunks for ln in (h.get("lines") or [])
                     if ln and ln[0] != "-")[:4096]
    lexer, lang, by = detect(path, head)
    out.update(lang=lang, detected_by=by)
    for h in hunks:
        lines = [ln for ln in (h.get("lines") or []) if len(ln) >= 4]
        old_ix = [i for i, ln in enumerate(lines) if ln[0] in (" ", "-")]
        new_ix = [i for i, ln in enumerate(lines) if ln[0] in (" ", "+")]
        rows: List[Any] = [None] * len(lines)
        long_ = any(len(str(ln[3])) > LONG_LINE for ln in lines)
        lex = (lambda xs: plain_rows(xs)) if long_ else (lambda xs: lex_lines(lexer, xs))
        new_rows = lex([str(lines[i][3]) for i in new_ix])
        for i, r in zip(new_ix, new_rows):
            rows[i] = r
        old_rows = lex([str(lines[i][3]) for i in old_ix])
        for i, r in zip(old_ix, old_rows):
            if lines[i][0] == "-":
                rows[i] = r
        if long_:
            out.update(plain=True, plain_reason=f"a hunk holds a line over {LONG_LINE:,} "
                                                f"characters — that hunk is plain")
        out["hunks"].append(rows)
    SNIPPETS.put(key, out, size * 3 + 256)
    return out


def snippet(text: str, lang: str = "", path: str = "") -> Dict[str, Any]:
    """Token rows for a code snippet (a spec / AC code block): by its fence language, else by a
    file name, else by content."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out = {"lang": "", "detected_by": "none", "plain": False, "plain_reason": "", "rows": []}
    if not available():
        out.update(plain=True, plain_reason=f"syntax highlighting needs Pygments: {INSTALL}",
                   rows=plain_rows(lines))
        return out
    if len(lines) > SNIPPET_MAX_LINES or len(text) > SNIPPET_MAX_BYTES or \
            any(len(x) > LONG_LINE for x in lines):
        out.update(plain=True, plain_reason="the snippet is too large to highlight",
                   rows=plain_rows(lines))
        return out
    key = ("snip", lang, path, hashlib.sha1(text.encode("utf-8", "replace")).hexdigest())
    got = SNIPPETS.get(key)
    if got is not None:
        return got
    lexer = by_name(lang) if lang else None
    if lexer is not None:
        name, by = lexer.name, "fence"
    else:
        lexer, name, by = detect(path or "snippet", text[:4096])
    out.update(lang=name, detected_by=by, rows=lex_lines(lexer, lines))
    SNIPPETS.put(key, out, len(text) * 3 + 256)
    return out


__all__ = ["window", "window_text", "diff", "snippet", "probe", "detect", "lex_lines",
           "token_class", "cache_info", "available", "lexer_count", "TOKEN_CLASSES", "CHUNK",
           "LEAD", "MAX_WINDOW", "HL_MAX_BYTES", "READ_MAX_BYTES", "LONG_LINE", "CHUNKS",
           "INDEXES", "SNIPPETS"]
