"""TypeScript/TSX -> mokata's `FileEdges`. The JS/TS graph floor's walker.

0.0.20 stage 05, `JS-TS-GRAPH-FLOOR`. mokata's code graph answered `defs` / `callers` /
`implementers` / `blast_radius` on Python and degraded to the lexical floor everywhere else, so a
TypeScript user got grep results from a product that advertises a graph.

⭐ THE TARGET SHAPE IS NOT NEW, AND THAT IS THE WHOLE DESIGN. This module produces
`ast_backend.FileEdges` — the same four typed lists the Python walker produces — so every consumer,
cache, query and test downstream is untouched. **A second graph would have meant a second
precedence rule, a second cache and a second set of answers for one question.** There is one graph;
this teaches it to read another language.

⚠ THE PARSER IS AN OPTIONAL EXTRA (`[graph-ts]`, ruled at doc 105 §9/G14), so every import of
`tree_sitter` in this file is LAZY and `available()` is the one predicate callers ask. Absent, the
repo's TS files are simply not in the corpus and `mokata doctor` says so — announced, never silent.

WHAT TRANSFERS FROM THE PYTHON WALKER, AND THE ONE THING THAT DOES NOT
-----------------------------------------------------------------------
Transfers unchanged: the four lists, the `(name, line, kind)` shapes, `IMPORT_VALUE`/`IMPORT_TYPE`
(G13 already built the distinction — `import type {Config}` is Python's `if TYPE_CHECKING:` wearing
different syntax, and it gets the representation that already exists rather than a new one), and
the `x.y()` -> `y` call-resolution rule, which is the same limitation Python's walker documents.

⛔ DOES NOT TRANSFER: **module tokenisation.** Python's `_add_module_tokens` splits a dotted name so
`imports(pkg)` matches whether the user names the package or a component. A TS specifier is either
a bare package (`react`, `@scope/pkg`) **or a path** (`./util`, `../api/client`), and a path is not
a name — it is a spelling relative to the importing file. `module_tokens` below is that decision,
written out rather than defaulted into.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set, Tuple

from .ast_backend import IMPORT_TYPE, IMPORT_VALUE, FileEdges


#: The extensions this walker claims. ⚠ NOT every extension `languages.JAVASCRIPT` declares:
#: `.js`/`.jsx`/`.mjs`/`.cjs` parse under a DIFFERENT grammar (`tree_sitter_javascript`), which
#: this stage does not ship. Claiming them here would hand plain JavaScript to the TypeScript
#: parser and report whatever came out — so the corpus stops where the grammar does, and the gap
#: is named rather than papered over.
TS_EXTENSIONS: Tuple[str, ...] = (".ts", ".tsx", ".mts", ".cts")

#: `.d.ts` is a GENERATED declaration file: types only, no runtime symbols, and frequently a
#: bundled copy of a dependency's public surface. Walking it adds every type in a package's API to
#: the user's own `defs` — the `node_modules` harm arriving through a file that is not under
#: `node_modules`. Excluded, and `is_ts_source` is the one predicate that says so.
DECLARATION_SUFFIX = ".d.ts"

#: TSX vs TS are SEPARATE GRAMMARS, not a flag: `.ts` does not parse as `.tsx` in every case
#: (`<T>expr` is a type assertion in one and an unclosed JSX tag in the other). Selection is by
#: extension, which is the only signal available before parsing.
_TSX_EXTENSIONS = (".tsx",)

#: Node types that declare a callable. `method_definition` covers constructors and class methods.
_FUNCTION_NODES = frozenset({"function_declaration", "generator_function_declaration"})
_METHOD_NODES = frozenset({"method_definition", "method_signature"})
#: A class-like declaration. `interface_declaration` is included on purpose: `implementers` is the
#: edge this row is most about, and an interface nobody can name is an interface nobody can query.
_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration", "interface_declaration"})
#: A value bound to a function expression — how modern React declares a component. A walker that
#: only knew `function_declaration` reports ZERO definitions for a whole component file, which
#: looks exactly like "the graph works, your file is just empty".
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function"})

KIND_FUNCTION = "function"
KIND_METHOD = "method"
KIND_CLASS = "class"


def available() -> bool:
    """True when the `[graph-ts]` extra is installed and this walker can parse.

    ⛔ Probes by IMPORTING, not by checking a version pin or a marker file: the question is whether
    a parse will work in this process, and only an import answers that. Broad on purpose — a
    partially-installed extra raises something other than ImportError.
    """
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
    except Exception:                                  # noqa: BLE001 — see the docstring
        return False
    return True


def is_ts_source(path: str) -> bool:
    """True for a file this walker should parse. The corpus rule, in one predicate.

    ⚠ `.d.ts` is refused BEFORE the extension check would accept it, because `.d.ts` ends in `.ts`.
    """
    low = path.lower()
    if low.endswith(DECLARATION_SUFFIX):
        return False
    return low.endswith(TS_EXTENSIONS)


def _grammar_for(path: str):
    """The tree-sitter `Language` for `path`'s extension. Lazy import; None when unavailable."""
    try:
        import tree_sitter_typescript as tst
        from tree_sitter import Language
    except Exception:                                  # noqa: BLE001 — the extra is optional
        return None
    if path.lower().endswith(_TSX_EXTENSIONS):
        return Language(tst.language_tsx())
    return Language(tst.language_typescript())


