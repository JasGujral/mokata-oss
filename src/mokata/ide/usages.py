"""0.0.27 P4 `ID-COPY-CONTEXT` — Copy with Context (its ref shape, ACs + blast radius) and Find Usages.

The rule is `reports/P4.rule.md` (rev 2, ruled GO with R2-1..R2-6). In short:

  * ONE resolver (`canon_path`, `definition_at`, `definitions_in`, `parse_ref`) for all three: a
    repo-relative name with the on-disk spelling, the viewer's line numbering (`textlines`), the
    innermost definition and its dotted `qual` (emitted only when every enclosing span is known —
    never guessed), and per backend the TARGET it is asked by: a language server a position, the
    adopted code graph its own node id (`graph_id`), the floor the bare name (said, with how many
    definitions share it).
  * Copy Ref is one line: `<P>:<lo>[-<hi>][ <P>#<qual>]`, a token bare when it is plain and a JSON
    string literal otherwise (so `:`/`#`/space/backtick/control characters round-trip exactly).
  * Long work is a JOB (`usages.find`, `copy.context`): no wall clock (the full blast radius is
    `budget=full`, untimed — Cancel is its time bound), progress that never invents a total, and
    the answer kept in an owner-only SPILL file under the IDE's own state dir (`.mokata/temp_local/
    ide/spill` in a governed repo, the user's profile otherwise). Pages are read from the spill by
    a stable `(outside, path, line, col)` cursor; nothing is held whole in memory.
  * Nothing is copied that the human was not shown the size and cut state of first; a cancelled
    or failed job leaves nothing behind (its spill is deleted, handles closed first).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import bisect
import codecs
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..errors import MokataError
from ..textlines import viewer_lines
from . import nav

# ------------------------------------------------------------------------------------ the words
#: rows per page the page may ask for at most (the viewport asks for fewer); a page size, not a cap
PAGE_MAX = 500
#: one index entry per this many spilled rows (memory is rows / INDEX_EVERY, never the rows)
INDEX_EVERY = 256
#: bytes of copied text one `text` request carries (the page concatenates; transport, not a cap)
TEXT_CHUNK = 1 << 20
#: lines scanned for secrets at once (the redaction is per line; the re-scan overlaps chunks)
SCAN_CHUNK_LINES = 2000
SPILL_DIRNAME = "spill"
SPILL_PREFIX = "p4-"
SENSITIVE = "‹sensitive path›"
GRAPH_REFS_LIMIT = ("structural (code-review-graph): calls, imports and inheritance only — a name "
                    "used as a value, an annotation, a string or a decorator is not included")


class RefRefused(MokataError, ValueError):
    """A ref, id or path from the page that does not name a line/definition in the repository —
    or a copy that cannot be made truthfully (a sensitive file, a secret left after redaction).
    A HARD refusal: answered 400 with the reason; nothing is copied."""


class Expired(MokataError, LookupError):
    """A result the page asks for is gone (replaced by a newer one from the same page, discarded,
    or the IDE restarted). Answered 410 `expired, re-run` — never another result's rows."""


# ------------------------------------------------------------------------------------ tokens
_BARE = re.compile(r"[A-Za-z0-9._/+@-]+\Z")
_LINES = re.compile(r":(\d+)(?:-(\d+))?")


def tok(s: str) -> str:
    """A Copy Ref token: bare when it is plain, else a JSON string literal (exact round trip)."""
    return s if _BARE.match(s) else json.dumps(s, ensure_ascii=True)


def _read_tok(text: str, i: int) -> Tuple[str, int]:
    if i < len(text) and text[i] == '"':
        try:
            val, j = json.JSONDecoder().raw_decode(text, i)
        except ValueError:
            raise RefRefused("a quoted token does not decode") from None
        if not isinstance(val, str):
            raise RefRefused("a quoted token must be a string")
        return val, j
    m = re.compile(r"[A-Za-z0-9._/+@-]+").match(text, i)
    if not m:
        raise RefRefused("expected a path or symbol token")
    return m.group(0), m.end()


@dataclass
class Ref:
    path: str
    lo: Optional[int] = None
    hi: Optional[int] = None
    qual: Optional[str] = None


def parse_ref(text: str) -> Ref:
    """`<P>[:<lo>[-<hi>]][ <P>#<qual>]` or `<P>#<qual>` — what Copy Ref produced, read back."""
    s = (text or "").strip()
    if not s:
        raise RefRefused("an empty ref")
    path, i = _read_tok(s, 0)
    ref = Ref(path)
    if i < len(s) and s[i] == "#":
        ref.qual, i = _read_tok(s, i + 1)
    else:
        m = _LINES.match(s, i)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            ref.lo, ref.hi = min(a, b), max(a, b)
            i = m.end()
        if i < len(s) and s[i] == " ":
            p2, j = _read_tok(s, i + 1)
            if p2 != path or j >= len(s) or s[j] != "#":
                raise RefRefused("the symbol id must name the same file")
            ref.qual, i = _read_tok(s, j + 1)
    if i != len(s):
        raise RefRefused(f"unexpected text after the ref at character {i + 1}")
    return ref


# ------------------------------------------------------------------------------- the identity
def canon_path(root: str, path: str) -> str:
    """A repo-relative '/' name with the ON-DISK spelling of every component: `a/./b`, `a//b`, a
    `\\` separator on Windows (on POSIX `\\` is a legal name character, never a separator) and a
    case flip on a case-insensitive file system all give one name. A name with no such file is
    refused (no guess)."""
    if os.sep == "/" and isinstance(path, str) and "\\" in path:
        raise RefRefused("a backslash in a file name is not supported here: on this system it is "
                         "a name character, not a separator")
    rel = nav.contained_rel(root, path)
    cur, out = root, []
    for part in rel.split("/"):
        try:
            names = os.listdir(cur)
        except OSError:
            raise RefRefused(f"no file {rel} in the repository") from None
        if part not in names:
            folded = [n for n in names if n.casefold() == part.casefold()]
            # only a case-INSENSITIVE file system resolves the asked spelling to the entry
            if len(folded) != 1 or not os.path.exists(os.path.join(cur, part)):
                raise RefRefused(f"no file {rel} in the repository")
            part = folded[0]
        out.append(part)
        cur = os.path.join(cur, part)
    return "/".join(out)


@dataclass
class Definition:
    path: str
    line: int
    end: Optional[int]
    name: str
    kind: str
    qual: Optional[str]           # dotted chain of enclosing outline names; None = not certain
    col: int = 1                  # the name's column on its definition line (1-based)
    graph_id: Optional[str] = None
    graph_why: str = ""           # why no graph node is used (when graph_id is None)

    def ident(self) -> str:
        return f"{tok(self.path)}#{tok(self.qual)}" if self.qual else ""

    def facts(self) -> Dict[str, Any]:
        return {"path": self.path, "line": self.line, "end": self.end, "name": self.name,
                "kind": self.kind, "qual": self.qual, "id": self.ident() or None,
                "col": self.col, "graph": bool(self.graph_id), "graph_why": self.graph_why}


def file_text(root: str, rel: str) -> Optional[str]:
    from . import diffs as D
    return D._read_text(root, rel)


def _outline(root: str, rel: str, text: Optional[str]) -> Dict[str, Any]:
    return nav.outline(rel, text, root)


