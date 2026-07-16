"""SI.6 — close the WriteGate side doors, and PROVE there are no others (0.0.13, final SI stage).

Three doors stood open around the universal gate. Each is a path where mokata's central claim —
"every durable write is human-gated, secret-scanned and audited" (P2/I1/I3) — was simply FALSE:

  C1 · `apply_consolidation` (= 52 M-6) ran its OWN bare confirm and then wrote. It was the last
       durable memory writer that never entered the WriteGate: no secret-scan (a merged, edited or
       summarized value could carry a credential straight into the store), no `write_gate` ledger
       record, and no WritePolicy seam. TM.S5c had already routed its writes journal-first, which
       made it LOOK governed — it was journalled, but never *gated*.

  C2 · `memory_export` was human-gated (SI.3) but never SCANNED. Export is the classic exfiltration
       surface (P23): it assembles the whole active memory corpus into a file built to be COMMITTED
       and handed to a teammate. A secret that reached memory through any unscanned path — and
       before this stage, C1 was exactly such a path — was copied out verbatim. The hole was on BOTH
       surfaces: the MCP tool called `export_memory(store, dest=...)` directly instead of
       `_gated_write`, and the CLI wrote the file with no gate, no scan and no ledger entry at all.
       (The two doors compose: C1 plants it, C2 exfiltrates it.)

  C3 · migrate's per-item WriteGate + secret hard-block already existed (37R). What did NOT was the
       funnel: a `postgres` destination is the SHARED team memory table, which the journal +
       single-flusher + CAS funnel owns, and migrate wrote it with `dest.put` — a bare
       `INSERT ... ON CONFLICT DO UPDATE` that does not bump `revision`. That bypassed CAS in BOTH
       directions: it silently clobbered a teammate's concurrent change, AND it left the row's
       revision stale, so a later flush's compare-and-set would match a revision that no longer
       described the doc and overwrite the migrated value in turn. A direct write did not merely
       skip the invariant — it corrupted it for every other writer.

And then the part that matters more than any of the three: THE ZERO-BYPASS AUDIT (`TestZeroBypass`).
A fix for three known doors is worth little if a fourth can be added next week. The audit sweeps
EVERY durable-write call site in `src/` and forces each into one of three registers — gated,
ungated-by-design (with its reason), or a KNOWN, FILED bypass. An unregistered writer FAILS. This is
the SI.4 AST-guard pattern (`TestEveryWriteRequestNamesItsWriter`), and it exists for the same
reason: K3 died as dead code because nothing structurally prevented it, and the C1 door stood open
for six stages because nothing structurally noticed it.

The audit is deliberately NOT a green light. It reports what is true, and what is true today is that
a cluster of CLI/bootstrap writers (init, harness setup, reset, stack export/import) still bypasses
the gate entirely. Those are REGISTERED, not excused — see `KNOWN_BYPASS`. Making the audit look
green by calling them "by design" would be exactly the overclaim doc 85 forbids.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mokata import team_journal                                    # noqa: E402
from mokata.config import Surface                                  # noqa: E402
from mokata.govern.ledger import AuditLedger                       # noqa: E402
from mokata.init import init_repo                                  # noqa: E402
from mokata.memory import (MemoryItem, MemoryStore, SQLiteBackend,  # noqa: E402
                           export_memory, export_payload)
from mokata.memory.consolidation import (MERGE, PRUNE, SUMMARIZE,  # noqa: E402
                                         ConsolidationProposal)
from mokata.memory.migrate import (batch_digest, item_digest,      # noqa: E402
                                   migrate_memory)

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")


def fake_secret() -> str:
    """The canonical AWS-docs example key, assembled at RUNTIME.

    mokata's own secret-guard PreToolUse hook blocks writing a file that carries a secret literal, so
    a test for the secret hard-block cannot spell one out. Dogfooding (the SI.4 convention)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _repo(d):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    return Surface.load(d)


def _store(d, ledger=None):
    surface = _repo(d)
    store = MemoryStore.from_surface(surface)
    if ledger is not None:
        store._ledger = ledger
    return surface, store


def _plant(store, subject, value):
    """Put an item straight into the backend, BYPASSING the gate — i.e. a secret that reached memory
    through an unscanned path (which is precisely what C1 was). This is how we prove the export
    blocks on the way OUT even when something got in."""
    it = MemoryItem.create(subject, value)
    store.backend.put(it)
    return it


# ======================================================================================
# C1 — apply_consolidation now commits through the universal WriteGate
# ======================================================================================