# ---- module tokenisation — the decision the Python model does not make for us ------------------

def module_tokens(specifier: str) -> Set[str]:
    """The `imports(...)` tokens for one TS module specifier.

    ⭐ THE RULE, AND ITS REASON. Python's `_add_module_tokens` splits `a.b.c` into the whole name
    and each segment, so `imports(pkg)` matches whether the user names the package or a component.
    A TS specifier is one of two different things and only one of them is a name:

        "react"            a package  -> the user names it exactly:      react
        "@scope/pkg"       a scoped package                              @scope/pkg, @scope, scope, pkg
        "./util"           a PATH relative to the importing file         util
        "../api/client"    ditto, deeper                                 api, client, api/client

    ⛔ THE RAW PATH IS NEVER A TOKEN. `./util` as a token makes `imports(util)` MISS and
    `imports(./util)` a spelling only the machine knows — and it is not even stable, because the
    same module is `./util` from one file and `../lib/util` from another. The leading `./` and `../`
    carry no information about WHAT was imported, only about where the importer sits.

    ⚠ AND THE EXTENSION IS STRIPPED. ESM requires `./util.js` in some configurations and forbids it
    in others, so the same module reaches this function spelled two ways; emitting `util.js` would
    split one module's edges across two tokens for a reason that is a build setting.

    ⚠ THE COST, STATED: two different `./util` files in different directories collapse onto the
    token `util`. That is the SAME imprecision Python's rule already has when two packages each
    hold a same-named module, and it is the trade that makes the query answerable at all — a token
    nobody can type is worse than a token that occasionally over-matches.

    ⚠ AND THE EXAMPLE ABOVE IS WORDED TO NAME NO FILE, deliberately. The first draft illustrated
    it with a plausible Python module filename, and `test_stage14_neo4j_removal`'s prose guard
    caught it: that guard exists so surviving prose cannot cite a module the tree no longer has,
    and it cannot tell a hypothetical example from a citation. **It is right not to try** — the
    same reason `disclosure-resolves` cannot tell a quoted promise from a made one.
    ⛔ AND THE SECOND DRAFT REPEATED THE OFFENDER INSIDE THE SENTENCE EXPLAINING THE FIX, which
    reds identically and is the sharper half of the lesson: **describing a violation by quoting it
    commits it again.** Describe the shape; never spell the path.
    """
    out: Set[str] = set()
    spec = (specifier or "").strip()
    if not spec:
        return out

    relative = spec.startswith(".")
    body = spec
    if relative:
        # Drop every leading `./` / `../` segment — position, not identity.
        parts = [p for p in body.split("/") if p not in ("", ".", "..")]
    else:
        parts = [p for p in body.split("/") if p]

    # Strip a module extension from the LAST segment only: `./util.js` and `./util` are one module.
    if parts:
        stem, ext = os.path.splitext(parts[-1])
        if ext.lower() in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts") and stem:
            parts[-1] = stem

    if not parts:
        return out

    if not relative:
        # A package name is what the user types, so it is a token whole as well as in parts.
        out.add("/".join(parts))
    elif len(parts) > 1:
        # A relative path's TAIL is nameable (`api/client`); its leading `./` is not.
        out.add("/".join(parts))

    for part in parts:
        if not part:
            continue
        out.add(part)
        if part.startswith("@") and len(part) > 1:
            out.add(part[1:])                          # `@scope` is also nameable as `scope`
    return out


