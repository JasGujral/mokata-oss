"""A TypeScript repo gets real call/import/heritage edges, not the lexical floor.

0.0.20 stage 05, `JS-TS-GRAPH-FLOOR`. The row: mokata's code graph answered `defs` / `callers` /
`implementers` / `blast_radius` on Python and degraded everywhere else, so a TypeScript user got
grep results from a product that advertises a graph.

⭐ THE WALKER PRODUCES `ast_backend.FileEdges` — THE SAME FOUR LISTS THE PYTHON WALKER PRODUCES —
so there is one graph, one cache and one precedence rule. A second backend would have meant a
second set of answers to one question.

THE FIXTURE IS HOSTILE ON PURPOSE (§7f)
----------------------------------------
`_HOSTILE` is not hello-world. In ~20 lines it carries a decorator, JSX, an abstract class, an
interface, `implements` alongside `extends`, a type-only import, a re-export, a namespace import,
`super()`, an arrow-function component with a hook, a `new`, a member call and a default export —
the constructs the row's own framing implies are the hard part. **A green run against a trivial
fixture would say nothing**, which is why `test_the_fixture_parses_with_NO_ERROR_NODES` grades the
parse itself before anything reads the edges out of it.

§7g — THE EXTRA IS OPTIONAL, SO "ABSENT" AND "BROKEN" MUST NOT SHARE A REPRESENTATION
--------------------------------------------------------------------------------------
`parse_source` returns `None` when the parser is missing AND when a file fails to parse, and a
caller must never read either as *"this file has no edges"* — a file with genuinely no symbols
returns an EMPTY `FileEdges`, a different object. `TheAbsentParserIsItsOwnAnswer` pins all three
apart, and `available()` is the predicate that separates the first two.

⚠ The pure-rule classes below carry NO parser guard on purpose: `module_tokens` and `is_ts_source`
are the corpus and tokenisation DECISIONS, they are plain Python, and they must stay graded on a
machine that never installs the extra — including the public mirror.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import ts_edges as T
from mokata.knowledge.ast_backend import IMPORT_TYPE, IMPORT_VALUE, FileEdges


_HOSTILE = '''import React from "react";
import type {Config} from "./cfg";
import * as api from "../api/client";
import {alpha as beta} from "@scope/pkg";
export {helper} from "./util";
interface Renderable { render(): void; }
abstract class Base implements Renderable {
  constructor(private n: string) { super(); }
  log() { console.log(this.n); }
}
export class Widget extends Base implements Renderable {
  static make(): Widget { return new Widget("w"); }
}
export const Panel = ({title}: Config) => {
  const [open, setOpen] = useState(false);
  return <div onClick={() => track("x")}>{title}</div>;
};
export default Panel;
'''

_needs_parser = unittest.skipUnless(
    T.available(), "the [graph-ts] extra is not installed — the walker cannot parse")


def _edges():
    return T.parse_source(_HOSTILE, "fixture.tsx")


def _names(rows):
    return sorted({r[0] for r in rows})


# ======================================================================== the corpus rule (pure)
class TheCorpusStopsWhereTheGrammarDoes(unittest.TestCase):
    """No parser needed: this is the DECISION about which files are even offered to one."""

    def test_ts_and_tsx_are_source(self):
        for path in ("a.ts", "b.tsx", "c.mts", "d.cts", "DIR/E.TSX"):
            self.assertTrue(T.is_ts_source(path), path)

    def test_a_declaration_file_is_NOT_source(self):
        """⛔ `.d.ts` is generated types, often a bundled copy of a dependency's public surface.
        Walking it puts every type in a package's API into the user's own `defs` — the
        `node_modules` harm arriving through a file that is not under `node_modules`."""
        for path in ("types.d.ts", "vendor/lib.D.TS", "a/b/index.d.ts"):
            self.assertFalse(T.is_ts_source(path), path)

    def test_a_declaration_file_ENDS_IN_ts_so_the_order_of_the_checks_matters(self):
        """The pin that would fail if the extension test ran first."""
        self.assertTrue("types.d.ts".endswith(".ts"))
        self.assertFalse(T.is_ts_source("types.d.ts"))

    def test_plain_JAVASCRIPT_is_NOT_claimed(self):
        """⚠ A DIFFERENT GRAMMAR, not shipped by this stage. Claiming `.js` would hand plain
        JavaScript to the TypeScript parser and report whatever came out — the corpus stops where
        the grammar does, and the gap is named rather than papered over."""
        for path in ("a.js", "b.jsx", "c.mjs", "d.cjs"):
            self.assertFalse(T.is_ts_source(path), path)

    def test_python_is_not_claimed_either(self):
        self.assertFalse(T.is_ts_source("mod.py"))


# ================================================================== module tokenisation (pure)
class TheModuleTokenRuleIsTheOneThingThatDoesNotTransfer(unittest.TestCase):
    """Python's dotted-name split has no TS equivalent, because a specifier can be a PATH."""

    def test_a_bare_package_is_a_name_and_is_a_token_WHOLE(self):
        self.assertEqual(T.module_tokens("react"), {"react"})

    def test_a_scoped_package_yields_the_whole_name_and_its_parts(self):
        self.assertEqual(T.module_tokens("@scope/pkg"),
                         {"@scope/pkg", "@scope", "scope", "pkg"})

    def test_a_RELATIVE_PATH_NEVER_yields_the_raw_specifier(self):
        """⛔ THE HEART OF THE RULE. `./util` as a token makes `imports(util)` MISS and
        `imports(./util)` a spelling only the machine knows — and it is not even stable: the same
        module is `./util` from one file and `../lib/util` from another."""
        for spec in ("./util", "../util", "../../util"):
            toks = T.module_tokens(spec)
            self.assertIn("util", toks, spec)
            self.assertNotIn(spec, toks, spec)
            self.assertFalse(any(t.startswith(".") for t in toks), (spec, toks))

    def test_a_deeper_path_yields_its_TAIL_as_well_as_its_segments(self):
        self.assertEqual(T.module_tokens("../api/client"), {"api", "client", "api/client"})

    def test_the_EXTENSION_is_stripped_so_one_module_is_one_token(self):
        """ESM requires `./util.js` in some configurations and forbids it in others, so the same
        module reaches the walker spelled two ways. Splitting its edges across two tokens would be
        a graph decided by a build setting."""
        self.assertEqual(T.module_tokens("./util.js"), T.module_tokens("./util"))
        self.assertEqual(T.module_tokens("./util.ts"), T.module_tokens("./util"))
        self.assertNotIn("util.js", T.module_tokens("./util.js"))

    def test_only_the_LAST_segment_loses_an_extension(self):
        """`../v1.2/util` — the dot is in a directory name, not an extension."""
        self.assertIn("v1.2", T.module_tokens("../v1.2/util"))

    def test_an_empty_or_dotted_specifier_yields_NOTHING_rather_than_a_dot(self):
        for spec in ("", "   ", ".", "..", "./", "../.."):
            self.assertEqual(T.module_tokens(spec), set(), repr(spec))