def _chain(symbols: Sequence[Dict[str, Any]], line: int) -> Tuple[List[Dict[str, Any]], bool]:
    """(the symbols whose span holds `line`, outer first; whether that chain is CERTAIN — every
    symbol starting at or before the line has a known end)."""
    certain = True
    held = []
    for s in symbols:
        if s["line"] > line:
            continue
        end = s.get("end")
        if end is None:
            certain = False
            continue
        if line <= end:
            held.append(s)
    held.sort(key=lambda s: (s["line"], -(s.get("end") or 0), s.get("depth", 0)))
    return held, certain


def _name_col(text_line: str, name: str) -> int:
    m = re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", text_line or "")
    return m.start() + 1 if m else 1


def _definition(rel: str, sym: Dict[str, Any], chain: List[Dict[str, Any]], certain: bool,
                lines: Optional[List[str]]) -> Definition:
    qual = ".".join(s["name"] for s in chain) if certain and chain and chain[-1] is sym else None
    if qual is not None and not all(re.fullmatch(r"[\w$]+", s["name"] or "") for s in chain):
        qual = None                        # a name the dotted grammar cannot carry: no id, no guess
    row = lines[sym["line"] - 1] if lines and 0 < sym["line"] <= len(lines) else ""
    return Definition(rel, sym["line"], sym.get("end"), sym["name"], sym.get("kind", "symbol"),
                      qual, _name_col(row, sym["name"]))


def definition_at(root: str, rel: str, line: int,
                  text: Optional[str] = None) -> Tuple[Optional[Definition], Dict[str, Any]]:
    """(the innermost definition holding `line`, the outline it came from)."""
    text = file_text(root, rel) if text is None else text
    ol = _outline(root, rel, text)
    syms = ol.get("symbols") or []
    chain, certain = _chain(syms, line)
    lines = viewer_lines(text) if text is not None else None
    if chain:
        return _definition(rel, chain[-1], chain, certain, lines), ol
    if not certain:
        s = nav.symbol_at(syms, line)
        if s is not None:
            return _definition(rel, s, [s], False, lines), ol
    return None, ol