class TestConsolidationIsGated(unittest.TestCase):

    def _dupes(self, store):
        a = MemoryItem.create("db.choice", "postgres")
        b = MemoryItem.create("db.choice", "postgres")
        store.backend.put(a)
        store.backend.put(b)
        return [store.backend.get(a.id), store.backend.get(b.id)]

    def test_a_secret_in_a_consolidated_value_is_hard_blocked(self):
        """THE C1 bug. A merge whose winning value carries a credential used to be written with no
        scan at all — `assume_yes` walked straight past a bare confirm and into the backend."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            olds = self._dupes(store)
            poisoned = MemoryItem.create("db.choice", f"postgres, key={fake_secret()}")
            p = ConsolidationProposal(kind=MERGE, mtype=olds[0].mtype, subject="db.choice",
                                      olds=olds, new=olds[-1])
            res = store.apply_consolidation(p, "edit", edited=poisoned, assume_yes=True)

            self.assertFalse(res.changed, "a secret must HARD-BLOCK the consolidation")
            self.assertTrue(res.blocked)
            values = [i.value for i in store.backend.all()]
            self.assertNotIn(poisoned.value, values,
                             "the secret-bearing value must not reach the store")

    def test_the_apply_is_recorded_on_the_ledger_as_a_write_gate_decision(self):
        """C1 left no `write_gate` entry — the write was invisible to the audit trail (I3)."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            _s, store = _store(d, ledger=led)
            olds = self._dupes(store)
            p = ConsolidationProposal(kind=MERGE, mtype=olds[0].mtype, subject="db.choice",
                                      olds=olds, new=olds[-1])
            res = store.apply_consolidation(p, "approve", assume_yes=True, ledger=led)

            self.assertTrue(res.changed)
            gate_entries = [e for e in led.entries()
                            if e.get("kind") == "write_gate" and e.get("decision") == "approved"]
            self.assertTrue(gate_entries,
                            "an applied consolidation must leave a `write_gate` approval on the "
                            "ledger, like every other durable memory write")
            self.assertEqual(gate_entries[-1].get("write_kind"), "memory")

    def test_declining_at_the_gate_writes_nothing_and_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            _s, store = _store(d, ledger=led)
            olds = self._dupes(store)
            p = ConsolidationProposal(kind=MERGE, mtype=olds[0].mtype, subject="db.choice",
                                      olds=olds, new=olds[-1])
            res = store.apply_consolidation(p, "approve", confirm=lambda _t: False, ledger=led)

            self.assertFalse(res.changed)
            self.assertEqual(len([i for i in store.backend.all() if i.status == "active"]), 2,
                             "a declined consolidation must change nothing")
            declined = [e for e in led.entries()
                        if e.get("kind") == "write_gate" and e.get("decision") == "declined"]
            self.assertTrue(declined, "the decline belongs on the ledger too")

    def test_a_read_only_trust_dial_blocks_it_at_the_real_gate(self):
        """The SI.4 seam now reaches consolidation — it took no `policy` at all before."""
        from mokata.govern import READ_ONLY, TrustPolicy
        from mokata.govern.trust import MCP_SURFACE, WritePolicy
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            olds = self._dupes(store)
            p = ConsolidationProposal(kind=MERGE, mtype=olds[0].mtype, subject="db.choice",
                                      olds=olds, new=olds[-1])
            ro = WritePolicy(trust=TrustPolicy({MCP_SURFACE: READ_ONLY}),
                             tool="apply_consolidation", surface=MCP_SURFACE)
            res = store.apply_consolidation(p, "approve", assume_yes=True, policy=ro)
            self.assertFalse(res.changed, "a read-only dial must block at the gate that writes")

    def test_prune_and_summarize_still_apply(self):
        """No behaviour change on the paths that were already correct: they still commit."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            stale = MemoryItem.create("old.note", "gone")
            store.backend.put(stale)
            p = ConsolidationProposal(kind=PRUNE, mtype="*", subject="(stale)",
                                      olds=[store.backend.get(stale.id)], new=None)
            self.assertTrue(store.apply_consolidation(p, "approve", assume_yes=True).changed)
            self.assertIsNone(store.backend.get(stale.id), "local prune still hard-deletes")

            summ = MemoryItem.create("topic", "the summary")
            p2 = ConsolidationProposal(kind=SUMMARIZE, mtype=summ.mtype, subject="topic",
                                       olds=[], new=summ)
            self.assertTrue(store.apply_consolidation(p2, "approve", assume_yes=True).changed)

    def test_reject_and_defer_are_unchanged_no_ops(self):
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            olds = self._dupes(store)
            p = ConsolidationProposal(kind=MERGE, mtype=olds[0].mtype, subject="db.choice",
                                      olds=olds, new=olds[-1])
            for decision in ("reject", "defer"):
                res = store.apply_consolidation(p, decision)
                self.assertFalse(res.changed)
                self.assertFalse(res.aborted, "reject/defer is a no-op, not an abort")


# ======================================================================================
# C2 — the export is SCANNED (P23), blocked items are named, and the write is ledgered
# ======================================================================================

class TestExportIsScanned(unittest.TestCase):

    def test_a_secret_never_reaches_the_export_artifact(self):
        """THE C2 bug, exercised pre-fix-shaped: plant a secret through an unscanned path (what C1
        was), then export. The artifact must not contain it."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "clean.fact", "nothing sensitive")
            _plant(store, "leaked.key", f"the prod key is {fake_secret()}")

            dest = os.path.join(d, "share.json")
            data = export_memory(store, dest=dest)

            self.assertEqual(data["blocked"], ["leaked.key"],
                             "the blocked item is named by its KEY (P23)")
            self.assertEqual(len(data["items"]), 1, "only the clean item is exported")

            with open(dest, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn(fake_secret(), raw,
                             "THE hole: a secret must never reach the export artifact")
            self.assertNotIn("leaked.key", raw)
            self.assertIn("clean.fact", raw)

    def test_the_refusal_names_the_key_and_never_prints_the_value(self):
        """P23 — a refusal must not print the credential it is refusing."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "leaked.key", f"the prod key is {fake_secret()}")
            data = export_memory(store)
            self.assertEqual(data["blocked"], ["leaked.key"])
            self.assertNotIn(fake_secret(), json.dumps(data))

    def test_the_artifacts_on_disk_shape_is_unchanged(self):
        """No behaviour change: `blocked` rides the RETURNED dict only — the recipient of the file
        never learns which keys held secrets."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "clean.fact", "nothing sensitive")
            dest = os.path.join(d, "share.json")
            export_memory(store, dest=dest)
            with open(dest, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            self.assertEqual(sorted(on_disk), ["items", "kind", "schema_version"])
            self.assertNotIn("blocked", on_disk)

    def test_the_cli_export_is_scanned_and_ledgered(self):
        """The CLI had the same hole. (Consent is unchanged — the explicit `mokata memory export` IS
        the consent, per the stage scope — but the SCAN and the LEDGER are now real.)"""
        from mokata.cli import main
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "leaked.key", f"the prod key is {fake_secret()}")
            _plant(store, "clean.fact", "nothing sensitive")
            store.close()

            rc = main(["memory", "export", "--path", d])
            self.assertEqual(rc, 0)

            dest = os.path.join(d, ".mokata", "memory-share.json")
            with open(dest, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn(fake_secret(), raw, "the CLI export must not exfiltrate a secret")

            led = AuditLedger.from_mokata_dir(os.path.join(d, ".mokata"))
            sends = [e for e in led.entries()
                     if e.get("kind") == "write_gate" and e.get("write_kind") == "send"]
            self.assertTrue(sends, "the export must be on the audit ledger as an egress write")

    def test_the_mcp_export_reports_the_blocked_count_and_ledgers_the_send(self):
        from _support import mcp_commit                     # propose -> human-approves -> redeem
        from mokata.mcp import tools_write as TW
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "leaked.key", f"the prod key is {fake_secret()}")
            _plant(store, "clean.fact", "nothing sensitive")
            store.close()

            res = mcp_commit(TW.memory_export, path=d)
            self.assertTrue(res["committed"])
            self.assertEqual(res["blocked"], 1)
            self.assertEqual(res["blocked_keys"], ["leaked.key"])
            self.assertEqual(res["items"], 1)

            with open(res["dest"], encoding="utf-8") as fh:
                self.assertNotIn(fake_secret(), fh.read())

    def test_export_payload_is_the_exact_bytes_the_gate_hashes(self):
        """The ledger's record must be of what actually LEFT, not a summary of it."""
        with tempfile.TemporaryDirectory() as d:
            _s, store = _store(d)
            _plant(store, "clean.fact", "nothing sensitive")
            dest = os.path.join(d, "share.json")
            data = export_memory(store, dest=dest)
            with open(dest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), export_payload(data))


