"""R-13F · D5-rider(3) — the tiered-recall `semantic_search` branch is MARKED and exercised.

The `hasattr(backend, "semantic_search")` branch in `memory/tiered.py` is reachable only through a
backend exposing `semantic_search` — today only `PgVectorBackend`, which no shipped store config
selects (it is export-only until DB.S4 wires pgvector in 0.0.15). The rider KEEPS the branch (it is
the exact shape DB.S4 will consume) behind an explicit not-yet-reachable marker, and pins it with
this named test so the branch is no longer unreachable-AND-unmarked. Both sub-paths are covered:
the index-backed ranking, and the honest degrade-with-notice when the index call fails.
"""

import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory.item import MemoryItem
from mokata.memory.tiered import tiered_recall


class _Store:
    def __init__(self, items, backend):
        self._items = items
        self.backend = backend

    def all_active(self):
        return list(self._items)


class _IndexBackend:
    """Stands in for the DB.S4 pgvector backend: answers a top-k `semantic_search`."""

    def __init__(self, ranked):
        self._ranked = ranked

    def semantic_search(self, query, top_k=10):
        return self._ranked


class _RaisingBackend:
    def semantic_search(self, query, top_k=10):
        raise RuntimeError("vector index unreachable")


class TestSemanticBranchReachable(unittest.TestCase):
    def _items(self):
        return [MemoryItem.create("alpha subject", "alpha value"),
                MemoryItem.create("beta subject", "beta value")]

    def test_d5_rider_3_semantic_search_branch_ranks_via_injected_index(self):
        items = self._items()
        # The index ranks BETA highest even though the query overlaps ALPHA lexically — so a semantic
        # win over lexical proves the injected `semantic_search` branch actually drove the score.
        backend = _IndexBackend([(items[1], 0.99), (items[0], 0.01)])
        hits = tiered_recall(_Store(items, backend), "alpha", embedder=lambda _t: [0.0], top_k=2)
        by_id = {h.item.id: h for h in hits}
        self.assertGreater(by_id[items[1].id].semantic, by_id[items[0].id].semantic,
                           "the injected semantic_search scores drove ranking")
        self.assertEqual(hits[0].item.id, items[1].id, "beta wins on the semantic tier")

    def test_d5_rider_3_semantic_search_failure_degrades_to_lexical_with_notice(self):
        items = self._items()
        with mock.patch("mokata.memory.tiered.note_degraded") as noted:
            hits = tiered_recall(_Store(items, _RaisingBackend()), "alpha",
                                 embedder=lambda _t: [0.0], top_k=2)
        self.assertTrue(hits, "recall degrades to the lexical floor, never raises")
        self.assertTrue(noted.called, "the vanished semantic tier says so — the silence was the bug")
        self.assertTrue(all(h.semantic == 0.0 for h in hits), "semantic contributed nothing")


if __name__ == "__main__":
    unittest.main()
