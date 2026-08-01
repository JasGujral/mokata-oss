"""DB.S5 — memory decay & lifecycle: usage signals, fusion terms, bi-temporal validity, budgets.

What these pin, in the order the deliverables were built:

  * **SCHEMA v4** — the four lifecycle columns land on a fresh store AND migrate onto a v3 one
    through the DB.S2b seams (PRAGMA table_info → ALTER what's missing; `PRAGMA user_version` as
    the generation stamp). The generations are cumulative: writing the v4 stamp must NOT make a
    store look un-backfilled for scope.
  * **USAGE RECORDING** — a recall bumps `hit_count`/`last_recalled_at`, and a telemetry failure
    is SWALLOWED. That last one is the important test in the file: a store whose telemetry write
    raises must still return its recall.
  * **THE FUSION** — the back-compat bar. With the new signals absent or zero, the fused score is
    arithmetically the pre-DB.S5 three-term sum and the ranking is identical. Pinned against the
    weights AND against a real store, both directions.
  * **BUDGETS + ARCHIVAL** — a sweep PROPOSES and never evicts; applying CLOSES a validity window
    and never deletes a row; pins are counted but never archived.
  * **THE TYPED FORMULA ENVELOPE** — the locked doc-62 decision, including the character-iteration
    bug it closes.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import _support  # noqa: F401 - puts src/ on the path

from mokata.memory import lifecycle
from mokata.memory.backends import (
    _LIFECYCLE_BACKFILL_STAMP,
    _SCOPE_BACKFILL_STAMP,
    SQLiteBackend,
    validity_columns_from_doc,
)
from mokata.memory.consolidation import ARCHIVE, propose_archival
from mokata.memory.formula import applies_to, make_formula, normalize_applicability
from mokata.memory.item import ACTIVE, ARCHIVED, EPISODIC, PERSISTENT, MemoryItem
from mokata.memory.store import MemoryStore
from mokata.memory.tiered import (
    GRAPH_WEIGHT,
    LEXICAL_WEIGHT,
    RECENCY_WEIGHT,
    SEMANTIC_WEIGHT,
    USAGE_WEIGHT,
    tiered_recall,
)

NOW = "2026-07-29T12:00:00+00:00"


def _ago(days: float) -> str:
    return (datetime.fromisoformat(NOW) - timedelta(days=days)).isoformat()


def _store(tmp: str, name: str = "m.db") -> SQLiteBackend:
    return SQLiteBackend(os.path.join(tmp, name))


# ===================================================================== (1) SCHEMA v4
class TestSchemaV4(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def test_a_fresh_store_has_the_four_lifecycle_columns(self):
        backend = _store(self.tmp)
        backend.put(MemoryItem.create("s", "v"))
        with sqlite3.connect(backend.path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(memory)")}
        for col in (lifecycle.VALID_FROM_COLUMN, lifecycle.VALID_TO_COLUMN,
                    lifecycle.HIT_COUNT_COLUMN, lifecycle.LAST_RECALLED_AT_COLUMN):
            self.assertIn(col, cols, f"v4 column {col} missing from a fresh store")

    def _v3_store(self, name: str = "v3.db") -> tuple:
        """A hand-built store at the v3 generation: scope columns, NO lifecycle columns, stamped 1.
        This is what a real DB.S2b-era store on disk looks like, which is what v4 must migrate."""
        path = os.path.join(self.tmp, name)
        item = MemoryItem.create("legacy", "written before v4")
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE memory (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, mtype TEXT,
                   subject TEXT, status TEXT, doc TEXT,
                   scope_level TEXT NOT NULL DEFAULT 'personal', scope_id TEXT,
                   pin INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0)""")
        conn.execute(
            "INSERT INTO memory (id, mtype, subject, status, doc, scope_level)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (item.id, item.mtype, item.subject, item.status,
             json.dumps(item.to_dict()), "personal"))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        conn.close()
        return path, item

    def test_a_v3_store_migrates_forward_cleanly(self):
        path, item = self._v3_store()
        SQLiteBackend(path)                        # opening IS the migration
        with sqlite3.connect(path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(memory)")}
            row = conn.execute(
                f"SELECT {lifecycle.VALID_FROM_COLUMN}, {lifecycle.VALID_TO_COLUMN}, "
                f"{lifecycle.HIT_COUNT_COLUMN} FROM memory").fetchone()
        self.assertIn(lifecycle.VALID_FROM_COLUMN, cols)
        # the window OPENED at the item's creation, and is still OPEN — the never-delete invariant
        # applied to a migration: upgrading must not retire a single existing item.
        self.assertEqual(row[0], item.created_at)
        self.assertIsNone(row[1], "the migration must never CLOSE a window")
        self.assertEqual(row[2], 0, "usage must not be synthesised from creation dates")

    def test_the_migration_is_idempotent_and_stamped(self):
        """DB.S7a re-expressed the stamp assertion from `==` to `>=`, and the change is a
        CORRECTION rather than a loosening. The generations are CUMULATIVE on one `user_version`
        field (see `_LIFECYCLE_BACKFILL_STAMP`'s own comment, which said so before v5 existed): what
        this test means is "the v4 migration has provably run", and on a store that has ALSO had v5
        applied the field reads 3. `==` asserted the incidental fact that v4 was the LAST migration
        in existence — true when it was written, and false the moment a v5 landed. `>=` is what the
        cumulative scale actually promises, and it still goes RED if the v4 migration is skipped."""
        path, _item = self._v3_store()
        SQLiteBackend(path)
        with sqlite3.connect(path) as conn:
            self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                                    _LIFECYCLE_BACKFILL_STAMP)
        SQLiteBackend(path)                        # a second open must be a clean no-op
        with sqlite3.connect(path) as conn:
            self.assertEqual(len(conn.execute("SELECT id FROM memory").fetchall()), 1)

    def test_the_v4_stamp_does_not_undo_the_scope_backfill(self):
        """The cumulative-generation property. `_backfill_scope_columns` guards on `>= 1`; a v4
        store stamped 2 must still read as scope-backfilled, or DB.S2b's pushdown would silently
        switch itself off on every store this stage touches."""
        self.assertGreaterEqual(_LIFECYCLE_BACKFILL_STAMP, _SCOPE_BACKFILL_STAMP)
        backend = _store(self.tmp)
        self.assertTrue(backend.supports_scope_pushdown)

    def test_a_put_never_writes_the_usage_columns(self):
        """An upsert must not reset a live row's counter from a stale in-memory doc."""
        backend = _store(self.tmp)
        item = MemoryItem.create("s", "v")
        backend.put(item)
        backend.record_usage([item.id], NOW)
        item.value = "edited"
        backend.put(item)                          # re-put the SAME id
        self.assertEqual(backend.usage_stats([item.id])[item.id][0], 1,
                         "a re-put reset the usage counter")

    def test_validity_columns_come_from_the_doc(self):
        doc = MemoryItem.create("s", "v").to_dict()
        valid_from, valid_to = validity_columns_from_doc(doc)
        self.assertEqual(valid_from, doc["provenance"]["created_at"])
        self.assertIsNone(valid_to, "an open window is NULL, not ''")


