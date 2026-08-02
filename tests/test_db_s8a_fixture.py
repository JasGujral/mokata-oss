"""DB.S8a — the at-scale fixture's OWN contracts.

Every later DB.S8 leg reports a number derived from `_scale_fixture`. If the fixture is wrong,
those numbers are wrong in a way that reads as GREEN — so the fixture is pinned before anything is
measured with it.

  F-1  the corpus is a pure function of the seed (byte-identical docs across two generations)
  F-2  ... and the seed is LOAD-BEARING (a different seed is a different corpus)
  F-3  N is DECLARED and TRUE — `declared_n` asserts rather than trusts
  F-4  a spec too small for its probes is REFUSED, never quietly given fewer probes
  F-5  BULK == PUT. The bulk loader's rows are byte-identical to the same items through `put`
  F-6  the fixture carries NO INSERT of its own (the structural half of F-5)
  F-7  ground truth is a FACT: the hop item shares no token with its query
  F-8  ... and the probe token occurs in exactly ONE item
  F-9  personal items carry a non-empty scope_id (else there are no seats to leak between)
  F-10 the typed edges land — one `depends_on` per probe, plus lineage and code anchors

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401

import _scale_fixture as F

from mokata.memory import edges as E
from mokata.memory import scope as S
from mokata.memory.backends import SQLiteBackend
from mokata.memory.episodic import _tokens


def _db(tmp, name="m.db"):
    return SQLiteBackend(os.path.join(tmp, name))


def _rows(path, table, columns):
    con = sqlite3.connect(path)
    try:
        cols = ", ".join(columns)
        return con.execute(f"SELECT {cols} FROM {table} ORDER BY {columns[0]}").fetchall()  # nosec B608
    finally:
        con.close()


class DeterminismTest(unittest.TestCase):
    def test_f1_the_same_seed_yields_a_byte_identical_corpus(self):
        """F-1 — the docs, not merely the ids. A generator that varied a timestamp or a dict
        ordering would still produce matching ids while producing a different store, and a quality
        number compared against a stored baseline would drift for no reason anyone could name."""
        a = F.generate(F.ScaleSpec(n_items=400, probes=10))
        b = F.generate(F.ScaleSpec(n_items=400, probes=10))
        self.assertEqual([json.dumps(i.to_doc(), sort_keys=True) for i in a.items],
                         [json.dumps(i.to_doc(), sort_keys=True) for i in b.items])
        self.assertEqual(a.probes, b.probes)

    def test_f2_a_different_seed_is_a_different_corpus(self):
        """F-2 — the other half of F-1, and it is not pedantry: a generator that ignored its seed
        would pass F-1 perfectly while making the seed a decoration."""
        a = F.generate(F.ScaleSpec(n_items=400, probes=10, seed=1))
        b = F.generate(F.ScaleSpec(n_items=400, probes=10, seed=2))
        self.assertNotEqual([i.value for i in a.items], [i.value for i in b.items])

    def test_f3_declared_n_is_the_n_the_corpus_actually_holds(self):
        c = F.generate(F.ScaleSpec(n_items=400, probes=10))
        self.assertEqual(c.declared_n, 400)
        self.assertEqual(len(c.items), 400)
        self.assertIn("N=400", c.describe())

    def test_f3b_a_short_corpus_refuses_to_report_its_declared_size(self):
        """F-3 — the assertion has to FIRE, or it is decoration. A leg whose corpus lost rows must
        not be able to report the size it asked for: that is the silent-cap failure exactly."""
        c = F.generate(F.ScaleSpec(n_items=400, probes=10))
        c.items.pop()
        with self.assertRaises(AssertionError):
            _ = c.declared_n

    def test_f4_a_spec_too_small_for_its_probes_is_refused(self):
        """F-4 — 10 probes plant 20 items. A corpus of 15 cannot hold them, and the failure mode
        worth preventing is the one where it silently plants 7 probes and every downstream recall
        number is computed over a ground truth nobody declared."""
        with self.assertRaises(ValueError):
            F.ScaleSpec(n_items=15, probes=10)


class BulkEqualsPutTest(unittest.TestCase):
    """F-5/F-6 — THE anti-SHIM-FALSE-GREEN pin.

    Doc 84 carries SHIM-FALSE-GREEN as a 🔴 harness row: a test double that hand-mirrors production
    proves the double. A bulk loader with its own INSERT is that shape at its most dangerous,
    because every DB.S8 contract is then asserted against rows the production writer never wrote —
    a missing `scope_level` projection would make the scope-isolation contracts pass trivially.
    """

    def test_f5_bulk_loaded_rows_are_byte_identical_to_per_item_put(self):
        corpus = F.generate(F.ScaleSpec(n_items=300, probes=8))
        cols = ("id", "mtype", "subject", "status", "doc", "scope_level", "scope_id",
                "pin", "priority", "valid_from", "valid_to")
        with tempfile.TemporaryDirectory() as tmp:
            slow, fast = _db(tmp, "slow.db"), _db(tmp, "fast.db")
            for item in corpus.items:
                slow.put(item)                      # the production path, item by item
            F.load_sqlite(fast, corpus)             # the fixture's bulk path
            self.assertEqual(_rows(slow.path, "memory", cols),
                             _rows(fast.path, "memory", cols))
            # The DB.S7a edge projection too — `created_at` excluded because it is a wall-clock
            # stamp taken per write, so the two loads legitimately differ there and nowhere else.
            edge_cols = (E.SRC_COLUMN, E.DST_COLUMN, E.KIND_COLUMN,
                         E.VALID_FROM_COLUMN, E.VALID_TO_COLUMN)
            self.assertEqual(_rows(slow.path, E.LOCAL_EDGES_TABLE, edge_cols),
                             _rows(fast.path, E.LOCAL_EDGES_TABLE, edge_cols))

    def test_f6_the_fixture_carries_no_insert_of_its_own(self):
        """F-6 — the structural half. F-5 compares today's two paths; this one keeps them one path
        tomorrow. The moment someone answers a schema change by teaching the loader its own
        statement, F-5 can be made to pass again by teaching it the change twice."""
        with open(F.__file__, encoding="utf-8") as fh:
            src = fh.read().upper()
        for forbidden in ("INSERT INTO", "CREATE TABLE", "UPDATE MEMORY"):
            self.assertNotIn(forbidden, src,
                             f"the fixture must not carry its own {forbidden} — it calls "
                             "`backend._put_on`, which IS `put`'s body")


class GroundTruthTest(unittest.TestCase):
    def test_f7_the_hop_item_shares_no_token_with_its_query(self):
        """F-7 — the property the whole A→D threshold rests on. If a hop item shared even one query
        token, the direct arms would score it and the expansion arm's measured gain would be
        vocabulary overlap wearing a traversal's name."""
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20))
        by_id = corpus.by_id()
        for probe in corpus.probes:
            hop = by_id[probe.hop_id]
            shared = _tokens(probe.query) & _tokens(f"{hop.subject} {hop.value}")
            self.assertEqual(shared, set(),
                             f"probe {probe.index}: hop item shares {shared} with its query")

    def test_f8_a_probe_token_occurs_in_exactly_one_item(self):
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20))
        for probe in corpus.probes:
            token = F.probe_token(probe.index)
            carriers = [it.id for it in corpus.items
                        if token in _tokens(f"{it.subject} {it.value}")]
            self.assertEqual(carriers, [probe.direct_id])

    def test_f8b_the_direct_item_reaches_the_hop_item_by_one_typed_edge(self):
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20))
        by_id = corpus.by_id()
        for probe in corpus.probes:
            self.assertEqual(by_id[probe.direct_id].depends_on, [probe.hop_id])


