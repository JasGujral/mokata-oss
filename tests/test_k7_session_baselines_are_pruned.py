"""0.0.25 K7 — per-session freshness baselines do not pile up forever, and pruning cannot hurt a live one.

Every new session key (each IDE server start) writes a `graph_freshness_index__<key>.json` cold
baseline — ~2 MB on the 7k-file scale repo — and none was ever deleted (36 files, 36 MB, after a
day of restarts). A new session's cold walk now prunes them: it keeps its own, the newest
`SESSION_BASELINES_KEPT`, and anything younger than `SESSION_BASELINE_GRACE_S`; a pruned baseline
goes WITH its `graph_freshness__<key>` state, so a session that was in fact still alive re-seeds
itself on its next pass (one extra cold walk) rather than running blind.

Mutations this module was checked against (each went RED, then was restored):
  M1 the cold walk no longer calls `prune_stale_sessions`  -> test_a_new_session_prunes_old_baselines
  M2 the state file is left behind (index deleted alone)   -> test_a_pruned_session_that_was_alive_reseeds_itself
  M3 the recency grace ignored                             -> test_recent_and_own_baselines_are_never_pruned
"""

import _support  # noqa: F401

import os
import tempfile
import time
import unittest

from mokata.knowledge import freshness as fr
from mokata.state import StateStore


class _Layer:
    primary = None
    fallback = None


class TestBaselinePruning(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k7-")
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 1\n")
        self.sdir = tempfile.mkdtemp(prefix="k7-state-")
        self.store = StateStore(self.sdir)

    def _old_session(self, key, age_s):
        self.store.write(fr.FRESHNESS_INDEX_PREFIX + key, {"entries": {}})
        self.store.write(fr.FRESHNESS_STATE_PREFIX + key, {"cold_done": True})
        t = time.time() - age_s
        for prefix in (fr.FRESHNESS_INDEX_PREFIX, fr.FRESHNESS_STATE_PREFIX):
            os.utime(self.store.path(prefix + key), (t, t))

    def _baselines(self):
        return sorted(n[len(fr.FRESHNESS_INDEX_PREFIX):-5] for n in os.listdir(self.sdir)
                      if n.startswith(fr.FRESHNESS_INDEX_PREFIX) and n.endswith(".json"))

    def _ctl(self, key):
        c = fr.FreshnessController(self.root, state=self.store, session_id=key)
        self.assertEqual(c.session_id, key)
        return c

    def test_a_new_session_prunes_old_baselines(self):
        for i in range(20):
            self._old_session(f"old{i:02d}", age_s=86400 + i * 60)   # old00 newest ... old19 oldest
        self._ctl("fresh").ensure_fresh(_Layer())
        kept = self._baselines()
        self.assertIn("fresh", kept)
        self.assertEqual(len(kept), fr.SESSION_BASELINES_KEPT,
                         "own + the newest (KEPT - 1) others: the bound holds")
        self.assertIn("old00", kept)
        self.assertNotIn("old19", kept)
        self.assertFalse(self.store.exists(fr.FRESHNESS_STATE_PREFIX + "old19"),
                         "a pruned baseline's state goes with it")

    def test_recent_and_own_baselines_are_never_pruned(self):
        for i in range(fr.SESSION_BASELINES_KEPT + 4):
            self._old_session(f"live{i:02d}", age_s=30)                # all inside the grace
        ctl = self._ctl("me")
        self._old_session("me", age_s=10 * 86400)                       # own: ancient, kept
        removed = ctl.prune_stale_sessions()
        self.assertEqual(removed, [])
        self.assertEqual(len(self._baselines()), fr.SESSION_BASELINES_KEPT + 5)

    def test_a_pruned_session_that_was_alive_reseeds_itself(self):
        for i in range(fr.SESSION_BASELINES_KEPT + 2):
            self._old_session(f"n{i:02d}", age_s=86400)
        self._old_session("sleeper", age_s=5 * 86400)
        self.assertIn("sleeper", self._ctl("me").prune_stale_sessions())
        self.assertNotIn("sleeper", self._baselines())
        # the sleeper wakes and answers: its next pass re-runs the cold walk and re-seeds
        self._ctl("sleeper").ensure_fresh(_Layer())
        self.assertIn("sleeper", self._baselines())
        self.assertIsNotNone(self._ctl("sleeper")._load_index())


if __name__ == "__main__":
    unittest.main()
