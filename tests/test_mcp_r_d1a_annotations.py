"""MCP-R.D1a — TOOL ANNOTATIONS PROJECTED FROM `ToolSpec.kind` (0.0.15, doc 88 §D1).

Every registered tool gets MCP `ToolAnnotations` projected from its already-declared `kind`
(read|write|approve) + the grounded open-world set, at `add_tool` — so a client (and the human's
allow/ask policy) can reason about a call BEFORE making it (P16 legibility, P14). This is a pure
PROJECTION: the `kind` sets read/destructive/idempotent; a tool's NAME sets openWorld via
`OPEN_WORLD_TOOLS`. No tool body, no input schema, no tool result changes (D0's guarantee holds).

  Projection table            : test_mcp_r_d1a_projection
  Coverage (all 55 annotated) : test_mcp_r_d1a_every_tool_annotated
  Open-world drift guard      : test_mcp_r_d1a_open_world_set_is_grounded
  SDK attach (client-read obj) : test_mcp_r_d1a_annotations_on_fastmcp_tools
  SCHEMA-PARITY (absolute)     : test_mcp_r_d1a_schema_unchanged
  Negative (no result leak)   : test_mcp_r_d1a_results_unchanged

Secret-safety: n/a — annotations are metadata derived from `kind`/`name` only; no arg/DSN/path is
read, so there is no value to leak (stated per the stage bar).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import tempfile
import unittest

import _support  # noqa: F401 - puts src/ on the path

import mokata.mcp.tool_annotations as A                    # noqa: E402
from mokata.mcp import server as MS                         # noqa: E402
from mokata.mcp import tools_read as TR                     # noqa: E402
from mokata.mcp.registry import TOOLS, tool_names           # noqa: E402


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)


# The GROUNDED open-world set, pinned here so any change to `OPEN_WORLD_TOOLS` is a conscious,
# reviewed test edit (drift guard). Grouped by the external sink each member reaches on a
# read-or-compute path (see annotations.OPEN_WORLD_TOOLS for the per-tool call-site citations).
EXPECTED_OPEN_WORLD = {
    # CRG code-graph subprocess (a graph QUERY, not mere backend identity)
    "query", "decompose", "ci_check", "remember", "spec_amend", "spec_check",
    # team Postgres (team-mode memory read / DSN health probe / PG transport / shared log)
    "recall", "status", "audit", "session_list", "govern", "apply_proposal",
    "memory_export", "memory_import", "session_push", "session_pull", "session_name",
    "audit_share",
    # remote catalog (stack index / manifest resolved from a possibly-remote source)
    "stacks_list", "stacks_search", "stacks_show", "import_stack", "stacks_install",
}


# ======================================================================================
# Projection table — kind -> hints, and NAME -> openWorld, exactly per doc 88 §D1
# ======================================================================================

class TestProjectionTable(unittest.TestCase):

    def test_mcp_r_d1a_projection(self):
        # read -> readOnlyHint:true (destructive/idempotent are meaningless under read-only: omitted)
        r = A.annotations_for("read", "doctor")          # a NON-open-world read
        self.assertIs(r["readOnlyHint"], True)
        self.assertIs(r["openWorldHint"], False)

        # write -> non-readonly + non-destructive (propose-only) + idempotent (proposal-id re-call)
        w = A.annotations_for("write", "reset")          # a NON-open-world write
        self.assertIs(w["readOnlyHint"], False)
        self.assertIs(w["destructiveHint"], False)
        self.assertIs(w["idempotentHint"], True)
        self.assertIs(w["openWorldHint"], False)

        # approve -> SAME hint shape as write (it is neither a read nor a propose-only write, but
        # the in-chat consent act carries the write axes; it reaches nothing external)
        a = A.annotations_for("approve", "approve")
        self.assertEqual({k: a[k] for k in ("readOnlyHint", "destructiveHint", "idempotentHint")},
                         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
        self.assertIs(a["openWorldHint"], False)

        # every open-world member -> openWorldHint:true, whatever its kind (reads AND propose-only
        # writes: the axis is INDEPENDENT of the write gate)
        for spec in TOOLS:
            if spec.name in EXPECTED_OPEN_WORLD:
                self.assertIs(A.annotations_for(spec.kind, spec.name)["openWorldHint"], True,
                              f"{spec.name} ({spec.kind}) should be openWorld")

        # a non-open-world read -> openWorldHint:false
        self.assertIs(A.annotations_for("read", "budget")["openWorldHint"], False)

        # a propose-only write can be BOTH non-destructive AND open-world (orthogonal axes):
        # spec_check shells to the CRG graph on its compute path yet its write is non-destructive.
        sc = A.annotations_for("write", "spec_check")
        self.assertIs(sc["destructiveHint"], False)      # propose-only: never destroys
        self.assertIs(sc["openWorldHint"], True)         # ...but its compute reaches CRG

    def test_mcp_r_d1a_unknown_kind_raises(self):
        # a NEW kind must be classified, never silently mis-annotated (drift backstop)
        with self.assertRaises(ValueError):
            A.annotations_for("mutate", "some_new_tool")


# ======================================================================================
# Coverage — EVERY registered tool is annotated with the right shape (all 56; none unannotated)
# ======================================================================================

class TestCoverage(unittest.TestCase):

    def test_mcp_r_d1a_every_tool_annotated(self):
        # the D0 ground-truth count, 55 -> 56 at HANDOFF.G1 (0.0.16): the `spec_show` read tool;
        # 56 -> 58 at REVIEW-FIX.R3 (0.0.16): `review_status` + `review_record`;
        # 58 -> 59 at WT-LIST (0.0.16): `worktree_list`, the read-only worktree×session join;
        # 59 -> 61 at M-4/R5 (0.0.16): `consolidate_proposals` (read — the drafting request) +
        # `consolidate` (gated write — the agent submits the summary it drafted).
        self.assertEqual(len(TOOLS), 61)
        for spec in TOOLS:
            ann = A.annotations_for(spec.kind, spec.name)
            self.assertIn("readOnlyHint", ann)           # present + bool for EVERY tool
            self.assertIsInstance(ann["readOnlyHint"], bool)
            self.assertIsInstance(ann["openWorldHint"], bool)
            if spec.kind == "read":
                self.assertIs(ann["readOnlyHint"], True)
            else:
                self.assertIs(ann["readOnlyHint"], False)
                self.assertIs(ann["destructiveHint"], False)
                self.assertIs(ann["idempotentHint"], True)
            # openWorld matches the grounded set exactly, per tool
            self.assertIs(ann["openWorldHint"], spec.name in EXPECTED_OPEN_WORLD, spec.name)

    def test_mcp_r_d1a_open_world_set_is_grounded(self):
        # (a) the constant equals the grounded, pinned set (a later change is a conscious edit)
        self.assertEqual(set(A.OPEN_WORLD_TOOLS), EXPECTED_OPEN_WORLD)
        # (b) every member is a REAL registered tool — no phantom / renamed-away entry survives
        registered = set(tool_names())
        self.assertTrue(set(A.OPEN_WORLD_TOOLS) <= registered,
                        f"phantom open-world tools: {set(A.OPEN_WORLD_TOOLS) - registered}")


# ======================================================================================
# SDK attach — annotations are set on the REAL FastMCP tool objects the client reads
# ======================================================================================

class TestSdkAttach(unittest.TestCase):

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d1a_annotations_on_fastmcp_tools(self):
        import asyncio

        server = MS.build_server()
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        # 55 -> 56 at HANDOFF.G1 (`spec_show`); 56 -> 58 at REVIEW-FIX.R3 (the 6r review loop);
        # 58 -> 59 at WT-LIST (`worktree_list`); 59 -> 61 at M-4/R5 (`consolidate_proposals` +
        # `consolidate`, the two-phase drafted-summary flow)
        self.assertEqual(len(tools), 61)

        by_name = {s.name: s for s in TOOLS}
        for name, tool in tools.items():
            ann = tool.annotations
            self.assertIsNotNone(ann, f"{name} has no annotations on the SDK object")
            expected = A.annotations_for(by_name[name].kind, name)
            self.assertEqual(ann.readOnlyHint, expected["readOnlyHint"], name)
            self.assertEqual(ann.openWorldHint, expected["openWorldHint"], name)
            if by_name[name].kind != "read":
                self.assertEqual(ann.destructiveHint, False, name)
                self.assertEqual(ann.idempotentHint, True, name)

        # spot-check the two axes on real client-visible objects
        self.assertIs(tools["doctor"].annotations.readOnlyHint, True)      # a read
        self.assertIs(tools["remember"].annotations.readOnlyHint, False)   # a write
        self.assertIs(tools["remember"].annotations.destructiveHint, False)  # propose-only
        self.assertIs(tools["query"].annotations.openWorldHint, True)      # CRG graph
        self.assertIs(tools["doctor"].annotations.openWorldHint, False)    # local-only


# ======================================================================================
# SCHEMA-PARITY (absolute) — annotations are METADATA; the inputSchema stays byte-identical to D0
# ======================================================================================

class TestSchemaParity(unittest.TestCase):

    @unittest.skipUnless(MS.mcp_available(), "optional MCP SDK not installed")
    def test_mcp_r_d1a_schema_unchanged(self):
        import asyncio

        from mcp.server.fastmcp import FastMCP

        sample = {"baseline", "status", "remember", "query", "audit", "spec_check", "approve"}

        # the RAW schema: the bare tool fn registered with NO wrapper and NO annotations
        def _raw_schema(fn, name):
            srv = FastMCP("parity")
            srv.add_tool(fn, name=name)
            return {t.name: t.inputSchema for t in asyncio.run(srv.list_tools())}[name]

        raw = {s.name: _raw_schema(s.fn, s.name) for s in TOOLS if s.name in sample}

        # the BUILT schema: through build_server (which now attaches annotations AND _serve)
        built = {t.name: t.inputSchema for t in asyncio.run(MS.build_server().list_tools())
                 if t.name in sample}

        self.assertEqual(set(built), sample)
        for name in sample:
            self.assertEqual(built[name], raw[name],
                             f"{name}: annotations/_serve changed the FastMCP input schema")


# ======================================================================================
# Negative — annotations are add_tool METADATA; they NEVER leak into a tool's return dict
# ======================================================================================

class TestResultsUnchanged(unittest.TestCase):

    def test_mcp_r_d1a_results_unchanged(self):
        annotation_keys = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            direct = TR.doctor(path=d)                    # the tool called directly
            served = MS._serve(TR.doctor, name="doctor", kind="read")(path=d)   # via D0 wrapper
            # `_serve` fires a self-registration daemon thread it deliberately never joins (R5).
            # That thread holds an open fd on `…/state/.session_registry.json.lock`; POSIX unlinks
            # an open file happily, Windows raises WinError 32 and the TemporaryDirectory teardown
            # below dies (WIN-LOCKHANDLE). Drain it INSIDE the block — `_await_registrations` is
            # the seam that exists for exactly this race.
            MS._await_registrations(5.0)
        # a read tool's output carries NONE of the annotation keys...
        self.assertFalse(annotation_keys & set(direct))
        # ...and the served result is byte-identical to the direct call (annotations are metadata)
        self.assertEqual(json.dumps(direct, sort_keys=True), json.dumps(served, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