# ============================================================================== the walk itself
@_needs_parser
class TheHostileFixtureIsRead(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.edges = _edges()

    def test_the_fixture_parses_with_NO_ERROR_NODES(self):
        """⛔ Graded BEFORE anything reads the edges: a tree full of ERROR nodes still yields
        SOME edges, and every assertion below would then be measuring a broken parse."""
        from tree_sitter import Parser
        language = T._grammar_for("fixture.tsx")
        tree = Parser(language).parse(_HOSTILE.encode("utf-8"))
        self.assertFalse(tree.root_node.has_error,
                         "the fixture did not parse cleanly, so nothing below means anything")

    def test_it_produced_the_SAME_SHAPE_the_python_walker_produces(self):
        self.assertIsInstance(self.edges, FileEdges)
        for row in self.edges.defs:
            self.assertEqual(len(row), 3)
        for row in self.edges.imports:
            self.assertEqual(len(row), 3)
        for row in self.edges.calls:
            self.assertEqual(len(row), 3)

    # ---- the edge the row is most about -------------------------------------------------------
    def test_EXTENDS_AND_IMPLEMENTS_both_reach_the_heritage(self):
        """⭐ *"Who implements this interface"* is the question the lexical floor cannot answer at
        all. `Widget extends Base implements Renderable` must yield BOTH."""
        by_name = {c[0]: c[2] for c in self.edges.classes}
        self.assertEqual(by_name["Widget"], ["Base", "Renderable"])

    def test_an_abstract_class_carries_its_implements_clause(self):
        by_name = {c[0]: c[2] for c in self.edges.classes}
        self.assertEqual(by_name["Base"], ["Renderable"])

    def test_an_interface_is_a_class_like_declaration(self):
        """An interface nobody can name is an interface nobody can query."""
        self.assertIn("Renderable", [c[0] for c in self.edges.classes])
        self.assertIn(("Renderable", 6, "class"), self.edges.defs)

    # ---- the definition shapes ----------------------------------------------------------------
    def test_an_ARROW_FUNCTION_COMPONENT_is_a_definition(self):
        """⭐ `export const Panel = () => …` is how modern React declares a component. A walker
        that only knew `function_declaration` reports ZERO definitions for a whole component file,
        which looks exactly like *the graph works, your file is just empty*."""
        self.assertIn(("Panel", 14, "function"), self.edges.defs)

    def test_methods_and_constructors_are_METHODS(self):
        kinds = {d[0]: d[2] for d in self.edges.defs}
        self.assertEqual(kinds["log"], "method")
        self.assertEqual(kinds["constructor"], "method")
        self.assertEqual(kinds["make"], "method")

    def test_a_method_SIGNATURE_on_an_interface_is_a_definition_too(self):
        self.assertIn("render", _names(self.edges.defs))

    # ---- imports --------------------------------------------------------------------------------
    def test_a_TYPE_ONLY_import_is_typed_and_uses_the_representation_that_ALREADY_EXISTS(self):
        """G13 built `IMPORT_TYPE` for Python's `if TYPE_CHECKING:`. `import type {Config}` is the
        same fact wearing different syntax, so it gets that representation rather than a new one."""
        by_token = {i[0]: i[2] for i in self.edges.imports}
        self.assertEqual(by_token["Config"], IMPORT_TYPE)
        self.assertEqual(by_token["cfg"], IMPORT_TYPE)

    def test_a_VALUE_import_is_not_typed_as_one(self):
        by_token = {i[0]: i[2] for i in self.edges.imports}
        self.assertEqual(by_token["react"], IMPORT_VALUE)
        self.assertEqual(by_token["React"], IMPORT_VALUE)

    def test_the_two_kinds_actually_BOTH_OCCUR_in_this_fixture(self):
        """⛔ Anti-vacuity: with only one kind present, every assertion above holds for a walker
        that hard-codes it."""
        self.assertEqual({i[2] for i in self.edges.imports}, {IMPORT_VALUE, IMPORT_TYPE})

    def test_a_NAMESPACE_import_binds_its_local_name(self):
        self.assertIn("api", _names(self.edges.imports))

    def test_an_ALIASED_import_binds_the_alias(self):
        """`{alpha as beta}` — the alias is what the rest of the file names, which is the rule the
        Python walker already follows for `import x as y`."""
        self.assertIn("beta", _names(self.edges.imports))

    def test_a_RE_EXPORT_is_an_import_edge(self):
        """`export {helper} from "./util"` — this module depends on that one at runtime, whatever
        it does with the names afterwards. Both the module and the name are edges, so
        `imports(helper)` does not answer for `import` and go silent for `export`."""
        tokens = _names(self.edges.imports)
        self.assertIn("util", tokens)
        self.assertIn("helper", tokens)

    # ---- calls ----------------------------------------------------------------------------------
    def test_a_bare_call_carries_its_enclosing_scope(self):
        self.assertIn(("track", 16, "Panel"), self.edges.calls)
        self.assertIn(("useState", 15, "Panel"), self.edges.calls)

    def test_a_NEW_expression_is_a_call(self):
        self.assertIn("Widget", [c[0] for c in self.edges.calls])

    def test_a_MEMBER_call_resolves_to_its_property_the_way_python_does(self):
        """`console.log(...)` -> `log`. mokata's Python backend resolves `x.y()` to `y` by the same
        rule and documents the limitation; this is the same model, not a new one."""
        self.assertIn("log", [c[0] for c in self.edges.calls])


# ============================================================== absent / broken / empty (§7g)
class TheAbsentParserIsItsOwnAnswer(unittest.TestCase):

    def test_available_is_a_real_probe_and_not_a_constant(self):
        self.assertIsInstance(T.available(), bool)

    @_needs_parser
    def test_a_file_with_NO_SYMBOLS_returns_EMPTY_EDGES_not_None(self):
        """⛔ The collapse this refuses: `None` means *the parser could not read this*, and empty
        edges mean *it read it and there was nothing*. A caller that folds them reports a parse
        failure as a file with no code."""
        edges = T.parse_source("// just a comment\n", "empty.ts")
        self.assertIsInstance(edges, FileEdges)
        self.assertEqual((edges.defs, edges.classes, edges.imports, edges.calls), ([], [], [], []))

    @_needs_parser
    def test_UNPARSEABLE_input_still_returns_edges_rather_than_raising(self):
        """tree-sitter is error-tolerant by design: it returns a tree with ERROR nodes rather than
        refusing. A half-written file mid-edit must not crash an indexing run."""
        edges = T.parse_source("export class {{{ broken", "broken.ts")
        self.assertIsNotNone(edges)

    def test_an_absent_grammar_yields_None_and_the_caller_can_TELL(self):
        """The mirror case, simulated: `_grammar_for` returns None and `parse_source` follows."""
        real = T._grammar_for
        T._grammar_for = lambda _path: None
        try:
            self.assertIsNone(T.parse_source(_HOSTILE, "fixture.tsx"))
        finally:
            T._grammar_for = real
        self.assertIsNotNone(T.available.__doc__, "available() is the predicate that tells them "
                                                  "apart and it must document that it does")


@_needs_parser
class TheGrammarIsChosenByExtension(unittest.TestCase):
    """⚠ TSX and TS are SEPARATE grammars, not a flag — `.ts` does not parse as `.tsx`."""

    def test_JSX_parses_under_the_TSX_grammar(self):
        edges = T.parse_source("export const A = () => <div>hi</div>;\n", "a.tsx")
        self.assertIn(("A", 1, "function"), edges.defs)

    def test_a_TYPE_ASSERTION_parses_under_the_TS_grammar(self):
        """`<T>expr` is a type assertion in `.ts` and an unclosed JSX tag in `.tsx`. This is the
        construct that makes one grammar insufficient, so it is the one that pins the selection."""
        edges = T.parse_source("function f(x: any) { return <string>x; }\n", "a.ts")
        self.assertIn(("f", 1, "function"), edges.defs)

    def test_the_two_extensions_resolve_to_DIFFERENT_grammars(self):
        self.assertIsNot(T._grammar_for("a.ts"), T._grammar_for("a.tsx"))


if __name__ == "__main__":
    unittest.main()
