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

import _writegraph                                                 # noqa: E402
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

            # 35b — the default backup dest is a timestamped `.mokata/backups/memory-<UTC>.json`.
            import glob
            backups = glob.glob(os.path.join(d, ".mokata", "backups", "memory-*.json"))
            self.assertEqual(len(backups), 1)
            dest = backups[0]
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
# DB.S5 adds `record_usage` — the memory backends' usage-telemetry writer. It is listed HERE, with
# the three governed mutators, on purpose: it does issue a real UPDATE against the durable store,
# so the audit must SEE it and someone must classify it. Leaving it off this list would have let a
# write on the read path stay invisible to the sweep, which is exactly the hole SI.6 exists to
# close. Its classification (UNGATED_BY_DESIGN, transient run-state) is an answer the register
# records, not one the detector assumes.
_BACKEND_MUT = {"put", "update", "delete", "record_usage"}
_BACKEND_BASES = {"backend", "dest", "source", "self"}

# SI.6-DELEGATED-BLINDNESS — the SQL RECEIVER class. A statement handed to a DB-API cursor reaches
# durable storage on both engines, and until this stage the sweep could not see one at all: the
# whole v5 edge substrate (DB.S7a) landed rows on SQLite AND Postgres through `conn.execute(INSERT
# …)` while the audit stayed green and never asked anyone to classify it.
#
# ⚠ DO NOT "OPTIMISE" THIS BY FILTERING ON THE STATEMENT TEXT. The obvious cheapening — keep only
# calls whose SQL literal starts with INSERT/UPDATE/DELETE, so SELECTs stay out of the register —
# was MEASURED at the stage grounding (2026-08-03) and is WRONG: it cuts 53 (file, function) keys
# to 17 and it DROPS `memory/edges.py:project_edges`, which is the very site this class exists to
# catch, because that INSERT is assembled by an f-string helper at `edges.py:272` and is not a
# literal at the call site. A filter that misses the motivating example is not a noise filter, it
# is a blind spot with a rationale. So the detector filters on the RECEIVER SHAPE and nothing else;
# read-only SQL is registered UNGATED_BY_DESIGN one site at a time, by a human, with a reason.
_SQL_EXEC = {"execute", "executemany", "executescript"}

# ---- register 1: GATED. The write executes inside a `commit=` callable handed to WriteGate.submit
# (directly, or as the sole callee of one). Each entry names the gate that runs it.
#
# KEYED BY QUALIFIED NAME as of 0.0.17 stage 1a's review (F1): `(file, Class.method)` for a method,
# `(file, outer.inner)` for a nested def. ONE ENTRY DESCRIBES ONE DEFINITION. The bare innermost
# name this replaced put eight distinct commit closures of `memory/store.py:_commit` under a single
# sentence, and four `delete` implementations under another; a planted `ExfilBackend.put` landed
# under the existing `put` key and kept the sweep green. Where an entry below splits, the split
# halves say what is true of THAT definition — a reason inherited from a sibling is the same
# collapse in a new costume.
GATED = {
    ("config_cmd.py", "config_set._commit"): "WriteGate @config_cmd.py:161 (config_set)",
    ("knowledge/graph_adopt.py", "adopt_graph._commit"): "WriteGate @knowledge/graph_adopt.py "
                                             "(adopt_graph pins the code graph into the manifest "
                                             "— GR.S2)",
    ("cli_commands/skills.py", "_skill_author.commit"): "WriteGate @cli_commands/skills.py:197",
    ("docsync.py", "reconcile_doc.commit"): "WriteGate @docsync.py:421",
    ("team.py", "_gated_write._commit"): "WriteGate @team.py:92 — the generic team committer.",
    ("team.py", "_join_vault._commit"): "WriteGate @team.py:679 (_join_vault) — the vault-pull "
        "leg of `team join`, whose per-item writes ride ONE batch approval. Split from the "
        "`_gated_write` entry above at the 0.0.17 review: they are two closures in two flows and "
        "the old single key could not tell them apart.",
    # ---- memory/store.py: TWELVE `_commit` closures are defined in this file and EIGHT of them
    # write directly. Under the bare key they were one entry with one sentence — the single worst
    # collapse the review measured. Each now says what THAT closure commits. All twelve run inside
    # `_gated_commit` (memory/store.py:511), which is the one gate; what differs is the content.
    ("memory/store.py", "MemoryStore.remember._commit"): "WriteGate @memory/store.py:511 "
        "(_gated_commit) — `remember` puts the ONE new item (store.py:805). The plain create path.",
    ("memory/store.py", "MemoryStore.promote._commit"): "WriteGate @memory/store.py:511 — "
        "`promote` updates the item's type/status in place (store.py:899). No new content: the "
        "value the human approved is the value that was already there.",
    ("memory/store.py", "MemoryStore.promote_scope._commit"): "WriteGate @memory/store.py:511 — "
        "`promote_scope` updates the item's SCOPE level/id (store.py:993, TM.S6). Moves a fact "
        "between scopes; the fact itself is unchanged.",
    ("memory/store.py", "MemoryStore.propose._commit"): "WriteGate @memory/store.py:511 — "
        "`propose` puts a PROPOSED item (store.py:1083). It enters the store in a non-active "
        "state, and the transition that activates it is `_transition`'s own gated closure below.",
    ("memory/store.py", "MemoryStore._transition._commit"): "WriteGate @memory/store.py:511 — the "
        "lifecycle transition: it updates the item's state and, where the transition supersedes a "
        "predecessor, that predecessor too (store.py:1184 + :1190 — TWO updates in one closure, "
        "which is the point of a closure: they land together or not at all).",
    ("memory/store.py", "MemoryStore.rollback._commit"): "WriteGate @memory/store.py:511 — "
        "`rollback` re-activates a prior version and demotes the current one (store.py:1271 + "
        ":1273). Both updates are content a reviewer already approved once.",
    ("memory/store.py", "MemoryStore.apply_proposal._commit"): "WriteGate @memory/store.py:511 — "
        "the self-healing applier's commit closure. ⚠ THIS KEY COVERS THREE DEFINITIONS: "
        "`apply_proposal` defines `_commit` three times (store.py:1753, :1772, :1785), one per "
        "proposal branch, and they write at :1763/:1766, :1777/:1779 and :1788. A qualified key "
        "separates definitions in different scopes, not three in the SAME scope — this is the "
        "residual named in `_writegraph.UNCLOSED_SHAPES` and counted by `qualname_collisions()`, "
        "and it is the ONLY such key in src/. All three apply an already-approved healing "
        "proposal to the item it names, under the one gate.",
    ("memory/store.py", "MemoryStore.apply_consolidation._commit"): "WriteGate @memory/store.py:511 "
        "— **bug 74 C1, the door this stage's parent closed.** MERGE/PRUNE/SUMMARIZE all land here "
        "(store.py:2105–2157: put, three updates, a delete, a final update). Before SI.6 this ran "
        "its own bare confirm and wrote straight to the backend — no secret-scan, no ledger record, "
        "no WritePolicy seam. It is the reason the register exists, and under the bare key its "
        "justification was shared with seven closures that never had that bug.",
    ("memory/backends.py", "NativeMemoryBackend.put"): "MemoryBackend contract impl — the injected-"
        "client adapter (`self.client.put(item.to_doc())`, backends.py:1205). Reached through the "
        "`MemoryBackend` contract from store._commit; it forwards a doc, it decides nothing.",
    ("memory/backends.py", "ObsidianBackend.put"): "MemoryBackend contract impl — writes the "
        "item's markdown file in the vault (`open(w)`, backends.py:1130). Reached through the "
        "`MemoryBackend` contract from store._commit; the bytes are the approved item's own.",
    ("memory/backends.py", "MemoryBackend.update"): "MemoryBackend contract DEFAULT — the abstract "
        "base's `update` is `self.put(item)` (backends.py:397), because storage is keyed by id. "
        "Every concrete backend that does not override it inherits this, and it is reached the "
        "same way they are: from store._commit.",
    ("memory/backends.py", "SQLiteBackend.delete"): "MemoryBackend contract impl — `DELETE FROM "
        "memory WHERE id=?` on the LOCAL store (backends.py:1094). From store._commit.",
    ("memory/backends.py", "PostgresBackend.delete"): "MemoryBackend contract impl — the SHARED "
        "table's revision-guarded delete (backends.py:1470). From store._commit; on a shared "
        "destination the journal+CAS funnel (C5) is what reaches it.",
    ("memory/backends.py", "ObsidianBackend.delete"): "MemoryBackend contract impl — removes the "
        "item's markdown file (`os.remove`, backends.py:1181). From store._commit.",
    ("memory/backends.py", "NativeMemoryBackend.delete"): "MemoryBackend contract impl — forwards "
        "to the injected client (backends.py:1228). From store._commit.",
    ("memory/overlay.py", "JournalOverlay.put"): "MemoryBackend contract impl — delegates to the "
                                                 "wrapped backend",
    ("memory/overlay.py", "JournalOverlay.update"): "MemoryBackend contract impl — delegates to "
                                                    "the wrapped backend",
    ("memory/overlay.py", "JournalOverlay.delete"): "MemoryBackend contract impl — delegates to "
                                                    "the wrapped backend",
    ("memory/share.py", "export_memory"): "SI.6 (74 C2) — per-item egress scan lives HERE; the file "
                                          "write is gated by both callers (mcp `_gated_write` / cli "
                                          "WriteGate, kind=send)",
    ("share.py", "export_manifest"): "SI.6b — per-KEY egress scan lives HERE (`plan_export`); the "
                                     "file write is gated by both callers (mcp `_gated_write` / cli "
                                     "WriteGate, kind=send). Same shape as memory/share.py above.",
    ("share.py", "apply_manifest._commit"): "WriteGate @share.py:265 (apply_manifest) — SI.6b. The UNTRUSTED "
                             "incoming stack is boundary-scanned before the gate (a secret REFUSES "
                             "the whole file, P15); the write itself now sits in the commit closure "
                             "the gate runs, so `apply_manifest` is no longer a write site at all. "
                             "The MCP twins wrap it in `_gated_write` and pass no ledger, so one "
                             "write still records exactly one decision.",
    # ---- session_transport.py — raw bundle committers, one entry per TRANSPORT (0.0.17 review F1).
    # ⚠ THE CALLER LIST BELOW IS CORRECTED, and the correction is the point (0.0.17 review F3a).
    # The old single entry read "sole callers are the two gated session_bundle committers
    # (SS.S5-verified)". That was FALSE and nothing checked it: `write_bundle` has THREE call sites
    # — session_bundle.py:695 (push) and :908 (rename), both gated, AND migrate_channels.py:304,
    # the vault channel's re-home, which is NOT a WriteGate write (see `migrate_channels.py:
    # _write_marker` in UNGATED_BY_DESIGN for what that flow actually carries). The classification
    # stays GATED because the two session_bundle committers are the governed push/rename path this
    # entry exists to describe; what changes is that the sentence no longer asserts a third caller
    # does not exist. Deriving caller lists mechanically instead of asserting them is filed
    # CALLER-LIST-UNPINNED (doc 84) and is NOT this stage.
    ("session_transport.py", "_FileTransport.write_bundle"): "raw committer, FILE transport "
        "(`atomic_write_text`, session_transport.py:95; inherited by LocalTransport and "
        "VaultTransport). THREE callers, ALL THREE GATED as of 0.0.17 stage 17: "
        "session_bundle.py:695 (push) + :908 (rename), and migrate_channels.py:352, the vault "
        "channel re-home. "
        "⚠ THAT LAST ONE IS THE ENTRY'S OWN HISTORY, kept because it is the evidence: this "
        "sentence used to end 'THIRD, ungated caller: migrate_channels.py:304', which made the "
        "entry FALSE by its own register's header. It was not argued away — it was declared and "
        "dated in GATED_CALLER_EXCEPTIONS with a stage (17) and a self-executing expiry, and stage "
        "17 routed that call through the WriteGate, at which point the expiry RED'd and the "
        "exception was deleted. The caller list is DATA in `DECLARED_CALLERS`; the gated/ungated "
        "verdict on each call site is derived from source, not from this sentence.",
    ("session_transport.py", "PostgresTransport.write_bundle"): "raw committer, POSTGRES transport "
        "(the bundle upsert, session_transport.py:188). Same three callers as the file transport "
        "above — session_bundle.py:695 + :908 + migrate_channels.py:352 — and, since stage 17 "
        "gated the third, the same verdict on all three: gate-enclosed. One exception covered both "
        "twins; closing the single call site retired it for both.",
    ("session_transport.py", "_FileTransport.delete_bundle"): "raw committer, FILE transport "
        "(`os.remove`, session_transport.py:114). Sole caller is the gated rename "
        "(session_bundle.py:909) — re-checked at the 0.0.17 review, unlike its `write_bundle` twin "
        "this one holds.",
    ("session_transport.py", "PostgresTransport.delete_bundle"): "raw committer, POSTGRES transport "
        "(session_transport.py:209). Sole caller is the gated rename (session_bundle.py:909).",
    ("vault.py", "commit_push"): "raw committer — both callers gated, and BOTH ARE NOW PINNED as "
        "data in `DECLARED_CALLERS` rather than asserted here (CALLER-LIST-UNPINNED). "
        "⚠ THE FIFTH FALSE CALLER STRING, and the one that says the mechanism was worth building: "
        "this entry read 'sole callers gated: collab.py:101 + mcp/tools_write.py:471'. BOTH halves "
        "were wrong. `mcp/tools_write.py` does not contain the string `commit_push` at all — the "
        "MCP caller is `mcp/tools_share.py:59`, inside `vault_push` — and the CLI call is "
        "`cli_commands/collab.py:109`, not :101 (:101 is where the `WriteGate` is constructed, four "
        "lines above the `commit=` lambda that actually runs the write). Stage 1a corrected four "
        "such strings by hand; this fifth was found by DERIVING the caller list instead, which is "
        "the whole point. The classification is unchanged and now verified: both call sites are "
        "gate-enclosed (`_writegraph.gate_enclosure`) — collab.py:109 lexically inside a `commit=` "
        "lambda, tools_share.py:59 inside the lambda handed to `_gated_write`.",
    ("vault.py", "_save_index"): "vault index — written only from within the gated push/claim flows",
    ("govern/revert.py", "ReversibleStateStore.write"): "reached only via gated_reversible_write's "
                                                        "commit closure",
    # ---- SI.6-DELEGATED-BLINDNESS: the SQL half. Each of these executes the statement that lands
    # a governed item's bytes; each is reached only from a write the gate already ran.
    ("memory/backends.py", "SQLiteBackend._put_on"): "SI.6-DB — the actual `INSERT … ON CONFLICT "
        "DO UPDATE` of a memory row on the LOCAL sqlite store (backends.py:980). It is the private "
        "tail of `SQLiteBackend.put`/`.update` and has no other caller, so it inherits their gate: "
        "reached only from store._commit. Registered separately from the method because the sweep "
        "now sees the statement as well, and both deserve an answer. (Split from its Postgres twin "
        "at the 0.0.17 review: one key cannot describe two engines' statements.)",
    ("memory/backends.py", "PostgresBackend._put_on"): "SI.6-DB — the SHARED table's `INSERT … ON "
        "CONFLICT DO UPDATE` (backends.py:1321), the Postgres twin of the sqlite one above. Same "
        "inheritance — the private tail of `PostgresBackend.put`/`.update`, reached only from "
        "store._commit — and on a shared destination it is the journal+CAS funnel (C5) that gets "
        "there, never a direct migrate write (bug 74 C3).",
    ("memory/edges.py", "project_edges"): "DB.S7a + SI.6-DELEGATED-BLINDNESS — **this is live "
        "confirmation (2), the site that motivated the whole stage.** The v5 edge substrate's "
        "durable INSERT (edges.py:356 withdrawal-close, :360 open-edge insert), run on BOTH "
        "engines. It is reached ONLY from `SQLiteBackend.put` / `PostgresBackend.put` (already "
        "GATED above) and from `team_journal.apply_memory_write` (GATED below, after its CAS "
        "matched), so every edge it projects was derived from a doc a human approved — it asserts "
        "nothing of its own. The sweep was blind to it for the entire DB.S7a build because SQL "
        "execution was not in the vocabulary, and the statement is f-string-assembled at "
        "edges.py:272 rather than literal here, which is also why a leading-verb filter would "
        "still miss it (see _SQL_EXEC).",
    ("memory/vector.py", "PgVectorBackend.put"): "MemoryBackend contract impl on the opt-in "
        "pgvector index (`PgVectorBackend(MemoryBackend)`) — the same contract, and therefore the "
        "same gate, as the `MemoryBackend` implementations above: reached from store._commit. The "
        "row it upserts is the item's own already-approved subject/value plus an embedding "
        "re-derived from them; it introduces no content a reviewer did not see. "
        "⚠ CALLER LIST CORRECTED (0.0.17 review F3b): this entry used to say 'reached ONLY from "
        "store._commit', and that was FALSE — `memory/reembed.py:124` calls `backend.put(item)` "
        "directly, once per item, inside `run_reembed`'s bulk rewrite. The classification is "
        "unchanged and the reason is the same in both flows (no new content: the vector is "
        "re-derived from the item's own gated text), but the exclusivity was asserted and never "
        "checked. `run_reembed` is LEDGERED below and carries its own preview→confirm gate. "
        "⚠ AND THAT MAKES THIS THE SECOND ENTRY FALSE BY GATED'S HEADER (0.0.17 stage 1a-FU). The "
        "1a correction stopped at 'the classification is unchanged', which is true of the CONTENT "
        "argument and silent about the CLASS: `GATED` is defined by the write executing inside a "
        "WriteGate commit callable, and reembed.py:124 is verifiably not one — LEDGERED consent is "
        "governance, but it is not this register's governance. Declared and dated at "
        "GATED_CALLER_EXCEPTIONS['REEMBED-UNGATED-VECTOR-PUT'] rather than left in prose. It was "
        "found by DERIVING the verdict instead of reading the sentence, which is the point of the "
        "whole mechanism.",
    ("memory/vector.py", "PgVectorBackend.delete"): "MemoryBackend contract impl on the pgvector "
        "index — the `DELETE FROM` twin of `put` above, project-scoped, reached from "
        "store._commit. (`run_reembed` rewrites vectors in place and never deletes, so the "
        "correction on `put` does not apply here — re-checked, not assumed.)",
    ("team_journal.py", "apply_memory_write"): "SI.6-DB — the FLUSHER's compare-and-set applier "
        "(revision-guarded DELETE @822 / INSERT @839 / UPDATE @852 against the shared memory "
        "table). It is GATED by INHERITANCE, and the inheritance is the C5 journal-first "
        "invariant, not a convenience: the entry it replays was written by `append` from INSIDE "
        "store._commit's gate, so the human decision, the secret-scan and the ledger record all "
        "happened before the entry existed. The flusher chooses nothing — it re-applies an "
        "approved payload under CAS and reports a conflict rather than overwriting. There is "
        "literally no content here a second gate could show anyone.",
    ("team_journal.py", "_close_edges_for"): "DB.S7a — closes the OPEN edges of an item a GATED "
        "prune removed (never deletes them: the relations were true for a while and a closed "
        "window says so honestly). Reached only from `apply_memory_write` above, and only AFTER "
        "its revision-guarded DELETE matched a row, so it inherits that CAS guarantee and that "
        "gate.",
    ("memory/migrate.py", "migrate_memory"): "SI.6 (74 C3): the postgres destination is "
        "journal-first (CAS + approval inheritance), and every MIGRATED item is written inside the "
        "per-item WriteGate — `migrate_memory` builds the gate at migrate.py:356 and submits each "
        "item through it at :400. The remaining direct destination write is the LOCAL "
        "sqlite/obsidian one (no shared rows, no funnel), and it is inside that same gate. "
        "⚠ THE `--drop-source` HALF OF THIS SENTENCE WAS FALSE (0.0.17 review F3c) AND IS NOW "
        "CLOSED AT STAGE 16 — BY SPLITTING THE ENTRY, WHICH IS WHY THIS ONE IS FINALLY TRUE AS "
        "WRITTEN. The reason this entry carried — preserved byte-for-byte when the entry MOVED "
        "from UNGATED_BY_DESIGN to GATED, which is exactly the mistake — ended '…and "
        "--drop-source — both inside the per-item WriteGate'. The drop never was. What F3c could "
        "only DESCRIBE (a destructive `source.delete` outside the gate, bespoke-confirm-only, "
        "unrecorded) it could not FIX inside this entry, because one key cannot be true of two "
        "regimes — see REGISTER-KEY-COLLISION (doc 84). Stage 16 extracts the drop into "
        "`memory/migrate.py:_drop_source`, which earns its own key, its own reason and its own "
        "class: LEDGERED, below. So THIS key now describes exactly one regime — the gate-run "
        "destination write — and the `--drop-source` sentence is no longer part of it. "
        "THE PROCESS NOTE, kept because it outlives the fix: moving a register entry means "
        "re-verifying the sentence you are moving, not only the register it lands in. "
        "For the record of how the move was found: SI.6-DELEGATED-BLINDNESS's coherence contract "
        "caught it on its FIRST RUN, and it is the register's own words that convicted it — an "
        "entry filed UNGATED_BY_DESIGN, a list whose header says 'not a governed durable write', "
        "whose justification ended '…inside the per-item WriteGate'. It was never by-design; it "
        "was GATED, in the wrong list, since SI.6. It also reaches "
        "`team_journal.apply_memory_write` by delegation through `team_journal.flush` (:348) — the "
        "path the contract printed.",
    ("govern/secret_ignore.py", "_gated_write._commit"): "SECRET-IGNORE — WriteGate @govern/secret_ignore.py "
        "(`_gated_write`, the sole writer of the version-controlled `.mokata/secret-ignores.json`; "
        "both `add_ignore` and `remove_ignore` route through it). The gate secret-scans the "
        "rendered store, so a secret pasted into `--reason` is hard-blocked; the store itself "
        "carries only sha256 hashes, never the flagged literal.",
}

