"""0.0.27 P12 (verify R1): history paging under clock skew yields every item exactly once.

Business rule: the Activity history is complete and never repeats an item, even when the wall clock
is not monotonic in the event sequence (NTP steps, VM resumes, a second machine writing). Rev 2's
bounded snapshot side dropped the exclusive marker on a clamped page and re-served the writes at the
cut time; this pins the verifier's own skew construction (600 events at ±500 s, 300 snapshots at
±600 s, limits 3 and 10) as a fixed case.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest

import _support  # noqa: F401
from _support import sqlite_disk_ok

from mokata.events import store_for_root
from mokata.events.schema import GateDecision
from mokata.ide import activity as A
from mokata.ide import snapshots as S
from mokata.init import init_repo

T0 = 1_760_000_000.0


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
@unittest.skipIf(shutil.which("git") is None, "needs git")
class HistoryUnderClockSkewIsExactlyOnce(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        subprocess.run(["git", "-C", self.d, "init", "-q", "-b", "main"], check=True,
                       capture_output=True, stdin=subprocess.DEVNULL)
        init_repo(root=self.d, profile="standard", assume_yes=True, out=lambda *a, **k: None)
        store_for_root(self.d).emit(GateDecision(gate="write_gate", decision="allowed"),
                                    session_id="X", run_id="RX")
        rng = random.Random(27)
        st = store_for_root(self.d)
        c = sqlite3.connect(st.path)
        with c:
            c.executemany(
                "INSERT INTO events (event_id, ts, schema_version, type, session_id, run_id, actor,"
                " duration_ms, ledger_seq, data) VALUES (?, ?, 1, 'gate_decision', ?, ?, '', NULL, NULL, ?)",
                [(f"v{i}", _iso(T0 + rng.uniform(-500, 500) // 1), "S1", "R1",
                  json.dumps({"gate": "write_gate", "decision": "allowed", "subject": "a"}))
                 for i in range(600)])
        c.close()
        p = os.path.join(S.store_dir(self.d), "timeline.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            for i in range(300):
                fh.write(json.dumps({"id": f"s{i}", "t": T0 + rng.uniform(-600, 600), "session": "S1",
                                     "run": "R1", "path": "src/a.py", "kind": "after", "sha": "",
                                     "state": "ok", "size": 1}) + "\n")

    def _walk(self, limit):
        before, ids = None, []
        while True:
            page = A.history(self.d, "S1", before=before, limit=limit)
            ids += [i["id"] for i in page["items"]]
            if page["next"] is None:
                return ids
            before = page["next"]

    def test_every_item_once_at_small_pages(self):
        everything = set(self._walk(10_000))
        self.assertGreaterEqual(len(everything), 900)
        for limit in (3, 10):
            ids = self._walk(limit)
            self.assertEqual(len(ids), len(set(ids)), f"duplicates at limit={limit}")
            self.assertEqual(set(ids), everything, f"gaps at limit={limit}")
