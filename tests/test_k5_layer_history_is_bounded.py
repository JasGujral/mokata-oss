"""0.0.25 K5 — a long-lived knowledge layer does not grow without bound.

The IDE keeps ONE `KnowledgeLayer` per repo for its whole lifetime, and `history` kept every full
answer it ever gave — references and all: +11 MB per hub query, 1.1 GB observed on the scale repo.
Its only reader (`build_story_analysis`) uses kind / target / count / backend. So:
  * history keeps the newest `HISTORY_MAX` entries, and an entry is a SUMMARY — never the
    references (a hub answer costs the same to remember as a leaf answer);
  * what fell off the front is COUNTED, and the story analysis says how many queries it cannot
    list instead of reading as complete.

Mutations this module was checked against (each went RED, then was restored):
  M1 `deque(maxlen=None)`                         -> test_history_keeps_only_the_newest
  M2 history stores the full QueryResult again     -> test_an_entry_does_not_hold_the_references
  M3 `history_dropped` never incremented           -> test_the_story_analysis_says_what_it_lost
"""

import _support  # noqa: F401

import unittest

from mokata.knowledge import layer as layer_mod
from mokata.knowledge.layer import KnowledgeLayer, build_story_analysis
from mokata.knowledge.query import GraphBackend, QueryResult, Reference


class _Big(GraphBackend):
    name = "fake"

    def query(self, kind, target, depth=1):
        return QueryResult(kind=kind, target=target, backend=self.name, basis="structural",
                           references=[Reference(f"f{i}.py", i, "", "x") for i in range(500)])


class TestHistoryIsBounded(unittest.TestCase):
    def setUp(self):
        self.layer = KnowledgeLayer(_Big())

    def test_history_keeps_only_the_newest(self):
        n = layer_mod.HISTORY_MAX + 7
        for i in range(n):
            self.layer.callers(f"s{i}")
        self.assertEqual(len(self.layer.history), layer_mod.HISTORY_MAX)
        self.assertEqual(self.layer.history[-1].target, f"s{n - 1}")
        self.assertEqual(self.layer.history[0].target, "s7")

    def test_an_entry_does_not_hold_the_references(self):
        self.layer.callers("hub")
        rec = self.layer.history[-1]
        self.assertFalse(hasattr(rec, "references"), "history must not pin a whole answer")
        self.assertEqual((rec.kind, rec.target, rec.count, rec.backend),
                         ("callers", "hub", 500, "fake"))

    def test_the_story_analysis_says_what_it_lost(self):
        for i in range(layer_mod.HISTORY_MAX + 3):
            self.layer.callers(f"s{i}")
        a = build_story_analysis("story-1", "summary", self.layer)
        self.assertEqual(a.queries_dropped, 3)
        self.assertEqual(len(a.queries), layer_mod.HISTORY_MAX)
        self.assertEqual(a.to_dict()["queries_dropped"], 3)
        self.assertEqual(type(a).from_dict(a.to_dict()).queries_dropped, 3)

    def test_a_short_story_is_complete(self):
        self.layer.callers("a")
        self.layer.blast_radius("a")
        a = build_story_analysis("story-2", "s", self.layer)
        self.assertEqual(a.queries_dropped, 0)
        self.assertEqual([q["kind"] for q in a.queries], ["callers", "blast_radius"])
        self.assertEqual(a.queries[0]["count"], 500)


if __name__ == "__main__":
    unittest.main()
