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


if __name__ == "__main__":
    unittest.main()
