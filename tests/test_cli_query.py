"""B2/B3 at the command path — `mokata query <kind> <target>` resolves a backend
through the router and runs the typed query (graph absent in CI -> the embedded AST floor
on a Python repo, GR.S1)."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import write_sample_repo

from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


class TestQueryCLI(unittest.TestCase):
    def test_query_runs_and_reports_backend(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["query", "callers", "compute", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("mod_a.py", out)
            self.assertIn("mod_b.py", out)
            # graph tool is absent here -> the embedded AST floor answered (GR.S1), non-degraded
            self.assertIn("ast", out)
            self.assertIn("[graph]", out)         # non-degraded mode (not "grep fallback")

    def test_query_unknown_kind_is_rejected_by_argparse(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            with self.assertRaises(SystemExit):
                main(["query", "teleport", "compute", "--path", d])


class TestQueryCLIWithCrgPresent(unittest.TestCase):
    """0.0.26 O11 sibling: `profile full` pins code-review-graph, and it IS installed but never
    built. Before the fix the answer was "0 result(s)" via code-review-graph [graph]; now the AST
    floor answers and the reply says why."""

    def _query(self, failing):
        from _support import crg_present_failing, crg_present_unbuilt
        (crg_present_failing if failing else crg_present_unbuilt)(self)
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["query", "callers", "compute", "--path", d])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("mod_a.py", out)
        self.assertIn("mod_b.py", out)
        self.assertIn("via ast", out)
        return out

    def test_an_unbuilt_graph_is_named_and_the_floor_answers(self):
        out = self._query(failing=False)
        # 0.0.26 O13 — an unbuilt index starts the ONE background build and the floor answers now.
        self.assertIn("code-review-graph is building its index (started", out)
        self.assertIn("[graph]", out)      # the AST floor's answer is not a degraded one

    def test_a_failing_graph_is_a_loud_degrade_not_a_zero(self):
        out = self._query(failing=True)
        self.assertIn("grep fallback", out)
        self.assertIn("failed", out)


def setUpModule():
    # 0.0.26 O11 — this module's expectations are the CORE install's: no external code graph on
    # the machine. Pinned, so a venv with code-review-graph (`mokata[full]`) means the same thing;
    # what is true WITH it present is graded in test_o11_crg_present (and the sibling below, if any).
    from _support import hide_external_graphs
    hide_external_graphs()


if __name__ == "__main__":
    unittest.main()
