"""DERIVES-FROM PRODUCER — an approved SUMMARIZE records what it was distilled out of.

`derives_from` was declared in the closed typed-edge set at DB.S7a and left INERT, alongside four
others. It is the one that could be wired now, and the reason is specific rather than a matter of
priority: **its producer already existed and it carried no open design question.** An approved
SUMMARIZE consolidation lands a NEW summary item and is the only place in the codebase that knows
which items it summarized (`ConsolidationProposal.olds`). Everything it lacked was somewhere to
persist that. The other four cannot be wired the same way and each for its own reason —
`contradicts` is detected at READ time and never persisted (wiring it would mean INVENTING edges),
`used_by` needs K5 prompt linkage that is not built, and `decided_in`/`promoted_from` have no
producer at all.

THE WIRING RULE, which is what actually made this cheap: `edges._ITEM_FIELD` wires an edge kind by
naming a persisted doc-JSON field on `MemoryItem`. A kind with no field cannot be anything but
inert, and a kind with one is migratable rather than inventable. So `derives_from` gains a fourth
inline list on exactly the terms the other three carry — the LIST is authoritative, the edge row is
a DERIVED PROJECTION rebuilt from it by the same gated write that persists it.

WHAT IS DELIBERATELY NOT DONE: the weight is not retuned. `KIND_WEIGHT[DERIVES_FROM]` is listed
explicitly now that it has a producer and is set to exactly `UNWIRED_DEFAULT_WEIGHT`'s value, so
wiring the kind is byte-identical in RANKING. Moving it would be a ranking change made on
intuition, and `expansion.py`'s own header says every weight is tuned against the at-scale fixture.
Pinned in `test_db_s7b_bounded_expansion` so a future retune has to be deliberate.
"""
import os
import tempfile
import unittest

import _support  # noqa: F401

from mokata.memory import edges as E
from mokata.memory.backends import SQLiteBackend
from mokata.memory.consolidation import SUMMARIZE
from mokata.memory.item import EPISODIC, MemoryItem
from mokata.memory.store import MemoryStore


def _turns(session="sess-1", n=3):
    return [MemoryItem.create(subject=session, value=f"turn {i} about the retry budget",
                           mtype=EPISODIC, source="test", author="tester",
                           id=f"turn-{i}") for i in range(n)]


def _store(d):
    store = MemoryStore(SQLiteBackend(os.path.join(d, "m.db")))
    for it in _turns():
        store.backend.put(it)
    return store


def _summary_proposal(store):
    props = [p for p in store.propose_consolidations() if p.kind == SUMMARIZE]
    assert len(props) == 1, f"expected 1 SUMMARIZE proposal, got {len(props)}"
    return props[0]


# ======================================================================== THE FIELD
class TheFieldRoundTrips(unittest.TestCase):
    def test_it_is_empty_by_default_so_legacy_items_are_unchanged(self):
        self.assertEqual([], MemoryItem("s", "v").derives_from)

    def test_it_survives_a_doc_round_trip(self):
        item = MemoryItem.create(subject="s", value="v", derives_from=["a", "b"])
        self.assertEqual(["a", "b"], MemoryItem.from_dict(item.to_dict()).derives_from)

    def test_a_pre_existing_doc_without_the_key_reads_as_empty(self):
        """Additive: a doc written before this field existed must not become 'unknown' (D6) nor
        raise. `[]` is 'was distilled out of nothing', which is true of every legacy item."""
        doc = MemoryItem.create(subject="s", value="v").to_dict()
        doc.pop("derives_from")
        self.assertEqual([], MemoryItem.from_dict(doc).derives_from)

    def test_it_is_a_MODELLED_key_not_an_unknown_one(self):
        """`DOC_KEYS` is what stops a modelled field being silently preserved as opaque `extra`.
        Adding the field without adding it here is exactly the drift that pin exists to catch."""
        from mokata.memory.item import DOC_KEYS
        self.assertIn("derives_from", DOC_KEYS)
        self.assertNotIn("derives_from", MemoryItem.create(subject="s", value="v").extra)


# ======================================================================== THE WIRING
class TheKindIsWired(unittest.TestCase):
    def test_derives_from_is_now_a_wired_kind(self):
        self.assertIn(E.DERIVES_FROM, E.WIRED_KINDS)

    def test_it_is_wired_by_naming_a_REAL_field(self):
        """The wiring rule, not the wiring itself: a kind is wired iff it names a persisted field
        the item model actually has. A map entry pointing at a field that does not exist would
        project nothing, silently."""
        self.assertTrue(hasattr(MemoryItem("s", "v"), E._ITEM_FIELD[E.DERIVES_FROM]))

    def test_the_four_without_a_producer_stay_unwired(self):
        """Wiring one kind must not become licence to wire the rest. Each of these is inert for a
        reason that has NOT changed."""
        for kind in (E.CONTRADICTS, E.USED_BY, E.DECIDED_IN, E.PROMOTED_FROM):
            self.assertNotIn(kind, E.WIRED_KINDS,
                             f"'{kind}' was wired without a producer — its edges would be invented")


