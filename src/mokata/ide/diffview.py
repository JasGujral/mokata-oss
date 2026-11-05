"""How a diff is SHOWN (0.0.27 P5 — `ID-DIFF-SPLIT`, `ID-DIFF-INTRALINE`). Read-only, no I/O except
`context_page`, which reads one page of one side of a file.

    intraline(old, new)       which characters of a changed line pair changed — token level
    batch(pairs)              many pairs (the rows in view), with job progress + cancel
    cost(pairs)               what `batch` will touch, in tokens — decided BEFORE the work
    ignore_whitespace(facts)  a DiffSet's facts with whitespace-only line changes shown as
                              context, and HOW MANY were hidden — never a silent filter
    context_page(...)         one page of the unchanged lines between two hunks (expand context)

THE RULES (each one a test in tests/test_p5_ide_diff_split_intraline.py):
  * intra-line work is bounded by what the page asks for (the rows in view), never by the file;
    a huge or minified line is diffed in time ~ its tokens (common head/tail trimmed first, then
    unique-token anchors, an exact LCS only on small gaps) — a gap with no anchor is marked as
    ONE changed span and the answer says so (`basis: "span"`), never as if it were exact;
  * the cost is stated before the work: a batch over `SYNC_TOKENS` runs as a job (progress in
    tokens, Cancel stops it at the next gap);
  * "ignore whitespace" hides a line pair only when the two lines are equal once whitespace is
    removed (CR included, so a CRLF<->LF change is whitespace), never a pair whose end-of-file
    newline differs, and NEVER a leading-whitespace (indentation) change unless the file is KNOWN
    to be a language where indentation is not code (`indent_significant`: a shebang decides first,
    then the extension; anything unknown — no extension, Snakefile, Makefile.am, .pyx, .md — keeps
    its indentation changes). Kept ones are counted (`ws_kept`). Every file and the whole set say
    how many line pairs were hidden;
  * an expanded context page is the NEW side's own lines (they are equal on both sides), read in
    bounded chunks — a line is never read whole: at most `LINE_BYTES` of it is kept and a longer
    one is CUT and said (`cut`), a page stops at `PAGE_BYTES`, and lines before the page are
    skipped chunk by chunk — with "\\r" kept so a CRLF file is shown as it is.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import os
import re
import subprocess
from array import array
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import MokataError
from ..knowledge.cancel import checkpoint, kill_on_cancel, report
from ..quiet_spawn import hidden

#: A token: a word, a run of whitespace, or one other character. A changed `foo` -> `bar` marks the
#: word, a changed indent marks the whitespace run, a changed `,` marks the comma.
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]", re.UNICODE)

#: An exact LCS (difflib, no autojunk) is run only on a gap of at most this many token-pair cells
#: (|a| * |b|): difflib's worst case is ~|a|*|b| steps, so one leaf stays ~tens of ms. Larger gaps
#: are split at unique-token anchors first (patience diff); a gap with no anchor is one span.
LEAF_CELLS = 40_000
#: A batch whose trimmed middles hold more tokens than this is a JOB (measured: ~0.1-0.2 s of
#: work on the dev box at this size — anything longer is visible work with progress and Cancel).
SYNC_TOKENS = 40_000
#: One context page: at most this many lines per request (the page asks again for more).
PAGE_MAX = 500
#: One context line keeps at most this many bytes; a longer line is cut and the page says so.
LINE_BYTES = 64 * 1024
#: One context page stops once it holds this many bytes (it always holds at least one line).
PAGE_BYTES = 1024 * 1024
#: The ONLY extensions whose indentation "Ignore whitespace" may hide: languages and formats where
#: leading whitespace is not syntax. Fail safe — anything not here (no extension, .py, .yml,
#: Makefile*, Snakefile, .pyx, .gd, .md, .txt, ...) keeps its indentation changes and counts them.
INDENT_FREE = frozenset((
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".c", ".h", ".cc", ".cpp",
    ".cxx", ".hpp", ".hh", ".hxx", ".cs", ".java", ".kt", ".kts", ".scala", ".go", ".rs",
    ".swift", ".m", ".mm", ".php", ".rb", ".pl", ".pm", ".lua", ".sh", ".bash", ".zsh", ".ksh",
    ".ps1", ".psm1", ".bat", ".cmd", ".sql", ".css", ".scss", ".less", ".html", ".htm", ".xhtml",
    ".xml", ".svg", ".json", ".jsonc", ".json5", ".toml", ".r", ".dart", ".sol", ".proto",
    ".gradle", ".groovy", ".tf", ".hcl", ".zig", ".jl", ".ex", ".exs", ".erl", ".clj", ".cljs",
    ".el", ".lisp", ".scm", ".vb", ".cmake"))
#: Interpreters (from a `#!` line) whose scripts may have their indentation hidden; any other
#: interpreter — python, make, an unknown one — keeps it.
INDENT_FREE_INTERPRETERS = frozenset((
    "sh", "bash", "zsh", "dash", "ksh", "fish", "node", "nodejs", "deno", "bun", "perl", "ruby",
    "php", "lua"))


class ContextUnavailable(MokataError):
    """The side a context page would be read from cannot be read (binary, gone, refused)."""


# =============================================================================== intra-line
def _trim(a: str, b: str) -> Tuple[int, int]:
    """Common head and tail lengths (characters, non-overlapping), pulled back to a token
    boundary so a changed word is marked whole (`count` -> `counter` marks the word, not `er`)."""
    n = min(len(a), len(b))
    p = 0
    # compare in blocks first: a 5 MB line with one change near the end is ~one pass
    step = 4096
    while p + step <= n and a[p:p + step] == b[p:p + step]:
        p += step
    while p < n and a[p] == b[p]:
        p += 1
    while p > 0 and _same_class(a, b, p):
        p -= 1
    s = 0
    m = n - p
    while s + step <= m and a[len(a) - s - step:len(a) - s] == b[len(b) - s - step:len(b) - s]:
        s += step
    while s < m and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    while s > 0 and _same_class_end(a, b, s):
        s -= 1
    return p, s


def _cls(ch: str) -> int:
    return 1 if (ch.isalnum() or ch == "_") else 2 if ch.isspace() else 3


def _same_class(a: str, b: str, p: int) -> bool:
    """Would cutting at `p` split a word or whitespace run on either side?"""
    c = _cls(a[p - 1])
    if c == 3:
        return False
    return (p < len(a) and _cls(a[p]) == c) or (p < len(b) and _cls(b[p]) == c)


def _same_class_end(a: str, b: str, s: int) -> bool:
    c = _cls(a[len(a) - s])
    if c == 3:
        return False
    ia, ib = len(a) - s - 1, len(b) - s - 1
    return (ia >= 0 and _cls(a[ia]) == c) or (ib >= 0 and _cls(b[ib]) == c)


def _tokens(s: str, ids: Dict[str, int]) -> Tuple[List[int], "array[int]"]:
    """`s` as token ids (equal text = equal id, shared across both sides through `ids`) and each
    token's END offset. Ints and a flat array, not strings and tuples: a 5 MB minified line is
    ~2M tokens, and a list of (start, end) tuples alone would be ~250 MB."""
    toks: List[int] = []
    ends = array("q")
    get = ids.setdefault
    for m in _TOKEN.finditer(s):
        toks.append(get(m.group(), len(ids)))
        ends.append(m.end())
    return toks, ends


def _lis(seq: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Longest run of `seq` (sorted by its first item) increasing in its second — O(n log n)."""
    import bisect
    tails: List[int] = []
    tails_at: List[int] = []
    prev = [-1] * len(seq)
    for k, (_i, j) in enumerate(seq):
        x = bisect.bisect_left(tails, j)
        if x == len(tails):
            tails.append(j)
            tails_at.append(k)
        else:
            tails[x] = j
            tails_at[x] = k
        prev[k] = tails_at[x - 1] if x else -1
    out: List[Tuple[int, int]] = []
    k = tails_at[-1] if tails_at else -1
    while k >= 0:
        out.append(seq[k])
        k = prev[k]
    out.reverse()
    return out