def definitions_in(root: str, rel: str, lo: int, hi: int,
                   text: Optional[str] = None) -> Tuple[List[Definition], List[str], Dict[str, Any]]:
    """(the INNERMOST definitions the lines lo..hi touch, the names of enclosing definitions that
    are NOT included, the outline). A definition that strictly encloses another touched one is
    dropped (a line in `Invoice.refund` asks for `refund`, not all of `Invoice`)."""
    text = file_text(root, rel) if text is None else text
    ol = _outline(root, rel, text)
    syms = ol.get("symbols") or []
    lines = viewer_lines(text) if text is not None else None
    touched = [s for s in syms if s.get("end") is not None and s["line"] <= hi and s["end"] >= lo]
    if not touched:
        d, _ = definition_at(root, rel, lo, text)
        return ([d] if d else []), [], ol

    def encloses(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return a is not b and a["line"] <= b["line"] and a["end"] >= b["end"] and \
            (a["line"], -a["end"]) < (b["line"], -b["end"])
    inner = [s for s in touched if not any(encloses(s, t) for t in touched)]
    dropped = sorted({s["name"] for s in touched if s not in inner})
    out = []
    for s in sorted(inner, key=lambda s: s["line"]):
        chain, certain = _chain(syms, s["line"])
        chain = [c for c in chain if c["line"] <= s["line"] and c["end"] >= (s["end"] or 0)]
        out.append(_definition(rel, s, chain, certain, lines))
    return out, dropped, ol


def resolve_qual(root: str, rel: str, qual: str) -> Definition:
    """`<P>#<qual>` back to its definition — exactly, or refused (never the nearest guess)."""
    text = file_text(root, rel)
    ol = _outline(root, rel, text)
    lines = viewer_lines(text) if text is not None else None
    syms = ol.get("symbols") or []
    hits = []
    for s in syms:
        chain, certain = _chain(syms, s["line"])
        chain = [c for c in chain if c.get("end") is not None and s.get("end") is not None
                 and c["line"] <= s["line"] and c["end"] >= s["end"]]
        if certain and chain and chain[-1] is s and ".".join(c["name"] for c in chain) == qual:
            hits.append(_definition(rel, s, chain, certain, lines))
    if len(hits) != 1:
        raise RefRefused(f"{rel} has {'no' if not hits else len(hits)} definitions with the id "
                         f"`{qual}` — it was renamed, deleted or is defined twice")
    return hits[0]


def copy_ref(root: str, path: str, lo: int, hi: Optional[int] = None) -> Dict[str, Any]:
    """The Copy Ref line for lines lo..hi (1-based, the viewer's numbering; hi < lo is swapped)."""
    rel = canon_path(root, path)
    hi = lo if hi is None else hi
    lo, hi = min(lo, hi), max(lo, hi)
    text = file_text(root, rel)
    if text is None:
        raise RefRefused(f"{rel} is not a readable text file")
    n = len(viewer_lines(text))
    if n == 0:
        return {"ref": tok(rel), "path": rel, "lines": None, "symbol": None,
                "note": "an empty file has no line"}
    if lo < 1 or hi > n:
        raise RefRefused(f"{rel} has {n} lines; lines {lo}-{hi} are not in it")
    loc = f"{tok(rel)}:{lo}" + (f"-{hi}" if hi != lo else "")
    d, ol = definition_at(root, rel, lo, text)
    twice = False
    if d is not None and d.qual:
        try:
            resolve_qual(root, rel, d.qual)
        except RefRefused:
            twice = True                     # an id that does not resolve back is not emitted
    ref = loc + (f" {d.ident()}" if d and d.qual and not twice and (hi <= (d.end or 0)) else "")
    note = ""
    if twice:
        note = f"no symbol id — `{d.qual}` is defined more than once in this file"
    elif d is not None and not d.qual:
        note = "no symbol id — the enclosing definitions' extent is not certain here"
    elif d is not None and hi > (d.end or 0):
        note = "no symbol id — the selection runs past the definition it starts in"
    return {"ref": ref, "path": rel, "lines": [lo, hi], "symbol": d.facts() if d else None,
            "note": note, "outline_note": ol.get("note", "")}


# ------------------------------------------------------------------------------ the graph node
def graph_node(root: str, d: Definition) -> None:
    """Set `d.graph_id` to the adopted code graph's OWN node id for this definition (two steps:
    the graph's outline of the file, then the node of that name at that line), or say why not:
    no adopted graph, no index, an index older than the file (lines may have moved — the floor
    answers), or no node there."""
    try:
        layer = nav._layer(root)
    except MokataError as exc:
        d.graph_why = f"no graph layer ({type(exc).__name__})"
        return
    primary = getattr(layer, "primary", None)
    if primary is None or not getattr(primary, "is_graph", False):
        d.graph_why = "no code graph is adopted"
        return
    from ..knowledge import crg_nodes as N
    import sqlite3
    reader = N.reader(root)
    if reader is None:
        d.graph_why = "the code graph has no built index for this repository"
        return
    try:
        got = reader.outline(d.path)
        if got is None:
            d.graph_why = "the code graph does not index this file"
            return
        with open(os.path.join(root, *d.path.split("/")), "rb") as fh:
            digest = N.file_digest(fh.read())
        if got.get("file_hash") and got["file_hash"] != digest:
            d.graph_why = ("the code graph indexed an older version of this file (lines may have "
                           "moved) — not trusted for this node")
            return
        rows = [r for r in reader.defs(d.name) if r["path"] == d.path and r["line"] == d.line]
    except (sqlite3.Error, OSError, ValueError):
        d.graph_why = "the code graph's index could not be read"
        return
    if len(rows) != 1:
        d.graph_why = "the code graph holds no single node at this definition"
        return
    d.graph_id = rows[0]["graph_id"]


# ------------------------------------------------------------------------------------ progress
def say(done: int, unit: str, note: str, total: int = 0) -> None:
    """Progress into the running job. `total=0` means INDETERMINATE (the page shows the count,
    never n/N) — and resets any total an earlier step left behind."""
    from ..knowledge.cancel import report
    report(done=done, total=total, unit=unit, note=note)


# ------------------------------------------------------------------------------------ the spill
def spill_dir(root: str) -> str:
    from .statedir import ide_dir
    return os.path.join(ide_dir(root), SPILL_DIRNAME)


class SpillFull(MokataError, OSError):
    """The spill could not be written (disk full, no permission). The job FAILS with this — a
    result is complete or absent, never cut at the byte the disk ran out."""


class SpillWriter:
    """An owner-only file in the spill dir, written once, then only read. Rows are JSON arrays,
    one per line, in key order; every `INDEX_EVERY`-th row's (key, offset) is indexed."""

    def __init__(self, directory: str, kind: str) -> None:
        try:
            fresh = not os.path.isdir(directory)
            os.makedirs(directory, mode=0o700, exist_ok=True)
            fd, self.path = tempfile.mkstemp(prefix=f"{SPILL_PREFIX}{os.getpid()}-{kind}-",
                                             suffix=".spill", dir=directory)
        except OSError as exc:
            raise SpillFull(f"no spill file could be made in {directory}: {exc}") from None
        self.fh = os.fdopen(fd, "wb")
        self.rows = 0
        self.bytes = 0
        self.index: List[Tuple[Any, int]] = []
        try:
            from ..winacl import owner_only          # Windows ignores 0o600: set the DACL itself
            if fresh:                  # once, when WE made the folder: re-applying a directory ACL
                owner_only(directory)  # walks every child (O(spills)), and its entry is inherited
            owner_only(self.path)
        except OSError as exc:
            self.discard()
            raise SpillFull(f"the spill file could not be made owner-only: {exc}") from None

    def row(self, key: Any, row: List[Any]) -> None:
        if self.rows % INDEX_EVERY == 0:
            self.index.append((key, self.bytes))
        self.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.rows += 1

    def write(self, text: str) -> None:
        data = text.encode("utf-8")
        try:
            self.fh.write(data)
        except OSError as exc:
            raise SpillFull(f"the spill file could not be written: {exc}") from None
        self.bytes += len(data)

    def close(self) -> None:
        if not self.fh.closed:
            try:
                self.fh.close()
            except OSError as exc:
                raise SpillFull(f"the spill file could not be finished: {exc}") from None

    def discard(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass                                    # closing a broken handle: removal follows
        _unlink(self.path)


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass                                        # swept at the next start (dead pid)


def sweep(directory: str) -> int:
    """Remove spill files a DEAD IDE process left (a crash): named `p4-<pid>-…`. Returns how many."""
    from ..session_registry import pid_alive
    n = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith(SPILL_PREFIX) and name.endswith(".spill")):
            continue
        try:
            pid = int(name[len(SPILL_PREFIX):].split("-", 1)[0])
        except ValueError:
            continue
        if pid != os.getpid() and not pid_alive(pid):
            _unlink(os.path.join(directory, name))
            n += 1
    return n


#: The backstop bounds of the live results (V4). A page releases its results when it leaves
#: (`pagehide`, or its event stream gone for `GONE_GRACE_S`); these bound what is left when that
#: never arrives. Past either bound the LEAST RECENTLY USED result goes and answers `Expired`.
STORE_MAX_RESULTS = 32
STORE_MAX_BYTES = 1 << 30
GONE_GRACE_S = 30.0
EXPIRED = "expired, re-run — this result was replaced, discarded, released or the IDE restarted"


@dataclass
class Result:
    id: str
    feature: str                                             # "usages" | "copy"
    head: Dict[str, Any]
    files: Dict[str, str] = field(default_factory=dict)      # name -> spill path
    index: Dict[str, List[Tuple[Any, int]]] = field(default_factory=dict)
    rows: Dict[str, int] = field(default_factory=dict)
    sizes: Dict[str, int] = field(default_factory=dict)
    made: float = field(default_factory=time.time)
    job: str = ""                                            # the job that answered it
    owners: set = field(default_factory=set)                 # client ids whose CURRENT result it is
    used: float = field(default_factory=time.monotonic)

    def nbytes(self) -> int:
        return sum(self.sizes.values()) if self.sizes else 0


class Store:
    """The live results, OWNED PER ASKING PAGE (0.0.27 P4 round 2, V2).

    One job can answer several pages (aliased or concurrent asks of one definition join it). Each
    page that asked becomes an owner of the result when IT fetches the answer (`claim`), so one
    page's next search never expires another page's result. A page's slot per feature holds the
    result of its NEWEST request: every request takes a sequence number when it is ASKED (`ask`),
    and an answer that finishes late — an older request — never replaces a newer one. A result
    nobody owns and nobody still waits for is dropped (its spill deleted) at once.

    Everything goes at IDE stop; a page's results when the page leaves (`release_client`); a
    crash's leftovers at the next start (`sweep`); and the LRU past `STORE_MAX_RESULTS` /
    `STORE_MAX_BYTES` (the backstop for a page that left without saying so)."""

    def __init__(self, directory: str) -> None:
        self.dir = directory
        self._lock = threading.Lock()
        self._by_id: Dict[str, Result] = {}
        self._slot: Dict[Tuple[str, str], Tuple[str, int]] = {}   # (client, feature) -> (rid, seq)
        self._seq = 0
        self._askers: Dict[str, Dict[str, int]] = {}               # job id -> {client: seq}
        self._timers: Dict[str, threading.Timer] = {}
        sweep(directory)

    # -- asking ---------------------------------------------------------------------------------
    def ask(self) -> int:
        """The sequence number of a request, taken when it is ASKED (not when it finishes)."""
        with self._lock:
            self._seq += 1
            return self._seq

    def asked(self, job: str, client: str, seq: int) -> None:
        """`client` asked (seq) the question the job `job` answers — every page that joined one job
        is recorded against it. Recorded BEFORE the job may store its answer (the route sets it
        before releasing the work's `put`), so a fast answer is never dropped as unwanted."""
        with self._lock:
            got = self._askers.setdefault(job, {})
            got[client] = max(seq, got.get(client, 0))

    # -- results --------------------------------------------------------------------------------
    def put(self, res: Result) -> None:
        """A finished answer. Nobody owns it yet; the pages that asked claim it."""
        drop: List[Result] = []
        with self._lock:
            self._by_id[res.id] = res
            if not self._askers.get(res.job) and not res.owners:
                drop.append(self._by_id.pop(res.id))           # everyone who asked has left
            drop += self._evict(keep=res.id)
        for r in drop:
            _drop_files(r)

    def claim(self, rid: str, client: str, feature: str) -> bool:
        """`client` fetched the answer `rid`: it becomes that page's result for `feature` unless
        the page has since asked something newer. False = superseded (the answer is still returned
        to the page, which ignores it; it is not the page's result)."""
        drop: List[Result] = []
        try:
            with self._lock:
                res = self._by_id.get(rid)
                if res is None:
                    raise Expired(EXPIRED)
                waiting = self._askers.get(res.job, {})
                seq = waiting.pop(client, None)
                if seq is None:
                    self._seq += 1
                    seq = self._seq
                if not waiting:
                    self._askers.pop(res.job, None)
                key = (client, feature)
                cur = self._slot.get(key)
                if cur is not None and cur[0] != rid and cur[1] > seq:
                    if not res.owners and res.job not in self._askers:
                        drop.append(self._by_id.pop(rid))
                    return False
                if cur is not None and cur[0] != rid:
                    drop += self._unown(cur[0], client)
                self._slot[key] = (rid, seq)
                res.owners.add(client)
                res.used = time.monotonic()
                return True
        finally:
            for r in drop:
                _drop_files(r)

    def get(self, rid: str) -> Result:
        with self._lock:
            res = self._by_id.get(rid)
            if res is not None:
                res.used = time.monotonic()
        if res is None:
            raise Expired(EXPIRED)
        return res

    def discard(self, rid: str, client: Optional[str] = None) -> bool:
        """`client` lets go of `rid` (None: everyone does). Its spill goes when nobody owns it."""
        drop: List[Result] = []
        with self._lock:
            res = self._by_id.get(rid)
            if res is None:
                return False
            for c in ([client] if client is not None else list(res.owners)):
                drop += self._unown(rid, c)
            if client is None and rid in self._by_id:
                drop.append(self._by_id.pop(rid))
                self._askers.pop(res.job, None)
        for r in drop:
            _drop_files(r)
        return True

    def release_client(self, client: str) -> int:
        """The page `client` left: it owns nothing and waits for nothing any more."""
        drop: List[Result] = []
        with self._lock:
            t = self._timers.pop(client, None)
            if t is not None:
                t.cancel()
            for key in [k for k in self._slot if k[0] == client]:
                rid, _seq = self._slot[key]
                drop += self._unown(rid, client)
            for job in list(self._askers):
                self._askers[job].pop(client, None)
                if not self._askers[job]:
                    del self._askers[job]
                    for res in [r for r in self._by_id.values() if r.job == job]:
                        if not res.owners:
                            drop.append(self._by_id.pop(res.id))
        for r in drop:
            _drop_files(r)
        return len(drop)

    def client_gone(self, client: str, grace: Optional[float] = None) -> None:
        """The page's event stream closed. An EventSource reconnects by itself, so its results are
        released only if it is still gone after `GONE_GRACE_S` (`client_back` cancels that)."""
        t = threading.Timer(GONE_GRACE_S if grace is None else grace,
                            self.release_client, args=(client,))
        t.daemon = True
        with self._lock:
            old = self._timers.pop(client, None)
            self._timers[client] = t
        if old is not None:
            old.cancel()
        t.start()

    def client_back(self, client: str) -> None:
        with self._lock:
            t = self._timers.pop(client, None)
        if t is not None:
            t.cancel()

    def close(self) -> None:
        with self._lock:
            all_ = list(self._by_id.values())
            self._by_id.clear()
            self._slot.clear()
            self._askers.clear()
            timers = list(self._timers.values())
            self._timers.clear()
        for t in timers:
            t.cancel()
        for r in all_:
            _drop_files(r)

    # -- under the lock -------------------------------------------------------------------------
    def _unown(self, rid: str, client: str) -> List[Result]:
        for key in [k for k, v in self._slot.items() if k[0] == client and v[0] == rid]:
            del self._slot[key]
        res = self._by_id.get(rid)
        if res is None:
            return []
        res.owners.discard(client)
        if not res.owners and res.job not in self._askers:
            return [self._by_id.pop(rid)]
        return []

    def _evict(self, keep: str) -> List[Result]:
        out: List[Result] = []
        live = sorted((r for r in self._by_id.values() if r.id != keep), key=lambda r: r.used)
        total = sum(r.nbytes() for r in self._by_id.values())
        while live and (len(self._by_id) > STORE_MAX_RESULTS or total > STORE_MAX_BYTES):
            r = live.pop(0)
            self._by_id.pop(r.id, None)
            self._askers.pop(r.job, None)
            for key in [k for k, v in self._slot.items() if v[0] == r.id]:
                del self._slot[key]
            total -= r.nbytes()
            out.append(r)
        return out


def _drop_files(res: Result) -> None:
    for p in res.files.values():
        _unlink(p)


def _key(row: List[Any]) -> Tuple[int, str, int, int]:
    return (int(row[6]), str(row[0]), int(row[1]), int(row[2]))


def page(res: Result, name: str, after: Optional[Sequence[Any]], limit: int) -> Dict[str, Any]:
    """`limit` rows after the cursor `after` (None = from the start), read from the spill: the
    rows, the ordinal of the first, and the next cursor (None at the end)."""
    path = res.files.get(name)
    if path is None:
        raise Expired(EXPIRED)
    limit = max(1, min(PAGE_MAX, int(limit)))
    idx = res.index.get(name) or []
    keys = [tuple(k) for k, _ in idx]
    start_i = 0
    want = None
    if after is not None:
        want = (int(after[0]), str(after[1]), int(after[2]), int(after[3]))
        start_i = max(0, bisect.bisect_right(keys, want) - 1)
    offset = idx[start_i][1] if idx else 0
    ordinal = start_i * INDEX_EVERY
    rows: List[List[Any]] = []
    nxt = None
    try:
        fh = open(path, "rb")
    except OSError:
        raise Expired("expired, re-run — the result's file is gone") from None
    with fh:
        fh.seek(offset)
        for raw in fh:
            row = json.loads(raw)
            if want is not None and _key(row) <= want:
                ordinal += 1
                continue
            if len(rows) == limit:
                nxt = list(_key(rows[-1]))
                break
            rows.append(row)
    first = ordinal + 1 if rows else None
    return {"rows": rows, "first": first, "next": nxt, "total": res.rows.get(name, 0)}


# ------------------------------------------------------------------------------ snippets
def _snip(raw_first: bytes, name: str) -> Tuple[str, int]:
    from ..knowledge.grep_backend import _clip
    t = codecs.getincrementaldecoder("utf-8")("replace").decode(raw_first, final=True)
    t = t[:-1] if t.endswith("\n") else t
    t = t[:-1] if t.endswith("\r") else t
    m = re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", t) if name else None
    if m:
        return _clip(t, m.start(), m.end(), cut=not raw_first.endswith(b"\n") and
                     len(raw_first) >= _block()), m.start() + 1
    return _clip(t, 0, 0, cut=len(t) > 240), 1


def _block() -> int:
    from ..knowledge.grep_backend import REFS_BLOCK
    return REFS_BLOCK


def fill_snippets(code_root: str, rows: List[List[Any]], name: str) -> None:
    """Snippets for rows a graph or a server returned without one: grouped by file, each file read
    ONCE, in path order, block by block (a huge line is never read whole)."""
    from ..knowledge.cancel import checkpoint
    by_file: Dict[str, List[List[Any]]] = {}
    for r in rows:
        if not r[6]:
            by_file.setdefault(r[0], []).append(r)
    blk = _block()
    for n, (rel, rs) in enumerate(sorted(by_file.items())):
        checkpoint()
        want: Dict[int, List[List[Any]]] = {}
        for r in rs:
            want.setdefault(int(r[1]), []).append(r)
        last = max(want)
        try:
            fh = open(os.path.join(code_root, *rel.split("/")), "rb")
        except OSError:
            continue
        with fh:
            ln = 0
            while ln < last:
                chunk = fh.readline(blk)
                if not chunk:
                    break
                ln += 1
                hit = want.get(ln)
                if hit:
                    text, col = _snip(chunk, name)
                    for r in hit:
                        r[4] = text
                        if not r[2] or r[2] == 1:
                            r[2] = col
                while chunk and not chunk.endswith(b"\n") and len(chunk) >= blk:
                    checkpoint()
                    chunk = fh.readline(blk)


# ------------------------------------------------------------------------------- find usages
def _row(path: str, line: int, col: int, symbol: Optional[str], snippet: str,
         outside: bool) -> List[Any]:
    return [path, int(line), int(col or 1), symbol or "", snippet or "",
            0 if outside else int(nav._is_test(path)), int(outside)]


def _write_rows(w: SpillWriter, rows: Iterable[List[Any]]) -> Dict[str, int]:
    """Write rows (already in key order) and count files/tests/code/outside."""
    from ..knowledge.cancel import checkpoint
    files, tests, outside = set(), 0, 0
    for i, r in enumerate(rows):
        if i % 4096 == 0:
            checkpoint()
        w.row(list(_key(r)), r)
        if r[6]:
            outside += 1
        else:
            files.add(r[0])
            tests += r[5]
    return {"files": len(files), "tests": tests, "code": w.rows - tests - outside,
            "outside": outside}


def find_usages(root: str, code_root: str, d: Definition, w: SpillWriter,
                lsp_host: Any = None, lexical: bool = False) -> Dict[str, Any]:
    """Every usage of definition `d`, written to `w` in (outside, path, line, col) order; the head
    of the answer. Backend order: a trusted, running, indexed language server (by position); else
    the adopted graph by its own node id; else the lexical floor by the bare name (said)."""
    from ..knowledge.cancel import checkpoint
    head: Dict[str, Any] = {"symbol": d.facts(), "lsp": None, "truncated": False,
                            "total_known": True}
    if lexical:                         # asked for: the word scan, over every source file
        head["graph_note"] = "the lexical scan was asked for"
        return _floor_usages(code_root, d, w, head)
    if lsp_host is not None:
        got = _lsp_usages(root, code_root, d, lsp_host)
        if "rows" in got:
            rows = sorted(got.pop("rows"), key=_key)
            head.update(got)
            head.update(_write_rows(w, rows))
            return head
        head["lsp"] = got.get("lsp")
    checkpoint()
    graph_node(code_root, d)
    if d.graph_id:
        say(0, "graph questions", f"usages of {d.name}: asking the code graph for its node")
        res = nav.run(code_root, "refs", d.graph_id)
        layer = nav._layer(code_root)
        if res.structural and res.backend == getattr(layer.primary, "name", None):
            rows = []
            for r in res.references:
                rows.append(_row(str(r.path), r.line, 1, r.symbol, r.snippet or "", False))
            rows = _dedupe(rows)
            fill_snippets(code_root, rows, d.name)
            rows.sort(key=_key)
            head.update({"backend": res.backend, "basis": res.basis, "label": GRAPH_REFS_LIMIT,
                         "asked_by": "node", "truncated": bool(res.truncated),
                         "total_known": res.total is not None or not res.truncated,
                         "graph_total": res.total, "definitions": None, "candidates": []})
            head.update(_write_rows(w, rows))
            return head
        head["graph_note"] = "the code graph did not answer for this node; the floor answers"
    else:
        head["graph_note"] = d.graph_why
    return _floor_usages(code_root, d, w, head)


def _dedupe(rows: List[List[Any]]) -> List[List[Any]]:
    seen, out = set(), []
    for r in rows:
        k = (r[6], r[0], r[1], r[2])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _floor_usages(code_root: str, d: Definition, w: SpillWriter,
                  head: Dict[str, Any]) -> Dict[str, Any]:
    from ..knowledge.grep_backend import GrepBackend, RefsScope
    scope = RefsScope()
    g = GrepBackend(code_root)
    defs: List[Dict[str, Any]] = []

    def rows() -> Iterator[List[Any]]:
        for r in g.iter_refs(d.name, scope):
            if r.metadata.get("defines"):
                defs.append({"path": str(r.path), "line": r.line})
                if str(r.path) == d.path and r.line == d.line:
                    continue                    # the definition itself is not a usage
            yield _row(str(r.path), r.line, r.metadata.get("col", 1), r.symbol, r.snippet, False)
    counts = _write_rows(w, rows())
    head.update(counts)
    head.update({"backend": "grep", "basis": "lexical", "label": scope.line(),
                 "asked_by": "name", "definitions": len(defs) if len(defs) > 1 else None,
                 "candidates": defs if len(defs) > 1 else [],
                 "scope": {"listed": scope.listed, "scanned": scope.scanned,
                           "unreadable": scope.unreadable, "pruned_dirs": scope.pruned_dirs,
                           "other_files": scope.other_files,
                           "extensions": list(scope.extensions)}})
    if len(defs) > 1:
        head["ambiguity"] = (f"{len(defs)} definitions are named `{d.name}` — the lexical floor "
                             "cannot tell their usages apart; these are word matches for all of "
                             "them")
    return head


def _lsp_usages(root: str, code_root: str, d: Definition, host: Any) -> Dict[str, Any]:
    """The language server's references — only when trusted, mapped, running and indexed. Any
    other state answers `{"lsp": {state, note}}` and the next backend speaks."""
    from . import lsp as L
    from . import trust as T
    if not T.status(root)["trusted"]:
        return {"lsp": {"state": "untrusted", "note": "this workspace is not trusted"}}
    lang = L.language_of(d.path)
    if lang is None:
        return {"lsp": {"state": "unsupported", "note": "no language server is mapped to this "
                                                       "file type"}}
    srv, why = host.server_for(lang)
    if srv is None:
        return {"lsp": dict(why or {"state": "stopped"})}
    if srv.indexing():
        return {"lsp": {"state": "indexing", "server": srv.name,
                        "note": f"{srv.name} is still indexing"}}
    abspath = os.path.join(code_root, *d.path.split("/"))
    spec = L.languages()[lang]
    say(0, "", f"usages of {d.name}: asking {srv.name}")
    try:
        uri, text = srv.open(abspath, L.language_id(lang, d.path, spec))
        lines = viewer_lines(text)
        cur = lines[d.line - 1] if 0 < d.line <= len(lines) else ""
        pos = {"line": d.line - 1, "character": L.utf16_col(cur, d.col)}
        res = srv.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": pos,
            "context": {"includeDeclaration": False}})
    except L.ServerGone as exc:
        return {"lsp": {"state": "crashed", "server": srv.name, "note": str(exc)[:200]}}
    locs = res if isinstance(res, list) else []
    cache = L._Lines()
    rows = []
    for x in locs:
        loc = L.location(code_root, x, cache, snippet=False)
        if not loc["outside"] and loc["path"] == d.path and loc["line"] == d.line:
            continue                                       # the declaration is not a usage
        rows.append(_row(loc["path"] or "", loc["line"], loc["col"], "", "", loc["outside"]))
    rows = _dedupe(rows)
    fill_snippets(code_root, rows, d.name)
    return {"rows": rows, "backend": srv.name, "basis": "lsp",
            "label": f"as resolved by {srv.name} (its own project view; dynamic uses it cannot "
                     "type are not included); the declaration is excluded",
            "asked_by": "position", "definitions": None, "candidates": [],
            "lsp": {"state": "ready", "server": srv.name}}


