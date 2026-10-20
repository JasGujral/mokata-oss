"""0.0.25 `IDE-SERVER-LIGHT` — the IDE server keeps ONE code graph per repo, not one per request.

Reported by Jas (2026-10-19): "keep the server light — it seems it's running a lot of non-used
processes". Measured on a 2-file repo with code-review-graph adopted, 15 page requests:
15 `code-review-graph --version` cold starts + 9 `code-review-graph serve` processes (~125 MB
each, dropped unclosed → zombies) before; 0 + 1 after.

Business rules:
  * one KnowledgeLayer per repo for the server's life; rebuilt only when the manifest changes, and
    the replaced layer's helper process is CLOSED, not abandoned;
  * the server closes every cached layer on the way out;
  * a live serve session answers `health()` without spawning anything; a version answer is
    remembered only when it succeeded;
  * the shared session never interleaves two requests (the IDE answers on worker threads).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav
from mokata.knowledge import crg_client as C


class _Layer:
    def __init__(self):
        self.client = mock.Mock()
        self.primary = mock.Mock(client=self.client)


class OneLayerPerRepo(unittest.TestCase):
    def setUp(self):
        nav.close_layers()
        self.addCleanup(nav.close_layers)
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, ".mokata"))
        self.manifest = os.path.join(self.d, ".mokata", "manifest.json")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write("{}")
        self.built = []

        def _build(_surface):
            layer = _Layer()
            self.built.append(layer)
            return layer
        p1 = mock.patch("mokata.knowledge.KnowledgeLayer.from_surface", side_effect=_build)
        p2 = mock.patch("mokata.config.Surface.load", return_value=object())
        for p in (p1, p2):
            p.start()
            self.addCleanup(p.stop)

    def test_requests_share_one_layer(self):
        first = nav._layer(self.d)
        for _ in range(5):
            self.assertIs(nav._layer(self.d), first)
        self.assertEqual(len(self.built), 1)

    def test_a_manifest_change_rebuilds_and_closes_the_old_one(self):
        old = nav._layer(self.d)
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write('{"changed": true}')
        os.utime(self.manifest, ns=(time.time_ns(), time.time_ns() + 10_000_000))
        new = nav._layer(self.d)
        self.assertIsNot(new, old)
        old.client.close.assert_called_once_with()
        new.client.close.assert_not_called()

    def test_shutdown_closes_every_cached_layer(self):
        layer = nav._layer(self.d)
        nav.close_layers()
        layer.client.close.assert_called_once_with()
        self.assertIsNot(nav._layer(self.d), layer, "the cache is empty after close_layers")

    def test_the_server_closes_layers_on_the_way_out(self):
        import inspect
        from mokata.ide import server
        src = inspect.getsource(server.serve)
        self.assertIn("nav.close_layers()", src.split("finally:", 1)[1])


class TheGraphClientDoesNotRespawn(unittest.TestCase):
    def _client(self, results):
        calls = []

        def run_cli(args):
            calls.append(args)
            return results.pop(0)
        return C.CodeReviewGraphClient(run_cli=run_cli, call_tool=lambda t, p: {}), calls

    def test_a_live_session_answers_health_without_a_spawn(self):
        client, calls = self._client([])
        live = mock.Mock()
        live.poll.return_value = None
        client._session = mock.Mock(_proc=live)
        self.assertTrue(client.health("."))
        self.assertEqual(calls, [])

    def test_a_successful_version_is_remembered(self):
        client, calls = self._client([(0, "code-review-graph 2.3.1", "")])
        self.assertEqual(client.version(), client.version())
        self.assertEqual(len(calls), 1)

    def test_a_failed_version_is_asked_again(self):
        client, calls = self._client([(1, "", "boom"), (0, "code-review-graph 2.3.1", "")])
        self.assertIsNone(client.version())
        self.assertIsNotNone(client.version())
        self.assertEqual(len(calls), 2)

    def test_close_stops_the_session_and_forgets_it(self):
        client, _ = self._client([])
        session = mock.Mock()
        client._session = session
        client.close()
        session.close.assert_called_once_with()
        self.assertIsNone(client._session)


class TheSharedSessionIsOneAtATime(unittest.TestCase):
    def test_concurrent_calls_never_interleave(self):
        s = C._McpStdioSession("code-review-graph")
        inside, overlaps = [0], []

        def _rpc(method, params):
            inside[0] += 1
            if inside[0] > 1:
                overlaps.append(params)
            time.sleep(0.01)
            inside[0] -= 1
            return {"content": [{"type": "text", "text": "{}"}]}
        with mock.patch.object(s, "_ensure"), mock.patch.object(s, "_rpc", side_effect=_rpc):
            ts = [threading.Thread(target=s.call, args=("t", {"i": i})) for i in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(overlaps, [])


if __name__ == "__main__":
    unittest.main()