class ScopePopulationTest(unittest.TestCase):
    def test_f9_personal_items_carry_a_non_empty_scope_id(self):
        """F-9 — `scope.on_path` matches an EMPTY personal id against ANY personal reader (the
        legacy/local-default carve-out). A fixture that left it blank would give every seat's
        private items to every other seat, and S-2's no-cross-seat-leak contract would then be
        asserting a property of a corpus with no seats to leak between."""
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20, seats=4))
        personal = [i for i in corpus.items if i.scope_level == S.PERSONAL]
        self.assertTrue(personal)
        self.assertTrue(all(i.scope_id for i in personal))
        self.assertEqual({i.scope_id for i in personal},
                         {corpus.spec.seat_user(k) for k in range(4)})

    def test_f9b_every_scope_level_is_populated(self):
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20))
        levels = {i.scope_level for i in corpus.items}
        self.assertEqual(levels, set(S.SCOPE_LEVELS))


class EdgePopulationTest(unittest.TestCase):
    def test_f10_the_typed_edges_land_in_the_table(self):
        corpus = F.generate(F.ScaleSpec(n_items=600, probes=20))
        with tempfile.TemporaryDirectory() as tmp:
            backend = _db(tmp)
            F.load_sqlite(backend, corpus)
            con = sqlite3.connect(backend.path)
            try:
                by_kind = dict(con.execute(
                    f"SELECT {E.KIND_COLUMN}, COUNT(*) FROM {E.LOCAL_EDGES_TABLE} "  # nosec B608
                    f"WHERE {E.VALID_TO_COLUMN} IS NULL GROUP BY {E.KIND_COLUMN}").fetchall())
            finally:
                con.close()
        # One `depends_on` per probe — the edge the expansion arm rides — plus lineage and code
        # anchors, so the walk is never proven on a single kind.
        self.assertEqual(by_kind.get(E.DEPENDS_ON), 20)
        self.assertGreater(by_kind.get(E.SUPERSEDES, 0), 0)
        self.assertGreater(by_kind.get(E.ABOUT_CODE, 0), 0)


if __name__ == "__main__":
    unittest.main()
