"""0.0.25 K6 — code-review-graph actually answers, and a capped answer says it is partial.

Two defects, measured against the real code-review-graph 2.3.9 on the scale repo:
  1. mokata's MCP `initialize` sent `clientInfo` without `version`. The MCP schema requires it and
     2.3.9 enforces it (-32602), so EVERY CRG query raised and silently fell back to the AST floor
     — the adopted graph never answered once.
  2. `query_graph_tool` caps its rows (default 100) and reports `result_count` / `results_omitted`;
     mokata passed no `max_results` and `_normalize` dropped the counts, so a 2,500-caller hub came
     back as "100 callers, structural, complete".

The fake server below is STRICT the way 2.3.9 is: it rejects an `initialize` without a client
version, honours `max_results` (default 100) and reports what it omitted, exactly in the real
tool's shape. It is reached as `[sys.executable, "serve", ...]` — the argv production builds.

Mutations this module was checked against (each went RED, then was restored):
  M1 `clientInfo` without `version`                     -> test_the_handshake_is_accepted
  M2 `max_results` not passed                           -> test_mokata_asks_for_its_own_cap
  M3 `_normalize` drops the counts (no `_cut`)          -> test_a_capped_answer_says_it_is_partial,
                                                           test_a_capped_refs_union_is_partial
"""

import _support  # noqa: F401

import json
import os
import sys
import tempfile
import unittest

from mokata.knowledge.crg_client import CRG_MAX_RESULTS, CodeReviewGraphClient, client_info
from mokata.knowledge.graph_backend import CodeReviewGraphBackend
from mokata.knowledge.query import TRUNCATED_NOTE_PREFIX

_SERVER = '''\
import json, sys

CALLERS = {"hub": 2500, "small": 3}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


def log(entry):
    with open("calls.log", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\\n")


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    method = msg.get("method")
    if method == "initialize":
        info = (msg.get("params") or {}).get("clientInfo") or {}
        if not info.get("version"):
            send({"jsonrpc": "2.0", "id": msg.get("id"),
                  "error": {"code": -32602, "message": "Invalid request parameters"}})
            continue
        send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"protocolVersion": "2024-11-05"}})
        continue
    if method and method.startswith("notifications/"):
        continue
    params = msg.get("params") or {}
    tool, args = params.get("name"), params.get("arguments") or {}
    log({"tool": tool, "args": args})
    if tool == "query_graph_tool":
        n = CALLERS.get(args.get("target"), 0)
        cap = int(args.get("max_results", 100))
        rows = [{"name": "c%d" % i, "qualified_name": "m%d.py::c%d" % (i, i), "kind": "Function",
                 "file_path": "m%d.py" % i, "line_start": 1, "is_test": False}
                for i in range(min(n, cap))]
        payload = {"status": "ok", "result_count": n, "results_omitted": max(0, n - cap),
                   "results": rows, "edges": []}
    else:
        payload = {}
    send({"jsonrpc": "2.0", "id": msg.get("id"),
          "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})
'''


class _Case(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="k6-crg-")
        with open(os.path.join(self.tmp, "serve"), "w", encoding="utf-8") as fh:
            fh.write(_SERVER)
        os.chdir(self.tmp)
        # 0.0.26 O13: the fake server stands for a BUILT index (this module grades the wire).
        self.client = CodeReviewGraphClient(name=sys.executable, root=self.tmp, timeout=10.0,
                                            index_probe=lambda _root: True)
        self.addCleanup(self.client.close)

    def tearDown(self):
        os.chdir(self._cwd)

    def calls(self):
        try:
            with open(os.path.join(self.tmp, "calls.log"), encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []


class TestTheHandshake(_Case):
    def test_the_handshake_is_accepted(self):
        rows = self.client.query("callers", "small", root=self.tmp)
        self.assertEqual(len(rows), 3)

    def test_the_client_names_its_version(self):
        from mokata import __version__
        self.assertEqual(client_info(), {"name": "mokata", "version": __version__})


class TestCapsAreDisclosed(_Case):
    def test_mokata_asks_for_its_own_cap(self):
        self.client.query("callers", "small", root=self.tmp)
        sent = [c["args"] for c in self.calls() if c["tool"] == "query_graph_tool"]
        self.assertEqual(sent[-1].get("max_results"), CRG_MAX_RESULTS)

    def test_a_capped_answer_says_it_is_partial(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=self.tmp,
                                         client=self.client)
        r = backend.query("callers", "hub")
        self.assertEqual(r.count, CRG_MAX_RESULTS)
        self.assertTrue(r.truncated, "a capped graph answer must not read as complete")
        self.assertEqual(r.total, 2500)
        self.assertIn(f"{TRUNCATED_NOTE_PREFIX} {CRG_MAX_RESULTS} of 2500 shown", r.note)
        wire = r.to_dict()
        self.assertEqual((wire["truncated"], wire["total"]), (True, 2500))

    def test_a_complete_answer_is_not_marked(self):
        backend = CodeReviewGraphBackend(name="code-review-graph", root=self.tmp,
                                         client=self.client)
        r = backend.query("callers", "small")
        self.assertFalse(r.truncated)
        self.assertIsNone(r.total)
        self.assertEqual(r.note, "answered by the codebase graph")

    def test_a_capped_refs_union_is_partial(self):
        rows = self.client.query("refs", "hub", root=self.tmp)
        self.assertTrue(rows.truncated)
        self.assertIsNone(rows.total, "a union's true size is unknown — no total is claimed")

    def test_a_capped_blast_radius_is_partial(self):
        """0.0.26 O2 — the blast radius walks `callers_of` over the real wire; a hub whose first hop
        the tool caps is a PARTIAL answer and says which bound bit."""
        rows = self.client.query("blast_radius", "hub", root=self.tmp, depth=1)
        self.assertTrue(rows.truncated)
        self.assertEqual(len(rows), CRG_MAX_RESULTS)
        self.assertIn("capped a callers_of answer", rows.cut_why)
        self.assertEqual({c["tool"] for c in self.calls()}, {"query_graph_tool"})


if __name__ == "__main__":
    unittest.main()