# ---- register 2: UNGATED BY DESIGN. Not a governed durable write. Each carries its reason.
UNGATED_BY_DESIGN = {
    ("memory/store.py", "MemoryStore.recall_relevant"): "DB.S5 — the CALL SITE of the usage telemetry below "
        "(`self.record_usage(...)` on the returned top-k). Registered separately from the writer "
        "because the sweep sees a caller and a callee and both deserve an answer: this is the read "
        "path that STAMPS, and it is ungated for the same reason the writer is (transient "
        "run-state, no approved content touched — see `memory/store.py:record_usage`). The stamp "
        "runs LAST, after the recall's answer exists, and cannot fail it.",
    ("memory/store.py", "MemoryStore.record_usage"): "DB.S5 — usage TELEMETRY (`hit_count` / "
        "`last_recalled_at`), transient RUN-STATE per the D5 policy, on the read path. It is not a "
        "governed durable write and there is nothing an approval could review: it touches NO "
        "content a human approved — not the `doc`, not the value, not the provenance, not the "
        "validity window — only two counter columns that exist solely to rank, and the write "
        "carries no user content to secret-scan (an integer and a clock reading). The `mark_dirty` "
        "precedent, on the memory store. Degrade-clean at THIS seam: a telemetry failure is "
        "swallowed so it can never fail the recall it rode. Deliberately does NOT journal in team "
        "mode — a monotonic counter has no CAS conflict to resolve, and un-approved rows do not "
        "belong in an approval ledger.",
    ("atomicfile.py", "atomic_write_text"): "the write PRIMITIVE itself — no target of its own",
    ("knowledge/user_prefs.py", "record_graph_decline"): "GR.S2 — the USER's own standing "
        "preference (a graph-adoption decline) recorded in ~/.mokata, NOT a governed PROJECT "
        "durable write. Recording the human's explicit 'no' is like temp_local run-state, not a "
        "silent project change (P2 governs project code/config/memory writes); the OPT-IN adopt "
        "that DOES write the project manifest goes through the gate (graph_adopt._commit, GATED).",
    ("extras_install.py", "record_extra_decline"): "DB.S4 — the SAME class as "
        "`user_prefs.record_graph_decline` directly above, generalised: the USER's standing 'no' "
        "to an optional extra (the embeddings model), recorded in ~/.mokata/extra_declines.json. "
        "Not a governed PROJECT write — nothing in the repo changes, and the whole point of the "
        "record is that the human is never asked again. The consent it guards is the gate; this "
        "is only its memory. (The ACCEPT path writes no project file at all — it runs pip.)",
    ("govern/lifecycle.py", "_write_tombstone"): "KB.S1 — the USER-scoped removal TOMBSTONE in "
        "~/.mokata/removals.json (repo identity + when + actor, NO repo content). Same class as "
        "user_prefs above: a user-profile record, not a governed PROJECT write. It exists BECAUSE "
        "a reset deletes the repo's own audit ledger — the removal must be recorded where the "
        "removal cannot reach (P22). The `_remove` it audits is in LEDGERED below.",
    ("govern/ledger.py", "AuditLedger.record"): "the audit ledger IS the record of gate decisions "
                                    "— gating it would recurse",
    ("govern/ledger.py", "AuditLedger._write_counter"): "the ledger's O(1) seq sidecar (MS.S3)",
    ("team_journal.py", "TeamJournal._append_all"): "the journal is the PRE-commit buffer. The GATED write path "
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
                                    "append-clobber survived review: `_append_all` now takes its OWN "
                                    "append lock, because nothing above it does. "
                                    "DB.S7d renamed the funnel `_append` → `_append_all` (it takes "
                                    "a LIST) and left `_append` as its one-record case, so the "
                                    "lock/gate posture above is unchanged — but the funnel now also "
                                    "carries `resolve_group`, a WHOLE-approval verdict. That is "
                                    "still journal bookkeeping over already-gated entries: the "
                                    "human decision it records was itself taken inside "
                                    "`MemoryStore.apply_group_decision`'s WriteGate, and the "
                                    "multi-record write exists precisely so those N records cannot "
                                    "land apart from one another.",
    ("team_journal.py", "TeamJournal.compact"): "J-PERF — journal COMPACTION, and the one rewrite in a "
                                    "file that is otherwise append-only. It is not a durable "
                                    "write in the governed sense because it adds NOTHING: it "
                                    "rewrites the temp_local journal with a subset of its own "
                                    "existing lines (kept byte-for-byte — no re-serialisation), "
                                    "dropping only ids the replay has already resolved to "
                                    "FLUSHED. Those entries are done and their audit record "
                                    "lives on the LEDGER (`team_flush`, C5), not here; nothing "
                                    "reads a flushed journal record. Every caller-visible read "
                                    "(pending/conflicts/blocked) is identical across it, so "
                                    "there is no change for a gate to show a human. Same class "
                                    "as `_append` above — journal bookkeeping over already-"
                                    "gated entries — and it runs under the APPEND lock (not the "
                                    "flush mutex, which excludes flushers only) with "
                                    "`atomic_write_text`, so a crash leaves the whole old file.",
    ("migrate_channels.py", "_write_marker"): "SIMP.S2 — the once-migrated idempotence marker "
        "under temp_local/deprecations/ (run-state; this marker only makes a re-run a no-op, and "
        "THAT is what makes it ungated: it records that a migration happened, it is not the "
        "migration). "
        "⚠ THE CLAIM ABOUT THE MIGRATION WAS CORRECTED AT THE 0.0.17 REVIEW (F3d) AND IS NOW TRUE "
        "AGAIN FOR A DIFFERENT REASON (stage 17). The original reason said 'the MIGRATION itself "
        "is gated via migrate_memory / import_memory' as if that covered every channel; it covered "
        "TWO of the three, and F3d cut it back to what was actually true — the VAULT channel did "
        "neither, re-homing bundles with `dst.write_bundle(tag, blob)` at :304 with no WriteGate, "
        "no secret scan and no `write_gate` ledger record. STAGE 17 CLOSED THAT: all three "
        "channels are gated now — `_migrate_memory_channel` routes to `memory.migrate_memory`, "
        "`_migrate_memory_share` to `memory.import_memory`, and `_migrate_vault` submits each "
        "bundle to the universal WriteGate (migrate_channels.py:339) with the write in the "
        "`commit=` callable at :342. The marker itself is unchanged and ungated for the reason "
        "above: it records that a migration happened, it is not the migration.",
    ("progress_events.py", "ProgressLog.append_event"): "append-only run telemetry under "
                                                        "temp_local/",
    ("state.py", "StateStore._atomic_write"): "StateStore — process/run state under temp_local/",
    ("state.py", "StateStore.delete"): "StateStore — process/run state under temp_local/",
    ("knowledge/ast_backend.py", "AstBackend._save_cache"): "GR.S1 — the incremental AST edge cache under "
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
    ("injection_ledger.py", "record_injected"):
        "H-1a S4 — the per-session already-injected ledger under temp_local/. Transient "
        "run-state on the GR.S4 dirty-set pattern above, and the same reading: P2 gates DURABLE "
        "writes (memory, code, config) and this is run-tracking. Stores memory item IDs only, "
        "never item CONTENT, and it is derived — deleting it costs a session one repeated "
        "injection and nothing else. Append-only (a single O_APPEND write), no update path, no "
        "delete path, and it never raises.",
    ("knowledge/anchor_fingerprints.py", "_write_record"):
        "H-6 S1 — the DURABLE anchor→fingerprint record under temp_local/ (H-6 plan of record, "
        "decision #5). It differs from the two GR.S4 entries above in ONE respect and it is worth "
        "naming, because that difference is the whole slice: this record is NOT session-scoped, it "
        "outlives the session that wrote it. That does not make it a governed write. P2 gates "
        "durable writes of FACTS — memory, code, config — and this holds nothing a human said: it "
        "stores a content HASH per anchored path (never source text, never an item's value), and "
        "every byte of it is re-derivable by re-hashing the repo. Losing it costs one silent "
        "re-record and can never cost a fact. The SQLite memory store lives under the same "
        "directory and outlives every session too — durability is not the gating question.",
    ("session_state.py", "SessionScopedStore.update"): "session run-state under temp_local/",
    ("session_state.py", "SessionScopedStore.delete"): "session run-state under temp_local/",
    ("govern/revert.py", "ReversibleStateStore.revert"): "undo of a state write under temp_local/",
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
    # ==================================================================================
    # SI.6-DELEGATED-BLINDNESS — the SQL receiver class (0.0.17 stage 1a). 50 sites the sweep
    # could not see at all until this stage; 6 of them are GATED (above) and these 44 are not.
    # There is NO blanket "SELECT is fine" rule and NO reasoning from a function's name: each line
    # below says what THAT statement does and why nothing about it is a governed durable write.
    # ==================================================================================

    # ---- memory/_sqlite.py — the one connection factory. PRAGMAs, not rows.
    ("memory/_sqlite.py", "_apply_pragmas"): "SI.6-DB — `PRAGMA busy_timeout=` on a connection "
        "just opened. A per-CONNECTION setting that lives and dies with the handle: it stores no "
        "byte, and two processes setting it differently disagree about nothing durable.",
    ("memory/_sqlite.py", "_current_mode"): "SI.6-DB — `PRAGMA journal_mode` with no `=`, i.e. a "
        "HEADER READ (its own docstring says so, and says why: a read needs no exclusive lock). "
        "Reads the mode, changes nothing.",
    ("memory/_sqlite.py", "_switch_to_wal"): "SI.6-DB — `PRAGMA journal_mode=WAL`. This one DOES "
        "change a durable byte (the db header) and is registered rather than waved through for "
        "exactly that reason. It is still not a governed write: it changes HOW the store journals, "
        "never WHAT it holds — no item, no value, no provenance, nothing a reviewer could be shown "
        "and nothing to secret-scan. MS.S7's whole point is that a sibling process making the same "
        "switch is a WIN, not a conflict, which is only true of a setting with no content.",

    # ---- memory/backends.py — SQLiteBackend + PostgresBackend. Schema, derived indexes, reads.
    # Every key here is now CLASS-QUALIFIED (0.0.17 review F1). Eleven of these entries used to be
    # one-per-NAME and therefore covered both engines with one sentence; each is now split, and the
    # split halves name their own engine and their own statement rather than saying "on both".
    ("memory/backends.py", "SQLiteBackend.__init__"): "SI.6-DB — `CREATE TABLE IF NOT EXISTS "
        "memory` + `CREATE INDEX IF NOT EXISTS memory_subject` on first open of the LOCAL store "
        "(backends.py:440, :463). Idempotent provisioning of mokata's own empty table: it creates "
        "a place for facts, it does not assert one. Gating it would put a human approval in front "
        "of opening a database. (`PostgresBackend.__init__` executes no statement of its own — the "
        "shared schema is `teamdb.py:_run_ddl`'s, below — so it is not a site and has no entry.)",
    ("memory/backends.py", "SQLiteBackend.fts5_available"): "SI.6-DB — a CAPABILITY PROBE: creates and "
        "immediately drops `temp.mokata_fts5_probe` to find out whether this SQLite build has "
        "FTS5. It runs against the `temp` schema, which is per-connection and vanishes with it, so "
        "nothing durable is touched at all — the probe's only output is a boolean.",
    ("memory/backends.py", "SQLiteBackend._ensure_fts"): "SI.6-DB — creates the FTS5 virtual table, its three "
        "sync triggers, and backfills it by SELECTing rows that are already in `memory`. A "
        "DERIVED INDEX over already-gated content, re-derivable in full from the rows it indexes: "
        "the `memory/reembed.py:run_reembed` reading exactly ('a recomputed index, not a change to "
        "what memory SAYS'). Deleting it costs one rebuild and can never cost a fact.",
    ("memory/backends.py", "SQLiteBackend._ensure_scope_columns"): "SI.6-DB — `PRAGMA table_info(memory)` then "
        "`ALTER TABLE memory ADD COLUMN` for the TM.S6 scope/precedence columns. Schema migration "
        "of mokata's own table, adding EMPTY columns; no row's content changes and there is "
        "nothing for a gate to preview.",
    ("memory/backends.py", "SQLiteBackend._ensure_lifecycle_columns"): "SI.6-DB — the `valid_from`/`valid_to` "
        "twin of `_ensure_scope_columns` directly above. Same reading: empty columns, no content.",
    ("memory/backends.py", "SQLiteBackend._ensure_edges"): "SI.6-DB — `CREATE TABLE IF NOT EXISTS` for the local "
        "edges table plus its two partial indexes, and `ALTER TABLE … ADD COLUMN` for a store "
        "provisioned before DB.S7a. Schema only; the rows that later land in it are projected by "
        "`memory/edges.py:project_edges`, which is GATED above.",
    ("memory/backends.py", "SQLiteBackend._backfill_scope_columns"): "SI.6-DB — a one-shot `UPDATE memory SET "
        "scope_level=…` that PROJECTS each row's own already-stored `doc` JSON into the new "
        "columns, then stamps `PRAGMA user_version` so it never runs twice. It adds nothing and "
        "changes nothing a human approved: every value it writes is read out of the same row's "
        "`doc`. A re-derivation of stored content into a queryable shape is the `_ensure_fts` "
        "class, not a new assertion.",
    ("memory/backends.py", "SQLiteBackend._backfill_lifecycle_columns"): "SI.6-DB — the lifecycle twin of "
        "`_backfill_scope_columns` above: projects `$.valid_from` (falling back to the row's own "
        "`provenance.created_at`) out of the row's `doc` into its column, guarded by the same "
        "`user_version` stamp. Same reading — a projection of the row's own content.",
    ("memory/backends.py", "SQLiteBackend._backfill_edges"): "DB.S7a — the v5 edge MIGRATION for an existing "
        "local store: reads every `doc`, re-derives the edges those already-gated docs assert, and "
        "inserts them, then stamps `user_version`. It asserts nothing new — every edge is derived "
        "from a doc a human already approved — and the refs whose target item is absent are SKIPPED "
        "and COUNTED (`edge_backfill_skipped`) rather than invented. Same class as `_ensure_fts`: "
        "a derived index, rebuildable from the rows it reads.",
    ("memory/backends.py", "PostgresBackend._read_edges_present"): "SI.6-DB — `SELECT to_regclass(%s)`, i.e. 'does "
        "the shared edges table exist on this DB'. A capability probe that reads the catalogue.",
    ("memory/backends.py", "PostgresBackend._read_scope_backfilled"): "SI.6-DB — reads one flag column off the "
        "shared `schema_version` row to learn whether the scope backfill has run. Read-only.",
    ("memory/backends.py", "SQLiteBackend.get"): "SI.6-DB — `SELECT doc FROM memory WHERE id=?` "
        "(backends.py:1011). One item out by id; no write path.",
    ("memory/backends.py", "PostgresBackend.get"): "SI.6-DB — `SELECT doc, revision …` "
        "(backends.py:1406). One item out by id, plus the shared row's revision, which is what "
        "makes a later CAS possible. Read-only.",
    ("memory/backends.py", "SQLiteBackend.all"): "SI.6-DB — `SELECT doc FROM memory … ORDER BY "
        "seq` (backends.py:1045): the whole (scoped, filtered) item list out. No write path.",
    ("memory/backends.py", "PostgresBackend.all"): "SI.6-DB — the shared table's twin of the "
        "sqlite listing above (backends.py:1463). No write path.",
    ("memory/backends.py", "SQLiteBackend.hydrate"): "SI.6-DB — bulk `SELECT` of docs for the "
        "recall path's candidate set (backends.py:1085). Read-only by construction.",
    ("memory/backends.py", "PostgresBackend.hydrate"): "SI.6-DB — the shared table's bulk `SELECT` "
        "of the same candidate set (backends.py:1542). Read-only by construction.",
    ("memory/backends.py", "SQLiteBackend.open_edges"): "DB.S7a — `SELECT` of an item's currently-"
        "open edges (`open_edges_sql`, backends.py:838). The read half of the edge substrate whose "
        "write half is `memory/edges.py:project_edges`, GATED above.",
    ("memory/backends.py", "PostgresBackend.open_edges"): "DB.S7a — the shared edges table's twin "
        "of the read above (backends.py:1386). Read-only.",
    ("memory/backends.py", "SQLiteBackend.expand_from"): "DB.S7b — the bounded recursive-CTE "
        "traversal (`expansion_sql`, backends.py:854). A `WITH RECURSIVE … SELECT`: it walks edges "
        "and returns rows, and the bound exists to cap its COST, not its authority. No write.",
    ("memory/backends.py", "PostgresBackend.expand_from"): "DB.S7b — the shared table's twin of "
        "the recursive traversal above (backends.py:1400). No write.",
    ("memory/backends.py", "SQLiteBackend.lexical_search"): "SI.6-DB — the BM25 (`bm25(…) MATCH`) "
        "ranking SELECT (backends.py:618). A ranking read; the FTS index it reads is maintained by "
        "`SQLiteBackend._ensure_fts` above.",
    ("memory/backends.py", "PostgresBackend.lexical_search"): "SI.6-DB — the `ts_rank(… @@ …)` "
        "ranking SELECT on the shared table (backends.py:1586). A ranking read.",
    ("memory/backends.py", "PostgresBackend.index_epoch"): "DB.S7c2 — `SELECT count(*), sum(revision), max(seq)`, "
        "the whole-index change counter a citation is stamped with. It COMPUTES the epoch from the "
        "table; it never stores one.",
    ("memory/backends.py", "SQLiteBackend.usage_stats"): "DB.S5 — `SELECT id, hit_count, "
        "last_recalled_at` for a batch of ids (backends.py:927). The READ twin of "
        "`SQLiteBackend.record_usage` below; ranking input only.",
    ("memory/backends.py", "PostgresBackend.usage_stats"): "DB.S5 — the shared table's twin of the "
        "telemetry read above (backends.py:1630). Ranking input only.",
    ("memory/backends.py", "SQLiteBackend.record_usage"): "DB.S5 — the `UPDATE memory SET "
        "hit_count = … + 1, last_recalled_at = …` itself, on the LOCAL store (backends.py:900). "
        "Registered here for the same reason and with the same answer as "
        "`memory/store.py:MemoryStore.record_usage` above, which is its caller: transient "
        "RUN-STATE on the read path. It touches NO content a human approved — not the doc, not the "
        "value, not the provenance, not the validity window — only two counter columns that exist "
        "solely to rank, so there is nothing an approval could review and nothing carrying user "
        "content to secret-scan. The sweep sees the statement now as well as the method; both get "
        "the same honest answer.",
    ("memory/backends.py", "PostgresBackend.record_usage"): "DB.S5 — the SHARED table's telemetry "
        "`UPDATE` (backends.py:1613), the Postgres twin of the local one above and the same "
        "answer: two counter columns, no approved content, nothing to secret-scan. It is "
        "deliberately NOT journalled — a monotonic counter has no CAS conflict to resolve, and "
        "un-approved rows do not belong in an approval ledger (the D5 policy, as stated on "
        "`MemoryStore.record_usage`).",
    ("memory/backends.py", "PostgresBackend.list_projects"): "SI.6-DB — `SELECT DISTINCT project`, for "
        "`--list-projects`. Read-only.",

    # ---- memory/vector.py — the opt-in pgvector index. Its put/delete are GATED above.
    ("memory/vector.py", "PgVectorBackend.get"): "SI.6-DB — `SELECT doc FROM <vector table> WHERE id=%s`, project-"
        "scoped. The MemoryBackend read contract on the vector index; no write path.",
    ("memory/vector.py", "PgVectorBackend.all"): "SI.6-DB — `SELECT doc … ORDER BY seq`, project-scoped. Read-only.",
    ("memory/vector.py", "PgVectorBackend.semantic_search"): "DB.S4 — the cosine-distance nearest-neighbour "
        "`SELECT … ORDER BY embedding <=> %s LIMIT %s`. It embeds the QUERY in memory to compare "
        "against stored vectors and stores neither the query nor the result; `MAX_TOP_K` bounds "
        "its cost, not its authority.",
    ("memory/vector.py", "PgVectorBackend.list_projects"): "SI.6-DB — `SELECT DISTINCT project` on the vector "
        "table. Read-only.",
    ("memory/vector.py", "PgVectorBackend.read_stamp"): "DB.S4 — `SELECT embedder, dim FROM <stamp table>`: reads "
        "the (embedder, dim) this index was built with, so a mismatch can be refused. Read-only, "
        "and deliberately permissive — an unreadable stamp reads as UNSTAMPED, the documented "
        "pre-DB.S4 behaviour.",
    ("memory/vector.py", "PgVectorBackend.write_stamp"): "DB.S4 — upserts the index's single `(embedder, dim)` "
        "stamp row as the LAST step of a re-embed. It is the PROVENANCE OF A DERIVED INDEX, not a "
        "memory fact: no item, no subject, no value, nothing a human approved — two identifiers "
        "describing how the vectors beside it were computed. It rides the re-embed that produced "
        "those vectors, and `memory/reembed.py:run_reembed` above is the entry that carries that "
        "flow's own preview→confirm gate and its ledger record.",

    # ---- session_transport.py — its write_bundle / delete_bundle are GATED above.
    ("session_transport.py", "PostgresTransport.read_bundle"): "SS.S5 — `SELECT blob … WHERE "
        "tag=%s` (session_transport.py:196): reads ONE session bundle back out. Read-only. "
        "(Under the bare key this entry also spoke for `_FileTransport.read_bundle`, which is NOT "
        "a site at all — it `open()`s with no mode flag, so the detector never sees it. Two "
        "transports, one sentence, and only one of them was ever in the register: exactly the "
        "collapse the qualified key removes.)",
    ("session_transport.py", "PostgresTransport.list_tags"): "SS.S5 — `SELECT tag … ORDER BY tag` (and the file "
        "transport's directory listing). Enumerates what exists; writes nothing.",
    ("session_transport.py", "PostgresTransport.list_projects"): "SS.S5 — `SELECT DISTINCT project` with bundles "
        "present, for `session --list-projects`. Read-only.",

    # ---- team_audit.py — the SHARED audit ledger. Same recursion answer as govern/ledger.py.
    ("team_audit.py", "SharedAuditLog.append"): "SI.6-DB — `INSERT` of one entry into the SHARED audit table. "
        "This is the team-mode twin of `govern/ledger.py:record` above and takes its answer: the "
        "audit ledger IS the record of gate decisions, so gating it would recurse — a write that "
        "must be approved before the approval can be recorded has no bottom. Append-only by "
        "construction (never updates or deletes an existing row), so two actors publishing "
        "concurrently both survive, and every row it writes describes a decision that was ALREADY "
        "made and already gated.",
    ("team_audit.py", "SharedAuditLog.max_seq"): "SI.6-DB — `SELECT MAX(seq) … WHERE namespace=%s AND actor=%s`, "
        "so a re-publish only appends what is new. Read-only idempotence check.",
    ("team_audit.py", "SharedAuditLog.read"): "SI.6-DB — `SELECT namespace, actor, entry … ORDER BY id`: the "
        "team-wide who-did-what read. Read-only.",
    ("team_audit.py", "SharedAuditLog.list_projects"): "SI.6-DB — `SELECT DISTINCT namespace` (the shared project "
        "key) for `audit --team --list-projects`. Read-only.",

    # ---- team_journal.py — its apply_memory_write / _close_edges_for are GATED above.
    ("team_journal.py", "_read_remote"): "SI.6-DB — reads the remote row for a key so a CAS miss "
        "can be reported as a CONFLICT with the other state attached, instead of a silent "
        "overwrite. Read-only, and it is what makes the conflict honest.",
    ("team_journal.py", "_edges_present"): "DB.S7a — `SELECT to_regclass(%s)` for the shared edges "
        "table: 'has this DB been provisioned for v5 edges'. Capability probe, read-only.",
    ("team_journal.py", "live_recover"): "SI.6-DB — `SELECT id FROM <memory table>` to learn which "
        "floor rows are already remote before enqueueing the stranded ones. Read-only here; the "
        "enqueue it feeds is a journal append, and the journal's own posture is `_append_all` "
        "above. An unreadable remote id-set degrades to empty, which re-enqueues and lands as "
        "already-applied (MS.S5) — never a duplicate.",

    # ---- teamdb.py — the shared-schema owner + the connection probe.
    ("teamdb.py", "_run_ddl"): "SI.6-DB — the ONLY schema writer in mokata (a D1 AST guard "
        "enforces that every caller is an init path). It runs an idempotent DDL pass — `CREATE "
        "TABLE IF NOT EXISTS` and friends — against a DSN the operator named, at the operator's "
        "explicit `team provision` command. It creates mokata's own EMPTY tables: no repo file "
        "changes, no memory item, no user content, nothing to secret-scan and nothing for a "
        "reviewer to compare. It is the shared-DB analogue of `memory/backends.py:__init__` "
        "directly above, which provisions the local store the same way. Any value that is not a "
        "mokata constant travels as a BOUND parameter, never interpolated. It fails closed on any "
        "DDL error. (The one non-DDL statement in the pass is DB.S7a's single-scalar report SELECT, "
        "deliberately inside the same `try` so a failed read fails the pass.)",
    ("teamdb.py", "_read_schema_version"): "SI.6-DB — reads the shared schema version (with a "
        "legacy-shape fallback) to decide whether this client and this DB agree. Read-only.",
    ("teamdb.py", "table_present"): "SI.6-DB — `SELECT to_regclass(%s)`. Catalogue read.",
    ("teamdb.py", "probe._work"): "SI.6-DB — `SELECT 1`, the reachability round-trip inside the "
        "connection probe. The smallest possible read; it exists to prove the DB answers.",

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
    ("memory/migrate.py", "_drop_source"): "0.0.17 STAGE 16 (MIGRATE-DROP-SOURCE-UNLEDGERED, doc "
        "84) — the `--drop-source` DELETE, and the entry exists at all because the drop was split "
        "out of `migrate_memory` to earn it. It DESTROYS every migrated item from the source store "
        "(`source.delete(it.id)`, migrate.py:231). Its consent is a bespoke BATCH confirm — "
        "`_default_drop_confirm` (migrate.py:176) -> `read_yes_no`, fail-closed off a TTY (checked, "
        "not assumed: `prompt.read_yes_no` returns False WITHOUT prompting when stdin is not a TTY, "
        "so the `drop_confirm or _default_drop_confirm` resolution is fail-closed one layer down "
        "rather than by construction; pinned by "
        "test_stage16_migrate_drop_ledgered.TestTheBespokeDropConsentIsFailClosed) — and "
        "`assume_yes` skips the prompt as the explicit non-interactive approval, the same posture "
        "`run_reembed` takes. Stage 16 adds the missing half: a `migrate_drop_source` ledger "
        "record. SECRET-SAFE by `run_reembed`'s rule verbatim — the source TOOL NAME and COUNTS "
        "(`items` actually deleted, `attempted`, `complete`), NEVER an item's subject or value, "
        "because a path that deletes memory items must not echo what it deleted into a durable "
        "record. Written in a `finally` so a drop that dies halfway is recorded as the PARTIAL it "
        "was rather than not at all; a refusal or a decline records nothing, because a record is a "
        "claim that something was destroyed. "
        "WHY LEDGERED AND NOT GATED: bespoke consent + an audit record + no WriteGate IS this "
        "register (the `run_reembed` shape). The ruling (Jas, doc 84) was explicit that the "
        "consent is real and deliberate — a batch drop is not a per-item write — so only the audit "
        "trail was added; the consent is UNCHANGED. "
        "WHY IT IS A SEPARATE FUNCTION AND NOT A BLOCK IN `migrate_memory`: the sweep keys by "
        "(file, QUALIFIED enclosing name), so sharing a key with the gate-run `dest.put` forced "
        "ONE sentence to describe TWO regimes — the collision that let the false `--drop-source` "
        "clause survive inside the GATED entry above from SI.6 until F3c. One entry, one "
        "definition, one regime. "
        "The same-store and pending/conflict refusals live INSIDE `_drop_source` (:212-:218) with "
        "the delete they protect, not in the caller: a destructive helper whose safety guards sit "
        "one frame up is one refactor away from being unsafe. Their behaviour is unchanged — "
        "dropping while writes are journaled is the 'partially migrated then lost' case the "
        "command promises never to cause.",
    ("memory/reembed.py", "run_reembed"): "DB.S4 — MOVED HERE FROM UNGATED_BY_DESIGN at the 0.0.17 "
        "review, and its own text is what moved it: the entry read 'human-gated, but NOT through "
        "the WriteGate' and 'it is ledgered', which is the LEDGERED definition VERBATIM — filed "
        "under a header that says 'not a governed durable write'. That is the same "
        "self-contradiction the coherence contract convicted `migrate_memory` of on its first run "
        "(GATED, above); this is its second instance, and finding the second by hand is what says "
        "the first was a pattern rather than a one-off. "
        "What it actually does, unchanged: it re-derives each item's EMBEDDING from that item's "
        "own already-gated subject/value — a recomputed index, like rebuilding an FTS index, not a "
        "change to what memory SAYS — by calling `backend.put(item)` per item at reembed.py:124 "
        "and re-stamping the index at :128. It carries its own preview→confirm gate (count + both "
        "embedder names, `read_yes_no`, fail-closed off a TTY; `assume_yes` is the explicit "
        "non-interactive approval) because it is a bulk rewrite of shared infrastructure, and it "
        "leaves a `memory_reembed` ledger record (embedder name + count, never memory content). "
        "Bespoke consent + an audit record + no WriteGate IS this register. If it ever gained the "
        "ability to change item CONTENT, it would belong in GATED instead.",
    ("init.py", "InitPlan.write_files"): "KB.S1 — writes manifest/constitution/.gitignore under init_repo's "
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

# ---- register 4b: DECLARED CALLERS. The caller list as DATA, for the entries that name one.
#
# CALLER-LIST-UNPINNED (doc 84, filed at the 0.0.17 stage 1a review). Several entries above earn
# their CLASSIFICATION from a sentence like "sole callers are the two gated session_bundle
# committers (SS.S5-verified)" — and every such sentence anyone checked was FALSE. Stage 1a
# corrected four by hand; deriving them instead immediately produced a fifth (`vault.py:commit_push`
# named a file that does not contain the symbol at all). Hand-written caller prose is what is being
# retired here.
#
# THE SPLIT THAT MAKES THIS NOT-PROSE. This table lists WHERE each caller is — nothing more. It
# does NOT say whether that caller is gated: that verdict is DERIVED from source by
# `_writegraph.gate_enclosure`, per call site, every run. So adding a caller here cannot excuse it;
# if the new call site is not gate-enclosed, direction B (below) reds. A table whose rows carried
# their own verdicts would just be the prose again in a dict.
#
# SCOPE, and why it is not every entry. A declared caller must be CHECKABLE, which means a
# `file.py:LINE` a reader and the parser can both go and look at. Entries whose caller is named
# only by ROLE — "reached from store._commit", "from within the gated push/claim flows" — are NOT
# in this table, because there is nothing there to verify; they are counted in the UNPINNABLE
# residual the report prints, rather than given a row that would look checked and not be.
DECLARED_CALLERS = {
    ("session_transport.py", "_FileTransport.write_bundle"): (
        ("session_bundle.py", 695), ("session_bundle.py", 908), ("migrate_channels.py", 352)),
    ("session_transport.py", "PostgresTransport.write_bundle"): (
        ("session_bundle.py", 695), ("session_bundle.py", 908), ("migrate_channels.py", 352)),
    ("session_transport.py", "_FileTransport.delete_bundle"): (("session_bundle.py", 909),),
    ("session_transport.py", "PostgresTransport.delete_bundle"): (("session_bundle.py", 909),),
    ("vault.py", "commit_push"): (("cli_commands/collab.py", 109), ("mcp/tools_share.py", 59)),
    ("memory/vector.py", "PgVectorBackend.put"): (("memory/reembed.py", 124),),
}

# ---- DIRECTION B's declared exceptions. A GATED entry whose own declared caller list contains a
# call site that is NOT gate-enclosed contradicts `GATED`'s header ("executes inside a `commit=`
# callable handed to WriteGate.submit"). That is GATED-ADMITS-UNGATED-CALLER (doc 84).
#
# THE RULING (0.0.17 stage 1a-FU): do NOT reclassify and do NOT weaken the header. Loosening it to
# "the governed path this entry describes, other callers enumerated" would license every future
# entry to hide an ungated caller behind prose — the exact collapse this stage exists to end. The
# defect is that the register keys the WRITER while governance is a property of the PATH, and it
# resolves when the ungated path is routed through the gate. So the falsehood is made VISIBLE and
# DATED here instead of papered over: an undeclared exception is the defect, a declared one with an
# expiry is a schedule.
#
# ★ THE EXPIRY IS SELF-EXECUTING, which is the part that matters. `expires_when` is not a note
# someone has to remember — `test_a_declared_exception_dies_the_moment_its_caller_is_gated` re-reads
# the call site every run and REDS if it has become gate-enclosed. The fix deletes the exception;
# nobody has to.
#
# ⚠ ONE MEMBER, DOWN FROM TWO (0.0.17 stage 17). `VAULT-REHOME-UNGATED-WRITE-BUNDLE` was DELETED
# HERE, and the deletion was not remembered by anyone: stage 17 routed
# `migrate_channels._migrate_vault` through the WriteGate, and on the very next run
# `test_a_declared_exception_dies_the_moment_its_caller_is_gated` went RED with "EXCEPTION
# 'VAULT-REHOME-UNGATED-WRITE-BUNDLE' HAS EXPIRED — DELETE IT". That red is the mechanism working,
# and it is the whole return on building the expiry as a predicate over src/ instead of a note in
# prose. The `write_bundle` twins' entries below no longer name a third, ungated caller, because
# there is no longer one.
GATED_CALLER_EXCEPTIONS = {
    "REEMBED-UNGATED-VECTOR-PUT": {
        "sites": (("memory/vector.py", "PgVectorBackend.put"),),
        "caller": ("memory/reembed.py", 124),
        "filed": "GATED-ADMITS-UNGATED-CALLER (doc 84) — ⚠ THE SECOND INSTANCE, AND IT WAS NOT IN "
                 "THE STAGE'S SCOPE: the 1a-FU ruling described ONE exception and asked that the "
                 "set be pinned at exactly one member. Built as instructed, the instrument found "
                 "TWO, and this is the one that was not expected. It is not a new fact — stage 1a's "
                 "fix F3b already corrected this entry to admit that `memory/reembed.py:124` calls "
                 "`backend.put(item)` directly — but it was recorded as prose and NOT filed as an "
                 "instance of the pattern, because the doc-84 row was written about `write_bundle` "
                 "alone. Derived, not believed: reembed.py:124 is not gate-enclosed. `run_reembed` "
                 "is LEDGERED — bespoke preview→confirm consent plus a `memory_reembed` ledger "
                 "record — so the write is GOVERNED, but not by the WriteGate, and `GATED`'s header "
                 "is a statement about the WriteGate specifically. Pinning the set at one would "
                 "have meant suppressing this to match a predicted count, which is the same 'one "
                 "green cell standing for several facts' the whole stage exists to end.",
        "expires_when": "the `put` call at memory/reembed.py:124 becomes gate-enclosed (or the "
                        "entry is split so the reembed path is described by its own class).",
    },
}


# ---- register 4c: CALLER PINS. DERIVED BY THE CALL GRAPH, committed so a new caller REDS.
#
# This is CALLER-LIST-UNPINNED's mechanism (doc 84), and it is deliberately NOT a hand-written
# claim: every row below was produced by `_writegraph.derive_callers` from the resolved graph and
# pasted whole. Nobody asserted any of it, so nobody can have asserted it wrongly — the only thing
# a human did was agree to freeze it. What that buys: a NEW caller of a registered writer changes
# this map, the pin mismatches, and the sweep goes RED naming the caller. Before this, a new caller
# silently falsified whichever justification happened to mention callers, and nothing anywhere
# noticed — which is how four register entries came to make caller claims that were all false.
#
# ⚠ READ THE COVERAGE, NOT JUST THE PIN. 58 of the 135 registered sites have a caller the resolver
# can see. The other 77 have NONE — not because nothing calls them, but because they are called
# through shapes in `_writegraph.UNCLOSED_SHAPES`: a `MemoryBackend` contract dispatch
# (`self.backend.put(item)`), a factory-bound transport (`dst.write_bundle(...)`), a callable handed
# to a gate. Both `write_bundle` twins are in that 77 — the very entries whose false caller list
# started this — so this mechanism WOULD NOT HAVE CAUGHT THEM, and saying so is the point. What
# covers those is `DECLARED_CALLERS` above, checked by `gate_enclosure` instead of by the graph.
# The 77 are counted and printed every run rather than left as an impression of completeness.
CALLER_PINS = {
    ("agent_skills.py", "prune_orphan_skills"): (
        ("agent_skills.py", "_regenerate_plugin_skills"),
        ("harness_setup.py", "apply_setup"),
        ("harness_setup.py", "apply_unsetup"),
    ),
    ("agent_skills.py", "write_skill_files"): (
        ("agent_skills.py", "_regenerate_plugin_skills"),
        ("harness_setup.py", "apply_setup"),
    ),
    ("atomicfile.py", "atomic_write_text"): (
        ("config_cmd.py", "config_set._commit"),
        ("flush_liveness.py", "store_state"),
        ("flush_liveness.py", "update_state"),
        ("govern/secret_ignore.py", "_gated_write._commit"),
        ("init.py", "InitPlan.write_files"),
        ("knowledge/anchor_fingerprints.py", "_write_record"),
        ("knowledge/graph_adopt.py", "adopt_graph._commit"),
        ("session_transport.py", "_FileTransport.write_bundle"),
        ("share.py", "apply_manifest._commit"),
        ("state.py", "StateStore._atomic_write"),
        ("team.py", "_gated_write._commit"),
        ("team.py", "_join_vault._commit"),
        ("team_health.py", "store"),
        ("team_journal.py", "TeamJournal.compact"),
        ("vault.py", "_save_index"),
        ("vault.py", "commit_push"),
        ("visibility.py", "capture_session_snapshot"),
    ),
    ("dashboard.py", "write_dashboard"): (
        ("cli_commands/runviews.py", "cmd_watch"),
        ("mcp/tools_read.py", "watch"),
    ),
    ("dashboard.py", "write_governance_dashboard"): (
        ("cli_commands/runviews.py", "cmd_govern"),
        ("mcp/tools_read.py", "govern"),
    ),
    ("extras_install.py", "record_extra_decline"): (
        ("extras_install.py", "offer_extra"),
    ),
    ("flush_liveness.py", "clear_state"): (
        ("flush_liveness.py", "flush_with_liveness"),
    ),
    ("flush_liveness.py", "update_state"): (
        ("flush_liveness.py", "flush_with_liveness"),
    ),
    ("govern/ledger.py", "AuditLedger._write_counter"): (
        ("govern/ledger.py", "AuditLedger.record"),
    ),
    ("govern/lifecycle.py", "_remove"): (
        ("govern/lifecycle.py", "reset_state"),
    ),
    ("govern/lifecycle.py", "_write_tombstone"): (
        ("govern/lifecycle.py", "reset_state"),
    ),
    ("harness_setup.py", "_write_command_file"): (
        ("harness_setup.py", "apply_setup"),
    ),
    ("harness_setup.py", "_write_json"): (
        ("harness_setup.py", "_merge_grant"),
        ("harness_setup.py", "_merge_hooks"),
        ("harness_setup.py", "_merge_mcp"),
        ("harness_setup.py", "_merge_statusline"),
        ("harness_setup.py", "_write_or_remove_json"),
    ),
    ("injection_ledger.py", "record_injected"): (
        ("hook_cli.py", "user_prompt_submit_main"),
    ),
    ("knowledge/anchor_fingerprints.py", "_write_record"): (
        ("knowledge/anchor_fingerprints.py", "record_anchors"),
    ),
    ("knowledge/ast_backend.py", "AstBackend._save_cache"): (
        ("knowledge/ast_backend.py", "AstBackend._ensure_index"),
    ),
    ("knowledge/freshness.py", "drain_dirty"): (
        ("knowledge/freshness.py", "FreshnessController._reconcile"),
    ),
    ("knowledge/freshness.py", "mark_dirty"): (
        ("hook_cli.py", "dirty_track_main"),
    ),
    ("knowledge/user_prefs.py", "record_graph_decline"): (
        ("knowledge/graph_adopt.py", "offer_graph_at_setup"),
    ),
    ("memory/_sqlite.py", "_apply_pragmas"): (
        ("memory/_sqlite.py", "connect_sqlite"),
    ),
    ("memory/_sqlite.py", "_current_mode"): (
        ("memory/_sqlite.py", "_switch_to_wal"),
    ),
    ("memory/_sqlite.py", "_switch_to_wal"): (
        ("memory/_sqlite.py", "_apply_pragmas"),
    ),
    ("memory/backends.py", "PostgresBackend._put_on"): (
        ("memory/backends.py", "PostgresBackend.put"),
    ),
    ("memory/backends.py", "PostgresBackend._read_edges_present"): (
        ("memory/backends.py", "PostgresBackend.supports_edges"),
    ),
    ("memory/backends.py", "PostgresBackend._read_scope_backfilled"): (
        ("memory/backends.py", "PostgresBackend.supports_scope_pushdown"),
    ),
    ("memory/backends.py", "SQLiteBackend._backfill_edges"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._backfill_lifecycle_columns"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._backfill_scope_columns"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._ensure_edges"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._ensure_fts"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._ensure_lifecycle_columns"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._ensure_scope_columns"): (
        ("memory/backends.py", "SQLiteBackend.__init__"),
    ),
    ("memory/backends.py", "SQLiteBackend._put_on"): (
        ("memory/backends.py", "SQLiteBackend.put"),
    ),
    ("memory/backends.py", "SQLiteBackend.all"): (
        ("team_journal.py", "_floor_rows"),
    ),
    ("memory/edges.py", "project_edges"): (
        ("memory/backends.py", "PostgresBackend._put_on"),
        ("memory/backends.py", "SQLiteBackend._put_on"),
        ("team_journal.py", "_project_edges_for"),
    ),
    ("memory/migrate.py", "_drop_source"): (
        ("memory/migrate.py", "migrate_memory"),
    ),
    ("memory/reembed.py", "run_reembed"): (
        ("cli_commands/memory.py", "_memory_reembed"),
    ),
    ("memory/store.py", "MemoryStore.record_usage"): (
        ("memory/store.py", "MemoryStore.recall_relevant"),
    ),
    ("memory/vector.py", "PgVectorBackend.read_stamp"): (
        ("memory/vector.py", "PgVectorBackend.verify_stamp"),
    ),
    ("migrate_channels.py", "_write_marker"): (
        ("migrate_channels.py", "run_channel_migration"),
    ),
    ("plans.py", "write_plan_file"): (
        ("brainstorm.py", "save_approach_plan"),
    ),
    ("plugin_cache.py", "record_plugin_root"): (
        ("hook_cli.py", "_record_plugin_root"),
    ),
    ("share.py", "export_manifest"): (
        ("mcp/tools_config.py", "export_stack"),
        ("mcp/tools_read.py", "export_preview"),
    ),
    ("state.py", "StateStore._atomic_write"): (
        ("state.py", "StateStore.update"),
        ("state.py", "StateStore.write"),
    ),
    ("team_health.py", "store"): (
        ("team_health.py", "check"),
    ),
    ("team_journal.py", "TeamJournal._append_all"): (
        ("team_journal.py", "TeamJournal._append"),
        ("team_journal.py", "TeamJournal.resolve_group"),
    ),
    ("team_journal.py", "TeamJournal.compact"): (
        ("team_journal.py", "TeamJournal.compact_if_needed"),
    ),
    ("team_journal.py", "_close_edges_for"): (
        ("team_journal.py", "apply_memory_write"),
    ),
    ("team_journal.py", "_edges_present"): (
        ("team_journal.py", "_close_edges_for"),
        ("team_journal.py", "_project_edges_for"),
    ),
    ("team_journal.py", "_read_remote"): (
        ("team_journal.py", "_mark_group_conflicted"),
        ("team_journal.py", "apply_memory_write"),
    ),
    ("team_journal.py", "apply_memory_write"): (
        ("team_journal.py", "_apply_approval_group._apply"),
    ),
    ("team_journal.py", "live_recover"): (
        ("cli_commands/sync.py", "cmd_sync"),
    ),
    ("teamdb.py", "_read_schema_version"): (
        ("teamdb.py", "probe._work"),
    ),
    ("teamdb.py", "_run_ddl"): (
        ("teamdb.py", "provision"),
        ("teamdb.py", "provision_vector"),
    ),
    ("teamdb.py", "table_present"): (
        ("teamdb.py", "ensure_schema"),
    ),
    ("vault.py", "_save_index"): (
        ("vault.py", "commit_push"),
        ("vault.py", "update_index"),
    ),
    ("vault.py", "commit_push"): (
        ("cli_commands/collab.py", "cmd_vault"),
        ("mcp/tools_share.py", "vault_push"),
    ),
    ("vault.py", "vault_pull"): (
        ("cli_commands/collab.py", "cmd_vault"),
        ("mcp/tools_read.py", "vault_pull"),
        ("team.py", "_join_vault"),
    ),
    ("visibility.py", "capture_session_snapshot"): (
        ("hook_cli.py", "session_start_main"),
    ),
}

def _declared_directions():
    """THE COHERENCE CONTRACT'S COVERAGE, rendered for a reader who is not its author.

    COHERENCE-CONTRACT-ONE-DIRECTIONAL (doc 84). The contract ran ONE direction — a write-freedom
    claim that reaches a governed write — and that direction convicted `migrate_memory` on its
    first run, which is the only reason the self-contradiction pattern is known at all. It is
    STRUCTURALLY INCAPABLE of seeing the inverse: a GATED entry whose own caller list admits an
    ungated caller. That is why the sweep was green with a knowably-false entry in it.

    An instrument whose coverage must be re-derived from its CALL SITE is the audited defect class
    one level up, so the directions ride the failure message and the report the way
    `_writegraph.UNCLOSED_SHAPES` does — rendered from one source, never paraphrased."""
    lines = ["DIRECTIONS THIS CONTRACT RUNS (declared, not re-derived from the call site):"]
    lines += [f"  - {d}" for d in _writegraph.CONTRACT_DIRECTIONS]
    lines.append("  gate enclosure is established ONLY through: "
                 + "; ".join(_writegraph.GATE_RUNNERS))
    lines += [f"  - NOT ESTABLISHED: {u}" for u in _writegraph.UNSEEN_ENCLOSURE]
    return "\n".join(lines)


def _declared_blind_spots(collisions=None):
    """The instruments' DECLARED limits, rendered for a reader who is not their author.

    This exists because of the 0.0.17 review's finding on the failure message: the sweep told the
    reader "a new durable write appeared in src/ and nobody said whether it is gated", which
    promises COMPLETENESS, while the module docstring quietly held a list of ten ordinary write
    shapes that evade the detector. A caveat only the author reads is not a declared caveat, and
    the gap between "everything it sees is real" and "it sees everything" is exactly the SI.6
    overclaim this whole stage exists to stop repeating in a new place.

    So the list rides the FAILURE MESSAGE and the AUDIT REPORT, from one source
    (`_writegraph.UNCLOSED_SHAPES`), rendered rather than paraphrased. `collisions` — the keys that
    STILL cover more than one definition after qualification — is measured per run and appended,
    because that residual moves with the code and a hard-coded sentence about it would rot."""
    lines = ["WHAT THIS SWEEP CANNOT SEE (declared, not discovered later — P16). Its honest claim",
             "is 'everything it sees is real', NEVER 'it sees everything'. Confirmed shapes that",
             "reach disk without becoming a site above:"]
    lines += [f"  - {shape}" for shape in _writegraph.UNCLOSED_SHAPES]
    if collisions:
        lines.append(f"  KEY COLLISIONS still live in src/ ({len(collisions)}): "
                     + ", ".join(f"{rel}:{fn} [{n} definitions]"
                                 for (rel, fn), n in sorted(collisions.items())))
    else:
        lines.append("  KEY COLLISIONS still live in src/: none this run (measured, not assumed).")
    return "\n".join(lines)


def _durable_write_sites(root=None):
    """Every DIRECT durable-write call site in src/, keyed by (file, QUALIFIED enclosing name).

    The key is the dotted scope path — `SQLiteBackend.put`, `MemoryStore.remember._commit` — and
    NOT the bare innermost name it used to be. That change is the 0.0.17 review's F1: a bare key
    made two definitions of one name in one file share a single entry and a single sentence
    (measured: 15 of 112 keys, worst `memory/store.py:_commit` at EIGHT commit closures), and a
    planted `ExfilBackend.put` appended to `memory/backends.py` therefore left the site count at
    112 and the sweep GREEN. One entry now describes one definition.

    `root` exists so the self-guard tests can point the detector at a planted package and prove the
    sweep can go RED — an audit that has never been observed failing is an audit nobody has tested.
    """
    root = SRC if root is None else root
    sites = {}

    class V(ast.NodeVisitor):
        def __init__(self, rel):
            self.rel, self.stack = rel, []

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

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
            elif attr in _SQL_EXEC:
                label = f"sql.{attr}"          # ANY receiver, NO statement-text filter — see _SQL_EXEC
            if label:
                # a lambda's write belongs to the named function that owns it — that IS the
                # commit-closure shape the gate runs
                key = (self.rel, ".".join(self.stack) if self.stack else "<module>")
                sites.setdefault(key, []).append(f"{label}@{node.lineno}")
            self.generic_visit(node)

    for base_dir, _dirs, files in os.walk(root):
        if "__pycache__" in base_dir:
            continue
        for name in sorted(f for f in files if f.endswith(".py")):
            p = os.path.join(base_dir, name)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, encoding="utf-8") as fh:
                v = V(rel)
                v.visit(ast.parse(fh.read(), filename=p))
    return sites