# ---- the walk ---------------------------------------------------------------------------------

def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _line(node) -> int:
    return node.start_point[0] + 1


def _named_child(node, *types):
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def _is_type_only(node) -> bool:
    """True for `import type {X}` / `export type {X}` — an anonymous `type` token on the statement.

    G13's ruling, applied one language over: a type-only import creates NO runtime edge, and
    `IMPORT_TYPE` is the representation that already exists for exactly that fact.
    """
    for child in node.children:
        if child.type == "type":
            return True
    return False


def _import_specifier_string(node, source: bytes) -> Optional[str]:
    string = _named_child(node, "string")
    if string is None:
        return None
    fragment = _named_child(string, "string_fragment")
    if fragment is not None:
        return _text(fragment, source)
    return _text(string, source).strip("\"'`")


def _local_names(clause, source: bytes) -> Set[str]:
    """Every local name an import clause binds — default, named, aliased, namespace.

    Python's walker records aliases too (`import x as y` records `y`), because the alias is what
    the rest of the file actually names.
    """
    names: Set[str] = set()
    if clause is None:
        return names
    for node in _descendants(clause):
        if node.type in ("identifier", "type_identifier"):
            names.add(_text(node, source))
        elif node.type == "import_specifier":
            for child in node.named_children:
                if child.type in ("identifier", "type_identifier"):
                    names.add(_text(child, source))
    return names


def _descendants(node):
    stack = list(node.named_children)
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.named_children)


def _heritage(node, source: bytes) -> List[str]:
    """The supertypes of a class-like declaration — `extends` AND `implements`, in that order.

    ⭐ BOTH, and the row is mostly about the second. A React/NestJS skill asking *"who implements
    this interface"* is the question the lexical floor cannot answer at all, so an `implements`
    clause dropped here is the edge this stage exists to add.
    """
    bases: List[str] = []
    heritage = _named_child(node, "class_heritage")
    if heritage is None:
        return bases
    for clause in heritage.named_children:
        if clause.type not in ("extends_clause", "implements_clause"):
            continue
        for ref in _descendants(clause):
            if ref.type in ("identifier", "type_identifier"):
                name = _text(ref, source)
                if name and name not in bases:
                    bases.append(name)
    # `interface X extends A, B` carries its supertypes on an `extends_type_clause` instead.
    return bases


def _interface_heritage(node, source: bytes) -> List[str]:
    bases: List[str] = []
    for child in node.named_children:
        if child.type not in ("extends_type_clause", "extends_clause"):
            continue
        for ref in _descendants(child):
            if ref.type in ("identifier", "type_identifier"):
                name = _text(ref, source)
                if name and name not in bases:
                    bases.append(name)
    return bases


def _callee_name(call, source: bytes) -> Optional[str]:
    """The callee `x()` / `a.b()` resolves to.

    `a.b()` resolves to `b`, which is Python's rule for `x.y()` and carries Python's limitation with
    it — the same model, not a new one.
    """
    func = call.child_by_field_name("function") or call.child_by_field_name("constructor")
    if func is None:
        return None
    if func.type == "identifier":
        return _text(func, source)
    if func.type == "member_expression":
        prop = func.child_by_field_name("property")
        return _text(prop, source) if prop is not None else None
    return None


