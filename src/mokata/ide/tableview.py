"""Delimited text (CSV / TSV / ...) as a TABLE in the IDE — and the cell-level diff of a changed one
(0.0.27 P8, ID-CSV-VIEW).

Rules this module keeps (each has a business test in tests/test_p8_ide_csv_view.py):

  * A file of ANY size is viewable. The server never reads it whole: a sparse index of byte offsets
    (one per STRIDE records) is built as work needs it and by the `table.count` job, and a page of
    rows is read from the nearest offset at or before it. Memory is bounded by the index (8 bytes
    per STRIDE records) and the page, not by the file.
  * A record is what Python's `csv` module says it is, so a quoted field with an embedded newline,
    a CRLF line end, doubled quotes and a UTF-8 BOM all read right. Blank lines are not records.
    A ragged row (cell count differs from the header's) is SHOWN, and flagged. Bytes that are not
    UTF-8 are decoded with replacement and the row is flagged `bad`. A cell longer than CELL_MAX
    characters is cut for display and the row flagged `cut` (a page must stay a page).
  * Line ends are CRLF, LF or a bare CR (old Mac exports). Encodings: UTF-8 (with or without a BOM)
    and UTF-16 with a BOM (either byte order) are decoded; UTF-32 and a UTF-16 file with no BOM
    (NUL bytes) are REFUSED with a plain note — never shown as garbage.
  * The delimiter is the extension's when the file agrees, else sniffed from the first bytes.
  * Cell content is ALWAYS text: this module returns strings and flags, never markup. `guard_cell`
    is the CSV-injection guard for anything copied or exported (a cell that a spreadsheet would run
    as a formula gets a leading apostrophe) — one implementation, used by the export route.
  * Table diff: two versions of one file, compared cell by cell — by row POSITION (O(1) memory) or
    by a KEY column (memory is the old side's keys + an 8-byte digest + an offset per row, said so
    in the answer). Columns are matched by header name. The changes are spooled to disk and paged,
    so an everything-changed 2 GB file is as viewable as a one-cell change.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import array
import atexit
import csv
import hashlib
import heapq
import itertools
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict, deque
from contextlib import ExitStack
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Tuple

from ..errors import MokataError
from ..knowledge.cancel import checkpoint, kill_on_cancel, report
from ..quiet_spawn import hidden

STRIDE = 256                 # one offset per this many records (header included)
STEP_RECORDS = 20000         # records scanned per locked step of the index build
PAGE_INDEX_STEPS = 4         # index steps a plain page request may add (80k records); past it the
                             # row is "not indexed yet" and the visible count job gets it there
PAGE_DEFAULT = 200
PAGE_MAX = 2000              # rows per request: a viewport is a few dozen; disclosed in /info
CELL_MAX = 65536             # characters of one cell kept for display
SNIFF_BYTES = 65536
SNIFF_RECORDS = 60
CACHE_TABLES = 8
CACHE_DIFFS = 8
EXPORT_MAX_BYTES = 32 * 1024 * 1024
#: extensions the Code view opens as a table by itself, and the ones it offers a Table button for
AUTO_EXT = (".csv", ".tsv", ".tab", ".psv")
OFFER_EXT = (".txt", ".dat", ".dsv")
CANDIDATES = (",", "\t", ";", "|")
DELIM_NAME = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}
BOM = b"\xef\xbb\xbf"
CHUNK = 1 << 20              # bytes per read of the line splitter
ROW_CHUNK = 1 << 16          # bytes per read when ONE record is fetched by offset
#: The most one RECORD (a line, or a quoted field spanning lines) may take. A record is held whole
#: while it is parsed (bytes + text + csv's buffer: about 3-4x its size at the bound), so the bound
#: IS the memory bound; past it the file is refused in plain words, in a few milliseconds.
RECORD_MAX = 64 * 1024 * 1024
#: Bytes a plain (non-job) request such as /info or /rows may read before it answers with what it
#: has; longer walks belong to the visible `table.count` / `table.view` jobs.
INLINE_BYTES = 32 * 1024 * 1024
#: Memory one sorted RUN may take before it is spilled to disk (external sort). A budget, not a
#: limit on rows: more rows make more runs.
RUN_BYTES = 64 * 1024 * 1024
_U16 = {"utf-16-le": (2, re.compile(b"\r\x00\n\x00|\n\x00|\r\x00")),
        "utf-16-be": (2, re.compile(b"\x00\r\x00\n|\x00\n|\x00\r"))}

try:
    csv.field_size_limit(2 ** 31 - 1)
except (OverflowError, ValueError):               # pragma: no cover - a 32-bit build
    csv.field_size_limit(2 ** 30)


class NotATable(MokataError):
    """The file cannot be shown as a table; `.state` says why (binary / absent / unreadable)."""

    def __init__(self, state: str, note: str = "") -> None:
        super().__init__(note or state)
        self.state, self.note = state, note or state


def is_table_path(path: str) -> bool:
    return (path or "").lower().endswith(AUTO_EXT + OFFER_EXT)


# ============================================================================ the injection guard
_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\Z")


def guard_cell(text: str) -> str:
    """OWASP's CSV-injection guard: a cell a spreadsheet would read as a formula (it starts with
    `=`, `+`, `-`, `@`, a tab or a carriage return) gets a leading apostrophe, so Excel / Sheets /
    LibreOffice show it as text. A plain number such as `-12.5` is left alone — it is not a formula
    and a data export that rewrote every negative number would be wrong."""
    if text and text[0] in "=+-@\t\r" and not _NUMBER.match(text):
        return "'" + text
    return text


def to_text(rows: List[List[Any]], fmt: str = "csv") -> str:
    """`rows` as CSV / TSV text with every cell guarded. Quoting is the csv module's, so a cell
    with the delimiter, a quote or a newline round-trips."""
    delim = "\t" if fmt == "tsv" else ","
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delim, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        w.writerow([guard_cell(c if isinstance(c, str) else "" if c is None else str(c))
                    for c in row])
    return buf.getvalue()


# ============================================================================ sniffing
def _widths(sample: str, delim: str) -> List[int]:
    out: List[int] = []
    try:
        for row in csv.reader(sample.splitlines(keepends=True), delimiter=delim):
            if row:
                out.append(len(row))
            if len(out) >= SNIFF_RECORDS:
                break
    except csv.Error:
        pass
    return out


def _score(widths: List[int]) -> float:
    if not widths:
        return 0.0
    modal = max(set(widths), key=widths.count)
    if modal < 2:
        return 0.0
    return modal * (widths.count(modal) / len(widths))


def sniff(path: str, sample: bytes, enc: str = "utf-8") -> Tuple[str, str]:
    """(delimiter, how it was found). The extension's delimiter wins when the file's first records
    agree with it; otherwise the candidate that gives the most consistent multi-column records."""
    low = path.lower()
    pref = "\t" if low.endswith((".tsv", ".tab")) else "|" if low.endswith(".psv") \
        else "," if low.endswith(".csv") else ""
    if enc != "utf-8":
        sample = sample[:len(sample) - len(sample) % 2]
    text = sample.decode(enc, "replace")
    cut = max(text.rfind("\n"), text.rfind("\r"))
    if cut > 0 and len(sample) >= SNIFF_BYTES:
        text = text[:cut + 1]
    scores = {d: _score(_widths(text, d)) for d in CANDIDATES}
    if pref and scores[pref] > 0 and scores[pref] >= 0.8 * max(scores.values()):
        return pref, "extension"
    best = max(CANDIDATES, key=lambda d: (scores[d], d == pref))
    if scores[best] > 0:
        return best, "sniffed"
    return (pref or ","), ("extension" if pref else "default")


# ============================================================================ records
def detect(head: bytes) -> Tuple[str, int]:
    """(encoding, BOM length) from the first bytes. UTF-32 and a BOM-less file with NULs are not
    tables we can read honestly, so they are refused here, by name."""
    if head.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        raise NotATable("unsupported", "this file is UTF-32 — save it as UTF-8 or UTF-16 to view it")
    if head.startswith(BOM):
        return "utf-8", len(BOM)
    if head.startswith(b"\xff\xfe"):
        return "utf-16-le", 2
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    if b"\0" in head[:8192]:
        raise NotATable("binary", "this file has NUL bytes — it is not delimited text (UTF-16 "
                        "needs a byte-order mark; save it as UTF-8 to view it)")
    return "utf-8", 0


def _too_long() -> "NotATable":
    return NotATable("unsupported", f"a single record in this file is over {RECORD_MAX >> 20} MiB — "
                     "that is one giant cell, not a table; open it as text or split it")


def _split_utf8(chunk: bytes) -> Tuple[List[bytes], bytes]:
    """(complete lines, unfinished tail) of ONE chunk. The tail is an unterminated line, or a line
    ending in CR (it may be the first half of a CRLF the next chunk completes)."""
    lines = chunk.splitlines(True)
    if not lines:
        return [], b""
    if lines[-1][-1:] not in (b"\n", b"\r") or lines[-1].endswith(b"\r"):
        return lines[:-1], lines[-1]
    return lines, b""


def _splitter_u16(enc: str):
    unit, pat = _U16[enc]

    def split(chunk: bytes) -> Tuple[List[bytes], bytes]:
        out: List[bytes] = []
        start = i = 0
        while True:
            m = pat.search(chunk, i)
            if m is None:
                break
            if m.start() % unit:                   # a "newline" straddling two characters
                i = m.start() + 1
                continue
            if m.end() == len(chunk) and m.group() in (b"\r\x00", b"\x00\r"):
                break                              # a CR at the edge: the next chunk may add its LF
            out.append(chunk[start:m.end()])
            start = i = m.end()
        return out, chunk[start:]
    return split


def _raw_lines(fh: Any, enc: str, chunk: int = CHUNK) -> Iterator[bytes]:
    """Byte lines of `fh` from where it is positioned, each WITH its terminator — CRLF, LF or a
    bare CR. LINEAR: each chunk is scanned once and only the unfinished tail is carried (as a list
    of pieces, joined once when its line ends), so a 60 MB line costs 60 MB of reads and scans,
    not a re-scan per chunk. A line over RECORD_MAX raises NotATable; every chunk is a Cancel
    checkpoint."""
    split = _splitter_u16(enc) if enc in _U16 else _split_utf8
    cr = b"\r\x00" if enc == "utf-16-le" else b"\x00\r" if enc == "utf-16-be" else b"\r"
    lf = b"\n\x00" if enc == "utf-16-le" else b"\x00\n" if enc == "utf-16-be" else b"\n"
    pending: List[bytes] = []
    plen = 0
    held_cr = False
    while True:
        checkpoint()
        data = fh.read(chunk)
        if not data:
            if pending:
                yield b"".join(pending)
            return
        if held_cr:                                # the previous chunk ended in a CR
            if data.startswith(lf):
                pending.append(lf)
                data = data[len(lf):]
            yield b"".join(pending)
            pending, plen, held_cr = [], 0, False
            if not data:
                continue
        lines, tail = split(data)
        if lines:
            if pending:
                lines[0] = b"".join(pending) + lines[0]
                pending, plen = [], 0
            yield from lines
        if tail:
            pending.append(tail)
            plen += len(tail)
            if plen > RECORD_MAX:
                raise _too_long()
            held_cr = tail.endswith(cr)


class _Lines:
    """The file as decoded lines (a CRLF stays inside the line csv sees), counting bytes consumed
    — so the byte offset after every record is exact — and noting a bad decode."""

    def __init__(self, fh: Any, pos: int, enc: str = "utf-8", chunk: int = CHUNK) -> None:
        self.bad, self.last, self.enc, self.rec_start = False, "", enc, pos
        if pos == 0:                               # the byte-order mark is not data
            head = fh.read(4)
            skip = len(BOM) if head.startswith(BOM) and enc == "utf-8" else \
                2 if enc != "utf-8" and head[:2] in (b"\xff\xfe", b"\xfe\xff") else 0
            fh.seek(skip)
            pos = skip
        self.pos = pos
        self.it = _raw_lines(fh, enc, chunk)

    def __iter__(self) -> "_Lines":
        return self

    def __next__(self) -> str:
        line = next(self.it)
        self.pos += len(line)
        if self.pos - self.rec_start > RECORD_MAX:        # a quoted field spanning many lines
            raise _too_long()
        try:
            s = line.decode(self.enc)
        except UnicodeDecodeError:
            s = line.decode(self.enc, "replace")
            self.bad = True
        self.last = s
        return s


def _records(fh: Any, start: int, delim: str, enc: str = "utf-8",
             chunk: int = CHUNK) -> Iterator[Tuple[List[str], int, int, bool, bool]]:
    """(cells, start_offset, end_offset, bad_utf8, cut) for each record from byte `start` on."""
    fh.seek(start)
    lines = _Lines(fh, start, enc, chunk)
    reader = csv.reader(lines, delimiter=delim)
    here = start
    while True:
        lines.bad = False
        lines.rec_start = here
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error:                          # e.g. a stray quote the dialect cannot take
            row = [lines.last.rstrip("\r\n")]
        if not row:
            here = lines.pos                       # a blank line is not a record
            continue
        cut = False
        for i, c in enumerate(row):
            if len(c) > CELL_MAX:
                row[i], cut = c[:CELL_MAX], True
        yield row, here, lines.pos, lines.bad, cut
        here = lines.pos


def _open_plain(path: str):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    return os.fdopen(os.open(path, flags), "rb")


# ============================================================================ the table
class Table:
    """One delimited file: its dialect, its sparse record index, and paging from it."""

    def __init__(self, path: str, rel: str = "", opener: Optional[Callable[[], Any]] = None) -> None:
        """`opener` returns a fresh binary file object for EVERY read. A repo file's opener is the
        contained open (`contained_opener`): no read of a repo file ever goes by a bare path."""
        self.path, self.rel = path, rel or os.path.basename(path)
        self._opener = opener
        try:
            with self.open() as fh:
                st = os.fstat(fh.fileno())
                head = fh.read(SNIFF_BYTES)
        except OSError as exc:
            raise NotATable("unreadable", str(exc)) from exc
        self.size, self.mtime = st.st_size, st.st_mtime_ns
        self.enc, skip = detect(head)
        self.bom = skip > 0
        self.delim, self.delim_by = sniff(self.rel, head[skip:], self.enc)
        self.lock = threading.RLock()
        self.offsets: List[int] = [0]          # offsets[i] = start of record i*STRIDE
        self.scan_pos = 0                      # start of the next record not yet counted
        self.scanned = 0                       # records counted so far (header included)
        self.complete = self.size == 0
        self.width = 0
        self.header: List[str] = []
        self.ragged = 0
        self.bad = 0
        self.cut = 0
        self._head_done = False

    # -- identity -----------------------------------------------------------------------------
    def open(self) -> Any:
        """A new binary handle on the file — through the contained open for a repo file."""
        return self._opener() if self._opener else _open_plain(self.path)

    def fresh(self) -> bool:
        try:
            with self.open() as fh:
                st = os.fstat(fh.fileno())
        except (OSError, NotATable):
            return False
        return st.st_size == self.size and st.st_mtime_ns == self.mtime

    # -- the index ----------------------------------------------------------------------------
    def step(self, limit: int = STEP_RECORDS, max_bytes: Optional[int] = None) -> bool:
        """Advance the index by up to `limit` records (and, for an inline request, up to
        `max_bytes` — it stops after the record that crosses it). True while there is more."""
        with self.lock:
            if self.complete:
                return False
            n = 0
            first = self.scan_pos
            with self.open() as fh:
                for row, _s, end, bad, cut in _records(fh, self.scan_pos, self.delim, self.enc):
                    if self.scanned == 0:
                        self.header = row
                        self.width = len(row)
                    elif len(row) != self.width:
                        self.ragged += 1
                    self.bad += bad
                    self.cut += cut
                    self.scanned += 1
                    self.scan_pos = end
                    if self.scanned % STRIDE == 0:
                        self.offsets.append(end)
                    n += 1
                    if n >= limit or (max_bytes is not None and end - first >= max_bytes):
                        return True
            self.complete = True
            return False

    def ensure(self, record: int, steps: Optional[int] = None) -> bool:
        """Index far enough that the block holding `record` has an offset (or the file ended).
        `steps` bounds the work a plain page request may do (long indexing belongs to the visible
        `table.count` job); False means "not reached yet"."""
        block = record // STRIDE
        while len(self.offsets) <= block and not self.complete:
            if steps is not None:
                if steps <= 0:
                    return False
                steps -= 1
            checkpoint()
            self.step(max_bytes=INLINE_BYTES if steps is not None else None)
        return True

    def head(self) -> None:
        """The header and the first block, enough for /info and the first page."""
        if not self._head_done:
            with self.lock:
                if self.scanned == 0:
                    self.step(STRIDE, INLINE_BYTES)
                self._head_done = True

    def count(self) -> Dict[str, Any]:
        """Walk the whole file (a job): rows, ragged rows, bad bytes. Cancellable per step."""
        while True:
            checkpoint()
            more = self.step()
            report(done=self.scan_pos >> 20, total=max(self.size >> 20, 1), unit="MB",
                   note=f"{max(self.scanned - 1, 0):,} rows so far")
            if not more:
                break
        return self.summary()

    # -- reading ------------------------------------------------------------------------------
    def total(self) -> Optional[int]:
        return max(self.scanned - 1, 0) if self.complete else None

    def summary(self) -> Dict[str, Any]:
        return {"rows": self.total(), "complete": self.complete, "ragged": self.ragged,
                "bad_utf8": self.bad, "cut": self.cut}

    def estimate(self) -> int:
        """Rows by the bytes the scanned prefix took — what the scrollbar uses while counting."""
        if self.complete:
            return max(self.scanned - 1, 0)
        if self.scanned < 2 or self.scan_pos <= 0:
            return 0
        return int(self.size / (self.scan_pos / self.scanned)) - 1

    def page(self, row: int, count: int) -> Dict[str, Any]:
        """Data rows `row` .. `row+count-1` (0-based, the header excluded)."""
        self.head()
        row, count = max(0, row), max(1, min(PAGE_MAX, count))
        rec = row + 1
        if not self.ensure(rec, PAGE_INDEX_STEPS):
            return {"row": row, "rows": [], "flags": {}, "total": None, "indexed": False,
                    "indexed_rows": max(self.scanned - 1, 0), "estimate": self.estimate(),
                    "complete": False}
        block = rec // STRIDE
        rows: List[List[str]] = []
        flags: Dict[str, List[str]] = {}
        if block < len(self.offsets):
            skip = rec - block * STRIDE
            with self.open() as fh:
                for i, (cells, _s, _e, bad, cut) in enumerate(
                        _records(fh, self.offsets[block], self.delim, self.enc)):
                    if i < skip:
                        continue
                    r = row + len(rows)
                    f = []
                    if len(cells) != self.width:
                        f.append("ragged")
                    if bad:
                        f.append("bad")
                    if cut:
                        f.append("cut")
                    if f:
                        flags[str(r)] = f
                    rows.append(cells)
                    if len(rows) >= count:
                        break
        return {"row": row, "rows": rows, "flags": flags, "total": self.total(), "indexed": True,
                "indexed_rows": max(self.scanned - 1, 0), "estimate": self.estimate(), "complete": self.complete}

    def info(self) -> Dict[str, Any]:
        self.head()
        return {"path": self.rel, "size": self.size, "delimiter": self.delim,
                "delimiter_name": DELIM_NAME.get(self.delim, self.delim),
                "delimiter_by": self.delim_by, "bom": self.bom, "encoding": self.enc, "header": self.header,
                "width": self.width, "page": PAGE_DEFAULT, "page_max": PAGE_MAX,
                "cell_max": CELL_MAX, "record_max": RECORD_MAX, **self.summary(), "estimate": self.estimate()}

    def records(self) -> Iterator[Tuple[List[str], int, bool, bool]]:
        """Every record in order: (cells, start_offset, bad, cut). The diff's sequential read."""
        with self.open() as fh:
            for row, start, _e, bad, cut in _records(fh, 0, self.delim, self.enc):
                yield row, start, bad, cut

    def row_at(self, offset: int) -> List[str]:
        with self.open() as fh:
            for row, _s, _e, _b, _c in _records(fh, offset, self.delim, self.enc, ROW_CHUNK):
                return row
        return []