# ---- register 5: DERIVED. NOT A DICT, AND THAT IS THE POINT (SI.6-DELEGATED-BLINDNESS).
#
# The four registers above are ASSERTED BY A HUMAN: a hand-written sentence per site saying why
# that write is gated, by-design, ledgered or filed. This fifth class is DERIVED BY THE CLOSURE and
# has no hand-written text at all, because a hand-written justification for a machine-derived fact
# is where the lies start — it is a sentence nobody re-checks attached to a claim nobody re-runs.
#
# A derived site is a function that reaches a durable write by DELEGATING to one. It INHERITS the
# classification of the writer(s) it reaches, and it records THE PATH, because a delegated
# classification the reader cannot trace is one they will rubber-stamp. `_ask_group` is the shape:
# an asker whose whole charter was "writes nothing", with `journal._append` one call away.
#
# Structural consequence, pinned by `test_the_derived_register_is_never_hand_written` below: a
# derived site can NEVER carry a hand-written entry, because every registered site is a DIRECT site
# and `derived` is the closure MINUS the direct sites. So "did a human assert this, or did the
# machine derive it?" is answerable at a glance, by which list the site is in.
_GRAPH_CACHE = {}


def _write_closure():
    """The resolved-call-graph closure over the direct write sites — cached (it parses all of src/)."""
    if "closure" not in _GRAPH_CACHE:
        graph = _writegraph.build_call_graph(SRC)
        _GRAPH_CACHE["graph"] = graph
        _GRAPH_CACHE["closure"] = _writegraph.close_over_writers(graph, _durable_write_sites())
    return _GRAPH_CACHE["graph"], _GRAPH_CACHE["closure"]