def _mark_tokens(A: List[int], B: List[int], progress: Optional[List[int]] = None
                 ) -> Tuple[bytearray, bytearray, bool]:
    """Changed flags for each token of A and B. Returns (ca, cb, exact): `exact` is False when a
    gap with no unique anchor was marked as one span."""
    ca, cb = bytearray(len(A)), bytearray(len(B))
    exact = True
    stack = [(0, len(A), 0, len(B))]
    while stack:
        checkpoint()
        a0, a1, b0, b1 = stack.pop()
        while a0 < a1 and b0 < b1 and A[a0] == B[b0]:
            a0 += 1
            b0 += 1
        while a0 < a1 and b0 < b1 and A[a1 - 1] == B[b1 - 1]:
            a1 -= 1
            b1 -= 1
        if a0 == a1 or b0 == b1:
            ca[a0:a1] = b"\1" * (a1 - a0)
            cb[b0:b1] = b"\1" * (b1 - b0)
            continue
        if (a1 - a0) * (b1 - b0) <= LEAF_CELLS:
            sm = difflib.SequenceMatcher(None, A[a0:a1], B[b0:b1], autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "equal":
                    ca[a0 + i1:a0 + i2] = b"\1" * (i2 - i1)
                    cb[b0 + j1:b0 + j2] = b"\1" * (j2 - j1)
            if progress is not None:
                progress[0] += (a1 - a0) + (b1 - b0)
                report(done=progress[0])
            continue
        cnt_a = Counter(A[a0:a1])
        cnt_b = Counter(B[b0:b1])
        at_b = {B[j]: j for j in range(b0, b1) if cnt_b[B[j]] == 1 and cnt_a.get(B[j]) == 1}
        anchors = _lis([(i, at_b[A[i]]) for i in range(a0, a1) if A[i] in at_b])
        if not anchors:
            ca[a0:a1] = b"\1" * (a1 - a0)
            cb[b0:b1] = b"\1" * (b1 - b0)
            exact = False
            if progress is not None:
                progress[0] += (a1 - a0) + (b1 - b0)
                report(done=progress[0])
            continue
        pi, pj = a0, b0
        for i, j in anchors:
            if pi < i or pj < j:
                stack.append((pi, i, pj, j))
            pi, pj = i + 1, j + 1
        if pi < a1 or pj < b1:
            stack.append((pi, a1, pj, b1))
    return ca, cb, exact


def _ranges(ends: "array[int]", flags: bytearray, off: int) -> List[List[int]]:
    out: List[List[int]] = []
    s = 0
    for e, f in zip(ends, flags):
        if f:
            if out and out[-1][1] == s + off:
                out[-1][1] = e + off
            else:
                out.append([s + off, e + off])
        s = e
    return out


def middle_tokens(a: str, b: str) -> int:
    """Tokens left to compare once the common head and tail are trimmed (the pair's cost)."""
    p, s = _trim(a, b)
    count = 0
    for t in (a[p:len(a) - s], b[p:len(b) - s]):
        for _m in _TOKEN.finditer(t):
            count += 1
    return count


def intraline(a: str, b: str, progress: Optional[List[int]] = None) -> Dict[str, Any]:
    """Which characters changed between line `a` (old) and line `b` (new): `old` / `new` are
    `[start, end)` character ranges. `basis`: "same" (equal), "token" (exact at token level), or
    "span" (a gap with no anchor was marked as one span — said, not hidden)."""
    if a == b:
        return {"old": [], "new": [], "basis": "same", "tokens": 0}
    p, s = _trim(a, b)
    ids: Dict[str, int] = {}
    A, ea = _tokens(a[p:len(a) - s], ids)
    B, eb = _tokens(b[p:len(b) - s], ids)
    del ids
    ca, cb, exact = _mark_tokens(A, B, progress)
    return {"old": _ranges(ea, ca, p), "new": _ranges(eb, cb, p),
            "basis": "token" if exact else "span", "tokens": len(A) + len(B)}


def _pairs_of(raw: Any) -> List[Tuple[str, str]]:
    if not isinstance(raw, list):
        raise ValueError("pairs must be a list of [old, new] line texts")
    out: List[Tuple[str, str]] = []
    for x in raw:
        if not (isinstance(x, list) and len(x) == 2 and isinstance(x[0], str)
                and isinstance(x[1], str)):
            raise ValueError("each pair is [old line text, new line text]")
        out.append((x[0], x[1]))
    return out


def cost(pairs: Iterable[Tuple[str, str]]) -> int:
    """Tokens `batch` will compare — measured before the work, so the route decides job-or-not
    on the INPUT, not on a timer."""
    return sum(middle_tokens(a, b) for a, b in pairs)


def batch(pairs: List[Tuple[str, str]], total: Optional[int] = None) -> Dict[str, Any]:
    """Every pair, with progress in tokens (a job's `report`) and a cancel checkpoint per gap."""
    progress = [0]
    if total is not None:
        report(done=0, total=total, unit="tokens")
    out = []
    for a, b in pairs:
        checkpoint()
        out.append(intraline(a, b, progress))
    return {"pairs": out, "tokens": progress[0]}


def digest(pairs: List[Tuple[str, str]]) -> str:
    """The question's identity for job dedupe — the text stays out of the job's args."""
    h = hashlib.sha256()
    for a, b in pairs:
        for t in (a, b):
            data = t.encode("utf-8", "surrogatepass")
            h.update(str(len(data)).encode("ascii") + b":" + data)
    return h.hexdigest()[:32]


# =============================================================================== whitespace
_WS = re.compile(r"\s+")


_VERSION_TAIL = re.compile(r"[\d.]+$")


def _interpreter(first_line: str) -> str:
    """The interpreter a `#!` line names ("" when it names none): `#!/usr/bin/env -S python3 -u`
    -> "python", `#!/bin/bash` -> "bash"."""
    words = first_line[2:].split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
    if not words:
        return ""
    return _VERSION_TAIL.sub("", os.path.basename(words[0]).lower())


def indent_significant(path: str, first_line: Optional[str] = None) -> bool:
    """Whether a leading-whitespace change in `path` may be CODE (so it is never hidden). True
    unless the file is known to be indentation-free: a `#!` first line decides by its
    interpreter; otherwise the extension must be in `INDENT_FREE`. Unsure is True."""
    if first_line is not None and first_line.startswith("#!"):
        return _interpreter(first_line) not in INDENT_FREE_INTERPRETERS
    ext = os.path.splitext(os.path.basename(path or ""))[1].lower()
    return ext not in INDENT_FREE


def _first_line(hunks: List[Dict[str, Any]]) -> Optional[str]:
    """Line 1 of the file's new side as the diff shows it, or None when no hunk holds it (then
    the name decides)."""
    for h in hunks:
        for ln in h.get("lines") or []:
            if ln[2] == 1:
                return ln[3]
    return None


def _lead(t: str) -> str:
    return t[:len(t) - len(t.lstrip())]


def _ws_block(dels: List[list], adds: List[list], indent: bool,
              noeol: Tuple[Optional[int], Optional[int]]) -> Tuple[List[list], int, int]:
    """One change block -> (its lines re-ordered with whitespace-only pairs as context, hidden,
    kept-because-indentation)."""
    def key(ln: list, side: int) -> Tuple[str, bool]:
        no = ln[1] if side == 0 else ln[2]
        return _WS.sub("", ln[3]), no is not None and no == noeol[side]

    ka = [key(x, 0) for x in dels]
    kb = [key(x, 1) for x in adds]
    if len(ka) == len(kb) and ka == kb:
        ops = [("equal", 0, len(ka), 0, len(kb))]
    else:
        ops = difflib.SequenceMatcher(None, ka, kb).get_opcodes()
    out: List[list] = []
    hidden = kept = 0
    for tag, i1, i2, j1, j2 in ops:
        if tag != "equal":
            out.extend(dels[i1:i2])
            out.extend(adds[j1:j2])
            continue
        for d, a in zip(dels[i1:i2], adds[j1:j2]):
            if indent and _lead(d[3]).replace("\r", "") != _lead(a[3]).replace("\r", "") \
                    and d[3].strip():
                out.extend([d, a])
                kept += 1
                continue
            out.append([" ", d[1], a[2], a[3]])
            hidden += 1
    return out, hidden, kept


def _hunk_noeol(h: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    ne = h.get("noeol") or [None, None]
    return (ne[0], ne[1])


def ignore_whitespace(facts: Dict[str, Any]) -> Dict[str, Any]:
    """A DiffSet's facts with every whitespace-only line change shown as context. Each file says
    `ws_hidden` (pairs hidden) and `ws_kept` (indentation changes kept where indentation is code);
    the set says the totals. A file left with no change says `ws_only`. Binary / too-large files
    are untouched (they have no lines)."""
    total_hidden = total_kept = 0
    for f in facts.get("files") or []:
        f["ws_hidden"] = f["ws_kept"] = 0
        if f.get("binary") or f.get("too_large"):
            continue
        indent = indent_significant(f.get("path", ""), _first_line(f.get("hunks") or []))
        hunks = []
        hid = kept = 0
        for h in f.get("hunks") or []:
            lines = h.get("lines") or []
            out: List[list] = []
            k = 0
            while k < len(lines):
                if lines[k][0] == " ":
                    out.append(lines[k])
                    k += 1
                    continue
                j = k
                while j < len(lines) and lines[j][0] != " ":
                    j += 1
                block = lines[k:j]
                got, h1, k1 = _ws_block([x for x in block if x[0] == "-"],
                                        [x for x in block if x[0] == "+"], indent,
                                        _hunk_noeol(h))
                out.extend(got)
                hid += h1
                kept += k1
                k = j
            if any(x[0] != " " for x in out):
                hunks.append({**h, "lines": out})
        f["hunks"] = hunks
        f["ws_hidden"], f["ws_kept"] = hid, kept
        f["added"] = sum(1 for h in hunks for x in h["lines"] if x[0] == "+")
        f["removed"] = sum(1 for h in hunks for x in h["lines"] if x[0] == "-")
        f["ws_only"] = bool(hid) and not hunks
        total_hidden += hid
        total_kept += kept
    facts["added"] = sum(f.get("added", 0) for f in facts.get("files") or [])
    facts["removed"] = sum(f.get("removed", 0) for f in facts.get("files") or [])
    facts["ws"] = "ignore"
    facts["ws_hidden"], facts["ws_kept"] = total_hidden, total_kept
    return facts


def apply_ws(facts: Dict[str, Any], ws: str) -> Dict[str, Any]:
    """The diff routes' `ws` parameter: "" / "show" = as is (says so), "ignore" = filtered."""
    if ws == "ignore":
        return ignore_whitespace(facts)
    if ws not in ("", "show"):
        raise ValueError("ws must be 'ignore' or 'show'")
    facts.setdefault("ws", "show")
    return facts


# =============================================================================== context pages
_SKIP_CHUNK = 1024 * 1024


def _read_line(stream: Any, keep: bool) -> Optional[Tuple[bytes, int]]:
    """The next line of a binary stream, read in bounded chunks: (at most LINE_BYTES of it with
    the "\\n" dropped — b"" when `keep` is False, its full length in bytes without the "\\n"), or
    None at the end. Never holds more than one chunk of a long line."""
    head = stream.readline(LINE_BYTES + 1)
    if not head:
        return None
    if head.endswith(b"\n"):
        body = head[:-1]
        return (body if keep else b""), len(body)
    total = len(head)
    if total > LINE_BYTES:
        while True:
            more = stream.readline(_SKIP_CHUNK)
            if not more:
                break
            if more.endswith(b"\n"):
                total += len(more) - 1
                break
            total += len(more)
    return (head[:LINE_BYTES] if keep else b""), total


def _cut_text(raw: bytes) -> str:
    """`raw` (a line's kept head) decoded, dropping a UTF-8 character the cut split in two."""
    end = len(raw)
    back = 0                                    # continuation bytes at the very end
    while back < 3 and back < end and (raw[end - 1 - back] & 0xC0) == 0x80:
        back += 1
    lead_at = end - 1 - back
    if lead_at >= 0 and raw[lead_at] >= 0xC0:
        lead = raw[lead_at]
        need = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        if back + 1 < need:
            raw = raw[:lead_at]
    return raw.decode("utf-8", "replace")


def _lines_from(stream: Any, start: int, count: int) -> Tuple[List[str], bool, List[List[int]]]:
    """Lines `start .. start+count-1` (1-based) of a binary stream, "\\r" kept; whether the
    stream ended within the page; and `cut` — `[index in the page, full length in bytes]` for each
    line longer than LINE_BYTES (it is shown cut). Stops early (not at the end) once the page holds
    PAGE_BYTES; always returns at least one line when there is one."""
    out: List[str] = []
    cut: List[List[int]] = []
    n = 0
    used = 0
    while True:
        if n + 1 >= start and (len(out) >= count or used >= PAGE_BYTES):
            return out, False, cut
        got = _read_line(stream, keep=n + 1 >= start)
        if got is None:
            return out, True, cut
        n += 1
        if n < start:
            continue
        raw, total = got
        if total > len(raw):
            cut.append([len(out), total])
            out.append(_cut_text(raw))
        else:
            out.append(raw.decode("utf-8", "replace"))
        used += len(raw)


def _first_block_binary(head: bytes) -> bool:
    return b"\0" in head[:8192]


def context_page(root: str, path: str, start: int, count: int, *, rev: str = "",
                 snap: str = "", tree: str = "") -> Dict[str, Any]:
    """One page of the new side of `path`: the working tree (`tree`, default the repo), a git
    `rev`, or a snapshot `snap`. Callers have contained `path` and validated `rev`/`snap`/`tree`."""
    if start < 1 or count < 1:
        raise ValueError("want start >= 1 and count >= 1")
    count = min(count, PAGE_MAX)
    if snap:
        from . import snapshots as S
        sn = S.get(root, snap)
        if sn is None or sn.path != path:
            raise ContextUnavailable(f"no snapshot {snap[:40]} of {path}")
        text = S.content_of(root, sn)
        if text is None:
            raise ContextUnavailable("the snapshot holds no text (binary or too large)")
        data = text.encode("utf-8")
        if _first_block_binary(data):
            raise ContextUnavailable("binary content — no lines to show")
        lines, eof, cut = _lines_from(io.BytesIO(data), start, count)
    elif rev:
        argv = ["git", "-C", root, "show", "--end-of-options", f"{rev}:{path}"]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, **hidden())
        except OSError as exc:
            raise ContextUnavailable(f"git could not run: {exc}") from None
        try:
            with kill_on_cancel(proc):
                head = proc.stdout.peek(8192) if hasattr(proc.stdout, "peek") else b""
                if _first_block_binary(head):
                    raise ContextUnavailable("binary content — no lines to show")
                lines, eof, cut = _lines_from(proc.stdout, start, count)
        finally:
            if proc.poll() is None:
                proc.kill()
            out, err = proc.communicate()
        if proc.returncode not in (0, None) and not lines and eof:
            raise ContextUnavailable(err.decode("utf-8", "replace").strip()[:300]
                                     or f"{path} is not in {rev}")
    else:
        from .diffs import open_contained
        got = open_contained(tree or root, path)
        if got == "":
            raise ContextUnavailable(f"{path} is gone from the working tree")
        if not got:
            raise ContextUnavailable(f"{path} is not readable")
        fh, _st = got
        with fh:
            head = fh.read(8192)
            if _first_block_binary(head):
                raise ContextUnavailable("binary content — no lines to show")
            fh.seek(0)
            lines, eof, cut = _lines_from(fh, start, count)
    return {"path": path, "start": start, "lines": lines, "eof": eof, "cut": cut,
            "next": None if eof else start + len(lines)}


__all__ = ["ContextUnavailable", "INDENT_FREE", "INDENT_FREE_INTERPRETERS", "LINE_BYTES",
           "PAGE_BYTES", "LEAF_CELLS", "PAGE_MAX", "SYNC_TOKENS",
           "apply_ws", "batch", "context_page", "cost", "digest", "ignore_whitespace",
           "indent_significant", "intraline", "middle_tokens"]