_TABLES: "OrderedDict[str, Table]" = OrderedDict()
_TLOCK = threading.Lock()


def contained_opener(root: str, rel: str) -> Callable[[], Any]:
    """An opener whose every call is the CONTAINED open (`diffs.open_contained`): resolved inside
    the repo, no final symlink, a regular file on the open descriptor, re-checked after the open.
    A swap between two reads is caught by the next read's own check."""
    from .diffs import open_contained

    def opener() -> Any:
        got = open_contained(root, rel)
        if got == "":
            raise NotATable("absent", "the file does not exist")
        if not got:
            raise NotATable("unreadable", "the file cannot be read")
        return got[0]
    return opener


def open_table(path: str, rel: str = "", opener: Optional[Callable[[], Any]] = None) -> Table:
    """The (cached) Table for `path`; a changed file gets a new one, so the index is never stale."""
    key = os.path.realpath(path)
    with _TLOCK:
        t = _TABLES.get(key)
        if t is not None and t.fresh():
            _TABLES.move_to_end(key)
            return t
        t = Table(key, rel, opener)
        _TABLES[key] = t
        while len(_TABLES) > CACHE_TABLES:
            _TABLES.popitem(last=False)
        return t


def cache_info() -> Dict[str, int]:
    return {"tables": len(_TABLES), "max_tables": CACHE_TABLES, "diffs": len(_DIFFS),
            "max_diffs": CACHE_DIFFS}