# ======================================================================================
# C3 — migrate goes journal-first: CAS + approval inheritance, zero direct Postgres writes
# ======================================================================================

class _SpyPgBackend:
    """A stand-in Postgres destination that RECORDS every durable call. The C3 claim is that after
    this stage `put`/`delete` are never reached on a shared destination — the writes go through the
    journal and are applied by the flusher under CAS. This spy is the proof."""

    TABLE = "mokata_memory"

    def __init__(self, rows=()):
        self.rows = {i.id: i for i in rows}
        self.calls = []                    # every DIRECT durable write attempted on the shared DB

    def all(self, mtype=None, statuses=None):
        out = []
        for i in self.rows.values():
            i._revision = getattr(i, "_revision", 3)      # a revision-tracking backend
            out.append(i)
        return out

    def get(self, item_id):
        return self.rows.get(item_id)

    def put(self, item):
        self.calls.append(("put", item.id))
        self.rows[item.id] = item

    def update(self, item):
        self.calls.append(("update", item.id))
        self.rows[item.id] = item

    def delete(self, item_id):
        self.calls.append(("delete", item_id))
        return self.rows.pop(item_id, None) is not None

    def close(self):
        pass


class _MigrateHarness:
    """A repo whose `postgres` destination is the spy, with the flush stubbed so the test can
    inspect exactly what was JOURNALLED (the flush itself is MS.S5's, and is tested there)."""

    def __init__(self, d, items=(), remote=()):
        self.d = d
        self.surface = _repo(d)
        self.src = SQLiteBackend(os.path.join(d, "src.db"))
        for i in items:
            self.src.put(i)
        self.dest = _SpyPgBackend(remote)
        self.flushes = []
        self.flush_result = None

    def _build(self, tool, root, config=None, clients=None, project=None):
        return self.dest if tool == "postgres" else self.src

    def _flush(self, surface, **kw):
        self.flushes.append(kw)
        if self.flush_result is not None:
            return self.flush_result
        return team_journal.FlushResult(flushed=len(self.pending()), pending=0)

    def pending(self):
        return team_journal.TeamJournal.for_surface(self.surface).pending()

    def run(self, to_backend="postgres", **kw):
        with mock.patch("mokata.memory.migrate.build_named_backend", self._build), \
             mock.patch("mokata.team_journal.flush", self._flush):
            return migrate_memory(self.surface, to_backend=to_backend, from_backend="sqlite",
                                  assume_yes=True, out=lambda *_a: None, **kw)


