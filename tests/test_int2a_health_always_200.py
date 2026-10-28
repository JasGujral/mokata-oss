"""INT-2a (0.0.26) — `/api/health` answers 200 with what is known NOW, never a bare 202 job handle.

The contract probe flaked under box load (P33/P35/P38/P39): the checks are the `health.check` job,
and past P32's 300 ms sync window the route answered 202 + handle — a different shape from the
same probe. The fix: the checks still run as that job, but the route always answers 200; while they
run, the body is the CHECKING shape (every row group `checking`, `checking: true`, the job in
`pending`), and `job=<id>` fetches the finished rows. These tests SIMULATE load deterministically
(the check blocks on an Event), so they do not depend on how busy the box is.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import json
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from _health_answer import finished_health
from mokata.ide import contract as C


def _client(root):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    c = TestClient(build_app(AppState(root, "tok", 9999, None, False)),
                   base_url="http://127.0.0.1:9999")
    c.get("/pair?t=tok")
    return c


def _health_schema():
    with open(C.CORE_FILE, encoding="utf-8") as fh:
        core = json.load(fh)
    entry = next(e for e in core["routes"] if e["path"] == "/api/health")
    return entry, core["schemas"]


class HealthAnswersTwoHundredUnderLoad(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mk-int2a-")
        self.addCleanup(shutil.rmtree, self.root, True)
        from mokata.ide import health as H
        self.H = H
        self.gate = threading.Event()
        real = H.health

        def slow(*a, **kw):                     # a loaded box: the checks take as long as we say
            self.gate.wait(30)
            return real(*a, **kw)

        p = mock.patch.object(H, "health", side_effect=slow)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.gate.set)
        self.c = _client(self.root)

    def test_INT2a_slow_checks_answer_200_checking_never_202(self):
        """(Firing control: route `/api/health` back through `_json(job=…)` and this is a 202.)"""
        r = self.c.get("/api/health", params={"sync_ms": "50"})
        self.assertEqual(200, r.status_code, r.text)
        body = r.json()
        self.assertTrue(body["checking"])
        self.assertTrue(body["rows"])
        self.assertTrue(all(row["state"] in ("checking", "not set up") for row in body["rows"]))
        self.assertEqual("health.check", body["pending"]["kind"])
        entry, schemas = _health_schema()
        self.assertEqual(["200"], sorted(entry["responses"]), "health declares ONE status")
        self.assertEqual([], C.validate(body, entry["responses"]["200"]["schema"], schemas),
                         "the checking body must satisfy the same 200 schema")

    def test_INT2a_sync_ms_zero_is_still_200(self):
        r = self.c.get("/api/health", params={"full": "1", "sync_ms": "0"})
        self.assertEqual(200, r.status_code)
        self.assertTrue(r.json()["checking"])

    def test_INT2a_the_finished_rows_follow_from_the_pending_job(self):
        first = self.c.get("/api/health", params={"sync_ms": "0"}).json()
        self.assertTrue(first["checking"])
        self.gate.set()                         # the load lifts; the job finishes
        done = finished_health(self.c).json()
        self.assertNotIn("checking", done)
        self.assertNotIn("checking", {row["state"] for row in done["rows"]})
        self.assertIn("mokata", {row["key"] for row in done["rows"]})

    def test_INT2a_fast_checks_answer_the_rows_directly(self):
        self.gate.set()
        r = self.c.get("/api/health", params={"sync_ms": "2000"})
        self.assertEqual(200, r.status_code)
        self.assertFalse(r.json().get("checking", False))


if __name__ == "__main__":
    unittest.main()
