"""GRAPH-HINT (0.0.15 rider, doc 84 §5 / doc 90 rough edge) — the status hint names the
ACTUAL answering backend.

Before this stage `graph_guidance` branched on `layer.uses_graph` alone, which is False for
BOTH floors (`AstBackend.is_graph = False` is deliberate — a floor above grep, not a graph).
So the DEFAULT install GR.S3 made important — a CRG-less Python repo, answered structurally
and `degraded=False` by the embedded AST floor — was told "no codebase graph wired — running
on the grep floor". Both halves false (P16).

These tests pin the three-way branch, the wording MIRROR against `mokata graph status`, and
that every surface inherits the fix from the ONE function.

Licensed under the Apache License, Version 2.0.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.detect import Detector
from mokata.init import init_repo
from mokata.knowledge import graph_guidance


def _silent(_):
    pass


def _repo(d, *, graph_present, python):
    """A loaded surface with the graph tool present/absent deterministically.

    `python` decides which FLOOR answers: the `ast` provider is routed by the `python_files`
    detect strategy (profiles.py:50), so a repo holding at least one `.py` file resolves to
    the AST floor and a repo holding none falls through to the lexical grep floor.
    """
    init_repo(root=d, profile="full", assume_yes=True, out=_silent)
    if python:
        with open(os.path.join(d, "sample.py"), "w", encoding="utf-8") as fh:
            fh.write("def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n")
    overrides = {"code-review-graph": graph_present, "serena": False, "ripgrep": False}
    return Surface.load(d, detector=Detector(overrides=overrides, cache=False))


class TestGraphHintThreeWay(unittest.TestCase):
    def test_graph_hint_ast_floor(self):
        """The default CRG-less Python repo: names the AST floor, NEVER claims grep."""
        with tempfile.TemporaryDirectory() as d:
            hint = graph_guidance(_repo(d, graph_present=False, python=True))
            self.assertIn("floor 'ast'", hint)
            self.assertIn("embedded AST floor", hint)
            self.assertIn("structurally", hint)          # structural answering AFFIRMED
            # the two false halves of the old message are gone
            self.assertNotIn("no codebase graph wired", hint)
            self.assertNotIn("grep floor", hint)
            # still a HINT, not a status line (Stage 25 Part B's contract)
            self.assertIn("mokata graph adopt", hint)
            self.assertIn("--profile full", hint)

    def test_graph_hint_real_graph(self):
        """A wired graph: the Stage 25 text is unchanged, pinned verbatim."""
        with tempfile.TemporaryDirectory() as d:
            hint = graph_guidance(_repo(d, graph_present=True, python=True))
            self.assertEqual(
                hint,
                "code graph active (code-review-graph) — use `mokata query callers <sym>` / "
                "`callees <sym>` / `blast_radius <sym>` for structural queries.",
            )

    def test_graph_hint_grep(self):
        """A genuinely-grep config — a repo with no `.py` files, so the chain resolves past
        `ast` to the lexical floor. The old text remains CORRECT here and is kept."""
        with tempfile.TemporaryDirectory() as d:
            hint = graph_guidance(_repo(d, graph_present=False, python=False))
            self.assertIn("floor 'grep'", hint)
            self.assertIn("no codebase graph wired", hint)
            self.assertIn("lexical", hint)
            self.assertNotIn("embedded AST floor is answering", hint)
            self.assertIn("--profile full", hint)

    def test_grep_branch_is_genuinely_grep(self):
        """Proves the grep test above is not passing on a mislabelled backend."""
        from mokata.knowledge.grep_backend import GrepBackend
        from mokata.knowledge.layer import KnowledgeLayer
        with tempfile.TemporaryDirectory() as d:
            layer = KnowledgeLayer.from_surface(_repo(d, graph_present=False, python=False))
            self.assertIsInstance(layer.primary, GrepBackend)
            self.assertFalse(layer.uses_graph)

    def test_ast_branch_is_genuinely_the_ast_floor_and_not_a_graph(self):
        """The fix is the MESSAGE, not a reclassification: `is_graph`/`uses_graph` unchanged."""
        from mokata.knowledge.ast_backend import AstBackend
        from mokata.knowledge.layer import KnowledgeLayer
        with tempfile.TemporaryDirectory() as d:
            layer = KnowledgeLayer.from_surface(_repo(d, graph_present=False, python=True))
            self.assertIsInstance(layer.primary, AstBackend)
            self.assertFalse(AstBackend.is_graph)         # deliberately still a floor
            self.assertFalse(layer.uses_graph)


class TestGraphHintMirrorsGraphStatus(unittest.TestCase):
    """doc 84 §5's requirement: the hint mirrors `graph status` wording, so the two surfaces
    cannot disagree about which backend answers."""

    def _graph_status(self, surface):
        """Drive the REAL `mokata graph status` against THIS surface.

        `cmd_graph_status` re-loads the surface from `args.path` with live detection, which
        would silently compare a different config than the hint saw (on a box where ripgrep
        is installed, the lexical floor is named 'ripgrep', not 'grep'). Patching the loader
        pins both surfaces to one layer, which is the only way the mirror claim is meaningful.
        """
        import argparse
        from unittest import mock

        from mokata.cli_commands import graph as graph_cmd
        buf = io.StringIO()
        with mock.patch.object(graph_cmd, "_load_surface", return_value=surface):
            with redirect_stdout(buf):
                graph_cmd.cmd_graph_status(argparse.Namespace(path=surface.root))
        return buf.getvalue().strip()

    def test_backend_word_matches_graph_status_for_every_config(self):
        """Asserted against the LIVE backend name, not a hardcoded word, so the mirror holds
        on any machine (the lexical floor is 'ripgrep' where ripgrep is installed)."""
        from mokata.knowledge.layer import KnowledgeLayer
        for python in (True, False):
            with self.subTest(python=python):
                with tempfile.TemporaryDirectory() as d:
                    surface = _repo(d, graph_present=False, python=python)
                    name = KnowledgeLayer.from_surface(surface).backend_name
                    self.assertEqual(name, "ast" if python else "grep")
                    hint = graph_guidance(surface)
                    status = self._graph_status(surface)
                    self.assertIn(f"floor '{name}'", status)
                    self.assertIn(f"floor '{name}'", hint)   # same vocabulary, both surfaces

    def test_active_graph_backend_word_matches(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, graph_present=True, python=True)
            hint = graph_guidance(surface)
            status = self._graph_status(surface)
            self.assertIn("code-review-graph", hint)
            self.assertIn("code-review-graph", status)
            self.assertIn("code graph active", hint)
            self.assertIn("code graph active", status)


class TestAllSurfacesInheritTheFix(unittest.TestCase):
    def test_status_cli_renders_the_ast_floor_text(self):
        import argparse

        from mokata.cli_commands.core import cmd_status
        with tempfile.TemporaryDirectory() as d:
            _repo(d, graph_present=False, python=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_status(argparse.Namespace(path=d))
            out = buf.getvalue()
            self.assertIn("floor 'ast'", out)
            self.assertNotIn("no codebase graph wired", out)

    def test_doctor_report_renders_the_ast_floor_text(self):
        from mokata.govern import diagnose
        with tempfile.TemporaryDirectory() as d:
            report = diagnose(_repo(d, graph_present=False, python=True))
            self.assertIn("floor 'ast'", report.graph_hint)
            self.assertIn(report.graph_hint, report.render())

    def test_no_surface_hardcodes_its_own_copy_of_the_old_string(self):
        """The literal old string must survive ONLY as prose in `graph_guidance`'s grep branch
        (plus historical code comments) — never re-implemented on another surface."""
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "mokata")
        needle = "running on the grep floor (safe, but lexical)"
        hits = []
        for dirpath, _dirs, files in os.walk(src):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8") as fh:
                    if needle in fh.read():
                        hits.append(p)
        self.assertEqual(hits, [], f"stale copy of the old hint text in {hits}")


if __name__ == "__main__":
    unittest.main()
