"""DB.S7b — K1 bounded ≤2-hop retrieval expansion, pinned contract by contract.

Each claim is stated as the MUTATION that turns it red, because a green assertion that cannot fail
is not a contract — it is decoration. Every mutation named in a docstring below was applied to the
SOURCE, the test confirmed RED, and the source restored.

What is deliberately NOT here, and lives in `tests/integration/test_db_s7b_live_db.py` instead:
the claim that SQLite and Postgres return the SAME walk. That is a statement about two engines,
and no double can make it.

**AND A HARNESS TRAP THIS FILE GUARDS AGAINST, because it would go green.** The `_PgShim` used by
DB.S2a/DB.S2b/DB.S3 executes the Postgres backend's SQL on real SQLite with `%s` → `?`. It is
therefore perfectly capable of running a recursive CTE — measured, not assumed — so a "Postgres
traversal" test written against it would PASS while proving nothing whatsoever about Postgres.
That is worse than an unavailable path, which at least fails loudly. `NoShimInTraversalTests`
below is the guard.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.memory import edges as E
from mokata.memory import expansion as X
from mokata.memory import tiered
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem
from mokata.memory.store import MemoryStore


def _item(item_id, subject, value, **kw):
    it = MemoryItem.create(subject, value, **kw)
    it.id = item_id
    return it


def _store(tmp, items):
    backend = SQLiteBackend(os.path.join(tmp, "memory.db"))
    for it in items:
        backend.put(it)
    return MemoryStore(backend), backend


def _chain(tmp):
    """`a → b → c → d` by `depends_on`, where ONLY `a` matches the query "rotation"."""
    return _store(tmp, [
        _item("a", "alpha rotation", "the auth token rotation policy", depends_on=["b"]),
        _item("b", "beta", "the KMS key schedule", depends_on=["c"]),
        _item("c", "gamma", "the HSM vendor contract", depends_on=["d"]),
        _item("d", "delta", "the offsite escrow arrangement"),
    ])


def _snapshot(hits):
    """A hit list as comparable BYTES: id, order, and every score at FULL float precision.

    `repr` rather than a rounded compare deliberately — "byte-identical ranking" is the claim, and
    a tolerance would let a real arithmetic change through as long as it was small."""
    return [(h.item.id, repr(h.score), repr(h.lexical), repr(h.semantic), repr(h.graph),
             repr(h.recency), repr(h.usage), repr(h.edge)) for h in hits]


# ======================================================================================
# P1 · THE ≤2-HOP BOUND
# ======================================================================================
class TwoHopBoundTest(unittest.TestCase):
    """K1's whole guarantee: expansion reaches two hops and stops.

    MUTATIONS (both confirmed RED, applied separately — the bound lives in TWO places and a test
    that pins only one leaves the other free to be wrong):
      * `expansion.MAX_HOPS = 2` → `3`;
      * the SQL bound `WHERE w.depth < CAST(? AS INTEGER)` → `<=`.
    Either makes the 3-hop node `d` appear.
    """

    def test_third_hop_never_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            hits = {h.item.id: h for h in store.recall_relevant("rotation")}
            self.assertEqual(1, hits["b"].path.depth, "b is one hop from the seed")
            self.assertEqual(2, hits["c"].path.depth, "c is two hops from the seed")
            self.assertGreater(hits["b"].edge, 0.0)
            self.assertGreater(hits["c"].edge, 0.0)
            # THE PIN. `d` is three hops out and must be untouched by the expansion.
            self.assertEqual(0.0, hits["d"].edge, "a THIRD hop was walked — the bound is broken")
            self.assertIsNone(hits["d"].path)

    def test_the_sql_carries_the_bound(self):
        """The bound is BOUND, not baked — so the constant is the single source of it."""
        with tempfile.TemporaryDirectory() as tmp:
            _, backend = _chain(tmp)
            self.assertEqual({1, 2}, {r[4] for r in backend.expand_from(["a"], X.MAX_HOPS)})
            # And the depth bound genuinely travels: asking for one hop returns only one.
            self.assertEqual({1}, {r[4] for r in backend.expand_from(["a"], 1)})

    def test_the_bound_does_not_creep_across_repeated_recalls(self):
        """**THE BOUND IS PER-RECALL *AND* ACROSS RECALLS.** Found by running it, not by reading it.

        `recall_relevant` STAMPS every hit it returns, expansion-admitted ones included. If the
        seed set were taken from the FUSED score, that stamp would give `b` a non-zero score on the
        next recall, promoting it to a seed — and the walk from `b` reaches `d`, which is three
        hops from the original query. Nothing would have walked three hops in any single recall;
        the reachable set would simply creep, one recall at a time, past the bound K1 exists to
        impose.

        MUTATION (confirmed RED): seed `select_seeds` from `h.score` instead of the match score —
        `d` surfaces on the second recall.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            for run in range(4):
                hits = {h.item.id: h for h in store.recall_relevant("rotation")}
                self.assertEqual(0.0, hits["d"].edge,
                                 f"the 3-hop node surfaced on recall #{run + 1} — the bound crept")
            # …and the expansion is STABLE, not merely bounded: the same query gives the same
            # reachable set on a cold store and a warm one.
            self.assertGreater(hits["b"].edge, 0.0)
            self.assertGreater(hits["c"].edge, 0.0)

    def test_decay_keeps_a_hop_under_a_direct_match(self):
        """"Can surface, does not displace" — stated as arithmetic rather than hoped for."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            hits = store.recall_relevant("rotation")
            self.assertEqual(["a", "b", "c", "d"], [h.item.id for h in hits])
            self.assertGreater(hits[0].score, hits[1].score,
                               "a 1-hop neighbour outranked a full direct match")


# ======================================================================================
# P2 · ONE STATEMENT, ONE DIALECT (the structural half; the live half is the integration leg)
# ======================================================================================
class OneDialectTest(unittest.TestCase):
    """The two engines run the SAME statement, so agreement is a property of one string.

    MUTATION: spell any dialect-specific token in `expansion_sql` — `::text` for the CAST,
    `instr(...)` for a path guard, a `LIMIT` in the recursive term — and the two emitted strings
    stop matching (and, on the live leg, Postgres refuses outright).
    """

    def test_identical_modulo_the_placeholder(self):
        table = E.SHARED_EDGES_TABLE
        sqlite_sql = X.expansion_sql(table, 3, placeholder="?")
        pg_sql = X.expansion_sql(table, 3, placeholder="%s")
        self.assertEqual(sqlite_sql.replace("?", "%s"), pg_sql)

    def test_no_limit_in_the_traversal(self):
        """The depth bound is a PREDICATE. A `LIMIT` means different things across engines and is
        variously refused inside a recursive term — so there must not be one anywhere."""
        sql = X.expansion_sql(E.LOCAL_EDGES_TABLE, 2).upper()
        self.assertNotIn("LIMIT", sql)
        self.assertIn("WHERE W.DEPTH < CAST(", sql)

    def test_the_anchor_declares_its_types(self):
        """Every text column in the anchor comes from a real column; the one literal is CAST.

        This is what keeps Postgres from having to INFER a parameter's type in the anchor — the
        asymmetry that passes on SQLite and fails on Postgres."""
        sql = X.expansion_sql(E.LOCAL_EDGES_TABLE, 1)
        anchor = sql.split("UNION ALL")[0]
        self.assertIn(f"SELECT e.{E.SRC_COLUMN}, e.{E.SRC_COLUMN}, e.{E.DST_COLUMN}, "
                      f"e.{E.KIND_COLUMN}, CAST(1 AS INTEGER)", anchor)

    def test_both_table_names_are_reachable(self):
        """One builder, two table constants — a hardcoded name would work locally and fail on team."""
        for table in (E.LOCAL_EDGES_TABLE, E.SHARED_EDGES_TABLE):
            self.assertIn(f"FROM {table} e", X.expansion_sql(table, 1))


# ======================================================================================
# P3 · OPEN WINDOWS ONLY — a closed edge is HISTORY, not a defect
# ======================================================================================
class OpenWindowFilterTest(unittest.TestCase):
    """The three-axis distinction, on the read side.

    MUTATIONS (three, all confirmed RED, applied separately):
      * drop `AND e.valid_to IS NULL` from the ANCHOR → the closed edge surfaces at hop 1;
      * drop it from the RECURSIVE TERM → the closed edge surfaces at hop 2;
      * report a closed edge as a staleness/degrade signal → the last assertion here fails.
    The anchor and the recursive term are two independent predicates over two different row sets;
    pinning one leaves the other free.
    """

    def _graph(self, tmp):
        store, backend = _store(tmp, [
            _item("a", "alpha rotation", "the auth token rotation policy", depends_on=["b"]),
            _item("b", "beta", "the KMS key schedule", depends_on=["c"]),
            _item("c", "gamma", "the HSM vendor contract"),
            _item("z", "zeta", "the retired pager rota"),
        ])
        return store, backend

    @staticmethod
    def _close(backend, src, dst):
        with backend._connect() as conn:
            conn.execute(
                f"UPDATE {E.LOCAL_EDGES_TABLE} SET {E.VALID_TO_COLUMN}='2026-01-01T00:00:00+00:00' "
                f"WHERE {E.SRC_COLUMN}=? AND {E.DST_COLUMN}=?", (src, dst))
            conn.commit()

    def test_a_closed_first_hop_is_not_walked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = self._graph(tmp)
            self._close(backend, "a", "b")               # the ANCHOR's row
            hits = {h.item.id: h for h in store.recall_relevant("rotation")}
            self.assertEqual(0.0, hits["b"].edge, "a CLOSED edge was walked from the anchor")
            self.assertEqual(0.0, hits["c"].edge, "…and it bridged to a second hop")

    def test_a_closed_second_hop_is_not_walked(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = self._graph(tmp)
            self._close(backend, "b", "c")               # the RECURSIVE TERM's row
            hits = {h.item.id: h for h in store.recall_relevant("rotation")}
            self.assertGreater(hits["b"].edge, 0.0, "the OPEN first hop must still walk")
            self.assertEqual(0.0, hits["c"].edge, "a CLOSED edge was walked in the recursive term")

    def test_a_closed_window_is_history_not_staleness(self):
        """The third axis. A closed edge is a COMPLETE, CORRECT record of a relation that was once
        true — it must be absent from the walk AND absent from anything that reports a problem.
        Surfacing it as stale would turn honest history into a defect report."""
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = self._graph(tmp)
            self._close(backend, "a", "b")
            # The row is still on disk — closing is never deleting (R3).
            with backend._connect() as conn:
                rows = conn.execute(
                    f"SELECT {E.VALID_TO_COLUMN} FROM {E.LOCAL_EDGES_TABLE} "
                    f"WHERE {E.SRC_COLUMN}='a' AND {E.DST_COLUMN}='b'").fetchall()
            self.assertEqual(1, len(rows))
            self.assertTrue(rows[0][0], "the window must be CLOSED, not the row deleted")
            # …and nothing in the healing surface calls it a problem.
            kinds = {p.kind for p in store.detect_issues()}
            self.assertNotIn("stale", {k.lower() for k in kinds},
                             "a window-closed edge was reported as stale — it is history")


# ======================================================================================
# P4 · BYTE-IDENTICAL DEGRADE — table absent, and config OFF
# ======================================================================================
class _NoEdgeBackend:
    """A backend with no `expand_from` at all — Obsidian's files, the native client, any
    third-party adapter written before DB.S7b. Delegates everything else."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name == "expand_from":
            raise AttributeError("expand_from")
        return getattr(self._inner, name)


