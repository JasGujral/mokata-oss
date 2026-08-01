"""M-1/R9 (S1) — approval stamping: the item carries its own consent chain.

Doc 52 M-1: provenance gains `approved_by`, `approved_at` and `approval_ledger_id` — "the item
carries its own consent chain". The `author` half already shipped (`store._stamp_author`); this is
the approval half, and it answers a different question. `author` is who WROTE the item; the stamp
is who let it LAND. On a poisoned proposal those are two different people, which is the whole of
R9's anti-rubber-stamp case (doc 83: "a poisoned proposal a human rubber-stamps still lands").

**The ledger is authoritative; the item copy is a PROJECTION.** The consent chain already exists in
the hash-chained audit ledger, and these three fields do not create a second source of truth for
who approved what: `approval_ledger_id` is a join key back to the gate's own `write_gate`/approved
entry, and `approved_by`/`approved_at` are denormalised so a render (or an export, or a teammate
with no access to our ledger file) can show the chain without a lookup. On disagreement the ledger
wins. Same call DB.S7a made for edge rows: "a column, not an inference".

The contracts pinned here, each with the mutation it exists to catch:

  1. The stamp rides the EXISTING gated write and no other path — an approved write does exactly
     ONE backend put, a declined one does zero and leaves no stamp. Pinned behaviourally with a
     counting backend, deliberately: `TestZeroBypass` (SI.6) is a static AST scan over a fixed
     vocabulary of write names and is blind to a DELEGATED write (filed 🔴, doc 84 §1
     SI.6-DELEGATED-BLINDNESS), so "no second ungated path" has to be observed, not grepped.
     RED when the stamp moves out of `_commit` to a post-`outcome.committed` write-back.
  2. The stamped id IS the approval's real seq — `item.approval_ledger_id == outcome.approval_seq
     == ledger.entries()[-1]["seq"]`, against a PRE-LOADED ledger so a naive 0/1 passes nothing,
     and recomputed per write so a foreign append between two writes moves the next stamp.
     RED when the prediction is cached, hoisted out of the hold, or replaced by a constant.
     (The CROSS-PROCESS half of B2 — that no other writer can append inside the hold — is pinned
     where it belongs, `test_ms_s3_ledger_chain.py::test_two_processes_append_concurrently` and
     `::test_predicted_id_equals_own_entry_under_commit`. Not duplicated here.)
  3. ONE identity source. `approved_by` is `self.identity` (`team_audit.actor()`), the SAME source
     `_stamp_author` uses. RED when it is read off `WriteRequest.actor`, which is the literal
     string "memory" — the subsystem, not a person.
  4. LOCAL mode stamps too. The id used to be computed on the `if team:` branch only, so a
     local-mode item had nothing to stamp. RED when that branch returns.
  5. Empty is UNSET and never forged. A pre-M-1/R9 item stays unstamped, round-trips as a fixed
     point, and behaves identically in recall/ranking/precedence/render (the DB.S5 degrade shape —
     NOT literal byte-identity: `to_dict` emits every modelled field, so a legacy doc gains three
     empty keys on its first write-back exactly as it gained `valid_from`/`valid_to`).
     RED when the fields default to a sentinel, or an approver is inferred for a legacy item.
  6. The edge backfill (M-1/R9 owns it — DB.S7a's `NULL::bigint` names this stage): a migrated
     edge INHERITS its item's stamp where the item carries one, and stays NULL where it does not.
     RED when the backfill invents an id, or overwrites an id a live-projected edge already has.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern.ledger import AuditLedger
from mokata.memory import (
    ACTIVE, DECISION, DOC_KEYS, MemoryItem, MemoryStore, approval_ledger_id_of,
)
from mokata.memory.backends import SQLiteBackend

APPROVAL_KEYS = ("approved_by", "approved_at", "approval_ledger_id")

# The identity a store built from a surface would carry (`team_audit.actor()`), and the string the
# gate puts on its OWN WriteRequest. They are different on purpose: pin 3 is that the stamp names
# the person, never the subsystem.
WHO = "ada"
GATE_REQUEST_ACTOR = "memory"


class _CountingBackend(SQLiteBackend):
    """A real SQLite backend that COUNTS its mutations. Real, not a mock: pin 1 is about how many
    times the durable write path runs, and a mock would let a second write pass unnoticed if it
    went through a name the mock did not model."""

    def __init__(self, path):
        super().__init__(path)
        self.puts = 0
        self.updates = 0
        self.deletes = 0

    def put(self, item):
        self.puts += 1
        return super().put(item)

    def update(self, item):
        self.updates += 1
        return super().update(item)

    def delete(self, item_id):
        self.deletes += 1
        return super().delete(item_id)

    @property
    def mutations(self):
        return self.puts + self.updates + self.deletes


class _Store:
    """A store over a real SQLite file with a REAL AuditLedger, so "the stamped id is the seq the
    ledger assigned" is a fact about an appended, hash-chained entry rather than about a stub."""

    def __init__(self, identity=WHO, preload=0):
        self.identity = identity
        self.preload = preload

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "memory.db")
        self.ledger = AuditLedger(os.path.join(self.tmp.name, "audit", "ledger.jsonl"))
        # Entries that already exist when the gated write happens. Without these a stamp of 0, 1 or
        # `None` would pass pin 2 by accident.
        for i in range(self.preload):
            self.ledger.record("unrelated", note=f"pre-existing #{i}")
        self.backend = _CountingBackend(self.path)
        self.store = MemoryStore(self.backend, enabled_types=(DECISION, "persistent", "episodic"),
                                 ledger=self.ledger, identity=self.identity)
        return self

    def __exit__(self, *exc):
        self.backend.close()
        self.tmp.cleanup()

    def approved_entries(self):
        return [e for e in self.ledger.entries()
                if e.get("kind") == "write_gate" and e.get("decision") == "approved"]

    def raw_doc(self, item_id):
        """The doc AS IT LANDED ON DISK — the stamp has to survive serialisation, not just live on
        the in-memory object the caller still holds."""
        with self.backend._connect() as conn:
            row = conn.execute("SELECT doc FROM memory WHERE id = ?", (item_id,)).fetchone()
        return json.loads(row[0]) if row else None