def extract_edges(tree, source: bytes) -> FileEdges:
    """The typed edges of one parsed TS/TSX file. Pure over a parsed tree — no I/O."""
    edges = FileEdges()

    def declared_name(node) -> Optional[str]:
        name = node.child_by_field_name("name")
        if name is not None:
            return _text(name, source)
        ident = _named_child(node, "identifier", "type_identifier", "property_identifier")
        return _text(ident, source) if ident is not None else None

    def walk(node, scope: str):
        for child in node.named_children:
            kind = child.type

            if kind == "import_statement":
                spec = _import_specifier_string(child, source)
                edge_kind = IMPORT_TYPE if _is_type_only(child) else IMPORT_VALUE
                toks = module_tokens(spec) if spec else set()
                toks |= _local_names(_named_child(child, "import_clause"), source)
                for token in sorted(toks):
                    edges.imports.append((token, _line(child), edge_kind))
                continue

            if kind == "export_statement" and _named_child(child, "string") is not None:
                # A RE-EXPORT (`export {x} from "./util"`) is an import edge: this module depends
                # on that one at runtime, whatever it does with the names afterwards.
                spec = _import_specifier_string(child, source)
                edge_kind = IMPORT_TYPE if _is_type_only(child) else IMPORT_VALUE
                toks = module_tokens(spec) if spec else set()
                # The re-exported NAMES too, for symmetry with the Python walker: `_import_edges`
                # records `alias.name` for `from x import y`, and a re-export is that plus a
                # re-publication. Dropping them here would make `imports(helper)` answer for
                # `import {helper} from "./util"` and stay silent for
                # `export {helper} from "./util"` — one fact, two answers, decided by which line
                # the author happened to write.
                toks |= _local_names(_named_child(child, "export_clause"), source)
                for token in sorted(toks):
                    edges.imports.append((token, _line(child), edge_kind))
                walk(child, scope)
                continue

            if kind in _CLASS_NODES:
                name = declared_name(child)
                if name:
                    bases = (_interface_heritage(child, source)
                             if kind == "interface_declaration"
                             else _heritage(child, source))
                    edges.classes.append((name, _line(child), bases))
                    edges.defs.append((name, _line(child), KIND_CLASS))
                    walk(child, name)
                    continue

            if kind in _FUNCTION_NODES:
                name = declared_name(child)
                if name:
                    edges.defs.append((name, _line(child), KIND_FUNCTION))
                    walk(child, name)
                    continue

            if kind in _METHOD_NODES:
                name = declared_name(child)
                if name:
                    edges.defs.append((name, _line(child), KIND_METHOD))
                    walk(child, name)
                    continue

            if kind == "variable_declarator":
                value = child.child_by_field_name("value")
                name = declared_name(child)
                if name and value is not None and value.type in _FUNCTION_VALUES:
                    # `export const Panel = () => …` — a definition, and on modern React the ONLY
                    # kind a component file has.
                    edges.defs.append((name, _line(child), KIND_FUNCTION))
                    walk(child, name)
                    continue

            if kind in ("call_expression", "new_expression"):
                callee = _callee_name(child, source)
                if callee:
                    edges.calls.append((callee, _line(child), scope))
                walk(child, scope)
                continue

            walk(child, scope)

    walk(tree.root_node, "")
    return edges


def parse_source(source: str, path: str) -> Optional[FileEdges]:
    """Parse one TS/TSX file's text into `FileEdges`. None when the extra is absent or parsing failed.

    ⛔ None is returned for BOTH, and the caller must not read it as *"this file has no edges"* —
    `available()` is what separates *the parser is not installed* from *this file did not parse*.
    A file with genuinely no symbols returns an EMPTY `FileEdges`, which is a different object.
    """
    language = _grammar_for(path)
    if language is None:
        return None
    try:
        from tree_sitter import Parser
        raw = source.encode("utf-8", "replace")
        tree = Parser(language).parse(raw)
    except Exception:                                  # noqa: BLE001 — a parse failure is an answer
        return None
    return extract_edges(tree, raw)