class _OffManifest:
    def setting(self, key, default=None):
        return {"edge_expansion": False} if key == "memory" else default

    def layer_enabled(self, name):
        return True


class _OffSurface:
    manifest = _OffManifest()


class DegradeIsByteIdenticalTest(unittest.TestCase):
    """Two independent OFF conditions, one bar: the ranking is what it was before DB.S7b existed.

    MUTATIONS (both confirmed RED):
      * make `store._edge_expander` return the backend's `expand_from` regardless of the config →
        the OFF snapshot gains edge scores;
      * make the expansion tier add a constant instead of `EDGE_WEIGHT * weight` → both snapshots
        move.
    """

    def _baseline(self, tmp):
        """The pre-DB.S7b ranking, produced by the one path that provably has no expansion in it."""
        store, _ = _chain(tmp)
        return _snapshot(tiered.tiered_recall(store, "rotation", expander=None))

    def test_a_backend_without_expand_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._baseline(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = _chain(tmp)
            store.backend = _NoEdgeBackend(backend)
            self.assertIsNone(store._edge_expander())
            self.assertEqual(baseline, _snapshot(store.recall_relevant("rotation")))

    def test_config_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._baseline(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            store.surface = _OffSurface()
            self.assertFalse(store.edge_expansion_enabled())
            self.assertIsNone(store._edge_expander())
            self.assertEqual(baseline, _snapshot(store.recall_relevant("rotation")))

    def test_on_by_default(self):
        """Doc 84 K1 / doc 55:78 — "behind a config flag ON by default"."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            self.assertTrue(store.edge_expansion_enabled(), "K1 must be ON by default")
            self.assertIsNotNone(store._edge_expander())

    def test_an_empty_edge_table_changes_nothing(self):
        """The tier is WIRED and runs; there is simply nothing to reach. This is the case a real
        store spends most of its life in, and it must cost the ranking nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _store(tmp, [_item("a", "alpha rotation", "the rotation policy"),
                                    _item("b", "beta", "unrelated")])
            baseline = _snapshot(tiered.tiered_recall(store, "rotation", expander=None))
            self.assertIsNotNone(store._edge_expander())
            self.assertEqual(baseline, _snapshot(store.recall_relevant("rotation")))

    def test_the_arithmetic_identity(self):
        """`EDGE_WEIGHT * 0.0` leaves the DB.S5 five-term sum bit for bit — stated as arithmetic."""
        for lex in (0.0, 0.25, 1.0):
            five = (tiered.SEMANTIC_WEIGHT * 0.0 + tiered.GRAPH_WEIGHT * 0.0
                    + tiered.LEXICAL_WEIGHT * lex + tiered.RECENCY_WEIGHT * 0.0
                    + tiered.USAGE_WEIGHT * 0.0)
            self.assertEqual(repr(five), repr(five + tiered.EDGE_WEIGHT * 0.0))

    def test_the_on_path_contribution_is_exactly_the_weighted_term(self):
        """The OTHER half, and the half the OFF tests structurally cannot reach.

        Every assertion above is about items with NO path — so a mutation to the term that only
        fires ON a path (`+ EDGE_WEIGHT * w + 0.01`) sails straight through all of them. Measured,
        not theorised: that mutation stayed GREEN against the whole OFF suite, and this is the pin
        that closes it. The claim is exact — `score == direct + EDGE_WEIGHT * path.weight`, to the
        float — because "the expansion adds precisely its weighted term and nothing else" is what
        makes the OFF case's byte-identity a consequence rather than a coincidence.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = _chain(tmp)
            direct = {h.item.id: h.score
                      for h in tiered.tiered_recall(store, "rotation", expander=None)}
            for hit in store.recall_relevant("rotation"):
                expected = direct[hit.item.id] + tiered.EDGE_WEIGHT * hit.edge
                self.assertEqual(repr(expected), repr(hit.score),
                                 f"{hit.item.id}: the expansion term is not exactly "
                                 f"EDGE_WEIGHT × weight")
            # …and the term is genuinely non-zero for the reached items, so this is not vacuous.
            reached = [h for h in store.recall_relevant("rotation") if h.edge > 0.0]
            self.assertEqual(["b", "c"], sorted(h.item.id for h in reached))


# ======================================================================================
# P5 · "WHY SURFACED" IS THE WALKED PATH, NEVER THE INLINE FIELDS
# ======================================================================================
class WhyIsTheRealPathTest(unittest.TestCase):
    """The explanation names the route the traversal ACTUALLY took.

    MUTATION (confirmed RED): build the phrase in `intelligence.why_surfaced` from the item's own
    `supersedes` / `depends_on` lists instead of `path.steps` — the fixture's decoy `supersedes:
    ["z"]` then appears in a sentence describing a relation the walk never crossed.
    """

    def _decoy(self, tmp):
        # `d` is reachable from `a` ONLY via a → depends_on → b → decided_in → d, and it ALSO
        # carries an inline `supersedes: ["z"]` that the traversal has no reason to mention.
        store, backend = _store(tmp, [
            _item("a", "alpha rotation", "the auth token rotation policy", depends_on=["b"]),
            _item("b", "beta", "the KMS key schedule"),
            _item("d", "delta", "the escrow decision", supersedes=["z"]),
            _item("z", "zeta", "the retired escrow decision"),
        ])
        # `decided_in` has no producer (doc 02 decision #2), so the b → d edge is written directly
        # — which is also what a future producer's edges will look like.
        with backend._connect() as conn:
            conn.execute(E.insert_open_sql(E.LOCAL_EDGES_TABLE), E.insert_open_params(
                E.MemoryEdge(src_id="b", dst_id="d", kind=E.DECIDED_IN,
                             valid_from="2026-01-01T00:00:00+00:00")))
            conn.commit()
        return store

    def test_the_phrase_names_the_walked_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._decoy(tmp)
            hit = {h.item.id: h for h in store.recall_relevant("rotation")}["d"]
            why = hit.explain("rotation")
            self.assertIn("depends_on", why)
            self.assertIn(E.DECIDED_IN, why)
            self.assertIn('from "a"', why, "the phrase must name the seed it was reached from")
            # THE PIN — the decoy relation was never walked and must not be spoken of.
            self.assertNotIn("supersedes", why)
            self.assertNotIn("z", why.split("(")[0].replace("[persistent]", ""))

    def test_the_path_is_the_real_edge_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._decoy(tmp)
            hit = {h.item.id: h for h in store.recall_relevant("rotation")}["d"]
            self.assertEqual([("a", "depends_on", "b"), ("b", E.DECIDED_IN, "d")],
                             [(s.src, s.kind, s.dst) for s in hit.path.steps])

    def test_a_direct_hit_claims_no_path(self):
        """An item the query matched must not acquire a traversal explanation it did not earn."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._decoy(tmp)
            hit = {h.item.id: h for h in store.recall_relevant("rotation")}["a"]
            self.assertIsNone(hit.path)
            self.assertNotIn("via", hit.explain("rotation"))


# ======================================================================================
# P6 · ONE STATEMENT PER RECALL (recursive CTEs ONLY — never a per-node loop)
# ======================================================================================
def _traced(backend):
    """Record the SQL sqlite3 ACTUALLY executes, via its own trace callback.

    Counting calls to `expand_from` would count the WRAPPER, not the backend — a per-node loop
    inside it would still be one call and the pin would be vacuous. (It was: this test's first
    version counted the wrapper, the per-node mutation stayed GREEN, and this is the fix.) The
    trace callback sits below mokata entirely, so what it sees is what the engine ran.
    """
    import contextlib
    statements = []
    real = backend._connect

    @contextlib.contextmanager
    def _tracing():
        with real() as conn:
            conn.set_trace_callback(statements.append)
            try:
                yield conn
            finally:
                conn.set_trace_callback(None)

    backend._connect = _tracing
    return statements


class OneStatementTest(unittest.TestCase):
    """The DB.S7 constraint: recursive CTEs ONLY, one statement — never a per-node fan of reads.

    MUTATION (confirmed RED): replace the CTE in `SQLiteBackend.expand_from` with a per-node
    `open_edges` loop — the edge-table statement count goes above 1 and none of them is recursive.
    """

    @staticmethod
    def _edge_statements(statements):
        return [s for s in statements if E.LOCAL_EDGES_TABLE in s and s.lstrip().upper()
                .startswith(("SELECT", "WITH"))]

    def test_a_recall_issues_exactly_one_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = _chain(tmp)
            statements = _traced(backend)
            store.recall_relevant("rotation")
            edge_reads = self._edge_statements(statements)
            self.assertEqual(1, len(edge_reads),
                             f"the traversal must be ONE statement, not one per node: {edge_reads}")
            self.assertIn("WITH RECURSIVE", edge_reads[0])

    def test_many_seeds_still_one_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = _store(tmp, [
                _item(f"s{n}", f"rotation subject {n}", "the rotation policy",
                      depends_on=[f"t{n}"]) for n in range(5)
            ] + [_item(f"t{n}", f"target {n}", "downstream") for n in range(5)])
            statements = _traced(backend)
            store.recall_relevant("rotation")
            self.assertEqual(1, len(self._edge_statements(statements)))


# ======================================================================================
# CYCLES — `a → b → a` must not make `a` its own expansion
# ======================================================================================
class CycleTest(unittest.TestCase):
    """`UNION ALL` does not deduplicate, so `a` genuinely comes back at depth 2 on BOTH engines
    (measured). The walk must drop it.

    MUTATION (confirmed RED): remove the `dst == seed` / already-on-path guard in
    `expansion.walk_paths` — `a` reappears as its own neighbour and boosts its own score.
    """

    def test_the_seed_is_not_its_own_expansion(self):
        rows = [("a", "a", "b", "depends_on", 1), ("a", "b", "a", "depends_on", 2)]
        result = X.walk_paths(rows, seeds=["a"], visible={"a", "b"})
        self.assertIn("b", result.paths)
        self.assertNotIn("a", result.paths, "the seed came back as its own expansion")

    def test_the_raw_walk_really_does_return_the_cycle(self):
        """The guard is not defending against a hypothetical — the SQL genuinely returns it."""
        with tempfile.TemporaryDirectory() as tmp:
            store, backend = _store(tmp, [
                _item("a", "alpha rotation", "the rotation policy", depends_on=["b"]),
                _item("b", "beta", "the schedule", depends_on=["a"]),
            ])
            rows = backend.expand_from(["a"], 2)
            self.assertIn(("a", "b", "a", "depends_on", 2), [tuple(r) for r in rows])
            hits = {h.item.id: h for h in store.recall_relevant("rotation")}
            self.assertEqual(0.0, hits["a"].edge, "a mutual pair boosted itself through a cycle")

    def test_a_longer_path_never_revisits_a_node(self):
        rows = [("a", "a", "b", "depends_on", 1), ("a", "b", "b", "depends_on", 2)]
        result = X.walk_paths(rows, seeds=["a"], visible={"a", "b"})
        self.assertEqual(1, result.paths["b"].depth, "a self-loop extended the path")

    def test_a_cyclic_route_can_never_outrank_its_acyclic_prefix(self):
        """**THE CORRECTNESS CLAIM, and it is NOT the guard that provides it.**

        Two mutations that removed the cycle guard entirely stayed GREEN against every
        result-level assertion in this file. That is not a hole in the tests — it is the truth
        about the code, and it is worth stating rather than papering over: a cyclic route to X
        CONTAINS the acyclic route to X as a prefix, and `path_weight` multiplies it by further
        kind weights (≤ 1.0) and a further `DEPTH_DECAY` (< 1.0). Weight is therefore monotonically
        non-increasing along a path, so `_better` discards the cyclic route on weight alone.

        This test pins THAT property. The guard is pinned separately, as what it actually is — a
        frontier prune. Labelling a prune as a correctness guard is how a team ends up trusting a
        line that was never load-bearing.
        """
        acyclic = (X.ExpansionStep("a", E.DEPENDS_ON, "b"),)
        cyclic = acyclic + (X.ExpansionStep("b", E.DEPENDS_ON, "c"),
                            X.ExpansionStep("c", E.DEPENDS_ON, "b"))
        self.assertLess(X.path_weight(cyclic), X.path_weight(acyclic))
        for kind in E.EDGE_KINDS:                       # holds for EVERY kind, not just the strong
            base = (X.ExpansionStep("a", kind, "b"),)
            longer = base + (X.ExpansionStep("b", kind, "c"),)
            self.assertLessEqual(X.path_weight(longer), X.path_weight(base))

    def test_the_guard_prunes_the_frontier(self):
        """The guard's REAL effect, made observable so it can be pinned at all.

        MUTATION (confirmed RED): remove the `any(s.dst == dst for s in prefix)` clause — the
        counter drops and the cyclic extension is carried forward in the frontier instead.
        """
        rows = [("a", "a", "b", E.DEPENDS_ON, 1),
                ("a", "b", "c", E.DEPENDS_ON, 2),
                ("a", "c", "b", E.DEPENDS_ON, 3)]           # ← closes the cycle back onto `b`
        result = X.walk_paths(rows, seeds=["a"], visible={"a", "b", "c"}, max_hops=3)
        self.assertGreater(result.cycles_pruned, 0, "the cycle was carried into the frontier")
        # …and the answer is right either way — which is exactly the point of the test above.
        self.assertEqual(1, result.paths["b"].depth)
        for path in result.paths.values():
            nodes = [path.seed] + [s.dst for s in path.steps]
            self.assertEqual(len(nodes), len(set(nodes)), f"path revisits a node: {path.render()}")

    def test_the_seed_cycle_is_pruned_not_merely_outranked(self):
        """`a → b → a` — the shipped-bound case. Counted, so the prune is observable here too."""
        rows = [("a", "a", "b", E.DEPENDS_ON, 1), ("a", "b", "a", E.DEPENDS_ON, 2)]
        result = X.walk_paths(rows, seeds=["a"], visible={"a", "b"})
        self.assertEqual(1, result.cycles_pruned)
        self.assertNotIn("a", result.paths)


# ======================================================================================
# SCOPE — no cross-scope leak THROUGH a hop (doc 55:83-84)
# ======================================================================================
class BridgePruneTest(unittest.TestCase):
    """An unreadable item must neither surface NOR bridge — and its id must never appear in a path.

    MUTATION (confirmed RED): filter only the final result instead of pruning at each hop (i.e.
    drop the `reached[seed].get(src)` check in `walk_paths`) — `c` then surfaces via an invisible
    `b`, and `b`'s id is printed inside `c`'s explanation.
    """

    ROWS = [("a", "a", "b", "depends_on", 1), ("a", "b", "c", "depends_on", 2)]

    def test_an_invisible_node_cannot_bridge(self):
        result = X.walk_paths(self.ROWS, seeds=["a"], visible={"a", "c"})   # `b` is NOT visible
        self.assertNotIn("b", result.paths)
        self.assertNotIn("c", result.paths,
                         "an item was reached THROUGH a node the caller may not read")

    def test_a_visible_bridge_still_works(self):
        result = X.walk_paths(self.ROWS, seeds=["a"], visible={"a", "b", "c"})
        self.assertEqual(2, result.paths["c"].depth)

    def test_no_invisible_id_reaches_a_rendered_path(self):
        result = X.walk_paths(self.ROWS, seeds=["a"], visible={"a", "c"})
        self.assertNotIn("b", " ".join(p.render() for p in result.paths.values()))

    def test_no_visibility_context_prunes_nothing(self):
        """Local zero-config: no scope context ⇒ `visible=None` ⇒ the walk is unfiltered."""
        result = X.walk_paths(self.ROWS, seeds=["a"], visible=None)
        self.assertEqual({"b", "c"}, set(result.paths))


# ======================================================================================
# BOUNDS ARE REPORTED, never silently applied
# ======================================================================================
class BoundsAreReportedTest(unittest.TestCase):
    """MUTATION (confirmed RED): drop `seeds_dropped` / `rows_truncated` from the return and the
    caller's notice — a capped read then reports as a complete one."""

    def test_seed_truncation_is_returned(self):
        ranked = [(f"i{n}", 1.0) for n in range(25)]
        seeds, dropped = X.select_seeds(ranked)
        self.assertEqual(X.SEED_CAP, len(seeds))
        self.assertEqual(25 - X.SEED_CAP, dropped)

    def test_a_zero_scoring_item_is_not_a_seed(self):
        """Doc 55:41 says seed HITS. An item the direct tiers scored 0.0 did not hit."""
        seeds, _ = X.select_seeds([("a", 0.5), ("b", 0.0), ("c", 0.0)])
        self.assertEqual(("a",), seeds)

    def test_row_truncation_is_returned(self):
        rows = [("a", "a", f"n{n}", "depends_on", 1) for n in range(X.MAX_WALKED_EDGES + 7)]
        result = X.walk_paths(rows, seeds=["a"], visible=None)
        self.assertEqual(7, result.rows_truncated)


# ======================================================================================
# WEIGHTS — the closed set is traversed GENERICALLY; the weight is the control
# ======================================================================================
class KindWeightTest(unittest.TestCase):
    """MUTATION (confirmed RED): restrict the traversal to `edges.WIRED_KINDS` — the
    `decided_in` edge in P5's fixture stops being walked and its path disappears."""

    def test_every_closed_kind_has_a_weight(self):
        for kind in E.EDGE_KINDS:
            self.assertIn(kind, X.KIND_WEIGHT, f"{kind} is in the closed set but has no weight")

    def test_the_unwired_five_default_explicitly(self):
        unwired = [k for k in E.EDGE_KINDS if k not in E.WIRED_KINDS]
        self.assertEqual(5, len(unwired))
        for kind in unwired:
            self.assertEqual(X.UNWIRED_DEFAULT_WEIGHT, X.KIND_WEIGHT[kind])

    def test_an_unwired_kind_is_still_traversed(self):
        """The set is walked GENERICALLY — a kind is controlled by its WEIGHT, never by being
        quietly absent from the walk, because an absent kind is one nobody can observe."""
        rows = [("a", "a", "b", E.PROMOTED_FROM, 1)]
        result = X.walk_paths(rows, seeds=["a"], visible={"a", "b"})
        self.assertIn("b", result.paths)
        self.assertGreater(result.paths["b"].weight, 0.0)

    def test_about_code_is_weighted_zero(self):
        """Its dst is a code path, never an item — a FORWARD hop across it can admit nothing."""
        self.assertEqual(0.0, X.KIND_WEIGHT[E.ABOUT_CODE])

    def test_a_path_multiplies_its_kinds(self):
        steps = (X.ExpansionStep("a", E.DEPENDS_ON, "b"), X.ExpansionStep("b", E.SUPERSEDES, "c"))
        self.assertAlmostEqual(
            X.KIND_WEIGHT[E.DEPENDS_ON] * X.KIND_WEIGHT[E.SUPERSEDES] * X.DEPTH_DECAY ** 2,
            X.path_weight(steps))

    def test_an_unknown_kind_is_worth_nothing(self):
        self.assertEqual(0.0, X.kind_weight("not-a-kind"))


# ======================================================================================
# HARNESS GUARD — the shim would go GREEN and prove nothing
# ======================================================================================
class NoShimInTraversalTests(unittest.TestCase):
    """The `_PgShim` executes real SQL on real SQLite (`%s` → `?`), so it runs a recursive CTE
    perfectly well — MEASURED, not assumed. A "Postgres traversal" test written against it would
    therefore pass while saying nothing about Postgres, which is the SI.6-DELEGATED-BLINDNESS
    shape: a green that reads as coverage it does not have.

    MUTATION (confirmed RED): add a `_PgShim` to this file's traversal tests — this guard fires.
    """

    # Every DB.S7b test file, swept as a SET rather than just this one — a guard that only watches
    # the file it lives in is a guard the next file routes around by existing.
    SWEPT = ("tests/test_db_s7b_bounded_expansion.py",
             "tests/integration/test_db_s7b_live_db.py")

    def test_no_shim_is_constructed_or_imported(self):
        """Detects CONSTRUCTION and IMPORT, not mention — the file has to be able to explain the
        trap in prose without tripping the guard that enforces it."""
        symbol = "_Pg" + "Shim"                 # spelled in pieces: never matches its own source
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for rel in self.SWEPT:
            path = os.path.join(root, rel)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                for num, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]
                    if f"{symbol}(" in code or (symbol in code and "import" in code):
                        offenders.append(f"{rel}:{num}")
        self.assertEqual([], offenders,
                         "a DB.S7b traversal test constructed or imported the Postgres SHIM — it "
                         "runs on SQLite and proves NOTHING about Postgres. Use the live-DB leg.")

    def test_the_guard_can_actually_fire(self):
        """The guard's own mutation, run inline: a line that CONSTRUCTS the shim is caught, while
        a line that merely names it in prose is not. Without this, the guard above could be
        vacuously green forever and nobody would know."""
        symbol = "_Pg" + "Shim"

        def caught(line):
            code = line.split("#", 1)[0]
            return f"{symbol}(" in code or (symbol in code and "import" in code)

        self.assertTrue(caught(f"        shim = {symbol}()"))
        self.assertTrue(caught(f"from test_db_s2a_pushdown import {symbol}"))
        self.assertFalse(caught(f"    The `{symbol}` runs on SQLite, so its green is worthless."))
        self.assertFalse(caught(f"        # a {symbol} here would prove nothing"))

    def test_the_shim_really_can_run_a_recursive_cte(self):
        """The evidence behind the guard, run rather than asserted: this is exactly what a shim
        would do, and exactly why its green is worthless here."""
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE {E.SHARED_EDGES_TABLE} "
                     f"({E.SRC_COLUMN} TEXT, {E.DST_COLUMN} TEXT, {E.KIND_COLUMN} TEXT, "
                     f"{E.VALID_TO_COLUMN} TEXT)")
            # noqa: E116 (the shim's whole trick — Postgres SQL, SQLite engine)
        conn.execute(f"INSERT INTO {E.SHARED_EDGES_TABLE} VALUES ('a','b','depends_on',NULL)")
        pg_sql = X.expansion_sql(E.SHARED_EDGES_TABLE, 1, placeholder="%s")
        rows = conn.execute(pg_sql.replace("%s", "?"), X.expansion_params(["a"], 2)).fetchall()
        self.assertEqual([("a", "a", "b", "depends_on", 1)], [tuple(r) for r in rows])
        conn.close()


if __name__ == "__main__":                                    # pragma: no cover
    unittest.main()
