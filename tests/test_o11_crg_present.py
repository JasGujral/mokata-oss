"""0.0.26 O11 — `SUITE-AND-VIEWS-WITH-CRG-PRESENT`: what is true when code-review-graph is installed.

`mokata[full]` installs code-review-graph (0.0.26 L1), so every full-mode user — and any CI leg that
tests full — has it on PATH or in mokata's own scripts dir. GR.S2(g) then USES it read-only on a
repo that never ran `mokata graph adopt` (a settled decision, not changed here). Measured with
code-review-graph 2.3.9 on a repo it never built:

  * in a git repo: `query_graph_tool` answers `status: "not_found"` with confidence "graph is
    empty: nothing is indexed, so this 0 says nothing about the code";
  * outside one: the MCP call answers `isError: true` ("repo_root does not look like a project
    root").

Both were read as ZERO callers — basis structural — because only the blast-radius walk checked the
status. The IDE's ripple then told the user a 30-caller hub had no callers and disclosed no cap
(there was nothing to cap). What must be true, each with CRG present:

  * a not_found / ambiguous answer is `GraphNotIndexed` for EVERY pattern kind (callers, callees,
    imports, implementers, refs), not an empty answer — the layer answers from the floor and says
    the graph does not index the target;
  * a CRG `status: "error"` and an MCP `isError` are the tool FAILING, degraded loudly;
  * an `ok` answer with zero rows is still a real structural zero;
  * the IDE's graph views still say what they left out when the detected graph cannot speak.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import crg_present_failing, crg_present_unbuilt

from mokata.knowledge import crg_client
from mokata.knowledge.crg_client import CodeReviewGraphClient, CrgUnavailable
from mokata.knowledge.query import GraphNotIndexed

# RECORDED from code-review-graph 2.3.9 on a git repo it never built (`callers_of`, target `hub`).
EMPTY_GRAPH = {"status": "not_found", "summary": "No node found matching 'hub'.",
               "confidence": "graph is empty: nothing is indexed, so this 0 says nothing about "
                             "the code; run `code-review-graph build`"}
# RECORDED from 2.3.9 outside a project root: the MCP `tools/call` result itself.
NOT_A_PROJECT = {"content": [{"type": "text", "text": "Error calling tool 'query_graph_tool': "
                              "repo_root does not look like a project root (no .git, .svn, or "
                              ".code-review-graph directory found): /tmp/x"}],
                 "isError": True}
OK_EMPTY = {"status": "ok", "pattern": "callers_of", "target": "leaf", "summary": "Found 0",
            "result_count": 0, "results_omitted": 0, "results": [], "edges": []}

PATTERN_KINDS = ("callers", "callees", "imports", "implementers", "refs")


def _client(payload):
    return CodeReviewGraphClient(root=".", call_tool=lambda _t, _p: dict(payload),
                                 run_cli=lambda _a: (0, "code-review-graph 2.3.9", ""))


class EveryPatternKindRefusesToCallNoAnswerAZero(unittest.TestCase):

    def test_an_empty_graph_is_not_indexed_for_every_kind(self):
        for kind in PATTERN_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(GraphNotIndexed) as cm:
                    _client(EMPTY_GRAPH).query(kind, "hub", root=".")
                self.assertIn("graph is empty", str(cm.exception))

    def test_an_ambiguous_target_is_not_indexed(self):
        amb = {"status": "ambiguous", "summary": "2 nodes match 'hub'"}
        with self.assertRaises(GraphNotIndexed):
            _client(amb).query("callers", "hub", root=".")

    def test_a_tool_error_status_is_a_failure_not_a_zero(self):
        for kind in PATTERN_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(CrgUnavailable):
                    _client({"status": "error", "error": "db locked"}).query(kind, "hub", root=".")

    def test_an_indexed_symbol_with_no_callers_is_still_a_real_zero(self):
        rows = _client(OK_EMPTY).query("callers", "leaf", root=".")
        self.assertEqual([], list(rows))


class AnMcpToolErrorIsTheToolFailing(unittest.TestCase):

    def _session(self, result):
        s = crg_client._McpStdioSession("code-review-graph", root=".")
        s._ensure = lambda: None
        s._rpc = lambda _m, _p: result
        return s

    def test_is_error_raises_unavailable_with_the_tools_reason(self):
        with self.assertRaises(CrgUnavailable) as cm:
            self._session(NOT_A_PROJECT).call("query_graph_tool", {})
        self.assertIn("does not look like a project root", str(cm.exception))

    def test_a_normal_text_answer_still_parses(self):
        ok = {"content": [{"type": "text", "text": '{"status": "ok", "results": []}'}]}
        self.assertEqual({"status": "ok", "results": []},
                         self._session(ok).call("query_graph_tool", {}))


def _silent(*_a, **_k):
    pass


class TheViewsStayHonestWithAnUnbuiltCrgDetected(unittest.TestCase):
    """The machine has code-review-graph and the repo never adopted it nor built its index — the
    common `mokata[full]` first run. Detection is FORCED through the real detector
    (`_support.crg_present_unbuilt`, so both venvs mean the same thing) and CRG's reply is the
    recorded empty-graph one; the views must answer from the floor and still disclose what they
    left out."""

    FAILING = False

    def setUp(self):
        from mokata.ide import nav
        from mokata.init import init_repo
        self.root = tempfile.mkdtemp(prefix="o11-crg-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(nav.close_layers)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        os.makedirs(os.path.join(self.root, "src", "app"))
        with open(os.path.join(self.root, "src", "app", "hub.py"), "w", encoding="utf-8") as fh:
            fh.write("def hub(x):\n    return x\n")
        for i in range(30):
            with open(os.path.join(self.root, "src", "app", f"m{i:03d}.py"), "w",
                      encoding="utf-8") as fh:
                fh.write(f"from app.hub import hub\n\n\ndef c{i}():\n    return hub({i})\n")
        self.asked = (crg_present_failing if self.FAILING else crg_present_unbuilt)(self)

    def test_a_megahub_ripple_counts_its_callers_from_the_floor_and_discloses_the_cap(self):
        from mokata.ide import visuals as V
        with mock.patch.object(V, "PAGE_NODES", 10):
            g = V.ripple(self.root, "hub", 1)
        self.assertTrue(self.asked, "the detected graph was not asked — the overlay is not forced")
        self.assertEqual(30, g["direct_callers"],
                         "an unbuilt detected graph's empty answer was read as 'no callers'")
        self.assertTrue(g["more"])                      # P37: paged, never a silent stop

    def test_the_layer_names_the_graph_that_could_not_speak(self):
        from mokata.knowledge import KnowledgeLayer
        from mokata.config import Surface
        layer = KnowledgeLayer.from_surface(Surface.load(self.root))
        self.addCleanup(lambda: getattr(layer, "close", lambda: None)())
        self.assertEqual("code-review-graph", layer.backend_name)
        res = layer.callers("hub")
        self.assertEqual(30, len(res.references))
        # 0.0.26 O13 — an unbuilt index is not "does not index `hub`": nothing is indexed yet,
        # the ONE background build has started, and the note says so.
        self.assertIn("code-review-graph is building its index (started", res.note)
        self.assertIn("answered by the 'ast' floor", res.note)


class TheViewsStayHonestWhenTheDetectedCrgErrors(TheViewsStayHonestWithAnUnbuiltCrgDetected):
    """Same, with a built index and a tool that FAILS: CRG answers an MCP error (recorded). The
    floor answers, the layer degrades loudly — and the views still count and disclose."""

    FAILING = True

    def test_the_layer_names_the_graph_that_could_not_speak(self):
        from mokata.knowledge import KnowledgeLayer
        from mokata.config import Surface
        layer = KnowledgeLayer.from_surface(Surface.load(self.root))
        res = layer.callers("hub")
        self.assertEqual(30, len(res.references))
        self.assertTrue(res.degraded)
        self.assertIn("'code-review-graph' failed", res.note)


class TheFirstUseNoticeIsOffTheAnswerChannelAndLedgered(unittest.TestCase):
    """GR.S2(h): the first use of a detected graph is disclosed AND ledgered. It was printed on
    STDOUT (so `mokata query --json` was no longer one JSON line on a full install) and the
    production call passed no ledger (so it was never ledgered)."""

    def setUp(self):
        from mokata.init import init_repo
        self.root = tempfile.mkdtemp(prefix="o11-note-")
        self.addCleanup(shutil.rmtree, self.root, True)
        init_repo(root=self.root, profile="standard", assume_yes=True, out=_silent)
        with open(os.path.join(self.root, "core.py"), "w", encoding="utf-8") as fh:
            fh.write("def parse(t):\n    return t\n\n\ndef total(t):\n    return parse(t)\n")
        crg_present_unbuilt(self)

    def test_query_json_stays_one_json_line_and_the_notice_goes_to_stderr(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["query", "callers", "parse", "--json", "--path", self.root])
        self.assertEqual(0, rc)
        lines = out.getvalue().splitlines()
        self.assertEqual(1, len(lines), out.getvalue())
        answer = json.loads(lines[0])
        self.assertEqual(["core.py"], sorted({r["path"] for r in answer["references"]}))
        self.assertIn("detected external code graph 'code-review-graph'", err.getvalue())

    def test_the_first_use_is_ledgered_once(self):
        import io
        from contextlib import redirect_stderr
        from mokata.config import Surface
        from mokata.govern.ledger import AuditLedger
        from mokata.knowledge import KnowledgeLayer
        with redirect_stderr(io.StringIO()):
            for _ in range(2):
                KnowledgeLayer.from_surface(Surface.load(self.root))
        led = AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata"))
        kinds = [e.get("kind") for e in led.entries()]
        self.assertEqual(1, kinds.count("graph_first_use"), kinds)


if __name__ == "__main__":
    unittest.main()
