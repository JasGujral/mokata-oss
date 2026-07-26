"""GR.S2 (k)+(l) — keep-functional machinery: liveness auto-recovery + version handshake.

The adopted code-review-graph is kept functional AUTOMATICALLY for OPERATIONAL acts only
(health probe, index refresh/rebuild, one bounded retry) — never a durable/manifest write.
When it dies mid-session, queries degrade LOUD to the AST floor (zero silent wrong answers)
and auto-recover when it is back. A version outside the grounded range is REFUSED at the
handshake rather than mis-mapped.

  liveness regression (dies -> loud AST -> recover) ... TestLiveness
  version-skew refusal ............................... TestVersionSkew
  recovery is operational only (no manifest write) ... TestRecoveryIsOperational
"""

import os
import tempfile
import unittest

from _support import write_sample_repo

from mokata.detect import Detector
from mokata.knowledge import (
    AstBackend,
    CodeReviewGraphBackend,
    CodeReviewGraphClient,
    CrgVersionSkew,
    KnowledgeLayer,
)
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.router import Router


class FakeCrgClient:
    supports_semantic = True

    def __init__(self, rows=None, version="2.3.6", revive_on_refresh=True):
        self.rows = rows or {"callers": [{"path": "svc.py", "line": 3, "symbol": "svc.run"}]}
        self._version = version
        self.alive = True
        self.revive_on_refresh = revive_on_refresh
        self.refreshes = 0

    def query(self, kind, target, root, depth=1):
        if not self.alive:
            raise RuntimeError("code-review-graph serve is not running")
        return list(self.rows.get(kind, []))

    def semantic(self, query, root, kind=None, limit=20):
        if not self.alive:
            raise RuntimeError("dead")
        return []

    def refresh(self, root, full=False):
        self.refreshes += 1
        if self.revive_on_refresh:
            self.alive = True
        return self.alive

    def version(self, root=None):
        return self._version

    def health(self, root):
        return self.alive


def _graph_layer(root, client):
    m = Manifest.from_dict(build_manifest_data("full", "0.1.0"))
    router = Router(m, Detector(overrides={"code-review-graph": True}))
    return KnowledgeLayer.from_router(router, root=root, client=client)


class TestLiveness(unittest.TestCase):
    def test_healthy_graph_answers_from_the_graph(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = _graph_layer(d, FakeCrgClient())
            self.assertTrue(layer.uses_graph)
            r = layer.callers("svc.run")
            self.assertFalse(r.degraded)
            self.assertEqual(r.backend, "code-review-graph")

    def test_fallback_is_the_ast_floor_on_python(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            layer = _graph_layer(d, FakeCrgClient())
            self.assertIsInstance(layer.fallback, AstBackend)

    def test_death_auto_recovers_via_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            client = FakeCrgClient(revive_on_refresh=True)
            layer = _graph_layer(d, client)
            layer.callers("svc.run")           # healthy
            client.alive = False               # CRG serve dies mid-session
            r = layer.callers("svc.run")        # one bounded recovery brings it back
            self.assertGreaterEqual(client.refreshes, 1)
            self.assertFalse(r.degraded)        # recovered -> real graph answer again
            self.assertEqual(r.backend, "code-review-graph")

    def test_unrecoverable_death_degrades_loud_to_ast_no_silent_wrong_answer(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            client = FakeCrgClient(revive_on_refresh=False)   # refresh can't revive it
            layer = _graph_layer(d, client)
            client.alive = False
            r = layer.callers("compute")        # a symbol the AST floor can answer
            self.assertTrue(r.degraded, "an unrecoverable graph must degrade LOUD, not silently")
            self.assertEqual(r.backend, "ast")  # the AST floor answered
            self.assertTrue(r.references)       # a real floor answer, not empty
            self.assertIn("fell back", r.note.lower() + r.note)

    def test_recovers_when_graph_comes_back_on_its_own(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            client = FakeCrgClient(revive_on_refresh=False)
            layer = _graph_layer(d, client)
            client.alive = False
            layer.callers("compute")            # degrades to AST
            client.alive = True                 # external revival
            r = layer.callers("svc.run")        # graph answers again, no degrade
            self.assertFalse(r.degraded)
            self.assertEqual(r.backend, "code-review-graph")


class TestVersionSkew(unittest.TestCase):
    def test_backend_handshake_refuses_out_of_range_version(self):
        client = FakeCrgClient(version="3.5.0")
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".", client=client)
        with self.assertRaises(CrgVersionSkew):
            backend.handshake()

    def test_backend_handshake_accepts_in_range(self):
        client = FakeCrgClient(version="2.3.6")
        backend = CodeReviewGraphBackend(name="code-review-graph", root=".", client=client)
        self.assertEqual(backend.handshake(), "2.3.6")

    def test_real_client_version_compat_via_injected_cli(self):
        def run_cli(args):
            return (0, "code-review-graph 3.9.9\n", "")
        c = CodeReviewGraphClient(name="code-review-graph", root=".", run_cli=run_cli)
        with self.assertRaises(CrgVersionSkew):
            c.check_version_compat()


class TestRecoveryIsOperational(unittest.TestCase):
    """Recovery is a run-state act only — it must never write the manifest."""

    def test_recovery_writes_no_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            write_sample_repo(d)
            client = FakeCrgClient(revive_on_refresh=True)
            layer = _graph_layer(d, client)
            client.alive = False
            layer.callers("svc.run")            # triggers recovery
            self.assertFalse(os.path.exists(os.path.join(d, ".mokata", "manifest.json")),
                             "operational recovery must not write a durable manifest")


if __name__ == "__main__":
    unittest.main()
