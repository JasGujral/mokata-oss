"""code-review-graph's NODE TABLE, read directly — symbols, outlines and definitions at scale.

0.0.26 P35 `NAV-AT-SCALE`. On kubernetes (7.3M lines of Go) code-review-graph had parsed 150k
definitions, yet the IDE showed no outline, ⌘P `@` returned 0 symbols "ready", and "go to
definition" walked the grep floor for 13–17 s: the MCP interface has no definition-site op
(`query_graph_tool` patterns are callers/callees/imports/…), so every one of those questions fell
past the graph that already held the answer (audit A #9, B-F9).

The answer is in the tool's own index: `graph.db`'s `nodes` table carries every definition with
its file, line span, kind, parent and language, indexed by file and by qualified name. This module
opens that database READ-ONLY (`connect_sqlite_readonly`: a `mode=ro` URI, no pragmas, nothing
written — the same door `crg_build.probe_index` reads it through) and answers:

    reader(root)          a `NodeReader` when the repo's index is BUILT, else None
    .outline(rel)         one file's definitions, nested by parent, with end lines — or None when
                          the index holds no such file
    .defs(name)           every definition named `name` (exact), as (path, line, kind, qname)
    .symbols()            every definition in the repo, for ⌘P (checkpointed, with progress)
    .languages()          language -> definition count (what the index covers)

Paths: code-review-graph stores ABSOLUTE paths under the root it was built in (`metadata.repo_root`);
every answer here is repo-relative with '/' separators, like every other mokata answer.

Stdlib only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ..repo_paths import name_of, path_of
from .cancel import checkpoint, report

#: Rows read per batch of the full symbol scan (each batch is one checkpoint + one progress line).
SCAN_BATCH = 5000

#: Bound parameters per IN (...) list (SQLite's oldest limit is 999 variables per statement).
SQL_CHUNK = 400
#: Names per full-text query (one MATCH expression of `name:"…" OR …`).
FTS_CHUNK = 50

#: code-review-graph's node kinds -> the kind words mokata's outline / ⌘P already use.
_KINDS = {"Class": "class", "Function": "function", "Test": "test", "Type": "type"}

#: File extension -> the language name code-review-graph records (only what mokata names to users).
LANGUAGE_OF_EXT = {
    ".py": "python", ".go": "go", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".java": "java",
    ".rs": "rust", ".rb": "ruby", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".kt": "kotlin", ".swift": "swift", ".php": "php",
    ".scala": "scala", ".sh": "bash", ".lua": "lua", ".dart": "dart", ".ps1": "powershell",
}

#: How a language is said to a person.
LANGUAGE_LABEL = {
    "python": "Python", "go": "Go", "typescript": "TypeScript", "tsx": "TypeScript",
    "javascript": "JavaScript", "java": "Java", "rust": "Rust", "ruby": "Ruby", "c": "C",
    "cpp": "C++", "csharp": "C#", "kotlin": "Kotlin", "swift": "Swift", "php": "PHP",
    "scala": "Scala", "bash": "shell", "lua": "Lua", "dart": "Dart", "powershell": "PowerShell",
}


def language_of(rel: str) -> Optional[str]:
    return LANGUAGE_OF_EXT.get(os.path.splitext(rel)[1].lower())


def label_of(language: Optional[str]) -> str:
    return LANGUAGE_LABEL.get(language or "", language or "this language")


def file_digest(data: bytes) -> str:
    """The digest code-review-graph records per File node (`file_hash`, sha256 of the bytes) —
    compared to tell whether an outline from the index still matches the file on disk."""
    return hashlib.sha256(data).hexdigest()


def _select(conn: sqlite3.Connection, sql: str, params: Any = ()) -> sqlite3.Cursor:
    """THE one read of code-review-graph's DB in this module: a SELECT on a `mode=ro`
    connection (`connect_sqlite_readonly`), which cannot create or modify the file."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ValueError("crg_nodes only reads code-review-graph's index")
    return conn.execute(sql, params)


def _kind(kind: str, parent: Optional[str]) -> str:
    if kind == "Function" and parent:
        return "method"
    return _KINDS.get(kind, (kind or "").lower())