# ============================================================================ the table diff
_DIFFS: "OrderedDict[str, Diff]" = OrderedDict()
_DLOCK = threading.Lock()


def _names(header: List[str]) -> List[str]:
    """Column names, made unique (`a`, `a#2`) so a repeated header still maps cell to cell."""
    seen: Dict[str, int] = {}
    out = []
    for h in header:
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}#{seen[h]}")
    return out


class Diff:
    """A finished table diff: its summary and its changes, spooled to a file and paged."""

    def __init__(self, did: str) -> None:
        self.id = did
        # An anonymous temp file: no name to leak, deleted by the OS when closed (Windows too).
        self.fh = tempfile.TemporaryFile("w+b")
        self.at = array.array("Q")             # 8 bytes per change: where its line starts
        self.summary: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def add(self, change: Dict[str, Any]) -> None:
        self.at.append(self.fh.tell())
        self.fh.write(json.dumps(change, ensure_ascii=False, separators=(",", ":"))
                      .encode("utf-8") + b"\n")

    def page(self, start: int, count: int) -> List[Dict[str, Any]]:
        start, count = max(0, start), max(1, min(PAGE_MAX, count))
        with self.lock:
            out = []
            self.fh.flush()
            for i in range(start, min(len(self.at), start + count)):
                self.fh.seek(self.at[i])
                out.append(json.loads(self.fh.readline().decode("utf-8")))
            self.fh.seek(0, os.SEEK_END)
            return out

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


