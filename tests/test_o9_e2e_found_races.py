"""0.0.26 O9 — the races the IDE browser e2e found, pinned without a browser.

  * A job that started — and finished — before the page's event stream connected was never heard:
    the first stream opened at the bus's current seq, so the Activity drawer showed a 6 ms AST
    re-parse as "queued" forever (and the chip never went back to idle). The stream now opens
    with the latest event of every job the bus keeps.
  * The page let a stale `queued` (the POST's answer, arriving after the `done` event) overwrite
    `done`. A job id is one run: a finished job never goes back (activity.js `update`).
  * Welcome (P39) yanked a user who moved BEFORE the first refresh landed off the screen they
    picked: "not moved yet" is now "no key or pointer input yet".

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _support  # noqa: F401

import json
import os
import shutil
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "src", "mokata", "ide", "static", "features")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class AJobFinishedBeforeTheStreamOpenedIsStillHeard(unittest.TestCase):
    def test_the_first_stream_carries_the_done_state_of_an_earlier_job(self):
        try:
            import httpx
        except ImportError:                      # pragma: no cover - [ide] dep
            self.skipTest("httpx is not installed")
        from test_ls_ide_light_at_scale import _Live, _repo
        root = tempfile.mkdtemp(prefix="mk-o9-race-")
        self.addCleanup(shutil.rmtree, root, True)
        _repo(root)
        live = _Live(root)
        self.addCleanup(live.stop)
        hdr = dict(live.headers, **{"x-mokata-client": "pg"})
        with httpx.Client(headers=hdr, timeout=10) as cl:
            # The job runs to DONE with no stream open (the page's first requests race its stream).
            r = cl.post(live.base + "/api/jobs", json={"kind": "tree.walk", "args": {}},
                        headers={"Origin": live.base})
            self.assertEqual(200, r.status_code, r.text)
            jid = r.json()["id"]
            t0 = time.monotonic()
            while cl.get(live.base + "/api/jobs/" + jid).json()["job"]["state"] != "done":
                self.assertLess(time.monotonic() - t0, 10, "the job never finished")
                time.sleep(0.05)
            got = None
            with cl.stream("GET", live.base + "/api/events?client=pg") as ev:
                t0 = time.monotonic()
                for ln in ev.iter_lines():
                    if ln.startswith("data:") and jid in ln:
                        got = json.loads(ln[5:])
                        break
                    if time.monotonic() - t0 > 5:
                        break
        self.assertIsNotNone(got, "the page's first stream never said this job happened")
        self.assertEqual("done", got["state"])


class TheDrawerNeverUnfinishesAJob(unittest.TestCase):
    def test_update_ignores_a_non_final_snapshot_of_a_finished_job(self):
        src = _read(os.path.join(STATIC, "activity.js"))
        body = src[src.index("function update(j)"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn('const FINAL = new Set(["done", "failed", "cancelled"])', src)
        self.assertIn("if (prev && FINAL.has(prev.state) && !FINAL.has(j.state)) return;",
                      body.splitlines()[2], "the guard must run before the snapshot is stored")


class WelcomeDoesNotYankAUserWhoMoved(unittest.TestCase):
    def test_input_before_the_first_refresh_keeps_the_users_screen(self):
        src = _read(os.path.join(STATIC, "welcome.js"))
        self.assertIn('document.addEventListener("keydown", moved', src)
        self.assertIn('document.addEventListener("pointerdown", moved', src)
        self.assertIn("if (!d.first_run || W.moved ||", src)


if __name__ == "__main__":
    unittest.main()