# ===================================================================== (2) USAGE RECORDING
class _RaisingBackend(SQLiteBackend):
    """A store whose telemetry write always fails — a read-only file, a v3 team schema, a locked
    DB. The recall it rides must be completely unaffected."""

    def record_usage(self, item_ids, now):        # type: ignore[override]
        raise sqlite3.OperationalError("attempt to write a readonly database")


class TestUsageRecording(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def test_a_recall_bumps_hit_count_and_last_recalled_at(self):
        backend = _store(self.tmp)
        store = MemoryStore(backend)
        item = MemoryItem.create("deploy", "run the deploy script")
        backend.put(item)
        self.assertEqual(backend.usage_stats([item.id])[item.id], (0, None))
        hits = store.recall_relevant("deploy")
        self.assertTrue(hits)
        count, last = backend.usage_stats([item.id])[item.id]
        self.assertEqual(count, 1)
        self.assertIsNotNone(last)

    def test_only_the_returned_top_k_is_stamped(self):
        """`hit_count` means "was surfaced", not "was considered" — every active item is
        considered on every recall, so counting candidates would carry no information."""
        backend = _store(self.tmp)
        store = MemoryStore(backend)
        wanted = MemoryItem.create("deploy", "the deploy runbook")
        other = MemoryItem.create("unrelated", "nothing to do with it")
        backend.put(wanted)
        backend.put(other)
        store.recall_relevant("deploy", top_k=1)
        self.assertEqual(backend.usage_stats([wanted.id])[wanted.id][0], 1)
        self.assertEqual(backend.usage_stats([other.id])[other.id][0], 0)

    def test_a_failing_telemetry_write_never_fails_the_recall(self):
        """THE degrade-clean pin. The write raises; the recall still returns its answer."""
        backend = _RaisingBackend(os.path.join(self.tmp, "ro.db"))
        store = MemoryStore(backend)
        item = MemoryItem.create("deploy", "run the deploy script")
        backend.put(item)
        hits = store.recall_relevant("deploy")     # must not raise
        self.assertEqual([h.item.id for h in hits], [item.id])
        self.assertFalse(store.record_usage([item.id]), "a failed write must report False")

    def test_a_backend_with_no_usage_columns_degrades_silently(self):
        class NoUsage:
            backend = None

            def all_active(self, *a, **kw):
                return []

        store = MemoryStore(_store(self.tmp))
        store.backend = NoUsage()                  # no record_usage / usage_stats at all
        self.assertFalse(store.record_usage(["x"]))
        self.assertEqual(store.usage_signals(["x"]), {})

    def test_the_team_overlay_delegates_telemetry(self):
        """TEAM mode wraps the backend in a `JournalOverlay`. It delegates unknown attributes, so
        telemetry reaches the real store — but that is a property worth pinning rather than
        assuming: if the overlay ever grew an explicit allow-list, usage would silently switch
        itself off in team mode only, which is the hardest place to notice it."""
        from mokata.memory.overlay import JournalOverlay

        class _EmptyJournal:
            def pending(self):
                return []

        backend = _store(self.tmp)
        overlay = JournalOverlay(backend, _EmptyJournal())
        item = MemoryItem.create("s", "v")
        overlay.put(item)
        self.assertTrue(hasattr(overlay, "record_usage"))
        self.assertEqual(overlay.record_usage([item.id], NOW), 1)
        self.assertEqual(overlay.usage_stats([item.id])[item.id][0], 1)

    def test_usage_recording_writes_no_doc_content(self):
        """Telemetry is transient run-state: it must leave every approved field byte-identical."""
        backend = _store(self.tmp)
        item = MemoryItem.create("s", "an approved value")
        backend.put(item)
        with sqlite3.connect(backend.path) as conn:
            before = conn.execute("SELECT doc FROM memory WHERE id=?", (item.id,)).fetchone()[0]
        backend.record_usage([item.id], NOW)
        with sqlite3.connect(backend.path) as conn:
            after = conn.execute("SELECT doc FROM memory WHERE id=?", (item.id,)).fetchone()[0]
        self.assertEqual(before, after, "telemetry rewrote the approved doc")


# ===================================================================== (3) THE FUSION
class _FakeStore:
    """The minimum a `tiered_recall` needs — items plus an optional usage map."""

    def __init__(self, items, usage=None):
        self._items, self._usage = items, usage or {}
        self.backend = object()                    # no lexical_search / semantic_search

    def all_active(self, *a, **kw):
        return list(self._items)

    def usage_signals(self, ids):
        return {k: v for k, v in self._usage.items() if k in set(ids)}


class TestFusionBackCompat(unittest.TestCase):
    """THE BAR: with the new signals absent or zero, ranking is identical to pre-DB.S5."""

    def setUp(self) -> None:
        self.items = [MemoryItem.create(f"deploy {i}", f"the deploy runbook {i}",
                                        created_at=_ago(10 + i))
                      for i in range(4)]

    def _legacy_fused(self, hit) -> float:
        """The pre-DB.S5 three-term score, recomputed from the hit's own per-tier values."""
        return (SEMANTIC_WEIGHT * hit.semantic + GRAPH_WEIGHT * hit.graph
                + LEXICAL_WEIGHT * hit.lexical)

    def test_with_no_usage_signal_the_score_is_the_legacy_three_term_sum(self):
        hits = tiered_recall(_FakeStore(self.items), "deploy runbook", now=NOW)
        self.assertTrue(hits)
        for hit in hits:
            self.assertEqual(hit.recency, 0.0)
            self.assertEqual(hit.usage, 0.0)
            self.assertAlmostEqual(hit.score, self._legacy_fused(hit), places=12)

    def test_a_fresh_v4_row_ranks_exactly_as_a_pre_v4_row_did(self):
        """A migrated store whose rows exist but have never been recalled: hits=0 everywhere."""
        zero = {it.id: lifecycle.UsageSignal(hits=0, last_recalled_at=None) for it in self.items}
        without = tiered_recall(_FakeStore(self.items), "deploy runbook", now=NOW)
        with_zero = tiered_recall(_FakeStore(self.items, zero), "deploy runbook", now=NOW)
        self.assertEqual([h.item.id for h in without], [h.item.id for h in with_zero])
        for a, b in zip(without, with_zero):
            self.assertAlmostEqual(a.score, b.score, places=12)

    def test_the_new_terms_switch_on_only_once_an_item_has_hits(self):
        hot = self.items[-1]                       # the OLDEST-created, so it loses every tiebreak
        usage = {hot.id: lifecycle.UsageSignal(hits=20, last_recalled_at=_ago(0.5))}
        hits = tiered_recall(_FakeStore(self.items, usage), "deploy runbook", now=NOW)
        by_id = {h.item.id: h for h in hits}
        self.assertGreater(by_id[hot.id].usage, 0.0)
        self.assertGreater(by_id[hot.id].recency, 0.0)
        for other in self.items[:-1]:
            self.assertEqual(by_id[other.id].usage, 0.0)
            self.assertEqual(by_id[other.id].recency, 0.0)

    def test_the_quality_terms_never_outweigh_the_content_tiers(self):
        """A maxed-out recency+usage boost must stay below the lexical floor's weight, or a
        popular-but-irrelevant item could outrank a precise match."""
        self.assertLess(RECENCY_WEIGHT + USAGE_WEIGHT, LEXICAL_WEIGHT + GRAPH_WEIGHT)
        self.assertLess(RECENCY_WEIGHT, LEXICAL_WEIGHT)
        self.assertLess(USAGE_WEIGHT, LEXICAL_WEIGHT)

    def test_the_deterministic_tiebreak_is_unchanged(self):
        """Equal scores still order created_at ASC then id ASC — the new terms enter the SCORE,
        never the tiebreak (a tiebreak on run-state would depend on state a recall mutates)."""
        same = [MemoryItem.create("x", "y", created_at=_ago(3), id=f"id-{i}") for i in (2, 0, 1)]
        hits = tiered_recall(_FakeStore(same), "nothing matches here", now=NOW)
        self.assertEqual([h.item.id for h in hits], ["id-0", "id-1", "id-2"])

    def test_a_usage_signals_failure_falls_back_to_the_legacy_ranking(self):
        """`MemoryStore.usage_signals` swallows its own failures, but `tiered_recall` accepts any
        duck-typed store — including one whose reader makes no such promise. No tier may crash a
        recall, and a signal added after that contract was written gets no exemption from it."""
        class Exploding(_FakeStore):
            def usage_signals(self, ids):
                raise RuntimeError("driver went away")

        hits = tiered_recall(Exploding(self.items), "deploy runbook", now=NOW)
        self.assertTrue(hits)
        for hit in hits:
            self.assertEqual(hit.recency, 0.0)
            self.assertEqual(hit.usage, 0.0)
            self.assertAlmostEqual(hit.score, self._legacy_fused(hit), places=12)

    def test_the_store_seam_swallows_a_missing_backend_reader(self):
        real = MemoryStore(_store(tempfile.mkdtemp()))
        real.backend = object()                    # no usage_stats attribute
        self.assertEqual(real.usage_signals(["a"]), {})


class TestScoringCurves(unittest.TestCase):
    def test_recency_is_zero_without_hits_whatever_the_dates(self):
        signal = lifecycle.UsageSignal(hits=0, last_recalled_at=NOW)
        self.assertEqual(lifecycle.recency_score(signal, NOW, NOW), 0.0)

    def test_recency_halves_every_half_life(self):
        signal = lifecycle.UsageSignal(hits=1, last_recalled_at=_ago(
            lifecycle.RECENCY_HALF_LIFE_DAYS))
        self.assertAlmostEqual(lifecycle.recency_score(signal, "", NOW), 0.5, places=6)

    def test_recency_falls_back_to_created_at_when_the_stamp_is_missing(self):
        signal = lifecycle.UsageSignal(hits=3, last_recalled_at=None)
        self.assertGreater(lifecycle.recency_score(signal, _ago(1), NOW), 0.9)

    def test_recency_clamps_a_future_stamp(self):
        signal = lifecycle.UsageSignal(hits=1, last_recalled_at=_ago(-30))   # clock skew
        self.assertEqual(lifecycle.recency_score(signal, "", NOW), 1.0)

    def test_an_unparseable_timestamp_degrades_to_no_signal(self):
        signal = lifecycle.UsageSignal(hits=1, last_recalled_at="not-a-date")
        self.assertEqual(lifecycle.recency_score(signal, "also not a date", NOW), 0.0)

    def test_usage_saturates_and_stays_bounded(self):
        self.assertEqual(lifecycle.usage_score(lifecycle.UsageSignal(hits=0)), 0.0)
        self.assertLess(lifecycle.usage_score(lifecycle.UsageSignal(hits=10**6)), 1.0)
        self.assertGreater(lifecycle.usage_score(lifecycle.UsageSignal(hits=10)),
                           lifecycle.usage_score(lifecycle.UsageSignal(hits=1)))
        self.assertEqual(lifecycle.usage_score(lifecycle.UsageSignal(hits=-5)), 0.0)


# ===================================================================== (4) BUDGETS + ARCHIVAL
class TestValidityWindow(unittest.TestCase):
    def test_an_item_with_no_window_reads_as_open(self):
        """Every pre-v4 item. Reading an absent window as CLOSED would retire the whole corpus."""
        self.assertTrue(lifecycle.is_open(MemoryItem.create("s", "v")))

    def test_close_window_sets_both_ends_and_keeps_everything_else(self):
        item = MemoryItem.create("s", "an approved value")
        lifecycle.close_window(item, NOW)
        self.assertFalse(lifecycle.is_open(item))
        self.assertEqual(item.valid_to, NOW)
        self.assertEqual(item.valid_from, item.created_at)
        self.assertEqual(item.value, "an approved value")
        self.assertTrue(item.provenance)

    def test_closing_an_already_closed_window_is_a_no_op(self):
        """The FIRST close is the true one — overwriting it would rewrite history to say the fact
        stopped being true later than it did."""
        item = MemoryItem.create("s", "v")
        lifecycle.close_window(item, NOW)
        lifecycle.close_window(item, "2027-01-01T00:00:00+00:00")
        self.assertEqual(item.valid_to, NOW)

    def test_the_window_round_trips_through_the_doc(self):
        item = MemoryItem.create("s", "v")
        lifecycle.close_window(item, NOW)
        back = MemoryItem.from_dict(item.to_dict())
        self.assertEqual(back.valid_to, NOW)
        self.assertFalse(lifecycle.is_open(back))


class TestBudgetSweep(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.backend = _store(self.tmp)
        self.store = MemoryStore(self.backend)
        self._budget = lifecycle._EPISODIC_BUDGETS["personal"]
        lifecycle._EPISODIC_BUDGETS["personal"] = 3

    def tearDown(self) -> None:
        lifecycle._EPISODIC_BUDGETS["personal"] = self._budget

    def _fill(self, n: int = 6, **kw):
        items = [MemoryItem.create(f"turn {i}", f"value {i}", mtype=EPISODIC,
                                   created_at=_ago(n - i), **kw) for i in range(n)]
        for it in items:
            self.backend.put(it)
        return items

    def test_a_bucket_within_budget_proposes_nothing(self):
        self._fill(2)
        self.assertEqual(self.store.propose_archival(now=NOW), [])

    def test_an_over_budget_bucket_proposes_exactly_the_excess(self):
        self._fill(6)
        proposals = self.store.propose_archival(now=NOW)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].kind, ARCHIVE)
        self.assertEqual(len(proposals[0].olds), 3)

    def test_the_sweep_proposes_but_never_evicts(self):
        """P2, structurally: proposing must leave the store byte-identical."""
        items = self._fill(6)
        before = {i.id: (i.status, i.valid_to) for i in self.backend.all()}
        self.store.propose_archival(now=NOW)
        after = {i.id: (i.status, i.valid_to) for i in self.backend.all()}
        self.assertEqual(before, after)
        self.assertEqual(len(after), len(items))

    def test_the_coldest_are_selected_and_a_hot_item_survives(self):
        items = self._fill(6)
        hot = items[0]                             # the OLDEST — cold by every other measure
        self.backend.record_usage([hot.id], NOW)
        proposals = self.store.propose_archival(now=NOW)
        self.assertNotIn(hot.id, {o.id for o in proposals[0].olds},
                         "a recently-used item was proposed for archival")

    def test_a_pinned_item_is_counted_but_never_archived(self):
        self._fill(4)
        pinned = [MemoryItem.create(f"pin {i}", "v", mtype=EPISODIC, pin=True,
                                    created_at=_ago(99)) for i in range(2)]
        for it in pinned:
            self.backend.put(it)
        proposals = self.store.propose_archival(now=NOW)
        proposed = {o.id for p in proposals for o in p.olds}
        for it in pinned:
            self.assertNotIn(it.id, proposed, "a pin is an un-overridable floor (doc 62 §3)")
        # ...and it WAS counted: 6 items against a budget of 3 means a proposal exists at all.
        self.assertTrue(proposals)

    def test_applying_closes_the_window_and_deletes_nothing(self):
        """THE never-delete pin, on the live apply path."""
        self._fill(6)
        before = len(self.backend.all())
        proposal = self.store.propose_archival(now=NOW)[0]
        archived_ids = {o.id for o in proposal.olds}
        result = self.store.apply_consolidation(proposal, "approve", assume_yes=True)
        self.assertTrue(result.changed)
        rows = self.backend.all()
        self.assertEqual(len(rows), before, "an archival deleted a row")
        for row in rows:
            if row.id in archived_ids:
                self.assertEqual(row.status, ARCHIVED)
                self.assertFalse(lifecycle.is_open(row))
                self.assertTrue(row.value, "the approved value must survive archival")
            else:
                self.assertEqual(row.status, ACTIVE)

    def test_a_rejected_sweep_changes_nothing(self):
        self._fill(6)
        proposal = self.store.propose_archival(now=NOW)[0]
        result = self.store.apply_consolidation(proposal, "reject")
        self.assertFalse(result.changed)
        self.assertTrue(all(lifecycle.is_open(i) for i in self.backend.all()))

    def test_a_declined_gate_changes_nothing(self):
        """Human-gated: declining at the gate must leave every window open."""
        self._fill(6)
        proposal = self.store.propose_archival(now=NOW)[0]
        result = self.store.apply_consolidation(proposal, "approve", confirm=lambda _t: False)
        self.assertFalse(result.changed)
        self.assertTrue(all(lifecycle.is_open(i) for i in self.backend.all()))

    def test_the_sweep_converges_after_one_approval(self):
        self._fill(6)
        proposal = self.store.propose_archival(now=NOW)[0]
        self.store.apply_consolidation(proposal, "approve", assume_yes=True)
        self.assertEqual(self.store.propose_archival(now=NOW), [],
                         "an archived item was still counted against its budget")

    def test_the_selection_is_deterministic(self):
        """A sweep that proposed a different set on each run over an unchanged store would be
        impossible for a human to review."""
        items = self._fill(6)
        first = [o.id for o in propose_archival(items, {}, NOW)[0].olds]
        second = [o.id for o in propose_archival(list(reversed(items)), {}, NOW)[0].olds]
        self.assertEqual(first, second)