class TestMigrateIsJournalFirst(unittest.TestCase):

    def test_zero_direct_postgres_writes(self):
        """THE C3 bug. `dest.put` on the shared team table bypassed the journal/CAS funnel entirely.
        The spy proves not one direct write survives."""
        with tempfile.TemporaryDirectory() as d:
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres"),
                                          MemoryItem.create("cache", "redis")])
            res = h.run()

            self.assertEqual(res.migrated, 2)
            self.assertTrue(res.journaled)
            self.assertEqual(h.dest.calls, [],
                             "NOT ONE direct write may reach the shared Postgres table — every "
                             "write goes through the journal + CAS funnel")

    def test_every_write_is_journalled_with_a_cas_base_revision(self):
        with tempfile.TemporaryDirectory() as d:
            existing = MemoryItem.create("db", "sqlite")        # already remote, at revision 3
            local = MemoryItem.create("cache", "redis")         # believed-new
            h = _MigrateHarness(d, items=[existing, local], remote=[existing])
            h.run()

            pend = {e.key: e for e in h.pending()}
            self.assertEqual(len(pend), 2)
            self.assertEqual(pend[existing.id].op, team_journal.OP_UPDATE)
            self.assertEqual(pend[existing.id].base_revision, 3,
                             "an existing shared row migrates as a REVISION-GUARDED update — a "
                             "concurrent writer surfaces as a conflict, never a lost update")
            self.assertEqual(pend[local.id].op, team_journal.OP_PUT)
            self.assertIsNone(pend[local.id].base_revision,
                              "a believed-new row inserts (ON CONFLICT surfaces a concurrent create)")

    def test_each_journal_entry_inherits_the_approval_that_licensed_it(self):
        """Approval inheritance (C5/P2): the deferred flush must be able to point at the human
        decision. The ledger_id is the seq of the gate's OWN `approved` record."""
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            h.run(ledger=led)

            entry = h.pending()[0]
            self.assertIsNotNone(entry.ledger_id)
            rows = led.entries()
            approved = rows[entry.ledger_id - 1]             # seq is 1-based
            self.assertEqual(approved.get("kind"), "write_gate")
            self.assertEqual(approved.get("decision"), "approved",
                             "the inherited id must name the gate's approval of THIS write")

    def test_the_batch_decision_itself_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            h.run(ledger=led)
            batch = [e for e in led.entries() if e.get("kind") == "migrate_batch"]
            self.assertEqual(len(batch), 1)
            self.assertEqual(batch[0]["items"], 1)
            self.assertTrue(batch[0]["batch_digest"])

    def test_a_secret_is_still_hard_blocked_and_never_journalled(self):
        with tempfile.TemporaryDirectory() as d:
            h = _MigrateHarness(d, items=[MemoryItem.create("clean", "fine"),
                                          MemoryItem.create("creds", fake_secret())])
            res = h.run()
            self.assertEqual(res.blocked, 1)
            self.assertEqual(res.migrated, 1)
            journalled = {e.key for e in h.pending()}
            secret_id = [i.id for i in h.src.all() if i.subject == "creds"][0]
            self.assertNotIn(secret_id, journalled,
                             "the secret-bearing item must not even be JOURNALLED")

    def test_drop_source_refuses_while_writes_are_still_pending(self):
        """Journal-first makes `migrated` mean `journalled`. Dropping the source before the writes
        have landed would be the 'partially migrated then lost' case migrate promises never to
        cause."""
        with tempfile.TemporaryDirectory() as d:
            item = MemoryItem.create("db", "postgres")
            h = _MigrateHarness(d, items=[item])
            h.flush_result = team_journal.FlushResult(flushed=0, pending=1, skipped=True,
                                                      reason="offline")
            res = h.run(drop_source=True)
            self.assertEqual(res.dropped, 0, "must NOT drop the source while writes are pending")
            self.assertIsNotNone(h.src.get(item.id), "the source is left intact")

    def test_a_local_destination_does_not_use_the_team_funnel(self):
        """sqlite/obsidian have no shared-row concurrency and no funnel — they keep the direct write.
        The fix is scoped to the shared table, not smeared across every destination."""
        with tempfile.TemporaryDirectory() as d:
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            res = h.run(to_backend="obsidian")
            self.assertFalse(res.journaled, "a local destination does not use the team funnel")
            self.assertEqual(h.pending(), [], "and it journals nothing")


# ======================================================================================
# (e) the batch-derivation guard — a batch approval covers EXACTLY what was approved
# ======================================================================================

