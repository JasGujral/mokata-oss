"""D6/D7 — the approved approach is persisted (human-gated by approval) and
retrievable downstream through the config surface."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.brainstorm import (
    Approach,
    BrainstormGateError,
    BrainstormSession,
    load_approved_approach,
    persist_approach,
)
from mokata.config import Surface
from mokata.init import init_repo
from mokata.state import StateStore


def approved_session():
    s = BrainstormSession("add a caching layer")
    s.ask("read/write ratio?")
    s.answer("read-heavy")
    s.propose_approaches([
        Approach("cache-aside", "read-through cache", pros=["simple"], cons=["stale"]),
        Approach("write-through", "writes via cache", pros=["fresh"], cons=["slow writes"]),
    ])
    s.approve("jas", "cache-aside")
    return s


def silent(_):
    pass


class TestPersistence(unittest.TestCase):
    def test_cannot_persist_before_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            s = BrainstormSession("topic")
            s.propose_approaches([
                Approach("a", "x", pros=["p"], cons=["c"]),
                Approach("b", "y", pros=["p"], cons=["c"]),
            ])
            with self.assertRaises(BrainstormGateError):
                persist_approach(s, store)
            self.assertIsNone(load_approved_approach(store))

    def test_persist_then_retrieve(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(os.path.join(d, "state"))
            persist_approach(approved_session(), store)

            loaded = load_approved_approach(store)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.approach.name, "cache-aside")
            self.assertEqual(loaded.topic, "add a caching layer")
            self.assertEqual(loaded.approver, "jas")
            self.assertIn(
                ("read/write ratio?", "read-heavy"),
                [(q.text, q.answer) for q in loaded.answered_questions],
            )

    def test_round_trips_through_the_config_surface(self):
        # Downstream (e.g. the completeness gate) retrieves via Surface.state.
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            surface = Surface.load(d)
            persist_approach(approved_session(), surface.state)

            reloaded = Surface.load(d)
            loaded = load_approved_approach(reloaded.state)
            self.assertEqual(loaded.approach.name, "cache-aside")
            # the persisted artifact is transient runtime state under temp_local/ (24D)
            self.assertTrue(
                os.path.exists(os.path.join(reloaded.mokata_dir, "temp_local", "state",
                                            "approved_approach.json"))
            )


if __name__ == "__main__":
    unittest.main()