# ------------------------------------------------------------------------------ staleness
def staleness(root: str, res: Result, rows: List[List[Any]]) -> Dict[str, Any]:
    """Is the symbol still where the list says, and which rows' files changed since?"""
    sym = res.head.get("symbol") or {}
    out: Dict[str, Any] = {"symbol": "same", "changed_rows": []}
    try:
        rel = sym.get("path") or ""
        if sym.get("qual"):
            now = resolve_qual(root, rel, sym["qual"])
            if now.line != sym.get("line"):
                out["symbol"] = "moved"
                out["note"] = f"`{sym['qual']}` moved to line {now.line} since this list was made"
        else:
            d, _ = definition_at(root, rel, int(sym.get("line") or 1))
            if d is None or d.name != sym.get("name"):
                raise RefRefused("gone")
    except (RefRefused, nav.PathRefused, OSError):
        out["symbol"] = "gone"
        out["note"] = (f"`{sym.get('qual') or sym.get('name')}` is no longer defined at "
                       f"{sym.get('path')}:{sym.get('line')} (renamed or deleted); this list is "
                       f"from {time.strftime('%H:%M:%S', time.localtime(res.made))}")
    seen: Dict[str, bool] = {}
    for i, r in enumerate(rows):
        if r[6]:
            continue
        p = r[0]
        if p not in seen:
            try:
                seen[p] = os.path.getmtime(os.path.join(root, *p.split("/"))) > res.made
            except OSError:
                seen[p] = True
        if seen[p]:
            out["changed_rows"].append(i)
    return out


