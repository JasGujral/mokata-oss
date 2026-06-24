"""Stage 20 — memory round-trips: persistence across sessions + surfaced healing.

A human-gated decision written in one session is recalled in a later one (proving the
SQLite floor persists), and a contradicting fact is SURFACED as a proposal rather than
silently rewritten — both facts stay active until a human resolves it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import DECISION, MemoryItem, MemoryStore


def _silent(_):
    pass


def _init(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=_silent)
    return Surface.load(d)


class TestMemoryPersistsAcrossSessions(unittest.TestCase):
    def test_decision_written_then_recalled_in_a_later_session(self):
        with tempfile.TemporaryDirectory() as d:
            # session 1 — write a gated decision, then close the store
            store1 = MemoryStore.from_surface(_init(d, "standard"))
            wr = store1.remember(
                MemoryItem.create("decision:db", "postgres", mtype=DECISION),
                assume_yes=True)
            self.assertTrue(wr.committed)
            store1.close()

            # session 2 — reload the surface; a new store over the same backing file
            store2 = MemoryStore.from_surface(Surface.load(d))
            hits = store2.recall("decision:db")
            self.assertEqual([i.value for i in hits], ["postgres"])
            store2.close()

            # the SQLite floor persisted under .mokata/
            self.assertTrue(os.path.exists(
                os.path.join(d, ".mokata", "memory", "memory.db")))


class TestHealingIsSurfacedNotApplied(unittest.TestCase):
    def test_contradiction_is_proposed_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            store = MemoryStore.from_surface(_init(d, "standard"))
            store.remember(MemoryItem.create("decision:db", "postgres", mtype=DECISION),
                           assume_yes=True)
            store.remember(MemoryItem.create("decision:db", "mysql", mtype=DECISION),
                           assume_yes=True)

            proposals = store.detect_issues()
            self.assertIn("contradiction", [p.kind for p in proposals])

            # surfacing changed nothing — both facts remain active for a human to resolve
            active = sorted(i.value for i in store.recall("decision:db"))
            self.assertEqual(active, ["mysql", "postgres"])
            store.close()


if __name__ == "__main__":
    unittest.main()
