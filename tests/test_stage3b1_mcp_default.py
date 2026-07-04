"""Stage 3b.1 — the MCP SDK ships by DEFAULT (plugin-first), and a missing SDK fails LOUD.

mokata is plugin-first (Stage 21), so its primary surface — the `mokata-mcp` server — must
work from a plain `pip install mokata`, not only from the `[mcp]` extra. This guard freezes
three guarantees so they can't silently regress:

  1. the MCP SDK is a DEFAULT [project] dependency (with the `python_version >= "3.10"` marker
     so 3.9 stays a clean no-op), and the `[mcp]` extra is kept as a compat alias;
  2. `mokata-mcp` fails LOUD, not dead, when the SDK is absent — a non-zero exit + an
     actionable stderr message, never an uncaught traceback / silent dead server;
  3. the SDK is still LAZILY imported — no top-level `import mcp` — so the core + CLI import
     and run with it absent (the 3.9 degrade path).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import re
import unittest
from contextlib import redirect_stderr

from mokata import mcp_server as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestMcpSdkIsDefaultDependency(unittest.TestCase):
    def setUp(self):
        self.pyproject = _read("pyproject.toml")

    def test_mcp_is_a_default_project_dependency(self):
        # The top-level `dependencies = [...]` array (NOT an optional extra) must carry the SDK
        # with the 3.10 environment marker, so a plain install pulls it on 3.10+ and no-ops on 3.9.
        m = re.search(r"(?m)^dependencies = \[(?P<body>.*)\]", self.pyproject)
        self.assertIsNotNone(m, "pyproject.toml has no top-level [project].dependencies array")
        deps = m.group("body")
        self.assertIn("mcp>=1.2", deps,
                      "the MCP SDK must be a DEFAULT dependency (plugin-first), not [mcp]-only")
        self.assertIn('python_version >= "3.10"', deps,
                      "the SDK dependency must keep the 3.10 marker so 3.9 stays a clean no-op")

    def test_mcp_extra_kept_as_compat_alias(self):
        # `pip install "mokata[mcp]"` must still resolve — the extra stays as a no-op alias.
        self.assertRegex(self.pyproject, r"(?m)^mcp = \[",
                         "the [mcp] extra must remain (compat alias for existing installs)")


class TestFailLoudNotDead(unittest.TestCase):
    """When the SDK is absent, `mokata-mcp` must name the cause + fix and exit non-zero — never
    a raw traceback that Claude Code surfaces only as a failed/absent server."""

    def test_main_exits_nonzero_with_actionable_stderr_when_sdk_absent(self):
        original = M.mcp_available
        M.mcp_available = lambda: False        # simulate the 3.9 / stripped-env case
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = M.main([])
        finally:
            M.mcp_available = original
        self.assertNotEqual(rc, 0, "a missing SDK must be a non-zero exit, not a silent success")
        msg = err.getvalue()
        self.assertIn("mokata-mcp", msg, "the message must identify the failing server")
        self.assertIn("3.10", msg, "the message must name the cause (needs Python >= 3.10)")
        self.assertRegex(msg.lower(), r"install|upgrade|reinstall",
                         "the message must state the fix, not just the failure")

    def test_main_does_not_raise_when_sdk_absent(self):
        # No uncaught ImportError/RuntimeError may escape — the failure is reported, not thrown.
        original = M.mcp_available
        M.mcp_available = lambda: False
        try:
            with redirect_stderr(io.StringIO()):
                rc = M.main([])          # must return, not raise
        finally:
            M.mcp_available = original
        self.assertIsInstance(rc, int)


class TestSdkStaysLazilyImported(unittest.TestCase):
    def test_no_top_level_mcp_import(self):
        # The core + CLI import mcp_server with the SDK absent, so the SDK must be imported only
        # INSIDE functions (build_server), never at module top level.
        src = _read(os.path.join("src", "mokata", "mcp_server.py"))
        offenders = [ln for ln in src.splitlines()
                     if re.match(r"^(import mcp\b|from mcp\b)", ln)]
        self.assertEqual(offenders, [],
                         f"MCP SDK must stay lazily imported; top-level import found: {offenders}")


if __name__ == "__main__":
    unittest.main()
