"""GR.S2 rider — docsync `_check_symbols` gets a real graph-backed resolver.

Grounding: today `_check_symbols`'s `resolve` predicate is never injected, so the symbol-
reference drift check is a silent no-op. This wires a real resolver through the new graph
client — using code-review-graph's AUTHORITATIVE existence signal (a `not_found` status) — and
records the disarm in AuditDegradation when a graph is present but can't authoritatively resolve.
Default (no graph wired) stays SILENT: no graph is the default, not a degrade.
"""

import unittest

from mokata.docsync import AuditDegradation, graph_symbol_resolver
from mokata.knowledge import GrepBackend, KnowledgeLayer


class _CrgResolveBackend:
    is_graph = True
    name = "code-review-graph"

    def __init__(self, present):
        self._present = set(present)

    def resolves(self, symbol):
        return symbol in self._present


class _RealGraphNoResolve:
    is_graph = True
    name = "serena"          # a real graph that doesn't expose an existence query


class TestResolverFactory(unittest.TestCase):
    def test_floor_returns_no_resolver_and_no_disarm(self):
        deg = AuditDegradation()
        layer = KnowledgeLayer(primary=GrepBackend(root="."))
        self.assertIsNone(graph_symbol_resolver(layer, deg))
        self.assertFalse(deg.degraded, "no graph wired is the DEFAULT, never a degrade")

    def test_crg_returns_working_resolver(self):
        layer = KnowledgeLayer(primary=_CrgResolveBackend(present=["mod.real"]))
        resolve = graph_symbol_resolver(layer)
        self.assertIsNotNone(resolve)
        self.assertTrue(resolve("mod.real"))
        self.assertFalse(resolve("mod.renamed_away"))

    def test_real_graph_without_resolve_records_disarm(self):
        deg = AuditDegradation()
        layer = KnowledgeLayer(primary=_RealGraphNoResolve())
        self.assertIsNone(graph_symbol_resolver(layer, deg))
        self.assertTrue(deg.degraded, "a present-but-incapable graph must record the disarm")


class TestCheckSymbolsUsesResolver(unittest.TestCase):
    def test_stale_symbol_flagged_via_resolver(self):
        from mokata.docsync import audit_text, gather_facts
        text = "See `mod.renamed_away` for details.\n\n    x = mod.renamed_away()\n"
        layer = KnowledgeLayer(primary=_CrgResolveBackend(present=["mod.real"]))
        resolve = graph_symbol_resolver(layer)
        findings = audit_text(text, facts=gather_facts(), resolve=resolve)
        self.assertTrue(any(f.checker == "symbol-ref" for f in findings))


class TestCrgClientResolves(unittest.TestCase):
    def test_resolves_maps_to_not_found_status(self):
        from mokata.knowledge.crg_client import CodeReviewGraphClient
        calls = []

        def call_tool(tool, params):
            calls.append((tool, params))
            return {"status": "not_found"} if params.get("target") == "gone" \
                else {"status": "ok", "results": []}
        c = CodeReviewGraphClient(name="code-review-graph", root=".", call_tool=call_tool)
        self.assertTrue(c.resolves("present", root="."))
        self.assertFalse(c.resolves("gone", root="."))


if __name__ == "__main__":
    unittest.main()
