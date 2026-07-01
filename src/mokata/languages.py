"""Stage 65 — language awareness for the grep floor + heuristic recognizers.

CRITICAL INVIOLABLE (locked exclusion): NO IN-HOUSE PARSER / AST. This module is
file-extension awareness + per-language LEXICAL heuristics ONLY — a central table mapping
extensions to a language and, per language, the regex patterns the grep FLOOR and the
spec/test recognizers use. The REAL structural graph stays adopt+fallback (external tools);
this is the dependency-free floor that keeps the typed queries + the gates answering on
whatever stack a dev opens.

A grep heuristic is approximate by design. It stays the floor and announces itself as
lexical (see `grep_backend`); the real graph adapter, when wired, is always preferred.

DEGRADE-CLEAN: an unknown extension maps to `GENERIC` — generic identifier matching that
never crashes. PYTHON behaviour is unchanged (the Python entry reproduces the prior
patterns exactly).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Control-flow / declaration words that look like a call (`name(`) but are not callees.
# A small, shared union is enough for the floor — over-keeping costs nothing but a miss.
_COMMON_KEYWORDS = frozenset({
    # python
    "if", "elif", "for", "while", "with", "return", "and", "or", "not", "in", "is",
    "def", "class", "lambda", "assert", "yield", "await", "del", "raise", "except",
    "import", "from", "global", "nonlocal", "pass", "else", "try", "finally", "as",
    "print", "super",
    # c-family / js / go / rust / java
    "function", "func", "fn", "let", "const", "var", "new", "switch", "case", "catch",
    "do", "this", "self", "match", "impl", "trait", "struct", "enum", "interface",
    "type", "package", "use", "mut", "pub", "static", "final", "public", "private",
    "protected", "void", "go", "defer", "select", "range", "make", "typeof", "extends",
    "implements", "throws", "throw", "instanceof",
})


@dataclass(frozen=True)
class Language:
    """The lexical heuristics for one language. Regexes only — never a parser.

    `block_style` is "indent" (Python) or "brace" (C-family): how the grep floor finds a
    function body when collecting its callees. Every regex captures group(1) = the relevant
    identifier where applicable.
    """

    name: str
    extensions: Tuple[str, ...]
    block_style: str
    # a line that DEFINES `{target}` (function/method/class/type) — formatted with the target
    _define_tmpl: str
    # any function/method definition on a line -> its name (group 1)
    _func_def: str
    # any scope opener (function OR type/class) -> its name (group 1); for `_enclosing`
    _scope_def: str
    # an import/require/use statement line
    _import: str
    # a subtype declaration -> (subtype_name, supertypes_text). Empty tuple => no convention.
    _inherit: Tuple[str, int, int] = ("", 0, 0)   # (pattern, name_group, supers_group)
    # every defined symbol name in a file's text (def/class/type/...): patterns, name=group 1
    _defs: Tuple[str, ...] = ()
    # a test-function start line -> its name (group "name"); and/or a test attribute line
    _test_def: str = ""
    _test_attr: str = ""
    keywords: frozenset = _COMMON_KEYWORDS

    # --- per-language predicates (all pure, all lexical) ---------------------------------
    def defines(self, line: str, target: str) -> bool:
        """True when `line` DEFINES a symbol named `target` (so callers can exclude it)."""
        if not self._define_tmpl:
            return False
        # `.replace` (not `.format`) — the templates contain literal regex braces (`\{`).
        pat = self._define_tmpl.replace("{target}", re.escape(target))
        return re.search(pat, line) is not None

    def func_name(self, line: str) -> Optional[str]:
        """The function/method name if `line` starts a definition, else None."""
        m = re.match(self._func_def, line) if self._func_def else None
        return m.group(1) if m else None

    def scope_name(self, line: str) -> Optional[str]:
        """The nearest enclosing scope's name (function OR type) if `line` opens one."""
        m = re.match(self._scope_def, line) if self._scope_def else None
        return m.group(1) if m else None

    def is_import_line(self, line: str) -> bool:
        return bool(self._import) and re.search(self._import, line) is not None

    def inheritance(self, line: str) -> Optional[Tuple[str, str]]:
        """(subtype_name, supertypes_text) if `line` declares an implements/extends/impl-for,
        else None. Go is structural -> no convention -> always None (degrade-clean)."""
        pat, ng, sg = self._inherit
        if not pat:
            return None
        m = re.search(pat, line)
        if not m:
            return None
        return m.group(ng), m.group(sg)

    def is_call_keyword(self, name: str) -> bool:
        return name in self.keywords

    def definition_names(self, text: str) -> List[str]:
        """Every defined symbol name (functions, classes, types) in `text`. Order-preserving,
        de-duplicated. Used for the spec-awareness touch-set."""
        out: List[str] = []
        seen = set()
        for pat in self._defs:
            for m in re.finditer(pat, text, re.MULTILINE):
                nm = m.group(1)
                if nm and nm not in seen:
                    seen.add(nm)
                    out.append(nm)
        return out

    def tests(self, lines: List[str]) -> List[Tuple[str, int, str]]:
        """Recognise test functions: yields (name, 1-based start line, body text) for each.

        Two conventions, both lexical: a test-named definition (pytest `test_*`, Go `Test*`,
        jest/vitest `test(...)`/`it(...)`) OR a test attribute/annotation (Rust `#[test]`,
        JUnit `@Test`) immediately preceding a function/method. Degrade-clean: a language with
        neither convention yields nothing."""
        out: List[Tuple[str, int, str]] = []
        test_def = re.compile(self._test_def) if self._test_def else None
        test_attr = re.compile(self._test_attr) if self._test_attr else None
        i = 0
        pending_attr = False
        while i < len(lines):
            line = lines[i]
            name = None
            if test_def is not None:
                m = test_def.search(line)
                if m:
                    name = m.groupdict().get("name") or (m.group(1) if m.groups() else None)
            if name is None and pending_attr and self._func_def:
                fn = re.match(self._func_def, line)
                if fn:
                    name = fn.group(1)
            if name is not None:
                start, body = self._block(lines, i)
                out.append((name, i + 1, body))
                pending_attr = False
                i = start
                continue
            if test_attr is not None and test_attr.search(line):
                pending_attr = True
            elif line.strip() and not (test_attr and test_attr.search(line)):
                pending_attr = False     # the attribute must sit just above its function
            i += 1
        return out

    # --- body extraction (indent- or brace-delimited; lexical, not parsed) ---------------
    def _block(self, lines: List[str], start: int) -> Tuple[int, str]:
        """Return (index after the block, body text) for the definition starting at `start`."""
        if self.block_style == "brace":
            return _brace_block(lines, start)
        return _indent_block(lines, start)


