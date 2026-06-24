"""C3 — episodic conversation memory: a searchable local store of past turns, a third
memory type with its own toggle, embeddings optional with a lexical fallback."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.memory import (
    EPISODIC,
    PERSISTENT,
    DECISION,
    EpisodicMemory,
    MemoryDisabledError,
    MemoryStore,
    SQLiteBackend,
)

ALL = (PERSISTENT, DECISION, EPISODIC)


def epi_store(d, types=ALL):
    return EpisodicMemory(
        MemoryStore(SQLiteBackend(os.path.join(d, "m.db")), enabled_types=types))


class TestEpisodicRecall(unittest.TestCase):
    def test_recalls_a_prior_turn_via_lexical_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            epi = epi_store(d)
            epi.record("s1", "we chose postgres as the database engine", assume_yes=True)
            epi.record("s1", "the standup is on tuesday morning", assume_yes=True)
            # no embedder supplied -> lexical keyword search
            hits = epi.search("which database did we choose", top_k=1)
            self.assertTrue(hits)
            self.assertIn("postgres", hits[0][0].value)

    def test_search_returns_ranked_scores(self):
        with tempfile.TemporaryDirectory() as d:
            epi = epi_store(d)
            epi.record("s1", "deploy uses docker compose", assume_yes=True)
            epi.record("s1", "lunch was good", assume_yes=True)
            hits = epi.search("how do we deploy with docker", top_k=2)
            self.assertEqual(len(hits), 2)
            self.assertGreaterEqual(hits[0][1], hits[1][1])   # sorted by score desc
            self.assertIn("docker", hits[0][0].value)

    def test_optional_embedder_is_used_when_supplied(self):
        with tempfile.TemporaryDirectory() as d:
            epi = epi_store(d)
            epi.record("s1", "alpha topic", assume_yes=True)
            epi.record("s1", "beta topic", assume_yes=True)

            # an embedder that scores by presence of the letter 'b'
            def embed(text):
                return [float(text.count("b"))]
            hits = epi.search("b", top_k=1, embedder=embed)
            self.assertIn("beta", hits[0][0].value)


class TestEpisodicToggle(unittest.TestCase):
    def test_disabling_episodic_removes_it_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            # episodic OFF (only persistent + decision enabled)
            epi = epi_store(d, types=(PERSISTENT, DECISION))
            with self.assertRaises(MemoryDisabledError):
                epi.record("s1", "anything", assume_yes=True)
            self.assertEqual(epi.search("anything"), [])

    def test_episodic_is_a_distinct_type(self):
        with tempfile.TemporaryDirectory() as d:
            epi = epi_store(d)
            epi.record("s1", "a turn", assume_yes=True)
            # episodic turns don't leak into persistent/decision reads
            self.assertEqual(epi.store.all_active(mtype=PERSISTENT), [])
            self.assertEqual(len(epi.store.all_active(mtype=EPISODIC)), 1)


if __name__ == "__main__":
    unittest.main()
