"""M-1/R9 (S1) — approval stamping against a REAL Postgres.

The unit suite proves the semantics against SQLite. Three of this slice's claims are not provable
there, and each of them fails SILENTLY rather than loudly:

  * **the stamp has to survive the REAL column/JSON round trip.** The item doc goes out through
    psycopg into a `TEXT`/`jsonb` column and comes back parsed; `approval_ledger_id` is an int on
    the way out and has to still be an int (not a string, not a float) on the way back, alongside
    the TIMESTAMPTZ-adjacent `approved_at` string. SQLite is permissive about exactly the coercions
    Postgres is not, so a store that round-trips locally can still land a doc nobody can join on.

  * **a FLUSHED item's stamp has to agree with its `team_flush` ledger record.** In team mode the
    write is journal-first: the item is stamped in the commit closure, the journal entry captures
    the same id, and the deferred flush re-records it (`_record_flushed`). Those are three separate
    carriers of one number, and only a real flush against a real server exercises the path where
    they could disagree.

  * **the edge backfill is a statement about a POPULATED store.** "Inherits where the item carries
    a stamp, stays NULL where it does not, and never overwrites a live-projected id" is a property
    of an `UPDATE … FROM` running against real rows and real `jsonb_typeof`, with a real BIGINT
    column refusing anything the guard lets through. A `_PgShim` executing this on SQLite would be
    testing the shim (doc 84 §1 SHIM-FALSE-GREEN), so it is not used here.

Gate is the same explicit contract as every other live-DB leg: MOKATA_LIVE_DB=1 + MOKATA_PG_DSN +
psycopg + a reachable DB, else these skip cleanly.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from test_db_s6_live_db import (_PG_LIVE, _PG_REASON, _PROJECT, _LivePgCase,  # noqa: F401
                                _flush, _pg_dsn, _store, _writer)
from test_db_s7a_live_db import _conn, _edge_rows, _plain_item, _provision_v4, _with_edges

WHO = "ada"


def _doc(dsn, rid):
    """The item doc AS POSTGRES HANDED IT BACK — the point of this file."""
    from mokata import teamdb
    got = _conn(dsn).execute(
        f"SELECT doc FROM {teamdb.MEMORY_TABLE} WHERE id=%s", (rid,)).fetchone()
    if got is None:
        return None
    return got[0] if isinstance(got[0], dict) else json.loads(got[0])


def _seed_stamped(dsn, rid, *, approval_id, supersedes=(), depends_on=()):
    """An item on the v4 store carrying a stamp — i.e. one written by a post-M-1/R9 build, which is
    the case the edge backfill exists to serve."""
    from mokata import teamdb
    item = _with_edges(rid, "v", supersedes=supersedes, depends_on=depends_on)
    item.approved_by = WHO
    item.approved_at = "2026-07-31T09:00:00+00:00"
    item.approval_ledger_id = approval_id
    _conn(dsn).execute(
        f"INSERT INTO {teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project) "
        f"VALUES (%s,%s,%s,%s,%s,%s)",
        (item.id, item.mtype, item.subject, item.status, json.dumps(item.to_doc()), _PROJECT))
    return item


def _seed_unstamped(dsn, rid, *, supersedes=(), depends_on=()):
    """A pre-M-1/R9 item: the approval keys are absent entirely, as they are on every doc written
    before this stage. Not empty-valued — ABSENT, which is the shape actually on disk out there."""
    from mokata import teamdb
    item = _with_edges(rid, "v", supersedes=supersedes, depends_on=depends_on)
    doc = item.to_doc()
    for key in ("approved_by", "approved_at", "approval_ledger_id"):
        doc.pop(key, None)
    _conn(dsn).execute(
        f"INSERT INTO {teamdb.MEMORY_TABLE} (id, mtype, subject, status, doc, project) "
        f"VALUES (%s,%s,%s,%s,%s,%s)",
        (item.id, item.mtype, item.subject, item.status, json.dumps(doc), _PROJECT))
    return item


# ======================================================================================
# the stamp on the wire
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class TheStampSurvivesTheRealRoundTrip(_LivePgCase):

    def test_a_gated_team_write_lands_its_consent_chain_in_postgres(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)
            store = _store(surface)
            store.identity = WHO
            from mokata.memory.item import DECISION, MemoryItem

            item = MemoryItem.create("rotate keys", "every 90 days", mtype=DECISION)
            self.assertTrue(store.remember(item, assume_yes=True).committed)
            # A healthy gated team write flushes itself (`_best_effort_flush`), so the row is
            # already in Postgres — a second flush would correctly report zero. The claim under
            # test is what LANDED, so it is read off the server rather than off a flush count.
            _flush(surface)

            doc = _doc(self.dsn, item.id)
            self.assertEqual(WHO, doc["approved_by"])
            self.assertTrue(doc["approved_at"])
            # An INT on the way back, not a string and not a float — the coercion Postgres is
            # strict about and SQLite is not. A stamp that returns as "12" joins to nothing.
            self.assertIsInstance(doc["approval_ledger_id"], int)
            self.assertNotIsInstance(doc["approval_ledger_id"], bool)
            self.assertGreater(doc["approval_ledger_id"], 0)

    def test_the_flushed_items_stamp_matches_its_team_flush_ledger_record(self):
        """Three carriers of one number: the item's stamp, the journal entry's `ledger_id`, and the
        `team_flush` audit record the deferred flush writes. They agree, or the audit trail from a
        durable row back to the human decision that licensed it is broken at the seam where team
        mode defers durability."""
        with tempfile.TemporaryDirectory() as d:
            surface = _writer(d, self.dsn)
            store = _store(surface)
            store.identity = WHO
            from mokata.memory.item import DECISION, MemoryItem

            item = MemoryItem.create("db", "postgres", mtype=DECISION)
            self.assertTrue(store.remember(item, assume_yes=True).committed)
            _flush(surface)                       # already flushed by the write; see above

            stamped = _doc(self.dsn, item.id)["approval_ledger_id"]
            self.assertIsNotNone(stamped)
            flushes = [e for e in store._ledger.entries() if e.get("kind") == "team_flush"]
            self.assertEqual(1, len(flushes))
            self.assertEqual(stamped, flushes[-1]["approval_ledger_id"])

            approved = [e for e in store._ledger.entries()
                        if e.get("kind") == "write_gate" and e.get("decision") == "approved"]
            self.assertEqual(approved[-1]["seq"], stamped)


# ======================================================================================
# the edge backfill — M-1/R9 owns the NULL DB.S7a had to leave
# ======================================================================================
@unittest.skipUnless(_PG_LIVE, _PG_REASON)
class MigratedEdgesInheritTheirItemsApproval(unittest.TestCase):
    """DB.S7a migrated the three implicit doc-JSON edge kinds and could only stamp them
    `NULL::bigint`, because the item carried no id to inherit — its own comment named this stage as
    the one that would close it. These run the REAL migration over a REAL populated v4 store."""

    def setUp(self):
        from mokata import teamdb
        self.dsn = _pg_dsn()
        _provision_v4(self.dsn)
        _conn(self.dsn).execute(f"DELETE FROM {teamdb.MEMORY_TABLE}")

    def tearDown(self):
        from mokata import teamdb
        from mokata.memory import _pg
        teamdb.provision(self.dsn)
        _pg.reset_manager()

    def _migrate(self):
        from mokata import teamdb
        teamdb.provision(self.dsn)

    def test_a_migrated_edge_inherits_the_stamp_its_item_carries(self):
        target = _seed_stamped(self.dsn, "target", approval_id=41)
        _seed_stamped(self.dsn, "src", approval_id=42, supersedes=[target.id])
        self._migrate()

        rows = _edge_rows(self.dsn, "src")
        self.assertEqual(1, len(rows))
        self.assertEqual(42, rows[0][5])          # the SRC item's approval, not the target's

    def test_an_unstamped_items_edges_stay_null(self):
        """"We do not know" stays "we do not know". A pre-M-1/R9 item is NOT retro-approved by the
        migration, and nothing here invents an id to make the column look complete."""
        target = _seed_unstamped(self.dsn, "target")
        _seed_unstamped(self.dsn, "src", supersedes=[target.id])
        self._migrate()

        rows = _edge_rows(self.dsn, "src")
        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0][5])

    def test_one_store_with_both_kinds_of_item_gets_both_answers(self):
        """The mixed store is the real one: some items written before this stage, some after."""
        target = _seed_unstamped(self.dsn, "target")
        _seed_stamped(self.dsn, "new", approval_id=7, depends_on=[target.id])
        _seed_unstamped(self.dsn, "old", depends_on=[target.id])
        self._migrate()

        self.assertEqual(7, _edge_rows(self.dsn, "new")[0][5])
        self.assertIsNone(_edge_rows(self.dsn, "old")[0][5])

    def test_the_backfill_fills_a_null_left_by_an_EARLIER_migration(self):
        """The upgrade path that actually exists: DB.S7a already ran and left NULLs, THEN items got
        stamped. The second provision must fill them — otherwise the ids would only ever appear on
        stores that happened to migrate after this release."""
        target = _seed_unstamped(self.dsn, "target")
        _seed_unstamped(self.dsn, "src", supersedes=[target.id])
        self._migrate()
        self.assertIsNone(_edge_rows(self.dsn, "src")[0][5])

        # The item is stamped later (as a gated write would), and init is re-run.
        from mokata import teamdb
        doc = _doc(self.dsn, "src")
        doc["approved_by"], doc["approval_ledger_id"] = WHO, 99
        _conn(self.dsn).execute(
            f"UPDATE {teamdb.MEMORY_TABLE} SET doc=%s WHERE id=%s", (json.dumps(doc), "src"))
        self._migrate()

        self.assertEqual(99, _edge_rows(self.dsn, "src")[0][5])

    def test_the_backfill_never_overwrites_an_id_already_on_an_edge(self):
        """A live-projected edge carries the id the FLUSH gave it (`_project_edges_for`). The
        backfill only ever writes into a NULL, so a derived id can never displace a real one — even
        when the item's own stamp says something different."""
        from mokata import teamdb
        from mokata.memory import edges as E
        target = _seed_stamped(self.dsn, "target", approval_id=1)
        _seed_stamped(self.dsn, "src", approval_id=42, supersedes=[target.id])
        self._migrate()
        _conn(self.dsn).execute(
            f"UPDATE {teamdb.EDGES_TABLE} SET {E.APPROVAL_LEDGER_COLUMN}=500 "
            f"WHERE {E.SRC_COLUMN}=%s", ("src",))

        self._migrate()
        self.assertEqual(500, _edge_rows(self.dsn, "src")[0][5])

    def test_re_running_the_migration_changes_nothing(self):
        target = _seed_stamped(self.dsn, "target", approval_id=1)
        _seed_stamped(self.dsn, "src", approval_id=42, supersedes=[target.id])
        self._migrate()
        before = _edge_rows(self.dsn)
        self._migrate()
        self._migrate()
        self.assertEqual(before, _edge_rows(self.dsn))

    def test_an_unjoinable_id_on_a_doc_yields_null_rather_than_failing_the_pass(self):
        """A hand-edited or imported doc can carry anything. A JSON boolean, a float, a string and
        the journal's `"floor-recovery"` sentinel must all read as NO id — and crucially must not
        raise mid-provision: a `'1.5'::bigint` cast would fail the whole `team init`."""
        from mokata import teamdb
        target = _seed_unstamped(self.dsn, "target")
        for i, hostile in enumerate([True, 1.5, -3, "floor-recovery", "12", None]):
            rid = f"h{i}"
            _seed_unstamped(self.dsn, rid, supersedes=[target.id])
            doc = _doc(self.dsn, rid)
            doc["approval_ledger_id"] = hostile
            _conn(self.dsn).execute(
                f"UPDATE {teamdb.MEMORY_TABLE} SET doc=%s WHERE id=%s", (json.dumps(doc), rid))

        self._migrate()                                  # must not raise

        for i in range(6):
            rows = _edge_rows(self.dsn, f"h{i}")
            self.assertEqual(1, len(rows))
            self.assertIsNone(rows[0][5], f"h{i}")


if __name__ == "__main__":
    unittest.main()
