"""D6 — memory-doc SCHEMA_VERSION: downgrade-safe.

The bug: memory docs carried no version. `MemoryItem.from_dict` is a key-WHITELIST parse (every
field is a `d.get(...)`) and `to_dict` re-emits only the keys it knows — so a teammate on an OLDER
mokata could read a doc a NEWER mokata wrote, drop every field it had no name for, and write the
doc back. An approved memory silently lost content, gated by nothing (P2/P21). The read alone was
lossless (nothing on disk changed); the WRITE-BACK is where the strip happened, and every mutation
path in the store is a write-back: promote · promote_scope · propose · the review transitions ·
rollback · heal · consolidate (including PRUNE) · remember (a share-import hands it a teammate's
parsed doc) · migrate (read from one backend, write to another).

After D6:
  * every new/updated doc carries `schema_version`; an unstamped legacy doc parses as v1 (the
    FROZEN floor — the shape only ever grew, so an unstamped doc IS a v1 doc) and gains the stamp
    on its first legitimate write;
  * a doc ABOVE MEMORY_DOC_VERSION (or with an unreadable stamp) is READ defensively — every
    modelled field parsed, every unknown field preserved on `item.extra` — announced ONCE
    (`memory-schema`, class `doc-schema`, fix "upgrade mokata"), and REFUSED by every mutation
    path, with the doc byte-identical afterwards. Read-but-never-write, not refuse-entirely:
    refusing on READ would blank a newer teammate's items out of every older teammate's recall
    (the hard split D2 exists to prevent), and reading destroys nothing;
  * within a version, an unknown sibling key survives a round-trip instead of being dropped.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import degrade
from mokata.degrade import FAILURE_DOC_SCHEMA
from mokata.memory import (
    ACTIVE, DECISION, DOC_KEYS, DOC_SUBSYSTEM, DOC_VERSION_KEY, GUARDRAIL, LEGACY_DOC_VERSION,
    MEMORY_DOC_VERSION, RULE, UNREADABLE_DOC_VERSION, MemoryDocTooNew, MemoryItem, MemoryStore,
    can_write_doc, doc_version, downgrade_refusal,
)
from mokata.memory.backends import SQLiteBackend
from mokata.memory.consolidation import MERGE, PRUNE, ConsolidationProposal
from mokata.memory.healing import CONTRADICTION, HealingProposal
from mokata.memory.share import import_memory

# A field a FUTURE mokata adds. This build has no name for it — which is the whole point.
FUTURE_KEY = "confidence"
FUTURE_VALUE = 0.93
NEWER = MEMORY_DOC_VERSION + 1


def _newer_doc(subject="rotate keys", value="every 90 days", **kw):
    """The doc a NEWER mokata wrote: our shape + a bumped stamp + a field we cannot model."""
    doc = MemoryItem.create(subject, value, **kw).to_dict()
    doc[DOC_VERSION_KEY] = NEWER
    doc[FUTURE_KEY] = FUTURE_VALUE
    return doc


def _legacy_doc(subject="legacy fact", value="written before D6", **kw):
    """A doc written by a PRE-D6 mokata: today's shape, with no stamp at all."""
    doc = MemoryItem.create(subject, value, **kw).to_dict()
    doc.pop(DOC_VERSION_KEY)
    return doc