# ------------------------------------------------------------------------- copy with context
def fence_for(text: str) -> str:
    runs = [len(m.group(0)) for m in re.finditer(r"`{3,}", text)]
    return "`" * max(3, (max(runs) + 1) if runs else 3)


def _clean(text: str, root: str) -> str:
    """A note mokata did not author in full: root and home relativised, control characters out."""
    out = text.replace(os.path.abspath(root), "<repo>").replace(os.path.expanduser("~"), "~")
    return re.sub(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩]", " ", out)


def read_lines(root: str, rel: str, lo: int, hi: int) -> str:
    """Lines lo..hi of a repo file, streamed (the viewer's numbering; each keeps its own EOL)."""
    from ..knowledge.cancel import checkpoint
    out: List[str] = []
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        ln = 1
        for raw in iter(lambda: fh.readline(_block()), b""):
            if ln > hi:
                break
            if ln >= lo:
                out.append(dec.decode(raw))
            if raw.endswith(b"\n"):
                ln += 1
                if ln % 4096 == 0:
                    checkpoint()
    return "".join(out)


def _head_sha(root: str) -> str:
    from . import diffs as D
    code, out = D._git(root, "rev-parse", "--short=12", "HEAD")
    if code != 0:
        return "no commit"
    sha = out.strip()
    code, st = D._git(root, "status", "--porcelain", "--untracked-files=no")
    return sha + ("+dirty" if code == 0 and st.strip() else "")