def _drop_all() -> None:
    with _DLOCK:
        for d in _DIFFS.values():
            d.close()
        _DIFFS.clear()


atexit.register(_drop_all)


def get_diff(did: str) -> Optional[Diff]:
    with _DLOCK:
        d = _DIFFS.get(did)
        if d is not None:
            _DIFFS.move_to_end(did)
        return d


def _keep(d: Diff) -> None:
    with _DLOCK:
        _DIFFS[d.id] = d
        while len(_DIFFS) > CACHE_DIFFS:
            _DIFFS.popitem(last=False)[1].close()


def _same(a: List[Optional[str]], b: List[Optional[str]]) -> bool:
    """Equal cell by cell, a column one side does not have counting as empty (a column added to
    the file must not make every old row "changed")."""
    return all((x or "") == (y or "") for x, y in zip(a, b))


def _digest(cells: List[Optional[str]]) -> int:
    h = hashlib.blake2b(digest_size=8)
    for c in cells:                       # length-prefixed: no cell content can fake a boundary
        b = (c or "").encode("utf-8", "replace")
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return int.from_bytes(h.digest(), "big")


def _human(n: int) -> str:
    return f"{n >> 20:,} MB" if n >= 1 << 20 else f"{max(n >> 10, 1):,} KB"


def _kdigest(key: str) -> bytes:
    """A 128-bit digest of a key cell: the keyed diff's map holds 16 bytes per row however long
    the key is (a collision needs 2^64 rows)."""
    return hashlib.blake2b(key.encode("utf-8", "replace"), digest_size=16).digest()