# ======================================================================== THE PRODUCER
class AnApprovedSummarizeRecordsItsLineage(unittest.TestCase):
    """REACHABILITY (doc 84 REACHABILITY-PINS-MISSING): every test here drives the REAL gated
    `apply_consolidation` path and never sets `derives_from` itself. A pin that constructed the
    lineage would prove the FIELD works and say nothing about whether anything produces it."""

    def test_the_summary_records_every_item_it_was_distilled_out_of(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            expected = sorted(o.id for o in p.olds)
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            landed = store.backend.get(p.new.id)
        self.assertTrue(expected, "the proposal summarized nothing — this pin proved nothing")
        self.assertEqual(expected, sorted(landed.derives_from),
                         "an approved SUMMARIZE landed a summary with NO lineage — the only place "
                         "that knows what it was distilled out of dropped the information")

    def test_a_REJECTED_summarize_records_nothing(self):
        """The producer rides the approval, not the proposal. A rejected consolidation writes
        nothing at all, lineage included."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            store.apply_consolidation(p, "reject")
            self.assertIsNone(store.backend.get(p.new.id))

    def test_a_HUMAN_EDITED_summary_still_carries_the_lineage(self):
        """`keep` is `edited or p.new`. A human who rewrote the prose still summarized the same
        turns — losing the lineage because the wording changed would be the wrong half to drop."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            expected = sorted(o.id for o in p.olds)
            edited = MemoryItem.create(subject=p.new.subject, value="my own words", mtype=p.new.mtype,
                                    id=p.new.id)
            store.apply_consolidation(p, "edit", edited=edited, assume_yes=True)
            landed = store.backend.get(p.new.id)
        self.assertEqual(expected, sorted(landed.derives_from))

    def test_re_applying_the_same_proposal_does_not_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            store.apply_consolidation(p, "approve", assume_yes=True)
            store.apply_consolidation(p, "approve", assume_yes=True)
            landed = store.backend.get(p.new.id)
        self.assertEqual(sorted(set(landed.derives_from)), sorted(landed.derives_from),
                         "re-applying the same consolidation duplicated the lineage ids")

    def test_the_lineage_never_points_at_the_summary_itself(self):
        """A self-edge is a cycle the traversal would have to prune and a relation that means
        nothing. Cheap to prevent at the producer; awkward to reason about anywhere downstream.

        DRIVEN WITH A HAND-BUILT PROPOSAL, and the reason is recorded rather than hidden: mutation
        showed the `o.id != keep.id` guard is UNREACHABLE through `propose_consolidations`, because
        that path always mints a fresh summary item whose id cannot be among the turns it
        summarizes. Deleting the guard therefore killed no test.

        It is kept and pinned rather than deleted because `apply_consolidation` is public and takes
        any `ConsolidationProposal` — the CLI and the MCP write tool both hand it one — so the
        input that trips it is constructible by a caller even though the built-in producer never
        constructs it. This is the DB.S7b cycle-guard lesson applied: a guard with no observable
        effect on the shipped path is either dead code or an unpinned contract, and the way to tell
        is to feed it the input it exists for."""
        from mokata.memory.consolidation import ConsolidationProposal
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            # A proposal that lists the summary among its OWN sources.
            hostile = ConsolidationProposal(kind=SUMMARIZE, mtype=p.new.mtype,
                                            subject=p.subject, olds=[p.new] + list(p.olds),
                                            new=p.new, rationale=p.rationale)
            store.apply_consolidation(hostile, "approve", assume_yes=True)
            landed = store.backend.get(p.new.id)
        self.assertNotIn(landed.id, landed.derives_from,
                         "the summary claims to derive from ITSELF — a self-edge the traversal "
                         "then has to prune, produced by the writer rather than prevented at it")
        self.assertEqual(sorted(o.id for o in p.olds), sorted(landed.derives_from),
                         "the self-reference was dropped but so was something else")

    def test_the_lineage_reaches_the_EDGE_TABLE_not_just_the_doc(self):
        """The point of a producer. The list is authoritative and the edge row is its projection —
        so an approved SUMMARIZE must leave real `derives_from` rows the traversal can walk, not
        merely a field nothing reads."""
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            p = _summary_proposal(store)
            expected = sorted(o.id for o in p.olds)
            store.apply_consolidation(p, "approve", assume_yes=True)
            rows = store.backend.expand_from([p.new.id], 1)
        walked = sorted(str(r[2]) for r in rows if str(r[3]) == E.DERIVES_FROM)
        self.assertEqual(expected, walked,
                         "the doc carries the lineage but the EDGE TABLE does not — the projection "
                         "did not run, so the wired kind is still inert in the traversal")


if __name__ == "__main__":
    unittest.main()