class _Store:
    """A store over a real SQLite file, so 'the doc on disk is byte-identical' is a fact about
    bytes, not about a mock. Writes are pre-approved (`assume_yes`) so that a refusal can only
    come from D6 — never from an unanswered gate prompt."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "memory.db")
        self.backend = SQLiteBackend(self.path)
        self.store = MemoryStore(self.backend, enabled_types=(DECISION, "persistent", "episodic"))
        return self

    def __exit__(self, *exc):
        self.backend.close()
        self.tmp.cleanup()

    def seed(self, doc):
        """Put a doc into the DB *as raw JSON*, bypassing the write path — this is a doc that got
        there because a NEWER mokata wrote it, which is exactly the scenario. `SQLiteBackend.put`
        would (correctly) refuse it."""
        with self.backend._connect() as conn:
            conn.execute(
                "INSERT INTO memory (id, mtype, subject, status, doc) VALUES (?,?,?,?,?)",
                (doc["id"], doc["mtype"], doc["subject"], doc["status"], json.dumps(doc)))
            conn.commit()
        return doc["id"]

    def raw(self, item_id):
        """The doc bytes as they sit in the DB."""
        with self.backend._connect() as conn:
            row = conn.execute("SELECT doc FROM memory WHERE id=?", (item_id,)).fetchone()
        return row[0] if row else None


class DocVersionModel(unittest.TestCase):
    """The version axis itself: the stamp, the frozen legacy floor, the unreadable-stamp arm."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_new_doc_carries_the_stamp(self):
        doc = MemoryItem.create("s", "v").to_dict()
        self.assertEqual(MEMORY_DOC_VERSION, doc[DOC_VERSION_KEY])

    def test_unstamped_doc_parses_as_the_frozen_legacy_floor(self):
        # D2 discipline: absence is not "unknown", it is PROOF the writer predates the stamp — and
        # every pre-D6 shape is a subset of v1, so v1 is exact, not generous.
        item = MemoryItem.from_dict(_legacy_doc())
        self.assertEqual(LEGACY_DOC_VERSION, item.schema_version)
        self.assertTrue(can_write_doc(item.schema_version))

    def test_doc_version_classifies_each_arm(self):
        self.assertEqual(LEGACY_DOC_VERSION, doc_version({}))                    # unstamped
        self.assertEqual(MEMORY_DOC_VERSION, doc_version({DOC_VERSION_KEY: MEMORY_DOC_VERSION}))
        self.assertEqual(NEWER, doc_version({DOC_VERSION_KEY: NEWER}))           # newer
        for garbage in ("2", 1.5, 0, -1, None, True, [2]):
            # A stamp we cannot read AS a version declares something we cannot classify.
            # Unknown is not permission (D2): it is never writable.
            self.assertEqual(UNREADABLE_DOC_VERSION, doc_version({DOC_VERSION_KEY: garbage}),
                             f"{garbage!r} must not read as a version")
            self.assertFalse(can_write_doc(doc_version({DOC_VERSION_KEY: garbage})))

    def test_can_write_doc_bounds(self):
        self.assertTrue(can_write_doc(MEMORY_DOC_VERSION))
        self.assertTrue(can_write_doc(LEGACY_DOC_VERSION))
        self.assertFalse(can_write_doc(NEWER))
        self.assertFalse(can_write_doc(UNREADABLE_DOC_VERSION))

    def test_doc_keys_is_exactly_what_to_dict_emits(self):
        # The pin that keeps "unknown" honest: add a modelled field without adding it to DOC_KEYS
        # and it would be misfiled as an unknown sibling (parsed into `extra`, never as itself).
        full = MemoryItem.create(
            "s", "v", kind=RULE, enforcement="hard", supersedes=["a"], depends_on=["b"],
            about_code=["x.py"], applicability={"topic": "t"}, review={"state": "draft"},
            scope_level="team", scope_id="core", pin=True, priority=3)
        self.assertEqual(DOC_KEYS, set(full.to_dict().keys()))

    def test_the_doc_axis_is_not_the_db_axis(self):
        # teamdb.TEAM_SCHEMA_VERSION versions the shared DDL; this versions the doc JSON. They move
        # independently — D6 adds NO DDL (the stamp rides the existing `doc` column).
        from mokata import teamdb
        self.assertEqual(1, MEMORY_DOC_VERSION)
        self.assertNotEqual(teamdb.TEAM_SCHEMA_VERSION, MEMORY_DOC_VERSION)