def _pack(row: int, start: int, dg: int) -> int:
    return (row << 112) | (start << 64) | dg


def _unpack(v: int) -> Tuple[int, int, int]:
    return v >> 112, (v >> 64) & ((1 << 48) - 1), v & ((1 << 64) - 1)


def _held_bytes(seen: Dict[bytes, int], more: Dict[bytes, Deque[int]]) -> int:
    """What the keyed diff's maps hold: the first-row map by one sampled entry, and EVERY repeated
    row (each is a packed int in a deque) — so a column of identical keys reports what it holds."""
    if not seen:
        return 0
    k, v = next(iter(seen.items()))
    held = sys.getsizeof(seen) + (sys.getsizeof(k) + sys.getsizeof(v)) * len(seen)
    held += sys.getsizeof(more)
    for key, dq in more.items():
        held += sys.getsizeof(key) + sys.getsizeof(dq) + sys.getsizeof(v) * len(dq)
    return held


class _Cursor:
    """Old rows by ascending offset from ONE sequential read of the old side. A request for an
    offset the cursor already passed (a file in a different order) falls back to one small
    random read — so the ordered case costs one pass, and only the shuffled case pays per row."""

    def __init__(self, table: Table) -> None:
        self.table, self.it, self.cur = table, table.records(), None

    def get(self, start: int) -> List[str]:
        while True:
            if self.cur is None:
                self.cur = next(self.it, None)
                if self.cur is None:
                    return self.table.row_at(start)
            if self.cur[1] == start:
                return self.cur[0]
            if self.cur[1] > start:
                return self.table.row_at(start)
            self.cur = None

    def close(self) -> None:
        self.it.close()


