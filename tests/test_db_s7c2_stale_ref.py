"""DB.S7c2 — STALE-REF, the MEMORY-HANDLE half: an `index_epoch`-stamped citation that outlives
the store that minted it FAILS LOUD instead of being silently acted on.

SCOPE, and it is deliberately one site. A generation stamp only means something for a citation
that crosses a store boundary, and grounding (doc 84:180) found exactly ONE in the codebase:
`prior_art.RelatedDecision.id`, minted from the recall at `brainstorm.py:554` and persisted through
`to_dict`/`from_dict` into brainstorm run state. The second nominated consumer — the MCP `recall`
tool (`mcp/tools_read.py:79`) — stays DELIBERATELY un-stamped and `TestMcpStaysUnstamped` holds
that line: its response carries no ids and its store dies with the call, so a stamp there could
only be a check that always passes (the always-equal stamp doc 84:180 forbids) or a public MCP
response-shape change, which is a different decision.

THE PINS, and what each one would let through if it were not here:

  S1  FIRES ON `index_epoch` STALENESS ONLY — a citation minted at one epoch and read at another
      is refused; same epoch is allowed. Without it the feature is decorative.
  S2  A WINDOW-CLOSED EDGE NEVER FIRES — closing an edge's validity window is HISTORY (R3), not
      staleness. The epoch is computed from the ITEM table alone, so this is structural rather
      than a special case. Without it, every K1/K2 relation withdrawal invalidates every
      outstanding citation and the refusal becomes noise a human learns to click through.
  S3  A RETIRED-STATUS ITEM NEVER FIRES — retirement is K2's signal, not this one. The check
      cannot even SEE `status`: it compares two epochs. Without it, STALE-REF grows a second,
      quieter opinion about supersession and starts contradicting healing.
  S4  LOUD, NEVER SILENT-CORRECT — the refusal names its fix and the check mutates NOTHING. No
      auto-refresh of a stale stamp. Without it, "self-healing" would quietly re-stamp a citation
      to the current epoch, which is the exact silent-act-on-stale-data this stage exists to stop.
  S5  VALIDATION IS A COMPARISON, NOT A QUERY — `check_stale_refs` takes no store, opens no
      connection, and resolves no id. Without it, a gate on the approve path acquires a read
      surface, and the "one cheap read, N comparisons" cost model becomes N reads.

Two boundaries, both asserted rather than assumed:

  * THE FLOOR DECLARES OFF, ONCE, HONESTLY. `_revision` is stamped only on the Postgres read paths
    (`backends.py:1239/:1296/:1341`); the SQLite floor has none, so STALE-REF is OFF there — and
    OFF is the ABSENCE of a stamp (`INDEX_EPOCH_OFF == ""`), never an invented constant that would
    compare equal to itself forever. An always-equal stamp is the trap doc 84:180 names twice.
  * H-6 IS NOT WIRED HERE. `fingerprint_forces_refresh` (`freshness.py:531`) is the CODE-ANCHOR
    half and belongs to H-6 (doc 02 decision #3). This stage must not reach for it.

NOTE on the short class names: the self-protect entropy backstop filed at `84:68` blocks long
CamelCase test identifiers, and blocked this file's first draft. Names are kept short rather than
carrying a `secret ignore` for each one.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import inspect
import pathlib
import sqlite3
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.brainstorm import Approach, BrainstormGateError, BrainstormSession
from mokata.brainstorm_impact import DesignFitVerdict
from mokata.govern.stale_ref_gate import (GATE_ID, StaleRefOutcome, brainstorm_stale_ref_gate,
                                          check_stale_refs)
from _translating import (
    Declaration,
    Interception,
    TranslatingConnection,
    placeholder_rewrite,
)

from mokata.memory.backends import PostgresBackend, SQLiteBackend
from mokata.memory.item import ACTIVE, PERSISTENT, SUPERSEDED, MemoryItem
from mokata.memory.staleness import INDEX_EPOCH_OFF, is_stale, read_index_epoch
from mokata.prior_art import RelatedDecision, run_prior_art


# ---------------------------------------------------------------- shared-store shim
def _to_regclass(conn, _sql, params):
    """Answer the backend's catalogue probe the way Postgres does: the table's name when it
    exists, NULL when it does not.

    This is an INTERCEPTION, not a rewrite — the engine never sees the statement. Letting it RAISE
    would make the backend's edge probe report a DEGRADED capability, i.e. a loud and correct
    warning about the SHIM rather than about the code under test."""
    name = (params or (None,))[0]
    present = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return conn.execute("SELECT ?", (present[0] if present else None,))


_DECLARATION = Declaration(
    suite="DB.S7c2 stale-ref index epoch",
    reason="an epoch claimed to CHANGE on a write must be proven against an actual SQL engine, "
           "not a hand-parsed fake that can only confirm the strings it was taught.",
    rewrites=(placeholder_rewrite(),),
    interceptions=(
        Interception(
            "to_regclass",
            matches=lambda sql: "to_regclass" in sql,
            respond=_to_regclass,
            why="SQLite has no catalogue function; the probe is answered from `sqlite_master`."),
    ),
    not_proven=(
        "anything about Postgres's catalogue itself — `to_regclass` is INTERCEPTED, so this suite "
        "never executes the probe the production code actually sends",
        "Postgres's own visibility rules for a concurrently-provisioned table: the interception "
        "answers from SQLite's schema, which has neither Postgres's transactional DDL nor its "
        "catalogue snapshot semantics",
    ),
)


class _PgShim(TranslatingConnection):
    """Runs the Postgres backend's SQL for real, on SQLite (`%s` -> `?`), against a table shaped
    like the provisioned `mokata_memory`.

    NOT the same posture as `test_db_s2a_pushdown._PgShim`, which this docstring claimed until
    0.0.17 stage 2 measured it: that shim translates placeholders and emulates NOTHING, whereas
    this one also INTERCEPTS the `to_regclass` capability probe and answers it itself. The stronger
    concession is now declared below rather than described as the weaker one."""

    def __init__(self):
        super().__init__(_DECLARATION)
        self._c.execute(
            """CREATE TABLE mokata_memory (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   id TEXT UNIQUE, mtype TEXT, subject TEXT, status TEXT, doc TEXT,
                   project TEXT, revision INTEGER NOT NULL DEFAULT 1,
                   scope_level TEXT NOT NULL DEFAULT 'personal', scope_id TEXT,
                   pin INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
                   valid_from TEXT, valid_to TEXT,
                   hit_count INTEGER NOT NULL DEFAULT 0, last_recalled_at TEXT
               )"""
        )


def _item(subject, value, item_id, status=ACTIVE):
    return MemoryItem(subject=subject, value=value, id=item_id, mtype=PERSISTENT, status=status)


def _pg(project="proj-a"):
    shim = _PgShim()
    return PostgresBackend(project=project, conn=shim), shim


def _cite(item_id="m1", epoch="", source="recall"):
    return RelatedDecision(id=item_id, subject="retry policy", kind="decision",
                           source=source, index_epoch=epoch)


def _recall_one(_query):
    return [_item("retry policy", "use utils.retry", "m1")]


def _lensed_session():
    s = BrainstormSession("add a retry helper")
    s.propose_approaches([
        Approach("a", "write a new retry loop", pros=["simple"], cons=["dup"], targets=["retry"]),
        Approach("b", "extend utils.retry", pros=["reuse"], cons=["coupling"], targets=["retry"]),
    ])
    s.assess_impacts(layer=None)
    s.record_design_fit("a", DesignFitVerdict("a", "fits"))
    return s


def _src_files():
    import mokata
    return list(pathlib.Path(mokata.__file__).parent.rglob("*.py"))


def _identifiers(mod):
    """Every identifier the module's CODE names — variables, attributes, arguments, functions,
    classes, imports, keyword names.

    Deliberately AST and not a text grep: a grep over the raw file also reads docstrings and
    comments, so it fails on a module that merely EXPLAINS what it refuses to do — which is the
    opposite of what these pins are for. Reasoning about a concept means naming it in code."""
    tree = ast.parse(inspect.getsource(mod))
    names = set()
    for node in ast.walk(tree):
        for attr in ("id", "attr", "arg", "name", "module"):
            val = getattr(node, attr, None)
            if isinstance(val, str):
                names.add(val.lower())
        for alias in getattr(node, "names", []) or []:
            if isinstance(alias, ast.alias):
                names.add(alias.name.lower())
    return names


# ================================================================ S1 · fires on epoch staleness
class TestS1FiresOnEpochOnly(unittest.TestCase):
    """The regression: a citation minted at one epoch, read at another, is REFUSED. Fails on
    pre-DB.S7c2 code, which had no epoch and acted on the citation happily."""

    def test_citation_from_another_epoch_refused(self):
        out = check_stale_refs(decisions=[_cite(epoch="1.1.1")], current_epoch="1.2.1")
        self.assertTrue(out.refused)
        self.assertEqual(["m1"], list(out.stale_ids))

    def test_citation_from_same_epoch_allowed(self):
        out = check_stale_refs(decisions=[_cite(epoch="1.1.1")], current_epoch="1.1.1")
        self.assertFalse(out.refused)
        self.assertTrue(out.allowed)
        self.assertEqual([], list(out.stale_ids))

    def test_only_stale_citations_named(self):
        out = check_stale_refs(
            decisions=[_cite("m1", epoch="1.1.1"), _cite("m2", epoch="9.9.9")],
            current_epoch="1.1.1")
        self.assertTrue(out.refused)
        self.assertEqual(["m2"], list(out.stale_ids))

    def test_epoch_moves_on_a_write(self):
        """S1 is worthless if the epoch never moves. Proven on a real SQL engine, not asserted."""
        backend, _shim = _pg()
        empty = backend.index_epoch()
        backend.put(_item("retry", "use utils.retry", "m1"))
        after_insert = backend.index_epoch()
        self.assertNotEqual(empty, after_insert)
        backend.put(_item("retry", "use utils.retry v2", "m2"))
        self.assertNotEqual(after_insert, backend.index_epoch())

    def test_unstamped_citation_never_fires(self):
        # OFF is the ABSENCE of a stamp. An un-stamped citation is not evidence of staleness.
        out = check_stale_refs(decisions=[_cite(epoch=INDEX_EPOCH_OFF)], current_epoch="1.2.1")
        self.assertFalse(out.refused)


# ================================================================ S2 · a closed edge window
class TestS2ClosedEdgeNeverFires(unittest.TestCase):
    """Closing an edge's validity window is R3 HISTORY, not staleness. Structural, not a carve-out:
    the epoch reads the ITEM table and never the edge table, so an edge close CANNOT move it."""

    def test_epoch_reads_items_never_edges(self):
        from mokata import teamdb
        from mokata.memory import edges as _edges
        backend, shim = _pg()
        backend.index_epoch()
        sql = " ".join(shim.sql_log)
        self.assertIn(teamdb.MEMORY_TABLE, sql)
        self.assertNotIn(_edges.SHARED_EDGES_TABLE, sql)

    def test_closing_a_window_leaves_epoch(self):
        backend, shim = _pg()
        backend.put(_item("retry", "use utils.retry", "m1"))
        before = backend.index_epoch()
        # an edge close is a write to a DIFFERENT table — the epoch's aggregate cannot see it.
        shim.execute("CREATE TABLE IF NOT EXISTS mokata_memory_edges "
                     "(src_id TEXT, kind TEXT, dst_id TEXT, valid_from TEXT, valid_to TEXT)")
        shim.execute("INSERT INTO mokata_memory_edges VALUES ('m1','depends_on','m2','t0',NULL)")
        shim.execute("UPDATE mokata_memory_edges SET valid_to='t1' WHERE src_id='m1'")
        self.assertEqual(before, backend.index_epoch())
        self.assertFalse(check_stale_refs(decisions=[_cite(epoch=before)],
                                          current_epoch=backend.index_epoch()).refused)


# ================================================================ S3 · a retired-status item
class TestS3RetiredItemNeverFires(unittest.TestCase):
    """Retirement is K2's signal. STALE-REF must not grow a second opinion about supersession —
    and it structurally cannot, because it compares epochs and never reads an item."""

    def test_retired_item_same_epoch_allowed(self):
        backend, _shim = _pg()
        backend.put(_item("retry", "old policy", "m1", status=SUPERSEDED))
        # the item is retired; the epoch the citation was minted at is unchanged → NOT stale.
        self.assertFalse(check_stale_refs(decisions=[_cite(epoch="1.1.1")],
                                          current_epoch="1.1.1").refused)

    def test_gate_code_never_names_status(self):
        import mokata.govern.stale_ref_gate as mod
        names = _identifiers(mod)
        for concept in ("status", "superseded", "retired", "supersedes", "lifecycle", "healing"):
            self.assertNotIn(concept, names,
                             f"the stale-ref gate must not reason about '{concept}' — that is K2")


# ================================================================ S4 · loud, never silent-correct
class TestS4LoudNeverSilentCorrect(unittest.TestCase):

    def test_refusal_names_its_fix(self):
        out = check_stale_refs(decisions=[_cite(epoch="1.1.1")], current_epoch="1.2.1")
        msg = out.render()
        self.assertIn("REFUSED", msg)
        self.assertIn("prior-art", msg.lower())
        self.assertTrue(out.reason)

    def test_check_never_auto_refreshes(self):
        cite = _cite(epoch="1.1.1")
        decisions = [cite]
        check_stale_refs(decisions=decisions, current_epoch="1.2.1")
        self.assertEqual("1.1.1", cite.index_epoch)     # NOT silently re-stamped to current
        self.assertEqual([cite], decisions)             # the list itself is untouched

    def test_approve_refuses_on_stale_citation(self):
        s = _lensed_session()
        s.record_prior_art("a", run_prior_art("a", ["retry"], recall=_recall_one,
                                              index_epoch="1.1.1"))
        gate = brainstorm_stale_ref_gate(s, "a", current_epoch="1.2.1")
        self.assertTrue(gate.refused)
        with self.assertRaises(BrainstormGateError) as ctx:
            s.approve("jas", "a", stale_ref_gate=gate)
        self.assertIn("REFUSED", str(ctx.exception))
        self.assertFalse(s.approved)

    def test_gate_id_is_its_own(self):
        self.assertEqual("stale-ref", GATE_ID)


# ================================================================ S5 · comparison, not a query
class TestS5CompareNotQuery(unittest.TestCase):

    def test_check_takes_no_store(self):
        params = set(inspect.signature(check_stale_refs).parameters)
        for forbidden in ("store", "backend", "conn", "dsn", "memory_store"):
            self.assertNotIn(forbidden, params)

    def test_gate_code_opens_no_read_surface(self):
        import mokata.govern.stale_ref_gate as mod
        names = _identifiers(mod)
        for verb in ("execute", "fetchone", "fetchall", "connect", "cursor", "recall",
                     "memorystore", "backend", "backends", "store"):
            self.assertNotIn(verb, names,
                             f"validation must be a comparison, not a query — found '{verb}'")

    def test_unresolvable_id_still_validates(self):
        # no lookup happens, so an id that exists nowhere is not an error — it is just an id.
        out = check_stale_refs(decisions=[_cite("id-that-exists-nowhere", epoch="1.1.1")],
                               current_epoch="1.1.1")
        self.assertFalse(out.refused)

    def test_is_stale_is_pure(self):
        self.assertEqual({"stamped", "current"}, set(inspect.signature(is_stale).parameters))


# ================================================================ the floor declares OFF, once
class TestFloorDeclaresOffOnce(unittest.TestCase):
    """team-only is STRUCTURAL: `_revision` exists only on the Postgres read paths. The floor gets
    an honest OFF — never an invented stamp that would compare equal to itself forever."""

    def test_sqlite_floor_reads_as_off(self):
        with tempfile.TemporaryDirectory() as d:
            backend = SQLiteBackend(f"{d}/m.db")
            self.addCleanup(backend.close)
            self.assertEqual(INDEX_EPOCH_OFF, read_index_epoch(backend))

    def test_off_is_absence_not_a_constant(self):
        self.assertFalse(INDEX_EPOCH_OFF)
        # an always-equal stamp would make the check pass forever in BOTH directions; OFF must not.
        self.assertFalse(is_stale(INDEX_EPOCH_OFF, "1.2.1"))
        self.assertFalse(is_stale("1.1.1", INDEX_EPOCH_OFF))
        self.assertTrue(is_stale("1.1.1", "1.2.1"))     # ... but ON still fires

    def test_postgres_backend_reads_as_on(self):
        backend, _shim = _pg()
        self.assertNotEqual(INDEX_EPOCH_OFF, read_index_epoch(backend))

    def test_off_declared_in_one_place(self):
        """ONE honest declaration (doc 84:180). If a second module starts deciding what OFF means,
        the floor's behaviour stops being auditable from a single read."""
        owners = sorted(p.name for p in _src_files()
                        if "INDEX_EPOCH_OFF =" in p.read_text(encoding="utf-8"))
        self.assertEqual(["staleness.py"], owners)

    def test_floor_citation_carries_no_stamp(self):
        with tempfile.TemporaryDirectory() as d:
            backend = SQLiteBackend(f"{d}/m.db")
            self.addCleanup(backend.close)
            res = run_prior_art("a", ["retry"], recall=_recall_one,
                                index_epoch=read_index_epoch(backend))
            self.assertTrue(res.decisions)
            for dec in res.decisions:
                self.assertEqual(INDEX_EPOCH_OFF, dec.index_epoch)


