"""GR.S4 — `about_code` write-time validation against the code graph.

A memory write carrying `about_code` anchors validates each anchor resolves in the graph.
An unresolvable anchor attaches a PROPOSAL-LEVEL warning (never a block, never an auto-write —
P2 untouched). Fail-OPEN: an anchor is flagged ONLY when the graph AUTHORITATIVELY says it does
not exist; no graph / no resolve capability / any error ⇒ treated as resolved (no false alarm).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import shutil
import tempfile
import unittest
from unittest import mock

from mokata.knowledge.about_code import AboutCodeCheck, check_about_code_anchors
from mokata.knowledge.query import GraphBackend, QueryResult


class _ResolvingBackend(GraphBackend):
    """A graph backend that authoritatively resolves a known symbol set."""

    is_graph = True
    supports_resolve = True

    def __init__(self, known, *, raise_on=None):
        self.name = "code-review-graph"
        self.root = "/x"
        self._known = set(known)
        self._raise_on = raise_on

    def query(self, kind, target, depth=1):
        return QueryResult(kind=kind, target=target, backend=self.name)

    def resolves(self, symbol):
        if self._raise_on and symbol == self._raise_on:
            raise RuntimeError("graph hiccup")
        return symbol in self._known


class _Layer:
    def __init__(self, primary):
        self.primary = primary
        self.fallback = None

    @property
    def uses_graph(self):
        return getattr(self.primary, "is_graph", False)


class TestCheckAboutCodeAnchors(unittest.TestCase):
    def test_resolvable_anchor_is_clean(self):
        layer = _Layer(_ResolvingBackend({"charge", "refund"}))
        out = check_about_code_anchors(["charge"], layer)
        self.assertEqual(out.unresolved, [])
        self.assertEqual(out.warning, "")

    def test_unresolvable_anchor_warns(self):
        layer = _Layer(_ResolvingBackend({"charge"}))
        out = check_about_code_anchors(["charge", "ghost_fn"], layer)
        self.assertEqual(out.unresolved, ["ghost_fn"])
        self.assertIn("ghost_fn", out.warning)
        self.assertIn("do not resolve", out.warning.lower().replace("does", "do"))

    def test_fail_open_without_resolve_capability(self):
        # An AST-floor / grep backend can't AUTHORITATIVELY resolve — never manufacture a warning.
        class _NoResolve(GraphBackend):
            is_graph = False
            name = "ast"

            def query(self, kind, target, depth=1):
                return QueryResult(kind=kind, target=target)

        out = check_about_code_anchors(["anything"], _Layer(_NoResolve()))
        self.assertEqual(out.unresolved, [])
        self.assertEqual(out.warning, "")

    def test_fail_open_on_resolve_error(self):
        layer = _Layer(_ResolvingBackend({"charge"}, raise_on="boom"))
        out = check_about_code_anchors(["charge", "boom"], layer)
        # a graph hiccup on 'boom' must NOT flag it (fail-open) — only 'charge' checked, clean.
        self.assertEqual(out.unresolved, [])

    def test_empty_and_none_are_clean(self):
        layer = _Layer(_ResolvingBackend(set()))
        self.assertEqual(check_about_code_anchors([], layer).warning, "")
        self.assertEqual(check_about_code_anchors(None, layer).warning, "")
        self.assertEqual(check_about_code_anchors(["x"], None).warning, "")

    def test_never_raises(self):
        # Even a hostile layer must not make validation raise.
        class _Bad:
            @property
            def primary(self):
                raise RuntimeError("nope")

        out = check_about_code_anchors(["x"], _Bad())
        self.assertIsInstance(out, AboutCodeCheck)


class TestRememberWiring(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        from mokata.init import init_repo
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda _: None)

    def test_resolvable_about_code_proposes_clean(self):
        from mokata.mcp.tools_write import remember
        out = remember(path=self.root, subject="pricing", value="charge in cents",
                       about_code=["charge"])
        self.assertEqual(out["status"], "proposed")
        self.assertNotIn("about_code_warning", out)
        self.assertFalse(out["committed"])

    def test_unresolvable_about_code_attaches_warning_never_blocks(self):
        warn = AboutCodeCheck(unresolved=["ghost_fn"],
                              warning="about_code anchor(s) do not resolve: ghost_fn")
        with mock.patch("mokata.knowledge.about_code.check_about_code_anchors",
                        return_value=warn):
            from mokata.mcp.tools_write import remember
            out = remember(path=self.root, subject="pricing", value="x",
                           about_code=["ghost_fn"])
        # A warning rides on the proposal — but it NEVER blocks (still a normal proposal, nothing
        # written, P2 intact).
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        self.assertIn("about_code_warning", out)
        self.assertIn("ghost_fn", out["about_code_warning"])

    def test_no_about_code_is_byte_identical(self):
        from mokata.mcp.tools_write import remember
        out = remember(path=self.root, subject="pricing", value="x")
        self.assertEqual(out["status"], "proposed")
        self.assertNotIn("about_code_warning", out)


if __name__ == "__main__":
    unittest.main()