def diff_tables(old: Optional[Table], new: Optional[Table], key: str, did: str) -> Dict[str, Any]:
    done = False
    try:
        with ExitStack() as stack:             # every record reader is CLOSED (its file with it)
            summary = _diff_tables(old, new, key, did, stack)   # before this function returns
            done = True                        # or raises — a Cancel included (Windows)
            return summary
    finally:
        if not done:                            # Cancel, a bad key, a read error: leave no spool
            half = _PENDING.pop(did, None)
            if half is not None:
                half.close()


_PENDING: Dict[str, "Diff"] = {}


def _diff_tables(old: Optional[Table], new: Optional[Table], key: str, did: str,
                 stack: ExitStack) -> Dict[str, Any]:
    """Compare two versions of a table (either may be absent: an added / deleted file).

    `key` empty compares row i with row i; otherwise rows are matched on that column (its header
    name), repeated keys pairing in order. Returns the summary (and the Diff is kept for paging)."""
    for t in (old, new):
        if t is not None:
            t.head()
    oh = _names(old.header) if old else []
    nh = _names(new.header) if new else []
    cols = nh + [c for c in oh if c not in nh]
    oi = {c: i for i, c in enumerate(oh)}
    ni = {c: i for i, c in enumerate(nh)}

    def aligned(cells: List[str], idx: Dict[str, int]) -> List[Optional[str]]:
        return [cells[idx[c]] if c in idx and idx[c] < len(cells) else None for c in cols]

    summary: Dict[str, Any] = {
        "columns": cols, "key": key, "mode": "key" if key else "position",
        "columns_added": [c for c in nh if old is not None and c not in oi],
        "columns_removed": [c for c in oh if new is not None and c not in ni],
        "added": 0, "removed": 0, "changed": 0, "same": 0, "duplicate_keys": 0,
        "old_rows": None, "new_rows": None,
        "memory": "a positional diff holds one row of each side at a time"}
    out = Diff(did)
    _PENDING[did] = out
    if key:
        if (old is not None and key not in oi) or (new is not None and key not in ni):
            out.close()
            raise KeyError(key)

    def change(op: str, o: Optional[List[Optional[str]]], n: Optional[List[Optional[str]]],
               orow: Optional[int], nrow: Optional[int]) -> None:
        ch: List[int] = []
        if op == "chg":
            ch = [i for i in range(len(cols)) if ((o or [])[i] or "") != ((n or [])[i] or "")]
        out.add({"op": op, "old_row": orow, "new_row": nrow, "old": o, "new": n, "changed": ch})

    old_data = (old.records() if old else iter(()))
    new_data = (new.records() if new else iter(()))
    for gen in (old_data, new_data):
        stack.callback(getattr(gen, "close", lambda: None))
    next(old_data, None)                      # the header
    next(new_data, None)
    osz, nsz = (old.size if old else 0), (new.size if new else 0)
    total_mb = max((osz + nsz) >> 20, 1)

    def progress(done: int, note: str) -> None:
        checkpoint()
        report(done=done >> 20, total=total_mb, unit="MB", note=note)
    nold = nnew = 0
    if not key:
        for i, (a, b) in enumerate(itertools.zip_longest(old_data, new_data)):
            if i % 5000 == 0:
                progress((a[1] if a else osz) + (b[1] if b else nsz),
                         f"{i:,} row pairs compared · {summary['changed'] + summary['added'] + summary['removed']:,} differ")
            if a is not None:
                nold += 1
            if b is not None:
                nnew += 1
            if a is None:
                summary["added"] += 1
                change("add", None, aligned(b[0], ni), None, i)
            elif b is None:
                summary["removed"] += 1
                change("del", aligned(a[0], oi), None, i, None)
            else:
                ao, an = aligned(a[0], oi), aligned(b[0], ni)
                if _same(ao, an):
                    summary["same"] += 1
                else:
                    summary["changed"] += 1
                    change("chg", ao, an, i, i)
    else:
        # key digest -> one packed int (old row number, its byte offset, a digest of its cells).
        # A matched key is popped; what is left at the end was removed. A key repeated on the old
        # side keeps its later rows in `more[key]`, paired in order with the new side's repeats.
        seen: Dict[bytes, int] = {}
        more: Dict[bytes, Deque[int]] = {}
        for i, (cells, start, _b, _c) in enumerate(old_data):
            if i % 5000 == 0:
                progress(start, f"reading the old side's keys · {i:,} rows")
            nold += 1
            kd = _kdigest(cells[oi[key]] if oi[key] < len(cells) else "")
            packed = _pack(i, start, _digest(aligned(cells, oi)))
            if kd in seen:
                more.setdefault(kd, deque()).append(packed)
                summary["duplicate_keys"] += 1
            else:
                seen[kd] = packed
        summary["memory_bytes"] = _held_bytes(seen, more)
        summary["memory"] = (f"keyed diff holds the old side's {nold:,} keys in memory "
                             f"(about {_human(summary['memory_bytes'])}); a positional diff "
                             "holds one row of each side")
        cursor, rcursor = (_Cursor(old), _Cursor(old)) if old else (None, None)
        for cur in (cursor, rcursor):
            if cur is not None:
                stack.callback(cur.close)
        for i, (cells, start, _b, _c) in enumerate(new_data):
            if i % 5000 == 0:
                progress(osz + start, f"matching the new side · {i:,} rows")
            nnew += 1
            kd = _kdigest(cells[ni[key]] if ni[key] < len(cells) else "")
            an = aligned(cells, ni)
            hit = seen.pop(kd, None)
            if hit is None and more.get(kd):
                hit = more[kd].popleft()
            if hit is None:
                summary["added"] += 1
                change("add", None, an, None, i)
                continue
            orow, ostart, dg = _unpack(hit)
            if dg == _digest(an):
                summary["same"] += 1
            else:
                summary["changed"] += 1
                change("chg", aligned(cursor.get(ostart), oi), an, orow, i)
        left = sorted(list(seen.values()) + [v for vs in more.values() for v in vs])
        for n, packed in enumerate(left):          # row-number order: the first field is the row
            if n % 5000 == 0:
                progress(osz + nsz, f"listing removed rows · {n:,}")
            orow, ostart, _dg = _unpack(packed)
            summary["removed"] += 1
            change("del", aligned(rcursor.get(ostart), oi), None, orow, None)
    summary["old_rows"] = nold if old else None
    summary["new_rows"] = nnew if new else None
    summary["changes"] = len(out.at)
    out.summary = summary
    _PENDING.pop(did, None)
    _keep(out)
    return summary