# ================================================================ the stamp rides the citation
class TestStampCrossesBoundary(unittest.TestCase):
    """The ONE site that justifies the feature: the citation must survive `to_dict`/`from_dict`
    into brainstorm run state, or there is nothing to compare on the far side."""

    def test_stamp_survives_citation_roundtrip(self):
        cite = _cite(epoch="1.1.1")
        self.assertEqual("1.1.1", RelatedDecision.from_dict(cite.to_dict()).index_epoch)

    def test_stamp_survives_session_roundtrip(self):
        s = _lensed_session()
        s.record_prior_art("a", run_prior_art("a", ["retry"], recall=_recall_one,
                                              index_epoch="1.1.1"))
        restored = BrainstormSession.from_dict(s.to_dict())
        self.assertEqual("1.1.1", restored.prior_art["a"].decisions[0].index_epoch)
        # and the restored citation is what the gate then judges — the boundary crossing is real.
        self.assertTrue(brainstorm_stale_ref_gate(restored, "a", current_epoch="1.2.1").refused)

    def test_only_recall_citations_stamped(self):
        """A `decisions[]`-sourced citation comes from the AP-SD hook, not from the store, so it
        crosses no store boundary and gets no stamp."""
        res = run_prior_art("a", ["retry"], recall=_recall_one, index_epoch="1.1.1",
                            decisions=[{"id": "d9", "subject": "retry", "about_code": ["retry"]}])
        by_source = {d.source: d for d in res.decisions}
        self.assertEqual("1.1.1", by_source["recall"].index_epoch)
        self.assertEqual(INDEX_EPOCH_OFF, by_source["decisions[]"].index_epoch)