def _sensitive(path: str) -> bool:
    from ..govern.secrets import scan
    return any(f.layer == "path" for f in scan("", path=path))


def radius_for(code_root: str, d: Definition) -> Dict[str, Any]:
    """One definition's FULL blast radius (untimed; Cancel is the bound): asked by the graph's node
    id when there is a trusted node, else by the bare name (said, with how many share it)."""
    from ..knowledge.query import BUDGET_FULL
    graph_node(code_root, d)
    layer = nav._layer(code_root)
    pname = getattr(getattr(layer, "primary", None), "name", None)
    asked = "node" if d.graph_id else "name"
    say(0, "references found", f"blast radius of {d.qual or d.name}: starting")
    res = nav.run(code_root, "blast_radius", d.graph_id or d.name, depth=nav.IMPACT_DEPTH,
                  budget=BUDGET_FULL)
    note_extra = ""
    if d.graph_id and res.backend != pname:
        note_extra = "the code graph did not answer for this node; asked again by name"
        asked = "name"
        res = nav.run(code_root, "blast_radius", d.name, depth=nav.IMPACT_DEPTH,
                      budget=BUDGET_FULL)
    shared = None
    if asked == "name":
        if res.definitions:
            shared = int(res.definitions)
        else:
            defs = nav.run(code_root, "defs", d.name)
            shared = len({(str(r.path), r.line) for r in defs.references}) or None
    cut = ""
    if res.truncated:
        seg = [p.strip() for p in (res.note or "").split("|") if p.strip().startswith("truncated")]
        cut = seg[0] if seg else "truncated"
    return {"def": d, "result": res, "asked_by": asked, "shared": shared,
            "graph_why": d.graph_why if not d.graph_id else "", "note_extra": note_extra,
            "cut": cut}


def _radius_lines(code_root: str, rad: Dict[str, Any], shape: str,
                  counts: Dict[str, int]) -> Iterator[str]:
    d: Definition = rad["def"]
    res = rad["result"]
    refs = sorted({(str(r.path), r.line, r.symbol or "") for r in res.references})
    files: Dict[str, int] = {}
    tests: List[Tuple[str, int, str]] = []
    for p, ln, s in refs:
        files[p] = files.get(p, 0) + 1
        if nav._is_test(p):
            tests.append((p, ln, s))
    yield f"\n### {tok(d.qual or d.name)} — {tok(d.path)}:{d.line}\n\n"
    basis = res.basis
    yield (f"- answered by {_clean(str(res.backend or 'the floor'), code_root)} (basis "
           f"{_clean(str(basis), code_root)}"
           + (", lexical — approximate" if not res.structural else "") + "), depth "
           f"{nav.IMPACT_DEPTH}, full budget, asked by {rad['asked_by']}\n")
    if rad["asked_by"] == "name" and rad["shared"] and rad["shared"] > 1:
        yield (f"- {rad['shared']} definitions share the name {tok(d.name)} — this radius is merged "
               "over all of them\n")
    if rad["graph_why"]:
        yield f"- no graph node used: {_clean(rad['graph_why'], code_root)}\n"
    if rad["note_extra"]:
        yield f"- {rad['note_extra']}\n"
    total = f"{len(refs)}"
    if res.truncated:
        total += f" of {res.total if res.total is not None else 'unknown'}"
        yield f"- CUT: {_clean(rad['cut'], code_root)} — this is NOT the whole radius\n"
    yield f"- {total} locations in {len(files)} files; {len(tests)} in tests\n"
    verdict = ("covered" if tests else "untested" if res.structural and not res.truncated
               else "unknown (a lexical or cut radius proves no absence)")
    yield f"- tests reaching it: {verdict}\n"
    for p, ln, s in tests:
        yield _loc_line(p, ln, s, counts)
    if shape == "full":
        yield "- locations:\n"
        for p, ln, s in refs:
            if not nav._is_test(p):
                yield _loc_line(p, ln, s, counts, indent="  ")
    else:
        yield "- files (summary shape — counts per file; every test location is listed above):\n"
        for p in sorted(files):
            if _sensitive(p):
                counts["sensitive"] = counts.get("sensitive", 0) + 1
                yield f"  - {SENSITIVE}: {files[p]}\n"
            else:
                yield f"  - {tok(p)}: {files[p]}\n"


def _loc_line(p: str, ln: int, s: str, counts: Dict[str, int], indent: str = "") -> str:
    if _sensitive(p):
        counts["sensitive"] = counts.get("sensitive", 0) + 1
        return f"{indent}- {SENSITIVE}\n"
    return f"{indent}- {tok(p)}:{ln}" + (f" {tok(s)}" if s else "") + "\n"