# ============================================================================ filter + sort (views)
FILTER_OPS = ("contains", "equals", "starts", "empty", "notempty", "gt", "lt", "gte", "lte")
_NUM_OPS = ("gt", "lt", "gte", "lte")


class _Rev:
    """A text sort key that orders backwards (a descending sort keeps equal keys in file order)."""
    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s

    def __lt__(self, o: "_Rev") -> bool:
        return self.s > o.s

    def __eq__(self, o: object) -> bool:
        return isinstance(o, _Rev) and self.s == o.s

    def __getstate__(self) -> str:
        return self.s

    def __setstate__(self, st: str) -> None:
        self.s = st


def _sort_key(cell: str, desc: bool) -> Tuple[Any, ...]:
    """Numbers (by value), then text (case-insensitive), then empty cells — LAST in either
    direction. Ties are broken by file order by the caller."""
    t = cell.strip()
    if t == "":
        return (2,)
    if _NUMBER.match(t):
        v = float(t)
        return (0, -v if desc else v)
    c = t.casefold()
    return (1, _Rev(c) if desc else c)


def _predicate(fcol: int, fop: str, fval: str, stats: Dict[str, int]) -> Callable[[List[str]], bool]:
    """A row filter. `fcol` -1 looks in every column. Numeric operators compare numbers; a cell
    that is not a number never matches and is COUNTED (`not_numeric`), so the answer says so."""
    want = fval.casefold()
    num = None
    if fop in _NUM_OPS:
        try:
            num = float(fval.strip())
        except ValueError:
            num = None

    def test(cell: str) -> bool:
        c = cell
        if fop == "empty":
            return c.strip() == ""
        if fop == "notempty":
            return c.strip() != ""
        if fop in _NUM_OPS:
            t = c.strip()
            if t == "":
                return False                          # a blank is not "not a number"
            if num is None or not _NUMBER.match(t):
                stats["not_numeric"] += 1
                return False
            v = float(t)
            return v > num if fop == "gt" else v < num if fop == "lt" else \
                v >= num if fop == "gte" else v <= num
        c = c.casefold()
        return c == want if fop == "equals" else c.startswith(want) if fop == "starts" else want in c

    def row(cells: List[str]) -> bool:
        if fcol < 0:
            return any(test(c) for c in cells)
        return test(cells[fcol] if fcol < len(cells) else "")
    return row


def _run_cost(cells: List[str]) -> int:
    return 120 + 56 * len(cells) + sum(len(c) for c in cells)


def _load_run(fh: Any) -> Iterator[Any]:
    fh.seek(0)
    while True:
        try:
            yield pickle.load(fh)
        except EOFError:
            return


