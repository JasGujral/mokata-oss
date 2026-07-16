"""Stage 21 — the plugin-first MCP surface.

Proves the MCP server is a thin, safe wrapper over the engine: it exposes the right tools,
READ tools return real data, and every WRITE tool is human-gated. Since SI.3 that gate is one the
MODEL CANNOT OPEN: a write tool is PROPOSE-ONLY, `approve`/`confirm` are accepted but commit
nothing, and only an approval a human minted out-of-band (`mokata approve <id>`, replayed here by
`_support.mcp_commit`) lets a write land — after which a secret is STILL hard-blocked, because
approval is a methodology gate and never a security override. The tool functions are SDK-free, so
these run with the MCP SDK absent; the server-construction test runs only when the optional
SDK is installed.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import mcp_commit, write_sample_repo

from mokata import mcp_server as M
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import DECISION, MemoryItem, MemoryStore
from mokata.share import export_manifest

EXPECTED_READ = {"query", "recall", "doctor", "coverage", "budget", "audit",
                 "status", "preview", "progress",
                 "lanes", "watch", "govern",          # Stage 54d — parallel-agent observability
                 "vault_list", "vault_search", "vault_pull",
                 # Stage 54e — command-parity read tools
                 "rules", "skills", "suggest", "lat_check", "index_status",
                 "baseline", "sessions", "config_get", "export_preview",
                 "decompose",                          # Stage 54f — task decomposition (read)
                 "session_list",                       # Stage 55a — portable sessions (read)
                 "session_windows",                    # MS.S2 — live-window registry (read)
                 "session_save",                       # SS.S0 — ungated in-flight save (read)
                 "tour",                                # Stage 56 — read-only first-run demo
                 "ci_check",                            # Stage 58 — mokata-as-a-PR-check (read)
                 "stacks_list", "stacks_search",        # Stage 70 — community stacks (read)
                 "stacks_show",
                 "plan_list", "plan_show"}               # Stage 6p — brainstorm plan file (read)
EXPECTED_WRITE = {"remember", "import_stack", "reset", "apply_proposal", "init",
                  "memory_export", "memory_import", "vault_push", "spec_check",
                  "config_set", "export_stack",       # Stage 54e — command-parity write tools
                  "session_push", "session_pull",     # Stage 55a — portable sessions (gated)
                  "session_name",                     # Stage 55b — rename (gated)
                  "reconfigure",                      # Stage 56b — reconfigure wizard (gated)
                  "stacks_install",                   # Stage 70 — community stacks (gated install)
                  "audit_share",                      # Stage 71 — team audit shared publish (gated)
                  "spec_emit",                        # SI-DEV.0 — THE spec-emit surface (gated)
                  "spec_amend"}                       # SI-DEV — the forced scope regression (gated)
# AP-MCP (doc 85 §5 D26 amendment): the in-chat approve tool is its OWN kind — neither a read nor
# a propose-only write — so it is excluded from both name lists (and from the SI.3 write-tool
# sweeps). Default-OFF opt-in; see test_ap_mcp_in_chat_approval.py for its behaviour.
EXPECTED_APPROVE = {"approve"}


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestToolRegistry(unittest.TestCase):
    def test_read_and_write_tools_are_registered_and_classified(self):
        self.assertEqual(set(M.read_tool_names()), EXPECTED_READ)
        self.assertEqual(set(M.write_tool_names()), EXPECTED_WRITE)
        # AP-MCP: `approve` is its own kind — in neither name list, so the SI.3 write sweeps skip it.
        self.assertNotIn("approve", set(M.read_tool_names()) | set(M.write_tool_names()))
        # the registry is the union of all three kinds, with no overlap between them.
        self.assertEqual(set(M.tool_names()), EXPECTED_READ | EXPECTED_WRITE | EXPECTED_APPROVE)
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

    def test_a_model_typed_confirm_does_not_write(self):
        """SI.3 — `confirm`/`approve` are still ACCEPTED (schema stability) but commit NOTHING.
        They were a consent flag the MODEL typed; consent is now minted out-of-band by a human."""
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = M.remember(path=d, subject="decision:db", value="postgres", confirm=True)
            self.assertEqual(res["status"], "proposed")
            self.assertFalse(res.get("committed", False))
            store = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual(store.all_active(), [])    # nothing landed on the model's say-so
            store.close()

    def test_remember_writes_only_with_a_human_minted_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            # propose -> the HUMAN approves out-of-band (`mokata approve <id>`) -> redeem by id.
            res = mcp_commit(M.remember, path=d, subject="decision:db", value="postgres")
            self.assertEqual(res["status"], "committed")
            self.assertTrue(res["committed"])
            store = MemoryStore.from_surface(Surface.load(d))
            self.assertEqual([i.value for i in store.recall("decision:db")], ["postgres"])
            store.close()

    def test_secret_is_blocked_even_when_a_human_approved_it(self):
        """A real human approval still cannot override a security block (P2/I1)."""
        with tempfile.TemporaryDirectory() as d:
            _init(d, "standard")
            res = mcp_commit(M.remember, path=d, subject="creds",
                             value="AKIAIOSFODNN7EXAMPLE")
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
        self.assertEqual(names, EXPECTED_READ | EXPECTED_WRITE | EXPECTED_APPROVE)


if __name__ == "__main__":
    unittest.main()