class NodeReader:
    """Read-only questions over one repo's built code-review-graph index."""

    def __init__(self, root: str, db: str) -> None:
        self.root = os.path.abspath(root)
        self.db = db
        self._built_root: Optional[str] = None

    # -- plumbing -----------------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        from ..memory._sqlite import connect_sqlite_readonly
        return connect_sqlite_readonly(self.db, timeout_s=2.0)

    def stamp(self) -> Tuple[int, int]:
        """Changes whenever code-review-graph writes its index (the DB or its WAL)."""
        out = []
        for suffix in ("", "-wal"):
            try:
                out.append(os.stat(self.db + suffix).st_mtime_ns)
            except OSError:
                out.append(0)
        return out[0], out[1]

    def built_root(self, conn: sqlite3.Connection) -> str:
        if self._built_root is None:
            row = None
            try:
                row = _select(conn, "SELECT value FROM metadata WHERE key='repo_root'").fetchone()
            except sqlite3.Error:
                row = None
            self._built_root = os.path.abspath(row[0]) if row and row[0] else self.root
        return self._built_root

    def _rel(self, path: str, base: str) -> Optional[str]:
        """code-review-graph's absolute (native) path as a repo NAME (`repo_paths.name_of`), or
        None when it lies outside the root the index was built in."""
        if not path or not os.path.isabs(path):
            return None
        try:
            rel = str(name_of(path, base))
        except ValueError:                      # another drive (Windows)
            return None
        return None if rel == ".." or rel.startswith("../") else rel

    def _abs_variants(self, rel: str, base: str) -> List[str]:
        return [path_of(base, rel)]

    # -- questions ----------------------------------------------------------------------------
    def outline(self, rel: str) -> Optional[Dict[str, Any]]:
        """`{symbols, file_hash, language}` for one file, or None when the index has no such
        file. Symbols carry name, kind, line, end, depth (1 under a class/type parent) and the
        graph's qualified name."""
        conn = self._conn()
        try:
            base = self.built_root(conn)
            paths = self._abs_variants(rel, base)
            marks = ",".join("?" * len(paths))
            rows = _select(
                conn, f"SELECT kind, name, line_start, line_end, parent_name, qualified_name, language, "
                f"file_hash FROM nodes WHERE file_path IN ({marks}) ORDER BY line_start, id",
                paths).fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        file_hash, language = "", None
        syms: List[Dict[str, Any]] = []
        for kind, name, start, end, parent, qn, lang, fh in rows:
            language = language or lang
            if kind == "File":
                file_hash = fh or ""
                continue
            syms.append({"name": name, "kind": _kind(kind, parent), "line": int(start or 1),
                         "end": int(end) if end else None, "depth": 1 if parent else 0,
                         "qualified_name": self._rel(qn, base) or qn})
        return {"symbols": syms, "file_hash": file_hash, "language": language}

    def defs(self, name: str) -> List[Dict[str, Any]]:
        """Every definition whose NAME is `name` (a `Class.method` spelling matches the graph's
        symbol column too), as rows `{path, line, end, kind, qualified_name, language}`."""
        conn = self._conn()
        try:
            base = self.built_root(conn)
            rows = _select(
                conn, "SELECT kind, name, file_path, line_start, line_end, parent_name, qualified_name, "
                "language FROM nodes WHERE kind != 'File' AND (name = ? OR symbol = ?) "
                "ORDER BY file_path, line_start", (name, name)).fetchall()
        finally:
            conn.close()
        out = []
        for kind, _n, fp, start, end, parent, qn, lang in rows:
            rel = self._rel(fp, base)
            if rel is None:
                continue
            out.append({"path": rel, "line": int(start or 1), "end": int(end) if end else None,
                        "kind": _kind(kind, parent), "language": lang,
                        "qualified_name": self._rel(qn, base) or qn, "graph_id": qn})
        return out

    def symbols(self) -> Tuple[List[Tuple[str, str, int, str]], Dict[str, int]]:
        """Every definition as ⌘P rows `(name, rel, line, kind)`, and language -> count. Read in
        batches; each batch checks the job's Cancel and reports n/N rows."""
        conn = self._conn()
        rows: List[Tuple[str, str, int, str]] = []
        langs: Dict[str, int] = {}
        try:
            base = self.built_root(conn)
            total = _select(conn, "SELECT count(*) FROM nodes WHERE kind != 'File'").fetchone()[0]
            report(done=0, total=total, unit="symbols", note="reading the code graph's symbols")
            cur = _select(conn, "SELECT name, file_path, line_start, kind, parent_name, language "
                               "FROM nodes WHERE kind != 'File'")
            # the common case without a relpath per row: a path under `<base><sep>` whose name
            # is the rest (on POSIX already '/'-spelled); anything else goes through `_rel`
            prefix = base.rstrip(os.sep) + os.sep
            cut = len(prefix)
            posix = os.sep == "/"
            while True:
                checkpoint()
                batch = cur.fetchmany(SCAN_BATCH)
                if not batch:
                    break
                for name, fp, start, kind, parent, lang in batch:
                    rel = fp[cut:] if posix and fp.startswith(prefix) else self._rel(fp, base)
                    if rel is None:
                        continue
                    rows.append((name, rel, int(start or 1), _kind(kind, parent)))
                    langs[lang or ""] = langs.get(lang or "", 0) + 1
                report(done=len(rows), total=total, unit="symbols",
                       note="reading the code graph's symbols")
        finally:
            conn.close()
        return rows, langs

    def _qns_named(self, conn: sqlite3.Connection, names: List[str]) -> Dict[str, str]:
        """qualified name -> name, for every definition NAMED one of `names` (as `defs`).

        `nodes.name` has no index (a scan of 150k rows: 0.2-0.3 s on kubernetes, per ring), so
        the names are found through code-review-graph's own full-text index (`nodes_fts`, 25 ms)
        and filtered to the EXACT name; `symbol` (`Class.method`) is indexed. A name the
        tokenizer cannot see, or an index without `nodes_fts`, is scanned as before."""
        out: Dict[str, str] = {}
        uniq = sorted(set(names))
        scan = [n for n in uniq if not any(ch.isalnum() for ch in n)]
        fts = [n for n in uniq if n not in scan]
        for i in range(0, len(fts), FTS_CHUNK):
            checkpoint()
            part = fts[i:i + FTS_CHUNK]
            match = " OR ".join('name:"' + n.replace('"', '""') + '"' for n in part)
            marks = ",".join("?" * len(part))
            try:
                rows = _select(
                    conn, f"SELECT n.qualified_name, n.name FROM nodes_fts JOIN nodes n ON "
                    f"n.id = nodes_fts.rowid WHERE nodes_fts MATCH ? AND n.kind != 'File' AND "
                    f"n.name IN ({marks})", [match] + part).fetchall()
            except sqlite3.Error:
                scan.extend(part)
                continue
            for qn, name in rows:
                out[qn] = name
            for qn, sym in _select(conn, f"SELECT qualified_name, symbol FROM nodes WHERE "
                                         f"symbol IN ({marks}) AND kind != 'File'", part):
                out.setdefault(qn, sym)
        for i in range(0, len(scan), SQL_CHUNK):
            checkpoint()
            part = scan[i:i + SQL_CHUNK]
            marks = ",".join("?" * len(part))
            want = set(part)
            for qn, name, sym in _select(
                    conn, f"SELECT qualified_name, name, symbol FROM nodes WHERE kind != 'File' "
                    f"AND (name IN ({marks}) OR symbol IN ({marks}))", part + part):
                out[qn] = name if name in want else sym
        return out

    def callers_by_name(self, names: List[str]) -> List[Tuple[str, str, str, int]]:
        """0.0.26 P37 (`GRAPH-AT-SCALE`, R4) — the PROVEN call edges into every definition named
        one of `names`, straight from the index: rows `(callee name, caller name, call-site
        path, call-site line)`. These are exactly the rows code-review-graph's own `callers_of`
        proves (an edge whose target is the definition's qualified name, from a caller node it
        holds); its name-matched fallback rows are NOT here — the tool's answer adds them. On
        kubernetes this is ~20 ms where the tool's union over 7 definitions takes 9–26 s, so the
        ripple draws its rings from here first and the tool's answer confirms them.
        `+kind` keeps SQLite on the target index (it otherwise scans every CALLS edge: 17 s)."""
        conn = self._conn()
        try:
            base = self.built_root(conn)
            targets = self._qns_named(conn, sorted(set(names)))
            qns = list(targets)
            edges: List[Tuple[str, str, str, int]] = []
            for i in range(0, len(qns), SQL_CHUNK):
                checkpoint()
                part = qns[i:i + SQL_CHUNK]
                marks = ",".join("?" * len(part))
                edges.extend(_select(
                    conn, f"SELECT target_qualified, source_qualified, file_path, line FROM edges "
                    f"WHERE target_qualified IN ({marks}) AND +kind = 'CALLS' "
                    f"ORDER BY file_path, line", part).fetchall())
            sources = sorted({e[1] for e in edges})
            name_of_src: Dict[str, str] = {}
            for i in range(0, len(sources), SQL_CHUNK):
                checkpoint()
                part = sources[i:i + SQL_CHUNK]
                marks = ",".join("?" * len(part))
                for qn, name in _select(conn, f"SELECT qualified_name, name FROM nodes WHERE "
                                              f"qualified_name IN ({marks})", part):
                    name_of_src[qn] = name
        finally:
            conn.close()
        out: List[Tuple[str, str, str, int]] = []
        for tq, sq, fp, line in edges:
            caller = name_of_src.get(sq)
            rel = self._rel(fp, base)
            if caller is None or rel is None:       # the tool drops a caller it holds no node for
                continue
            out.append((targets[tq], caller, rel, int(line or 0)))
        return out

    def _names_of(self, conn: sqlite3.Connection, qns: List[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for i in range(0, len(qns), SQL_CHUNK):
            checkpoint()
            part = qns[i:i + SQL_CHUNK]
            marks = ",".join("?" * len(part))
            for qn, name in _select(conn, f"SELECT qualified_name, name FROM nodes WHERE "
                                          f"qualified_name IN ({marks})", part):
                out[qn] = name
        return out

    def call_path(self, src: str, dst: str,
                  depth: int) -> Optional[List[Tuple[str, Optional[str], int]]]:
        """0.0.26 P37 — the shortest call chain from any definition NAMED `src` to one named
        `dst` over the index's CALLS edges (a callee the index holds is followed by its
        qualified name, a bare unresolved target by its name — as code-review-graph's own
        `callees_of` lists them), within `depth` hops. Rows `(name, call-site path, line)`
        from `src` (site None) to `dst`, or None. One indexed SELECT per level and chunk; each
        checks the job's Cancel and says how far it got."""
        conn = self._conn()
        try:
            base = self.built_root(conn)
            start = self._qns_named(conn, [src])
            parent: Dict[str, Tuple[Optional[str], str, int]] = {q: (None, "", 0) for q in start}
            name: Dict[str, str] = dict(start)
            frontier = list(start)
            for level in range(1, depth + 1):
                report(done=level - 1, total=depth, unit="levels",
                       note=f"searching the code graph's index: {len(parent)} symbols seen")
                rows = []
                for i in range(0, len(frontier), SQL_CHUNK):
                    checkpoint()
                    part = frontier[i:i + SQL_CHUNK]
                    marks = ",".join("?" * len(part))
                    rows.extend(_select(
                        conn, f"SELECT source_qualified, target_qualified, file_path, line FROM "
                        f"edges WHERE source_qualified IN ({marks}) AND +kind = 'CALLS'",
                        part).fetchall())
                known = self._names_of(conn, sorted({r[1] for r in rows} - set(name)))
                name.update(known)
                bare = sorted({r[1] for r in rows if r[1] not in name and "::" not in r[1]})
                by_bare: Dict[str, List[str]] = {}
                for qn, nm in self._qns_named(conn, bare).items():
                    by_bare.setdefault(nm, []).append(qn)
                    name.setdefault(qn, nm)
                nxt: List[str] = []
                for sq, tq, fp, line in rows:
                    targets = [tq] if tq in name and tq not in by_bare else by_bare.get(tq, [])
                    tname = name.get(tq, tq if "::" not in tq else None)
                    if tname is None:
                        continue
                    if tname == dst:
                        hops = [(dst, self._rel(fp, base), int(line or 0))]
                        cur: Optional[str] = sq
                        while cur is not None:
                            up, upf, upl = parent[cur]
                            hops.append((name[cur], self._rel(upf, base) if upf else None, upl))
                            cur = up
                        hops.reverse()
                        return hops
                    for t in targets:
                        if t not in parent:
                            parent[t] = (sq, fp, int(line or 0))
                            nxt.append(t)
                frontier = nxt
                if not frontier:
                    break
        finally:
            conn.close()
        return None

    def languages(self) -> Dict[str, int]:
        conn = self._conn()
        try:
            return {lang or "": n for lang, n in _select(
                conn, "SELECT language, count(*) FROM nodes WHERE kind != 'File' GROUP BY language")}
        finally:
            conn.close()


def reader(root: str) -> Optional[NodeReader]:
    """A reader over `root`'s code-review-graph index when it is BUILT (code-review-graph's own
    record says so), else None. Never creates, writes or raises."""
    from .crg_build import INDEX_BUILT, db_path, probe_index
    if probe_index(root) != INDEX_BUILT:                # never raises (crg_build.probe_db)
        return None
    return NodeReader(root, db_path(root))


__all__ = ["NodeReader", "reader", "language_of", "label_of", "file_digest", "LANGUAGE_OF_EXT",
           "SCAN_BATCH", "SQL_CHUNK"]