class TestBatchDerivation(unittest.TestCase):
    """migrate (and `team join`'s vault pull) ride ONE human decision across MANY per-item writes.
    That is only sound if what lands is what was approved. Red-teamed below: mutate an item between
    the approval and its write, and the write is REFUSED."""

    def test_the_digest_binds_content_not_identity(self):
        a = MemoryItem.create("db", "postgres")
        before = item_digest(a)
        a.value = "mysql"
        self.assertNotEqual(item_digest(a), before, "a changed VALUE must change the digest")

    def test_the_batch_digest_is_order_independent(self):
        a, b = MemoryItem.create("a", "1"), MemoryItem.create("b", "2")
        self.assertEqual(batch_digest([a, b]), batch_digest([b, a]))

    def test_an_item_mutated_between_approval_and_apply_is_refused(self):
        """THE red-team. The human approved a batch; something swaps an item's value before it is
        written. The approval does not cover the new content, so it must not be written."""
        with tempfile.TemporaryDirectory() as d:
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            planted = h.src.all()[0]

            def _mutate_at_the_gate(_text):
                planted.value = f"postgres, key={fake_secret()}"   # injected AFTER the hash
                return True

            # the confirm callback runs AT the gate — i.e. after the batch was hashed
            with mock.patch("mokata.memory.migrate.build_named_backend", h._build), \
                 mock.patch("mokata.team_journal.flush", h._flush), \
                 mock.patch.object(h.src, "all", return_value=[planted]):
                res = migrate_memory(h.surface, to_backend="postgres", from_backend="sqlite",
                                     confirm=_mutate_at_the_gate, out=lambda *_a: None)

            self.assertEqual(res.refused, 1,
                             "content that changed after the approval must be REFUSED")
            self.assertEqual(res.migrated, 0)
            self.assertEqual(h.pending(), [], "nothing may be journalled")
            self.assertEqual(h.dest.calls, [])

    def test_the_refusal_is_recorded_on_the_ledger_as_a_blocked_write(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            planted = h.src.all()[0]

            def _mutate(_text):
                planted.value = "swapped"
                return True

            with mock.patch("mokata.memory.migrate.build_named_backend", h._build), \
                 mock.patch("mokata.team_journal.flush", h._flush), \
                 mock.patch.object(h.src, "all", return_value=[planted]):
                migrate_memory(h.surface, to_backend="postgres", from_backend="sqlite",
                               confirm=_mutate, ledger=led, out=lambda *_a: None)

            blocked = [e for e in led.entries()
                       if e.get("kind") == "write_gate" and e.get("decision") == "blocked"]
            self.assertTrue(blocked)
            self.assertIn("after the batch approval", blocked[-1].get("reason", ""))

    def test_an_unmutated_batch_still_migrates_cleanly(self):
        """The negative control: the guard must not refuse an honest migration."""
        with tempfile.TemporaryDirectory() as d:
            h = _MigrateHarness(d, items=[MemoryItem.create("db", "postgres")])
            res = h.run()
            self.assertEqual(res.refused, 0)
            self.assertEqual(res.migrated, 1)


# ======================================================================================
# THE ZERO-BYPASS AUDIT — the release exit criterion, as a CI-permanent sweep
# ======================================================================================

# Durable-write PRIMITIVES. A call to one of these persists bytes outside the process.
_NAME_WRITERS = {"atomic_write_text", "atomic_write_bytes"}
_ATTR_WRITERS = {"write_text", "write_bytes", "atomic_write_text", "atomic_write_bytes"}
_SHUTIL_MUT = {"copy", "copy2", "copytree", "move", "rmtree"}
_OS_MUT = {"remove", "unlink", "rename", "replace", "rmdir"}
_BACKEND_MUT = {"put", "update", "delete"}
_BACKEND_BASES = {"backend", "dest", "source", "self"}

# ---- register 1: GATED. The write executes inside a `commit=` callable handed to WriteGate.submit
# (directly, or as the sole callee of one). Each entry names the gate that runs it.
GATED = {
    ("config_cmd.py", "_commit"): "WriteGate @config_cmd.py:161 (config_set)",
    ("knowledge/graph_adopt.py", "_commit"): "WriteGate @knowledge/graph_adopt.py (adopt_graph "
                                             "pins the code graph into the manifest — GR.S2)",
    ("cli_commands/skills.py", "commit"): "WriteGate @cli_commands/skills.py:197",
    ("docsync.py", "commit"): "WriteGate @docsync.py:421",
    ("team.py", "_commit"): "WriteGate @team.py:92 (_gated_write) + @team.py:679 (_join_vault)",
    ("memory/store.py", "_commit"): "WriteGate @memory/store.py:511 (_gated_commit) — ALL memory "
                                    "writes, incl. apply_consolidation as of SI.6 (74 C1)",
    ("memory/backends.py", "put"): "MemoryBackend contract impl — reached only from store._commit",
    ("memory/backends.py", "update"): "MemoryBackend contract impl — only from store._commit",
    ("memory/backends.py", "delete"): "MemoryBackend contract impl — only from store._commit",
    ("memory/overlay.py", "put"): "MemoryBackend contract impl — delegates to the wrapped backend",
    ("memory/overlay.py", "update"): "MemoryBackend contract impl — delegates to the wrapped backend",
    ("memory/overlay.py", "delete"): "MemoryBackend contract impl — delegates to the wrapped backend",
    ("memory/share.py", "export_memory"): "SI.6 (74 C2) — per-item egress scan lives HERE; the file "
                                          "write is gated by both callers (mcp `_gated_write` / cli "
                                          "WriteGate, kind=send)",
    ("share.py", "export_manifest"): "SI.6b — per-KEY egress scan lives HERE (`plan_export`); the "
                                     "file write is gated by both callers (mcp `_gated_write` / cli "
                                     "WriteGate, kind=send). Same shape as memory/share.py above.",
    ("share.py", "_commit"): "WriteGate @share.py:265 (apply_manifest) — SI.6b. The UNTRUSTED "
                             "incoming stack is boundary-scanned before the gate (a secret REFUSES "
                             "the whole file, P15); the write itself now sits in the commit closure "
                             "the gate runs, so `apply_manifest` is no longer a write site at all. "
                             "The MCP twins wrap it in `_gated_write` and pass no ledger, so one "
                             "write still records exactly one decision.",
    ("session_transport.py", "write_bundle"): "raw committer — sole callers are the two gated "
                                              "session_bundle committers (SS.S5-verified)",
    ("session_transport.py", "delete_bundle"): "raw committer — sole caller is the gated rename",
    ("vault.py", "commit_push"): "raw committer — sole callers gated: collab.py:101 + "
                                 "mcp/tools_write.py:471",
    ("vault.py", "_save_index"): "vault index — written only from within the gated push/claim flows",
    ("govern/revert.py", "write"): "reached only via gated_reversible_write's commit closure",
}

# ---- register 2: UNGATED BY DESIGN. Not a governed durable write. Each carries its reason.
UNGATED_BY_DESIGN = {
    ("atomicfile.py", "atomic_write_text"): "the write PRIMITIVE itself — no target of its own",
    ("knowledge/user_prefs.py", "record_graph_decline"): "GR.S2 — the USER's own standing "
        "preference (a graph-adoption decline) recorded in ~/.mokata, NOT a governed PROJECT "
        "durable write. Recording the human's explicit 'no' is like temp_local run-state, not a "
        "silent project change (P2 governs project code/config/memory writes); the OPT-IN adopt "
        "that DOES write the project manifest goes through the gate (graph_adopt._commit, GATED).",
    ("govern/lifecycle.py", "_write_tombstone"): "KB.S1 — the USER-scoped removal TOMBSTONE in "
        "~/.mokata/removals.json (repo identity + when + actor, NO repo content). Same class as "
        "user_prefs above: a user-profile record, not a governed PROJECT write. It exists BECAUSE "
        "a reset deletes the repo's own audit ledger — the removal must be recorded where the "
        "removal cannot reach (P22). The `_remove` it audits is in LEDGERED below.",
    ("govern/ledger.py", "record"): "the audit ledger IS the record of gate decisions — gating it "
                                    "would recurse",
    ("govern/ledger.py", "_write_counter"): "the ledger's O(1) seq sidecar (MS.S3)",
    ("team_journal.py", "_append"): "the journal is the PRE-commit buffer. The GATED write path "
                                    "(`append`) reaches it from inside the gate's commit closure "
                                    "(journal-first, C5) — but that is only ONE of its callers, and "
                                    "this entry used to name it as if it were the only one. It is "
                                    "not: the flusher's `mark_flushed`/`mark_conflict`/"
                                    "`mark_blocked` append under the FLUSH mutex (no gate, and a "
                                    "DIFFERENT lock), and `resolve`/`recover_stranded_floor` append "
                                    "under no lock at all. Those markers are journal bookkeeping, "
                                    "not new durable content — each records the fate of an ALREADY-"
                                    "gated entry — so the write stays ungated by design. But the "
                                    "old wording implied a single-lock serialisation that never "
                                    "held for them, which is precisely how MS.S8's Windows "
                                    "append-clobber survived review: `_append` now takes its OWN "
                                    "append lock, because nothing above it does.",
    ("progress_events.py", "append_event"): "append-only run telemetry under temp_local/",
    ("state.py", "_atomic_write"): "StateStore — process/run state under temp_local/",
    ("state.py", "delete"): "StateStore — process/run state under temp_local/",
    ("knowledge/ast_backend.py", "_save_cache"): "GR.S1 — the incremental AST edge cache under "
                                                 "temp_local/ (transient, same class as run state; "
                                                 "a missing/torn cache is a silent full re-parse). "
                                                 "Stores symbols/paths/lines only, never source text.",
    ("knowledge/freshness.py", "mark_dirty"): "GR.S4 — the graph dirty-set under temp_local/ "
                                              "(transient run-state, GR.S1-cache precedent; the "
                                              "PostToolUse async observability lane). Stores touched "
                                              "PATHS only, never content; an append failure just "
                                              "means the next query reconciles via HEAD/mtime.",
    ("knowledge/freshness.py", "drain_dirty"): "GR.S4 — consume-and-clear of the temp_local/ graph "
                                              "dirty-set (rename-then-read). Transient run-state, "
                                              "same class as the AST cache; never a governed write.",
    ("session_state.py", "update"): "session run-state under temp_local/",
    ("session_state.py", "delete"): "session run-state under temp_local/",
    ("govern/revert.py", "revert"): "undo of a state write under temp_local/",
    ("flush_liveness.py", "store_state"): "flush backoff/liveness state under temp_local/",
    ("flush_liveness.py", "update_state"): "flush backoff/liveness state under temp_local/",
    ("flush_liveness.py", "clear_state"): "flush backoff/liveness state under temp_local/",
    ("team_health.py", "store"): "health verdict cache under temp_local/",
    ("visibility.py", "capture_session_snapshot"): "session baseline snapshot under temp_local/",
    ("plans.py", "write_plan_file"): "plan drafts under temp_local/ (the PLAN is gated, not the "
                                     "draft file)",
    ("dashboard.py", "write_dashboard"): "derived HTML under temp_local/ — rendered FROM governed "
                                         "data",
    ("dashboard.py", "write_governance_dashboard"): "derived HTML under temp_local/",
    ("plugin_cache.py", "record_plugin_root"): "machine-local cache under ~/.mokata (outside the "
                                               "repo)",
    ("cli_commands/knowledge.py", "cmd_ci_check"): "ephemeral CI PR-comment body at an operator-"
                                                   "named --comment-file",
    ("vault.py", "vault_pull"): "copies an ALREADY-gated, hash-verified vault artifact out to an "
                                "operator-named dest (0.0.14 DEPRECATES the vault transport). "
                                "DB.S9 (0.0.13) AUDITED this annotation and confirmed it accurate: "
                                "the re-hash-and-refuse has been in `vault_pull` since 35d, so "
                                "'hash-verified' was never aspirational. DB.S9 added only the "
                                "AUDIT of a caught tamper (`vault_integrity` ledger row) — the "
                                "verification itself was already there.",
    ("memory/migrate.py", "migrate_memory"): "SI.6 (74 C3): the postgres destination is now "
                                             "journal-first (CAS + approval inheritance). The "
                                             "remaining direct calls are the LOCAL sqlite/obsidian "
                                             "destination (no shared rows, no funnel) and "
                                             "--drop-source — both inside the per-item WriteGate.",
}

# ---- register 3: LEDGERED (KB.S1). A governed durable write that keeps its bespoke human-at-TTY
# consent AND now leaves an audit-ledger record (P7: every durable write leaves a record), but is a
# BOOTSTRAP/SETUP one-shot that does not pass through the universal WriteGate. Distinct from GATED
# (no `commit=` closure / no secret-scan of user content — these write mokata's OWN scaffolding, so
# there is nothing to content-review) and from UNGATED_BY_DESIGN (these ARE governed project writes,
# not temp_local/derived/user-profile state). They are honestly their own class — calling them
# "GATED" would overclaim (doc 85 §4 forbids), and calling them "by design" would understate the
# record they now leave.
#
# These are the 6 that SI.6's sweep filed to KNOWN_BYPASS and doc 84 as "CLI-surface gate bypasses"
# (SI.6b closed the stack-share pair earlier). KB.S1 closes them: each writer's flow now records the
# act on the repo's audit ledger — init in `write_files` (the ledger's own creation is its first
# entry: the bootstrap-ordering answer), the harness/skills writers via a batched `setup`/`unsetup`
# record at the flow that drives them, and `_remove` via a user-scoped TOMBSTONE that survives the
# deletion of the repo ledger (the unsetup answer). Consent is UNCHANGED everywhere.
LEDGERED = {
    ("init.py", "write_files"): "KB.S1 — writes manifest/constitution/.gitignore under init_repo's "
        "bespoke read_yes_no; `_record_bootstrap` now leaves a `setup`/action=init record NAMING "
        "them on the repo ledger it creates (the ledger's own creation is the FIRST entry — the "
        "bootstrap-ordering answer). Files named, contents never (not content review).",
    ("harness_setup.py", "_write_json"): "KB.S1 — raw JSON committer (.mcp.json / settings.json "
        "merges). Every flow that reaches it (apply_setup / unsetup_harness / mcp_install) records a "
        "batched `setup`/`unsetup`/`mcp_install` entry naming the files on the repo ledger. Bespoke "
        "setup consent unchanged; merge-safety unchanged.",
    ("harness_setup.py", "_write_command_file"): "KB.S1 — materializes a command template; "
        "apply_setup records the batched `setup` entry naming the commands. Byte-identical write.",
    ("agent_skills.py", "write_skill_files"): "KB.S1 — writes SKILL.md; apply_setup records the "
        "batched `setup` entry naming the skills. (The `python -m mokata.agent_skills` regen of the "
        "SHIPPED plugin tree is a build tool, not a governed project write — no repo ledger there.)",
    ("agent_skills.py", "prune_orphan_skills"): "KB.S1 — DELETES stale mokata skill dirs; "
        "apply_setup (sync) and unsetup_harness record the removed dirs in the `setup`/`unsetup` "
        "entry. Marker-guarded (never a user's own skill), unchanged.",
    ("govern/lifecycle.py", "_remove"): "KB.S1 — DELETES .mokata/ under reset_state's read_yes_no "
        "consent (fail-closed off a TTY); the removal is now recorded in a user-scoped TOMBSTONE "
        "(~/.mokata/removals.json via `_write_tombstone`) that SURVIVES the deletion of the repo's "
        "own ledger — repo identity + when + actor, no repo content. The MCP `reset` already gated "
        "it; the CLI/library path now audits it too (parity).",
}

# ---- register 4: KNOWN BYPASS. Real durable writes that genuinely bypass the WriteGate AND leave
# no audit record. As of KB.S1 (0.0.14) this is EMPTY: the memory/export/migrate funnel (SI.6) and
# stack-share pair (SI.6b) are GATED, and the 6 setup one-shots are LEDGERED above. The register is
# KEPT — and the sweep FAILS if anything reappears here — so the 0.0.13 exit criterion "every
# durable write is at least recorded" cannot silently regress. A genuinely un-closable new writer is
# filed HERE with its reason (and flagged for Jas), never waved through.
KNOWN_BYPASS: dict = {}


def _durable_write_sites():
    """Every durable-write call site in src/, keyed by (file, enclosing named function)."""
    sites = {}

    class V(ast.NodeVisitor):
        def __init__(self, rel):
            self.rel, self.stack = rel, []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            fn, label = node.func, None
            name = getattr(fn, "id", None)
            if name in _NAME_WRITERS:
                label = name
            elif name == "open":
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = str(kw.value.value)
                if any(c in mode for c in "wax"):
                    label = f"open({mode})"
            attr = getattr(fn, "attr", None)
            val = getattr(fn, "value", None)
            base = getattr(val, "id", None) or getattr(getattr(val, "value", None), "id", None)
            if attr in _ATTR_WRITERS:
                label = attr
            elif base == "shutil" and attr in _SHUTIL_MUT:
                label = f"shutil.{attr}"
            elif base == "os" and attr in _OS_MUT:
                label = f"os.{attr}"
            elif attr in _BACKEND_MUT and base in _BACKEND_BASES:
                label = f"{base}.{attr}"
            if label:
                # a lambda's write belongs to the named function that owns it — that IS the
                # commit-closure shape the gate runs
                key = (self.rel, self.stack[-1] if self.stack else "<module>")
                sites.setdefault(key, []).append(f"{label}@{node.lineno}")
            self.generic_visit(node)

    for root, _dirs, files in os.walk(SRC):
        if "__pycache__" in root:
            continue
        for name in sorted(f for f in files if f.endswith(".py")):
            p = os.path.join(root, name)
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")
            with open(p, encoding="utf-8") as fh:
                v = V(rel)
                v.visit(ast.parse(fh.read(), filename=p))
    return sites


class TestZeroBypass(unittest.TestCase):
    """The release exit criterion, as a permanent CI guard.

    Every durable-write call site in src/ must be REGISTERED — gated, ungated-by-design, or a known
    filed bypass. A writer nobody classified is the failure mode this exists to prevent: that is how
    `apply_consolidation` (C1) sat outside the gate for six stages while every doc said otherwise."""

    def test_every_durable_write_site_in_src_is_registered(self):
        sites = _durable_write_sites()
        registered = (set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED)
                      | set(KNOWN_BYPASS))
        unregistered = sorted(set(sites) - registered)

        detail = "\n".join(f"    {rel}:{fn}  ({', '.join(sites[(rel, fn)])})"
                           for rel, fn in unregistered)
        self.assertEqual(
            unregistered, [],
            "UNREGISTERED DURABLE WRITER(S) — a new durable write appeared in src/ and nobody said "
            "whether it is gated.\n\n" + detail + "\n\n"
            "Route it through a WriteGate (see memory/store.py:_gated_commit or "
            "mcp/tools_write.py:_gated_write) and add it to GATED; or, if it is genuinely not a "
            "governed durable write (temp_local runtime state, the ledger, a derived artifact), add "
            "it to UNGATED_BY_DESIGN with a one-line reason. If it really does bypass the gate, it "
            "goes in KNOWN_BYPASS *and* on the backlog — never let it pass silently.")

    def test_the_register_carries_no_stale_entries(self):
        """Keeps the register honest: a justification for a site that no longer exists is a lie that
        makes the next reader trust the whole list less."""
        sites = _durable_write_sites()
        stale = sorted((set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED)
                        | set(KNOWN_BYPASS)) - set(sites))
        self.assertEqual(stale, [],
                         "these registered sites no longer exist — remove them: " + str(stale))

    def test_the_known_bypass_register_is_empty(self):
        """THE KB.S1 EXIT-CRITERION FLIP. SI.6 froze KNOWN_BYPASS at its filed count and failed if a
        7th appeared; KB.S1 CLOSED the 6 setup one-shots (all now LEDGERED), so the register must be
        EMPTY. It is kept — and this asserts empty rather than deleting it — so a NEW durable writer
        that bypasses both the gate AND the ledger cannot be quietly filed here: reappearing means
        editing this, which means someone reviews it (and flags it for Jas)."""
        self.assertEqual(
            KNOWN_BYPASS, {},
            "KNOWN_BYPASS is non-empty. A durable writer bypasses BOTH the WriteGate and the audit "
            "ledger. Close it: route it through a gate (→ GATED) or leave it a bespoke-consent "
            "record (→ LEDGERED). Filing it here again is the 0.0.13 exit criterion regressing.")

    def test_the_ledgered_setup_one_shots_carry_their_reasons(self):
        """The 6 KB.S1 closures are honestly its own class (bespoke consent + audit record, no
        WriteGate) — each must say why it is LEDGERED rather than GATED."""
        self.assertTrue(all(v.strip() for v in LEDGERED.values()),
                        "every LEDGERED setup one-shot must carry its justification")

    def test_no_memory_path_is_a_known_bypass(self):
        """SI.6's own three doors: CLOSED. Whatever else the sweep found, C1/C2/C3 are not on the
        bypass register."""
        for rel, fn in KNOWN_BYPASS:
            self.assertFalse(rel.startswith("memory/"),
                             f"a memory writer ({rel}:{fn}) is bypassing the gate — that is SI.6's "
                             f"whole charter")

    def test_the_audit_report(self):
        """Prints the disposition of every durable-write site. This runs in CI on every push, so the
        residual bypasses are in the output every time, until they are gone."""
        sites = _durable_write_sites()
        lines = ["", "=" * 78, "SI.6 ZERO-BYPASS AUDIT — every durable-write call site in src/",
                 "=" * 78]
        for title, reg in (("GATED — inside a gate-wrapped committer", GATED),
                           ("UNGATED BY DESIGN — not a governed durable write", UNGATED_BY_DESIGN),
                           ("LEDGERED — bespoke consent + audit record, bootstrap/setup one-shot "
                            "(KB.S1)", LEDGERED),
                           ("KNOWN BYPASS — gate-AND-ledger bypass, filed (doc 84)", KNOWN_BYPASS)):
            present = sorted(k for k in reg if k in sites)
            lines.append(f"\n{title}  [{len(present)}]")
            for rel, fn in present:
                lines.append(f"  {rel}:{fn}")
                lines.append(f"      {reg[(rel, fn)]}")
        lines.append("\n" + "-" * 78)
        lines.append(f"TOTAL sites: {len(sites)}  |  gated: {len(GATED)}  |  "
                     f"by-design: {len(UNGATED_BY_DESIGN)}  |  ledgered: {len(LEDGERED)}  |  "
                     f"KNOWN BYPASS: {len(KNOWN_BYPASS)}")
        lines.append("VERDICT: mokata's memory / export / migrate funnel has ZERO bypasses, and as")
        lines.append("         of SI.6b so does the STACK-SHARE pair. KB.S1 closes the last 6 SETUP")
        lines.append("         one-shots (init / harness / skills / reset) — each keeps its bespoke")
        lines.append("         TTY consent and now leaves an audit record (LEDGERED). KNOWN_BYPASS")
        lines.append("         is EMPTY: every durable write in src/ is gated, by-design, or")
        lines.append("         recorded. (Full WriteGate routing of the setup surfaces is 0.0.14+.)")
        lines.append("=" * 78)
        print("\n".join(lines))
        self.assertTrue(sites)


