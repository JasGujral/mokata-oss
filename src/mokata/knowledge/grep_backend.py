"""B3 — the grep floor: a lexical implementation of the typed query API.

This is the *documented fallback* when no structural graph is present. It is regex/text
search over source files — NOT a parser and NOT a graph. Results are approximate by
design (every result is marked `degraded`); the value is that the same typed API keeps
working with zero external dependencies, on any machine, so the engine never hard-fails.

The heuristics are Python-shaped (the v1 sample language); the file walk is generic.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from .query import QUERY_KINDS, GraphBackend, QueryResult, Reference

SOURCE_EXTENSIONS = (".py",)

# Identifiers that look like calls but are language keywords, not callees.
_PY_KEYWORDS = {
    "if", "elif", "for", "while", "with", "return", "and", "or", "not", "in", "is",
    "def", "class", "lambda", "assert", "yield", "await", "del", "raise", "except",
    "import", "from", "global", "nonlocal", "pass", "else", "try", "finally", "as",
}


class GrepBackend(GraphBackend):
    is_graph = False

    def __init__(self, root: str, name: str = "grep",
                 extensions: Tuple[str, ...] = SOURCE_EXTENSIONS) -> None:
        self.root = root
        self.name = name
        self.extensions = tuple(extensions)

    # --- public API ----------------------------------------------------------
    def query(self, kind: str, target: str, depth: int = 1) -> QueryResult:
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
        else:
            refs = self._blast_radius(target, depth)
        return QueryResult(
            kind=kind, target=target, references=refs, backend=self.name,
            degraded=True,
            note="lexical fallback (no structural graph; results are approximate)",
        )

    # --- file helpers --------------------------------------------------------
    def _files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            # skip hidden dirs (e.g. .mokata, .git) so config/state isn't scanned
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.endswith(self.extensions):
                    yield os.path.join(dirpath, fn)

    @staticmethod
    def _read(path: str) -> List[str]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.root)

    @staticmethod
    def _enclosing(lines: List[str], idx: int):
        """Nearest preceding def/class with smaller indentation than line `idx`."""
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        for j in range(idx - 1, -1, -1):
            m = re.match(r"^(\s*)(?:def|class)\s+(\w+)", lines[j])
            if m and len(m.group(1)) < indent:
                return m.group(2)
        return None

    # --- query implementations ----------------------------------------------
    def _callers(self, target: str) -> List[Reference]:
        call = re.compile(r"(?<![\w.])" + re.escape(target) + r"\s*\(")
        is_def = re.compile(r"^\s*(?:def|class)\s+" + re.escape(target) + r"\b")
        out: List[Reference] = []
        for path in self._files():
            lines = self._read(path)
            for i, line in enumerate(lines):
                if is_def.search(line):
                    continue
                if call.search(line):
                    out.append(Reference(self._rel(path), i + 1, line.strip(),
                                         self._enclosing(lines, i)))
        return out

    def _callees(self, target: str) -> List[Reference]:
        call = re.compile(r"(?<![\w.])(\w+)\s*\(")
        is_def = re.compile(r"^(\s*)def\s+" + re.escape(target) + r"\s*\(")
        out: List[Reference] = []
        for path in self._files():
            lines = self._read(path)
            i = 0
            while i < len(lines):
                m = is_def.match(lines[i])
                if not m:
                    i += 1
                    continue
                base = len(m.group(1))
                j = i + 1
                while j < len(lines):
                    ln = lines[j]
                    if ln.strip() == "":
                        j += 1
                        continue
                    if len(ln) - len(ln.lstrip()) <= base:
                        break          # dedent: end of the def body
                    for cm in call.finditer(ln):
                        name = cm.group(1)
                        if name == target or name in _PY_KEYWORDS:
                            continue
                        out.append(Reference(self._rel(path), j + 1, ln.strip(), name))
                    j += 1
                i = j
        return out

    def _implementers(self, target: str) -> List[Reference]:
        cls = re.compile(r"^\s*class\s+(\w+)\s*\(([^)]*)\)")
        word = re.compile(r"(?<![\w.])" + re.escape(target) + r"(?![\w])")
        out: List[Reference] = []
        for path in self._files():
            lines = self._read(path)
            for i, line in enumerate(lines):
                m = cls.match(line)
                if m and word.search(m.group(2)):
                    out.append(Reference(self._rel(path), i + 1, line.strip(),
                                         m.group(1)))
        return out

    def _imports(self, target: str) -> List[Reference]:
        word = re.compile(r"(?<![\w.])" + re.escape(target) + r"(?![\w])")
        out: List[Reference] = []
        for path in self._files():
            lines = self._read(path)
            for i, line in enumerate(lines):
                s = line.strip()
                if (s.startswith("import ") or s.startswith("from ")) and word.search(line):
                    out.append(Reference(self._rel(path), i + 1, s, None))
        return out

    def _blast_radius(self, target: str, depth: int) -> List[Reference]:
        """Transitive callers up to `depth` hops — the impact surface of `target`."""
        seen_syms = {target}
        seen_refs = set()
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
