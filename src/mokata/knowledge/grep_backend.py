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

import os
import re
from typing import List, Optional, Tuple

from .. import languages
from .query import QUERY_KINDS, GraphBackend, QueryResult, Reference

# Kept as a module symbol for back-compat; the default walk now spans every known language.
SOURCE_EXTENSIONS = languages.SOURCE_EXTENSIONS


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
        call = re.compile(r"(?<![\w.])" + re.escape(target) + r"\s*\(")
        out: List[Reference] = []
        for path in self._files():
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.defines(line, target):
                    continue
                if call.search(line):
                    out.append(Reference(self._rel(path), i + 1, line.strip(),
                                         self._enclosing(lang, lines, i)))
        return out

    def _callees(self, target: str) -> List[Reference]:
        call = re.compile(r"(?<![\w.])(\w+)\s*\(")
        out: List[Reference] = []
        for path in self._files():
            lang = self._lang(path)
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
        for path in self._files():
            lang = self._lang(path)
            lines = self._read(path)
            for i, line in enumerate(lines):
                if lang.is_import_line(line) and word.search(line):
                    out.append(Reference(self._rel(path), i + 1, line.strip(), None))
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
