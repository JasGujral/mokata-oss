"""0.0.28 lane I2 — a first call made from `tools/list` alone is right (`MCP-FIRST-CALL-FAILS`).

Over real stdio, every registered tool is called with arguments built ONLY from the schema the
server advertised — once plainly, and once with every array/object argument sent as a JSON string
(models stringify nested arrays; the SDK has always accepted it, so the front door must too). No
such call may be refused as `invalid_input`: if the schema-built call is wrong, the schema lied.

And the empty write: `remember({})` / `remember(text=…)` minted a proposal for an EMPTY memory on
master. Neither may mint anything now.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import shutil
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk
import _mcp_stdio as H

_NOT_CALLED = frozenset({"baseline"})       # runs the repo's whole test suite



@_mcp_sdk.requires_mcp_sdk
class TestI2_FirstCallFromSchemaSucceeds(unittest.TestCase):
    """All tools x {plain, stringified}: never `invalid_input`."""

    def test_I2_every_tool_accepts_its_own_schema_built_call(self):
        repo = H.make_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        srv = H.McpStdio(repo)
        try:
            tools = [t for t in srv.tools_list() if t["name"] not in _NOT_CALLED]
            ids = {}
            for t in tools:
                call = H.required_call(t, repo)
                ids[srv.tool_start(t["name"], call)] = (t["name"], "plain")
                if call != H.stringified(call):
                    ids[srv.tool_start(t["name"], H.stringified(call))] = (t["name"], "stringified")
            verdict = srv.settle(ids)
            replies = {key: H.payload(srv.reply(rid)) for rid, key in ids.items()}
        finally:
            srv.close()
        self.assertTrue(verdict.ok, repr(verdict))
        refused = {f"{n}/{v}": out.get("field") for (n, v), out in replies.items()
                   if isinstance(out, dict) and out.get("reason_code") == "invalid_input"
                   and not H.world_refusal(n, out)}
        self.assertEqual(refused, {}, "a call built from the advertised schema was refused")
        self.assertGreater(sum(1 for _n, v in replies if v == "stringified"), 0)


@_mcp_sdk.requires_mcp_sdk
class TestI2_EmptyWriteNeverProposed(unittest.TestCase):
    """`remember({})` and `remember(text=…)` never mint a proposal. FIRING CONTROL (mutant): give
    `remember.value` a default again and `{subject}` proposes an empty memory."""

    def test_I2_an_empty_memory_is_never_proposed(self):
        repo = H.make_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        srv = H.McpStdio(repo)
        try:
            outs = [srv.tool("remember", {"path": repo}),
                    srv.tool("remember", {"path": repo, "text": "a fact"}),
                    srv.tool("remember", {"path": repo, "subject": "s"}),
                    srv.tool("remember", {"path": repo, "subject": "s", "value": ""})]
        finally:
            srv.close()
        for out in outs:
            self.assertNotIn("proposal_id", out, out)
            self.assertEqual(out.get("status"), "refused", out)
            self.assertEqual(out.get("reason_code"), "invalid_input", out)


if __name__ == "__main__":
    unittest.main()