# ======================================================================== pin 1 — one gated write
class StampRidesTheGatedWrite(unittest.TestCase):

    def test_an_approved_write_makes_exactly_one_backend_mutation(self):
        """The stamp rides the write that was already happening — it does not add a second one.

        RED when the stamp is applied after `outcome.committed` (a post-gate write-back): the item
        would be put once by the gate's commit closure and once more to persist the stamp, and the
        second one answers to nothing — no secret scan, no self-protect layer, no ledger entry.
        """
        with _Store() as s:
            item = MemoryItem.create("rotate keys", "every 90 days", mtype=DECISION)
            res = s.store.remember(item, assume_yes=True)
            self.assertTrue(res.committed)
            self.assertEqual(1, s.backend.mutations)
            self.assertEqual(1, s.backend.puts)

    def test_the_stamp_is_on_the_doc_that_landed_on_disk(self):
        with _Store(preload=4) as s:
            item = MemoryItem.create("rotate keys", "every 90 days", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            doc = s.raw_doc(item.id)
            self.assertEqual(WHO, doc["approved_by"])
            self.assertTrue(doc["approved_at"])
            self.assertEqual(s.approved_entries()[-1]["seq"], doc["approval_ledger_id"])

    def test_a_declined_write_stamps_nothing_and_writes_nothing(self):
        """The gate's refusal is the stamp's refusal. RED for any stamping path that runs before
        or outside the human gate — the item object itself must come back clean, because a caller
        holding a declined item must not be able to re-submit it wearing an approval."""
        with _Store() as s:
            item = MemoryItem.create("rotate keys", "every 90 days", mtype=DECISION)
            res = s.store.remember(item, confirm=lambda _text: False)
            self.assertFalse(res.committed)
            self.assertEqual(0, s.backend.mutations)
            self.assertEqual([], s.store.all_active(DECISION))
            self.assertEqual("", item.approved_by)
            self.assertEqual("", item.approved_at)
            self.assertIsNone(item.approval_ledger_id)
            self.assertEqual([], s.approved_entries())

    def test_every_item_one_approval_touches_carries_that_same_approval(self):
        """A supersede writes TWO items under ONE human decision. Both name it — and name the same
        id, matching the journal's own notion of an approval group (`_approval_key`) rather than
        inventing a per-item one. RED when the id is resolved per call site instead of per gate."""
        with _Store(preload=2) as s:
            base = MemoryItem.create("db", "postgres", mtype=DECISION)
            s.store.remember(base, assume_yes=True)
            first_seq = s.raw_doc(base.id)["approval_ledger_id"]

            newer = MemoryItem.create("db", "postgres 16", mtype=DECISION,
                                      supersedes=[base.id])
            s.store.remember(newer, assume_yes=True)
            second_seq = s.raw_doc(newer.id)["approval_ledger_id"]

            self.assertNotEqual(first_seq, second_seq)   # a NEW decision gets a NEW id
            self.assertEqual(s.approved_entries()[-1]["seq"], second_seq)


# ================================================================= pin 2 — the id is the real seq
class TheStampedIdIsTheApprovalsRealSeq(unittest.TestCase):

    def test_stamp_equals_outcome_seq_equals_the_ledger_entry(self):
        """The three-way equality that makes the projection a projection. Pre-loaded ledger so the
        value under test is a real seq (>4), not a default that happens to line up."""
        with _Store(preload=7) as s:
            item = MemoryItem.create("rotate keys", "every 90 days", mtype=DECISION)
            s.store.remember(item, assume_yes=True)

            approved = s.approved_entries()
            self.assertEqual(1, len(approved))
            stamped = s.raw_doc(item.id)["approval_ledger_id"]
            self.assertEqual(approved[-1]["seq"], stamped)
            self.assertGreater(stamped, 7)          # it tracked the pre-loaded entries

    def test_a_foreign_append_between_writes_moves_the_next_stamp(self):
        """The prediction is recomputed per gated write, never cached or derived from a counter the
        store keeps itself. RED when `_pending_approval_seq` is hoisted, memoised, or replaced by
        an incrementing field on the store."""
        with _Store(preload=1) as s:
            first = MemoryItem.create("a", "1", mtype=DECISION)
            s.store.remember(first, assume_yes=True)
            first_stamp = s.raw_doc(first.id)["approval_ledger_id"]

            for i in range(5):                       # somebody else appends, loudly and legally
                s.ledger.record("unrelated", note=f"between #{i}")

            second = MemoryItem.create("b", "2", mtype=DECISION)
            s.store.remember(second, assume_yes=True)
            second_stamp = s.raw_doc(second.id)["approval_ledger_id"]

            self.assertEqual(second_stamp, first_stamp + 6)
            self.assertEqual(s.approved_entries()[-1]["seq"], second_stamp)

    def test_no_ledger_means_no_id_and_still_a_clean_write(self):
        """Degrade: a store with no ledger cannot name an approval, so it names none — and the
        write still lands. RED when the absence is papered over with a 0 or a 1."""
        with tempfile.TemporaryDirectory() as tmp:
            backend = _CountingBackend(os.path.join(tmp, "m.db"))
            try:
                store = MemoryStore(backend, enabled_types=(DECISION,), identity=WHO)
                item = MemoryItem.create("k", "v", mtype=DECISION)
                self.assertTrue(store.remember(item, assume_yes=True).committed)
                self.assertIsNone(item.approval_ledger_id)
                self.assertEqual(WHO, item.approved_by)   # who still known; only the id is not
            finally:
                backend.close()


# ==================================================================== pin 3 — one identity source
class OneIdentitySourceForWhoApproved(unittest.TestCase):

    def test_approved_by_is_the_run_identity_not_the_gate_request_actor(self):
        """RED when `approved_by` is read off the gate's `WriteRequest.actor`, which memory sets to
        the literal "memory" — a subsystem name in a field that claims to name a human."""
        with _Store() as s:
            item = MemoryItem.create("k", "v", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            doc = s.raw_doc(item.id)
            self.assertEqual(WHO, doc["approved_by"])
            self.assertNotEqual(GATE_REQUEST_ACTOR, doc["approved_by"])

    def test_the_approver_and_the_author_come_from_the_same_source(self):
        """One notion of "who". `_stamp_author` and `_stamp_approval` both read `self.identity`, so
        a build that changed one and not the other would show two different people for one act."""
        with _Store() as s:
            item = MemoryItem.create("k", "v", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            doc = s.raw_doc(item.id)
            self.assertEqual(doc["provenance"]["author"], doc["approved_by"])

    def test_no_identity_leaves_the_approver_empty_rather_than_guessed(self):
        """A directly-constructed store has no identity. Unknown stays unknown — the id still
        lands, because THAT one is knowable. RED for any fallback to "user"/"unknown"/getpass."""
        with _Store(identity=None, preload=3) as s:
            item = MemoryItem.create("k", "v", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            doc = s.raw_doc(item.id)
            self.assertEqual("", doc["approved_by"])
            self.assertEqual(s.approved_entries()[-1]["seq"], doc["approval_ledger_id"])


# ======================================================================= pin 4 — local mode stamps
class LocalModeCarriesItsConsentChainToo(unittest.TestCase):

    def test_a_local_store_stamps_the_full_chain(self):
        """The id was computed on the `if team:` branch only, so local mode had nothing to stamp —
        approval provenance would have existed for teams and silently not for everyone else. Every
        store in this file is local, and this is the pin that says so on purpose."""
        with _Store(preload=2) as s:
            self.assertFalse(s.store._team_mode())
            item = MemoryItem.create("k", "v", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            doc = s.raw_doc(item.id)
            self.assertEqual(WHO, doc["approved_by"])
            self.assertTrue(doc["approved_at"])
            self.assertIsNotNone(doc["approval_ledger_id"])

    def test_a_second_gated_decision_on_one_item_restamps_it(self):
        """The stamp names the decision that licensed the item as it stands NOW. An item whose
        scope moved under approval #9 must not still claim approval #4 — that is the "approved
        once, changed later" hole R9 exists to close. A scope promotion is used because it is a
        real second gated decision on an existing item, taking the `_OP_UPDATE` fork.

        RED when the stamp only fills an empty field (the `_stamp_author` placeholder rule — right
        for `author`, which does not change hands on an edit, and wrong for an approval)."""
        with _Store(preload=1) as s:
            item = MemoryItem.create("db", "postgres", mtype=DECISION)
            s.store.remember(item, assume_yes=True)
            first = s.raw_doc(item.id)["approval_ledger_id"]

            promoted = s.store.promote_scope(item.id, "project", assume_yes=True)
            self.assertTrue(promoted.changed, promoted.message)
            second = s.raw_doc(item.id)["approval_ledger_id"]
            self.assertGreater(second, first)
            self.assertEqual(s.approved_entries()[-1]["seq"], second)


# ================================================================== pin 5 — empty is unset, always
class UnstampedItemsDegradeCleanly(unittest.TestCase):

    def test_a_legacy_doc_parses_as_unstamped(self):
        """No keys → no approval. RED when the fields are inferred from `created_at` / the current
        actor on the parse path."""
        doc = MemoryItem.create("legacy", "written before M-1/R9").to_dict()
        for key in APPROVAL_KEYS:
            doc.pop(key)
        item = MemoryItem.from_dict(doc)
        self.assertEqual("", item.approved_by)
        self.assertEqual("", item.approved_at)
        self.assertIsNone(item.approval_ledger_id)

    def test_an_item_that_has_never_been_gated_carries_no_approval(self):
        """A CONSTRUCTED item is not an approved one. `create()` takes no approval argument and the
        dataclass defaults to unset, so the only way to acquire a stamp is to be approved — there
        is no path that mints one.

        This is the pin that exercises the DEFAULTS (`from_dict` passes all three explicitly, so it
        never reaches them). RED when a field defaults to a sentinel like "user": every item in the
        process would then claim an approver it never had, including one the gate declined.
        """
        for item in (MemoryItem.create("s", "v"), MemoryItem(subject="s", value="v")):
            self.assertEqual("", item.approved_by)
            self.assertEqual("", item.approved_at)
            self.assertIsNone(item.approval_ledger_id)
        self.assertNotIn("approved_by", MemoryItem.create.__code__.co_varnames)

    def test_an_unstamped_doc_is_a_round_trip_fixed_POINT(self):
        """parse → emit → parse is stable. The doc GAINS the three empty keys on its first
        write-back (every modelled field is emitted always — the DB.S5 `valid_from`/`valid_to`
        shape, deliberately not literal byte-identity), and never gains anything after that."""
        doc = MemoryItem.create("legacy", "v").to_dict()
        for key in APPROVAL_KEYS:
            doc.pop(key)
        once = MemoryItem.from_dict(doc).to_dict()
        twice = MemoryItem.from_dict(once).to_dict()
        self.assertEqual(once, twice)
        self.assertEqual({"approved_by": "", "approved_at": "", "approval_ledger_id": None},
                         {k: once[k] for k in APPROVAL_KEYS})

    def test_the_keys_are_modelled_not_unknown_siblings(self):
        """In DOC_KEYS, so D6 treats them as fields this build owns rather than carrying them on
        `extra` — and so the D6 pin keeps failing loudly for the NEXT un-declared field."""
        for key in APPROVAL_KEYS:
            self.assertIn(key, DOC_KEYS)
        self.assertEqual({}, MemoryItem.from_dict(MemoryItem.create("s", "v").to_dict()).extra)

    def test_an_unjoinable_id_reads_as_no_id(self):
        """A hand-edited or hostile doc cannot mint a joinable-looking approval. `True` is excluded
        because `True == 1` in Python would fold a flag into approval #1 — the same exclusion
        `team_journal._approval_key` makes about the same value."""
        for hostile in (True, False, "floor-recovery", "12", 1.5, None, [], {}):
            self.assertIsNone(approval_ledger_id_of(hostile), hostile)
        self.assertEqual(12, approval_ledger_id_of(12))

    def test_a_hostile_id_on_a_doc_is_dropped_on_parse(self):
        doc = MemoryItem.create("s", "v").to_dict()
        doc["approval_ledger_id"] = "floor-recovery"
        self.assertIsNone(MemoryItem.from_dict(doc).approval_ledger_id)

    def test_recall_and_ranking_are_unchanged_for_unstamped_items(self):
        """Behavioural identity — the half of the degrade contract that actually protects the
        corpus. An unstamped item recalls, ranks and orders exactly as it did before M-1/R9."""
        with _Store() as s:
            for n, (subject, value) in enumerate([("auth", "use oauth"), ("db", "postgres"),
                                                  ("auth deploy", "rotate keys")]):
                item = MemoryItem.create(subject, value, mtype=DECISION)
                s.store.remember(item, assume_yes=True)
            stamped = [i.subject for i in s.store.recall("auth", mtype=DECISION)]

            # The same corpus, with every stamp stripped back off on disk.
            with s.backend._connect() as conn:
                for row in conn.execute("SELECT id, doc FROM memory").fetchall():
                    doc = json.loads(row[1])
                    for key in APPROVAL_KEYS:
                        doc.pop(key, None)
                    conn.execute("UPDATE memory SET doc = ? WHERE id = ?",
                                 (json.dumps(doc), row[0]))
                conn.commit()
            unstamped = [i.subject for i in s.store.recall("auth", mtype=DECISION)]

            self.assertEqual(stamped, unstamped)
            self.assertTrue(stamped)
            self.assertTrue(all(i.status == ACTIVE for i in s.store.all_active(DECISION)))


# =============================================================== pin 6 — the migrated-edge backfill
class MigratedEdgesInheritTheirItemsApproval(unittest.TestCase):
    """DB.S7a stamped migrated edges `NULL::bigint` because the item carried no id to inherit, and
    its own comment named this stage as the thing that would close it. These pin the SQL's SHAPE
    and its guarantees; the behaviour against a real server is in
    `tests/integration/test_m1_r9_live_db.py` (a `_PgShim` cannot vouch for it — doc 84 §1
    SHIM-FALSE-GREEN)."""

    def _sql(self):
        from mokata import teamdb
        return teamdb._edge_approval_backfill_sql()

    def test_the_migration_no_longer_hardcodes_a_null_ledger_id(self):
        from mokata import teamdb
        joined = " ".join(teamdb._edge_backfill_sql())
        self.assertNotIn("NULL::bigint", joined)
        self.assertIn("approval_ledger_id", joined)

    def test_the_backfill_only_ever_fills_a_hole(self):
        """It cannot overwrite the id a live-projected edge already inherited from the flush."""
        self.assertIn("IS NULL", self._sql())
        self.assertIn("approval_ledger_id IS NULL", " ".join(self._sql().split()))

    def test_the_backfill_touches_no_row_it_cannot_fill(self):
        """A row-churn guard, and named as one.

        It was tempting to call this "never invents an id" — it is not. Verified by mutation
        against a live server: deleting this predicate changes NO observable value, because the
        rows it excludes are ones where the update would write NULL over NULL. What it actually
        buys is that a re-run does not touch every unstamped edge in the store (WAL, autovacuum,
        and a rowcount that would misreport how much the pass did).

        The never-invent guarantee lives one layer down, in `_item_absent_id_expr` below — that one
        IS load-bearing, and its mutation fails `team init` outright.
        """
        self.assertIn("src.approval_id IS NOT NULL", " ".join(self._sql().split()))

    def test_the_id_expression_refuses_anything_it_cannot_join_on(self):
        """The SQL twin of `approval_ledger_id_of`, and the actual never-invent guarantee: only a
        positive integral JSON number becomes an id.

        Both guards are load-bearing, confirmed by live mutation. Dropping the integer regex makes
        a doc carrying `1.5` fail `team init` outright — `ProvisionError: invalid input syntax for
        type bigint: "1.5"` — so a single hand-edited or imported doc would take down provisioning
        for the whole team. Fail-closed, but for a value that should simply have read as "no id"."""
        from mokata import teamdb
        expr = " ".join(teamdb._item_approval_id_sql("m").split())
        self.assertIn("jsonb_typeof(m.doc::jsonb->'approval_ledger_id') = 'number'", expr)
        self.assertIn("^[0-9]+$", expr)
        self.assertIn("::bigint", expr)

    def test_the_backfill_runs_after_the_rows_it_fills_are_created(self):
        """Ordering is the safety property: the UPDATE fills rows the INSERTs above it created."""
        from mokata import teamdb
        stmts = teamdb.provision_sql()
        backfill = self._sql()
        self.assertIn(backfill, stmts)
        inserts = [i for i, s in enumerate(stmts)
                   if s in teamdb._edge_backfill_sql()]
        self.assertTrue(inserts)
        self.assertGreater(stmts.index(backfill), max(inserts))

    def test_provisioning_stays_idempotent_in_shape(self):
        """Ordinary DML, no schema — safe to re-run under the role that owns the migration, and a
        second pass updates zero rows because the NULLs it targeted are gone."""
        sql = self._sql().upper()
        for ddl in ("CREATE ", "ALTER ", "DROP "):
            self.assertNotIn(ddl, sql)
        self.assertTrue(sql.startswith("UPDATE "))


if __name__ == "__main__":
    unittest.main()