class TestBudgetTable(unittest.TestCase):
    """Kept OUT of TestBudgetSweep on purpose: that class monkeypatches the personal episodic
    budget in setUp, so a table assertion living there would read the patched value."""

    def test_budgets_differ_by_scope_and_type(self):
        self.assertNotEqual(lifecycle.budget_for("global", PERSISTENT),
                            lifecycle.budget_for("personal", PERSISTENT))
        self.assertGreater(lifecycle.budget_for("personal", EPISODIC),
                           lifecycle.budget_for("personal", PERSISTENT))
        self.assertGreater(lifecycle.budget_for("personal", PERSISTENT),
                           lifecycle.budget_for("global", PERSISTENT))

    def test_an_unrecognised_scope_errs_toward_proposing_nothing(self):
        """A bigger budget proposes LESS — an unreadable scope must not lead to proposing the
        retirement of items whose scope we could not even read."""
        unknown = lifecycle.budget_for("a-level-nobody-anticipated", PERSISTENT)
        self.assertEqual(unknown, lifecycle.DEFAULT_BUDGET)
        self.assertGreaterEqual(unknown, max(lifecycle._DURABLE_BUDGETS.values()))


# ===================================================================== (5) LOCKED DECISIONS
class TestFormulaTypedEnvelope(unittest.TestCase):
    """The locked doc-62 'typed-envelope formulae' decision, landed."""

    def test_a_bare_string_trigger_is_one_entry_not_a_character_iteration(self):
        """The bug the envelope closes: `for t in "auth"` matched on single letters, so the
        formula fired on almost every query with nothing anywhere to attribute it to."""
        item = make_formula("f", "{x}")
        item.applicability = {"triggers": "auth"}
        self.assertEqual(normalize_applicability(item.applicability)["triggers"], ["auth"])
        self.assertFalse(applies_to(item, "a"))
        self.assertFalse(applies_to(item, "deploy the app"))
        self.assertTrue(applies_to(item, "auth flow"))

    def test_the_envelope_always_has_all_three_typed_fields(self):
        for raw in ({}, None, ["not", "a", "dict"], "a string", {"triggers": None}, 7):
            envelope = normalize_applicability(raw)
            self.assertEqual(set(envelope), {"triggers", "topic", "params"})
            self.assertIsInstance(envelope["triggers"], list)
            self.assertIsInstance(envelope["topic"], str)
            self.assertIsInstance(envelope["params"], list)

    def test_unreadable_metadata_never_matches(self):
        """Coerce, don't reject — but an envelope we cannot read as metadata matches nothing."""
        item = make_formula("f", "{x}")
        item.applicability = ["garbage"]
        self.assertFalse(applies_to(item, "anything at all"))

    def test_empty_and_duplicate_terms_are_dropped_deterministically(self):
        envelope = normalize_applicability({"triggers": ["tax", "", "  ", "tax", "vat"]})
        self.assertEqual(envelope["triggers"], ["tax", "vat"])

    def test_the_constructor_and_the_reader_agree_on_shape(self):
        item = make_formula("f", "{a} + {b}", triggers=["tax"], topic="finance")
        self.assertEqual(item.applicability, normalize_applicability(item.applicability))