def make_view(table: Table, vid: str, fcol: int = -1, fop: str = "", fval: str = "",
              scol: Optional[int] = None, sdir: int = 1) -> Dict[str, Any]:
    """A FILTERED and/or SORTED view of the WHOLE file, built as a job: every row is read once
    (progress, Cancel), matches are kept, and a sort is EXTERNAL — sorted runs of RUN_BYTES spilled
    to anonymous temp files, then merged — so memory is the run budget, not the file. The result
    is spooled to disk and paged (`get_diff(id).page`), each entry {"n": file row, "c": cells,
    "f": flags}. It is complete or it does not exist: a cancelled build keeps nothing."""
    table.head()
    width = table.width
    stats = {"not_numeric": 0}
    keep = _predicate(fcol, fop, fval, stats) if fop else None
    desc = sdir < 0
    out = Diff(vid)
    _PENDING[vid] = out
    done = False
    runs: List[Any] = []
    total_mb = max(table.size >> 20, 1)
    try:
        matched = scanned = 0
        batch: List[Tuple[Any, ...]] = []
        cost = 0
        with ExitStack() as stack:
            recs = table.records()
            stack.callback(recs.close)
            next(recs, None)                                   # the header
            for n, (cells, start, bad, cut) in enumerate(recs):
                if n % 5000 == 0:
                    checkpoint()
                    report(done=start >> 20, total=total_mb, unit="MB",
                           note=f"{n:,} rows read · {matched:,} match")
                scanned += 1
                if keep is not None and not keep(cells):
                    continue
                matched += 1
                fl = [x for x, on in (("ragged", len(cells) != width), ("bad", bad), ("cut", cut)) if on]
                if scol is None:
                    out.add({"n": n, "c": cells, "f": fl})
                    continue
                batch.append((_sort_key(cells[scol] if scol < len(cells) else "", desc), n, cells, fl))
                cost += _run_cost(cells)
                if cost >= RUN_BYTES:
                    batch.sort()
                    f = tempfile.TemporaryFile("w+b")
                    runs.append(f)
                    for item in batch:
                        pickle.dump(item, f, protocol=pickle.HIGHEST_PROTOCOL)
                    batch, cost = [], 0
                    checkpoint()
        if scol is not None:
            batch.sort()
            streams = [_load_run(f) for f in runs] + [iter(batch)]
            for k, item in enumerate(heapq.merge(*streams)):
                if k % 5000 == 0:
                    checkpoint()
                    report(done=k, total=max(matched, 1), unit="rows", note="merging the sorted runs")
                out.add({"n": item[1], "c": item[2], "f": item[3]})
        summary: Dict[str, Any] = {
            "id": vid, "matched": matched, "scanned": scanned, "complete": True, "runs": len(runs),
            "not_numeric": stats["not_numeric"], "sorted_by": scol, "sort_dir": sdir if scol is not None else 0,
            "filter": ({"col": fcol, "op": fop, "value": fval} if fop else None),
            "memory": (f"sorting spills runs of about {RUN_BYTES >> 20} MiB to temp files and merges them"
                       if scol is not None else "a filter holds one row at a time; matches go to a temp file")}
        out.summary = summary
        _PENDING.pop(vid, None)
        _keep(out)
        done = True
        return summary
    finally:
        for f in runs:
            try:
                f.close()
            except OSError:
                pass
        if not done:
            half = _PENDING.pop(vid, None)
            if half is not None:
                half.close()


# ============================================================================ the two sides
def spool_revision(root: str, rev: str, rel: str, into: str) -> Optional[str]:
    """`git show rev:rel` streamed into a new file under `into` (a big blob is never held in
    memory; Cancel kills git). Its path, or None when the file does not exist at that revision."""
    fh = tempfile.NamedTemporaryFile(dir=into, delete=False, prefix="side-")
    try:
        try:
            p = subprocess.Popen(["git", "-C", root, "show", "--end-of-options", f"{rev}:{rel}"],
                                 stdout=fh, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 **hidden())
        except (FileNotFoundError, OSError):
            return None
        with kill_on_cancel(p):
            code = p.wait()
        checkpoint()
    finally:
        fh.close()                             # closed before anything reads or removes it
    return fh.name if code == 0 else None


def diff_files(root: str, rel: str, old_rel: str, base: str, head: str, key: str,
               did: str) -> Dict[str, Any]:
    """The table diff of `rel`: its content at `base` (default HEAD) against `head` (empty = the
    working tree). Both sides are read through contained, streamed paths."""
    with tempfile.TemporaryDirectory(prefix="mokata-tside-") as tmp:
        sides: List[Optional[Table]] = []
        for rev, name in ((base or "HEAD", old_rel or rel), (head, rel)):
            if rev:
                p = spool_revision(root, rev, name, tmp)
                sides.append(Table(p, name) if p else None)
            else:
                from ..repo_paths import path_of
                try:
                    sides.append(open_table(path_of(root, name), name, contained_opener(root, name)))
                except NotATable as exc:
                    if exc.state != "absent":
                        raise
                    sides.append(None)
        return diff_tables(sides[0], sides[1], key, did)
