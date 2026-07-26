"""GR.S2 (a)+(b)+(c)+(d) — manifest parity: `ast` is a first-class routed provider.

The 0.0.13 drift: `standard` wired `code_graph: ["ripgrep","grep"]` while GR.S1's AST floor
was the thing that actually answered structural queries on Python repos — selected by a
hardcoded `layer._has_python` branch OUTSIDE the router chain, so no manifest named it. This
stage makes `ast` a real `TOOL_CATALOG` provider (builtin, detected by python-files-present),
puts it in the `code_graph` fallback chain, updates the `standard` profile, and moves the AST
selection into the router chain proper — so the committed manifest names the chain that answers.

Deliverable -> test map:
  (a) `ast` a first-class TOOL_CATALOG provider ...... TestAstProvider
  (b) CAPABILITY_FALLBACKS.code_graph gains `ast` ..... TestCapabilityChain
  (c) `standard` profile chain names the answerer ..... TestStandardProfile + test_gr_s2_regression
  (d) AST selection via the router chain proper ....... TestRouterChainSelection
  python-files-present detection strategy ............. TestPythonFilesDetect
  negatives: zero-py byte-identical .................. TestZeroPyByteIdentical
"""

import os
import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import AstBackend, GrepBackend, KnowledgeLayer
from mokata.manifest import Manifest
from mokata.profiles import (
    CAPABILITY_FALLBACKS,
    TOOL_CATALOG,
    build_manifest_data,
)
from mokata.router import Router


def _router(profile, root, overrides=None):
    m = Manifest.from_dict(build_manifest_data(profile, "0.1.0"))
    det = Detector(overrides=overrides or {})
    return Router(m, det)


class TestAstProvider(unittest.TestCase):
    """(a) `ast` is a first-class, builtin provider the manifest can NAME."""

    def test_ast_in_tool_catalog_as_builtin(self):
        self.assertIn("ast", TOOL_CATALOG)
        entry = TOOL_CATALOG["ast"]
        self.assertEqual(entry["provides"], "code_graph")
        self.assertEqual(entry["kind"], "builtin")

    def test_ast_detected_by_python_files_present(self):
        self.assertEqual(TOOL_CATALOG["ast"]["detect"]["type"], "python_files")


class TestCapabilityChain(unittest.TestCase):
    """(b) the code_graph fallback chain is code-review-graph -> serena -> ast -> ripgrep -> grep."""

    def test_ast_third_in_the_chain(self):
        chain = CAPABILITY_FALLBACKS["code_graph"]["fallback"]
        self.assertEqual(
            chain, ["code-review-graph", "serena", "ast", "ripgrep", "grep"]
        )


class TestStandardProfile(unittest.TestCase):
    """(c) the `standard` profile names the chain that actually answers."""

    def test_standard_names_ast(self):
        data = build_manifest_data("standard", "0.1.0")
        chain = data["capabilities"]["code_graph"]["fallback"]
        self.assertIn("ast", chain)
        # ast is preferred over the lexical floor (it answers degraded=False on Python).
        self.assertLess(chain.index("ast"), chain.index("grep"))

    def test_standard_manifest_carries_the_ast_tool_entry(self):
        data = build_manifest_data("standard", "0.1.0")
        self.assertIn("ast", data["tools"])
        self.assertEqual(data["tools"]["ast"]["kind"], "builtin")


class TestPythonFilesDetect(unittest.TestCase):
    """python-files-present detection: `ast` is present iff the repo holds a .py file."""

    def test_present_when_python_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            det = Detector(root=d)
            self.assertTrue(det.is_present("ast", TOOL_CATALOG["ast"]))

    def test_absent_when_no_python_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("function f(){}\n")
            det = Detector(root=d)
            self.assertFalse(det.is_present("ast", TOOL_CATALOG["ast"]))

    def test_dotfiles_skipped_like_the_walks(self):
        with tempfile.TemporaryDirectory() as d:
            hidden = os.path.join(d, ".venv")
            os.makedirs(hidden)
            with open(os.path.join(hidden, "buried.py"), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            det = Detector(root=d)
            self.assertFalse(det.is_present("ast", TOOL_CATALOG["ast"]))


class TestRouterChainSelection(unittest.TestCase):
    """(d) AST is selected THROUGH the router chain — `router.resolve('code_graph')`
    returns `ast`, and the layer builds an AstBackend from that resolution."""

    def test_router_resolves_ast_on_python_repo(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            # no real graph tool present; ripgrep absent -> the chain lands on `ast`.
            router = _router("standard", d, overrides={"ripgrep": False})
            router.detector.root = d
            res = router.resolve("code_graph")
            self.assertEqual(res.tool, "ast")
            self.assertTrue(res.available)

    def test_layer_builds_astbackend_from_router_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            router = _router("standard", d, overrides={"ripgrep": False})
            layer = KnowledgeLayer.from_router(router, root=d)
            self.assertIsInstance(layer.primary, AstBackend)
            self.assertEqual(layer.backend_name, "ast")
            self.assertFalse(layer.uses_graph)   # AST is a floor, not the adopted graph

    def test_no_hardcoded_has_python_branch(self):
        """(d) the selection must come from the router chain, not a private _has_python
        branch bolted onto select_backends."""
        from mokata.knowledge import layer as layer_mod
        self.assertFalse(
            hasattr(layer_mod, "_has_python"),
            "AST selection must move into the router chain (no _has_python branch)",
        )


class TestZeroPyByteIdentical(unittest.TestCase):
    """Negative: a repo with zero .py files resolves to the grep floor exactly as before."""

    def test_zero_py_is_grep_floor(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("function compute(){ return helper(); }\n")
            router = _router("standard", d, overrides={"ripgrep": False})
            layer = KnowledgeLayer.from_router(router, root=d)
            self.assertIsInstance(layer.primary, GrepBackend)
            self.assertEqual(layer.backend_name, "grep")

    def test_zero_py_byte_identical_to_direct_grep(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("function main(){ return compute(); }\n"
                         "function compute(){ return 1; }\n")
            router = _router("standard", d, overrides={"ripgrep": False})
            via_layer = KnowledgeLayer.from_router(router, root=d).callers("compute")
            direct = GrepBackend(root=d, name="grep").query("callers", "compute")
            self.assertEqual(via_layer.to_dict(), direct.to_dict())


class GrS2Regression(unittest.TestCase):
    """test_gr_s2_regression — the drift bug: the `standard` manifest must NAME the chain that
    actually answers structural queries. On old code `standard` wired only ["ripgrep","grep"]
    while the AST floor answered — the manifest lied. This fails on the old profiles."""

    def test_standard_profile_names_the_chain_that_answers(self):
        data = build_manifest_data("standard", "0.1.0")
        chain = data["capabilities"]["code_graph"]["fallback"]
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            router = Manifest.from_dict(data)
            r = Router(router, Detector(overrides={"ripgrep": False}))
            layer = KnowledgeLayer.from_router(r, root=d)
            answerer = layer.backend_name          # what ACTUALLY answers
        # the manifest's declared chain must name the real answerer (no silent drift).
        self.assertIn(answerer, chain,
                      f"manifest chain {chain} does not name the real answerer {answerer!r}")


if __name__ == "__main__":
    unittest.main()
