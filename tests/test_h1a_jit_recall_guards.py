"""H-1a grounding — the two filed defects that BLOCK per-turn recall injection (doc 84 §4).

Both are on `memory/brain.py`, both are harmless where the code sits today (its only production
caller is a benchmark op), and both become live on the warm path of EVERY turn the moment H-1a
wires the injection hook. They are fixed BEFORE any hook wiring, in their own commit, for exactly
that reason.

  * JIT-RECALL-COUNTS-A-READ — `jit_recall` read `store.all_active()`, which calls `_bump_read()`
    -> `_persist_stats` -> DISK. So the retrieval half of H-1a was not read-only: it wrote
    `memory_stats` on every call. This is the SAME defect DB.S7c1 already found and fixed on the
    healing side (`_subgraph_visible`); the fix — split the INSTRUMENTATION, never the rule — was
    never applied to the JIT path. Left unfixed it breaks the read-only-injection pin AND makes
    `stats.reads` (surfaced by `/mokata:govern` as the read/write ratio) a count of TURNS.

  * JIT-RECALL-UNSCOPED — `jit_recall` and `always_on_items` read `all_active`/`peek_active`
    DIRECTLY, so neither applied the TM.S6 scope union, the M-A2 precedence winner, nor the TM.S10
    readability filter. Byte-identical in local/zero-config mode (no scope context, no enforcing
    policy), so it is invisible until TEAM mode — where a teammate's private item can reach an
    auto-injected surface, which is precisely what DB.S8's S-2 contract forbids.

The read-only claim is pinned BEHAVIOURALLY (a whole-tree byte snapshot around the call), never by
a name-based sweep: asserting "does not call `all_active`" pins the mutation someone thought to
name and passes for every one they did not.

The cross-seat leak also has a LIVE-POSTGRES leg (`tests/integration/test_h1a_live_db.py`) — the
unit level has produced false greens on exactly this class before (doc 84 SHIM-FALSE-GREEN).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _support import sqlite_disk_ok, tree_snapshot

from mokata.govern import AuditLedger
from mokata.memory.access import AccessPolicy, VIEWER
from mokata.memory.backends import SQLiteBackend
from mokata.memory.brain import always_on_items, always_on_lines, jit_recall
from mokata.memory.item import MemoryItem
from mokata.memory.scope import CATEGORY, PERSONAL, PROJECT
from mokata.memory.store import MemoryStore

_QUERY = "deploy the release pipeline on friday"


def _plain_store(tmp, *, identity=None, access=None, scope_context=None):
    """A directly-constructed store (no Surface): the local/zero-config shape."""
    return MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")),
                       ledger=AuditLedger(os.path.join(tmp, "ledger.jsonl")),
                       identity=identity, access=access, scope_context=scope_context)


def _put(store, subject, value, *, kind="context", **scope):
    item = MemoryItem.create(subject, value, kind=kind,
                             id=scope.pop("id", None) or subject, **scope)
    store.backend.put(item)
    return item


# ==================================================================== JIT-RECALL-COUNTS-A-READ
@unittest.skipUnless(sqlite_disk_ok(), "sandbox cannot back an on-disk SQLite DB")
class TestJitRecallIsANonCountingRead(unittest.TestCase):
    """A `jit_recall` performs ZERO durable writes."""

    def _surface_store(self, d):
        from mokata.config import Surface
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        surface = Surface.load(d)
        return surface, MemoryStore.from_surface(surface)

    def test_a_jit_recall_writes_nothing_to_disk(self):
        """THE pin, behaviourally: hash the WHOLE tree before and after a recall. Any durable
        write at all — the stats bump this defect was filed for, or anything else — fails it."""
        with tempfile.TemporaryDirectory() as d:
            _surface, store = self._surface_store(d)
            _put(store, "deploy", "deploy the pipeline on friday")
            _put(store, "release", "release pipeline notes")
            store.all_active()                       # warm: a REAL counting read persists once
            before = tree_snapshot(d)
            for _ in range(3):
                hits = jit_recall(store, _QUERY)
            self.assertTrue(hits, "the fixture must actually retrieve, or this pins nothing")
            self.assertEqual(before, tree_snapshot(d),
                             "jit_recall performed a DURABLE WRITE on the auto-injection path")

    def test_a_jit_recall_does_not_move_the_read_counter(self):
        """The other half of the same defect: `stats.reads` is the read/write ratio
        `/mokata:govern` surfaces. Counting an auto-injection makes it a count of TURNS."""
        with tempfile.TemporaryDirectory() as d:
            _surface, store = self._surface_store(d)
            _put(store, "deploy", "deploy the pipeline on friday")
            before = store.stats.reads
            for _ in range(5):
                jit_recall(store, _QUERY)
            self.assertEqual(before, store.stats.reads)

    def test_always_on_items_stays_non_counting(self):
        """Regression: the always-on half was ALREADY non-counting (`peek_active`) and the scope
        fix must not have cost it that."""
        with tempfile.TemporaryDirectory() as d:
            _surface, store = self._surface_store(d)
            _put(store, "rule", "always run the tests", kind="rule")
            store.all_active()
            before = tree_snapshot(d), store.stats.reads
            for _ in range(3):
                self.assertTrue(always_on_items(store))
            self.assertEqual(before, (tree_snapshot(d), store.stats.reads))

    def test_a_real_recall_still_counts(self):
        """The instrumentation was SPLIT, not removed — a genuine user recall still counts."""
        with tempfile.TemporaryDirectory() as d:
            _surface, store = self._surface_store(d)
            _put(store, "deploy", "deploy the pipeline on friday")
            before = store.stats.reads
            store.all_active()
            self.assertEqual(before + 1, store.stats.reads)


# ======================================================================== JIT-RECALL-UNSCOPED
class TestJitPathAppliesTheVisibilityRule(unittest.TestCase):
    """Both auto-injection reads go through the store's ONE visibility rule (`_visible_filter`)."""

    def test_a_teammates_private_item_never_returns_from_jit_recall(self):
        """DB.S8 S-2, at the unit floor: bob is a project viewer. Alice's PERSONAL item is not
        his to see, and an auto-injected surface is the worst place for it to appear."""
        with tempfile.TemporaryDirectory() as d:
            policy = AccessPolicy.from_grants({"project": {VIEWER: ["bob"]}}, enforce=True)
            store = _plain_store(d, identity="bob", access=policy)
            _put(store, "mine", "deploy the pipeline on friday",
                 id="visible", scope_level=PROJECT, scope_id="web")
            _put(store, "hers", "deploy the pipeline on friday — alice private",
                 id="private", scope_level=PERSONAL, scope_id="alice")
            ids = {i.id for i in jit_recall(store, _QUERY)}
            self.assertEqual({"visible"}, ids,
                             "a teammate's PRIVATE item reached the JIT injection path")

    def test_a_teammates_private_rule_never_reaches_the_always_on_lines(self):
        """The always-on half is ALREADY live in the SessionStart briefing, so this leak is not
        hypothetical — it is shipping today for any team seat."""
        with tempfile.TemporaryDirectory() as d:
            policy = AccessPolicy.from_grants({"project": {VIEWER: ["bob"]}}, enforce=True)
            store = _plain_store(d, identity="bob", access=policy)
            _put(store, "team-rule", "always run the tests", kind="rule",
                 id="visible", scope_level=PROJECT, scope_id="web")
            _put(store, "alice-rule", "alice's private rule", kind="rule",
                 id="private", scope_level=PERSONAL, scope_id="alice")
            self.assertEqual({"visible"}, {i.id for i in always_on_items(store)})
            lines, _overflow = always_on_lines(store, 12)
            self.assertEqual(1, len(lines))
            self.assertNotIn("alice", " ".join(lines))

    def test_an_out_of_scope_item_is_not_in_the_union(self):
        """TM.S6: recall is the UNION up the scope PATH, not the whole table. A sibling
        category the working scope never reaches must not be injected."""
        from mokata.memory.scope import ScopeContext
        with tempfile.TemporaryDirectory() as d:
            ctx = ScopeContext(project="web", category="payments")
            store = _plain_store(d, scope_context=ctx)
            _put(store, "mine", "deploy the pipeline on friday",
                 id="on-path", scope_level=CATEGORY, scope_id="payments")
            _put(store, "theirs", "deploy the pipeline on friday",
                 id="off-path", scope_level=CATEGORY, scope_id="search")
            self.assertEqual({"on-path"}, {i.id for i in jit_recall(store, _QUERY)})

    def test_the_precedence_winner_is_injected_not_both(self):
        """M-A2: two conflicting scoped items for one subject must not BOTH inject — the
        narrower scope wins, and the injection surface sees exactly one."""
        from mokata.memory.scope import ScopeContext
        with tempfile.TemporaryDirectory() as d:
            ctx = ScopeContext(project="web", category="payments")
            store = _plain_store(d, scope_context=ctx)
            _put(store, "deploy-day", "deploy the pipeline on friday",
                 id="broad", scope_level=PROJECT, scope_id="web")
            _put(store, "deploy-day", "deploy the pipeline on friday — narrow",
                 id="narrow", scope_level=CATEGORY, scope_id="payments")
            hits = jit_recall(store, _QUERY)
            self.assertEqual(1, len(hits), "both scope levels injected — no precedence winner")
            self.assertEqual("narrow", hits[0].id)

    def test_local_zero_config_recall_is_byte_identical(self):
        """The guard on the whole change: with no scope context and no enforcing policy the
        visible filter is the identity function, so a solo user's recall is unchanged."""
        with tempfile.TemporaryDirectory() as d:
            store = _plain_store(d)
            for n in range(4):
                _put(store, f"s{n}", f"deploy the pipeline on friday {n}")
            self.assertEqual({f"s{n}" for n in range(4)},
                             {i.id for i in jit_recall(store, _QUERY, top_k=10)})


# ================================================================ duck-typing / minimal stores
class TestMinimalStoresStillWork(unittest.TestCase):
    """`brain` is duck-typed over "a store"; a double that predates the seam must not break."""

    class _OnlyAllActive:
        def __init__(self, items):
            self._items = items

        def all_active(self):
            return list(self._items)

    def test_a_double_with_only_all_active_still_recalls(self):
        items = [MemoryItem.create("deploy", "deploy the pipeline on friday", kind="context")]
        store = self._OnlyAllActive(items)
        self.assertEqual(1, len(jit_recall(store, _QUERY)))
        self.assertEqual([], always_on_items(store))


if __name__ == "__main__":                              # pragma: no cover
    unittest.main()