class DefensiveRead(unittest.TestCase):
    """A newer doc READS — losslessly — and says so once."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_newer_doc_is_read_not_refused(self):
        item = MemoryItem.from_dict(_newer_doc())
        self.assertEqual("rotate keys", item.subject)      # the readable core is served
        self.assertEqual("every 90 days", item.value)
        self.assertEqual(NEWER, item.schema_version)       # carrying the truth about its own doc

    def test_read_preserves_the_fields_this_build_cannot_model(self):
        item = MemoryItem.from_dict(_newer_doc())
        self.assertEqual({FUTURE_KEY: FUTURE_VALUE}, item.extra)
        # ... and re-emits them: a read/export of a newer doc is lossless.
        self.assertEqual(FUTURE_VALUE, item.to_dict()[FUTURE_KEY])
        self.assertEqual(NEWER, item.to_dict()[DOC_VERSION_KEY])   # never re-stamped DOWN to ours

    def test_the_notice_is_named_and_classed_once(self):
        degrade.reset_degrade_notices()
        for _ in range(5):                                  # a corpus read, not a single row
            MemoryItem.from_dict(_newer_doc())
        notices = [n for n in degrade.emitted_notices() if n.subsystem == DOC_SUBSYSTEM]
        self.assertEqual(1, len(notices), "the notice must be emitted ONCE, not once per row")
        notice = notices[0]
        self.assertEqual(FAILURE_DOC_SCHEMA, notice.failure_class)
        self.assertIn("pip install -U mokata", notice.remediation)
        rendered = notice.render()
        self.assertIn("NEWER mokata", rendered)
        # The CM.S2 shape, and TRUE in every clause: no shared DB is involved, so it must not
        # claim one is unreachable, unprovisioned, or fixable with `mokata team init`.
        self.assertNotIn("team init", rendered)
        self.assertNotIn("mokata sync", rendered)
        self.assertNotIn("journaled", rendered)


class DurableSerializerRefuses(unittest.TestCase):
    """The backstop: no write sink can serialize a newer doc, whatever calls it."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_to_doc_refuses_a_newer_doc(self):
        item = MemoryItem.from_dict(_newer_doc())
        with self.assertRaises(MemoryDocTooNew) as ctx:
            item.to_doc()
        self.assertEqual(FAILURE_DOC_SCHEMA, ctx.exception.failure_class)

    def test_to_doc_refuses_an_unreadable_stamp(self):
        doc = _newer_doc()
        doc[DOC_VERSION_KEY] = "banana"
        with self.assertRaises(MemoryDocTooNew):
            MemoryItem.from_dict(doc).to_doc()

    def test_to_dict_stays_lossless_so_reads_and_exports_never_raise(self):
        item = MemoryItem.from_dict(_newer_doc())
        self.assertEqual(FUTURE_VALUE, item.to_dict()[FUTURE_KEY])   # must not raise

    def test_backend_put_refuses(self):
        with _Store() as s:
            with self.assertRaises(MemoryDocTooNew):
                s.backend.put(MemoryItem.from_dict(_newer_doc()))