class TestLockedDecisionsAlreadyLanded(unittest.TestCase):
    """The other two locked 2026-07-14 decisions shipped at TM.S6/TM.S7 — pinned here so the
    'landed' claim in the D-list is checkable rather than asserted."""

    def test_categories_are_fixed_core_plus_free_tags(self):
        from mokata.memory.scope import CATEGORY, CONVENTIONAL_CATEGORIES, is_valid_scope
        self.assertTrue(CONVENTIONAL_CATEGORIES, "the fixed CORE must exist")
        self.assertTrue(is_valid_scope(CATEGORY))
        # ...and the tags themselves are free: an arbitrary string is a valid category id.
        item = MemoryItem.create("s", "v", scope_level=CATEGORY, scope_id="a-brand-new-tag")
        self.assertEqual(MemoryItem.from_dict(item.to_dict()).scope_id, "a-brand-new-tag")

    def test_the_merge_ladder_is_written_down_and_cycle_proof(self):
        from mokata.memory import precedence
        from mokata.memory.scope import assert_acyclic
        self.assertIn("THE RESOLUTION LADDER", precedence.__doc__ or "")
        assert_acyclic()                           # raises on any cycle / diamond


# ===================================================================== (6) GROWTH-DEAD
class TestGrowthIsGone(unittest.TestCase):
    def test_the_dead_growth_module_is_deleted(self):
        """D3: `RetainOnSuccess`/`FormulaProposal`/`apply_formula_proposal` were public exports
        with zero production callers for three releases. The retrieval half stays."""
        with self.assertRaises(ImportError):
            __import__("mokata.memory.growth")
        import mokata.memory as memory
        for gone in ("RetainOnSuccess", "FormulaProposal", "apply_formula_proposal",
                     "assert_growable", "GROWABLE_KINDS"):
            self.assertNotIn(gone, memory.__all__)
            self.assertFalse(hasattr(memory, gone))
        self.assertTrue(hasattr(memory, "make_formula"), "the LIVE formula half must survive")


if __name__ == "__main__":
    unittest.main()
