"""Stage 21 — the plugin-first MCP surface.

Proves the MCP server is a thin, safe wrapper over the engine: it exposes the right tools,
READ tools return real data, and every WRITE tool is human-gated — propose-only with no
`confirm`, and a secret is blocked even when confirmed. The tool functions are SDK-free, so
these run with the MCP SDK absent; the server-construction test runs only when the optional
SDK is installed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import write_sample_repo

from mokata import mcp_server as M
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import DECISION, MemoryItem, MemoryStore
from mokata.share import export_manifest

EXPECTED_READ = {"query", "recall", "doctor", "coverage", "budget", "audit",
                 "status", "preview"}
EXPECTED_WRITE = {"remember", "import_stack", "reset", "apply_proposal"}


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestToolRegistry(unittest.TestCase):
    def test_read_and_write_tools_are_registered_and_classified(self):
        self.assertEqual(set(M.read_tool_names()), EXPECTED_READ)
        self.assertEqual(set(M.write_tool_names()), EXPECTED_WRITE)
        # the registry is the union, with no overlap between read and write
        self.assertEqual(set(M.tool_names()), EXPECTED_READ | EXPECTED_WRITE)
        self.assertFalse(EXPECTED_READ & EXPECTED_WRITE)


class TestReadToolsReturnData(unittest.TestCase):
    def test_status_and_doctor_and_query_return_real_data(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            write_sample_repo(d)

            status = M.status(path=d)
            self.assertEqual(status["profile"], "standard")
            self.assertTrue(status["capabilities"])

            self.assertTrue(M.doctor(path=d)["ok"])

            q = M.query(path=d, kind="callers", target="compute")
            self.assertEqual(q["kind"], "callers")
            self.assertIsInstance(q["references"], list)

    def test_recall_reflects_committed_memory(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d, "standard")
            store = MemoryStore.from_surface(surface)
            store.remember(MemoryItem.create("decision:db", "postgres", mtype=DECISION),
                           assume_yes=True)
            store.close()

            recalled = M.recall(path=d, subject="decision:db")
            self.assertTrue(recalled["enabled"])
            self.assertEqual([i["value"] for i in recalled["items"]], ["postgres"])


class TestWriteToolsAreHumanGated(unittest.TestCase):
    def test_remember_is_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = M.remember(path=d, subject="decision:db", value="postgres")
            self.assertEqual(res["status"], "proposed")
            # nothing was written
            store = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(store.all_active(), [])
            store.close()

    def test_remember_writes_only_with_explicit_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = M.remember(path=d, subject="decision:db", value="postgres",
                             confirm=True)
            self.assertEqual(res["status"], "committed")
            self.assertTrue(res["committed"])
            store = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual([i.value for i in store.recall("decision:db")], ["postgres"])
            store.close()

    def test_secret_is_blocked_even_when_confirmed(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = M.remember(path=d, subject="creds",
                             value="AKIAIOSFODNN7EXAMPLE", confirm=True)
            self.assertEqual(res["status"], "blocked")
            self.assertFalse(res["committed"])
            self.assertTrue(res["findings"])
            store = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(store.recall("creds"), [])     # the secret never landed
            store.close()

    def test_import_stack_is_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as src, \
                tempfile.TemporaryDirectory() as dst:
            surface = _init(src, "full")
            shared = os.path.join(src, "stack.json")
            export_manifest(surface, dest=shared)

            res = M.import_stack(path=dst, file=shared)
            self.assertEqual(res["status"], "proposed")
            self.assertTrue(res["valid"])
            # no config was written to the destination
            self.assertFalse(os.path.exists(os.path.join(dst, ".mokata", "manifest.json")))

    def test_reset_is_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = M.reset(path=d)
            self.assertEqual(res["status"], "proposed")
            self.assertTrue(res["targets"])
            # the state dir is still there
            self.assertTrue(os.path.exists(os.path.join(d, ".mokata")))

    def test_apply_proposal_is_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _init(d, "standard")
            store = MemoryStore.from_surface(surface)
            store.remember(MemoryItem.create("decision:db", "postgres", mtype=DECISION),
                           assume_yes=True)
            store.remember(MemoryItem.create("decision:db", "mysql", mtype=DECISION),
                           assume_yes=True)
            store.close()

            res = M.apply_proposal(path=d, subject="decision:db", decision="approve")
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(res["kind"], "contradiction")
            # both facts still active — nothing resolved without confirm
            store2 = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(len(store2.recall("decision:db")), 2)
            store2.close()


@unittest.skipUnless(M.mcp_available(), "optional MCP SDK not installed")
class TestServerConstructsWithSdk(unittest.TestCase):
    def test_server_builds_and_lists_every_tool(self):
        import asyncio

        server = M.build_server()
        self.assertEqual(type(server).__name__, "FastMCP")
        names = {t.name for t in asyncio.run(server.list_tools())}
        self.assertEqual(names, EXPECTED_READ | EXPECTED_WRITE)


if __name__ == "__main__":
    unittest.main()