class MutationPathsRefuseLoudly(unittest.TestCase):
    """THE strip regression. For every write-back path: refuse loudly, doc byte-identical after."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def _assert_refused(self, store, item_id, before, message):
        self.assertIn("newer", message.lower())
        self.assertIn("pip install -U mokata", message)
        self.assertEqual(before, store.raw(item_id), "the doc on disk must be BYTE-IDENTICAL")
        # and the field a newer mokata wrote is still there, in the bytes.
        self.assertEqual(FUTURE_VALUE, json.loads(store.raw(item_id))[FUTURE_KEY])

    def test_remember_refuses(self):
        # The share-import shape: `remember` is handed a teammate's parsed doc.
        with _Store() as s:
            item = MemoryItem.from_dict(_newer_doc())
            res = s.store.remember(item, assume_yes=True)
            self.assertFalse(res.committed)
            self.assertTrue(res.refused)
            self.assertFalse(res.blocked)             # not a secret — a version refusal
            self.assertIn("pip install -U mokata", res.message)
            self.assertIsNone(s.raw(item.id), "nothing may be written at all")

    def test_promote_refuses(self):
        with _Store() as s:
            doc = _newer_doc(kind=RULE)
            item_id = s.seed(doc)
            before = s.raw(item_id)
            res = s.store.promote(item_id, "hard", assume_yes=True)
            self.assertFalse(res.changed)
            self._assert_refused(s, item_id, before, res.message)

    def test_promote_scope_refuses(self):
        with _Store() as s:
            item_id = s.seed(_newer_doc(scope_level="personal"))
            before = s.raw(item_id)
            res = s.store.promote_scope(item_id, "team", assume_yes=True)
            self.assertFalse(res.changed)
            self._assert_refused(s, item_id, before, res.message)

    def test_propose_refuses(self):
        with _Store() as s:
            item = MemoryItem.from_dict(_newer_doc())
            res = s.store.propose(item, change="edit", proposer="ann", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("pip install -U mokata", res.message)
            self.assertIsNone(s.raw(item.id))

    def test_review_transition_refuses(self):
        with _Store() as s:
            doc = _newer_doc()
            doc["status"] = "proposed"
            doc["review"] = {"state": "draft", "proposer": "ann", "approver": "",
                             "base_id": "", "change": "edit"}
            item_id = s.seed(doc)
            before = s.raw(item_id)
            for step in ("submit_for_review", "approve", "reject", "publish"):
                res = getattr(s.store, step)(item_id, actor="bob", assume_yes=True)
                self.assertFalse(res.ok, f"{step} must refuse")
                self._assert_refused(s, item_id, before, res.message)

    def test_publish_refuses_when_the_BASE_is_newer(self):
        # The proposal is ours; the item it would SUPERSEDE was written by a newer mokata.
        # Publishing rewrites the base, so the base's version governs the whole transition.
        with _Store() as s:
            base_id = s.seed(_newer_doc(subject="rotate keys", value="every 30 days"))
            base_before = s.raw(base_id)
            draft = MemoryItem.create("rotate keys", "every 90 days")
            draft.status = "proposed"
            draft.review = {"state": "approved", "proposer": "ann", "approver": "bob",
                            "base_id": base_id, "change": "edit"}
            s.backend.put(draft)                      # ours: a v1 doc, writes fine
            draft_before = s.raw(draft.id)

            res = s.store.publish(draft.id, actor="carol", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("pip install -U mokata", res.message)
            # NEITHER doc moves: publishing the successor while failing to supersede its base
            # would leave two live items claiming the same subject.
            self.assertEqual(base_before, s.raw(base_id))
            self.assertEqual(draft_before, s.raw(draft.id))

    def test_rollback_refuses_when_the_PRIOR_is_newer(self):
        with _Store() as s:
            prior_id = s.seed(_newer_doc(subject="rotate keys", value="every 30 days"))
            prior_before = s.raw(prior_id)
            current = MemoryItem.create("rotate keys", "every 90 days",
                                        supersedes=[prior_id])
            s.backend.put(current)
            current_before = s.raw(current.id)

            res = s.store.rollback(current.id, actor="ann", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("pip install -U mokata", res.message)
            self.assertEqual(prior_before, s.raw(prior_id))
            self.assertEqual(current_before, s.raw(current.id))

    def test_heal_refuses(self):
        with _Store() as s:
            old_id = s.seed(_newer_doc(subject="rotate keys", value="every 30 days"))
            before = s.raw(old_id)
            old = s.store.get(old_id)
            new = MemoryItem.create("rotate keys", "every 90 days")
            p = HealingProposal(kind=CONTRADICTION, subject="rotate keys", mtype="persistent",
                                old=old, new=new, rationale="disagreement")
            res = s.store.apply_proposal(p, "approve", assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.refused)
            self._assert_refused(s, old_id, before, res.message)

    def test_consolidation_merge_refuses(self):
        with _Store() as s:
            old_id = s.seed(_newer_doc(subject="rotate keys", value="every 30 days"))
            before = s.raw(old_id)
            old = s.store.get(old_id)
            keep = MemoryItem.create("rotate keys", "every 90 days")
            p = ConsolidationProposal(kind=MERGE, subject="rotate keys", mtype="persistent",
                                      olds=[old], new=keep, rationale="near-duplicates")
            res = s.store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.refused)
            self._assert_refused(s, old_id, before, res.message)

    def test_consolidation_PRUNE_refuses(self):
        # A prune DELETES. Deleting a doc whose fields this build cannot read destroys them just as
        # thoroughly as stripping them — "I didn't understand it, so I removed it" is the same bug
        # wearing a different verb.
        with _Store() as s:
            old_id = s.seed(_newer_doc())
            before = s.raw(old_id)
            old = s.store.get(old_id)
            p = ConsolidationProposal(kind=PRUNE, subject=old.subject, mtype=old.mtype,
                                      olds=[old], new=None, rationale="stale")
            res = s.store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.refused)
            self.assertIsNotNone(s.raw(old_id), "the doc must still EXIST")
            self._assert_refused(s, old_id, before, res.message)

    def test_the_notice_fires_for_the_refusal(self):
        with _Store() as s:
            degrade.reset_degrade_notices()
            item = MemoryItem.from_dict(_newer_doc())
            s.store.remember(item, assume_yes=True)
            classes = [n.failure_class for n in degrade.emitted_notices()
                       if n.subsystem == DOC_SUBSYSTEM]
            self.assertEqual([FAILURE_DOC_SCHEMA], classes)


class ExportStaysLossless(unittest.TestCase):
    """An export is a COPY OUT, not a rewrite: it must carry a newer doc whole — stamp and all —
    so the teammate who CAN read it gets it whole. An export that refused (or stripped) newer items
    would BE the strip, one hop downstream."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_export_carries_a_newer_doc_verbatim(self):
        from mokata.memory.share import export_memory
        with _Store() as s:
            doc = _newer_doc()
            s.seed(doc)
            data = export_memory(s.store)                   # must not raise
            [exported] = [i for i in data["items"] if i["id"] == doc["id"]]
            self.assertEqual(NEWER, exported[DOC_VERSION_KEY])
            self.assertEqual(FUTURE_VALUE, exported[FUTURE_KEY])


