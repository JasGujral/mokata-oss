"""Stage 3b.1 — the MCP SDK ships by DEFAULT (plugin-first), and a missing SDK fails LOUD.

mokata is plugin-first (Stage 21), so its primary surface — the `mokata-mcp` server — must
work from a plain `pip install mokata`, not only from the `[mcp]` extra. This guard freezes
three guarantees so they can't silently regress:

  1. the MCP SDK is an UNCONDITIONAL [project] dependency (no env marker — the Python >= 3.10
     floor, TM.S0, means it installs everywhere mokata does), and the `[mcp]` extra is kept
     as a compat alias;
  2. `mokata-mcp` fails LOUD, not dead, when the SDK is absent — a non-zero exit + an
     actionable stderr message, never an uncaught traceback / silent dead server;
  3. the SDK is still LAZILY imported — no top-level `import mcp` — so the core + CLI import
     and run even in a stripped/broken env where it is absent.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import importlib
import importlib.util
import io
import os
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

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
        # UNCONDITIONALLY — no `python_version` env marker (dropped with the >= 3.10 floor), so a
        # plain install always pulls it.
        m = re.search(r"(?m)^dependencies = \[(?P<body>.*)\]", self.pyproject)
        self.assertIsNotNone(m, "pyproject.toml has no top-level [project].dependencies array")
        deps = m.group("body")
        self.assertIn("mcp>=1.2", deps,
                      "the MCP SDK must be a DEFAULT dependency (plugin-first), not [mcp]-only")
        self.assertNotIn("python_version", deps,
                         "the SDK dependency must be unconditional — the 3.9 env marker is gone")

    def test_mcp_extra_kept_as_compat_alias(self):
        # `pip install "mokata[mcp]"` must still resolve — the extra stays as a no-op alias.
        self.assertRegex(self.pyproject, r"(?m)^mcp = \[",
                         "the [mcp] extra must remain (compat alias for existing installs)")


class TestFailLoudNotDead(unittest.TestCase):
    """When the SDK is absent, `mokata-mcp` must name the cause + fix and exit non-zero — never
    a raw traceback that Claude Code surfaces only as a failed/absent server."""

    def test_main_exits_nonzero_with_actionable_stderr_when_sdk_absent(self):
        original = M.mcp_available
        M.mcp_available = lambda: False        # simulate the stripped/broken-env case
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                rc = M.main([])
        finally:
            M.mcp_available = original
        self.assertNotEqual(rc, 0, "a missing SDK must be a non-zero exit, not a silent success")
        msg = err.getvalue()
        self.assertIn("mokata-mcp", msg, "the message must identify the failing server")
        self.assertIn("MCP SDK", msg, "the message must name the cause (the MCP SDK is missing)")
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


class TestMcpAvailableReflectsUsability(unittest.TestCase):
    """`mcp_available()` must reflect whether the SDK can actually be IMPORTED, not merely whether
    it sits on disk. A present-but-broken SDK (its own transitive dep missing — e.g. the SDK
    imports jsonschema, but jsonschema is absent) must read as UNAVAILABLE, so `mokata-mcp` fails
    loud (guarantee #2) instead of build_server() raising an uncaught error mid-startup. The
    jsonschema-absent CI leg hit exactly this: find_spec('mcp') was truthy, so the old check
    reported the SDK available, then build_server()'s `from mcp...` raised ModuleNotFoundError."""

    def test_false_when_sdk_present_but_import_raises(self):
        real_import = importlib.import_module
        real_find = importlib.util.find_spec

        def broken_import(name, *a, **k):
            if name == "mcp" or name.startswith("mcp."):
                raise ImportError("No module named 'jsonschema'")   # the SDK's own dep is gone
            return real_import(name, *a, **k)

        def present_on_disk(name, *a, **k):
            if name == "mcp" or name.startswith("mcp."):
                return object()                                     # pretend it's installed
            return real_find(name, *a, **k)

        with mock.patch("importlib.import_module", side_effect=broken_import), \
             mock.patch("importlib.util.find_spec", side_effect=present_on_disk):
            self.assertFalse(M.mcp_available(),
                             "a present-but-unimportable SDK must read as unavailable")


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