# ================================================================ the MCP surface stays un-stamped
class TestMcpStaysUnstamped(unittest.TestCase):
    """DELIBERATE, and the reason is kept in doc 84:180: the `recall` response carries no ids and
    its store dies with the call, so a stamp could only be an always-passing check or a public
    response-shape change. This test fails the day someone stamps it by reflex."""

    def test_recall_gains_no_stamp_and_no_ids(self):
        import mokata.mcp.tools_read as mod
        src = inspect.getsource(mod.recall)
        self.assertNotIn("index_epoch", src)
        self.assertNotIn('"id"', src)


# ================================================================ the H-6 boundary
class TestH6BoundaryHolds(unittest.TestCase):
    """The CODE-ANCHOR half is H-6's (doc 02 decision #3). This stage must not reach for it."""

    def test_new_modules_skip_the_bridge(self):
        import mokata.govern.stale_ref_gate as gate_mod
        import mokata.memory.staleness as stale_mod
        for mod in (gate_mod, stale_mod):
            self.assertNotIn("fingerprint_forces_refresh", inspect.getsource(mod))

    def test_the_bridge_has_an_enumerated_caller_list(self):
        """Was `test_dormant_bridge_still_dormant` — CONVERTED, NOT DELETED, at H-6 S1 (2026-08-01).

        DB.S7c2 asserted `== ["freshness.py"]`: declared there, wired nowhere. H-6 is the stage that
        ends that dormancy, so the assertion cannot survive verbatim — but the thing it was
        protecting can, and does. It becomes an ALLOW-LIST: the pre-named tripwire keeps ONE
        definition and exactly the callers H-6's plan of record names (P1), so the day a fifth
        module reaches for it the suite still says so.

        (The plan of record predicted this conversion at S2, the wake. It lands at S1 instead,
        because S1 is where the second caller actually appears — the verdict function IS the
        comparison, and `_reconcile` consumes its output.)

        The `govern`-side half of this boundary is UNTOUCHED and still asserts absence: STALE-REF's
        code-anchor refusal consumes verdicts, never the raw predicate.
        """
        callers = sorted(p.name for p in _src_files()
                         if "fingerprint_forces_refresh(" in p.read_text(encoding="utf-8"))
        self.assertEqual(["anchor_fingerprints.py", "freshness.py"], callers)