class ShareImportRefuses(unittest.TestCase):
    """A teammate's share file full of newer docs: refused item-by-item, and reported as REFUSED —
    not as 'you declined at the gate', which nobody did."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_import_refuses_newer_items_and_names_them(self):
        with _Store() as s:
            newer = _newer_doc(subject="rotate keys")
            ours = MemoryItem.create("backup cadence", "nightly").to_dict()
            data = {"kind": "mokata-memory-share", "items": [newer, ours]}
            res = import_memory(s.store, data, assume_yes=True)

            self.assertEqual(["rotate keys"], res.refused)
            self.assertEqual(["backup cadence"], res.added)    # the rest still imports
            self.assertEqual([], res.declined, "a client refusal is NOT a human decline")
            self.assertIn("REFUSED", res.render())
            self.assertIsNone(s.raw(newer["id"]), "the refused item must not be stored at all")


class LegacyDocsStillWork(unittest.TestCase):
    """D2's legacy-parse discipline: an unstamped doc parses, mutates, and GAINS the stamp."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_legacy_doc_parses_mutates_and_gains_the_stamp(self):
        with _Store() as s:
            doc = _legacy_doc(subject="legacy rule", value="always lint", kind=GUARDRAIL)
            item_id = s.seed(doc)
            self.assertNotIn(DOC_VERSION_KEY, json.loads(s.raw(item_id)))   # unstamped on disk

            res = s.store.promote(item_id, "soft", assume_yes=True)         # a legitimate write
            self.assertTrue(res.changed, res.message)

            stored = json.loads(s.raw(item_id))
            self.assertEqual(MEMORY_DOC_VERSION, stored[DOC_VERSION_KEY])   # ... and now stamped
            self.assertEqual("soft", stored["enforcement"])                 # the mutation landed
            self.assertEqual("always lint", stored["value"])                # nothing else moved

    def test_a_legacy_doc_emits_no_notice(self):
        MemoryItem.from_dict(_legacy_doc())
        self.assertEqual([], [n for n in degrade.emitted_notices()
                              if n.subsystem == DOC_SUBSYSTEM])