class TestTheStoreCannotWriteOutsideACommitClosure(unittest.TestCase):
    """The structural guard that would have caught C1 the day it was written.

    Every durable memory write goes through `_durable_write`, and every `_durable_write` must be
    called from INSIDE a commit closure the gate runs — never from the body of a public store method.
    That is exactly the difference between `apply_proposal` (which nested its writes in `_commit`)
    and `apply_consolidation` (which did not, and thereby skipped the gate)."""

    def test_every_durable_write_call_in_the_store_is_nested_in_a_commit_closure(self):
        path = os.path.join(SRC, "memory", "store.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)

        offenders = []

        class V(ast.NodeVisitor):
            def __init__(self):
                self.depth = 0            # function nesting depth inside the class body

            def visit_FunctionDef(self, node):
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

            def visit_Call(self, node):
                if getattr(node.func, "attr", None) == "_durable_write":
                    # depth 1 = the body of a store METHOD; >= 2 = inside its commit closure
                    if self.depth < 2:
                        offenders.append(node.lineno)
                self.generic_visit(node)

        V().visit(tree)
        self.assertEqual(
            offenders, [],
            "these `_durable_write` calls sit directly in a store method body, NOT inside a commit "
            f"closure the WriteGate runs (memory/store.py:{offenders}) — so they would commit "
            "without the secret-scan, the human gate and the ledger record. This is bug 74 C1.")


if __name__ == "__main__":
    unittest.main()