def _gate_enclosure():
    """Where each call RUNS — cached alongside the graph it is derived with."""
    if "enclosure" not in _GRAPH_CACHE:
        graph, _closure = _write_closure()
        _GRAPH_CACHE["enclosure"] = _writegraph.gate_enclosure(SRC, graph=graph)
    return _GRAPH_CACHE["enclosure"]


def _excused_sites():
    return {site for exc in GATED_CALLER_EXCEPTIONS.values() for site in exc["sites"]}


def _classes_for(site, closure):
    """The register classes a DERIVED site inherits, via the direct writers it can reach."""
    names = []
    for writer in sorted(closure.writers.get(site, ())):
        for label, reg in (("GATED", GATED), ("UNGATED_BY_DESIGN", UNGATED_BY_DESIGN),
                           ("LEDGERED", LEDGERED), ("KNOWN_BYPASS", KNOWN_BYPASS)):
            if writer in reg and label not in names:
                names.append(label)
    return names


class TestZeroBypass(unittest.TestCase):
    """The release exit criterion, as a permanent CI guard.

    Every durable-write call site in src/ must be REGISTERED — gated, ungated-by-design, or a known
    filed bypass. A writer nobody classified is the failure mode this exists to prevent: that is how
    `apply_consolidation` (C1) sat outside the gate for six stages while every doc said otherwise."""

    def test_every_durable_write_site_in_src_is_registered(self):
        sites = _durable_write_sites()
        graph, _closure = _write_closure()
        registered = (set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED)
                      | set(KNOWN_BYPASS))
        unregistered = sorted(set(sites) - registered)

        detail = "\n".join(f"    {rel}:{fn}  ({', '.join(sites[(rel, fn)])})"
                           for rel, fn in unregistered)
        self.assertEqual(
            unregistered, [],
            "UNREGISTERED DURABLE WRITER(S) — a durable write THIS SWEEP CAN SEE appeared in src/ "
            "and nobody said whether it is gated.\n\n" + detail + "\n\n"
            "Route it through a WriteGate (see memory/store.py:_gated_commit or "
            "mcp/tools_write.py:_gated_write) and add it to GATED; or, if it is genuinely not a "
            "governed durable write (temp_local runtime state, the ledger, a derived artifact), add "
            "it to UNGATED_BY_DESIGN with a one-line reason. If it really does bypass the gate, it "
            "goes in KNOWN_BYPASS *and* on the backlog — never let it pass silently.\n\n"
            + _declared_blind_spots(graph.collisions))

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

    def test_no_write_free_claim_delegates_to_a_governed_write(self):
        """THE COHERENCE CONTRACT (SI.6-DELEGATED-BLINDNESS) — the stage's real deliverable, and the
        thing that would have caught `_ask_group`.

        A site registered UNGATED_BY_DESIGN has a human's sentence attached saying it is NOT a
        governed durable write. If the resolved call graph shows that same site reaching a GATED or
        LEDGERED writer, the two statements CONTRADICT each other — the register says there is
        nothing here for a human to approve while the code says approved-content bytes land through
        a delegate. Neither half is wrong on its own, which is exactly why a vocabulary scan can
        never see it and why P10 needed a whole-tree byte comparison to catch one instance.

        Today the set is EMPTY, and this asserts it stays empty. That is worth pinning precisely
        BECAUSE it is currently a coincidence rather than a contract: nothing before this stage
        stopped a write-freedom claim from acquiring a delegated write underneath it."""
        graph, _closure = _write_closure()
        violations = _writegraph.coherence_violations(
            graph, set(UNGATED_BY_DESIGN), set(GATED) | set(LEDGERED))
        # ^ LEDGERED is a governed target too, which is why moving `memory/reembed.py:run_reembed`
        #   into it (0.0.17 review) tightened this contract rather than loosening it.
        detail = "\n".join(f"    {rel}:{fn}\n        {_writegraph.render_path(path)}"
                           for (rel, fn), path in violations)
        self.assertEqual(
            violations, [],
            "INCOHERENT REGISTER ENTRY(-IES) — a site whose register entry CLAIMS it is not a "
            "governed durable write reaches one anyway, by delegation:\n\n" + detail + "\n\n"
            "One of the two is false. Either the write really is governed (move the entry to "
            "GATED / LEDGERED and say which gate runs it), or the delegation is a bug and the call "
            "should not be there. Do NOT resolve this by widening the by-design reason to cover "
            "the delegate — that is how `apply_consolidation` stayed 'governed' in prose for six "
            "stages (74 C1), and how an asker whose charter was 'writes nothing' shipped with a "
            "`journal._append` inside it (DB.S7d P10).\n\n"
            "AND NOTE WHAT AN EMPTY RESULT DOES NOT MEAN. This contract sees only edges the "
            "resolver could resolve; a write-freedom claim that delegates through one of the "
            "shapes below is a contradiction it cannot notice.\n"
            + _declared_directions() + "\n" + _declared_blind_spots(graph.collisions))

    def test_no_gated_entry_declares_an_ungated_caller(self):
        """THE COHERENCE CONTRACT, DIRECTION B (0.0.17 stage 1a-FU) — the direction that was
        missing, and the reason the sweep could be GREEN with a knowably-false entry in it.

        Direction A above asks whether a WRITE-FREE claim reaches a governed write. It convicted
        `migrate_memory` on its first run, which is the only reason the self-contradiction pattern
        is known at all — and it is STRUCTURALLY INCAPABLE of the inverse: a GATED entry whose own
        caller list admits a caller that is not gated. `GATED`'s header classifies by how the write
        is REACHED ("executes inside a `commit=` callable handed to WriteGate.submit"), so such an
        entry is false by its own register's definition.

        WHY THIS CANNOT BE DONE FROM THE CALL GRAPH, measured rather than assumed: BOTH
        `write_bundle` twins have ZERO resolvable callers (they are reached through a
        factory-bound transport, one of `UNCLOSED_SHAPES`). So the callers come from
        `DECLARED_CALLERS` — but the VERDICT on each does not: `gate_enclosure` re-reads the call
        site from source every run. Declaring a caller therefore cannot excuse it; only a named
        exception with an expiry can, and those are pinned by count below."""
        enclosure = _gate_enclosure()
        violations = _writegraph.ungated_caller_violations(
            enclosure, DECLARED_CALLERS, set(GATED), _excused_sites())
        detail = "\n".join(f"    {_writegraph.render_site(site)}\n"
                           f"        declared caller {rel}:{lineno} is NOT gate-enclosed"
                           for site, (rel, lineno) in violations)
        self.assertEqual(
            violations, [],
            "GATED ENTRY(-IES) WITH AN UNGATED DECLARED CALLER — the entry sits in a register "
            "whose header says the write executes inside a gate-run commit callable, and its own "
            "caller list names a call site that does not:\n\n" + detail + "\n\n"
            "Do NOT resolve this by weakening GATED's header to 'the governed path this entry "
            "describes, other callers enumerated' — that licenses every future entry to hide an "
            "ungated caller behind prose, which is the collapse this stage exists to end. Either "
            "route the caller through the gate, or file a DECLARED exception in "
            "GATED_CALLER_EXCEPTIONS carrying the stage that closes it and the condition that "
            "expires it. An undeclared exception is the defect; a declared one is a schedule.\n"
            + _declared_directions())

    def test_every_declared_caller_really_calls_what_it_is_declared_to_call(self):
        """A DECLARED caller is a `file.py:LINE` claim, so it is checkable — and it is checked.

        Without this, `DECLARED_CALLERS` would be the prose caller list again in a nicer container:
        a line number nobody re-reads drifts the moment the file above it grows, and a stale line
        would quietly stop matching any call at all — which, for a line declared UNGATED, would
        silently DELETE the violation and re-green the sweep. So the line must still hold a call
        spelled with the site's own method name."""
        enclosure = _gate_enclosure()
        for site, callers in sorted(DECLARED_CALLERS.items()):
            method = site[1].split(".")[-1]
            for rel, lineno in callers:
                names = enclosure.call_names(rel, lineno)
                self.assertIn(method, names,
                              f"{_writegraph.render_site(site)} declares a caller at {rel}:{lineno}"
                              f", but that line calls {sorted(names) or 'nothing'} — not "
                              f"`{method}`. The declaration has drifted off its line; re-derive it "
                              "rather than adjusting the number until it looks right.")

    def test_a_declared_exception_dies_the_moment_its_caller_is_gated(self):
        """★ THE EXPIRY IS SELF-EXECUTING, and that is the difference between a schedule and a note.

        Each exception exists because ONE call site is not gate-enclosed. That is a fact about
        src/, re-derived here every run: the moment stage 17 routes `_migrate_vault` through the
        WriteGate, this test REDS and says to delete the exception. Nobody has to remember. An
        exception that outlives its reason is an undeclared exception with a paper trail."""
        enclosure = _gate_enclosure()
        for name, exc in sorted(GATED_CALLER_EXCEPTIONS.items()):
            rel, lineno = exc["caller"]
            self.assertFalse(
                enclosure.is_gate_run(rel, lineno),
                f"EXCEPTION '{name}' HAS EXPIRED — DELETE IT. Its whole reason for existing is "
                f"that {rel}:{lineno} calls a GATED writer from outside the gate. That call is now "
                f"gate-enclosed, so the entry it excuses is true on every path and needs no "
                f"exception: {exc['expires_when']}")

    def test_the_declared_exception_set_is_pinned_and_every_member_carries_its_expiry(self):
        """An exception nobody counted grows. The count is pinned so adding one is a review event,
        and each member must name the stage that closes it and the condition that ends it —
        'declared' without an expiry is just a permanent excuse with better manners.

        ⚠ PINNED AT ONE, DOWN FROM TWO (0.0.17 stage 17) — and the arithmetic is worth reading in
        order. The 1a-FU ruling described a single exception (`write_bundle`) and asked for the set
        to be pinned at exactly one member. Built as specified, the instrument found TWO: the
        second was `PgVectorBackend.put`, whose caller `memory/reembed.py:124` stage 1a's own F3b
        correction already admitted in prose and which nobody had filed as an instance of the
        pattern. Pinning at one THEN would have meant deleting a true finding to match a predicted
        count. The set is one now for the opposite reason: stage 17 gated
        `migrate_channels._migrate_vault`, the `write_bundle` exception EXPIRED BY ITSELF, and it
        was deleted because its cause was gone. A count that arrives by closing a defect and a
        count that arrives by suppressing a finding look identical in the assertion and are
        opposites in fact — which is why this docstring records which one happened."""
        self.assertEqual(
            len(GATED_CALLER_EXCEPTIONS), 1,
            "the declared-exception set changed size. Every member is a KNOWN FALSEHOOD in the "
            "register being carried deliberately until a named stage closes it, so growing this "
            "set is a decision someone must take on purpose: " + str(sorted(GATED_CALLER_EXCEPTIONS)))
        for name, exc in sorted(GATED_CALLER_EXCEPTIONS.items()):
            self.assertTrue(exc["filed"].strip() and exc["expires_when"].strip(),
                            f"exception '{name}' must name where it is filed and what ends it")
            for site in exc["sites"]:
                self.assertIn(site, GATED, f"exception '{name}' excuses a non-GATED site {site}")
                self.assertIn(site, DECLARED_CALLERS,
                              f"exception '{name}' excuses {site}, which declares no callers — so "
                              "there is nothing for direction B to have flagged and the exception "
                              "is inert")

    def test_the_caller_list_of_every_registered_site_is_derived_not_asserted(self):
        """CALLER-LIST-UNPINNED's mechanism (doc 84): a NEW caller reds the sweep instead of
        silently falsifying a justification.

        Stage 1a corrected four false caller strings BY HAND. This is what stops the fifth — and
        there already was a fifth: deriving `vault.py:commit_push`'s callers showed its entry named
        `mcp/tools_write.py:471`, a file that does not contain the symbol at all. Nothing in the
        register is hand-written here; the map is `derive_callers`' output, frozen."""
        graph, _closure = _write_closure()
        registered = set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED) | set(KNOWN_BYPASS)
        derived = {s: c for s, c in _writegraph.derive_callers(graph, registered).items() if c}

        added = sorted(set(derived) - set(CALLER_PINS))
        dropped = sorted(set(CALLER_PINS) - set(derived))
        changed = sorted(s for s in set(derived) & set(CALLER_PINS)
                         if derived[s] != tuple(CALLER_PINS[s]))
        detail = "".join(
            [f"\n    NEW CALLER(S) reach {_writegraph.render_site(s)}: "
             f"{[_writegraph.render_site(c) for c in derived[s]]}" for s in added]
            + [f"\n    NO CALLER now resolves to {_writegraph.render_site(s)} (was "
               f"{[_writegraph.render_site(c) for c in CALLER_PINS[s]]})" for s in dropped]
            + [f"\n    CALLERS CHANGED for {_writegraph.render_site(s)}: "
               f"{[_writegraph.render_site(c) for c in CALLER_PINS[s]]} -> "
               f"{[_writegraph.render_site(c) for c in derived[s]]}" for s in changed])
        self.assertEqual(
            (added, dropped, changed), ([], [], []),
            "THE DERIVED CALLER LIST OF A REGISTERED WRITER CHANGED." + detail + "\n\n"
            "Re-read the register entry for each site above: if its justification depends on WHO "
            "calls it, that justification may now be false. Then update CALLER_PINS from "
            "`_writegraph.derive_callers` — never by hand, and never by editing the entry's prose "
            "to match, which is the failure this pin exists to end.\n"
            + _declared_directions())

    def test_the_unpinnable_caller_claims_are_counted_not_implied(self):
        """P16 again, on this instrument. Most registered sites have NO resolvable caller — 77 of
        135 — because they are reached by a contract dispatch, a factory-bound receiver or a
        callable handed to a gate. Both `write_bundle` twins are among them, which means the pin
        above would NOT have caught the very entries that motivated it.

        A coverage number that only its author knows is the SI.6 overclaim in a new place, so the
        residual is measured here and printed in the report. It is asserted as a bound rather than
        an exact figure: the point is that the majority are unpinnable and the reader is told so."""
        graph, _closure = _write_closure()
        registered = set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED) | set(KNOWN_BYPASS)
        derived = _writegraph.derive_callers(graph, registered)
        unpinnable = sorted(s for s, c in derived.items() if not c)

        self.assertEqual(len(unpinnable) + len(CALLER_PINS), len(registered),
                         "every registered site is either pinned or counted unpinnable — there is "
                         "no third state in which it is quietly neither")
        for twin in (("session_transport.py", "_FileTransport.write_bundle"),
                     ("session_transport.py", "PostgresTransport.write_bundle")):
            self.assertIn(twin, unpinnable,
                          f"{twin} now HAS a resolvable caller. That is good news and it "
                          "invalidates the sentence above: re-derive the coverage claim rather "
                          "than leaving a stale one in the report.")

    def test_rule_e_yield_is_stated_not_left_as_a_silent_ratio(self):
        """RULE (e) RESOLVES 36 CALLS AND ADDS 2 DERIVED SITES, and 36 -> 2 is a ratio a reader of
        a derived report is entitled to have explained rather than left to wonder at.

        THE EXPLANATION, and it is checkable, not a hand-wave: the rule's job is to resolve calls
        through a constructor-bound receiver, and MOST such receivers are not write-reaching at all
        — a `Path`, a planner, a formatter. Only the resolutions whose TARGET reaches a durable
        write can add anything to the closure, so the honest denominator is not 36. This asserts
        the whole chain: the count matches the recorded edges, the write-reaching subset is what it
        is, and the sites that appear are exactly those two.

        Both baselines were re-derived through `scripts/mutate.sh` with a measurement label rather
        than by hand (doc 85 §7b: "I am only measuring, not mutation-testing" is not an exemption):
        rule (e) ON gives 118 derived sites, the M05-shape mutation OFF gives 116.

        35 -> 36 AT 0.0.17 STAGE 21, re-derived rather than bumped. `KnowledgeIndex._iter_files`
        stopped being a `@staticmethod` (it now RECORDS the nested checkouts it skipped, which is
        per-walk state), so `freshness._cold_walk`'s unbound `KnowledgeIndex._iter_files(...)`
        became a receiver call on a locally-constructed `idx`. Exactly one edge appeared, its
        target writes nothing, and the `productive` list below is unchanged — which is the whole
        point of asserting the ratio's numerator and its write-reaching subset separately."""
        graph, closure = _write_closure()
        reaching = set(closure.direct) | set(closure.derived)

        self.assertEqual(len(graph.receiver_edges), graph.receiver_calls,
                         "the rule-(e) COUNT and the rule-(e) EDGE LIST disagree — a count with no "
                         "list can only be re-asserted, never re-graded")
        self.assertEqual(graph.receiver_calls, 36,
                         "rule (e)'s resolution count moved; re-derive its yield before trusting "
                         "any number in the audit report that depends on it")
        productive = sorted({caller for caller, _ln, target in graph.receiver_edges
                             if target in reaching})
        self.assertEqual(
            productive,
            [("mcp/tools_read.py", "session_save"), ("team_journal.py", "_floor_rows")],
            "of rule (e)'s 36 resolutions, exactly these reach a durable write — every other one "
            "binds a receiver whose class writes nothing, which is why 36 resolutions buy 2 sites "
            "rather than 35. If this list moved, the ratio in the report is stale.")
        for site in productive:
            self.assertIn(site, closure.derived,
                          f"{site} is rule (e)'s whole contribution to the register and it is not "
                          "in the derived set — the rule has gone inert")

    def test_the_derived_register_is_never_hand_written(self):
        """DERIVED sites are a distinct register CLASS, not more rows in GATED.

        A derived site's classification is inherited from the writer it reaches and its evidence is
        the path — it must never acquire a hand-written justification, because a hand-written
        sentence for a machine-derived fact is a claim nobody re-checks. This holds structurally
        (every registered site is a DIRECT site; derived is the closure minus the direct sites) and
        is pinned here so a future author cannot 'helpfully' paste a derived site into GATED to
        quieten something."""
        _graph, closure = _write_closure()
        asserted = set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED) | set(KNOWN_BYPASS)
        overlap = sorted(set(closure.derived) & asserted)
        self.assertEqual(
            overlap, [],
            "these sites are DERIVED (they reach a durable write only by delegating) yet carry a "
            f"hand-written register entry: {overlap}. A derived classification is inherited from "
            "the writer it reaches and evidenced by its path — it is not a sentence anyone writes.")
        self.assertTrue(closure.derived, "the closure found no delegated writers at all — that is "
                                         "not credible for this package and means the resolver is "
                                         "broken, not that the code got simpler")

    def test_every_derived_site_can_name_the_writer_it_reaches(self):
        """A delegated classification the reader cannot trace is one they will rubber-stamp, so the
        path is part of the deliverable, not a debugging aid."""
        _graph, closure = _write_closure()
        for site in closure.derived:
            path = closure.derived[site]
            self.assertGreaterEqual(len(path), 2, f"{site} is derived but has no path")
            self.assertEqual(path[0], site, f"{site}'s path does not start at itself")
            self.assertIn(path[-1], closure.direct,
                          f"{site}'s path ends at {path[-1]}, which is not a direct writer")
            self.assertTrue(_classes_for(site, closure),
                            f"{site} reaches a writer but inherits no register class")

    def test_the_audit_report(self):
        """Prints the disposition of every durable-write site. This runs in CI on every push, so the
        residual bypasses are in the output every time, until they are gone."""
        sites = _durable_write_sites()
        _graph, closure = _write_closure()
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
        lines.append("DERIVED — reaches a durable write by DELEGATION (SI.6-DELEGATED-BLINDNESS).")
        lines.append("  Machine-derived: no hand-written justification, classification INHERITED,")
        lines.append(f"  the path IS the evidence.  [{len(closure.derived)}]")
        for site in closure.derived:
            lines.append(f"  {site[0]}:{site[1]}   [{' + '.join(_classes_for(site, closure))}]")
            lines.append(f"      {closure.path_text(site)}")
        lines.append("\n" + "-" * 78)
        registered = set(GATED) | set(UNGATED_BY_DESIGN) | set(LEDGERED) | set(KNOWN_BYPASS)
        derived_callers = _writegraph.derive_callers(_graph, registered)
        unpinnable = [s for s, c in derived_callers.items() if not c]
        enclosure = _gate_enclosure()
        lines.append("CALLER LISTS — DERIVED, never asserted (CALLER-LIST-UNPINNED, doc 84). Stage")
        lines.append("  1a corrected four false caller strings by hand; deriving them found a")
        lines.append("  fifth (vault.py:commit_push named a file with no such symbol in it).")
        lines.append(f"  pinned from the call graph: {len(CALLER_PINS)} site(s)")
        lines.append(f"  UNPINNABLE — no caller the resolver can see: {len(unpinnable)} site(s), "
                     f"including BOTH `write_bundle` twins,")
        lines.append("    which is to say this mechanism would NOT have caught the entries that")
        lines.append("    motivated it. Those are covered by DECLARED_CALLERS + gate enclosure.")
        lines.append(f"  declared caller lists (file:line, checked against source): "
                     f"{len(DECLARED_CALLERS)} site(s), "
                     f"{sum(len(v) for v in DECLARED_CALLERS.values())} call site(s)")
        for site, callers in sorted(DECLARED_CALLERS.items()):
            for rel, ln in callers:
                verdict = "gate-enclosed" if enclosure.is_gate_run(rel, ln) else "NOT gate-enclosed"
                lines.append(f"      {site[0]}:{site[1]}  <- {rel}:{ln}  [{verdict}]")
        lines.append(f"\nDECLARED EXCEPTIONS to direction B  [{len(GATED_CALLER_EXCEPTIONS)}] — known "
                     "falsehoods carried")
        lines.append("  deliberately, each with the stage that closes it and a SELF-EXECUTING expiry:")
        for name, exc in sorted(GATED_CALLER_EXCEPTIONS.items()):
            lines.append(f"  {name}  (caller {exc['caller'][0]}:{exc['caller'][1]})")
            for site in exc["sites"]:
                lines.append(f"      excuses {site[0]}:{site[1]}")
            lines.append(f"      expires when {exc['expires_when']}")
        lines.append("\n" + "-" * 78)
        lines.append(f"DIRECT sites (asserted by a human): {len(sites)}  |  gated: {len(GATED)}  | "
                     f" by-design: {len(UNGATED_BY_DESIGN)}  |  ledgered: {len(LEDGERED)}  |  "
                     f"KNOWN BYPASS: {len(KNOWN_BYPASS)}")
        lines.append(f"DERIVED sites (closed by the call graph): {len(closure.derived)}")
        lines.append(f"TOTAL sites the audit can see: {closure.total}")
        lines.append(f"resolver coverage: {_graph.resolved_calls} calls resolved to a target, "
                     f"{_graph.unresolved_calls} unresolved")
        lines.append(f"  (of the resolved, {_graph.receiver_calls} come from rule (e), the "
                     f"constructor-bound receiver added")
        lines.append("   at the 0.0.17 review — `store = SQLiteBackend(p)` … `store.put(i)`, which "
                     "neither")
        lines.append(f"   instrument could see before. {_graph.dangling_targets} distinct targets "
                     f"name no definition —")
        lines.append("   a class constructor or a re-export — and are counted, not called "
                     "'resolved to a def'.)")
        reaching = set(closure.direct) | set(closure.derived)
        productive = sorted({c for c, _ln, t in _graph.receiver_edges if t in reaching})
        lines.append(f"  RULE (e)'s YIELD, stated rather than left as a silent ratio: "
                     f"{_graph.receiver_calls} calls resolved,")
        lines.append(f"   {len(productive)} of them reaching a durable write and so adding a site "
                     f"— {', '.join(f'{r}:{f}' for r, f in productive)}.")
        lines.append("   The gap is not a defect: most constructor-bound receivers hold a class "
                     "that writes")
        lines.append("   nothing, so they resolve a call without reaching the register. Measured "
                     "through")
        lines.append("   scripts/mutate.sh with a measurement label (doc 85 §7b): rule (e) ON = 118 "
                     "derived")
        lines.append("   sites, the M05-shape mutation OFF = 116.")
        lines.append("")
        lines.append(_declared_directions())
        lines.append("")
        lines.append("VERDICT — stated as what this instrument PROVES, and no wider (0.0.17 review):")
        lines.append("         EVERY DURABLE WRITE THIS SWEEP CAN SEE is classified — gated,")
        lines.append("         by-design, or ledgered — and KNOWN_BYPASS is EMPTY. mokata's memory /")
        lines.append("         export / migrate funnel has ZERO bypasses, and as of SI.6b so does")
        lines.append("         the STACK-SHARE pair; KB.S1's 6 SETUP one-shots (init / harness /")
        lines.append("         skills / reset) each keep bespoke TTY consent plus an audit record")
        lines.append("         (LEDGERED). No site registered as write-free reaches a governed write")
        lines.append("         through the RESOLVED call graph.")
        lines.append("         WHAT IT DOES NOT PROVE: that no durable writer exists outside what it")
        lines.append("         can see. The shapes below are confirmed to evade it. 'No durable")
        lines.append("         writer reaches disk unclassified' is the EXIT CRITERION, not this")
        lines.append("         run's finding. (Full WriteGate routing of the setup surfaces is")
        lines.append("         0.0.14+; the write-free BEHAVIOURAL harness is stage 1b.)")
        lines.append("")
        lines.append(_declared_blind_spots(_graph.collisions))
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