class RoundTripPreservation(unittest.TestCase):
    """Forward-compat WITHIN a version: an unknown sibling key survives a same-version write-back."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_same_version_round_trip_preserves_unknown_siblings(self):
        doc = MemoryItem.create("s", "v").to_dict()
        doc["a_key_we_do_not_model"] = {"nested": [1, 2]}
        item = MemoryItem.from_dict(doc)
        self.assertEqual({"a_key_we_do_not_model": {"nested": [1, 2]}}, item.extra)
        self.assertEqual(doc, item.to_doc())     # a DURABLE write, and it is byte-for-byte the doc

    def test_unknown_siblings_survive_a_real_mutation(self):
        with _Store() as s:
            doc = MemoryItem.create("lint rule", "always lint", kind=RULE).to_dict()
            doc["a_key_we_do_not_model"] = "keep me"
            item_id = s.seed(doc)

            res = s.store.promote(item_id, "hard", assume_yes=True)
            self.assertTrue(res.changed, res.message)

            stored = json.loads(s.raw(item_id))
            self.assertEqual("keep me", stored["a_key_we_do_not_model"])   # not dropped
            self.assertEqual("hard", stored["enforcement"])                # and the write landed

    def test_extra_can_never_shadow_a_modelled_field(self):
        item = MemoryItem.create("s", "v")
        item.extra = {"value": "SHADOW", "subject": "SHADOW"}   # hostile / hand-edited
        doc = item.to_doc()
        self.assertEqual("v", doc["value"])
        self.assertEqual("s", doc["subject"])


class NoBehaviourChange(unittest.TestCase):
    """Same-version reads/writes are what they always were, plus one additive key."""

    def setUp(self):
        degrade.reset_degrade_notices()

    def test_the_stamp_is_the_only_new_key_d6_added(self):
        """D6's own additive claim: the version STAMP was the only key D6 introduced.

        The pin names every key added SINCE, rather than being loosened, so it keeps failing for
        any un-declared new key while staying true about what D6 did. DB.S5 added `valid_from` /
        `valid_to` (the bi-temporal window) additively under doc v1 — the same call doc 95's D6
        decision made for the M-1/R9 fields — so `MEMORY_DOC_VERSION` does NOT move: the shape only
        ever grew, and an older reader tolerates unknown siblings by carrying them in `extra`."""
        item = MemoryItem.create("s", "v")
        before_d6 = {
            "id", "subject", "value", "mtype", "status", "kind", "provenance", "expires_at",
            "supersedes", "depends_on", "scope_level", "scope_id", "pin", "priority",
            "enforcement", "applicability", "review", "about_code",
        }
        added_since_d6 = ({"valid_from", "valid_to"}                                # DB.S5
                          | {"approved_by", "approved_at", "approval_ledger_id"})   # M-1/R9
        self.assertEqual({DOC_VERSION_KEY},
                         set(item.to_dict()) - before_d6 - added_since_d6)

    def test_same_version_round_trip_is_byte_identical(self):
        item = MemoryItem.create("s", "v", kind=RULE, enforcement="hard")
        self.assertEqual(item.to_dict(), MemoryItem.from_dict(item.to_dict()).to_dict())

    def test_the_ordinary_write_read_cycle_is_unchanged(self):
        with _Store() as s:
            item = MemoryItem.create("backup", "nightly", mtype=DECISION)
            res = s.store.remember(item, assume_yes=True)
            self.assertTrue(res.committed)
            self.assertFalse(res.refused)
            [got] = s.store.all_active(DECISION)
            self.assertEqual("nightly", got.value)
            self.assertEqual(ACTIVE, got.status)
            self.assertEqual(MEMORY_DOC_VERSION, got.schema_version)
            self.assertEqual({}, got.extra)


if __name__ == "__main__":   # pragma: no cover
    unittest.main()
