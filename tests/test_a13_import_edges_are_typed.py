"""AN IMPORT EDGE CARRIES ITS KIND — a contract dependency and a runtime one are different facts.
(0.0.20, doc 105 §9 G13.)

`from x import Y` inside `if TYPE_CHECKING:` creates **no runtime dependency** — the module is
never imported at run time — and it creates a **real contract dependency**: change `Y`'s shape and
this file stops type-checking.

⛔ **BOTH OF THE OBVIOUS ANSWERS ARE WRONG, WHICH IS THE TELL THAT THE SHAPE WAS WRONG.** Emitting
it as an ordinary import asserts a runtime edge that does not exist. Dropping it answers *"nothing
depends on this"* about a type other modules are written against — and in a large codebase that is
not an under-report, it is a **confident wrong answer**, which is the class this release is about.

So `FileEdges.imports` carries a kind (§7g), `imports(x)` answers **both** by default — the honest
superset, so no query loses a site — and a caller that needs only runtime edges can filter, which
it could not do while the two shared a representation.

⭐ THE RULING WAS MADE FOR TYPESCRIPT AND PYTHON TURNED OUT TO NEED IT TOO. G13 was opened by the
stage 05 costing, about `import type`. `if TYPE_CHECKING:` is the same fact in the language the
floor already parses, and the floor had been calling those runtime edges for its whole life.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.knowledge import ast_backend as A


def edges(src: str):
    return A.extract_edges(A.parse_source(src))


def kinds(src: str):
    """`{token: kind}` for a source, so a case reads as the claim it is making."""
    return {t: k for t, _line, k in edges(src).imports}


class AnOrdinaryImportIsARuntimeEdge(unittest.TestCase):

    def test_a_plain_import(self):
        self.assertEqual(A.IMPORT_VALUE, kinds("import os\n")["os"])

    def test_a_from_import(self):
        self.assertEqual(A.IMPORT_VALUE, kinds("from pkg import thing\n")["thing"])

    def test_an_import_inside_a_FUNCTION_is_still_a_runtime_edge(self):
        """A deferred import runs — later, but it runs. The kind is about EXECUTION, not position,
        and a rule keyed on indentation would have got this wrong."""
        self.assertEqual(A.IMPORT_VALUE, kinds("def f():\n    import json\n")["json"])


class ATypeCheckingImportIsAContractEdge(unittest.TestCase):

    SRC = ("from typing import TYPE_CHECKING\n"
           "if TYPE_CHECKING:\n"
           "    from mypkg.contracts import Widget\n")

    def test_the_guarded_import_is_a_TYPE_edge(self):
        self.assertEqual(A.IMPORT_TYPE, kinds(self.SRC)["Widget"])

    def test_and_so_is_every_module_token_it_produced(self):
        """The token expansion (`mypkg`, `mypkg.contracts`, …) exists so `imports(<module>)`
        matches whichever name a user asks by. Every one of them describes the SAME edge, so a
        kind that varied across them would let the answer depend on how the question was spelled."""
        k = kinds(self.SRC)
        for token in ("mypkg", "mypkg.contracts", "mypkg.contracts.Widget", "contracts"):
            self.assertEqual(A.IMPORT_TYPE, k[token], token)

    def test_the_ELSE_branch_is_NOT_a_type_edge(self):
        """⭐ The half a naive implementation gets wrong. `else:` of a `TYPE_CHECKING` guard is the
        RUNTIME branch — it is what actually executes — so an implementation that marked the whole
        statement would invert the fact for exactly the pattern the idiom exists to express."""
        k = kinds(self.SRC + "else:\n    import json\n")
        self.assertEqual(A.IMPORT_VALUE, k["json"])
        self.assertEqual(A.IMPORT_TYPE, k["Widget"])

    def test_the_dotted_spelling_is_recognised_too(self):
        self.assertEqual(A.IMPORT_TYPE,
                         kinds("import typing\n"
                               "if typing.TYPE_CHECKING:\n"
                               "    from pkg import Thing\n")["Thing"])

    def test_an_ordinary_if_does_NOT_make_its_imports_type_only(self):
        """The control. A detector that treated any `if` as a type guard would mark every
        conditional import — a real and common runtime pattern — as creating no runtime edge."""
        self.assertEqual(A.IMPORT_VALUE,
                         kinds("import sys\n"
                               "if sys.version_info >= (3, 11):\n"
                               "    import tomllib\n")["tomllib"])


class TheQueryStILLAnswersBoth(unittest.TestCase):
    """⛔ The kind is for a caller that wants to FILTER. It may not silently narrow the default
    answer, or G13 would have removed edges from `blast_radius` in the name of typing them."""

    def test_imports_of_a_type_only_module_are_still_found(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "consumer.py"), "w", encoding="utf-8") as fh:
                fh.write("from typing import TYPE_CHECKING\n"
                         "if TYPE_CHECKING:\n"
                         "    from contracts import Widget\n")
            backend = A.AstBackend(d)
            result = backend.query("imports", "Widget")
        self.assertTrue(result.references,
                        "a type-only import vanished from `imports` — the query narrowed instead "
                        "of the record widening, which is the opposite of the ruling")


class TheCacheCannotHydrateTheOldShape(unittest.TestCase):
    """§7d — pre-1.0 this is a RE-DERIVE, not a migration: the cache is derived data and rebuilding
    it costs one walk. What must not happen is a two-tuple row reaching a reader expecting three."""

    def test_the_schema_version_moved(self):
        self.assertGreaterEqual(A.CACHE_SCHEMA_VERSION, 2,
                                "the cache version did not move with the shape, so a cache written "
                                "before the kind existed would be hydrated into the new reader")

    def test_a_two_element_row_is_DROPPED_rather_than_guessed(self):
        """⛔ Not defaulted to `value`. That would guess, and the guess is wrong for every
        TYPE_CHECKING import the old walker recorded — a silent wrong answer where a missing row
        is a re-parse."""
        hydrated = A.FileEdges.from_dict({"imports": [["os", 1], ["json", 2, A.IMPORT_VALUE]]})
        self.assertEqual([("json", 2, A.IMPORT_VALUE)], hydrated.imports)

    def test_a_round_trip_keeps_the_kind(self):
        original = edges("from typing import TYPE_CHECKING\n"
                         "if TYPE_CHECKING:\n"
                         "    import contracts\n")
        again = A.FileEdges.from_dict(original.to_dict())
        self.assertEqual(sorted(original.imports), sorted(again.imports))