def _indent_block(lines: List[str], start: int) -> Tuple[int, str]:
    base = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    j = start + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= base:
            break
        block.append(ln)
        j += 1
    return j, "\n".join(block)


def _brace_block(lines: List[str], start: int) -> Tuple[int, str]:
    """Collect lines from the opening `{` until braces balance. If no brace is found on a
    reasonable horizon, fall back to just the start line (degrade-clean)."""
    block: List[str] = []
    depth = 0
    opened = False
    j = start
    while j < len(lines):
        ln = lines[j]
        block.append(ln)
        for ch in ln:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
        j += 1
        if opened and depth <= 0:
            break
        if not opened and j - start > 5:
            break                       # no body brace nearby -> stop scanning
    return j, "\n".join(block)


def iter_block_lines(lines: List[str], start: int, block_style: str):
    """Yield (index, line) for each line in the function body that begins at `start`
    (EXCLUDING the definition line). Shared by the grep floor's callee scan."""
    if block_style == "brace":
        end, _ = _brace_block(lines, start)
    else:
        end, _ = _indent_block(lines, start)
    for j in range(start + 1, end):
        yield j, lines[j]


# --------------------------------------------------------------------------- the registry
PYTHON = Language(
    name="python",
    extensions=(".py", ".pyi"),
    block_style="indent",
    _define_tmpl=r"^\s*(?:def|class)\s+{target}\b",
    _func_def=r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
    _scope_def=r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)",
    _import=r"^\s*(?:import|from)\s",
    _inherit=(r"^\s*class\s+(\w+)\s*\(([^)]*)\)", 1, 2),
    _defs=(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", r"^\s*class\s+(\w+)\s*[\(:]"),
    _test_def=r"^\s*def\s+(?P<name>test\w+)\s*\(",
)

JAVASCRIPT = Language(
    name="javascript",
    extensions=(".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
    block_style="brace",
    _define_tmpl=(r"(?:function\s+{target}\b|(?:class|interface)\s+{target}\b"
                  r"|(?:const|let|var)\s+{target}\s*=|{target}\s*[:=]\s*(?:async\s*)?\()"),
    _func_def=(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\("
               r"|^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*"
               r"(\w+)\s*\([^)]*\)\s*[:{]"),
    _scope_def=(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)"
                r"|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\("
                r"|^\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("),
    _import=r"^\s*(?:import\b|export\b.*\bfrom\b)|require\s*\(",
    _inherit=(r"\bclass\s+(\w+)\s+(?:extends|implements)\s+([\w,\s.<>]+?)\s*\{", 1, 2),
    _defs=(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)",
           r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)",
           r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=",
           r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*(\w+)\s*\([^)]*\)\s*[:{]"),
    _test_def=r"""(?:^|\s)(?:it|test)\s*\(\s*["'`](?P<name>[^"'`]+)""",
)

GO = Language(
    name="go",
    extensions=(".go",),
    block_style="brace",
    _define_tmpl=r"^\s*func\s+(?:\([^)]*\)\s*)?{target}\b|^\s*type\s+{target}\b",
    _func_def=r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(",
    _scope_def=r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(|^\s*type\s+(\w+)\s",
    _import=r"^\s*import\s|^\s*\"[\w./-]+\"\s*$",
    # Go interfaces are satisfied structurally — there is no `implements` keyword. No
    # convention to match lexically -> degrade-clean (the grep floor reports nothing here).
    _inherit=("", 0, 0),
    _defs=(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(", r"^\s*type\s+(\w+)\s"),
    _test_def=r"^\s*func\s+(?P<name>Test\w+)\s*\(",
)

RUST = Language(
    name="rust",
    extensions=(".rs",),
    block_style="brace",
    _define_tmpl=(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+{target}\b"
                  r"|^\s*(?:pub\s+)?(?:struct|enum|trait)\s+{target}\b"),
    _func_def=r"^\s*(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*",
    _scope_def=(r"^\s*(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)"
                r"|^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"),
    _import=r"^\s*use\s|^\s*(?:pub\s+)?mod\s",
    # `impl Trait for Type {` -> Type implements Trait. group1=Trait, group2=Type; we report
    # (Type, Trait) so an implementers(Trait) query finds Type.
    _inherit=(r"^\s*impl\s+(\w+)\s+for\s+(\w+)", 2, 1),
    _defs=(r"^\s*(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)",
           r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"),
    _test_def="",
    _test_attr=r"#\[\s*(?:\w+::)?test\s*\]|#\[\s*test\s*\]",
)

JAVA = Language(
    name="java",
    extensions=(".java",),
    block_style="brace",
    # Java is ambiguous: a method def and a call both look like `<type> name(...)`. A def
    # opens a body (trailing `{` / `throws … {`); a call ends with `;`. Require the brace so
    # `return compute();` is NOT mistaken for a definition of `compute`.
    _define_tmpl=(r"\b(?:class|interface|enum)\s+{target}\b"
                  r"|^\s*(?:@\w+\s*)*(?:public\s+|private\s+|protected\s+|static\s+|final\s+"
                  r"|abstract\s+|synchronized\s+)*[\w<>\[\],.]+\s+{target}\s*\([^)]*\)\s*"
                  r"(?:throws[\w,\s.]+)?\{"),
    _func_def=(r"^\s*(?:@\w+\s*)*(?:public\s+|private\s+|protected\s+|static\s+|final\s+"
               r"|abstract\s+|synchronized\s+)*[\w<>\[\],.]+\s+(\w+)\s*\([^)]*\)\s*"
               r"(?:throws[\w,\s.]+)?\{"),
    _scope_def=(r"^\s*(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*"
                r"(?:class|interface|enum)\s+(\w+)"
                r"|^\s*(?:@\w+\s*)*(?:public\s+|private\s+|protected\s+|static\s+|final\s+)*"
                r"[\w<>\[\],.]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[\w,\s.]+)?\{"),
    _import=r"^\s*import\s",
    _inherit=(r"\b(?:class|interface)\s+(\w+)\s+(?:extends|implements)\s+([\w,\s.<>]+?)\s*\{",
              1, 2),
    _defs=(r"^\s*(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*"
           r"(?:class|interface|enum)\s+(\w+)",
           r"^\s*(?:@\w+\s*)*(?:public\s+|private\s+|protected\s+|static\s+|final\s+"
           r"|synchronized\s+|abstract\s+)*[\w<>\[\],.]+\s+(\w+)\s*\([^)]*\)\s*"
           r"(?:throws[\w,\s.]+)?\{"),
    _test_def="",
    _test_attr=r"@Test\b",
)

# The generic fallback for an UNKNOWN language: no extension claim, broad identifier
# heuristics, no crashes. Recognises common definition keywords across families so a
# never-seen language still surfaces *something* lexically instead of erroring.
GENERIC = Language(
    name="generic",
    extensions=(),
    block_style="brace",
    _define_tmpl=r"\b(?:def|func|fn|function|class|struct|type|interface)\s+{target}\b",
    _func_def=r"^\s*(?:\w+\s+)*(?:def|func|fn|function)\s+(\w+)",
    _scope_def=r"^\s*(?:\w+\s+)*(?:def|func|fn|function|class|struct|type|interface)\s+(\w+)",
    _import=r"^\s*(?:import|from|use|require|include)\b",
    _inherit=("", 0, 0),
    _defs=(r"^\s*(?:\w+\s+)*(?:def|func|fn|function)\s+(\w+)",
           r"^\s*(?:\w+\s+)*(?:class|struct|type|interface|trait|enum)\s+(\w+)"),
    _test_def="",
)

LANGUAGES: Dict[str, Language] = {
    lang.name: lang for lang in (PYTHON, JAVASCRIPT, GO, RUST, JAVA)
}

# extension -> Language (lowercased). Built from the registry so the two never drift.
_BY_EXT: Dict[str, Language] = {}
for _lang in LANGUAGES.values():
    for _ext in _lang.extensions:
        _BY_EXT[_ext] = _lang

# Every source extension the grep floor walks — the union across all known languages.
SOURCE_EXTENSIONS: Tuple[str, ...] = tuple(sorted(_BY_EXT))


def language_for(path: str) -> Language:
    """The Language for `path`'s extension, or GENERIC for an unknown one (degrade-clean)."""
    ext = os.path.splitext(path)[1].lower()
    return _BY_EXT.get(ext, GENERIC)


def is_source_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _BY_EXT
