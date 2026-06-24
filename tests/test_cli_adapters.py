"""CLI for A6/H4 — `mokata coverage` and `mokata mcp`."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestCoverageCLI(unittest.TestCase):
    def test_coverage_reports_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="full", assume_yes=True, out=silent)
            rc, out = run_cli(["coverage", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("coverage", out.lower())
            self.assertIn("code_graph", out)


class TestMcpCLI(unittest.TestCase):
    def test_mcp_degrades_when_none(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["mcp", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no servers", out.lower())

    def test_mcp_lists_discovered_servers(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            with open(os.path.join(d, MOKATA_DIR, "mcp.json"), "w",
                      encoding="utf-8") as fh:
                json.dump([{"name": "crg", "provides": ["code_graph"]}], fh)
            rc, out = run_cli(["mcp", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("code_graph", out)
            self.assertIn("crg", out)


if __name__ == "__main__":
    unittest.main()
