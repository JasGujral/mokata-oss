"""O14b — the MCP server's code-graph session is shared, and the amend lens is never cut.

1. **The leaked `code-review-graph serve`.** Measured on a django copy (0.0.26 O14): every MCP call
   that touched the graph started a fresh `serve` and never closed it, so the server collected one
   live child process per call until it exited, and every call paid the serve start-up again. The
   server now shares ONE session per repo (`crg_client.enable_session_pool`). Proven over real
   stdio with a stand-in `code-review-graph` on PATH: N graph-backed calls leave one child.
   FIRING CONTROL: drop `enable_session_pool()` from `mcp.server.main` and the count grows with N.

2. **No clock on gate 2** (Jas, U10 overrule: no arbitrary limits; long work is visible). The
   blast-radius lens on a scope-WIDENING amendment runs as long as it needs; on MCP a long one
   answers `running` and the re-call joins it (O14). The answer says which BASIS it was computed on
   (graph, or the lexical floor), and a lens that RAISES still fails closed.
   FIRING CONTROL: restore a budgeted lens that refuses on expiry and `test_a_slow_lens_is_waited_for`
   goes red; drop the basis line and `test_the_basis_is_disclosed` goes red.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk  # noqa: E402

from test_o14_mcp_stability import _Stdio, _repo  # noqa: E402

_FAKE_CRG = r'''
import json, sys
if "--version" in sys.argv:
    print("code-review-graph 2.3.9"); sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "serve":
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg.get("method") == "initialize":
            res = {"protocolVersion": "2024-11-05", "capabilities": {},
                   "serverInfo": {"name": "stand-in", "version": "2.3.9"}}
        else:
            res = {"content": [{"type": "text", "text": json.dumps({"results": []})}]}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}) + "\n")
        sys.stdout.flush()
    sys.exit(0)
sys.exit(1)
'''


def _children_serving(pid):
    """Live child processes of `pid` whose command line is a `serve` (Linux /proc)."""
    n = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as fh:
                stat = fh.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            if ppid != pid:
                continue
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
            if b"serve" in argv and stat.rsplit(")", 1)[1].split()[0] != "Z":
                n += 1
        except (OSError, ValueError, IndexError):
            continue
    return n


@unittest.skipUnless(sys.platform.startswith("linux"), "counts children through /proc")
@_mcp_sdk.requires_mcp_sdk
class TestR_O14b_OneGraphSessionPerServer(unittest.TestCase):

    N = 5

    def test_R_O14b_graph_calls_do_not_leak_serve_processes(self):
        repo = _repo()
        bindir = tempfile.mkdtemp(prefix="o14b-bin-")
        self.addCleanup(shutil.rmtree, repo, True)
        self.addCleanup(shutil.rmtree, bindir, True)
        script = os.path.join(bindir, "fake_crg.py")
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_FAKE_CRG)
        tool = os.path.join(bindir, "code-review-graph")
        with open(tool, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({script!r}, "
                     f"run_name='__main__')\n")
        os.chmod(tool, 0o755)
        os.makedirs(os.path.join(repo, ".code-review-graph"))
        db = sqlite3.connect(os.path.join(repo, ".code-review-graph", "graph.db"))
        db.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        db.execute("INSERT INTO metadata VALUES ('last_updated', '2026-10-23T00:00:00')")
        db.commit()
        db.close()

        srv = _Stdio(repo, "run-o14b-pool",
                     env={"MOKATA_CRG": tool})     # O15: named, never found on PATH
        try:
            counts, backends = [], []
            for _ in range(self.N):
                res, _dt = srv.tool("query", {"path": repo, "kind": "callers", "target": "f1"})
                backends.append(res.get("backend"))
                time.sleep(0.2)
                counts.append(_children_serving(srv.p.pid))
        finally:
            srv.close()
        self.assertIn("code-review-graph", backends, f"the stand-in graph never answered: {backends}")
        self.assertEqual(max(counts), 1, f"live `serve` children after each call: {counts}")


class TestR_O14b_TheLensHasNoClock(unittest.TestCase):
    """Drive the real `begin_amend` with gate 2 live and everything else planted (as a26 did)."""

    def _gate_two(self, lens, uses_graph=False):
        from mokata.engine import amend as A

        class _Diff:
            widens_scope = True
            authorized_added = ("src/new.py",)
            undeferred = ()
            added_criteria = ("AC9",)

            def to_dict(self):
                return {}

            def render(self):
                return ""

        class _Spec:
            approach, title, criteria = "a", "t", [1]

        store = mock.Mock()
        store.read.return_value = {}
        surface = mock.Mock()
        surface.state = store
        layer = mock.Mock()
        layer.uses_graph = uses_graph
        layer.backend_name = "code-review-graph" if uses_graph else "grep"
        passed = mock.Mock(passed=True)
        with mock.patch.object(A, "load_emitted_spec", return_value=_Spec()), \
                mock.patch.object(A, "spec_version", return_value=1), \
                mock.patch.object(A, "diff_specs", return_value=_Diff()), \
                mock.patch.object(A, "amend_from_state", return_value=mock.Mock(is_open=True)), \
                mock.patch.object(A, "open_amend"), \
                mock.patch.object(A, "run_completeness_gate", return_value=passed), \
                mock.patch.object(A, "red_owed_for", return_value=()), \
                mock.patch("mokata.brainstorm_impact.compute_impact", lens):
            return A.begin_amend(surface, _Spec(), [], run_id="run", store=store, layer=layer)

    def test_a_slow_lens_is_waited_for(self):
        from mokata.brainstorm_impact import ApproachImpact

        def slow(*_a, **_k):
            time.sleep(1.5)
            return ApproachImpact(approach="a", degraded=True)

        plan = self._gate_two(slow)
        self.assertTrue(plan.ok, f"a slow lens was refused: {plan.gate} {plan.reason_text}")
        self.assertIsNotNone(plan.impact)

    def test_the_basis_is_disclosed(self):
        from mokata.brainstorm_impact import ApproachImpact
        floor = self._gate_two(lambda *_a, **_k: ApproachImpact(approach="a", degraded=True))
        self.assertIn("floor", floor.lens_basis)
        self.assertIn("grep", floor.lens_basis)
        graph = self._gate_two(lambda *_a, **_k: ApproachImpact(approach="a"), uses_graph=True)
        self.assertIn("code graph", graph.lens_basis)

    def test_a_raising_lens_still_fails_closed(self):
        def boom(*_a, **_k):
            raise RuntimeError("engine broke")

        plan = self._gate_two(boom)
        self.assertFalse(plan.ok)
        self.assertEqual(plan.gate, "blast-radius")

    def test_no_clock_remains(self):
        from mokata.engine import amend as A
        self.assertFalse(hasattr(A, "_LENS_BUDGET_SECS"))
        self.assertFalse(hasattr(A, "_bounded_lens"))


if __name__ == "__main__":
    unittest.main()


class TestR_O14b_SecretGuardNamesAreNotKeys(unittest.TestCase):
    """The own-corpus false positives found on integration 4454e77 (each would WALL a legitimate
    write, non-overridably), cleared at the predicate — and the keys next to them still block.
    FIRING CONTROL: remove any one of `_is_alphabet_listing`, `_is_env_var_name`, `_DECL_NAME_RE`
    or the subscript-key rule in `govern/secrets.py` and its row here goes red."""

    # a generated-looking key, assembled so this file is not itself a blockable literal
    KEY = "".join(("xY9kZ2mQ7p", "L4nR8vT3wB", "6cF1dG5hJ0"))

    def _flags(self, text):
        from mokata.govern import secrets
        return [f for f in secrets.scan(text=text) if f.kind == "high-entropy-token"]

    def test_names_and_listings_pass(self):
        for line in (
                '_SID_SAFE = frozenset("0123456789abcdefghijklmnopqrstuvwxyz'
                'ABCDEFGHIJKLMNOPQRSTUVWXYZ-_")',
                'ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"',
                "class R18_APathologicalTsFileIsNeverARaw500(unittest.TestCase):",
                'self.page.fill("#sq", "func schema_k8sio_api_core_v1_PodSpec(")',
                'self.M["R18_responses_ge_500"] = len(self.bad) - n0',
                "dedicated `MOKATA_G2_JUDGE_API_KEY` (LoCoMo reuses that client)"):
            with self.subTest(line=line[:40]):
                self.assertEqual(self._flags(line), [])

    def test_keys_beside_them_still_block(self):
        for line in (
                'ALPHABET = "%s"' % self.KEY,
                "class Foo:  # %s" % self.KEY,
                'self.M["k"] = "%s"' % self.KEY,
                "export it as `%s`" % "_".join(("XQ7ZKP", "RT9VBN", "LMQ2W", "ZZK8T")),
                "leftover %s" % self.KEY):
            with self.subTest(line=line[:40]):
                self.assertTrue(self._flags(line), line)