# ================================================================ the consumer only gains a check
class TestConsumerOnlyGainsCheck(unittest.TestCase):

    def test_no_gate_is_old_behaviour(self):
        s = _lensed_session()
        s.record_prior_art("a", run_prior_art("a", ["retry"], recall=_recall_one))
        s.approve("jas", "a")
        self.assertTrue(s.approved)

    def test_fresh_gate_lets_approval_proceed(self):
        s = _lensed_session()
        s.record_prior_art("a", run_prior_art("a", ["retry"], recall=_recall_one,
                                              index_epoch="1.1.1"))
        gate = brainstorm_stale_ref_gate(s, "a", current_epoch="1.1.1")
        s.approve("jas", "a", stale_ref_gate=gate)
        self.assertTrue(s.approved)

    def test_no_prior_art_is_not_refused_here(self):
        # STALE-REF judges CITATIONS. "the step never ran" is GR-PA's refusal, not a duplicate here.
        s = _lensed_session()
        self.assertFalse(brainstorm_stale_ref_gate(s, "a", current_epoch="1.2.1").refused)

    def test_outcome_follows_doc_85_shape(self):
        out = check_stale_refs(decisions=[], current_epoch="1.1.1")
        self.assertIsInstance(out, StaleRefOutcome)
        self.assertTrue(hasattr(out, "allowed") and hasattr(out, "render"))


if __name__ == "__main__":
    unittest.main()