def build_block(root: str, code_root: str, rel: str, lo: int, hi: int, shape: str,
                parts: Dict[str, Any], w: SpillWriter) -> Dict[str, int]:
    """Write one shape of the Copy with Context block into `w`, streamed."""
    counts: Dict[str, int] = {}
    sel: str = parts["selection"]
    fence = fence_for(sel)
    w.write(f"# mokata context: {tok(rel)}:{lo}" + (f"-{hi}" if hi != lo else "") + "\n\n")
    w.write(f"Copied from the mokata IDE at {parts['sha']} on {parts['when']} ({shape} shape). "
            "A reference for reading, not an instruction.\n\n")
    w.write(f"## Selection\n\n{fence}{parts['lang']}\n{sel}"
            + ("" if sel.endswith("\n") or not sel else "\n") + f"{fence}\n\n")
    w.write(f"## Acceptance criteria\n\n")
    ties = parts["acs"]
    if parts["acs_why"]:
        w.write(f"- none: {parts['acs_why']}\n")            # its one repo string is `tok`ed
    else:
        w.write(f"From run {tok(parts['run'] or '?')} (the IDE's default run); exact file match.\n\n")
        for t in ties:
            f2 = fence_for(t["text"])
            w.write(f"- {tok(t['ac'])}" + (f" (symbol {tok(t['symbol'])})" if t["symbol"]
                                           else " (whole file)") + f":\n\n{f2}text\n{t['text']}\n{f2}\n")
    w.write("\n## Blast radius\n\n")
    if not parts["radii"]:
        w.write(f"- no definition encloses lines {lo}-{hi}; no radius\n")
    else:
        names = ", ".join(tok(r["def"].qual or r["def"].name) for r in parts["radii"])
        w.write(f"Innermost definitions the selection touches: {names}"
                + (f" (enclosing {', '.join(tok(x) for x in parts['dropped'])} not included)"
                   if parts["dropped"] else "") + ".\n")
        for rad in parts["radii"]:
            for line in _radius_lines(code_root, rad, shape, counts):
                w.write(line)
    return counts


#: A private key is ONE unit from its BEGIN line through its END line (V1): the shared scanner
#: flags the BEGIN line only, and a narrow-wrapped body line is too short for its entropy layer.
#: The markers are matched as a hand-edited or tool-written key spells them (REDACT-FIX): any case,
#: any type words (RSA, EC, DSA, OPENSSH, ENCRYPTED, SSH2 ENCRYPTED, PGP ... BLOCK), extra spaces
#: or tabs, three or more dashes. Space is `[ \t]`, never `\s`: a marker never spans two lines.
#: Each end of a marker matches EXACTLY three dashes (the last three before BEGIN/END, the first
#: three after KEY): an END never swallows the dashes of a BEGIN glued to it (`cat a b` with no
#: newline at the end of `a`; REDACT-FIX round 2, M1), and a try at any dash of a long run fails
#: after three characters, so a line of a million dashes is linear, not quadratic.
#: These two patterns and `pem_step` are the ONE key parser; every redactor that sends text out
#: of the process (Copy with Context here, session context in `ide/context.py`) uses them.
_PEM = r"-{{3}}[ \t]*{word}[ \t]+(?:[A-Z0-9]+[ \t]+)*PRIVATE[ \t]+KEY(?:[ \t]+BLOCK)?[ \t]*-{{3}}"
PEM_BEGIN = re.compile(_PEM.format(word="BEGIN"), re.IGNORECASE)
PEM_END = re.compile(_PEM.format(word="END"), re.IGNORECASE)
_B64_BODY = re.compile(r"[A-Za-z0-9+/]{4,}={0,2}")
#: base64-looking lines held back while waiting to see whether an END line closes them (a key
#: whose BEGIN is above the selection); past this many they are let through, and an END after
#: them then REFUSES the copy (the start of that key already went out).
PEM_HOLD_LINES = 4096
#: the assembled block is re-scanned WHOLE up to this size, in overlapping windows above it
WHOLE_SCAN_MAX = 16 << 20
REPLACED = "‹line replaced by mokata's secret scan: {kind}›"


def _replace(line: str, kind: str, mark: str = REPLACED) -> str:
    eol = line[len(line.rstrip("\r\n")):]
    return mark.format(kind=kind) + eol


def _b64_body(line: str) -> bool:
    t = line.strip().replace("\\n", "").strip("\"'`,+;() ").strip()
    return bool(t) and _B64_BODY.fullmatch(t) is not None


#: characters of a line kept across read blocks so a marker split by a block is still seen
_PEM_TAIL = 4096


def _pem_walk(inside: bool, text: str) -> Tuple[bool, bool, int]:
    """(key material?, inside after, where the last marker ended) for one run of text."""
    key = inside
    pos = 0
    while True:
        m = (PEM_END if inside else PEM_BEGIN).search(text, pos)
        if m is None:
            return key, inside, pos
        key = True
        inside = not inside
        pos = m.end()


def pem_step(inside: bool, text: str) -> Tuple[bool, bool]:
    """One line through the key state machine: (is this line key material, inside a key after
    it). `inside` is the state BEFORE the line. Every marker on the line counts, in order, so an
    END followed by the next key's BEGIN on one line leaves the state inside."""
    return _pem_walk(inside, text)[:2]


def pem_state_before(root: str, rel: str, lo: int) -> bool:
    """Is line `lo` of the file inside a private key (a BEGIN above it with no END yet)? Streamed:
    a line is read in blocks, and the unconsumed end of each block (after the last marker, at
    most `_PEM_TAIL` characters) is carried into the next, so a marker split by a block is seen
    once and never twice."""
    from ..knowledge.cancel import checkpoint
    inside = False
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        ln = 1
        tail = ""
        for raw in iter(lambda: fh.readline(_block()), b""):
            if ln >= lo:
                break
            text = tail + dec.decode(raw)
            _key, inside, end = _pem_walk(inside, text)
            if raw.endswith(b"\n"):
                ln += 1
                tail = ""
                if ln % 4096 == 0:
                    checkpoint()
            else:
                tail = text[max(end, len(text) - _PEM_TAIL):]
    return inside


def pem_lines(lines: Iterable[str], counts: Dict[str, int], rel: str,
              inside: bool = False, mark: str = REPLACED) -> Iterator[str]:
    """`lines` with every private key replaced WHOLE: BEGIN through END (an unterminated BEGIN to
    the end), and a base64 run that an END line closes (its BEGIN was above). Counts each line.
    `inside`: the first line is inside a key (the caller knows its BEGIN is above). `mark`: the
    replacement line's wording (`{kind}` is filled in)."""
    pending: List[str] = []
    let_through = False                      # a base64 run was let through un-held

    def rep(line: str) -> str:
        counts["replaced"] = counts.get("replaced", 0) + 1
        return _replace(line, "private-key", mark)
    for ln in lines:
        body = ln.rstrip("\r\n")
        if inside:
            yield rep(ln)
            inside = pem_step(True, body)[1]
            continue
        b = PEM_BEGIN.search(body)
        e = PEM_END.search(body)
        if e and (b is None or e.start() < b.start()):
            # an END before any BEGIN: it closes a key whose BEGIN was above this text
            if let_through:
                raise RefRefused(f"the context for {rel} holds a private key longer than mokata "
                                 "can hold back — nothing is copied")
            for p in pending:
                yield rep(p)
            pending = []
            yield rep(ln)
            inside = pem_step(False, body[e.end():])[1]
            continue
        if b:
            yield from pending
            pending = []
            let_through = False
            yield rep(ln)
            inside = pem_step(False, body)[1]
            continue
        if _b64_body(body):
            pending.append(ln)
            if len(pending) > PEM_HOLD_LINES:
                yield from pending
                pending = []
                let_through = True
            continue
        yield from pending
        pending = []
        let_through = False
        yield ln
    yield from pending


def _line_findings(line: str) -> Optional[str]:
    from ..govern.secrets import scan
    for f in scan(line):
        if f.layer in ("signature", "entropy"):
            return f.kind
    return None


