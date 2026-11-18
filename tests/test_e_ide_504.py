"""0.0.29 lane E `IDE-UI-PIN-GAPS` - the IDE's one 504 has an in-process probe, held to its contract.

`tests/test_p0_ide_api_contract.py` probes every status a contract entry declares, in process. The
image route's 504 ("git did not answer": a partial clone fetching the blob over a dead network) is
the one status the app can answer that no contract probe reaches, because a probe needs a git that
goes silent. This file makes git go silent under the test's control - no clock, no wait - and holds
the answer to the contract.

Business rules:
  * a git that stops answering ends the request with 504 and the words, as JSON, never a hang, and
    the git process is killed;
  * the 504 body has the shape the contract names for a refusal (`error`, `state`, `note`), is
    `nosniff` like every error answer of the route, and says `state: unresponsive`;
  * a 504 is not a 404: the person is told git did not answer, not that the file is missing;
  * every status the route can answer is DECLARED by the contract entry for it - an undeclared
    status is an answer no client was told about.

How git "goes silent" without a wall clock: the bounded read (`mokata.bounded_io.read_bounded`) is
the thing that decides "silence for STALL_SECS"; the test makes that decision for it, once, at the
first read. Everything else - the route, the watcher of the client, the kill - runs for real.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import test_p9_ide_image_diff as P9

try:
    from starlette.testclient import TestClient
    from mokata.ide import contract as C
    from mokata.ide import imagediff as IM
    from mokata.ide.features import image as IMAGE_ROUTES
    from mokata.ide.server import AppState, build_app
    NO_IDE = None
except ImportError as exc:                       # pragma: no cover - depends on the extras
    NO_IDE = f"the [ide] extra is not installed ({exc})"
    IM = None

from mokata.bounded_io import READ_TIMEOUT, BoundedReadResult

CONTRACT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "mokata", "ide", "features", "image.contract.json")


class SilentGit:
    """A `git cat-file blob` that never says a word. Records whether it was killed."""

    def __init__(self):
        r, self._w = os.pipe()
        self.stdout = os.fdopen(r, "rb")
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True
        try:
            os.close(self._w)
        except OSError:
            pass

    def wait(self, timeout=None):
        return 0


@unittest.skipIf(NO_IDE, NO_IDE or "")
class TheImageRoutes504(P9.Repo):
    def setUp(self):
        super().setUp()
        P9.write(self.d, "a.png", P9.png(6, 4))
        self.commit()
        self.fake = SilentGit()
        self.reads = []

        def silence_reached(reader, timeout, cancelled=None):
            self.reads.append(timeout)            # the bound the product asked for: a read, not a sleep
            return BoundedReadResult(READ_TIMEOUT)

        for target in (mock.patch.object(IM, "_popen", lambda *a, **k: self.fake),
                       mock.patch("mokata.bounded_io.read_bounded", silence_reached)):
            target.start()
            self.addCleanup(target.stop)
        app = build_app(AppState(self.d, P9.TOKEN, P9.PORT, None, False))
        self.c = TestClient(app, base_url=P9.BASE, follow_redirects=False)
        self.c.get(f"/pair?t={P9.TOKEN}")
        with open(CONTRACT, encoding="utf-8") as fh:
            self.contract = json.load(fh)

    def raw(self, **params):
        return self.c.get("/api/image/raw", params=dict({"path": "a.png", "rev": "HEAD"}, **params))

    def test_a_git_that_goes_silent_is_a_504_with_the_words_and_is_killed(self):
        r = self.raw()
        self.assertEqual(504, r.status_code)
        body = r.json()
        self.assertEqual(("ImageUnavailable", "unresponsive"), (body["error"], body["state"]))
        self.assertIn("git did not answer", body["note"])
        self.assertTrue(self.fake.killed, "the silent git was left running")
        self.assertEqual(1, len(self.reads), "exactly one bounded read decided it")

    def test_the_504_body_has_the_shape_the_contract_names_for_a_refusal(self):
        r = self.raw()
        self.assertTrue(r.headers["content-type"].startswith("application/json"))
        self.assertEqual("nosniff", r.headers["x-content-type-options"])
        schemas = self.contract["schemas"]
        self.assertEqual([], C.validate(r.json(), {"$ref": "#/schemas/ImageRefusal"}, schemas))

    def test_a_504_is_not_dressed_as_a_missing_file(self):
        self.assertNotEqual(404, self.raw().status_code)
        self.assertEqual(404, self.raw(path="nothing.png").status_code,
                         "a path that is not there is still a 404 (the 504 is not a catch-all)")

    # RED on the tree: image.contract.json's /api/image/raw declares 400/404/413/415, not 504
    # (`features/image.py:_STATUS` answers it). The one-line fix is outside lane E's files: filed
    # IMAGE-CONTRACT-504-UNDECLARED (doc 84). Unexpected success when fixed -> drop the marker.
    @unittest.expectedFailure
    def test_every_status_the_raw_route_can_answer_is_declared_by_its_contract_entry(self):
        entry = next(e for e in self.contract["routes"] if e["path"] == "/api/image/raw")
        answers = {200, 400} | set(IMAGE_ROUTES._STATUS.values())
        declared = {int(s) for s in entry["responses"]}
        self.assertEqual([], sorted(answers - declared),
                         "the route answers these statuses and the contract does not declare them")


if __name__ == "__main__":
    unittest.main()
