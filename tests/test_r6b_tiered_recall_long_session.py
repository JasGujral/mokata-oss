"""0.0.27 R6 follow-up — TIERED-RECALL-SILENT-OFF: a long session keeps its ranking, and says so
when it cannot.

Business rules — what must be true for the user:
  * A long session keeps the FULL ranking. The per-turn pack excludes what this session was
    already handed (S4). Before this fix the exclusion was paid for by over-fetching `k + |seen|`
    candidates. Past ~33 handed items that blew the declared 150-candidate bound, the bound's
    assert fired on EVERY turn, and the hook silently fell back to the keyword floor. So from
    roughly turn 7 onwards the graph, meaning and expansion tiers stopped ranking the user's
    memory, and nothing said so. Now a session that has been handed 200 items still gets the
    tiered stack, and it still gets UNSEEN items.
  * The size contract holds by construction. No (top_k, |seen|) pair ever asks the lexical tier
    for more than `CANDIDATE_UNION_CAP` nominees. The bound was not raised to hide the bug: the
    exclusion now happens INSIDE the ranked query once over-fetching would cross it.
  * A turn whose ranking could not use the tiered stack SAYS so in the injected context (the O12
    rule: never a silent cut). It is not just quietly ranked by keywords.
  * A short session is byte-identical to v0.0.26 (`test_r6_prompt_hook_import_graph.GOLDEN`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok

from mokata import bootstrap as B
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryStore
from mokata.memory import tiered as T
from mokata.memory.item import MemoryItem
from mokata.quiet_spawn import hidden

QUERY = "fix the parser bug"
N_ITEMS = 260
N_SEEN = 200


def _silent(*_a, **_k):
    pass


def _repo(d: str) -> None:
    subprocess.run(["git", "init", "-q", d], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, **hidden())
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    store = MemoryStore.from_surface(Surface.load(d), knowledge=False)
    for i in range(N_ITEMS):
        store.backend.put(MemoryItem.create(
            f"parser note {i:03d}", f"the parser bug, case {i:03d}", kind="context",
            id=f"p{i:03d}", created_at="2026-01-01T00:%02d:%02dZ" % (i // 60, i % 60)))


class _Graph:
    """A graph tier that confirms ONE unseen item. If the tier ranks, that item leads the pack."""

    def __init__(self, favourite: str):
        self.favourite, self.calls = favourite, 0

    def __call__(self, _query, item):
        self.calls += 1
        return 1.0 if item.id == self.favourite else 0.0


@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TheR6b_LongSessionKeepsTheTieredRanking(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        _repo(cls.d)
        cls.seen = {f"p{i:03d}" for i in range(N_SEEN)}       # already handed to this session

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def _pack(self, graph, **patches):
        # `bootstrap` reads the floor as `mokata.memory.jit_recall` (the lazy package attribute).
        floor = (contextlib.nullcontext() if patches.get("allow_floor") else
                 mock.patch("mokata.memory.jit_recall",
                            side_effect=AssertionError("the keyword floor ranked this turn")))
        expansion = mock.patch.object(T, "_expansion_tier", wraps=T._expansion_tier)
        with floor, expansion as ex:
            res = B.build_injection(Surface.load(self.d), QUERY, budget=4000,
                                    exclude_ids=set(self.seen), graph_scorer=graph)
        return res, ex

    def test_R6b_200_handed_items_still_get_graph_and_expansion_ranking(self):
        # every item matches equally, so only the graph tier can put p230 first. p230 is not the
        # first unseen row, so it cannot lead by insertion order or by luck
        graph = _Graph(favourite="p230")
        res, expansion = self._pack(graph)
        lines = [ln for ln in res.text.splitlines() if ln.startswith("- ")]
        self.assertTrue(lines, res.text)
        self.assertEqual("p230", res.item_ids[0], "the graph-confirmed item must lead")
        self.assertGreater(graph.calls, 0)
        self.assertTrue(expansion.called, "the expansion pass must run on a long session")
        self.assertNotIn("keyword floor", res.text)

    def test_R6b_the_pack_promotes_only_what_the_session_has_not_seen(self):
        res, _ = self._pack(_Graph(favourite="p230"))
        self.assertTrue(res.item_ids)
        self.assertEqual([], [i for i in res.item_ids if i in self.seen])

    def test_R6b_a_tier_failure_is_said_in_the_pack(self):
        with mock.patch.object(MemoryStore, "recall_relevant",
                               side_effect=RuntimeError("tier down")):
            res, _ = self._pack(None, allow_floor=True)
        self.assertIn(B.TIERED_RECALL_FALLBACK_NOTE.format(exc="RuntimeError"), res.text)
        self.assertTrue([ln for ln in res.text.splitlines() if ln.startswith("- ")],
                        "the keyword floor still delivers the pack")


class TheR6b_SizeContractHoldsByConstruction(unittest.TestCase):

    def test_R6b_no_request_asks_the_lexical_tier_past_the_declared_bound(self):
        for top_k in (1, 5, 37, 50):
            for seen in (0, 7, 33, 200, 5000):
                window, push = T.nomination_window(top_k, seen)
                self.assertLessEqual(window, T.CANDIDATE_UNION_CAP, (top_k, seen))
                self.assertEqual(T.LEXICAL_NOMINATION_MAX, T.CANDIDATE_UNION_CAP)
                self.assertGreaterEqual(window, min(top_k, T.CANDIDATE_UNION_CAP), (top_k, seen))
                # short sessions keep v0.0.26's over-fetch (byte-identical); long ones push it in
                self.assertEqual(push, (top_k + seen) * 4 > T.CANDIDATE_UNION_CAP
                                 and seen > 0, (top_k, seen))

    def test_the_cap_assert_was_not_raised(self):
        self.assertEqual(150, T.CANDIDATE_UNION_CAP)


if __name__ == "__main__":
    unittest.main()