def _residual(text: str) -> bool:
    from ..govern.secrets import scan
    return (PEM_BEGIN.search(text) is not None or PEM_END.search(text) is not None
            or any(f.layer in ("signature", "entropy") for f in scan(text)))


def verify_assembled(path: str, rel: str) -> None:
    """Re-scan the ASSEMBLED, redacted block with the shared scanner (and the key markers): whole
    up to `WHOLE_SCAN_MAX`, window by window above it. The windows need no overlap: the shared
    scanner matches within one line (`govern.secrets._scan_signatures` splits on lines), and the
    one multi-line secret it knows — a private key — is handled as a unit by `pem_lines` before
    this, statefully across the whole block. Anything found refuses the whole copy."""
    from ..knowledge.cancel import checkpoint
    refuse = RefRefused(f"the context for {rel} still carries a secret after redaction — nothing "
                        "is copied")
    if os.path.getsize(path) <= WHOLE_SCAN_MAX:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            if _residual(fh.read()):
                raise refuse
        return
    with open(path, "r", encoding="utf-8", newline="") as fh:
        while True:
            checkpoint()
            chunk = [ln for _, ln in zip(range(SCAN_CHUNK_LINES), fh)]
            if not chunk:
                return
            if _residual("".join(chunk)):
                raise refuse


def scan_spill(src: str, w: SpillWriter, rel: str) -> Dict[str, int]:
    """Copy `src` into `w` with every secret replaced: a private key WHOLE (`pem_lines`), any
    other secret-bearing line by itself (each line of a chunk with a finding is scanned on its
    own, so the line replaced is the line that holds it). Then the assembled result is re-scanned
    (`verify_assembled`), and a secret still there refuses the whole copy."""
    from ..govern.secrets import scan
    from ..knowledge.cancel import checkpoint
    counts: Dict[str, int] = {"replaced": 0}
    done = 0
    with open(src, "r", encoding="utf-8", newline="") as fh:
        lines = pem_lines(fh, counts, rel)
        while True:
            checkpoint()
            chunk = [ln for _, ln in zip(range(SCAN_CHUNK_LINES), lines)]
            if not chunk:
                break
            if any(f.layer in ("signature", "entropy") for f in scan("".join(chunk))):
                for i, ln in enumerate(chunk):
                    kind = _line_findings(ln)
                    if kind:
                        chunk[i] = _replace(ln, kind)
                        counts["replaced"] += 1
            w.write("".join(chunk))
            done += len(chunk)
            say(done, "lines scanned for secrets", "scanning the block for secrets")
    w.close()
    verify_assembled(w.path, rel)
    return {"replaced": counts["replaced"]}


def copy_context(root: str, code_root: str, rel: str, lo: int, hi: int,
                 directory: str) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, int]]:
    """The two shapes of the block, built and secret-scanned into spills. Returns (head, files,
    sizes). Any failure or Cancel removes every spill it made."""
    from .features.lsp import ac_ties
    made: List[SpillWriter] = []
    try:
        if _sensitive(rel):
            raise RefRefused(f"{rel} is a sensitive location — its text is never copied")
        say(0, "", "reading the selection")
        sel = read_lines(code_root, rel, lo, hi)
        # V1: a selection that starts INSIDE a private key (its BEGIN above line lo) is key
        # material from its first line — replaced before it enters the block at all.
        selcounts: Dict[str, int] = {"replaced": 0}
        sel = "".join(pem_lines(re.findall(r"[^\n]*\n|[^\n]+$", sel), selcounts, rel,
                                inside=pem_state_before(code_root, rel, lo)))
        defs, dropped, _ol = definitions_in(code_root, rel, lo, hi)
        names = [x for d in defs for x in (d.name, d.qual) if x]
        acs = ac_ties(root, rel, names or None)
        from .statedir import initialized
        acs_why = ("" if acs else "this repository is not set up with mokata (no spec)"
                   if not initialized(root) else "no AC of the default run's emitted spec "
                   f"anchors {tok(rel)}")
        radii = []
        for i, d in enumerate(defs):
            say(i, "definitions", f"blast radius {i + 1} of {len(defs)}: {d.qual or d.name}",
                total=len(defs))
            radii.append(radius_for(code_root, d))
        ext = os.path.splitext(rel)[1].lstrip(".")
        parts = {"selection": sel, "sha": _head_sha(code_root),
                 "when": time.strftime("%Y-%m-%d %H:%M %Z"), "lang": ext if re.fullmatch(
                     r"[A-Za-z0-9+#-]{1,12}", ext or "") else "", "acs": acs, "acs_why": acs_why,
                 "run": acs[0]["run"] if acs else "", "radii": radii, "dropped": dropped}
        files: Dict[str, str] = {}
        sizes: Dict[str, int] = {}
        meta: Dict[str, Any] = {"shapes": {}}
        for shape in ("full", "summary"):
            raw = SpillWriter(directory, "raw")
            made.append(raw)
            counts = build_block(root, code_root, rel, lo, hi, shape, parts, raw)
            raw.close()
            clean = SpillWriter(directory, shape)
            made.append(clean)
            sc = scan_spill(raw.path, clean, rel)
            raw.discard()
            made.remove(raw)
            files[shape] = clean.path
            sizes[shape] = clean.bytes
            meta["shapes"][shape] = {"bytes": clean.bytes, "tokens": (clean.bytes + 3) // 4,
                                     "replaced": sc["replaced"] + selcounts["replaced"],
                                     "sensitive": counts.get("sensitive", 0)}
        cut = [r["def"].qual or r["def"].name for r in radii if r["result"].truncated]
        lexical = [r["def"].qual or r["def"].name for r in radii if not r["result"].structural]
        meta.update({"path": rel, "lines": [lo, hi], "selection_bytes": len(sel.encode("utf-8")),
                     "definitions": [r["def"].facts() for r in radii], "dropped": dropped,
                     "cut": cut, "lexical": lexical, "acs": len(acs), "acs_why": acs_why,
                     "complete": not cut,
                     "radii": [{"name": r["def"].qual or r["def"].name,
                                "count": r["result"].count, "total": r["result"].total,
                                "truncated": bool(r["result"].truncated),
                                "basis": r["result"].basis, "backend": r["result"].backend,
                                "asked_by": r["asked_by"], "shared": r["shared"]}
                               for r in radii]})
        made.clear()
        return meta, files, sizes
    finally:
        for m in made:
            m.discard()


def text_chunk(res: Result, shape: str, offset: int) -> Dict[str, Any]:
    path = res.files.get(shape)
    if path is None:
        raise Expired(EXPIRED)
    try:
        fh = open(path, "rb")
    except OSError:
        raise Expired("expired, re-run — the result's file is gone") from None
    with fh:
        fh.seek(max(0, offset))
        data = fh.read(TEXT_CHUNK)
    # never split a UTF-8 sequence across chunks
    cut = len(data)
    if cut == TEXT_CHUNK:
        while cut > 0 and (data[cut - 1] & 0xC0) == 0x80:
            cut -= 1
        if cut > 0 and data[cut - 1] >= 0xC0:
            cut -= 1
    data = data[:cut]
    nxt = offset + len(data)
    size = res.sizes.get(shape, 0)
    return {"text": data.decode("utf-8"), "offset": offset, "next": nxt if nxt < size else None,
            "size": size}


__all__ = ["tok", "parse_ref", "canon_path", "Definition", "definition_at", "definitions_in",
           "resolve_qual", "copy_ref", "find_usages", "copy_context", "Store", "Result", "page",
           "staleness", "text_chunk", "RefRefused", "Expired", "SpillWriter", "sweep",
           "spill_dir", "fence_for"]
